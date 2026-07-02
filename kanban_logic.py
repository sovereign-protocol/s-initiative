"""
Kanban app for the new stack.

Contract:
  Model:
    - The discussion topic is always the kanban board node.
    - Board/columns/cards are regular protocol nodes.
    - Column data: {type: "kanban_column", name, order}
    - Card data: {type: "kanban_card", name, description, participants, order}

  API:
    GET  /api/kanban/board
    POST /api/kanban/auto_adopt          {enabled}
    POST /api/kanban/invite              {address}
    POST /api/kanban/columns/create      {name}
    POST /api/kanban/columns/rename      {column_uuid, name}
    POST /api/kanban/columns/delete      {column_uuid}
    POST /api/kanban/columns/move        {column_uuid, index}
    POST /api/kanban/cards/create        {column_uuid, name, description, participants}
    POST /api/kanban/cards/update        {card_uuid, name, description, participants}
    POST /api/kanban/cards/delete        {card_uuid}
    POST /api/kanban/cards/move          {card_uuid, column_uuid, index}
    POST /api/kanban/adopt               {source_addr, node_uuid, adopt_absence}
"""

from __future__ import annotations

import asyncio
import copy
import time
from typing import Any

from protocol import PRSPNode
from session import Session, SessionResult
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


DEFAULT_COLUMNS = ["To Do", "Doing", "Done"]
KANBAN_APP_NAME = "S-Kanban"


class KanbanLogic:
    def __init__(self, session: Session, config: dict):
        self.session = session
        self.config = config
        self.pending_guards: list[dict] = []
        self.guard_ttl_seconds = 12

    def board_payload(self, auto_adopt: bool = True) -> dict:
        board = self.ensure_board()
        if auto_adopt:
            self.adopt_all_incoming_changes()
            board = self.ensure_board()
        events = self.transition_events(board.uuid)
        return {
            "address": self.session.address,
            "board": board.to_dict(),
            "boards": [item.to_dict() for item in self.boards()],
            "user_profile": self.user_profile().to_dict(),
            "users": self.users(),
            "network": self.session.get_network_info(),
            "peers": {
                addr: tree.to_dict() if tree else None
                for addr, tree in sorted(self.session.peer_perspectives.items())
            },
            "auto_adopt": self.auto_adopt_enabled(board),
            "transition_events": events,
            "transition_by_node": self.transition_by_node(events),
        }

    def auto_adopt_enabled(self, board: PRSPNode | None = None) -> bool:
        board = board or self.ensure_board()
        values = self.session.protocol.root.data.get("kanban_auto_adopt_by_board", {})
        if isinstance(values, dict) and board.uuid in values:
            return bool(values[board.uuid])
        return bool(self.session.protocol.root.data.get("kanban_auto_adopt", True))

    def set_auto_adopt(self, enabled: bool) -> SessionResult:
        board = self.ensure_board()
        root = self.session.protocol.root
        data = dict(root.data)
        values = dict(data.get("kanban_auto_adopt_by_board", {}))
        values[board.uuid] = bool(enabled)
        data["kanban_auto_adopt_by_board"] = values
        return self.session.modify(root.uuid, data, root.weights)

    def ensure_board(self) -> PRSPNode:
        remembered_uuid = self.session.protocol.root.data.get("kanban_board_uuid")
        explicit = bool(self.session.protocol.root.data.get("kanban_board_selection_explicit"))
        remembered = self.session.protocol.index.get(remembered_uuid) if remembered_uuid else None
        if explicit and remembered and remembered.data.get("type") == "kanban_board":
            return remembered
        for topic_uuid in sorted(self.session.active_topic_uuids):
            active = self.session.protocol.index.get(topic_uuid)
            if active and active.data.get("type") == "kanban_board":
                self._remember_board(active.uuid)
                return active
            if active and self._is_kanban_app_topic(active):
                boards = self._boards_under(active)
                if boards:
                    self._remember_board(boards[0].uuid)
                    return boards[0]
        if remembered and remembered.data.get("type") == "kanban_board":
            return remembered
        for node in self.boards():
            self._remember_board(node.uuid)
            return node
        board = self._create_board_node("Kanban Board")
        self._remember_board(board.uuid)
        return board

    def boards(self) -> list[PRSPNode]:
        containers = self._kanban_containers()
        boards = []
        for container in containers:
            boards.extend(self._boards_under(container))
        return sorted(boards, key=lambda node: (
            str(node.data.get("name", "")),
            node.created_at,
        ))

    def create_board(self, name: str = "Kanban Board") -> SessionResult:
        board = self._create_board_node(name or "Kanban Board")
        self._remember_board(board.uuid, explicit=True)
        return SessionResult("ok", value=board.uuid,
                             effects=self.session._sync_effects(self._kanban_container().uuid))

    def select_board(self, board_uuid: str) -> SessionResult:
        board = self.session.protocol.index.get(board_uuid)
        if not board or board.data.get("type") != "kanban_board":
            return SessionResult("error", reason="board not found")
        self._remember_board(board.uuid, explicit=True)
        return SessionResult("ok", value=board.uuid)

    def rename_board(self, board_uuid: str, name: str) -> SessionResult:
        board = self.session.protocol.index.get(board_uuid)
        if not board or board.data.get("type") != "kanban_board":
            return SessionResult("error", reason="board not found")
        data = dict(board.data)
        data["name"] = name or "Kanban Board"
        return self.session.modify(board.uuid, data, board.weights)

    def copy_board(self, board_uuid: str) -> SessionResult:
        board = self.session.protocol.index.get(board_uuid)
        if not board or board.data.get("type") != "kanban_board":
            return SessionResult("error", reason="board not found")
        container = self._kanban_container()
        result = self.session.copy(board.uuid, container.uuid)
        if result.status != "ok":
            return result
        clone = result.value
        data = dict(clone.data)
        data["name"] = f"{data.get('name', 'Kanban Board')} copy"
        self.session.modify(clone.uuid, data, clone.weights)
        self._remember_board(clone.uuid, explicit=True)
        return SessionResult("ok", value=clone.uuid,
                             effects=self.session._sync_effects(clone.uuid))

    def delete_board(self, board_uuid: str) -> SessionResult:
        boards = self.boards()
        if len(boards) <= 1:
            return SessionResult("error", reason="cannot delete the last board")
        board = self.session.protocol.index.get(board_uuid)
        if not board or board.data.get("type") != "kanban_board":
            return SessionResult("error", reason="board not found")
        result = self.session.delete(board.uuid)
        if result.status != "ok":
            return result
        remaining = [item for item in self.boards() if item.uuid != board_uuid]
        if remaining:
            self._remember_board(remaining[0].uuid)
        return result

    def _create_board_node(self, name: str) -> PRSPNode:
        container = self._kanban_container()
        board = self.session.create_child(
            container.uuid,
            {"type": "kanban_board", "name": name},
            {},
        ).value
        for order, name in enumerate(DEFAULT_COLUMNS):
            self.session.create_child(
                board.uuid,
                {"type": "kanban_column", "name": name, "order": order},
                {},
            )
        return board

    def invite(self, runtime, address: str) -> dict:
        topic_uuids = [self.user_profile().uuid]
        for topic_uuid in topic_uuids:
            start = self.session.start_discussion(topic_uuid)
            if start.status != "ok":
                return {"status": "error", "reason": start.reason}
        return runtime.adapter.invite_to_discuss(
            address,
            topic_uuids=topic_uuids,
        )

    def share_board(self, runtime, address: str,
                    board_uuid: str | None = None) -> dict:
        address = address.rstrip("/")
        if address not in self.session.members:
            return {"status": "error", "reason": "connect identity first"}
        board = self.session.protocol.index.get(board_uuid) if board_uuid else self.ensure_board()
        if not board or board.data.get("type") != "kanban_board":
            return {"status": "error", "reason": "board not found"}
        start = self.session.start_discussion(board.uuid)
        if start.status != "ok":
            return {"status": "error", "reason": start.reason}
        return runtime.adapter.invite_to_discuss(
            address,
            topic_uuids=[board.uuid],
        )

    def join_discussion(self, runtime, address: str,
                        topic_uuid: str | None = None,
                        topic_uuids: list[str] | None = None) -> dict:
        topic_uuids = runtime.adapter._topic_uuids(topic_uuid, topic_uuids)
        if not topic_uuids:
            return {"status": "error", "reason": "topic_uuid is required"}
        try:
            address = address.rstrip("/")
            fetched = []
            for uuid in topic_uuids:
                payload = runtime.adapter.fetch_subtree(address, uuid)
                tree = runtime.adapter._decode_wire_subtree(payload["subtree"], address)
                fetched.append((tree, payload.get("parent_uuid")))
            app_topics = [item for item in fetched if self._is_kanban_app_topic(item[0])]
            board_topics = [item for item in fetched if self._is_kanban_board_topic(item[0])]
            user_topics = [item for item in fetched if self._is_shared_user_topic(item[0])]
            accepted_count = len(board_topics) + len(user_topics)
            if app_topics or accepted_count != len(fetched) or accepted_count == 0:
                return {
                    "status": "error",
                    "reason": "S-Kanban accepts only board topics and user/shared identity",
                }

            adopted = []
            for tree, _parent_uuid in board_topics:
                accepted = self.session.accept_topic_invitation(
                    tree,
                    self._kanban_container().uuid,
                )
                if accepted.status != "ok":
                    return {"status": "error", "reason": accepted.reason}
                adopted.append(accepted.value)
            for tree, _parent_uuid in user_topics:
                accepted = self.session.accept_topic_invitation(
                    tree,
                    self._user_folder().uuid,
                )
                if accepted.status != "ok":
                    return {"status": "error", "reason": accepted.reason}
                adopted.append(accepted.value)

            for tree, parent_uuid in fetched:
                self.session.apply_peer_subtree(address, copy.deepcopy(tree), parent_uuid)

            response = runtime.adapter.http.post_json(
                runtime.adapter._url(address, "/p2p/join"),
                {
                    "from_addr": self.session.address,
                    "topic_uuid": topic_uuids[0],
                    "topic_uuids": topic_uuids,
                    "known_members": sorted(self.session.members),
                },
                timeout=10,
            )
            if response.get("status") != "ok":
                return response
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}

        for member in response.get("members", []):
            if member != self.session.address:
                self.session.add_peer_topics(member, topic_uuids)
        if address != self.session.address:
            self.session.add_peer_topics(address, topic_uuids)
        for topic in topic_uuids:
            runtime.adapter.execute_effects(self.session._sync_effects(topic))
        return {
            "status": "ok",
            "members": response.get("members", []),
            "topic_uuids": topic_uuids,
            "adopted_root_uuid": adopted[0] if adopted else None,
            "adopted_root_uuids": adopted,
        }

    def user_profile(self) -> PRSPNode:
        shared = self._shared_user()
        data = dict(shared.data)
        changed = False
        if data.get("type") != "identity_user":
            data["type"] = "identity_user"
            changed = True
        if data.get("role") != "shared_identity":
            data["role"] = "shared_identity"
            changed = True
        if data.get("name") != "shared":
            data["name"] = "shared"
            changed = True
        if not data.get("address"):
            data["address"] = self.session.address
            changed = True
        if not data.get("display_name"):
            data["display_name"] = self._identity_field(shared, "name", self.session.address).data.get("value")
            changed = True
        if "picture" not in data:
            data["picture"] = self._identity_field(shared, "picture", "").data.get("value")
            changed = True
        if changed:
            self.session.protocol.modify(shared.uuid, data, shared.weights)
        self._identity_field(shared, "name", data.get("display_name") or self.session.address)
        self._identity_field(shared, "picture", data.get("picture") or "")
        return shared

    def set_user_profile(self, name: str, picture: str = "") -> SessionResult:
        profile = self.user_profile()
        data = dict(profile.data)
        data.update({
            "type": "identity_user",
            "role": "shared_identity",
            "address": self.session.address,
            "name": "shared",
            "display_name": name or self.session.address,
            "picture": picture or "",
        })
        name_field = self._identity_field(profile, "name", name or self.session.address)
        picture_field = self._identity_field(profile, "picture", picture or "")
        self.session.modify(name_field.uuid, dict(name_field.data, value=name or self.session.address), name_field.weights)
        self.session.modify(picture_field.uuid, dict(picture_field.data, value=picture or ""), picture_field.weights)
        return self.session.modify(profile.uuid, data, profile.weights)

    def users(self) -> list[dict]:
        users = [self._user_info(self.session.address, self.user_profile())]
        for addr, tree in sorted(self.session.peer_perspectives.items()):
            users.append(self._user_info(addr, self._find_user_profile(tree)))
        seen = set()
        out = []
        for user in users:
            if user["address"] in seen:
                continue
            seen.add(user["address"])
            out.append(user)
        return out

    def create_column(self, name: str) -> SessionResult:
        board = self.ensure_board()
        return self._with_guard(lambda: self.session.create_child(
            board.uuid,
            {"type": "kanban_column", "name": name or "Column", "order": len(self.columns(board))},
            {},
        ))

    def rename_column(self, column_uuid: str, name: str) -> SessionResult:
        column = self._node(column_uuid, "kanban_column")
        if not column:
            return SessionResult("error", reason="column not found")
        data = dict(column.data)
        data["name"] = name or "Column"
        return self._with_guard(lambda: self.session.modify(column.uuid, data, column.weights))

    def delete_column(self, column_uuid: str) -> SessionResult:
        return self._with_guard(lambda: self.session.delete(column_uuid))

    def move_column(self, column_uuid: str, index: int) -> SessionResult:
        board = self.ensure_board()
        column = self._node(column_uuid, "kanban_column")
        if not column or column.parent_uuid != board.uuid:
            return SessionResult("error", reason="column not found")
        columns = [item for item in self.columns(board) if item.uuid != column_uuid]
        columns.insert(max(0, min(index, len(columns))), column)
        return self._reorder(columns)

    def create_card(self, column_uuid: str, name: str,
                    description: str = "",
                    participants: list[str] | None = None) -> SessionResult:
        column = self._node(column_uuid, "kanban_column")
        if not column:
            return SessionResult("error", reason="column not found")
        return self._with_guard(lambda: self.session.create_child(
            column.uuid,
            {
                "type": "kanban_card",
                "name": name or "Card",
                "description": description or "",
                "participants": participants or [],
                "order": len(self.cards(column)),
            },
            {},
        ))

    def update_card(self, card_uuid: str, name: str,
                    description: str = "",
                    participants: list[str] | None = None) -> SessionResult:
        card = self._node(card_uuid, "kanban_card")
        if not card:
            return SessionResult("error", reason="card not found")
        data = dict(card.data)
        data.update({
            "name": name or "Card",
            "description": description or "",
            "participants": participants or [],
        })
        return self._with_guard(lambda: self.session.modify(card.uuid, data, card.weights))

    def delete_card(self, card_uuid: str) -> SessionResult:
        return self._with_guard(lambda: self.session.delete(card_uuid))

    def move_card(self, card_uuid: str, column_uuid: str, index: int) -> SessionResult:
        card = self._node(card_uuid, "kanban_card")
        column = self._node(column_uuid, "kanban_column")
        if not card or not column:
            return SessionResult("error", reason="card or column not found")

        def operation():
            effects = []
            if card.parent_uuid != column.uuid:
                moved = self.session.move(card.uuid, column.uuid)
                if moved.status != "ok":
                    return moved
                effects.extend(moved.effects)
            board = self.session.protocol.index.get(column.parent_uuid)
            fresh_card = self.session.protocol.index[card.uuid]
            if board and board.data.get("type") == "kanban_board":
                for board_column in self.columns(board):
                    board_column.children = [
                        child for child in board_column.children
                        if child.uuid != card.uuid
                    ]
                fresh_card.parent_uuid = column.uuid
            fresh_column = self.session.protocol.index[column.uuid]
            cards = [item for item in self.cards(fresh_column) if item.uuid != card.uuid]
            cards.insert(max(0, min(index, len(cards))), fresh_card)
            fresh_column.children = cards
            if board:
                self.session.protocol.cascade_hash(board.uuid)
            reordered = self._reorder(cards)
            if reordered.status != "ok":
                return reordered
            effects.extend(reordered.effects)
            return SessionResult("ok", value=True, effects=effects)

        return self._with_guard(operation)

    def accept_peer_node(self, source_addr: str, node_uuid: str,
                         adopt_absence: bool = False) -> SessionResult:
        if adopt_absence:
            return self._with_guard(lambda: self.session.delete(node_uuid))
        peer = self.session.get_cached_peer_subtree(source_addr, node_uuid)
        if not peer:
            return SessionResult("error", reason="peer node not found")
        local = self.session.protocol.index.get(node_uuid)
        parent_uuid = peer.parent_uuid if peer.parent_uuid in self.session.protocol.index else None
        if not parent_uuid and local:
            parent_uuid = local.parent_uuid
        if not parent_uuid or parent_uuid not in self.session.protocol.index:
            return SessionResult("error", reason="local parent not found")

        def operation():
            adopted = copy.deepcopy(peer)
            adopted.parent_uuid = parent_uuid
            parent = self.session.protocol.index[parent_uuid]
            adopted_child_uuids = self._collect_subtree_uuids(adopted) - {adopted.uuid}
            if adopted_child_uuids:
                self._remove_uuids_from_tree(
                    self.session.protocol.root,
                    adopted_child_uuids,
                )
            existing = self.session.protocol.index.get(adopted.uuid)
            if existing:
                self.session.protocol.deindex_subtree(existing)
                old_parent = self.session.protocol.index.get(existing.parent_uuid)
                if old_parent:
                    old_parent.children = [
                        child for child in old_parent.children
                        if child.uuid != adopted.uuid
                    ]
            parent.children = [child for child in parent.children if child.uuid != adopted.uuid]
            parent.children.append(adopted)
            self.session.protocol.index_subtree(adopted)
            self.session.protocol.cascade_hash(parent.uuid)
            return SessionResult("ok", value=adopted.uuid,
                                 effects=self.session._sync_effects(adopted.uuid))

        return self._with_guard(operation)

    def adopt_incoming_changes(self, board: PRSPNode | None = None) -> bool:
        board = board or self.ensure_board()
        changed = False
        for addr, peer in sorted(self.session.peer_perspectives.items()):
            if not self._peer_discusses_node(addr, board.uuid):
                continue
            peer_board = self.session.get_cached_peer_subtree(addr, board.uuid)
            if not peer_board:
                continue
            board = self.session.protocol.index.get(board.uuid) or board
            top_event = self._top_transition_event(addr, board.uuid)
            if not top_event or top_event["type"] == "in_agreement":
                if top_event:
                    self._mark_peer_aligned(addr, top_event.get("peer_state_hash"))
                continue
            if self._guard_blocks(addr, top_event.get("peer_state_hash")):
                continue
            if top_event["type"] != "peer_made_changes":
                for event in self.session.analyze_peer_transitions(addr, board.uuid):
                    if event["type"] not in ("peer_made_changes", "local_missing_node"):
                        continue
                    if event["node_uuid"] == board.uuid:
                        continue
                    result = self.accept_peer_node(addr, event["node_uuid"])
                    changed = changed or result.status == "ok"
                continue
            self._replace_subtree(peer_board)
            changed = True
        return changed

    def on_peer_update(self) -> SessionResult:
        changed = self.adopt_all_incoming_changes()
        if not changed:
            return SessionResult("ok", value=False)
        return SessionResult(
            "ok",
            value=True,
            effects=self.session._sync_effects(None),
        )

    def adopt_all_incoming_changes(self) -> bool:
        changed = False
        for board in self.boards():
            if (self._is_active_discussion_node(board.uuid)
                    and self.auto_adopt_enabled(board)):
                changed = self.adopt_incoming_changes(board) or changed
        return changed

    def transition_events(self, board_uuid: str | None = None) -> list[dict]:
        board = self.ensure_board()
        board_uuid = board_uuid or board.uuid
        events = []
        for addr in sorted(self.session.peer_perspectives):
            if not self._peer_discusses_node(addr, board.uuid):
                continue
            events.extend(self.session.analyze_peer_transitions(addr, board_uuid))
        return events

    def transition_by_node(self, events: list[dict]) -> dict:
        priority = {
            "divergence": 6,
            "cannot_compare": 5,
            "peer_made_changes": 4,
            "local_missing_node": 4,
            "local_made_changes": 3,
            "peer_missing_node": 3,
            "in_agreement": 0,
        }
        out = {}
        for event in events:
            node_uuid = event.get("node_uuid")
            if not node_uuid:
                continue
            event_info = {
                "type": event["type"],
                "peer_addr": event.get("peer_addr"),
            }
            current = out.get(node_uuid)
            if current:
                if event["type"] != "in_agreement":
                    current.setdefault("events", []).append(dict(event_info))
                if priority.get(current["type"], 0) >= priority.get(event["type"], 0):
                    continue
                current.update(event_info)
                continue
            out[node_uuid] = dict(event_info)
            if event["type"] != "in_agreement":
                out[node_uuid]["events"] = [dict(event_info)]
        return out

    def columns(self, board: PRSPNode | None = None) -> list[PRSPNode]:
        board = board or self.ensure_board()
        return sorted(
            [child for child in board.children if child.data.get("type") == "kanban_column"],
            key=lambda node: (float(node.data.get("order", 0)), node.created_at),
        )

    def cards(self, column: PRSPNode) -> list[PRSPNode]:
        return sorted(
            [child for child in column.children if child.data.get("type") == "kanban_card"],
            key=lambda node: (float(node.data.get("order", 0)), node.created_at),
        )

    def _with_guard(self, operation) -> SessionResult:
        board = self.ensure_board()
        old_hash = board.state_hash
        result = operation()
        if result.status == "ok":
            new_board = self.ensure_board()
            if new_board.state_hash != old_hash:
                self.pending_guards.append({
                    "old_hash": old_hash,
                    "new_hash": new_board.state_hash,
                    "pending_peers": set(self.session.members - {self.session.address}),
                    "expires_at": time.monotonic() + self.guard_ttl_seconds,
                })
        return result

    def _reorder(self, nodes: list[PRSPNode]) -> SessionResult:
        effects = []
        for order, node in enumerate(nodes):
            if node.data.get("order") == order:
                continue
            data = dict(node.data)
            data["order"] = order
            result = self.session.modify(node.uuid, data, node.weights)
            if result.status != "ok":
                return result
            effects.extend(result.effects)
        return SessionResult("ok", value=True, effects=effects)

    def _replace_subtree(self, peer_board: PRSPNode) -> None:
        local = self.session.protocol.index.get(peer_board.uuid)
        if not local or not local.parent_uuid:
            return
        parent = self.session.protocol.index[local.parent_uuid]
        imported = PRSPNode.from_dict(peer_board.to_dict())
        imported.parent_uuid = parent.uuid
        self.session.protocol.deindex_subtree(local)
        parent.children = [imported if child.uuid == local.uuid else child for child in parent.children]
        self.session.protocol.index_subtree(imported)
        self.session.protocol.cascade_hash(parent.uuid)

    def _top_transition_event(self, peer_addr: str, board_uuid: str) -> dict | None:
        events = self.session.analyze_peer_transitions(peer_addr, board_uuid)
        return events[0] if events else None

    def _guard_blocks(self, addr: str, peer_hash: str) -> bool:
        blocked = False
        for guard in self.pending_guards:
            if addr not in guard["pending_peers"]:
                continue
            if peer_hash == guard["new_hash"]:
                guard["pending_peers"].discard(addr)
            elif peer_hash == guard["old_hash"]:
                blocked = True
            else:
                guard["pending_peers"].discard(addr)
        self._prune_guards()
        return blocked

    def _mark_peer_aligned(self, addr: str, state_hash: str) -> None:
        for guard in self.pending_guards:
            if state_hash == guard["new_hash"]:
                guard["pending_peers"].discard(addr)
        self._prune_guards()

    def _prune_guards(self) -> None:
        now = time.monotonic()
        self.pending_guards = [
            guard for guard in self.pending_guards
            if guard["pending_peers"] and guard["expires_at"] > now
        ]

    def _node(self, uuid: str, node_type: str) -> PRSPNode | None:
        node = self.session.protocol.index.get(uuid)
        if node and node.data.get("type") == node_type:
            return node
        return None

    def _remember_board(self, board_uuid: str, explicit: bool = False) -> None:
        root = self.session.protocol.root
        data = dict(root.data)
        data["kanban_board_uuid"] = board_uuid
        if explicit:
            data["kanban_board_selection_explicit"] = True
        self.session.protocol.modify(root.uuid, data, root.weights)

    def _kanban_container(self) -> PRSPNode:
        return self._folder(self._apps_folder(), KANBAN_APP_NAME, "kanban_app")

    def _kanban_containers(self) -> list[PRSPNode]:
        active = [
            self.session.protocol.index[uuid]
            for uuid in sorted(self.session.active_topic_uuids)
            if uuid in self.session.protocol.index
            and self._is_kanban_app_topic(self.session.protocol.index[uuid])
        ]
        if active:
            return active
        return [self._kanban_container()]

    def _apps_folder(self) -> PRSPNode:
        return self._folder(self.session.protocol.root, "apps")

    def _user_folder(self) -> PRSPNode:
        return self._folder(self.session.protocol.root, "user")

    def _shared_user(self) -> PRSPNode:
        return self._folder(self._user_folder(), "shared", "identity_user")

    def _identity_topic_uuids(self) -> list[str]:
        return [self._shared_user().uuid]

    def _user_info(self, fallback_addr: str, profile: PRSPNode | None) -> dict:
        data = profile.data if profile else {}
        address = data.get("address") or fallback_addr
        return {
            "address": address,
            "name": data.get("display_name") or data.get("name") or address,
            "picture": data.get("picture") or "",
        }

    def _find_user_profile(self, node: PRSPNode | None) -> PRSPNode | None:
        if not node:
            return None
        if node.data.get("type") == "identity_user":
            return node
        for child in node.children:
            found = self._find_user_profile(child)
            if found:
                return found
        return None

    def _folder(self, parent: PRSPNode, name: str,
                node_type: str = "folder") -> PRSPNode:
        for child in parent.children:
            if child.data.get("name") == name and child.data.get("type") in ("folder", node_type):
                return child
        created = self.session.create_child(
            parent.uuid,
            {"type": node_type, "name": name},
            {},
        ).value
        return created

    def _identity_field(self, shared: PRSPNode, field: str, value: str) -> PRSPNode:
        for child in shared.children:
            if child.data.get("type") == "identity_field" and child.data.get("field") == field:
                return child
        return self.session.create_child(
            shared.uuid,
            {"type": "identity_field", "field": field, "value": value},
            {},
        ).value

    def _boards_under(self, root: PRSPNode) -> list[PRSPNode]:
        out = []
        if root.data.get("type") == "kanban_board":
            out.append(root)
        for child in root.children:
            out.extend(self._boards_under(child))
        return out

    def _is_kanban_app_topic(self, node: PRSPNode) -> bool:
        return (
            node.data.get("type") == "kanban_app"
            and node.data.get("name") == KANBAN_APP_NAME
        )

    def _is_kanban_board_topic(self, node: PRSPNode) -> bool:
        return node.data.get("type") == "kanban_board"

    def _is_shared_user_topic(self, node: PRSPNode) -> bool:
        return (
            node.data.get("type") == "identity_user"
            and node.data.get("role") == "shared_identity"
        )

    def _is_active_discussion_node(self, node_uuid: str) -> bool:
        return any(
            self._contains_uuid(topic_uuid, node_uuid)
            for topic_uuid in self.session.active_topic_uuids
        )

    def _peer_discusses_node(self, peer_addr: str, node_uuid: str) -> bool:
        return any(
            self._contains_uuid(topic_uuid, node_uuid)
            for topic_uuid in self.session.peer_topic_sets.get(peer_addr, set())
        )

    def _contains_uuid(self, root_uuid: str, node_uuid: str) -> bool:
        root = self.session.protocol.index.get(root_uuid)
        return bool(root and self._find_in_tree(root, node_uuid))

    def _find_in_tree(self, root: PRSPNode, node_uuid: str) -> PRSPNode | None:
        if root.uuid == node_uuid:
            return root
        for child in root.children:
            found = self._find_in_tree(child, node_uuid)
            if found:
                return found
        return None

    def _collect_subtree_uuids(self, node: PRSPNode) -> set[str]:
        out = {node.uuid}
        for child in node.children:
            out.update(self._collect_subtree_uuids(child))
        return out

    def _remove_uuids_from_tree(self, root: PRSPNode, uuids: set[str]) -> None:
        self._remove_uuids_from_tree_locked(root, uuids)

    def _remove_uuids_from_tree_locked(self, root: PRSPNode, uuids: set[str]) -> bool:
        changed = False
        kept = []
        for child in root.children:
            if child.uuid in uuids:
                self.session.protocol.deindex_subtree(child)
                changed = True
                continue
            changed = self._remove_uuids_from_tree_locked(child, uuids) or changed
            kept.append(child)
        root.children = kept
        if changed:
            root.refresh_hashes()
        return changed


def create_logic(session: Session, config: dict) -> KanbanLogic:
    return KanbanLogic(session, config)


def build_routes(logic: KanbanLogic, runtime, config: dict) -> list[Route]:
    async def api_board(request: Request):
        result = await asyncio.to_thread(logic.on_peer_update)
        if result.value:
            await asyncio.to_thread(runtime.adapter.execute_effects, result.effects)
            runtime.notify_change()
        return JSONResponse(logic.board_payload(auto_adopt=False))

    async def api_auto_adopt(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.set_auto_adopt(bool(data.get("enabled"))))

    async def api_create_board(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.create_board(data.get("name", "Kanban Board")))

    async def api_select_board(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.select_board(data["board_uuid"]))

    async def api_rename_board(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.rename_board(
            data["board_uuid"],
            data.get("name", "Kanban Board"),
        ))

    async def api_copy_board(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.copy_board(data["board_uuid"]))

    async def api_delete_board(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.delete_board(data["board_uuid"]))

    async def api_invite(request: Request):
        data = await request.json()
        result = await asyncio.to_thread(logic.invite, runtime, data["address"].strip().rstrip("/"))
        status = 200 if result.get("status") == "ok" else 409
        if status == 200:
            runtime.notify_change()
        return JSONResponse(result, status_code=status)

    async def api_share_board(request: Request):
        data = await request.json()
        result = await asyncio.to_thread(
            logic.share_board,
            runtime,
            data["address"].strip().rstrip("/"),
            data.get("board_uuid"),
        )
        status = 200 if result.get("status") == "ok" else 409
        if status == 200:
            runtime.notify_change()
        return JSONResponse(result, status_code=status)

    async def api_profile(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.set_user_profile(
            data.get("name", ""),
            data.get("picture", ""),
        ))

    async def api_create_column(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.create_column(data.get("name", "Column")))

    async def api_rename_column(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.rename_column(data["column_uuid"], data.get("name", "Column")))

    async def api_delete_column(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.delete_column(data["column_uuid"]))

    async def api_move_column(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.move_column(data["column_uuid"], int(data.get("index", 0))))

    async def api_create_card(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.create_card(
            data["column_uuid"],
            data.get("name", "Card"),
            data.get("description", ""),
            _participants(data.get("participants")),
        ))

    async def api_update_card(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.update_card(
            data["card_uuid"],
            data.get("name", "Card"),
            data.get("description", ""),
            _participants(data.get("participants")),
        ))

    async def api_delete_card(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.delete_card(data["card_uuid"]))

    async def api_move_card(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.move_card(
            data["card_uuid"],
            data["column_uuid"],
            int(data.get("index", 0)),
        ))

    async def api_adopt(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.accept_peer_node(
            data["source_addr"],
            data["node_uuid"],
            bool(data.get("adopt_absence")),
        ))

    return [
        Route("/api/kanban/board", api_board),
        Route("/api/kanban/auto_adopt", api_auto_adopt, methods=["POST"]),
        Route("/api/kanban/boards/create", api_create_board, methods=["POST"]),
        Route("/api/kanban/boards/select", api_select_board, methods=["POST"]),
        Route("/api/kanban/boards/rename", api_rename_board, methods=["POST"]),
        Route("/api/kanban/boards/copy", api_copy_board, methods=["POST"]),
        Route("/api/kanban/boards/delete", api_delete_board, methods=["POST"]),
        Route("/api/kanban/profile", api_profile, methods=["POST"]),
        Route("/api/kanban/invite", api_invite, methods=["POST"]),
        Route("/api/kanban/boards/share", api_share_board, methods=["POST"]),
        Route("/api/kanban/columns/create", api_create_column, methods=["POST"]),
        Route("/api/kanban/columns/rename", api_rename_column, methods=["POST"]),
        Route("/api/kanban/columns/delete", api_delete_column, methods=["POST"]),
        Route("/api/kanban/columns/move", api_move_column, methods=["POST"]),
        Route("/api/kanban/cards/create", api_create_card, methods=["POST"]),
        Route("/api/kanban/cards/update", api_update_card, methods=["POST"]),
        Route("/api/kanban/cards/delete", api_delete_card, methods=["POST"]),
        Route("/api/kanban/cards/move", api_move_card, methods=["POST"]),
        Route("/api/kanban/adopt", api_adopt, methods=["POST"]),
    ]


async def _json_result(runtime, result: SessionResult) -> JSONResponse:
    if result.status != "ok":
        return JSONResponse({"status": "error", "reason": result.reason}, status_code=409)
    deliveries = await asyncio.to_thread(runtime.adapter.execute_effects, result.effects)
    runtime.notify_change()
    payload: dict[str, Any] = {"status": "ok"}
    if hasattr(result.value, "to_dict"):
        payload["value"] = result.value.to_dict()
    elif result.value is not None:
        payload["value"] = result.value
    errors = [item for item in deliveries if not item.ok]
    if errors:
        payload["delivery_errors"] = [
            {"effect_type": item.effect_type, "target": item.target, "reason": item.reason}
            for item in errors
        ]
    return JSONResponse(payload)


def _participants(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]

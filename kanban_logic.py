"""
Kanban app for the new stack.

Contract:
  Model:
    - The discussion topic is always the kanban board node.
    - Board/columns/cards are regular protocol nodes.
    - Column data: {type: "kanban_column", name, order}
    - Card data: {type: "kanban_card", name, description, owners, order}

  API:
    GET  /api/kanban/board
    POST /api/kanban/auto_adopt          {enabled}
    POST /api/kanban/invite              {address}
    POST /api/kanban/columns/create      {name}
    POST /api/kanban/columns/rename      {column_uuid, name}
    POST /api/kanban/columns/delete      {column_uuid}
    POST /api/kanban/columns/move        {column_uuid, index}
    POST /api/kanban/cards/create        {column_uuid, name, description, owners}
    POST /api/kanban/cards/update        {card_uuid, name, description, owners}
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


class KanbanLogic:
    def __init__(self, session: Session, config: dict):
        self.session = session
        self.config = config
        self.pending_guards: list[dict] = []
        self.guard_ttl_seconds = 12

    def board_payload(self, auto_adopt: bool = True) -> dict:
        if auto_adopt and self.auto_adopt_enabled():
            self.adopt_incoming_changes()
        board = self.ensure_board()
        events = self.transition_events(board.uuid)
        return {
            "address": self.session.address,
            "board": board.to_dict(),
            "network": self.session.get_network_info(),
            "peers": {
                addr: tree.to_dict() if tree else None
                for addr, tree in sorted(self.session.peer_perspectives.items())
            },
            "auto_adopt": self.auto_adopt_enabled(),
            "transition_events": events,
            "transition_by_node": self.transition_by_node(events),
        }

    def auto_adopt_enabled(self) -> bool:
        return bool(self.session.protocol.root.data.get("kanban_auto_adopt", True))

    def set_auto_adopt(self, enabled: bool) -> SessionResult:
        root = self.session.protocol.root
        data = dict(root.data)
        data["kanban_auto_adopt"] = bool(enabled)
        return self.session.modify(root.uuid, data, root.weights)

    def ensure_board(self) -> PRSPNode:
        if self.session.active_topic_uuid:
            active = self.session.protocol.index.get(self.session.active_topic_uuid)
            if active and active.data.get("type") == "kanban_board":
                self._remember_board(active.uuid)
                return active
        remembered_uuid = self.session.protocol.root.data.get("kanban_board_uuid")
        remembered = self.session.protocol.index.get(remembered_uuid) if remembered_uuid else None
        if remembered and remembered.data.get("type") == "kanban_board":
            return remembered
        for node in self.session.protocol.index.values():
            if node.data.get("type") == "kanban_board":
                self._remember_board(node.uuid)
                return node
        board = self.session.create_child(
            self.session.protocol.root.uuid,
            {"type": "kanban_board", "name": "Kanban Board"},
            {},
        ).value
        for order, name in enumerate(DEFAULT_COLUMNS):
            self.session.create_child(
                board.uuid,
                {"type": "kanban_column", "name": name, "order": order},
                {},
            )
        self._remember_board(board.uuid)
        return board

    def invite(self, runtime, address: str) -> dict:
        board = self.ensure_board()
        start = self.session.start_discussion(board.uuid)
        if start.status != "ok":
            return {"status": "error", "reason": start.reason}
        return runtime.adapter.invite_to_discuss(address, board.uuid)

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
                    description: str = "", owners: list[str] | None = None) -> SessionResult:
        column = self._node(column_uuid, "kanban_column")
        if not column:
            return SessionResult("error", reason="column not found")
        return self._with_guard(lambda: self.session.create_child(
            column.uuid,
            {
                "type": "kanban_card",
                "name": name or "Card",
                "description": description or "",
                "owners": owners or [],
                "order": len(self.cards(column)),
            },
            {},
        ))

    def update_card(self, card_uuid: str, name: str,
                    description: str = "", owners: list[str] | None = None) -> SessionResult:
        card = self._node(card_uuid, "kanban_card")
        if not card:
            return SessionResult("error", reason="card not found")
        data = dict(card.data)
        data.update({
            "name": name or "Card",
            "description": description or "",
            "owners": owners or [],
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
            fresh_column = self.session.protocol.index[column.uuid]
            cards = [item for item in self.cards(fresh_column) if item.uuid != card.uuid]
            fresh_card = self.session.protocol.index[card.uuid]
            cards.insert(max(0, min(index, len(cards))), fresh_card)
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

    def adopt_incoming_changes(self) -> bool:
        board = self.ensure_board()
        changed = False
        for addr, peer in sorted(self.session.peer_perspectives.items()):
            if self.session.peer_topics.get(addr) != board.uuid:
                continue
            if not peer or peer.uuid != board.uuid:
                continue
            board = self.ensure_board()
            top_event = self._top_transition_event(addr, board.uuid)
            if not top_event or top_event["type"] == "in_agreement":
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
            self._replace_subtree(peer)
            changed = True
        return changed

    def on_peer_update(self) -> SessionResult:
        if not self.auto_adopt_enabled():
            return SessionResult("ok", value=False)
        changed = self.adopt_incoming_changes()
        if not changed:
            return SessionResult("ok", value=False)
        board = self.ensure_board()
        return SessionResult(
            "ok",
            value=True,
            effects=self.session._sync_effects(board.uuid),
        )

    def transition_events(self, board_uuid: str | None = None) -> list[dict]:
        board = self.ensure_board()
        board_uuid = board_uuid or board.uuid
        events = []
        for addr in sorted(self.session.peer_perspectives):
            if self.session.peer_topics.get(addr) != board.uuid:
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

    def _remember_board(self, board_uuid: str) -> None:
        root = self.session.protocol.root
        data = dict(root.data)
        data["kanban_board_uuid"] = board_uuid
        self.session.protocol.modify(root.uuid, data, root.weights)

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

    async def api_invite(request: Request):
        data = await request.json()
        result = await asyncio.to_thread(logic.invite, runtime, data["address"].strip().rstrip("/"))
        status = 200 if result.get("status") == "ok" else 409
        if status == 200:
            runtime.notify_change()
        return JSONResponse(result, status_code=status)

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
            _owners(data.get("owners")),
        ))

    async def api_update_card(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.update_card(
            data["card_uuid"],
            data.get("name", "Card"),
            data.get("description", ""),
            _owners(data.get("owners")),
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
        Route("/api/kanban/invite", api_invite, methods=["POST"]),
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


def _owners(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]

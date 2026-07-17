"""
Kanban app for the new stack.

Contract:
  Model:
    - The discussion topic is always the kanban board node.
    - Board/columns/cards are regular protocol nodes.
    - Column data: {type: "kanban_column", name, order}
    - Card data: {type: "kanban_card", name, description, participants, owner, order}
      owner is a profile uuid that must also be present in participants, or None.
    Board data additionally carries: objective (short free-text tagline,
      default ""), used by the Board of Boards summary view.
    Agenda item data: {type: "agenda_item", text, priority, author}
      - a direct child of the board, same as columns. priority is one of
      "high"/"medium"/"low", or None if not set (optional - a card doesn't
      need one), author is a profile uuid. Purely async: created/deleted
      like any node, synced/merged via the board's existing topic - no
      dedicated protocol support needed.

  API:
    GET  /api/kanban/board
    POST /api/kanban/boards/set_objective {board_uuid, objective}
    POST /api/kanban/agenda/create        {text, priority}  # priority optional
    POST /api/kanban/agenda/delete        {item_uuid}
    POST /api/kanban/agenda/set_priority  {item_uuid, priority}  # priority optional, clears if omitted
    POST /api/kanban/auto_adopt          {mode}  # one of: always, not_owner, not_member, never
    POST /api/kanban/columns/create      {name}
    POST /api/kanban/columns/rename      {column_uuid, name}
    POST /api/kanban/columns/delete      {column_uuid}
    POST /api/kanban/columns/move        {column_uuid, index}
    POST /api/kanban/cards/create        {column_uuid, name, description, participants, owner}
    POST /api/kanban/cards/update        {card_uuid, name, description, participants, owner}
    POST /api/kanban/cards/delete        {card_uuid}
    POST /api/kanban/cards/move          {card_uuid, column_uuid, index}
    POST /api/kanban/adopt               {source_addr, node_uuid, adopt_absence}
    POST /api/kanban/perspective            {node_uuid, state}  # one of: none, kept_mine, pushed_back
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
ORDER_GAP_EPSILON = 1e-9
AUTO_ADOPT_MODES = ("always", "not_owner", "not_member", "never")
AGENDA_PRIORITIES = ("high", "medium", "low")
# Priority is optional (None/"" means "not set") - unset sorts below every
# explicit priority rather than defaulting to "medium".
AGENDA_PRIORITY_RANK = {"high": 3, "medium": 2, "low": 1}


class KanbanLogic:
    def __init__(self, session: Session, config: dict):
        self.session = session
        self.config = config
        # How long a "divergence" classification must be observed
        # continuously before it's shown to the user as a real conflict -
        # see _debounce_divergences. Defaults to 2 sync cycles (whichever
        # of the two sync mechanisms this instance actually has configured
        # is slower), since that's roughly how long a transient one can
        # take to resolve on its own.
        self._divergence_debounce_seconds = float(config.get(
            "divergence_debounce_seconds",
            2 * max(
                float(config.get("peer_sync_interval_seconds", 2)),
                float(config.get("relay_poll_interval_seconds", 3)),
            ),
        ))
        self._divergence_first_seen: dict[tuple[str, str], float] = {}

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
            "network": self._network_info_with_relay_liveness(),
            "peers": {
                addr: tree.to_dict()
                for addr, tree in sorted(self.session.peer_perspectives.items())
            },
            "auto_adopt_mode": self.auto_adopt_mode(board),
            "transition_events": events,
            "transition_by_node": self.transition_by_node(events),
            "agenda_items": [item.to_dict() for item in self.agenda_items(board)],
        }

    def _network_info_with_relay_liveness(self) -> dict:
        # Relay peers have no http reachability signal, so peer_status
        # always shows them "online, last_seen: never" - a stopped peer
        # looked permanently alive (review U-7). The relay layer already
        # computes real liveness from presence-file mtimes
        # (RelayLogic.peer_liveness); attach it per relay peer so the UI
        # can show stale/unknown honestly. The instance access mirrors
        # accept_connect_token's (relay stashes itself in the shared
        # config dict); absent relay config, the payload is unchanged.
        info = self.session.get_network_info()
        relay_logic = self.config.get("_relay_logic_instance")
        if relay_logic is None or not getattr(relay_logic, "storage", None):
            return info
        for addr, peer_info in (info.get("peers") or {}).items():
            if addr.startswith("relay:"):
                peer_info["relay_liveness"] = relay_logic.peer_liveness(
                    addr.split(":", 1)[1],
                )
        return info

    def auto_adopt_mode(self, board: PRSPNode | None = None) -> str:
        board = board or self.ensure_board()
        values = self._metadata().get("auto_adopt_by_board", {})
        if isinstance(values, dict) and board.uuid in values:
            return self._normalize_auto_adopt_mode(values[board.uuid])
        return self._normalize_auto_adopt_mode(self._metadata().get("auto_adopt", "always"))

    def set_auto_adopt_mode(self, mode: str) -> SessionResult:
        board = self.ensure_board()
        normalized = self._normalize_auto_adopt_mode(mode)
        self._set_board_auto_adopt(board.uuid, normalized)
        return SessionResult("ok", value=normalized)

    def _set_board_auto_adopt(self, board_uuid: str, mode: str) -> None:
        metadata = self._metadata()
        values = dict(metadata.get("auto_adopt_by_board", {}))
        values[board_uuid] = self._normalize_auto_adopt_mode(mode)
        metadata["auto_adopt_by_board"] = values

    @staticmethod
    def _normalize_auto_adopt_mode(value: Any) -> str:
        if isinstance(value, bool):
            return "always" if value else "never"
        if value in AUTO_ADOPT_MODES:
            return value
        return "always"

    def ensure_board(self) -> PRSPNode:
        remembered_uuid = self._metadata().get("selected_board_uuid")
        explicit = bool(self._metadata().get("board_selection_explicit"))
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
                             effects=self.session.sync_effects(self._kanban_container().uuid))

    def select_board(self, board_uuid: str) -> SessionResult:
        board = self.session.protocol.index.get(board_uuid)
        if not board or board.data.get("type") != "kanban_board":
            return SessionResult("error", reason="board not found")
        self._remember_board(board.uuid, explicit=True)
        return SessionResult("ok", value=board.uuid)

    def accept_relay_board(self, subtree: PRSPNode) -> SessionResult:
        # Grafts a board first discovered via the relay (not a live P2P
        # join) into our own board list - same graft call and same
        # "manual review until the user opts in" default the live-join
        # accept path already uses, just triggered by a connect token
        # instead of a real-time /p2p/join handshake.
        accepted = self.session.accept_topic_invitation(subtree, self._kanban_container().uuid)
        if accepted.status == "ok":
            self._set_board_auto_adopt(accepted.value, False)
            self._remember_board(accepted.value)
        return accepted

    def rename_board(self, board_uuid: str, name: str) -> SessionResult:
        board = self.session.protocol.index.get(board_uuid)
        if not board or board.data.get("type") != "kanban_board":
            return SessionResult("error", reason="board not found")
        data = dict(board.data)
        data["name"] = name or "Kanban Board"
        return self.session.modify(board.uuid, data, board.weights)

    def set_board_objective(self, board_uuid: str, objective: str) -> SessionResult:
        board = self.session.protocol.index.get(board_uuid)
        if not board or board.data.get("type") != "kanban_board":
            return SessionResult("error", reason="board not found")
        data = dict(board.data)
        data["objective"] = objective or ""
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
                             effects=self.session.sync_effects(clone.uuid))

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
            {"type": "kanban_board", "name": name, "objective": ""},
            {},
        ).value
        for order, name in enumerate(DEFAULT_COLUMNS):
            self.session.create_child(
                board.uuid,
                {"type": "kanban_column", "name": name, "order": order},
                {},
            )
        return self.session.get_node(board.uuid) or board

    def unshare_board(self, runtime, board_uuid: str | None = None) -> dict:
        board = self.session.protocol.index.get(board_uuid) if board_uuid else self.ensure_board()
        if not board or board.data.get("type") != "kanban_board":
            return {"status": "error", "reason": "board not found"}
        # Unsharing must also unmark the board in relay's `shared` intent,
        # or relay keeps publishing it (and stays armed) forever - `shared`
        # has no other shrink path (review R-3). Done before the peer check
        # below: a board shared via token but never yet accepted by anyone
        # has no peers, yet its relay publishing still has to stop.
        relay_logic = self.config.get("_relay_logic_instance")
        if relay_logic is not None:
            relay_logic.unmark_topics_shared([board.uuid])
        board_peers = [
            peer for peer, topics in sorted(self.session.peer_topic_sets.items())
            if board.uuid in topics
        ]
        if not board_peers:
            return {"status": "ok", "topic_uuids": []}
        deliveries = runtime.adapter.leave_topic(board.uuid)
        removed_topics = {board.uuid}
        any_board_remaining = any(
            self._is_kanban_board_topic(self.session.protocol.index.get(topic_uuid))
            for topics in self.session.peer_topic_sets.values()
            for topic_uuid in topics
        )
        if not any_board_remaining:
            profile_topics = sorted({
                topic_uuid
                for topics in self.session.peer_topic_sets.values()
                for topic_uuid in topics
            })
            removed_topics.update(profile_topics)
            deliveries.extend(runtime.adapter.disconnect())
        errors = [
            {"effect_type": item.effect_type, "target": item.target, "reason": item.reason}
            for item in deliveries
            if not item.ok
        ]
        payload = {"status": "ok", "topic_uuids": sorted(removed_topics)}
        if errors:
            payload["delivery_errors"] = errors
        return payload

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
            board_topics = [item for item in fetched if self._is_kanban_board_topic(item[0])]
            user_topics = [item for item in fetched if self._is_shared_user_topic(item[0])]
            accepted_count = len(board_topics) + len(user_topics)
            if accepted_count != len(fetched) or accepted_count == 0:
                return {
                    "status": "error",
                    "reason": "S-Kanban accepts only board topics and shared user profiles",
                }
            response_topic_uuids = list(topic_uuids)
            peer_fetch_topics = [tree.uuid for tree, _ in fetched]
            own_profile_uuid = self.user_profile().uuid
            if own_profile_uuid not in response_topic_uuids:
                response_topic_uuids.append(own_profile_uuid)
            start = self.session.start_discussion(own_profile_uuid)
            if start.status != "ok":
                return {"status": "error", "reason": start.reason}

            adopted = []
            for tree, _parent_uuid in board_topics:
                accepted = self.session.accept_topic_invitation(
                    tree,
                    self._kanban_container().uuid,
                )
                if accepted.status != "ok":
                    return {"status": "error", "reason": accepted.reason}
                self._set_board_auto_adopt(accepted.value, False)
                adopted.append(accepted.value)
            for tree, parent_uuid in fetched:
                self.session.apply_peer_subtree(address, copy.deepcopy(tree), parent_uuid)

            response = runtime.adapter.http.post_json(
                runtime.adapter._url(address, "/p2p/join"),
                {
                    "from_addr": self.session.address,
                    "topic_uuid": response_topic_uuids[0],
                    "topic_uuids": response_topic_uuids,
                    "pull_topic_uuids": [
                        *(tree.uuid for tree, _ in board_topics),
                        own_profile_uuid,
                    ],
                    "topic_members": self.session.topic_members_by_topic(response_topic_uuids),
                },
                timeout=10,
            )
            if response.get("status") != "ok":
                return response
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}

        topic_members = self.session.topic_members_from_map(
            response.get("topic_members") or {}, response_topic_uuids,
        )
        indirect_board_members: dict[str, set[str]] = {}
        board_topic_uuids = {tree.uuid for tree, _ in board_topics}
        for topic, members in topic_members.items():
            for member in members:
                if member == self.session.address:
                    continue
                already_known = topic in self.session.peer_topic_sets.get(member, set())
                self.session.add_peer(
                    member,
                    topic,
                    fetch_from_peer=member != address or topic in peer_fetch_topics,
                )
                if (
                    topic in board_topic_uuids
                    and member != address
                    and not already_known
                ):
                    indirect_board_members.setdefault(member, set()).add(topic)
        if address != self.session.address:
            self._set_peer_owned_topics(address, peer_fetch_topics)
        for member, topics in sorted(indirect_board_members.items()):
            runtime.adapter.invite_to_discuss(
                member,
                topic_uuids=[*sorted(topics), own_profile_uuid],
            )
        for topic in response_topic_uuids:
            runtime.adapter.execute_effects(self.session.sync_effects(topic))
        return {
            "status": "ok",
            "members": sorted({
                member
                for members in topic_members.values()
                for member in members
            }),
            "topic_uuids": response_topic_uuids,
            "adopted_root_uuid": adopted[0] if adopted else None,
            "adopted_root_uuids": adopted,
            "topic_members": {
                topic: sorted(members)
                for topic, members in sorted(topic_members.items())
            },
        }

    def _set_peer_owned_topics(self, address: str, topic_uuids: list[str]) -> None:
        current = set(self.session.fetch_topic_uuids(address))
        current.discard(self.user_profile().uuid)
        current.update(topic_uuids)
        self.session.set_peer_fetch_topics(address, current)

    def user_profile(self) -> PRSPNode:
        return self.session.identity

    def set_user_profile(self, name: str, picture: str = "") -> SessionResult:
        return self.session.set_identity(name, picture)

    def users(self) -> list[dict]:
        users = [self._user_info(self.session.address, self.user_profile())]
        # Union with peer_perspectives, not just members - a relay-only
        # peer (e.g. "relay:B") never goes through add_peer (deliberately;
        # see Session.note_relay_peer_topic), so it would never appear here
        # if this only looked at members. Their identity is still visible
        # via the ordinary peer_perspectives cache, same as any HTTP peer's.
        addrs = (self.session.members | set(self.session.peer_perspectives)) - {self.session.address}
        for addr in sorted(addrs):
            profile = self._find_peer_user_profile(addr)
            users.append(self._user_info(addr, profile, self._peer_profile_uuid(addr, profile)))
        seen = set()
        out = []
        for user in users:
            # An empty id means "identity not resolved yet", not a real
            # identity - deduping on it would collapse every unresolved
            # peer into whichever one happened to come first (review K-6).
            if user["id"]:
                if user["id"] in seen:
                    continue
                seen.add(user["id"])
            out.append(user)
        return out

    def create_column(self, name: str) -> SessionResult:
        board = self.ensure_board()
        existing = self.columns(board)
        last_order = float(existing[-1].data.get("order", 0)) if existing else None
        return self.session.create_child(
            board.uuid,
            {
                "type": "kanban_column",
                "name": name or "Column",
                "order": self._order_between(last_order, None),
            },
            {},
        )

    def rename_column(self, column_uuid: str, name: str) -> SessionResult:
        column = self._node(column_uuid, "kanban_column")
        if not column:
            return SessionResult("error", reason="column not found")
        data = dict(column.data)
        data["name"] = name or "Column"
        return self.session.modify(column.uuid, data, column.weights)

    def delete_column(self, column_uuid: str) -> SessionResult:
        return self.session.delete(column_uuid)

    def move_column(self, column_uuid: str, index: int) -> SessionResult:
        board = self.ensure_board()
        column = self._node(column_uuid, "kanban_column")
        if not column or column.parent_uuid != board.uuid:
            return SessionResult("error", reason="column not found")
        siblings = [item for item in self.columns(board) if item.uuid != column_uuid]
        return self._place_in_order(column, siblings, index)

    def create_card(self, column_uuid: str, name: str,
                    description: str = "",
                    participants: list[str] | None = None,
                    owner: str | None = None) -> SessionResult:
        column = self._node(column_uuid, "kanban_column")
        if not column:
            return SessionResult("error", reason="column not found")
        existing = self.cards(column)
        last_order = float(existing[-1].data.get("order", 0)) if existing else None
        participants = participants or []
        return self.session.create_child(
            column.uuid,
            {
                "type": "kanban_card",
                "name": name or "Card",
                "description": description or "",
                "participants": participants,
                "owner": self._normalize_owner(owner, participants),
                "order": self._order_between(last_order, None),
            },
            {},
        )

    def update_card(self, card_uuid: str, name: str,
                    description: str = "",
                    participants: list[str] | None = None,
                    owner: str | None = None,
                    expected_state_hash: str | None = None) -> SessionResult:
        card = self._node(card_uuid, "kanban_card")
        if not card:
            return SessionResult("error", reason="card not found")
        # Lost-update guard (review U-3): the modal captured the card's
        # state_hash when it opened; if the card changed since (a peer
        # edit landed, or auto-adopt merged one) we'd silently overwrite
        # that with the stale form values. Reject instead so the user can
        # re-open against the merged card. Optional - callers that don't
        # pass a hash keep the old last-write-wins behavior.
        if expected_state_hash is not None and expected_state_hash != card.state_hash:
            return SessionResult("error", reason="card changed while you were editing")
        participants = participants or []
        data = dict(card.data)
        data.update({
            "name": name or "Card",
            "description": description or "",
            "participants": participants,
            "owner": self._normalize_owner(owner, participants),
        })
        return self.session.modify(card.uuid, data, card.weights)

    @staticmethod
    def _normalize_owner(owner: str | None, participants: list[str]) -> str | None:
        return owner if owner and owner in participants else None

    def delete_card(self, card_uuid: str) -> SessionResult:
        card = self._node(card_uuid, "kanban_card")
        if not card:
            return SessionResult("error", reason="card not found")
        return self.session.delete(card.uuid)

    def move_card(self, card_uuid: str, column_uuid: str, index: int) -> SessionResult:
        card = self._node(card_uuid, "kanban_card")
        column = self._node(column_uuid, "kanban_column")
        if not card or not column:
            return SessionResult("error", reason="card or column not found")

        def operation():
            moved = self.session.move_child(card.uuid, column.uuid, index)
            if moved.status != "ok":
                return moved
            effects = list(moved.effects)
            fresh_column = self.session.protocol.index[column.uuid]
            fresh_card = self.session.protocol.index[card.uuid]
            siblings = [item for item in self.cards(fresh_column) if item.uuid != card.uuid]
            placed = self._place_in_order(fresh_card, siblings, index)
            if placed.status != "ok":
                return placed
            effects.extend(placed.effects)
            return SessionResult("ok", value=True, effects=effects)

        return operation()

    def accept_peer_node(self, source_addr: str, node_uuid: str,
                         adopt_absence: bool = False) -> SessionResult:
        return self.session.accept_peer_node(source_addr, node_uuid, adopt_absence)

    def set_perspective_state(self, node_uuid: str, state: str) -> SessionResult:
        return self.session.set_perspective_state(node_uuid, state)

    def adopt_incoming_changes(self, board: PRSPNode | None = None) -> bool:
        board = board or self.ensure_board()
        mode = self.auto_adopt_mode(board)

        def eligible(node: PRSPNode, event_type: str) -> bool:
            is_card = node.data.get("type") == "kanban_card"
            if is_card:
                return self._auto_adopt_allows_node(mode, node)
            # Known consequence (review S-6/K-4): in not_owner/not_member,
            # a peer's brand-new column is ineligible here, and reconcile
            # walks events uuid-sorted rather than parents-first, so its
            # child cards can't adopt either (no local parent yet) - the
            # whole new column waits for manual adoption. Documented in
            # AUTO_ADOPT_DESCRIPTIONS; revisit if parents-first ordering
            # ever lands.
            #
            # A column (or any non-card node) that doesn't exist locally yet
            # would have to be adopted as a whole subtree, which could pull
            # in child cards the current mode is supposed to filter out.
            # Only "always" mode may adopt those; other modes leave it for
            # manual review.
            if event_type == "local_missing_node":
                return mode == "always"
            return True

        def adopt_mode(node: PRSPNode) -> str:
            # Update a non-card node's own fields only - never cascade into
            # its children, so an allowed column-rename can't smuggle in a
            # filtered-out card change underneath it.
            return "full" if node.data.get("type") == "kanban_card" else "shallow"

        changed = False
        for addr in sorted(self.session.peer_perspectives):
            if not self.session.peer_discusses_node(addr, board.uuid):
                continue
            # Collaboration topics belong to their author and always follow
            # that author's perspective, independently of the board's card
            # auto-adopt policy.
            changed = self._adopt_originator_agenda_changes(addr, board.uuid) or changed
            if mode == "never":
                continue

            def source_eligible(node: PRSPNode, event_type: str) -> bool:
                # Agenda changes are handled above using author authority;
                # never accept a forwarded/stale copy from another peer.
                if node.data.get("type") == "agenda_item":
                    return False
                return eligible(node, event_type)

            changed = self.session.reconcile_peer_changes(
                addr, board.uuid,
                node_is_eligible=source_eligible,
                node_adopt_mode=adopt_mode,
                # Per-node reconciliation is required here: replacing the
                # whole board could bypass agenda-author authority (and the
                # card ownership filters above) through a parent hash.
                allow_wholesale_replace=False,
            ) or changed
        return changed

    def _adopt_originator_agenda_changes(self, peer_addr: str,
                                          board_uuid: str) -> bool:
        """Make an agenda item's originator authoritative on every peer."""
        originator_uuid = self._peer_profile_uuid(peer_addr)
        if not originator_uuid:
            return False
        changed = False
        for event in self.session.analyze_peer_transitions(peer_addr, board_uuid):
            if event["type"] not in (
                "peer_made_changes", "local_missing_node", "divergence",
            ):
                continue
            node_uuid = event.get("node_uuid")
            peer_node = self.session.get_cached_peer_subtree(peer_addr, node_uuid)
            if not peer_node or peer_node.data.get("type") != "agenda_item":
                continue
            if peer_node.data.get("author") != originator_uuid:
                continue
            result = self.session.accept_peer_node(peer_addr, node_uuid)
            changed = changed or result.status == "ok"
        return changed

    def _auto_adopt_allows_node(self, mode: str, node: PRSPNode | None) -> bool:
        if mode == "always":
            return True
        if mode == "never":
            return False
        if not node or node.data.get("type") != "kanban_card":
            return True
        my_id = self.user_profile().uuid
        if mode == "not_owner":
            return node.data.get("owner") != my_id
        if mode == "not_member":
            return my_id not in (node.data.get("participants") or [])
        return True

    def on_peer_update(self) -> SessionResult:
        changed = self.adopt_all_incoming_changes()
        if not changed:
            return SessionResult("ok", value=False)
        return SessionResult(
            "ok",
            value=True,
            effects=self.session.sync_effects(None),
        )

    def adopt_all_incoming_changes(self) -> bool:
        changed = False
        for board in self.boards():
            active = self._is_active_discussion_node(board.uuid)
            mode = self.auto_adopt_mode(board)
            self.session.trace_event(
                "kanban.adopt_all_incoming_changes_check",
                board_uuid=board.uuid,
                active=active,
                mode=mode,
            )
            if active:
                changed = self.adopt_incoming_changes(board) or changed
        return changed

    def transition_events(self, board_uuid: str | None = None) -> list[dict]:
        board = self.ensure_board()
        board_uuid = board_uuid or board.uuid
        events = []
        for addr in sorted(self.session.peer_perspectives):
            if not self.session.peer_discusses_node(addr, board.uuid):
                continue
            events.extend(self.session.analyze_peer_transitions(addr, board_uuid))
        return self._debounce_divergences(events)

    def _debounce_divergences(self, events: list[dict]) -> list[dict]:
        # Display-only concern - reconciliation (adopt_incoming_changes)
        # calls session.analyze_peer_transitions directly, never through
        # here, so debouncing what the UI shows can never delay or mask
        # what auto-adopt actually acts on.
        #
        # A "divergence" classification can be a genuine conflict, or just
        # a timing artifact: the single-hop causal model
        # (Session._classify_content/_classify_move) only recognizes a
        # clean one-hop relationship as peer_made_changes/local_made_changes -
        # two rapid local changes landing before the peer's next sync tick
        # has caught up even once look identical to a real divergence, even
        # though it resolves itself the moment the peer's cache refreshes
        # (e.g. once a peer with auto-adopt on catches up and re-publishes).
        # Rather than flash an alarming "Conflict" label for something
        # very likely to self-resolve within a sync cycle or two, hold off
        # reporting it as a real divergence until it's been observed
        # continuously for a short grace window.
        now = time.time()
        still_diverging = set()
        out = []
        for event in events:
            node_uuid = event.get("node_uuid")
            peer_addr = event.get("peer_addr")
            if event["type"] != "divergence" or not node_uuid or not peer_addr:
                out.append(event)
                continue
            key = (peer_addr, node_uuid)
            still_diverging.add(key)
            first_seen = self._divergence_first_seen.setdefault(key, now)
            if now - first_seen < self._divergence_debounce_seconds:
                # Within the grace window - report as in_agreement for now
                # (nothing worth showing yet), not a lie about the actual
                # sync state, just a deferred display decision.
                out.append({**event, "type": "in_agreement"})
            else:
                out.append(event)
        # Drop tracking for anything not divergent this round, so a later
        # divergence on the same node starts its own fresh grace window.
        for key in set(self._divergence_first_seen) - still_diverging:
            del self._divergence_first_seen[key]
        return out

    def transition_by_node(self, events: list[dict]) -> dict:
        priority = {
            "divergence": 6,
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
                "keep_mine_active": event.get("keep_mine_active"),
                "priority": priority.get(event["type"], 0),
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
            [child for child in board.live_children() if child.data.get("type") == "kanban_column"],
            key=lambda node: (float(node.data.get("order", 0)), node.created_at),
        )

    def cards(self, column: PRSPNode) -> list[PRSPNode]:
        return sorted(
            [child for child in column.live_children() if child.data.get("type") == "kanban_card"],
            key=lambda node: (float(node.data.get("order", 0)), node.created_at),
        )

    def agenda_items(self, board: PRSPNode | None = None) -> list[PRSPNode]:
        board = board or self.ensure_board()
        return sorted(
            [child for child in board.live_children() if child.data.get("type") == "agenda_item"],
            key=lambda node: (
                -AGENDA_PRIORITY_RANK.get(node.data.get("priority"), 0),
                node.created_at,
            ),
        )

    def create_agenda_item(self, text: str, priority: str | None = None) -> SessionResult:
        board = self.ensure_board()
        return self.session.create_child(
            board.uuid,
            {
                "type": "agenda_item",
                "text": text or "",
                "priority": priority if priority in AGENDA_PRIORITIES else None,
                "author": self.user_profile().uuid,
            },
            {},
        )

    def delete_agenda_item(self, item_uuid: str) -> SessionResult:
        item = self._node(item_uuid, "agenda_item")
        if not item:
            return SessionResult("error", reason="agenda item not found")
        if item.data.get("author") != self.user_profile().uuid:
            return SessionResult("error", reason="only the topic originator can delete it")
        return self.session.delete(item.uuid)

    def set_agenda_item_priority(self, item_uuid: str, priority: str | None) -> SessionResult:
        item = self._node(item_uuid, "agenda_item")
        if not item:
            return SessionResult("error", reason="agenda item not found")
        if item.data.get("author") != self.user_profile().uuid:
            return SessionResult("error", reason="only the topic originator can set its priority")
        data = dict(item.data)
        data["priority"] = priority if priority in AGENDA_PRIORITIES else None
        return self.session.modify(item.uuid, data, item.weights)

    @staticmethod
    def _order_between(low: float | None, high: float | None) -> float | None:
        if low is None and high is None:
            return 0.0
        if low is None:
            return high - 1.0
        if high is None:
            return low + 1.0
        if high - low < ORDER_GAP_EPSILON:
            return None
        return (low + high) / 2.0

    def _place_in_order(self, moved: PRSPNode, siblings: list[PRSPNode],
                        index: int) -> SessionResult:
        index = max(0, min(index, len(siblings)))
        low = float(siblings[index - 1].data.get("order", 0)) if index > 0 else None
        high = float(siblings[index].data.get("order", 0)) if index < len(siblings) else None
        new_order = self._order_between(low, high)
        if new_order is None:
            ordered = list(siblings)
            ordered.insert(index, moved)
            return self._reorder(ordered)
        data = dict(moved.data)
        data["order"] = new_order
        return self.session.modify(moved.uuid, data, moved.weights)

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

    def _node(self, uuid: str, node_type: str) -> PRSPNode | None:
        node = self.session.protocol.index.get(uuid)
        if node and node.data.get("type") == node_type:
            return node
        return None

    def _remember_board(self, board_uuid: str, explicit: bool = False) -> None:
        metadata = self._metadata()
        metadata["selected_board_uuid"] = board_uuid
        if explicit:
            metadata["board_selection_explicit"] = True

    def _metadata(self) -> dict:
        apps = self.session.app_metadata.setdefault("apps", {})
        return apps.setdefault(KANBAN_APP_NAME, {})

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

    def _user_info(self, fallback_addr: str, profile: PRSPNode | None,
                   profile_uuid: str | None = None) -> dict:
        data = profile.data if profile else {}
        address = fallback_addr
        user_id = profile_uuid or (profile.uuid if profile else None)
        display_name = data.get("display_name") or ""
        if display_name == address or display_name.startswith(("http://", "https://")):
            display_name = ""
        return {
            "id": user_id or "",
            "profile_uuid": user_id or "",
            "address": address,
            "name": display_name or "?",
            "picture": data.get("picture") or "",
        }

    def _find_peer_user_profile(self, address: str) -> PRSPNode | None:
        return self.session.peer_identity(address)

    def _peer_profile_uuid(self, address: str, profile: PRSPNode | None = None) -> str:
        if profile:
            return profile.uuid
        for topic_uuid in self.session.fetch_topic_uuids(address):
            cached = self.session.get_cached_peer_subtree(address, topic_uuid)
            if cached and self._is_shared_user_topic(cached):
                return cached.uuid
        # No "assume it's the profile if it's not a board we recognize"
        # fallback here on purpose: that used to be safe when a peer's only
        # ever-fetched topics were exactly one board plus one profile
        # (join_discussion's own accept-time guarantee), but relay now
        # tracks every topic a peer publishes via peer_topic_sets - a board
        # this side never grafted locally has no entry in
        # self.session.protocol.index either, so "not a board" and "is the
        # profile" stopped meaning the same thing. Returning "" (unknown
        # for now) is correct; guessing wrong hands a peer's own board back
        # as if it were their identity.
        return ""

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

    def _boards_under(self, root: PRSPNode) -> list[PRSPNode]:
        out = []
        if root.data.get("type") == "kanban_board":
            out.append(root)
        for child in root.children:
            out.extend(self._boards_under(child))
        return out

    def _is_kanban_app_topic(self, node: PRSPNode | None) -> bool:
        if not node:
            return False
        return (
            node.data.get("type") == "kanban_app"
            and node.data.get("name") == KANBAN_APP_NAME
        )

    def _is_kanban_board_topic(self, node: PRSPNode | None) -> bool:
        if not node:
            return False
        return node.data.get("type") == "kanban_board"

    def _is_shared_user_topic(self, node: PRSPNode | None) -> bool:
        return self.session.is_identity_node(node)

    def _is_active_discussion_node(self, node_uuid: str) -> bool:
        return any(
            self.session._is_descendant_or_self(topic_uuid, node_uuid)
            for topic_uuid in self.session.active_topic_uuids
        )


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
        return await _json_result(runtime, logic.set_auto_adopt_mode(data.get("mode", "always")))

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

    async def api_set_board_objective(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.set_board_objective(
            data["board_uuid"],
            data.get("objective", ""),
        ))

    async def api_copy_board(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.copy_board(data["board_uuid"]))

    async def api_delete_board(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.delete_board(data["board_uuid"]))

    async def api_unshare_board(request: Request):
        data = await request.json()
        result = await asyncio.to_thread(
            logic.unshare_board,
            runtime,
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
            data.get("owner"),
        ))

    async def api_update_card(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.update_card(
            data["card_uuid"],
            data.get("name", "Card"),
            data.get("description", ""),
            _participants(data.get("participants")),
            data.get("owner"),
            expected_state_hash=data.get("expected_state_hash"),
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

    async def api_perspective(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.set_perspective_state(
            data["node_uuid"],
            data["state"],
        ))

    async def api_create_agenda_item(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.create_agenda_item(
            data.get("text", ""),
            data.get("priority"),
        ))

    async def api_delete_agenda_item(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.delete_agenda_item(data["item_uuid"]))

    async def api_set_agenda_item_priority(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.set_agenda_item_priority(
            data["item_uuid"],
            data.get("priority"),
        ))

    return [
        Route("/api/kanban/board", api_board),
        Route("/api/kanban/auto_adopt", api_auto_adopt, methods=["POST"]),
        Route("/api/kanban/boards/create", api_create_board, methods=["POST"]),
        Route("/api/kanban/boards/select", api_select_board, methods=["POST"]),
        Route("/api/kanban/boards/rename", api_rename_board, methods=["POST"]),
        Route("/api/kanban/boards/set_objective", api_set_board_objective, methods=["POST"]),
        Route("/api/kanban/boards/copy", api_copy_board, methods=["POST"]),
        Route("/api/kanban/boards/delete", api_delete_board, methods=["POST"]),
        Route("/api/kanban/profile", api_profile, methods=["POST"]),
        Route("/api/kanban/boards/unshare", api_unshare_board, methods=["POST"]),
        Route("/api/kanban/columns/create", api_create_column, methods=["POST"]),
        Route("/api/kanban/columns/rename", api_rename_column, methods=["POST"]),
        Route("/api/kanban/columns/delete", api_delete_column, methods=["POST"]),
        Route("/api/kanban/columns/move", api_move_column, methods=["POST"]),
        Route("/api/kanban/cards/create", api_create_card, methods=["POST"]),
        Route("/api/kanban/cards/update", api_update_card, methods=["POST"]),
        Route("/api/kanban/cards/delete", api_delete_card, methods=["POST"]),
        Route("/api/kanban/cards/move", api_move_card, methods=["POST"]),
        Route("/api/kanban/adopt", api_adopt, methods=["POST"]),
        Route("/api/kanban/perspective", api_perspective, methods=["POST"]),
        Route("/api/kanban/agenda/create", api_create_agenda_item, methods=["POST"]),
        Route("/api/kanban/agenda/delete", api_delete_agenda_item, methods=["POST"]),
        Route("/api/kanban/agenda/set_priority", api_set_agenda_item_priority, methods=["POST"]),
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

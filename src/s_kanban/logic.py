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
    POST /api/kanban/cards/comments/create {card_uuid, text}
    POST /api/kanban/cards/comments/delete {comment_uuid}
    POST /api/kanban/adopt               {source_addr, node_uuid, adopt_absence}
    POST /api/kanban/rollback            {source_addr, node_uuid}
"""

from __future__ import annotations

import copy
from typing import Any

from sovereign import (
    ApplicationRegistration, ProtocolNode, Session, SessionResult,
    avatar_attachment,
)


DEFAULT_COLUMNS = ["To Do", "Doing", "Done"]
KANBAN_APP_NAME = "S-Kanban"
KANBAN_APPLICATION_ID = "kanban"
ORDER_GAP_EPSILON = 1e-9
AUTO_ADOPT_MODES = ("always", "not_owner", "not_member", "never")
AGENDA_PRIORITIES = ("high", "medium", "low")
# Priority is optional (None/"" means "not set") - unset sorts below every
# explicit priority rather than defaulting to "medium".
AGENDA_PRIORITY_RANK = {"high": 3, "medium": 2, "low": 1}


class KanbanLogic:
    def __init__(self, session: Session, config: dict | None = None,
                 channel_manager=None):
        self.session = session
        self.config = config or {}
        self.channel_manager = channel_manager
        # Revision ownership must exist before the first board/card mutation.
        # Session.identity bootstraps its own origin without recursion.
        self.session.identity

    def application_registration(self) -> ApplicationRegistration:
        return ApplicationRegistration(
            KANBAN_APPLICATION_ID,
            frozenset({"kanban_board"}),
            self.boards,
            self.accept_board_invitation,
            assignment_scoped=True,
            mount_invitation=True,
            on_peer_update=self.on_peer_update,
        )

    def _channel_manager(self):
        return self.channel_manager

    def board_payload(self, auto_adopt: bool = True) -> dict:
        board = self.ensure_board()
        if auto_adopt:
            self.adopt_all_incoming_changes()
            board = self.ensure_board()
        events = self.transition_events(board.uuid)
        channel_manager = self._channel_manager()
        return {
            "address": self.session.address,
            "board": board.to_dict(),
            "boards": [item.to_dict() for item in self.boards()],
            "user_profile": self.user_profile().to_dict(),
            "users": self.users(),
            "network": self._network_info(board.uuid),
            "peers": {
                addr: tree.to_dict()
                for addr, tree in sorted(self.session.peer_perspectives.items())
            },
            "auto_adopt_mode": self.auto_adopt_mode(board),
            "transition_events": events,
            "transition_by_node": self.transition_by_node(events),
            "agenda_items": [item.to_dict() for item in self.agenda_items(board)],
            "comments_by_card": self._comments_by_card(board),
            "channel_targets": channel_manager.list_targets() if channel_manager else [],
            "channel_target_id": channel_manager.target_for_topic(board.uuid) if channel_manager else None,
        }

    def _comments_by_card(self, board: ProtocolNode) -> dict:
        # UI-friendly view of card comments: resolved author labels, sorted by
        # time, keyed by card uuid. The comments also live in the board tree as
        # card children, so they sync via the board topic - this is just the
        # convenient shape for the card modal.
        names = {user["id"]: user["name"] for user in self.users() if user.get("id")}
        out = {}
        for column in self.columns(board):
            for card in self.cards(column):
                comments = self.card_comments(card)
                if not comments:
                    continue
                out[card.uuid] = [
                    {
                        "uuid": comment.uuid,
                        "text": comment.data.get("text", ""),
                        "author": comment.data.get("author"),
                        "author_label": names.get(comment.data.get("author"), ""),
                        "created_at": comment.created_at,
                    }
                    for comment in comments
                ]
        return out

    def _network_info(self, board_uuid: str | None = None) -> dict:
        channel_manager = self._channel_manager()
        return (
            channel_manager.network_info(board_uuid)
            if channel_manager else self.session.get_network_info()
        )

    # The policy is Session's; Kanban only adds the two modes that are judged
    # against card ownership. Settings saved before the move still live under
    # this application's metadata, so they are read through rather than reset.
    def auto_adopt_mode(self, board: ProtocolNode | None = None) -> str:
        board = board or self.ensure_board()
        legacy = self._metadata().get("auto_adopt_by_board", {})
        fallback = self._normalize_auto_adopt_mode(
            legacy[board.uuid] if isinstance(legacy, dict) and board.uuid in legacy
            else self._metadata().get("auto_adopt", "always")
        )
        return self._normalize_auto_adopt_mode(
            self.session.auto_adopt_mode(board.uuid, fallback)
        )

    def set_auto_adopt_mode(self, mode: str) -> SessionResult:
        board = self.ensure_board()
        normalized = self._normalize_auto_adopt_mode(mode)
        return self.session.set_auto_adopt_mode(
            board.uuid, normalized, AUTO_ADOPT_MODES,
        )

    @staticmethod
    def _normalize_auto_adopt_mode(value: Any) -> str:
        if isinstance(value, bool):
            return "always" if value else "never"
        if value in AUTO_ADOPT_MODES:
            return value
        return "always"

    def ensure_board(self) -> ProtocolNode:
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

    def boards(self) -> list[ProtocolNode]:
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

    def accept_board_invitation(self, subtree: ProtocolNode) -> SessionResult:
        # Grafts a board first discovered through any channel into our own
        # board list. A genuinely new shared board starts
        # fully collaborative; reconnecting an existing board never reaches
        # this path and therefore retains its local per-board setting.
        was_known = subtree.uuid in self.session.protocol.index
        accepted = self.session.accept_topic_invitation(subtree, self._kanban_container().uuid)
        if accepted.status == "ok":
            if not was_known:
                self.session.set_auto_adopt_mode(
                    accepted.value, "always", AUTO_ADOPT_MODES,
                )
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
        channel_manager = self._channel_manager()
        if channel_manager:
            channel_manager.assign_topic_target(board_uuid, None)
        result = self.session.delete(board.uuid)
        if result.status != "ok":
            return result
        remaining = [item for item in self.boards() if item.uuid != board_uuid]
        if remaining:
            self._remember_board(remaining[0].uuid)
        return result

    def _create_board_node(self, name: str) -> ProtocolNode:
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

    def unshare_board(self, board_uuid: str | None = None) -> SessionResult:
        board = self.session.protocol.index.get(board_uuid) if board_uuid else self.ensure_board()
        if not board or board.data.get("type") != "kanban_board":
            return SessionResult("error", reason="board not found")
        # Detach the board before the peer check: token creation may already
        # have armed channel publication even when nobody accepted it yet.
        channel_manager = self._channel_manager()
        if channel_manager is not None:
            channel_manager.assign_topic_target(board.uuid, None)
        board_peers = [
            peer for peer, topics in sorted(self.session.peer_topic_sets.items())
            if board.uuid in topics
        ]
        if not board_peers:
            return SessionResult("ok", value={"topic_uuids": []})
        leave_result = self.session.leave_topic(board.uuid)
        effects = list(leave_result.effects)
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
            effects.extend(self.session.disconnect().effects)
        return SessionResult(
            "ok",
            value={"topic_uuids": sorted(removed_topics)},
            effects=effects,
        )

    def user_profile(self) -> ProtocolNode:
        return self.session.identity

    def users(self) -> list[dict]:
        users = [self._user_info(self.session.address, self.user_profile())]
        # Union with peer_perspectives, not just members - a relay-only
        # peer (e.g. "relay:B") never goes through add_peer (deliberately;
        # see Session.note_indirect_peer_topic), so it would never appear here
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

    def create_card_comment(self, card_uuid: str, text: str) -> SessionResult:
        # A comment is an immutable child node of the card (the agenda_item
        # pattern): concurrent comments set-union merge, and appending one
        # touches only the card's subtree hash, not its own content revision.
        card = self._node(card_uuid, "kanban_card")
        if not card:
            return SessionResult("error", reason="card not found")
        text = (text or "").strip()
        if not text:
            return SessionResult("error", reason="comment text is required")
        return self.session.create_child(
            card.uuid,
            {
                "type": "card_comment",
                "text": text,
                "author": self.user_profile().uuid,
            },
            {},
        )

    def card_comments(self, card: ProtocolNode) -> list[ProtocolNode]:
        return sorted(
            [child for child in card.live_children()
             if child.data.get("type") == "card_comment"],
            key=lambda node: node.created_at,
        )

    def delete_card_comment(self, comment_uuid: str) -> SessionResult:
        comment = self._node(comment_uuid, "card_comment")
        if not comment:
            return SessionResult("error", reason="comment not found")
        if comment.data.get("author") != self.user_profile().uuid:
            return SessionResult("error", reason="only the author can delete a comment")
        return self.session.delete(comment.uuid)

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
        # Session adopts an existing node's own fields shallowly (containers
        # keep their cards) and grafts only a brand-new subtree - no
        # kanban-specific container handling is needed anymore.
        return self.session.accept_peer_node(source_addr, node_uuid, adopt_absence)

    def rollback_peer_node(self, source_addr: str,
                           node_uuid: str,
                           rollback_absence: bool = False) -> SessionResult:
        return self.session.rollback_peer_node(
            source_addr, node_uuid, rollback_absence,
        )

    def adopt_incoming_changes(self, board: ProtocolNode | None = None) -> bool:
        board = board or self.ensure_board()
        mode = self.auto_adopt_mode(board)

        def eligible(node: ProtocolNode, event_type: str) -> bool:
            node_type = node.data.get("type")
            if node_type == "kanban_card":
                return self._auto_adopt_allows_node(mode, node)
            if node_type == "card_comment":
                # Comments are additive and author-stamped - always adopt one
                # (including a brand-new one) so a peer's note appears under any
                # auto-adopt mode, the way agenda items follow their author.
                return True
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

            def source_eligible(node: ProtocolNode, event_type: str) -> bool:
                # Agenda changes are handled above using author authority;
                # never accept a forwarded/stale copy from another peer.
                if node.data.get("type") == "agenda_item":
                    return False
                # Deleting a container removes its whole subtree at the
                # protocol level (no orphans), so Kanban decides here, before
                # the protocol acts: decline a column/board deletion while it
                # still holds a card this mode protects, or a later prune would
                # take that card with it. The container stays as a divergence
                # to resolve by hand; unprotected cards in it still delete
                # through their own per-node events.
                if (event_type == "peer_made_changes"
                        and node.data.get("type") in ("kanban_column", "kanban_board")):
                    peer = self.session.get_cached_peer_subtree(addr, node.uuid)
                    if (peer is not None and peer.deleted
                            and self._has_protected_descendant(mode, node)):
                        return False
                return eligible(node, event_type)

            changed = self.session.reconcile_peer_changes(
                addr, board.uuid,
                node_is_eligible=source_eligible,
            ) or changed
        return changed

    def _adopt_originator_agenda_changes(self, peer_addr: str,
                                          board_uuid: str) -> bool:
        """Make an agenda item's originator authoritative on every peer."""
        originator_profile = self._find_peer_user_profile(peer_addr)
        originator_uuid = self._peer_profile_uuid(peer_addr, originator_profile)
        if not originator_uuid:
            return False
        changed = False
        for event in self.session.analyze_peer_transitions(peer_addr, board_uuid):
            if event["type"] == "in_agreement":
                continue
            node_uuid = event.get("node_uuid")
            peer_node = self.session.get_cached_peer_subtree(peer_addr, node_uuid)
            local_node = self.session.protocol.index.get(node_uuid)
            authority_node = peer_node or local_node
            if (not authority_node
                    or authority_node.data.get("type") != "agenda_item"):
                continue
            if authority_node.data.get("author") != originator_uuid:
                continue
            # The originator is authoritative even when a revert makes the
            # generic one-hop hash classifier call the recipient's value
            # "newer" (local_made_changes). Absence is authoritative too.
            result = self.session.accept_peer_node(
                peer_addr, node_uuid, adopt_absence=peer_node is None,
            )
            changed = changed or result.status == "ok"
        return changed

    def _auto_adopt_allows_node(self, mode: str, node: ProtocolNode | None) -> bool:
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

    def _has_protected_descendant(self, mode: str, node: ProtocolNode) -> bool:
        # True if any live card under `node` is one this mode keeps (an owned
        # card under not_owner, a joined card under not_member) - i.e. adopting
        # a deletion of `node` would remove a card the policy protects.
        for child in node.children:
            if child.deleted:
                continue
            if child.data.get("type") == "kanban_card":
                if not self._auto_adopt_allows_node(mode, child):
                    return True
            elif self._has_protected_descendant(mode, child):
                return True
        return False

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
            for event in self.session.analyze_peer_transitions(addr, board_uuid):
                event["changes"] = (
                    [] if event["type"] == "in_agreement"
                    else self.describe_peer_changes(
                        addr, event.get("node_uuid"),
                    )
                )
                events.append(event)
        return events

    def describe_peer_changes(self, peer_addr: str,
                              node_uuid: str | None) -> list[dict]:
        """Describe the peer's current version relative to this client.

        These are semantic current-version differences, not an audit log:
        in a true two-sided divergence they intentionally say what the peer
        version contains relative to mine, without claiming which historical
        operation produced it.
        """
        if not node_uuid:
            return []
        local = self.session.protocol.index.get(node_uuid)
        peer = self.session.get_cached_peer_subtree(peer_addr, node_uuid)
        if not local and not peer:
            return []
        if (local and peer
                and not (local.state_hash != peer.state_hash
                         or local.parent_uuid != peer.parent_uuid)):
            return []
        node = peer or local
        node_type = node.data.get("type") or "node"
        node_label = {
            "kanban_card": "Card",
            "kanban_column": "Column",
            "kanban_board": "Board",
            "agenda_item": "Discussion topic",
        }.get(node_type, "Item")
        if not local:
            return [{
                "kind": "presence",
                "field": "node",
                "label": node_label,
                "local_value": None,
                "peer_value": "present",
                "summary": f"{node_label} exists only in the peer version",
                "local_summary": f"Keep {node_label.lower()} absent",
            }]
        if not peer:
            return [{
                "kind": "presence",
                "field": "node",
                "label": node_label,
                "local_value": "present",
                "peer_value": None,
                "summary": f"{node_label} exists only in your version",
                "local_summary": f"Keep your {node_label.lower()}",
            }]

        changes: list[dict] = []

        def add_field(field: str, label: str, summary: str,
                      local_summary: str) -> None:
            changes.append({
                "kind": "field",
                "field": field,
                "label": label,
                "local_value": local.data.get(field),
                "peer_value": peer.data.get(field),
                "summary": summary,
                "local_summary": local_summary,
            })

        if local.deleted != peer.deleted:
            changes.append({
                "kind": "deletion",
                "field": "deleted",
                "label": node_label,
                "local_value": local.deleted,
                "peer_value": peer.deleted,
                "summary": (
                    f"{node_label} is deleted in the peer version"
                    if peer.deleted else
                    f"{node_label} is present in the peer version"
                ),
                "local_summary": (
                    f"Keep your {node_label.lower()} present"
                    if peer.deleted else
                    f"Keep your {node_label.lower()} deleted"
                ),
            })

        if local.parent_uuid != peer.parent_uuid:
            local_parent = self.session.protocol.index.get(local.parent_uuid)
            peer_parent = self.session.get_cached_peer_subtree(
                peer_addr, peer.parent_uuid,
            )
            local_parent_name = self._node_display_name(local_parent)
            peer_parent_name = self._node_display_name(peer_parent)
            changes.append({
                "kind": "move",
                "field": "parent_uuid",
                "label": "Column" if node_type == "kanban_card" else "Location",
                "local_value": local.parent_uuid,
                "peer_value": peer.parent_uuid,
                "local_label": local_parent_name,
                "peer_label": peer_parent_name,
                "summary": (
                    f'Move from "{local_parent_name}" to "{peer_parent_name}"'
                ),
                "local_summary": (
                    f'Move from "{peer_parent_name}" to "{local_parent_name}"'
                ),
            })
        elif (local.data.get("order") != peer.data.get("order")
              and "order" in (set(local.data) | set(peer.data))):
            changes.append({
                "kind": "position",
                "field": "order",
                "label": "Position",
                "local_value": local.data.get("order"),
                "peer_value": peer.data.get("order"),
                "summary": "Use peer position",
                "local_summary": "Keep your current position",
            })

        scalar_labels = {
            "name": "Name",
            "description": "Description",
            "objective": "Objective",
            "text": "Text",
            "priority": "Priority",
        }
        for field, label in scalar_labels.items():
            local_value = local.data.get(field)
            peer_value = peer.data.get(field)
            if local_value == peer_value:
                continue
            if field in ("description", "objective"):
                summary = f"Use peer {label.lower()}"
                local_summary = f"Keep your {label.lower()}"
            else:
                summary = (
                    f"{label}: {self._display_value(local_value)}"
                    f" → {self._display_value(peer_value)}"
                )
                local_summary = (
                    f"{label}: {self._display_value(peer_value)}"
                    f" → {self._display_value(local_value)}"
                )
            add_field(field, label, summary, local_summary)

        if node_type == "kanban_card":
            local_participants = set(local.data.get("participants") or [])
            peer_participants = set(peer.data.get("participants") or [])
            added = sorted(peer_participants - local_participants)
            removed = sorted(local_participants - peer_participants)
            if added or removed:
                added_labels = [self._participant_name(item) for item in added]
                removed_labels = [self._participant_name(item) for item in removed]
                parts = []
                if added_labels:
                    if len(added_labels) == 1:
                        parts.append(f"Add {added_labels[0]} as participant")
                    else:
                        parts.append(f"Add {', '.join(added_labels)} as participants")
                if removed_labels:
                    if len(removed_labels) == 1:
                        parts.append(f"Remove {removed_labels[0]} as participant")
                    else:
                        parts.append(f"Remove {', '.join(removed_labels)} as participants")
                local_parts = []
                if removed_labels:
                    if len(removed_labels) == 1:
                        local_parts.append(f"Add {removed_labels[0]} as participant")
                    else:
                        local_parts.append(f"Add {', '.join(removed_labels)} as participants")
                if added_labels:
                    if len(added_labels) == 1:
                        local_parts.append(f"Remove {added_labels[0]} as participant")
                    else:
                        local_parts.append(f"Remove {', '.join(added_labels)} as participants")
                changes.append({
                    "kind": "participants",
                    "field": "participants",
                    "label": "Participants",
                    "local_value": sorted(local_participants),
                    "peer_value": sorted(peer_participants),
                    "added": added,
                    "removed": removed,
                    "added_labels": added_labels,
                    "removed_labels": removed_labels,
                    "summary": "; ".join(parts),
                    "local_summary": "; ".join(local_parts),
                })
            local_owner = local.data.get("owner")
            peer_owner = peer.data.get("owner")
            if local_owner != peer_owner:
                changes.append({
                    "kind": "owner",
                    "field": "owner",
                    "label": "Owner",
                    "local_value": local_owner,
                    "peer_value": peer_owner,
                    "local_label": self._participant_name(local_owner),
                    "peer_label": self._participant_name(peer_owner),
                    "summary": (
                        f"Owner: {self._participant_name(local_owner)}"
                        f" → {self._participant_name(peer_owner)}"
                    ),
                    "local_summary": (
                        f"Owner: {self._participant_name(peer_owner)}"
                        f" → {self._participant_name(local_owner)}"
                    ),
                })

        if local.weights != peer.weights:
            changes.append({
                "kind": "weights",
                "field": "weights",
                "label": "Weights",
                "local_value": local.weights,
                "peer_value": peer.weights,
                "summary": "Weights changed",
                "local_summary": "Keep your current weights",
            })
        return changes

    @staticmethod
    def _node_display_name(node: ProtocolNode | None) -> str:
        if not node:
            return "Unknown"
        return str(node.data.get("name") or node.data.get("text") or "Untitled")

    @staticmethod
    def _display_value(value: Any) -> str:
        if value in (None, ""):
            return "None"
        return f'"{value}"'

    def _participant_name(self, participant: str | None) -> str:
        if not participant:
            return "Unassigned"
        for user in self.users():
            if participant in (
                user.get("id"), user.get("profile_uuid"),
                user.get("identity_key"), user.get("address"),
            ):
                name = user.get("name")
                if name and name != "?":
                    return name
        return str(participant)[:8]

    def transition_by_node(self, events: list[dict]) -> dict:
        priority = Session.TRANSITION_PRIORITY
        out = {}
        for event in events:
            node_uuid = event.get("node_uuid")
            if not node_uuid:
                continue
            event_info = {
                "type": event["type"],
                "original_type": event.get("original_type"),
                "peer_addr": event.get("peer_addr"),
                "origin_identity": event.get("origin_identity"),
                "local_revision_origin": event.get(
                    "local_revision_origin",
                ),
                "peer_revision_origin": event.get(
                    "peer_revision_origin",
                ),
                "local_state_hash": event.get("local_state_hash"),
                "peer_state_hash": event.get("peer_state_hash"),
                "local_base_hash": event.get("local_base_hash"),
                "peer_base_hash": event.get("peer_base_hash"),
                "local_revision": event.get("local_revision"),
                "peer_revision": event.get("peer_revision"),
                "changes": event.get("changes") or [],
                "reaction": self._reaction_for_event(event),
                "peer_observed_local_revision": event.get(
                    "peer_observed_local_revision", False,
                ),
                "priority": priority.get(event["type"], 0),
            }
            signature = self._transition_event_signature(event_info)
            current = out.get(node_uuid)
            if current:
                if event["type"] != "in_agreement":
                    existing = next((
                        item for item in current.setdefault("events", [])
                        if self._transition_event_signature(item) == signature
                    ), None)
                    if existing:
                        deliveries = existing.setdefault(
                            "delivery_peer_addrs",
                            [existing.get("peer_addr")],
                        )
                        if event_info.get("peer_addr") not in deliveries:
                            deliveries.append(event_info.get("peer_addr"))
                        if (self._peer_is_revision_origin(event_info)
                                and not self._peer_is_revision_origin(existing)):
                            existing["peer_addr"] = event_info.get("peer_addr")
                            if self._transition_event_signature(current) == signature:
                                current["peer_addr"] = event_info.get("peer_addr")
                        continue
                    current.setdefault("events", []).append(dict(event_info))
                if priority.get(current["type"], 0) >= priority.get(event["type"], 0):
                    continue
                current.update(event_info)
                continue
            out[node_uuid] = dict(event_info)
            if event["type"] != "in_agreement":
                out[node_uuid]["events"] = [dict(event_info)]
        return out

    def _reaction_for_event(self, event: dict) -> str:
        # Session owns this: it is decided purely from revision origins and
        # base hashes, and every application needs the same answer.
        return self.session.reaction_for_event(event)

    @staticmethod
    def _transition_event_signature(event: dict) -> tuple:
        # Key on the pre-staging (logical) type, not the staged `type`:
        # staging splits one situation into divergence/in_transition per the
        # per-peer "observed" flag, so keying on `type` would show the same
        # target revision held by two peers as two entries. original_type is
        # the same for both, so they dedupe into one (with both peers in
        # delivery_peer_addrs).
        return (
            event.get("original_type") or event.get("type"),
            event.get("origin_identity"),
            event.get("local_revision") or event.get("local_state_hash"),
            event.get("peer_revision") or event.get("peer_state_hash"),
        )

    def _peer_is_revision_origin(self, event: dict) -> bool:
        origin = event.get("origin_identity")
        peer_addr = event.get("peer_addr")
        return bool(
            origin and peer_addr
            and self.session.peer_identity_key.get(peer_addr) == origin
        )

    def columns(self, board: ProtocolNode | None = None) -> list[ProtocolNode]:
        board = board or self.ensure_board()
        return sorted(
            [child for child in board.live_children() if child.data.get("type") == "kanban_column"],
            key=lambda node: (float(node.data.get("order", 0)), node.created_at),
        )

    def cards(self, column: ProtocolNode) -> list[ProtocolNode]:
        return sorted(
            [child for child in column.live_children() if child.data.get("type") == "kanban_card"],
            key=lambda node: (float(node.data.get("order", 0)), node.created_at),
        )

    # Agendas are Session's - an agenda item is a child of the topic root, and
    # a board is one. These stay only to keep Kanban's board-scoped calling
    # convention; the rules live in one place.
    def agenda_items(self, board: ProtocolNode | None = None) -> list[ProtocolNode]:
        board = board or self.ensure_board()
        return self.session.agenda_items(board.uuid)

    def create_agenda_item(self, text: str, priority: str | None = None) -> SessionResult:
        return self.session.create_agenda_item(
            self.ensure_board().uuid, text, priority,
        )

    def delete_agenda_item(self, item_uuid: str) -> SessionResult:
        return self.session.delete_agenda_item(item_uuid)

    def set_agenda_item_priority(self, item_uuid: str, priority: str | None) -> SessionResult:
        return self.session.set_agenda_item_priority(item_uuid, priority)

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

    def _place_in_order(self, moved: ProtocolNode, siblings: list[ProtocolNode],
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

    def _reorder(self, nodes: list[ProtocolNode]) -> SessionResult:
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

    def _node(self, uuid: str, node_type: str) -> ProtocolNode | None:
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
        return apps.setdefault(KANBAN_APPLICATION_ID, {})

    def _kanban_container(self) -> ProtocolNode:
        return self._folder(self._apps_folder(), KANBAN_APP_NAME, "kanban_app")

    def _kanban_containers(self) -> list[ProtocolNode]:
        active = [
            self.session.protocol.index[uuid]
            for uuid in sorted(self.session.active_topic_uuids)
            if uuid in self.session.protocol.index
            and self._is_kanban_app_topic(self.session.protocol.index[uuid])
        ]
        if active:
            return active
        return [self._kanban_container()]

    def _apps_folder(self) -> ProtocolNode:
        return self._folder(self.session.protocol.root, "apps")

    def _user_info(self, fallback_addr: str, profile: ProtocolNode | None,
                   profile_uuid: str | None = None) -> dict:
        data = profile.data if profile else {}
        address = fallback_addr
        user_id = profile_uuid or (profile.uuid if profile else None)
        display_name = data.get("display_name") or ""
        if display_name == address or display_name.startswith(("http://", "https://")):
            display_name = ""
        avatar = avatar_attachment(data)
        return {
            "id": user_id or "",
            "profile_uuid": user_id or "",
            "identity_key": data.get("identity_key") or "",
            "address": address,
            "name": display_name or "?",
            "picture": (
                f"/api/blob/{avatar['blob_id']}" if avatar
                else data.get("picture") or ""
            ),
            "picture_blob_id": avatar["blob_id"] if avatar else "",
        }

    def _find_peer_user_profile(self, address: str) -> ProtocolNode | None:
        return self.session.peer_identity(address)

    def _peer_profile_uuid(self, address: str, profile: ProtocolNode | None = None) -> str:
        if profile:
            return profile.uuid
        for topic_uuid in self.session.fetch_topic_uuids(address):
            cached = self.session.get_cached_peer_subtree(address, topic_uuid)
            if cached and self._is_shared_user_topic(cached):
                return cached.uuid
        # No "assume it's the profile if it's not a board we recognize"
        # fallback here on purpose: that used to be safe when a peer's only
        # ever-fetched topics were exactly one board plus one profile
        # (join_discussion's own accept-time guarantee), but an indirect channel now
        # tracks every topic a peer publishes via peer_topic_sets - a board
        # this side never grafted locally has no entry in
        # self.session.protocol.index either, so "not a board" and "is the
        # profile" stopped meaning the same thing. Returning "" (unknown
        # for now) is correct; guessing wrong hands a peer's own board back
        # as if it were their identity.
        return ""

    def _folder(self, parent: ProtocolNode, name: str,
                node_type: str = "folder") -> ProtocolNode:
        for child in parent.children:
            if child.data.get("name") == name and child.data.get("type") in ("folder", node_type):
                return child
        created = self.session.create_child(
            parent.uuid,
            {"type": node_type, "name": name},
            {},
        ).value
        return created

    def _boards_under(self, root: ProtocolNode) -> list[ProtocolNode]:
        out = []
        if root.data.get("type") == "kanban_board":
            out.append(root)
        for child in root.children:
            out.extend(self._boards_under(child))
        return out

    def _is_kanban_app_topic(self, node: ProtocolNode | None) -> bool:
        if not node:
            return False
        return (
            node.data.get("type") == "kanban_app"
            and node.data.get("name") == KANBAN_APP_NAME
        )

    def _is_kanban_board_topic(self, node: ProtocolNode | None) -> bool:
        if not node:
            return False
        return node.data.get("type") == "kanban_board"

    def _is_shared_user_topic(self, node: ProtocolNode | None) -> bool:
        return self.session.is_identity_node(node)

    def _is_active_discussion_node(self, node_uuid: str) -> bool:
        return any(
            self.session._is_descendant_or_self(topic_uuid, node_uuid)
            for topic_uuid in self.session.active_topic_uuids
        )


def create_logic(session: Session, config: dict) -> KanbanLogic:
    logic = KanbanLogic(session, config)
    session.register_application(logic.application_registration())
    return logic

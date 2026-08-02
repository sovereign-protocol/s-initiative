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
    GET  /api/initiative/board
    POST /api/initiative/boards/set_objective {board_uuid, objective}
    POST /api/initiative/agenda/create        {text, priority}  # priority optional
    POST /api/initiative/agenda/delete        {item_uuid}
    POST /api/initiative/agenda/set_priority  {item_uuid, priority}  # priority optional, clears if omitted
    POST /api/initiative/auto_adopt          {mode}  # one of: always, not_owner, not_member, never
    POST /api/initiative/columns/create      {name}
    POST /api/initiative/columns/rename      {column_uuid, name}
    POST /api/initiative/columns/delete      {column_uuid}
    POST /api/initiative/columns/move        {column_uuid, index}
    POST /api/initiative/cards/create        {column_uuid, name, description, participants, owner}
    POST /api/initiative/cards/update        {card_uuid, name, description, participants, owner}
    POST /api/initiative/cards/delete        {card_uuid}
    POST /api/initiative/cards/move          {card_uuid, column_uuid, index}
    POST /api/initiative/cards/comments/create {card_uuid, text}
    POST /api/initiative/cards/comments/delete {comment_uuid}
    POST /api/initiative/adopt               {source_addr, node_uuid, adopt_absence}
    POST /api/initiative/rollback            {source_addr, node_uuid}
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from sovereign import (
    ApplicationRegistration, ProtocolNode, Session, SessionResult,
    avatar_attachment, canonical_attachments,
)


DEFAULT_COLUMNS = ["To Do", "Doing", "Done"]
INITIATIVE_APP_NAME = "S-Initiative"
INITIATIVE_APPLICATION_ID = "initiative"
AUTO_ADOPT_MODES = ("always", "not_owner", "not_member", "never")
AGENDA_PRIORITIES = ("high", "medium", "low")
DISPLAYED_DIVERGENCE_TYPES = frozenset({
    "kanban_board", "kanban_column", "kanban_card",
})
OWNED_NODE_TYPES = frozenset({
    *DISPLAYED_DIVERGENCE_TYPES,
    "agenda_item", "card_attachment", "card_comment",
})


class InitiativeLogic:
    def __init__(self, session: Session, config: dict | None = None,
                 collaboration=None):
        self.session = session
        self.config = config or {}
        self.collaboration = collaboration
        # Revision ownership must exist before the first board/card mutation.
        # Session.identity bootstraps its own origin without recursion.
        self.session.identity
        with self.session.lock:
            self.session.application_metadata(INITIATIVE_APPLICATION_ID)

    def application_registration(self) -> ApplicationRegistration:
        return ApplicationRegistration(
            INITIATIVE_APPLICATION_ID,
            frozenset({"kanban_board"}),
            self.boards,
            self.accept_board_invitation,
            assignment_scoped=True,
            mount_invitation=True,
            on_peer_update=self.on_peer_update,
        )

    def board_payload(self, network: dict | None = None) -> dict:
        """Return the current board view without reconciling or creating data."""
        boards = self.boards()
        board = self._selected_board(boards)
        network = (
            self._network_info(board.uuid if board else None)
            if network is None else network
        )
        events = self.transition_events(board.uuid, network) if board else []
        return {
            "address": self.session.address,
            "board": board.to_dict() if board else None,
            "boards": [item.to_dict() for item in boards],
            "user_profile": self.user_profile().to_dict(),
            "users": self.users(),
            "network": network,
            "peers": {
                addr: tree.to_dict()
                for addr, tree in sorted(
                    self.session.peer_perspectives_for_topic().items(),
                )
            },
            "auto_adopt_mode": (
                self.auto_adopt_mode(board) if board else "always"
            ),
            # The shell renders the adoption control and the agenda, so it
            # needs this application's mode set and who "mine" is.
            "auto_adopt_modes": list(AUTO_ADOPT_MODES),
            "identity_uuid": self.session.identity.uuid,
            "known_identities": self.session.known_identities(),
            "transition_events": events,
            "transition_by_node": self.transition_by_node(events),
            "agenda_items": (
                [item.to_dict() for item in self.agenda_items(board)]
                if board else []
            ),
            "comments_by_card": self._comments_by_card(board) if board else {},
            "attachments_by_card": (
                self._attachments_by_card(board) if board else {}
            ),
        }

    def board_snapshot(self) -> dict:
        """Build board state under Session without consulting transport."""
        payload = self.board_payload({"_include_all": True})
        board = payload.get("board") or {}
        return {
            "payload": payload,
            "topic_uuid": board.get("uuid"),
        }

    @classmethod
    def merge_board_observation(
        cls, snapshot: dict, network: dict,
    ) -> dict:
        """Decorate a detached board snapshot with current channel liveness."""
        payload = snapshot["payload"]
        events = [
            event for event in payload.get("transition_events", [])
            if cls._transition_visible(event, network)
        ]
        payload["network"] = network
        payload["transition_events"] = events
        payload["transition_by_node"] = cls._filter_transition_groups(
            payload.get("transition_by_node", {}), network,
        )
        return payload

    @classmethod
    def _filter_transition_groups(
        cls, grouped: dict, network: dict,
    ) -> dict:
        filtered = {}
        for node_uuid, group in grouped.items():
            candidates = group.get("events") or [group]
            visible = [
                event for event in candidates
                if cls._transition_visible(event, network)
            ]
            if not visible:
                continue
            top = max(
                visible,
                key=lambda event: tuple(
                    event.get("priority") or Session.transition_rank(event),
                ),
            )
            merged = dict(top)
            if any(event.get("type") != "in_agreement" for event in visible):
                merged["events"] = visible
            filtered[node_uuid] = merged
        return filtered

    @staticmethod
    def _transition_visible(event: dict, network: dict) -> bool:
        if event.get("stage") != "in_flight":
            return True
        peers = network.get("peers") or {}
        addresses = event.get("delivery_peer_addrs") or [
            event.get("peer_addr"),
        ]
        return any(
            (
                (peers.get(address) or {}).get("channel_liveness") or {}
            ).get("state") == "alive"
            for address in addresses if address
        )

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

    def _attachments_by_card(self, board: ProtocolNode) -> dict:
        # Same shape and reasoning as _comments_by_card: the files live in the
        # board tree as card children and sync with the board topic; this is
        # only the convenient view for the modal, with the download URL Core
        # already serves resolved for each blob.
        names = {user["id"]: user["name"] for user in self.users() if user.get("id")}
        out = {}
        for column in self.columns(board):
            for card in self.cards(column):
                entries = []
                for node in self.card_attachments(card):
                    for item in canonical_attachments(node.data.get("attachments")):
                        entries.append({
                            "uuid": node.uuid,
                            "name": item["name"],
                            "size": item["size"],
                            "mime": item["mime"],
                            "url": f"/api/blob/{item['blob_id']}",
                            "author": node.data.get("author"),
                            "author_label": names.get(node.data.get("author"), ""),
                            "created_at": node.created_at,
                        })
                if entries:
                    out[card.uuid] = entries
        return out

    def _network_info(self, board_uuid: str | None = None) -> dict:
        return (
            self.collaboration.network_info(board_uuid)
            if self.collaboration else self.session.get_network_info()
        )

    def network_info(self, board_uuid: str | None = None) -> dict:
        return self._network_info(board_uuid)

    def collaboration_context(
        self, topic_uuid: str, network: dict | None = None,
    ) -> dict:
        board = self._node(topic_uuid, "kanban_board")
        if not board:
            return {}
        network = (
            self.network_info(topic_uuid) if network is None else network
        )
        events = self.transition_events(topic_uuid, network)
        return {
            "agenda_items": [
                item.to_dict() for item in self.session.agenda_items(topic_uuid)
            ],
            "transition_events": events,
            "transition_by_node": self.transition_by_node(events),
            "identity_uuid": self.session.identity.uuid,
            "known_identities": self.session.known_identities(),
            "auto_adopt_mode": self.auto_adopt_mode(board),
            "auto_adopt_modes": list(AUTO_ADOPT_MODES),
        }

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

    def set_auto_adopt_mode(
        self, mode: str, board_uuid: str | None = None,
    ) -> SessionResult:
        board = (
            self._node(board_uuid, "kanban_board")
            if board_uuid else self.ensure_board()
        )
        if not board:
            return SessionResult("error", reason="board not found")
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
        board = self._selected_board(self.boards())
        if board:
            self._remember_board(board.uuid)
            return board
        board = self._create_board_node("Kanban Board")
        self._remember_board(board.uuid)
        return board

    def _selected_board(
        self, boards: list[ProtocolNode],
    ) -> ProtocolNode | None:
        """Choose a board without changing selection metadata."""
        remembered_uuid = self._metadata().get("selected_board_uuid")
        explicit = bool(self._metadata().get("board_selection_explicit"))
        remembered = self.session.protocol.index.get(remembered_uuid) if remembered_uuid else None
        if explicit and remembered and remembered.data.get("type") == "kanban_board":
            return remembered
        for active in self.session.active_topics():
            if active.data.get("type") == "kanban_board":
                return active
            if active and self._is_initiative_app_topic(active):
                active_boards = self._boards_under(active)
                if active_boards:
                    return active_boards[0]
        if remembered and remembered.data.get("type") == "kanban_board":
            return remembered
        for node in boards:
            return node
        return None

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
        return SessionResult("ok", value=board.uuid)

    def select_board(self, board_uuid: str) -> SessionResult:
        board = self._node(board_uuid, "kanban_board")
        if not board:
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
        board = self._node(board_uuid, "kanban_board")
        if not board:
            return SessionResult("error", reason="board not found")
        data = dict(board.data)
        data["name"] = name or "Kanban Board"
        return self.session.modify(board.uuid, data, board.weights)

    def set_board_objective(self, board_uuid: str, objective: str) -> SessionResult:
        board = self._node(board_uuid, "kanban_board")
        if not board:
            return SessionResult("error", reason="board not found")
        data = dict(board.data)
        data["objective"] = objective or ""
        return self.session.modify(board.uuid, data, board.weights)

    def copy_board(self, board_uuid: str) -> SessionResult:
        board = self._node(board_uuid, "kanban_board")
        if not board:
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
        return SessionResult("ok", value=clone.uuid)

    def delete_board(self, board_uuid: str) -> SessionResult:
        # The last board goes too. Refusing it left no way to clear a host
        # of boards it no longer wants, and there is nothing to protect:
        # ensure_board() makes a fresh empty one the next time the board
        # view is opened, exactly as it does on a first run.
        board = self._node(board_uuid, "kanban_board")
        if not board:
            return SessionResult("error", reason="board not found")
        release = self.session.end_topic_sharing(board_uuid)
        result = self.session.delete(board.uuid)
        if result.status != "ok":
            return result
        result.effects = [*release.effects, *result.effects]
        remaining = [item for item in self.boards() if item.uuid != board_uuid]
        # Clearing the selection matters when nothing is left: a board still
        # awaiting its peers' confirmation stays in the index as a deleted
        # node, and a remembered uuid would hand that corpse back as the
        # current board instead of letting ensure_board() start a new one.
        self._remember_board(remaining[0].uuid if remaining else "")
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

    def user_profile(self) -> ProtocolNode:
        return self.session.identity

    def users(self) -> list[dict]:
        users = [self._user_info(self.session.address, self.user_profile())]
        addrs = set(self.session.peer_addresses()) - {self.session.address}
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
        return self.session.create_child(
            board.uuid,
            {
                "type": "kanban_column",
                "name": name or "Column",
                "order": self.session.next_child_order(
                    board.uuid, "kanban_column",
                ),
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
        column = self._node(column_uuid, "kanban_column")
        if not column:
            return SessionResult("error", reason="column not found")
        return self.session.delete(column.uuid)

    def move_column(self, column_uuid: str, index: int) -> SessionResult:
        board = self.ensure_board()
        column = self._node(column_uuid, "kanban_column")
        if not column or column.parent_uuid != board.uuid:
            return SessionResult("error", reason="column not found")
        return self.session.move_child_to_parent_index(
            column.uuid, board.uuid, index,
        )

    def create_card(self, column_uuid: str, name: str,
                    description: str = "",
                    participants: list[str] | None = None,
                    owner: str | None = None) -> SessionResult:
        column = self._node(column_uuid, "kanban_column")
        if not column:
            return SessionResult("error", reason="column not found")
        participants = participants or []
        return self.session.create_child(
            column.uuid,
            {
                "type": "kanban_card",
                "name": name or "Card",
                "description": description or "",
                "participants": participants,
                "owner": self._normalize_owner(owner, participants),
                "order": self.session.next_child_order(
                    column.uuid, "kanban_card",
                ),
            },
            {},
        )

    def update_card(self, card_uuid: str, name: str,
                    description: str = "",
                    participants: list[str] | None = None,
                    owner: str | None = None,
                    expected_content_hash: str | None = None) -> SessionResult:
        card = self._node(card_uuid, "kanban_card")
        if not card:
            return SessionResult("error", reason="card not found")
        # Lost-update guard (review U-3): the modal captured the card's
        # content_hash when it opened; if the card changed since (a peer
        # edit landed, or auto-adopt merged one) we'd silently overwrite
        # that with the stale form values. Reject instead so the user can
        # re-open against the merged card. Optional - callers that don't
        # pass a hash keep the old last-write-wins behavior.
        # Deliberately content_hash, not state_hash: only the fields this
        # form actually edits may block a save. state_hash also covers the
        # card's children, so a comment - which this very class documents as
        # touching the subtree hash but not the card's own revision - would
        # otherwise lock the user out of saving their own edit.
        if expected_content_hash is not None and expected_content_hash != card.content_hash:
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

    def create_card_attachment(self, card_uuid: str,
                               attachment: dict) -> SessionResult:
        # A child node, not a field on the card, for the same reason comments
        # are: two people attaching at once then set-union merge instead of
        # diverging the card's own content. Core's blob machinery walks every
        # node's "attachments", so publication, peer fetch and GC need no
        # Kanban-specific knowledge.
        card = self._node(card_uuid, "kanban_card")
        if not card:
            return SessionResult("error", reason="card not found")
        references = canonical_attachments([attachment])
        if not references:
            return SessionResult("error", reason="a valid file reference is required")
        return self.session.create_child(
            card.uuid,
            {
                "type": "card_attachment",
                "author": self.user_profile().uuid,
                "attachments": references,
            },
            {},
        )

    def card_attachments(self, card: ProtocolNode) -> list[ProtocolNode]:
        return sorted(
            [child for child in card.live_children()
             if child.data.get("type") == "card_attachment"],
            key=lambda node: node.created_at,
        )

    def delete_card_attachment(self, attachment_uuid: str) -> SessionResult:
        attachment = self._node(attachment_uuid, "card_attachment")
        if not attachment:
            return SessionResult("error", reason="attachment not found")
        if attachment.data.get("author") != self.user_profile().uuid:
            return SessionResult(
                "error", reason="only the author can remove an attachment",
            )
        return self.session.delete(attachment.uuid)

    def move_card(self, card_uuid: str, column_uuid: str, index: int) -> SessionResult:
        card = self._node(card_uuid, "kanban_card")
        column = self._node(column_uuid, "kanban_column")
        if not card or not column:
            return SessionResult("error", reason="card or column not found")

        moved = self.session.move_child_to_parent_index(
            card.uuid, column.uuid, index,
        )
        if moved.status != "ok":
            return moved
        card = self.session.protocol.index[card.uuid]
        stamped = self.session.modify(
            card.uuid,
            {**card.data, "position_updated_at": self._position_now()},
            card.weights,
        )
        if stamped.status != "ok":
            return stamped
        return SessionResult(
            "ok", value=card.uuid,
            effects=[*moved.effects, *stamped.effects],
        )

    def accept_peer_node(self, source_addr: str, node_uuid: str,
                         adopt_absence: bool = False) -> SessionResult:
        # Session adopts an existing node's own fields shallowly (containers
        # keep their cards) and grafts only a brand-new subtree - no
        # kanban-specific container handling is needed anymore.
        local_exists = node_uuid in self.session.protocol.index
        if ((local_exists and not self.owns_node(node_uuid))
                or (not adopt_absence
                    and not self.owns_node(node_uuid, source_addr))):
            return SessionResult("error", reason="node is not part of a Kanban board")
        return self.session.accept_peer_node(source_addr, node_uuid, adopt_absence)

    def rollback_peer_node(self, source_addr: str,
                           node_uuid: str,
                           rollback_absence: bool = False) -> SessionResult:
        if (not self.owns_node(node_uuid)
                or (not rollback_absence
                    and not self.owns_node(node_uuid, source_addr))):
            return SessionResult("error", reason="node is not part of a Kanban board")
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
            # Missing columns under restricted modes are adopted shallowly
            # before this generic pass. Any other missing container would
            # still graft its whole subtree, so only "always" may accept it.
            if event_type == "local_missing_node":
                return mode == "always"
            return True

        changed = False
        for addr in self.session.peer_addresses():
            if not self.session.peer_discusses_node(addr, board.uuid):
                continue
            # Collaboration topics belong to their author and always follow
            # that author's perspective, independently of the board's card
            # auto-adopt policy.
            changed = self._adopt_originator_agenda_changes(addr, board.uuid) or changed
            if mode == "never":
                continue
            if mode in ("not_owner", "not_member"):
                changed = (
                    self._adopt_missing_columns_shallowly(addr, board.uuid)
                    or changed
                )
            changed = (
                self._adopt_newer_card_positions(addr, board.uuid, mode)
                or changed
            )

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

    def _adopt_missing_columns_shallowly(
        self, peer_addr: str, board_uuid: str,
    ) -> bool:
        """Create peer columns first without bypassing per-card policy."""
        changed = False
        for event in self.session.analyze_peer_transitions(
            peer_addr, board_uuid,
        ):
            if event["type"] != "local_missing_node":
                continue
            peer_node = self.session.get_cached_peer_subtree(
                peer_addr, event.get("node_uuid"),
            )
            if (
                not peer_node
                or peer_node.data.get("type") != "kanban_column"
            ):
                continue
            result = self.session.accept_peer_node(
                peer_addr,
                peer_node.uuid,
                adopt_descendants=False,
            )
            changed = changed or result.status == "ok"
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
            originator_change = (
                authority_node.data.get("author") == originator_uuid
            )
            order_only_change = self._agenda_order_only_change(
                local_node, peer_node,
            )
            if order_only_change:
                # Reordering is shared board state. If two clients move the
                # same item before polling, the last move wins regardless of
                # item authorship; an older publication can never undo it.
                if (
                    not local_node
                    or not peer_node
                    or self._position_updated_at(peer_node)
                    <= self._position_updated_at(local_node)
                ):
                    continue
            elif not originator_change:
                continue
            # The originator is authoritative even when a revert makes the
            # generic one-hop hash classifier call the recipient's value
            # "newer" (local_made_changes). Absence is authoritative too.
            result = self.session.accept_peer_node(
                peer_addr, node_uuid, adopt_absence=peer_node is None,
            )
            changed = changed or result.status == "ok"
        return changed

    def _adopt_newer_card_positions(
        self, peer_addr: str, board_uuid: str, mode: str,
    ) -> bool:
        """Resolve move-only card conflicts by their last move time."""
        changed = False
        for event in self.session.analyze_peer_transitions(
            peer_addr, board_uuid,
        ):
            if event["type"] == "in_agreement":
                continue
            node_uuid = event.get("node_uuid")
            local_node = self.session.protocol.index.get(node_uuid)
            peer_node = self.session.get_cached_peer_subtree(
                peer_addr, node_uuid,
            )
            if (
                not self._card_position_only_change(local_node, peer_node)
                or not self._auto_adopt_allows_node(mode, local_node)
                or self._position_updated_at(peer_node)
                <= self._position_updated_at(local_node)
            ):
                continue
            result = self.session.accept_peer_node(peer_addr, node_uuid)
            changed = changed or result.status == "ok"
        return changed

    @staticmethod
    def _card_position_only_change(
        local_node: ProtocolNode | None,
        peer_node: ProtocolNode | None,
    ) -> bool:
        if not local_node or not peer_node:
            return False
        if (
            local_node.data.get("type") != "kanban_card"
            or peer_node.data.get("type") != "kanban_card"
            or local_node.deleted != peer_node.deleted
            or local_node.weights != peer_node.weights
        ):
            return False
        local_data = dict(local_node.data)
        peer_data = dict(peer_node.data)
        local_order = local_data.pop("order", None)
        peer_order = peer_data.pop("order", None)
        local_data.pop("position_updated_at", None)
        peer_data.pop("position_updated_at", None)
        return (
            local_data == peer_data
            and (
                local_node.parent_uuid != peer_node.parent_uuid
                or local_order != peer_order
            )
        )

    @staticmethod
    def _agenda_order_only_change(
        local_node: ProtocolNode | None,
        peer_node: ProtocolNode | None,
    ) -> bool:
        if not local_node or not peer_node:
            return False
        if (
            local_node.deleted != peer_node.deleted
            or local_node.weights != peer_node.weights
            or local_node.parent_uuid != peer_node.parent_uuid
        ):
            return False
        local_data = dict(local_node.data)
        peer_data = dict(peer_node.data)
        local_order = local_data.pop("order", None)
        peer_order = peer_data.pop("order", None)
        local_data.pop("position_updated_at", None)
        peer_data.pop("position_updated_at", None)
        return local_data == peer_data and local_order != peer_order

    @staticmethod
    def _position_now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")

    @staticmethod
    def _position_updated_at(node: ProtocolNode) -> str:
        return str(node.data.get("position_updated_at") or node.updated_at)

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
        return SessionResult("ok", value=True)

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

    def transition_events(
        self, board_uuid: str | None = None, network: dict | None = None,
    ) -> list[dict]:
        board = (
            self._node(board_uuid, "kanban_board")
            if board_uuid else self._selected_board(self.boards())
        )
        if not board:
            return []
        board_uuid = board_uuid or board.uuid
        events = []
        for addr in self.session.peer_addresses():
            if not self.session.peer_discusses_node(addr, board.uuid):
                continue
            liveness = self._peer_liveness(addr, board_uuid, network)
            for event in self.session.analyze_peer_transitions(addr, board_uuid):
                if not self._is_displayed_divergence(addr, event):
                    continue
                # A peer without a live, explicitly selected topic channel
                # cannot yet react to a local revision. Keep its cached
                # perspective, but do not turn that expected silence into a
                # lamp/counter entry. Confirmed divergences and incoming peer
                # changes remain visible.
                if (
                    event["stage"] == "in_flight"
                    and liveness.get("state") != "alive"
                ):
                    continue
                event["changes"] = (
                    [] if event["type"] == "in_agreement"
                    else self.describe_peer_changes(
                        addr, event.get("node_uuid"),
                        authored_locally=event["type"] in (
                            "local_made_changes", "peer_missing_node",
                        ),
                    )
                )
                events.append(event)
        return events

    def _is_displayed_divergence(self, peer_addr: str, event: dict) -> bool:
        node_uuid = event.get("node_uuid")
        local = self.session.protocol.index.get(node_uuid)
        peer = self.session.get_cached_peer_subtree(peer_addr, node_uuid)
        node = local or peer
        return bool(
            node and node.data.get("type") in DISPLAYED_DIVERGENCE_TYPES
        )

    def _peer_liveness(
        self, peer_addr: str, topic_uuid: str, network: dict | None = None,
    ) -> dict:
        if network and network.get("_include_all"):
            return {"state": "alive"}
        peer_info = ((network or {}).get("peers") or {}).get(peer_addr) or {}
        if peer_info.get("channel_liveness") is not None:
            return peer_info["channel_liveness"]
        if network is not None:
            return {"state": "unknown"}
        resolver = getattr(
            self.collaboration, "peer_liveness_for_address", None,
        )
        if not resolver:
            return {"state": "unknown"}
        return resolver(peer_addr, topic_uuid) or {"state": "unknown"}

    def describe_peer_changes(self, peer_addr: str,
                              node_uuid: str | None,
                              authored_locally: bool = False) -> list[dict]:
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
            return self._annotate_authorship([{
                "kind": "presence",
                "field": "node",
                "label": node_label,
                "local_value": None,
                "peer_value": "present",
                "summary": f"{node_label} exists only in the peer version",
                "local_summary": f"Keep {node_label.lower()} absent",
            }], node_label, authored_locally)
        if not peer:
            return self._annotate_authorship([{
                "kind": "presence",
                "field": "node",
                "label": node_label,
                "local_value": "present",
                "peer_value": None,
                "summary": f"{node_label} exists only in your version",
                "local_summary": f"Keep your {node_label.lower()}",
            }], node_label, authored_locally)

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
        return self._annotate_authorship(changes, node_label, authored_locally)

    @staticmethod
    def _annotate_authorship(changes: list[dict], node_label: str,
                             authored_locally: bool) -> list[dict]:
        """Name what the author did, in words neither side has to invert.

        The rest of a change record is deliberately peer-relative ("...in the
        peer version"), which is the right frame for choosing a version but
        the wrong one for saying what happened: it forces the person who made
        the change to read their own edit described from the far end. This
        one field states the act, so each side can render "<act> by me" or
        "<act> by <name>" from the same record.
        """
        for change in changes:
            kind = change.get("kind")
            detail = ""
            if kind == "presence":
                act = "created"
            elif kind == "deletion":
                peer_deleted = bool(change.get("peer_value"))
                deleted_by_author = peer_deleted != authored_locally
                act = "deleted" if deleted_by_author else "restored"
            elif kind == "move":
                act = "moved"
            elif kind == "position":
                act = "reordered"
            elif kind == "participants":
                # "added" / "removed" are peer-relative; from the author's
                # own end they swap when the author is this client.
                gained = change.get(
                    "removed_labels" if authored_locally else "added_labels",
                ) or []
                lost = change.get(
                    "added_labels" if authored_locally else "removed_labels",
                ) or []
                parts = []
                if gained:
                    parts.append(f"{', '.join(gained)} added")
                if lost:
                    parts.append(f"{', '.join(lost)} removed")
                act = "modified"
                detail = "; ".join(parts)
            elif kind in ("field", "owner", "weights"):
                act = "modified"
                detail = f"{str(change.get('label') or '').lower()} changed"
            else:
                act = "changed"
            # Kept apart so the author lands between the act and its detail
            # - "Card modified by me: Ana added", not "Card modified: Ana
            # added by me", which reads as though Ana added something.
            change["node_label"] = node_label
            change["authored_act"] = act
            change["authored_detail"] = detail
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
        out = {}
        for event in events:
            node_uuid = event.get("node_uuid")
            if not node_uuid:
                continue
            event_info = {
                "type": event["type"],
                "stage": event.get("stage"),
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
                "priority": Session.transition_rank(event),
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
                if Session.transition_rank(current) >= Session.transition_rank(event):
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
        # Key on the relation, never on the stage: the same target revision
        # held by two peers is one situation even when only one of them has
        # observed our side. Keying on stage as well would split it into two
        # entries instead of one carrying both delivery_peer_addrs.
        return (
            event.get("type"),
            event.get("origin_identity"),
            event.get("local_revision") or event.get("local_state_hash"),
            event.get("peer_revision") or event.get("peer_state_hash"),
        )

    def _peer_is_revision_origin(self, event: dict) -> bool:
        origin = event.get("origin_identity")
        peer_addr = event.get("peer_addr")
        return bool(
            origin and peer_addr
            and self.session.peer_identity_key_for_address(peer_addr) == origin
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

    def create_agenda_item(
        self, text: str, priority: str | None = None,
        board_uuid: str | None = None,
    ) -> SessionResult:
        board = (
            self._node(board_uuid, "kanban_board")
            if board_uuid else self.ensure_board()
        )
        if not board:
            return SessionResult("error", reason="board not found")
        return self.session.create_agenda_item(
            board.uuid, text, priority,
        )

    def delete_agenda_item(self, item_uuid: str) -> SessionResult:
        if not self.owns_node(item_uuid):
            return SessionResult("error", reason="agenda item not found")
        return self.session.delete_agenda_item(item_uuid)

    def set_agenda_item_priority(self, item_uuid: str, priority: str | None) -> SessionResult:
        if not self.owns_node(item_uuid):
            return SessionResult("error", reason="agenda item not found")
        return self.session.set_agenda_item_priority(item_uuid, priority)

    def move_agenda_item(self, item_uuid: str, index: int) -> SessionResult:
        if not self.owns_node(item_uuid):
            return SessionResult("error", reason="agenda item not found")
        moved = self.session.move_agenda_item(item_uuid, index)
        if moved.status != "ok":
            return moved
        item = self.session.protocol.index[item_uuid]
        stamped = self.session.modify(
            item.uuid,
            {**item.data, "position_updated_at": self._position_now()},
            item.weights,
        )
        if stamped.status != "ok":
            return stamped
        return SessionResult(
            "ok", value=item.uuid,
            effects=[*moved.effects, *stamped.effects],
        )

    def _node(self, uuid: str, node_type: str) -> ProtocolNode | None:
        node = self.session.protocol.index.get(uuid)
        if (
            node
            and node.data.get("type") == node_type
            and self.owns_node(uuid)
        ):
            return node
        return None

    def owns_node(self, node_uuid: str, peer_addr: str | None = None) -> bool:
        """Whether one side's node belongs to a Kanban topic and schema."""
        if peer_addr is not None:
            node = self.session.get_cached_peer_subtree(peer_addr, node_uuid)
            if not node or node.data.get("type") not in OWNED_NODE_TYPES:
                return False
            topic_uuids = set(
                self.session.peer_topics_for_node(peer_addr, node_uuid),
            )
            if local_topic := self._local_kanban_topic(node_uuid):
                topic_uuids.add(local_topic.uuid)
            return any(
                (topic := self.session.get_cached_peer_subtree(peer_addr, topic_uuid))
                and topic.data.get("type") == "kanban_board"
                and self._subtree_contains(topic, node_uuid)
                for topic_uuid in topic_uuids
            )

        node = self.session.protocol.index.get(node_uuid)
        if not node or node.data.get("type") not in OWNED_NODE_TYPES:
            return False
        return self._local_kanban_topic(node_uuid) is not None

    def _local_kanban_topic(self, node_uuid: str) -> ProtocolNode | None:
        node = self.session.protocol.index.get(node_uuid)
        if not node:
            return None
        seen = set()
        current = node
        while current and current.uuid not in seen:
            seen.add(current.uuid)
            if current.data.get("type") == "kanban_board":
                parent = self.session.protocol.index.get(current.parent_uuid)
                return current if self._is_initiative_app_topic(parent) else None
            current = self.session.protocol.index.get(current.parent_uuid)
        return None

    @staticmethod
    def _subtree_contains(root: ProtocolNode, node_uuid: str) -> bool:
        return root.uuid == node_uuid or any(
            InitiativeLogic._subtree_contains(child, node_uuid)
            for child in root.children
        )

    def _remember_board(self, board_uuid: str, explicit: bool = False) -> None:
        # Both keys are one selection decision, so they are written under a
        # single Session transaction rather than as two separate updates.
        with self.session.lock:
            metadata = self.session.application_metadata(INITIATIVE_APPLICATION_ID)
            metadata["selected_board_uuid"] = board_uuid
            if explicit:
                metadata["board_selection_explicit"] = True

    def _metadata(self) -> dict:
        """Return a detached read copy of this application's metadata.

        Session hands out the live namespace only to a caller holding its
        lock. This application's requests are not wrapped in a Session
        transaction, so readers take a snapshot and writers open their own
        transaction - see _remember_board.
        """
        with self.session.lock:
            return copy.deepcopy(
                self.session.application_metadata(INITIATIVE_APPLICATION_ID),
            )

    def _kanban_container(self) -> ProtocolNode:
        return self._folder(self._apps_folder(), INITIATIVE_APP_NAME, "kanban_app")

    def _kanban_containers(self) -> list[ProtocolNode]:
        active = [
            topic
            for topic in self.session.active_topics()
            if self._is_initiative_app_topic(topic)
        ]
        if active:
            return active
        apps = next(
            (
                child for child in self.session.protocol.root.live_children()
                if child.data.get("type") == "folder"
                and child.data.get("name") == "apps"
            ),
            None,
        )
        if not apps:
            return []
        return [
            child for child in apps.live_children()
            if self._is_initiative_app_topic(child)
        ]

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
        for topic_uuid in self.session.peer_topic_uuids(address):
            cached = self.session.get_cached_peer_subtree(address, topic_uuid)
            if cached and self._is_shared_user_topic(cached):
                return cached.uuid
        # No "assume it's the profile if it's not a board we recognize"
        # fallback here on purpose: that used to be safe when a peer's only
        # ever-fetched topics were exactly one board plus one profile, but a
        # mailbox channel may track every topic a peer publishes - a board
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

    def _is_initiative_app_topic(self, node: ProtocolNode | None) -> bool:
        if not node:
            return False
        return (
            node.data.get("type") == "kanban_app"
            and node.data.get("name") == INITIATIVE_APP_NAME
        )

    def _is_shared_user_topic(self, node: ProtocolNode | None) -> bool:
        return self.session.is_identity_node(node)

    def _is_active_discussion_node(self, node_uuid: str) -> bool:
        return self.session.is_node_in_active_topic(node_uuid)

"""Versioned public query/command facade exposed by S-Kanban."""

from __future__ import annotations

from sovereign import ProtocolNode

from .logic import KanbanLogic


KANBAN_FACADE_API_VERSION = 1


class KanbanFacade:
    """Stable facade returning detached node snapshots and command results."""

    def __init__(self, logic: KanbanLogic):
        self._logic = logic

    def boards(self) -> list[ProtocolNode]:
        return self._logic.boards()

    def columns(self, board: ProtocolNode) -> list[ProtocolNode]:
        return self._logic.columns(board)

    def cards(self, column: ProtocolNode) -> list[ProtocolNode]:
        return self._logic.cards(column)

    def users(self) -> list[dict]:
        return self._logic.users()

    def user_profile(self) -> ProtocolNode:
        return self._logic.user_profile()

    def transition_events(
        self, topic_uuid: str, network: dict | None = None,
    ) -> list[dict]:
        return self._logic.transition_events(topic_uuid, network)

    def transition_by_node(self, events: list[dict]) -> dict:
        return self._logic.transition_by_node(events)

    def collaboration_context(
        self, topic_uuid: str, network: dict | None = None,
    ) -> dict:
        return self._logic.collaboration_context(topic_uuid, network)

    def create_board(self, name: str = "Kanban Board"):
        return self._logic.create_board(name)

    def copy_board(self, board_uuid: str):
        return self._logic.copy_board(board_uuid)

    def rename_board(self, board_uuid: str, name: str):
        return self._logic.rename_board(board_uuid, name)

    def delete_board(self, board_uuid: str):
        return self._logic.delete_board(board_uuid)

    def set_board_objective(self, board_uuid: str, objective: str):
        return self._logic.set_board_objective(board_uuid, objective)

    def move_card(self, card_uuid: str, column_uuid: str, index: int):
        return self._logic.move_card(card_uuid, column_uuid, index)

    def update_card(
        self, card_uuid: str, name: str, description: str = "",
        participants: list[str] | None = None, owner: str | None = None,
        expected_content_hash: str | None = None,
    ):
        return self._logic.update_card(
            card_uuid, name, description, participants, owner,
            expected_content_hash,
        )

    def delete_card(self, card_uuid: str):
        return self._logic.delete_card(card_uuid)

    def accept_peer_node(
        self, source_addr: str, node_uuid: str, adopt_absence: bool = False,
    ):
        return self._logic.accept_peer_node(
            source_addr, node_uuid, adopt_absence,
        )

    def rollback_peer_node(
        self, source_addr: str, node_uuid: str,
        rollback_absence: bool = False,
    ):
        return self._logic.rollback_peer_node(
            source_addr, node_uuid, rollback_absence,
        )

    def create_agenda_item(
        self, text: str, priority: str | None = None,
        board_uuid: str | None = None,
    ):
        return self._logic.create_agenda_item(text, priority, board_uuid)

    def delete_agenda_item(self, item_uuid: str):
        return self._logic.delete_agenda_item(item_uuid)

    def set_agenda_item_priority(
        self, item_uuid: str, priority: str | None,
    ):
        return self._logic.set_agenda_item_priority(item_uuid, priority)

    def move_agenda_item(self, item_uuid: str, index: int):
        return self._logic.move_agenda_item(item_uuid, index)

    def set_auto_adopt_mode(
        self, mode: str, board_uuid: str | None = None,
    ):
        return self._logic.set_auto_adopt_mode(mode, board_uuid)

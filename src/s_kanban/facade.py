"""Versioned public query facade exposed by S-Kanban."""

from __future__ import annotations

from sovereign import ProtocolNode

from .logic import KanbanLogic


KANBAN_FACADE_API_VERSION = 1


class KanbanFacade:
    """Stable query surface for optional cross-application consumers."""

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

    def transition_events(self, topic_uuid: str) -> list[dict]:
        return self._logic.transition_events(topic_uuid)

    def transition_by_node(self, events: list[dict]) -> dict:
        return self._logic.transition_by_node(events)

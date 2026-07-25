"""Versioned public query facade exposed by S-Kanban."""

from __future__ import annotations

from sovereign import ProtocolNode

from .logic import AUTO_ADOPT_MODES, KanbanLogic


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

    def collaboration_context(self, topic_uuid: str) -> dict:
        board = self._logic.session.get_node(topic_uuid)
        if not board or board.data.get("type") != "kanban_board":
            return {}
        network = self._logic._network_info(topic_uuid)
        events = self._logic.transition_events(topic_uuid, network)
        return {
            "agenda_items": [
                item.to_dict() for item in self._logic.session.agenda_items(topic_uuid)
            ],
            "transition_events": events,
            "transition_by_node": self._logic.transition_by_node(events),
            "identity_uuid": self._logic.session.identity.uuid,
            "known_identities": self._logic.session.known_identities(),
            "auto_adopt_mode": self._logic.auto_adopt_mode(board),
            "auto_adopt_modes": list(AUTO_ADOPT_MODES),
        }

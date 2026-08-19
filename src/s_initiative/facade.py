"""Versioned public query/command facade exposed by S-Initiative."""

from __future__ import annotations

from sovereign import ProtocolNode

from .logic import InitiativeLogic


INITIATIVE_FACADE_API_VERSION = 2


class InitiativeFacade:
    """Stable facade returning detached node snapshots and command results."""

    def __init__(self, logic: InitiativeLogic):
        self._logic = logic

    def initiatives(self) -> list[ProtocolNode]:
        return self._logic.initiatives()

    def columns(self, initiative: ProtocolNode) -> list[ProtocolNode]:
        return self._logic.columns(initiative)

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

    def create_initiative(self, name: str = "Initiative"):
        return self._logic.create_initiative(name)

    def copy_initiative(self, initiative_uuid: str):
        return self._logic.copy_initiative(initiative_uuid)

    def export_snapshot(
        self, initiative_uuid: str, name: str = "", description: str = "",
    ):
        return self._logic.export_snapshot(initiative_uuid, name, description)

    def create_from_snapshot(self, document: dict, name: str = ""):
        return self._logic.create_from_snapshot(document, name)

    def rename_initiative(self, initiative_uuid: str, name: str):
        return self._logic.rename_initiative(initiative_uuid, name)

    def delete_initiative(self, initiative_uuid: str):
        return self._logic.delete_initiative(initiative_uuid)

    def set_initiative_objective(self, initiative_uuid: str, objective: str):
        return self._logic.set_initiative_objective(initiative_uuid, objective)

    def set_initiative_dates(
        self, initiative_uuid: str,
        planned_start: str | None = None, planned_end: str | None = None,
    ):
        return self._logic.set_initiative_dates(
            initiative_uuid, planned_start, planned_end,
        )

    def claim_initiative_date(
        self, initiative_uuid: str, field: str, value: str = "",
    ):
        return self._logic.claim_initiative_date(initiative_uuid, field, value)

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
        initiative_uuid: str | None = None,
    ):
        return self._logic.create_agenda_item(text, priority, initiative_uuid)

    def delete_agenda_item(self, item_uuid: str):
        return self._logic.delete_agenda_item(item_uuid)

    def update_agenda_item(self, item_uuid: str, text: str):
        return self._logic.update_agenda_item(item_uuid, text)

    def set_agenda_item_priority(
        self, item_uuid: str, priority: str | None,
    ):
        return self._logic.set_agenda_item_priority(item_uuid, priority)

    def move_agenda_item(self, item_uuid: str, index: int):
        return self._logic.move_agenda_item(item_uuid, index)

    def set_auto_adopt_mode(
        self, mode: str, initiative_uuid: str | None = None,
    ):
        return self._logic.set_auto_adopt_mode(mode, initiative_uuid)

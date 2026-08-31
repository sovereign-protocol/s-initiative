"""Versioned public query/command facade exposed by S-Initiative."""

from __future__ import annotations

from sovereign import ProtocolNode

from .logic import InitiativeLogic


INITIATIVE_FACADE_API_VERSION = 4


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

    def needs(self, initiative: ProtocolNode | None = None) -> list[ProtocolNode]:
        return self._logic.needs(initiative)

    def create_need(
        self, text: str, beneficiary_label: str = "",
        beneficiary_actor_uuid: str = "",
    ):
        return self._logic.create_need(
            text, beneficiary_label, beneficiary_actor_uuid,
        )

    def update_need(
        self, need_uuid: str, text: str | None = None,
        beneficiary_label: str | None = None,
        beneficiary_actor_uuid: str | None = None,
    ):
        return self._logic.update_need(
            need_uuid, text, beneficiary_label, beneficiary_actor_uuid,
        )

    def delete_need(self, need_uuid: str):
        return self._logic.delete_need(need_uuid)

    def move_need(self, need_uuid: str, index: int):
        return self._logic.move_need(need_uuid, index)

    def sections(self, initiative: ProtocolNode | None = None) -> list[ProtocolNode]:
        return self._logic.sections(initiative)

    def clauses(self, parent: ProtocolNode) -> list[ProtocolNode]:
        return self._logic.clauses(parent)

    def create_section(self, title: str):
        return self._logic.create_section(title)

    def rename_section(self, section_uuid: str, title: str):
        return self._logic.rename_section(section_uuid, title)

    def delete_section(self, section_uuid: str):
        return self._logic.delete_section(section_uuid)

    def move_section(self, section_uuid: str, index: int):
        return self._logic.move_section(section_uuid, index)

    def create_clause(self, parent_uuid: str, text: str):
        return self._logic.create_clause(parent_uuid, text)

    def update_clause(self, clause_uuid: str, text: str):
        return self._logic.update_clause(clause_uuid, text)

    def delete_clause(self, clause_uuid: str):
        return self._logic.delete_clause(clause_uuid)

    def move_clause(self, clause_uuid: str, index: int):
        return self._logic.move_clause(clause_uuid, index)

    def realities(self, parent: ProtocolNode) -> list[ProtocolNode]:
        return self._logic.realities(parent)

    def create_reality(self, parent_uuid: str, text: str):
        return self._logic.create_reality(parent_uuid, text)

    def delete_reality(self, reality_uuid: str):
        return self._logic.delete_reality(reality_uuid)

    def investments(
        self, initiative: ProtocolNode | None = None,
        actor_uuid: str | None = None,
    ) -> list[ProtocolNode]:
        return self._logic.investments(initiative, actor_uuid)

    def create_investment(self, actor_uuid: str, availability: str):
        return self._logic.create_investment(actor_uuid, availability)

    def delete_investment(self, investment_uuid: str):
        return self._logic.delete_investment(investment_uuid)

    def milestones(
        self, initiative: ProtocolNode | None = None,
    ) -> list[ProtocolNode]:
        return self._logic.milestones(initiative)

    def current_milestone(
        self, initiative: ProtocolNode | None = None,
    ) -> ProtocolNode | None:
        return self._logic.current_milestone(initiative)

    def create_milestone(
        self, title: str, planned_at: str = "", intention: str = "",
    ):
        return self._logic.create_milestone(title, planned_at, intention)

    def update_milestone(
        self, milestone_uuid: str, title: str | None = None,
        planned_at: str | None = None, intention: str | None = None,
    ):
        return self._logic.update_milestone(
            milestone_uuid, title, planned_at, intention,
        )

    def claim_milestone_reached(
        self, milestone_uuid: str, value: str = "",
    ):
        return self._logic.claim_milestone_reached(milestone_uuid, value)

    def delete_milestone(self, milestone_uuid: str):
        return self._logic.delete_milestone(milestone_uuid)

    def move_milestone(self, milestone_uuid: str, index: int):
        return self._logic.move_milestone(milestone_uuid, index)

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

    def react_to_node(
        self, source_addr: str, node_uuid: str, reaction: str,
        absent: bool = False,
    ):
        return self._logic.react_to_node(
            source_addr, node_uuid, reaction, absent,
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

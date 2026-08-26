"""Initiative-owned relationships to teams and flows."""

import unittest

from s_initiative.logic import InitiativeLogic
from sovereign import ApplicationRegistration, SessionResult
from sovereign.protocol import ProtocolNode
from sovereign.session import Session


def register_stand_in(
    session: Session, application_id: str, root_type: str,
    noun: str = "", made: dict | None = None, template_required: bool = False,
) -> list[ProtocolNode]:
    topics = []

    def create_topic(title, template, snapshot):
        if made is not None:
            made.update(title=title, template=template, snapshot=snapshot)
        if template_required and not template and snapshot is None:
            return SessionResult("error", reason="choose a workflow to start from")
        created = session.create_child(
            session.root_uuid(), {"type": root_type, "title": title}, {},
        )
        topics.append(created.value)
        session.start_discussion(created.value.uuid)
        return created

    session.register_application(ApplicationRegistration(
        application_id=application_id,
        root_types=frozenset({root_type}),
        list_topics=lambda: list(topics),
        accept_invitation=session.accept_topic_invitation,
        assignment_scoped=True,
        mount_invitation=True,
        topic_noun=noun,
        template_required=template_required,
        list_templates=lambda: [
            {"value": item.uuid, "name": str(item.data.get("title") or "")}
            for item in topics
        ],
        create_topic=create_topic if noun else None,
    ))
    return topics


def make_topic(session, topics, root_type, title):
    topic = session.create_child(
        session.root_uuid(), {"type": root_type, "title": title}, {},
    ).value
    topics.append(topic)
    session.start_discussion(topic.uuid)
    return topic


def initiative_session():
    session = Session("local")
    logic = InitiativeLogic(session)
    session.register_application(logic.application_registration())
    return session, logic


class InitiativeRelationshipTests(unittest.TestCase):
    def test_relationship_is_an_initiative_owned_protocol_node(self):
        session, logic = initiative_session()
        initiative = logic.ensure_initiative()
        teams = register_stand_in(session, "team", "team")
        team = make_topic(session, teams, "team", "Acme")

        created = logic.create_relationship(initiative.uuid, team.uuid, "team")

        self.assertEqual(created.status, "ok", created.reason)
        node = session.get_node(created.value.uuid)
        self.assertEqual(node.data["type"], "initiative_relationship")
        self.assertEqual(node.parent_uuid, initiative.uuid)
        relationships = logic.initiative_relationships(
            session.protocol.index[initiative.uuid],
        )
        self.assertEqual(relationships[0]["topic_uuid"], team.uuid)
        self.assertTrue(relationships[0]["held"])

    def test_an_initiative_belongs_to_at_most_one_team(self):
        session, logic = initiative_session()
        initiative = logic.ensure_initiative()
        teams = register_stand_in(session, "team", "team")
        first = make_topic(session, teams, "team", "Acme")
        second = make_topic(session, teams, "team", "Other")
        logic.create_relationship(initiative.uuid, first.uuid, "team")

        again = logic.create_relationship(initiative.uuid, second.uuid, "team")

        self.assertEqual(again.status, "error")
        self.assertIn("already belongs", again.reason)

    def test_an_initiative_may_run_multiple_flows(self):
        session, logic = initiative_session()
        initiative = logic.ensure_initiative()
        flows = register_stand_in(session, "flow", "flow_process")
        first = make_topic(session, flows, "flow_process", "Hiring")
        second = make_topic(session, flows, "flow_process", "Budgeting")
        logic.create_relationship(initiative.uuid, first.uuid, "flow")
        logic.create_relationship(initiative.uuid, second.uuid, "flow")

        relationships = logic.initiative_relationships(
            session.protocol.index[initiative.uuid],
        )
        self.assertEqual(
            [item["title"] for item in relationships], ["Budgeting", "Hiring"],
        )

    def test_creation_requires_the_target_to_be_held_and_owned_by_the_kind(self):
        session, logic = initiative_session()
        initiative = logic.ensure_initiative()
        missing = logic.create_relationship(
            initiative.uuid, "team-elsewhere", "team",
        )
        wrong = logic.create_relationship(
            initiative.uuid, initiative.uuid, "team",
        )

        self.assertEqual(missing.status, "error")
        self.assertEqual(wrong.status, "error")

    def test_removing_relationship_keeps_related_topic(self):
        session, logic = initiative_session()
        initiative = logic.ensure_initiative()
        teams = register_stand_in(session, "team", "team")
        team = make_topic(session, teams, "team", "Acme")
        created = logic.create_relationship(initiative.uuid, team.uuid, "team")

        removed = logic.remove_relationship(created.value.uuid)

        self.assertEqual(removed.status, "ok")
        self.assertIsNotNone(session.get_node(team.uuid))
        self.assertEqual(logic.initiative_relationships(initiative), [])

    def test_candidates_are_held_supported_topics_not_already_related(self):
        session, logic = initiative_session()
        initiative = logic.ensure_initiative()
        teams = register_stand_in(session, "team", "team")
        flows = register_stand_in(session, "flow", "flow_process")
        team = make_topic(session, teams, "team", "Acme")
        make_topic(session, flows, "flow_process", "Hiring")

        self.assertEqual(
            [(item["application_id"], item["title"])
             for item in logic.relationship_candidates(initiative.uuid)],
            [("flow", "Hiring"), ("team", "Acme")],
        )
        logic.create_relationship(initiative.uuid, team.uuid, "team")
        self.assertEqual(
            [item["title"] for item in logic.relationship_candidates(initiative.uuid)],
            ["Hiring"],
        )

    def test_relationships_reach_the_board_payload(self):
        session, logic = initiative_session()
        initiative = logic.ensure_initiative()
        teams = register_stand_in(session, "team", "team")
        team = make_topic(session, teams, "team", "Acme")
        logic.create_relationship(initiative.uuid, team.uuid, "team")

        payload = logic.board_payload({"peers": {}})

        self.assertEqual(
            [item["topic_uuid"] for item in payload["relationships"]], [team.uuid],
        )
        self.assertEqual(payload["relationship_candidates"], [])

    def test_related_topic_can_be_made_by_its_own_application(self):
        session, logic = initiative_session()
        initiative = logic.ensure_initiative()
        made = {}
        register_stand_in(
            session, "flow", "flow_process", noun="Flow", made=made,
            template_required=True,
        )

        created = logic.create_related_topic(
            initiative.uuid, "flow", "Choosing", "election",
        )

        self.assertEqual(created.status, "ok", created.reason)
        self.assertEqual((made["title"], made["template"]), ("Choosing", "election"))
        self.assertEqual(
            logic.initiative_relationships(
                session.protocol.index[initiative.uuid],
            )[0]["title"],
            "Choosing",
        )


if __name__ == "__main__":
    unittest.main()

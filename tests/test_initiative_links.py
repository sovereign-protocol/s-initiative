"""An initiative names the team it belongs to and the flows it runs.

Both are other applications' topics, so nothing here imports them: a stand-in
registered under the same application id is enough, and is the honest test
besides - what S-Initiative knows about a team is its application id and its
uuid, which is the whole of what a link carries.
"""

import unittest

from s_initiative.logic import InitiativeLogic
from sovereign import ApplicationRegistration, SessionResult
from sovereign.protocol import ProtocolNode
from sovereign.session import Session


def register_stand_in(
    session: Session, application_id: str, root_type: str,
    noun: str = "", made: dict | None = None, template_required: bool = False,
) -> list[ProtocolNode]:
    """Another application on this session, owning one root type.

    Given a noun it also says how one of its topics is made, which is how a
    real one says it and the only way an initiative can make one. What
    starts from nothing, from a template or from a file is its own answer
    and S-Initiative knows none of it.
    """
    topics: list[ProtocolNode] = []

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


def make_team(session: Session, title: str) -> ProtocolNode:
    topics = getattr(session, "_teams", None)
    if topics is None:
        topics = register_stand_in(session, "team", "team")
        session._teams = topics
    topic = session.create_child(
        session.root_uuid(), {"type": "team", "title": title}, {},
    ).value
    topics.append(topic)
    session.start_discussion(topic.uuid)
    return topic


def make_flow(session: Session, title: str) -> ProtocolNode:
    topics = getattr(session, "_flows", None)
    if topics is None:
        topics = register_stand_in(session, "flow", "flow_process")
        session._flows = topics
    topic = session.create_child(
        session.root_uuid(), {"type": "flow_process", "title": title}, {},
    ).value
    topics.append(topic)
    session.start_discussion(topic.uuid)
    return topic


def initiative_session(address: str = "local") -> tuple[Session, InitiativeLogic]:
    session = Session(address)
    logic = InitiativeLogic(session)
    session.register_application(logic.application_registration())
    return session, logic


class InitiativeLinkTests(unittest.TestCase):
    def test_an_initiative_names_the_team_it_belongs_to(self):
        session, logic = initiative_session()
        initiative = logic.ensure_initiative()
        team = make_team(session, "Acme")

        linked = logic.link_topic(initiative.uuid, team.uuid, "team", "Acme")

        self.assertEqual(linked.status, "ok")
        links = logic.initiative_links(session.protocol.index[initiative.uuid])
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["application_id"], "team")
        self.assertEqual(links[0]["topic_uuid"], team.uuid)
        self.assertTrue(links[0]["held"])

    def test_an_initiative_names_one_team_and_no_more(self):
        session, logic = initiative_session()
        initiative = logic.ensure_initiative()
        first = make_team(session, "Acme")
        second = make_team(session, "Other")
        logic.link_topic(initiative.uuid, first.uuid, "team", "Acme")

        again = logic.link_topic(initiative.uuid, second.uuid, "team", "Other")

        self.assertEqual(again.status, "error")
        self.assertIn("already names a team", again.reason)

    def test_an_initiative_runs_as_many_flows_as_it_likes(self):
        session, logic = initiative_session()
        initiative = logic.ensure_initiative()
        first = make_flow(session, "Hiring")
        second = make_flow(session, "Budgeting")

        logic.link_topic(initiative.uuid, first.uuid, "flow", "Hiring")
        logic.link_topic(initiative.uuid, second.uuid, "flow", "Budgeting")

        links = logic.initiative_links(session.protocol.index[initiative.uuid])
        self.assertEqual([link["title"] for link in links],
                         ["Budgeting", "Hiring"])

    def test_the_topic_s_own_name_wins_over_the_recorded_one(self):
        session, logic = initiative_session()
        initiative = logic.ensure_initiative()
        team = make_team(session, "Acme")
        logic.link_topic(initiative.uuid, team.uuid, "team", "Acme")

        node = session.protocol.index[team.uuid]
        session.modify(team.uuid, {**node.data, "title": "Acme Renamed"}, {})

        links = logic.initiative_links(session.protocol.index[initiative.uuid])
        self.assertEqual(links[0]["title"], "Acme Renamed")

    def test_removing_a_link_keeps_the_team(self):
        session, logic = initiative_session()
        initiative = logic.ensure_initiative()
        team = make_team(session, "Acme")
        logic.link_topic(initiative.uuid, team.uuid, "team", "Acme")
        link = logic.initiative_links(session.protocol.index[initiative.uuid])[0]

        removed = logic.unlink_topic(link["uuid"])

        self.assertEqual(removed.status, "ok")
        self.assertIsNotNone(session.get_node(team.uuid))
        self.assertEqual(
            logic.initiative_links(session.protocol.index[initiative.uuid]), [],
        )

    def test_an_initiative_does_not_link_to_another_initiative(self):
        session, logic = initiative_session()
        initiative = logic.ensure_initiative()
        other = logic.create_initiative("Other")

        linked = logic.link_topic(
            initiative.uuid, other.value, "initiative", "Other",
        )

        self.assertEqual(linked.status, "error")

    def test_only_what_is_held_here_is_offered_to_link(self):
        session, logic = initiative_session()
        initiative = logic.ensure_initiative()
        team = make_team(session, "Acme")
        make_flow(session, "Hiring")

        offered = logic.linkable_topics(initiative.uuid)

        self.assertEqual(
            [(item["application_id"], item["title"]) for item in offered],
            [("flow", "Hiring"), ("team", "Acme")],
        )
        # Its own initiative is not something to link, and neither is one already
        # linked.
        logic.link_topic(initiative.uuid, team.uuid, "team", "Acme")
        self.assertEqual(
            [item["title"] for item in logic.linkable_topics(initiative.uuid)],
            ["Hiring"],
        )

    def test_a_link_to_a_team_this_client_lacks_is_an_invitation(self):
        """Not broken, and not a key either: it resolves only where somebody
        is publishing what it names."""
        session, logic = initiative_session()
        initiative = logic.ensure_initiative()

        logic.link_topic(initiative.uuid, "team-elsewhere", "team", "Acme")

        links = logic.initiative_links(session.protocol.index[initiative.uuid])
        self.assertFalse(links[0]["held"])
        self.assertEqual(links[0]["title"], "Acme")
        followed = logic.follow_link(links[0]["uuid"])
        self.assertEqual(followed.status, "error")
        self.assertIn("publishing", followed.reason)

    def test_following_a_link_takes_up_a_team_a_peer_publishes(self):
        session, logic = initiative_session()
        initiative = logic.ensure_initiative()
        register_stand_in(session, "team", "team")
        author = Session("si-author")
        author.identity
        team = make_team(author, "Acme")
        logic.link_topic(initiative.uuid, team.uuid, "team", "Acme")
        link = logic.initiative_links(session.protocol.index[initiative.uuid])[0]
        self.assertFalse(link["held"])

        session.note_indirect_peer_topic(author.address, team.uuid)
        session.apply_peer_subtree(
            author.address,
            ProtocolNode.from_dict(author.protocol.index[team.uuid].to_dict()),
            None,
        )
        followed = logic.follow_link(link["uuid"])

        self.assertEqual(followed.status, "ok")
        self.assertIsNotNone(session.get_node(team.uuid))
        self.assertTrue(
            logic.initiative_links(session.protocol.index[initiative.uuid])[0]["held"],
        )

    def test_the_links_reach_the_board_payload(self):
        session, logic = initiative_session()
        initiative = logic.ensure_initiative()
        team = make_team(session, "Acme")
        logic.link_topic(initiative.uuid, team.uuid, "team", "Acme")

        payload = logic.board_payload({"peers": {}})

        self.assertEqual(
            [link["topic_uuid"] for link in payload["links"]], [team.uuid],
        )
        self.assertEqual(payload["linkable_topics"], [])

    def test_a_flow_can_be_made_and_named_here_in_one_act(self):
        """The making is S-Flow's; the naming is this application's.

        Nothing here knows what a process is beyond that it is made by
        asking Core for one - and a client without S-Flow is not offered
        the kind at all rather than refused after asking.
        """
        session, logic = initiative_session()
        initiative = logic.ensure_initiative()

        # Without the application there is nothing to offer.
        self.assertEqual(logic.link_kinds(), [])
        made = {}
        register_stand_in(
            session, "flow", "flow_process",
            noun="Flow", made=made, template_required=True,
        )
        self.assertEqual(
            [(kind["application_id"], kind["noun"], kind["template_required"])
             for kind in logic.link_kinds()],
            [("flow", "Flow", True)],
        )

        created = logic.create_linked_topic(
            initiative.uuid, "flow", "Choosing a facilitator", "election",
        )

        self.assertEqual(created.status, "ok")
        self.assertEqual(
            (made["title"], made["template"]),
            ("Choosing a facilitator", "election"),
        )
        links = logic.initiative_links(session.protocol.index[initiative.uuid])
        self.assertEqual(
            [(link["label"], link["title"], link["held"], link["mine"])
             for link in links],
            [("Flow", "Choosing a facilitator", True, True)],
        )

    def test_what_the_owning_application_refuses_is_not_named_here(self):
        """A refusal travels back unchanged and nothing is linked.

        Which template ids are real is S-Flow's answer, not this one's -
        this application does not know a workflow from a uuid.
        """
        session, logic = initiative_session()
        initiative = logic.ensure_initiative()
        register_stand_in(
            session, "flow", "flow_process", noun="Flow", template_required=True,
        )

        refused = logic.create_linked_topic(initiative.uuid, "flow", "Nameless")

        self.assertEqual(refused.status, "error")
        self.assertIn("workflow", refused.reason)
        self.assertEqual(
            logic.initiative_links(session.protocol.index[initiative.uuid]), [],
        )


if __name__ == "__main__":
    unittest.main()

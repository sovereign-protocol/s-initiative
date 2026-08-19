import asyncio
import json
import unittest

from s_initiative.controller import build_routes
from s_initiative.logic import InitiativeLogic
from sovereign import Session
from starlette.requests import Request


class _Runtime:
    def deliver_effects(self, effects):
        return []

    def notify_change(self, change_kind="application"):
        pass


def _post_request(path: str, payload: dict) -> Request:
    body = json.dumps(payload).encode()
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({
        "type": "http",
        "method": "POST",
        "path": path,
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
    }, receive)


class InitiativeOwnershipControllerTests(unittest.TestCase):
    def setUp(self):
        self.session = Session("local")
        self.logic = InitiativeLogic(self.session)
        self.logic.ensure_initiative()
        self.routes = build_routes(self.logic, _Runtime())

    def _post(self, path: str, payload: dict):
        endpoint = next(route.endpoint for route in self.routes if route.path == path)
        return asyncio.run(endpoint(_post_request(path, payload)))

    def test_the_date_routes_are_wired_to_their_commands(self):
        # A route path is not covered by the logic tests, and a typo in one
        # leaves a command nothing can reach.
        initiative = self.logic.ensure_initiative()

        planned = self._post("/api/initiative/initiatives/set_dates", {
            "initiative_uuid": initiative.uuid,
            "planned_start": "2026-03-12",
        })
        claimed = self._post("/api/initiative/initiatives/claim_date", {
            "initiative_uuid": initiative.uuid,
            "field": "actual_start",
            "value": "2026-03-14",
        })

        self.assertEqual(planned.status_code, 200)
        self.assertEqual(claimed.status_code, 200)
        held = self.session.get_node(initiative.uuid)
        self.assertEqual(held.data["planned_start"], "2026-03-12")
        self.assertEqual(held.data["actual_start"], "2026-03-14")

    def test_the_claim_route_refuses_to_be_a_second_way_into_a_plan(self):
        initiative = self.logic.ensure_initiative()

        response = self._post("/api/initiative/initiatives/claim_date", {
            "initiative_uuid": initiative.uuid,
            "field": "planned_start",
            "value": "2026-03-12",
        })

        self.assertEqual(response.status_code, 409)
        self.assertNotIn(
            "planned_start", self.session.get_node(initiative.uuid).data,
        )

    def test_the_needs_routes_are_wired_to_their_commands(self):
        self.logic.ensure_initiative()

        created = self._post("/api/initiative/needs/create", {
            "text": "Onboarding takes three weeks",
            "beneficiary_label": "New joiners",
        })
        need = self.logic.needs()[0]
        updated = self._post("/api/initiative/needs/update", {
            "need_uuid": need.uuid, "beneficiary_label": "Newcomers",
        })
        moved = self._post("/api/initiative/needs/move", {
            "need_uuid": need.uuid, "index": 0,
        })
        removed = self._post("/api/initiative/needs/delete", {
            "need_uuid": need.uuid,
        })

        for response in (created, updated, moved, removed):
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.logic.needs(), [])

    def test_delete_need_rejects_a_need_typed_node_outside_an_initiative(self):
        foreign = self.session.create_child(
            self.session.root_uuid(),
            {"type": "initiative_need", "text": "foreign", "order": 0}, {},
        ).value

        response = self._post(
            "/api/initiative/needs/delete", {"need_uuid": foreign.uuid},
        )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.session.protocol.index[foreign.uuid].deleted)

    def test_the_approach_routes_are_wired_to_their_commands(self):
        section = self.logic.sections()[0]

        renamed = self._post("/api/initiative/sections/rename", {
            "section_uuid": section.uuid, "title": "How we will win",
        })
        added = self._post("/api/initiative/clauses/create", {
            "parent_uuid": section.uuid, "text": "Win the first three teams",
        })
        clause = self.logic.clauses(self.session.get_node(section.uuid))[0]
        updated = self._post("/api/initiative/clauses/update", {
            "clause_uuid": clause.uuid, "text": "Win three teams",
        })
        moved = self._post("/api/initiative/sections/move", {
            "section_uuid": section.uuid, "index": 1,
        })
        created = self._post("/api/initiative/sections/create", {
            "title": "Open questions",
        })
        removed = self._post("/api/initiative/clauses/delete", {
            "clause_uuid": clause.uuid,
        })

        for response in (renamed, added, updated, moved, created, removed):
            self.assertEqual(response.status_code, 200)
        titles = [s.data["title"] for s in self.logic.sections()]
        self.assertIn("How we will win", titles)
        self.assertIn("Open questions", titles)

    def test_create_clause_rejects_a_parent_outside_an_initiative(self):
        foreign = self.session.create_child(
            self.session.root_uuid(),
            {"type": "initiative_section", "title": "foreign", "order": 0}, {},
        ).value

        response = self._post("/api/initiative/clauses/create", {
            "parent_uuid": foreign.uuid, "text": "Nope",
        })

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            [child for child in self.session.protocol.index[foreign.uuid].children],
            [],
        )

    def test_the_impact_resource_and_milestone_routes_reach_their_commands(self):
        initiative = self.logic.ensure_initiative()
        actor = self.logic.user_profile().uuid

        intended = self._post("/api/initiative/clauses/create", {
            "parent_uuid": initiative.uuid, "text": "Two teams renew",
        })
        assessed = self._post("/api/initiative/realities/create", {
            "parent_uuid": initiative.uuid, "text": "Three teams renewed",
        })
        availability = self._post("/api/initiative/investments/create", {
            "actor_uuid": actor, "availability": "One day a week",
        })
        milestone_created = self._post("/api/initiative/milestones/create", {
            "title": "Pilot", "planned_at": "2026-04-30",
        })
        milestone = self.logic.milestones()[0]
        milestone_updated = self._post("/api/initiative/milestones/update", {
            "milestone_uuid": milestone.uuid, "title": "Pilot complete",
        })
        milestone_reached = self._post("/api/initiative/milestones/reach", {
            "milestone_uuid": milestone.uuid, "value": "2026-05-02",
        })
        milestone_moved = self._post("/api/initiative/milestones/move", {
            "milestone_uuid": milestone.uuid, "index": 0,
        })

        for response in (
            intended, assessed, availability, milestone_created,
            milestone_updated, milestone_reached, milestone_moved,
        ):
            self.assertEqual(response.status_code, 200)
        held = self.session.get_node(milestone.uuid)
        self.assertEqual(held.data["title"], "Pilot complete")
        self.assertEqual(held.data["reached_at"], "2026-05-02")

    def test_new_node_types_outside_an_initiative_are_refused(self):
        foreign_milestone = self.session.create_child(
            self.session.root_uuid(),
            {"type": "initiative_milestone", "title": "foreign", "order": 0}, {},
        ).value
        foreign_reality = self.session.create_child(
            self.session.root_uuid(),
            {
                "type": "initiative_reality",
                "author_actor_uuid": self.logic.user_profile().uuid,
                "text": "foreign",
                "recorded_at": "2026-01-01T00:00:00+00:00",
            },
            {},
        ).value
        foreign_investment = self.session.create_child(
            self.session.root_uuid(),
            {
                "type": "initiative_investment",
                "actor_uuid": self.logic.user_profile().uuid,
                "availability": "foreign",
                "previous_uuid": "",
                "recorded_at": "2026-01-01T00:00:00+00:00",
            },
            {},
        ).value

        responses = (
            self._post("/api/initiative/milestones/delete", {
                "milestone_uuid": foreign_milestone.uuid,
            }),
            self._post("/api/initiative/realities/delete", {
                "reality_uuid": foreign_reality.uuid,
            }),
            self._post("/api/initiative/investments/delete", {
                "investment_uuid": foreign_investment.uuid,
            }),
        )

        self.assertEqual([response.status_code for response in responses], [409, 409, 409])

    def test_delete_column_rejects_a_kanban_typed_node_outside_a_board(self):
        foreign = self.session.create_child(
            self.session.root_uuid(), {"type": "kanban_column", "name": "foreign"}, {},
        ).value

        response = self._post(
            "/api/initiative/columns/delete", {"column_uuid": foreign.uuid},
        )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.session.protocol.index[foreign.uuid].deleted)

    def test_adopt_rejects_a_peer_only_kanban_node_under_a_foreign_topic(self):
        peer = Session("peer")
        foreign_topic = peer.create_child(
            peer.root_uuid(), {"type": "agreement", "title": "foreign"}, {},
        ).value
        local_copy = peer.get_node(foreign_topic.uuid)
        self.session.adopt_subtree(local_copy, self.session.root_uuid())
        peer_card = peer.create_child(
            foreign_topic.uuid, {"type": "kanban_card", "name": "foreign"}, {},
        ).value
        self.session.apply_peer_subtree(
            "peer", peer.get_node(foreign_topic.uuid), None,
        )
        self.session.note_indirect_peer_topic("peer", foreign_topic.uuid)

        response = self._post("/api/initiative/adopt", {
            "source_addr": "peer",
            "node_uuid": peer_card.uuid,
        })

        self.assertEqual(response.status_code, 409)
        self.assertNotIn(peer_card.uuid, self.session.protocol.index)


if __name__ == "__main__":
    unittest.main()

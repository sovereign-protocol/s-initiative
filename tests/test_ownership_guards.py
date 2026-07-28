import asyncio
import json
import unittest

from s_kanban.controller import build_routes
from s_kanban.logic import KanbanLogic
from sovereign import Session
from starlette.requests import Request


class _Runtime:
    def deliver_effects(self, effects):
        return []

    def notify_change(self):
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


class KanbanOwnershipControllerTests(unittest.TestCase):
    def setUp(self):
        self.session = Session("local")
        self.logic = KanbanLogic(self.session)
        self.logic.ensure_board()
        self.routes = build_routes(self.logic, _Runtime())

    def _post(self, path: str, payload: dict):
        endpoint = next(route.endpoint for route in self.routes if route.path == path)
        return asyncio.run(endpoint(_post_request(path, payload)))

    def test_delete_column_rejects_a_kanban_typed_node_outside_a_board(self):
        foreign = self.session.create_child(
            self.session.root_uuid(), {"type": "kanban_column", "name": "foreign"}, {},
        ).value

        response = self._post(
            "/api/kanban/columns/delete", {"column_uuid": foreign.uuid},
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

        response = self._post("/api/kanban/adopt", {
            "source_addr": "peer",
            "node_uuid": peer_card.uuid,
        })

        self.assertEqual(response.status_code, 409)
        self.assertNotIn(peer_card.uuid, self.session.protocol.index)


if __name__ == "__main__":
    unittest.main()

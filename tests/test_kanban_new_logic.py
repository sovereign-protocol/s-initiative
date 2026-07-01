import tempfile
import unittest
from pathlib import Path

import app_server
from kanban_logic import KanbanLogic
from protocol import PRSPNode


class MemoryHttpClient:
    def __init__(self, runtimes):
        self.runtimes = runtimes

    def get_json(self, url: str, timeout: float = 5) -> dict:
        runtime, path = self._split(url)
        if path.startswith("/p2p/subtree/"):
            payload, status = runtime.adapter.p2p_subtree(path.rsplit("/", 1)[1])
            if status != 200:
                raise RuntimeError(payload.get("reason", "not found"))
            return payload
        raise RuntimeError(f"unexpected GET {path}")

    def post_json(self, url: str, payload: dict,
                  timeout: float = 5) -> dict:
        runtime, path = self._split(url)
        if path == "/api/join_discussion":
            return runtime.adapter.join_discussion(payload["address"], payload.get("topic_uuid"))
        if path == "/p2p/join":
            response, status = runtime.adapter.p2p_join(payload)
            if status != 200:
                raise RuntimeError(response.get("reason", "join failed"))
            return response
        if path == "/p2p/ping":
            response, status = runtime.adapter.p2p_ping(payload)
            if status != 200:
                raise RuntimeError(response.get("reason", "ping failed"))
            return response
        if path == "/p2p/announce":
            response, status = runtime.adapter.p2p_announce(payload)
            if status != 200:
                raise RuntimeError(response.get("reason", "announce failed"))
            return response
        if path == "/p2p/leave":
            response, status = runtime.adapter.p2p_leave(payload)
            if status != 200:
                raise RuntimeError(response.get("reason", "leave failed"))
            return response
        raise RuntimeError(f"unexpected POST {path}")

    def _split(self, url):
        for address in sorted(self.runtimes, key=len, reverse=True):
            if url.startswith(address):
                return self.runtimes[address], url[len(address):]
        raise RuntimeError(f"unknown address in {url}")


class KanbanNewLogicTests(unittest.TestCase):
    def test_default_board_has_columns(self):
        runtime = self.runtime(8301)
        board = runtime.logic.ensure_board()

        self.assertEqual(board.data["type"], "kanban_board")
        self.assertEqual(
            [column.data["name"] for column in runtime.logic.columns(board)],
            ["To Do", "Doing", "Done"],
        )

    def test_card_crud_and_move(self):
        runtime = self.runtime(8302)
        logic: KanbanLogic = runtime.logic
        board = logic.ensure_board()
        todo, doing = logic.columns(board)[:2]

        card = logic.create_card(todo.uuid, "Task", "Desc", ["A"]).value
        logic.update_card(card.uuid, "Task 2", "Desc 2", ["B"])
        move = logic.move_card(card.uuid, doing.uuid, 0)

        self.assertEqual(move.status, "ok")
        moved = runtime.session.protocol.index[card.uuid]
        self.assertEqual(moved.parent_uuid, doing.uuid)
        self.assertEqual(moved.data["name"], "Task 2")
        self.assertEqual(moved.data["owners"], ["B"])

    def test_two_clients_auto_adopt_collaborate(self):
        left = self.runtime(8303)
        right = self.runtime(8304)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        right.logic.ensure_board()

        invite = left.logic.invite(left, right.address)
        self.assertEqual(invite["status"], "ok")
        self.assertEqual(right.logic.ensure_board().uuid, board.uuid)

        column = left.logic.columns(board)[0]
        card = left.logic.create_card(column.uuid, "Shared", "", []).value
        left.adapter.execute_effects(left.session._sync_effects(board.uuid))
        right.logic.board_payload()

        self.assertIn(card.uuid, right.session.protocol.index)
        self.assertEqual(right.session.protocol.index[card.uuid].data["name"], "Shared")

    def test_two_clients_auto_adopt_card_move(self):
        left = self.runtime(8313)
        right = self.runtime(8314)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        left.logic.invite(left, right.address)
        first, second = left.logic.columns(board)[:2]
        card = left.logic.create_card(first.uuid, "Move me", "", []).value
        left.adapter.execute_effects(left.session._sync_effects(board.uuid))
        right.logic.board_payload()

        left.logic.move_card(card.uuid, second.uuid, 0)
        left.adapter.execute_effects(left.session._sync_effects(board.uuid))
        right.logic.board_payload()

        self.assertEqual(right.session.protocol.index[card.uuid].parent_uuid, second.uuid)

    def test_auto_adopt_move_keeps_exported_hashes_valid(self):
        left = self.runtime(8315)
        right = self.runtime(8316)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        left.logic.invite(left, right.address)
        first, second = left.logic.columns(board)[:2]

        card = left.logic.create_card(first.uuid, "Hash safe", "", []).value
        left.adapter.execute_effects(left.session._sync_effects(board.uuid))
        right.logic.board_payload()
        right.adapter.execute_effects(right.session._sync_effects(board.uuid))
        left.logic.board_payload()

        left.logic.move_card(card.uuid, second.uuid, 0)
        left.adapter.execute_effects(left.session._sync_effects(board.uuid))
        right.logic.board_payload()
        right.adapter.execute_effects(right.session._sync_effects(board.uuid))
        left.logic.board_payload()

        for runtime in (left, right):
            for node_uuid in runtime.session.protocol.index:
                subtree = runtime.session.get_subtree(node_uuid)
                PRSPNode.from_dict(subtree["subtree"])

    def test_auto_adopt_off_keeps_difference_until_adopt(self):
        left = self.runtime(8305)
        right = self.runtime(8306)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        left.logic.invite(left, right.address)
        right.logic.set_auto_adopt(False)

        column = left.logic.columns(board)[0]
        card = left.logic.create_card(column.uuid, "Needs adopt", "", []).value
        left.adapter.execute_effects(left.session._sync_effects(board.uuid))
        right.logic.board_payload()

        self.assertNotIn(card.uuid, right.session.protocol.index)
        payload = right.logic.board_payload()
        self.assertEqual(
            payload["transition_by_node"][card.uuid]["type"],
            "local_missing_node",
        )
        adopt = right.logic.accept_peer_node(left.address, card.uuid)
        self.assertEqual(adopt.status, "ok")
        self.assertIn(card.uuid, right.session.protocol.index)

    def test_transition_event_prevents_stale_peer_rollback(self):
        left = self.runtime(8307)
        right = self.runtime(8308)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        left.logic.invite(left, right.address)
        column = left.logic.columns(board)[0]

        card = left.logic.create_card(column.uuid, "Local", "", []).value
        payload = left.logic.board_payload()

        self.assertIn(card.uuid, left.session.protocol.index)
        self.assertEqual(
            payload["transition_by_node"][board.uuid]["type"],
            "local_made_changes",
        )

    def test_transition_by_node_keeps_all_peer_events(self):
        runtime = self.runtime(8311)
        node_uuid = "node-1"

        out = runtime.logic.transition_by_node([
            {
                "node_uuid": node_uuid,
                "type": "peer_missing_node",
                "peer_addr": "http://127.0.0.1:8002",
            },
            {
                "node_uuid": node_uuid,
                "type": "divergence",
                "peer_addr": "http://127.0.0.1:8003",
            },
        ])

        self.assertEqual(out[node_uuid]["type"], "divergence")
        self.assertEqual(len(out[node_uuid]["events"]), 2)
        self.assertEqual(
            [event["peer_addr"] for event in out[node_uuid]["events"]],
            ["http://127.0.0.1:8002", "http://127.0.0.1:8003"],
        )

    @staticmethod
    def runtime(port: int):
        directory = tempfile.TemporaryDirectory()
        config = app_server.load_config(None, "kanban")
        config["storage_file"] = str(Path(directory.name) / f"{port}.json")
        runtime = app_server.create_runtime(port, config)
        runtime._test_tmp = directory
        return runtime


if __name__ == "__main__":
    unittest.main()

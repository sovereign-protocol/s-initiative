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
            join = getattr(runtime.logic, "join_discussion", None)
            if join:
                return join(
                    runtime,
                    payload["address"],
                    payload.get("topic_uuid"),
                    payload.get("topic_uuids"),
                )
            return runtime.adapter.join_discussion(
                payload["address"],
                payload.get("topic_uuid"),
                payload.get("topic_uuids"),
            )
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
            runtime.session.protocol.index[board.parent_uuid].data,
            {"type": "kanban_app", "name": "S-Kanban"},
        )
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
        self.assertEqual(moved.data["participants"], ["B"])

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
        self.assertNotIn(board.uuid, right.session.protocol.index)
        share = left.logic.share_board(left, right.address, board.uuid)
        self.assertEqual(share["status"], "ok")
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
        left.logic.share_board(left, right.address, board.uuid)
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
        left.logic.share_board(left, right.address, board.uuid)
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
        left.logic.share_board(left, right.address, board.uuid)
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
        left.logic.share_board(left, right.address, board.uuid)
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

    def test_rename_board_updates_board_list(self):
        runtime = self.runtime(8315)
        board = runtime.logic.ensure_board()

        result = runtime.logic.rename_board(board.uuid, "Planning")

        self.assertEqual(result.status, "ok")
        self.assertEqual(runtime.logic.ensure_board().data["name"], "Planning")
        self.assertEqual(runtime.logic.boards()[0].data["name"], "Planning")

    def test_user_profile_is_single_shared_topic(self):
        runtime = self.runtime(8316)

        result = runtime.logic.set_user_profile("Alice", "https://example.test/a.png")

        self.assertEqual(result.status, "ok")
        profile = runtime.logic.user_profile()
        self.assertEqual(profile.data["type"], "identity_user")
        self.assertEqual(profile.data["name"], "shared")
        self.assertEqual(profile.data["display_name"], "Alice")
        self.assertEqual(profile.data["picture"], "https://example.test/a.png")
        self.assertEqual(runtime.logic._identity_topic_uuids(), [profile.uuid])
        self.assertEqual(
            {child.data["field"]: child.data["value"] for child in profile.children},
            {
                "name": "Alice",
                "picture": "https://example.test/a.png",
            },
        )

    def test_invite_shares_only_identity_topic(self):
        left = self.runtime(8322)
        right = self.runtime(8323)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        left.logic.ensure_board()
        left.logic.set_user_profile("Alice", "")

        invite = left.logic.invite(left, right.address)

        self.assertEqual(invite["status"], "ok")
        topics = invite["topic_uuids"]
        topic_data = [left.session.protocol.index[uuid].data for uuid in topics]
        self.assertEqual(len(topic_data), 1)
        self.assertTrue(any(data.get("role") == "shared_identity" for data in topic_data))

    def test_share_board_adds_selected_board_topic(self):
        left = self.runtime(8326)
        right = self.runtime(8327)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        left.logic.invite(left, right.address)

        share = left.logic.share_board(left, right.address, board.uuid)

        self.assertEqual(share["status"], "ok")
        self.assertIn(board.uuid, share["topic_uuids"])
        self.assertIn(board.uuid, right.session.protocol.index)

    def test_kanban_rejects_non_board_or_identity_invitation(self):
        left = self.runtime(8324)
        right = self.runtime(8325)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        other = left.session.create_child(
            left.session.protocol.root.uuid,
            {"type": "folder", "name": "Not S-Kanban"},
            {},
        ).value

        result = right.logic.join_discussion(right, left.address, other.uuid)

        self.assertEqual(result["status"], "error")
        self.assertIn("board topics", result["reason"])

    def test_first_participant_is_owner(self):
        runtime = self.runtime(8317)
        runtime.logic.set_user_profile("Alice", "")
        board = runtime.logic.ensure_board()
        column = runtime.logic.columns(board)[0]

        card = runtime.logic.create_card(
            column.uuid,
            "Mine",
            "",
            [runtime.address],
        ).value

        self.assertEqual(card.data["participants"], [runtime.address])
        self.assertEqual(runtime.logic.users()[0]["name"], "Alice")

    def test_card_participants_are_ordered(self):
        runtime = self.runtime(8318)
        board = runtime.logic.ensure_board()
        column = runtime.logic.columns(board)[0]

        card = runtime.logic.create_card(
            column.uuid,
            "With people",
            "",
            ["owner", "participant"],
        ).value
        update = runtime.logic.update_card(
            card.uuid,
            "With people",
            "",
            ["owner-2", "participant-2"],
        )

        self.assertEqual(update.status, "ok")
        self.assertEqual(card.data["participants"], ["owner-2", "participant-2"])

    def test_auto_adopt_updates_board_not_currently_selected(self):
        left = self.runtime(8319)
        right = self.runtime(8320)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board1 = left.logic.ensure_board()
        board2 = left.logic.create_board("Board 2").value
        left.logic.select_board(board1.uuid)
        right.logic.ensure_board()
        left.logic.invite(left, right.address)
        left.logic.share_board(left, right.address, board1.uuid)
        left.logic.share_board(left, right.address, board2)
        right.logic.select_board(board2)
        right.logic.set_auto_adopt(False)
        right.logic.select_board(board1.uuid)
        self.assertTrue(right.logic.auto_adopt_enabled())
        right.logic.select_board(board2)

        column = left.logic.columns(board1)[0]
        card = left.logic.create_card(column.uuid, "Board 1 card", "", []).value
        left.adapter.execute_effects(left.session._sync_effects(board1.uuid))
        right.logic.board_payload()

        self.assertIn(card.uuid, right.session.protocol.index)
        self.assertEqual(right.logic.ensure_board().uuid, board2)

    def test_selected_board_is_not_overridden_by_active_topic(self):
        runtime = self.runtime(8321)
        shared = runtime.logic.ensure_board()
        local = runtime.logic.create_board("Local Board").value
        runtime.session.start_discussion(shared.uuid)

        result = runtime.logic.select_board(local)
        payload = runtime.logic.board_payload()

        self.assertEqual(result.status, "ok")
        self.assertEqual(payload["board"]["uuid"], local)

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

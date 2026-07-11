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
        if path == "/p2p/sync_status":
            response, status = runtime.adapter.p2p_sync_status(payload)
            if status != 200:
                raise RuntimeError(response.get("reason", "sync failed"))
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

    def test_move_card_does_not_touch_sibling_hashes(self):
        runtime = self.runtime(8363)
        logic: KanbanLogic = runtime.logic
        board = logic.ensure_board()
        todo, doing = logic.columns(board)[:2]

        first = logic.create_card(todo.uuid, "First", "", []).value
        second = logic.create_card(todo.uuid, "Second", "", []).value
        third = logic.create_card(todo.uuid, "Third", "", []).value
        second_hash_before = runtime.session.protocol.index[second.uuid].state_hash
        third_hash_before = runtime.session.protocol.index[third.uuid].state_hash

        result = logic.move_card(first.uuid, doing.uuid, 0)

        self.assertEqual(result.status, "ok")
        self.assertEqual(
            runtime.session.protocol.index[second.uuid].state_hash,
            second_hash_before,
        )
        self.assertEqual(
            runtime.session.protocol.index[third.uuid].state_hash,
            third_hash_before,
        )

    def test_move_card_lands_between_neighbors_with_fractional_order(self):
        runtime = self.runtime(8364)
        logic: KanbanLogic = runtime.logic
        board = logic.ensure_board()
        todo, doing = logic.columns(board)[:2]

        first = logic.create_card(todo.uuid, "First", "", []).value
        second = logic.create_card(todo.uuid, "Second", "", []).value
        moving = logic.create_card(doing.uuid, "Moving", "", []).value

        result = logic.move_card(moving.uuid, todo.uuid, 1)

        self.assertEqual(result.status, "ok")
        ordered = logic.cards(runtime.session.protocol.index[todo.uuid])
        self.assertEqual(
            [card.uuid for card in ordered],
            [first.uuid, moving.uuid, second.uuid],
        )
        moved_order = runtime.session.protocol.index[moving.uuid].data["order"]
        self.assertGreater(moved_order, float(first.data["order"]))
        self.assertLess(moved_order, float(second.data["order"]))

    def test_move_card_renumbers_when_order_gap_is_exhausted(self):
        runtime = self.runtime(8365)
        logic: KanbanLogic = runtime.logic
        board = logic.ensure_board()
        todo, doing = logic.columns(board)[:2]

        first = logic.create_card(todo.uuid, "First", "", []).value
        second = logic.create_card(todo.uuid, "Second", "", []).value
        third = logic.create_card(doing.uuid, "Third", "", []).value
        logic.session.modify(first.uuid, {**first.data, "order": 0.0}, first.weights)
        logic.session.modify(second.uuid, {**second.data, "order": 1e-10}, second.weights)

        result = logic.move_card(third.uuid, todo.uuid, 1)

        self.assertEqual(result.status, "ok")
        ordered = logic.cards(runtime.session.protocol.index[todo.uuid])
        self.assertEqual(
            [card.uuid for card in ordered],
            [first.uuid, third.uuid, second.uuid],
        )
        self.assertEqual(
            [card.data["order"] for card in ordered],
            [0, 1, 2],
        )

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
        right.logic.set_auto_adopt_mode("always")

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
        right.logic.set_auto_adopt_mode("always")
        first, second = left.logic.columns(board)[:2]
        card = left.logic.create_card(first.uuid, "Move me", "", []).value
        left.adapter.execute_effects(left.session._sync_effects(board.uuid))
        right.logic.board_payload()

        left.logic.move_card(card.uuid, second.uuid, 0)
        left.adapter.execute_effects(left.session._sync_effects(board.uuid))
        right.logic.board_payload()

        self.assertEqual(right.session.protocol.index[card.uuid].parent_uuid, second.uuid)

    def test_auto_adopt_not_owner_skips_only_cards_i_own(self):
        left = self.runtime(8369)
        right = self.runtime(8370)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        left.logic.invite(left, right.address)
        left.logic.share_board(left, right.address, board.uuid)
        right.logic.set_auto_adopt_mode("always")
        column = left.logic.columns(board)[0]
        right_id = right.logic.user_profile().uuid
        left_id = left.logic.user_profile().uuid
        owned_by_me = left.logic.create_card(
            column.uuid, "Owned by me", "", [right_id], owner=right_id,
        ).value
        owned_by_peer = left.logic.create_card(
            column.uuid, "Owned by peer", "", [right_id], owner=left_id,
        ).value
        left.adapter.execute_effects(left.session._sync_effects(board.uuid))
        right.logic.board_payload()

        right.logic.set_auto_adopt_mode("not_owner")
        left.logic.update_card(owned_by_me.uuid, "Renamed (owned by me)", "", [right_id], owner=right_id)
        left.logic.update_card(owned_by_peer.uuid, "Renamed (owned by peer)", "", [right_id], owner=left_id)
        left.adapter.execute_effects(left.session._sync_effects(board.uuid))
        right.logic.board_payload()

        self.assertEqual(
            right.session.protocol.index[owned_by_me.uuid].data["name"], "Owned by me",
        )
        self.assertEqual(
            right.session.protocol.index[owned_by_peer.uuid].data["name"],
            "Renamed (owned by peer)",
        )

    def test_reaffirm_suppresses_auto_adopt_until_toggled_off(self):
        left = self.runtime(8373)
        right = self.runtime(8374)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        left.logic.invite(left, right.address)
        left.logic.share_board(left, right.address, board.uuid)
        right.logic.set_auto_adopt_mode("always")
        column = left.logic.columns(board)[0]

        card = left.logic.create_card(column.uuid, "Original", "", []).value
        left.adapter.execute_effects(left.session._sync_effects(board.uuid))
        right.logic.board_payload()
        self.assertEqual(right.session.protocol.index[card.uuid].data["name"], "Original")

        reaffirm_result = right.logic.reaffirm_node(card.uuid)
        self.assertEqual(reaffirm_result.status, "ok")

        left.logic.update_card(card.uuid, "Changed by left", "", [])
        left.adapter.execute_effects(left.session._sync_effects(board.uuid))
        right.logic.board_payload()

        self.assertEqual(
            right.session.protocol.index[card.uuid].data["name"], "Original",
        )

        # Toggling the reaffirm off lets auto-adopt catch up again.
        right.logic.reaffirm_node(card.uuid)
        right.logic.board_payload()

        self.assertEqual(
            right.session.protocol.index[card.uuid].data["name"], "Changed by left",
        )

    def test_auto_adopt_not_member_skips_any_card_im_on(self):
        left = self.runtime(8371)
        right = self.runtime(8372)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        left.logic.invite(left, right.address)
        left.logic.share_board(left, right.address, board.uuid)
        right.logic.set_auto_adopt_mode("always")
        column = left.logic.columns(board)[0]
        right_id = right.logic.user_profile().uuid
        im_a_member = left.logic.create_card(
            column.uuid, "I'm a member", "", [right_id],
        ).value
        not_involved = left.logic.create_card(
            column.uuid, "Not involved", "", [],
        ).value
        left.adapter.execute_effects(left.session._sync_effects(board.uuid))
        right.logic.board_payload()

        right.logic.set_auto_adopt_mode("not_member")
        left.logic.update_card(im_a_member.uuid, "Renamed (member)", "", [right_id])
        left.logic.update_card(not_involved.uuid, "Renamed (uninvolved)", "", [])
        left.adapter.execute_effects(left.session._sync_effects(board.uuid))
        right.logic.board_payload()

        self.assertEqual(
            right.session.protocol.index[im_a_member.uuid].data["name"], "I'm a member",
        )
        self.assertEqual(
            right.session.protocol.index[not_involved.uuid].data["name"],
            "Renamed (uninvolved)",
        )

    def test_three_peer_chain_auto_adopts_card_move(self):
        left = self.runtime(8356)
        middle = self.runtime(8357)
        right = self.runtime(8358)
        client = MemoryHttpClient({
            left.address: left,
            middle.address: middle,
            right.address: right,
        })
        for runtime in (left, middle, right):
            runtime.adapter.http = client
        board = left.logic.ensure_board()
        left.logic.share_board(left, middle.address, board.uuid)
        middle.logic.share_board(middle, right.address, board.uuid)
        middle.logic.set_auto_adopt_mode("always")
        right.logic.set_auto_adopt_mode("always")
        first, second = left.logic.columns(board)[:2]

        def tick():
            for runtime in (left, middle, right):
                result = runtime.logic.on_peer_update()
                runtime.adapter.execute_effects(result.effects)

        card = left.logic.create_card(first.uuid, "Move through chain", "", []).value
        left.adapter.execute_effects(left.session._sync_effects(board.uuid))
        for _ in range(3):
            tick()
        self.assertIn(card.uuid, middle.session.protocol.index)
        self.assertIn(card.uuid, right.session.protocol.index)

        result = left.logic.move_card(card.uuid, second.uuid, 0)
        left.adapter.execute_effects(result.effects)
        for _ in range(5):
            tick()

        for runtime in (left, middle, right):
            self.assertEqual(
                runtime.session.protocol.index[card.uuid].parent_uuid,
                second.uuid,
            )

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
        right.logic.set_auto_adopt_mode("never")

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

    def test_shared_board_defaults_to_auto_adopt_off(self):
        left = self.runtime(8361)
        right = self.runtime(8362)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()

        share = left.logic.share_board(left, right.address, board.uuid)

        self.assertEqual(share["status"], "ok")
        self.assertEqual(right.logic.ensure_board().uuid, board.uuid)
        self.assertEqual(right.logic.auto_adopt_mode(), "never")

    def test_adopt_peer_absence_deletes_local_card(self):
        left = self.runtime(8344)
        right = self.runtime(8345)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        left.logic.share_board(left, right.address, board.uuid)
        right.logic.set_auto_adopt_mode("never")
        right_board = right.logic.ensure_board()
        column = right.logic.columns(right_board)[0]
        card = right.logic.create_card(column.uuid, "Only local", "", []).value

        payload = right.logic.board_payload()
        self.assertEqual(
            payload["transition_by_node"][card.uuid]["type"],
            "peer_missing_node",
        )
        adopt = right.logic.accept_peer_node(
            left.address,
            card.uuid,
            adopt_absence=True,
        )

        self.assertEqual(adopt.status, "ok")
        self.assertNotIn(card.uuid, right.session.protocol.index)

    def test_moved_card_transition_collapses_missing_pair(self):
        left = self.runtime(8346)
        right = self.runtime(8347)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        left.logic.share_board(left, right.address, board.uuid)
        right.logic.set_auto_adopt_mode("always")
        first, second = left.logic.columns(board)[:2]
        card = left.logic.create_card(first.uuid, "Move me", "", []).value
        left.adapter.execute_effects(left.session._sync_effects(board.uuid))
        right.logic.board_payload()
        right.logic.set_auto_adopt_mode("never")

        left.logic.move_card(card.uuid, second.uuid, 0)
        PRSPNode.from_dict(left.session.protocol.root.to_dict())
        left.adapter.execute_effects(left.session._sync_effects(board.uuid))
        payload = right.logic.board_payload()

        self.assertEqual(
            payload["transition_by_node"][card.uuid]["type"],
            "peer_made_changes",
        )
        adopt = right.logic.accept_peer_node(left.address, card.uuid)
        self.assertEqual(adopt.status, "ok")
        self.assertEqual(
            right.session.protocol.index[card.uuid].parent_uuid,
            second.uuid,
        )
        PRSPNode.from_dict(right.session.protocol.root.to_dict())

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
        self.assertEqual(profile.data["type"], "shared_user_profile")
        self.assertEqual(profile.data["name"], "public_profile")
        self.assertEqual(profile.data["display_name"], "Alice")
        self.assertEqual(profile.data["picture"], "https://example.test/a.png")
        self.assertEqual(runtime.session.identity.uuid, profile.uuid)
        self.assertEqual(profile.children, [])

    def test_profile_topic_is_under_shared_user_data_and_not_adopted(self):
        left = self.runtime(8333)
        right = self.runtime(8334)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        left.logic.set_user_profile("Alice", "")

        board = left.logic.ensure_board()
        invite = left.logic.share_board(left, right.address, board.uuid)

        self.assertEqual(invite["status"], "ok")
        left_profile = left.logic.user_profile()
        left_container = left.session.protocol.index[left_profile.parent_uuid]
        self.assertEqual(left_container.data["name"], "shared_user_data")
        self.assertEqual(left_profile.data["name"], "public_profile")
        right_profile = right.logic.user_profile()
        self.assertNotIn(left_profile.uuid, right.session.protocol.index)
        self.assertNotEqual(left_profile.uuid, right_profile.uuid)
        self.assertEqual(
            right.session.get_cached_peer_subtree(left.address, left_profile.uuid).data["display_name"],
            "Alice",
        )

    def test_user_profile_does_not_reuse_peer_identity(self):
        left = self.runtime(8331)
        right = self.runtime(8332)
        left.logic.set_user_profile("Alice", "")
        peer_identity = PRSPNode.from_dict(left.logic.user_profile().to_dict())
        right.session.apply_peer_subtree(
            left.address,
            peer_identity,
            peer_identity.parent_uuid,
        )

        profile = right.logic.user_profile()

        self.assertNotEqual(profile.uuid, peer_identity.uuid)
        self.assertEqual(profile.data["address"], right.address)

    def test_share_board_shares_board_and_profile_topics(self):
        left = self.runtime(8322)
        right = self.runtime(8323)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        left.logic.ensure_board()
        left.logic.set_user_profile("Alice", "")

        board = left.logic.ensure_board()
        invite = left.logic.share_board(left, right.address, board.uuid)

        self.assertEqual(invite["status"], "ok")
        topics = invite["topic_uuids"]
        local_topic_data = [
            left.session.protocol.index[uuid].data
            for uuid in topics
            if uuid in left.session.protocol.index
        ]
        self.assertIn(board.uuid, topics)
        self.assertIn(left.logic.user_profile().uuid, topics)
        self.assertTrue(any(data.get("type") == "shared_user_profile" for data in local_topic_data))
        self.assertIn(right.address, left.session.members)
        left_identity = left.logic.user_profile().uuid
        right_identity = right.logic.user_profile().uuid
        self.assertIn(right_identity, left.session.fetch_topic_uuids(right.address))
        self.assertIn(left_identity, right.session.fetch_topic_uuids(left.address))

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
        self.assertIn(board.uuid, left.session.fetch_topic_uuids(right.address))
        self.assertIn(board.uuid, right.session.fetch_topic_uuids(left.address))
        for topic_uuid in right.session.fetch_topic_uuids(left.address):
            if topic_uuid != left.logic.user_profile().uuid:
                self.assertIn(topic_uuid, left.session.protocol.index)
        self.assertIsNotNone(
            right.session.get_cached_peer_subtree(left.address, left.logic.user_profile().uuid)
        )
        for topic_uuid in left.session.fetch_topic_uuids(right.address):
            if topic_uuid != right.logic.user_profile().uuid:
                self.assertIn(topic_uuid, right.session.protocol.index)
        self.assertIsNotNone(
            left.session.get_cached_peer_subtree(right.address, right.logic.user_profile().uuid)
        )

    def test_share_board_connects_identity_when_needed(self):
        left = self.runtime(8335)
        right = self.runtime(8336)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.create_board("Glow").value

        share = left.logic.share_board(left, right.address, board)

        self.assertEqual(share["status"], "ok")
        self.assertIn(board, right.session.protocol.index)
        self.assertIn(right.address, left.session.members)
        self.assertIn(left.address, right.session.members)
        self.assertIn(board, left.session.peer_topic_sets[right.address])
        self.assertIn(board, right.session.peer_topic_sets[left.address])

    def test_unshare_board_disconnects_when_no_board_remains(self):
        left = self.runtime(8337)
        right = self.runtime(8338)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.create_board("Glow").value
        left.logic.share_board(left, right.address, board)

        unshare = left.logic.unshare_board(left, board)

        self.assertEqual(unshare["status"], "ok")
        self.assertNotIn(right.address, left.session.members)
        self.assertNotIn(left.address, right.session.members)

    def test_unshare_board_keeps_identity_when_another_board_remains(self):
        left = self.runtime(8339)
        right = self.runtime(8340)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        first = left.logic.create_board("Glow").value
        second = left.logic.create_board("Flow").value
        left.logic.share_board(left, right.address, first)
        left.logic.share_board(left, right.address, second)

        unshare = left.logic.unshare_board(left, first)

        self.assertEqual(unshare["status"], "ok")
        self.assertIn(right.address, left.session.members)
        self.assertIn(left.address, right.session.members)
        self.assertNotIn(first, left.session.peer_topic_sets[right.address])
        self.assertIn(second, left.session.peer_topic_sets[right.address])

    def test_unshare_board_removes_topic_for_all_board_peers(self):
        left = self.runtime(8341)
        middle = self.runtime(8342)
        right = self.runtime(8343)
        client = MemoryHttpClient({
            left.address: left,
            middle.address: middle,
            right.address: right,
        })
        left.adapter.http = client
        middle.adapter.http = client
        right.adapter.http = client
        board = left.logic.create_board("Glow").value
        left.logic.share_board(left, middle.address, board)
        left.logic.share_board(left, right.address, board)

        unshare = left.logic.unshare_board(left, board)

        self.assertEqual(unshare["status"], "ok")
        self.assertNotIn(middle.address, left.session.members)
        self.assertNotIn(right.address, left.session.members)
        self.assertNotIn(left.address, middle.session.members)
        self.assertNotIn(left.address, right.session.members)

    def test_board_share_through_middle_peer_meshes_existing_board_members(self):
        first = self.runtime(8328)
        middle = self.runtime(8329)
        third = self.runtime(8330)
        client = MemoryHttpClient({
            first.address: first,
            middle.address: middle,
            third.address: third,
        })
        first.adapter.http = client
        middle.adapter.http = client
        third.adapter.http = client
        shared_board = first.logic.ensure_board()
        middle_private_board = middle.logic.create_board("Middle private").value
        first.logic.invite(first, middle.address)
        first.logic.share_board(first, middle.address, shared_board.uuid)
        middle.logic.invite(middle, third.address)

        share = middle.logic.share_board(middle, third.address, shared_board.uuid)

        self.assertEqual(share["status"], "ok")
        self.assertIn(
            shared_board.uuid,
            third.session.peer_topic_sets[first.address],
        )
        self.assertIn(
            shared_board.uuid,
            first.session.peer_topic_sets[third.address],
        )
        self.assertNotIn(middle_private_board, third.session.protocol.index)
        self.assertNotIn(
            middle_private_board,
            third.session.peer_topic_sets.get(first.address, set()),
        )

    def test_indirect_board_share_carries_known_peer_identities(self):
        first = self.runtime(8345)
        middle = self.runtime(8346)
        third = self.runtime(8347)
        client = MemoryHttpClient({
            first.address: first,
            middle.address: middle,
            third.address: third,
        })
        first.adapter.http = client
        middle.adapter.http = client
        third.adapter.http = client
        first.logic.set_user_profile("Alice", "https://example.test/a.png")
        shared_board = first.logic.ensure_board()
        first.logic.invite(first, middle.address)
        first.logic.share_board(first, middle.address, shared_board.uuid)
        middle.logic.invite(middle, third.address)

        share = middle.logic.share_board(middle, third.address, shared_board.uuid)

        self.assertEqual(share["status"], "ok")
        users = {user["address"]: user for user in third.logic.users()}
        self.assertEqual(users[first.address]["name"], "Alice")
        self.assertEqual(users[first.address]["picture"], "https://example.test/a.png")

    def test_existing_board_member_receives_new_member_profile(self):
        first = self.runtime(8350)
        middle = self.runtime(8351)
        third = self.runtime(8352)
        client = MemoryHttpClient({
            first.address: first,
            middle.address: middle,
            third.address: third,
        })
        first.adapter.http = client
        middle.adapter.http = client
        third.adapter.http = client
        first.logic.set_user_profile("Alice", "")
        middle.logic.set_user_profile("Bob", "")
        third.logic.set_user_profile("Cynthia", "")
        shared_board = first.logic.ensure_board()

        first.logic.share_board(first, middle.address, shared_board.uuid)
        share = first.logic.share_board(first, third.address, shared_board.uuid)

        self.assertEqual(share["status"], "ok")
        users = {user["address"]: user for user in middle.logic.users()}
        self.assertEqual(users[third.address]["name"], "Cynthia")

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
            [runtime.logic.user_profile().uuid],
        ).value

        self.assertEqual(card.data["participants"], [runtime.logic.user_profile().uuid])
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
        updated = runtime.session.get_node(card.uuid)
        self.assertEqual(updated.data["participants"], ["owner-2", "participant-2"])

    def test_card_owner_must_be_a_participant(self):
        runtime = self.runtime(8366)
        board = runtime.logic.ensure_board()
        column = runtime.logic.columns(board)[0]

        card = runtime.logic.create_card(
            column.uuid, "Task", "", ["alice", "bob"], owner="carol",
        ).value

        self.assertIsNone(card.data["owner"])

    def test_card_owner_kept_when_valid(self):
        runtime = self.runtime(8367)
        board = runtime.logic.ensure_board()
        column = runtime.logic.columns(board)[0]

        card = runtime.logic.create_card(
            column.uuid, "Task", "", ["alice", "bob"], owner="alice",
        ).value

        self.assertEqual(card.data["owner"], "alice")

    def test_card_owner_clears_when_removed_from_participants(self):
        runtime = self.runtime(8368)
        board = runtime.logic.ensure_board()
        column = runtime.logic.columns(board)[0]
        card = runtime.logic.create_card(
            column.uuid, "Task", "", ["alice", "bob"], owner="alice",
        ).value

        runtime.logic.update_card(card.uuid, "Task", "", ["bob"], owner="alice")

        updated = runtime.session.get_node(card.uuid)
        self.assertIsNone(updated.data["owner"])

    def test_three_peer_auto_adopt_deletes_all_cards(self):
        left = self.runtime(8353)
        middle = self.runtime(8354)
        right = self.runtime(8355)
        client = MemoryHttpClient({
            left.address: left,
            middle.address: middle,
            right.address: right,
        })
        for runtime in (left, middle, right):
            runtime.adapter.http = client
            runtime.logic.set_user_profile(runtime.address, "")
        board = left.logic.ensure_board()
        left.logic.share_board(left, middle.address, board.uuid)
        left.logic.share_board(left, right.address, board.uuid)

        def tick():
            for runtime in (left, middle, right):
                result = runtime.logic.on_peer_update()
                runtime.adapter.execute_effects(result.effects)

        def card_ids(runtime):
            out = []
            board_node = runtime.logic.ensure_board()
            for column in runtime.logic.columns(board_node):
                out.extend(card.uuid for card in runtime.logic.cards(column))
            return out

        for runtime, name in (
            (left, "A card"),
            (middle, "B card"),
            (right, "C card"),
        ):
            column = runtime.logic.columns(runtime.logic.ensure_board())[0]
            result = runtime.logic.create_card(column.uuid, name, "", [])
            runtime.adapter.execute_effects(result.effects)
            tick()

        self.assertEqual(len(card_ids(left)), 3)

        # Deletion now flows through the same auto-adopt path as any other
        # change (it's just another hashed field), so a joined peer only
        # picks it up automatically once its own mode allows adoption -
        # joiners default to "never" (manual review).
        middle.logic.set_auto_adopt_mode("always")
        right.logic.set_auto_adopt_mode("always")

        for card_uuid in list(card_ids(left)):
            result = left.logic.delete_card(card_uuid)
            left.adapter.execute_effects(result.effects)
            tick()

        self.assertEqual(card_ids(left), [])
        self.assertEqual(card_ids(middle), [])
        self.assertEqual(card_ids(right), [])

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
        right.logic.set_auto_adopt_mode("never")
        right.logic.select_board(board1.uuid)
        right.logic.set_auto_adopt_mode("always")
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

    def test_board_selection_is_local_app_metadata(self):
        runtime = self.runtime(8359)
        board = runtime.logic.ensure_board()
        root_before = runtime.session.protocol.root.state_hash

        result = runtime.logic.select_board(board.uuid)

        self.assertEqual(result.status, "ok")
        self.assertEqual(runtime.session.protocol.root.state_hash, root_before)
        self.assertEqual(
            runtime.session.app_metadata["apps"]["S-Kanban"]["selected_board_uuid"],
            board.uuid,
        )

    def test_auto_adopt_is_local_app_metadata(self):
        runtime = self.runtime(8360)
        board = runtime.logic.ensure_board()
        root_before = runtime.session.protocol.root.state_hash

        result = runtime.logic.set_auto_adopt_mode("never")

        self.assertEqual(result.status, "ok")
        self.assertEqual(runtime.logic.auto_adopt_mode(board), "never")
        self.assertEqual(runtime.session.protocol.root.state_hash, root_before)

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

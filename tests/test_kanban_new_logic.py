import tempfile
import time
import unittest
from pathlib import Path

import app_server
from s_kanban.logic import KanbanLogic
from sovereign.protocol import ProtocolNode
from sovereign.session import Session


def connect(host, guest, board_uuid: str | None = None) -> dict:
    """Wire two runtimes the way the app actually does: `guest` accepts a
    connect token offering `host`'s http channel.

    Replaces the removed address-based invite/share_board flow, which had no
    UI caller left. Omit board_uuid to share only the host's identity (the
    old `invite`); pass one to share a board too (the old `share_board`).
    """
    profile_uuid = host.logic.user_profile().uuid
    topic_uuids = [profile_uuid] if board_uuid is None else [board_uuid, profile_uuid]
    for topic_uuid in topic_uuids:
        host.session.start_discussion(topic_uuid)
    result = guest.channel_manager.accept_invitation(
        host.session.identity.to_dict(),
        topic_uuids,
        [{"type": "http", "descriptor_version": 1, "address": host.address}],
    )
    return result.value if result.ok else {
        "status": "error", "reason": result.reason,
    }


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

        self.assertEqual(result.status, "ok", result.reason)
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

        invite = connect(left, right)
        self.assertEqual(invite["status"], "ok")
        self.assertNotIn(board.uuid, right.session.protocol.index)
        share = connect(left, right, board.uuid)
        self.assertEqual(share["status"], "ok")
        self.assertEqual(right.logic.ensure_board().uuid, board.uuid)
        right.logic.set_auto_adopt_mode("always")

        column = left.logic.columns(board)[0]
        card = left.logic.create_card(column.uuid, "Shared", "", []).value
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
        right.logic.board_payload()

        self.assertIn(card.uuid, right.session.protocol.index)
        self.assertEqual(right.session.protocol.index[card.uuid].data["name"], "Shared")

    def test_agenda_priority_always_follows_its_originator(self):
        left = self.runtime(8393)
        right = self.runtime(8394)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        connect(left, right)
        connect(left, right, board.uuid)
        right.logic.set_auto_adopt_mode("never")

        item = left.logic.create_agenda_item("Discuss priority", "high").value
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
        right.logic.board_payload()

        # Simulate a stale/non-author copy that diverged from the same
        # previously agreed version. The public logic rejects this edit;
        # using the session directly recreates persisted pre-fix data.
        stale = right.session.protocol.index[item.uuid]
        stale_data = dict(stale.data)
        stale_data["priority"] = "low"
        right.session.modify(stale.uuid, stale_data, stale.weights)
        left.logic.set_agenda_item_priority(item.uuid, "medium")
        left_payload = left.session.get_subtree(board.uuid)
        right_payload = right.session.get_subtree(board.uuid)
        left.session.apply_peer_subtree(
            right.address,
            ProtocolNode.from_dict(right_payload["subtree"]),
            right_payload["parent_uuid"],
        )
        right.session.apply_peer_subtree(
            left.address,
            ProtocolNode.from_dict(left_payload["subtree"]),
            left_payload["parent_uuid"],
        )

        self.assertEqual(
            right.session.analyze_peer_transitions(left.address, item.uuid)[0]["type"],
            "divergence",
        )
        self.assertTrue(right.logic.adopt_incoming_changes())
        self.assertEqual(
            right.session.protocol.index[item.uuid].data["priority"],
            "medium",
        )

    def test_agenda_priority_revert_to_none_follows_its_originator(self):
        left = self.runtime(8397)
        right = self.runtime(8398)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        connect(left, right)
        connect(left, right, board.uuid)
        right.logic.set_auto_adopt_mode("never")
        item = left.logic.create_agenda_item("Radar").value
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
        right.logic.board_payload()

        left.logic.set_agenda_item_priority(item.uuid, "high")
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
        right.logic.board_payload()
        self.assertEqual(
            right.session.protocol.index[item.uuid].data["priority"], "high",
        )

        left.logic.set_agenda_item_priority(item.uuid, None)
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
        self.assertEqual(
            right.session.analyze_peer_transitions(left.address, item.uuid)[0]["type"],
            "in_transition",
        )
        right.logic.board_payload()

        self.assertIsNone(
            right.session.protocol.index[item.uuid].data["priority"],
        )

    def test_non_originator_cannot_change_agenda_priority(self):
        left = self.runtime(8395)
        right = self.runtime(8396)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        connect(left, right)
        connect(left, right, board.uuid)
        item = left.logic.create_agenda_item("Owned by left", "low").value
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
        right.logic.board_payload()

        result = right.logic.set_agenda_item_priority(item.uuid, "high")

        self.assertEqual(result.status, "error")
        self.assertEqual(right.session.protocol.index[item.uuid].data["priority"], "low")

    def test_two_clients_auto_adopt_card_move(self):
        left = self.runtime(8313)
        right = self.runtime(8314)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        connect(left, right)
        connect(left, right, board.uuid)
        right.logic.set_auto_adopt_mode("always")
        first, second = left.logic.columns(board)[:2]
        card = left.logic.create_card(first.uuid, "Move me", "", []).value
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
        right.logic.board_payload()

        left.logic.move_card(card.uuid, second.uuid, 0)
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
        right.logic.board_payload()

        self.assertEqual(right.session.protocol.index[card.uuid].parent_uuid, second.uuid)

    def test_auto_adopt_does_not_rollback_opposing_local_move(self):
        left = self.runtime(8325)
        right = self.runtime(8326)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        connect(left, right)
        connect(left, right, board.uuid)
        right.logic.set_auto_adopt_mode("always")
        first, second = left.logic.columns(board)[:2]
        card = left.logic.create_card(second.uuid, "Opposing move", "", []).value
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
        right.logic.board_payload()
        left.logic.move_card(card.uuid, first.uuid, 0)
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
        right.logic.board_payload()
        self.assertEqual(right.session.protocol.index[card.uuid].parent_uuid, first.uuid)

        right.logic.move_card(card.uuid, second.uuid, 0)
        right.logic.board_payload()

        self.assertEqual(right.session.protocol.index[card.uuid].parent_uuid, second.uuid)

    def test_auto_adopt_accepts_newer_move_back_after_agreement(self):
        left = self.runtime(8327)
        right = self.runtime(8328)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        connect(left, right)
        connect(left, right, board.uuid)
        left.logic.set_auto_adopt_mode("always")
        right.logic.set_auto_adopt_mode("always")
        first, second = left.logic.columns(board)[:2]
        card = left.logic.create_card(second.uuid, "Move back", "", []).value
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
        right.logic.board_payload()
        left.logic.move_card(card.uuid, first.uuid, 0)
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
        right.logic.board_payload()
        self.assertEqual(right.session.protocol.index[card.uuid].parent_uuid, first.uuid)

        time.sleep(0.002)
        right.logic.move_card(card.uuid, second.uuid, 0)
        payload = right.session.get_subtree(board.uuid)
        left.session.apply_peer_subtree(
            right.address,
            ProtocolNode.from_dict(payload["subtree"]),
            payload["parent_uuid"],
        )
        left.logic.board_payload()

        self.assertEqual(left.session.protocol.index[card.uuid].parent_uuid, second.uuid)

    def test_auto_adopt_not_owner_skips_only_cards_i_own(self):
        left = self.runtime(8369)
        right = self.runtime(8370)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        connect(left, right)
        connect(left, right, board.uuid)
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
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
        right.logic.board_payload()

        right.logic.set_auto_adopt_mode("not_owner")
        left.logic.update_card(owned_by_me.uuid, "Renamed (owned by me)", "", [right_id], owner=right_id)
        left.logic.update_card(owned_by_peer.uuid, "Renamed (owned by peer)", "", [right_id], owner=left_id)
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
        right.logic.board_payload()

        self.assertEqual(
            right.session.protocol.index[owned_by_me.uuid].data["name"], "Owned by me",
        )
        self.assertEqual(
            right.session.protocol.index[owned_by_peer.uuid].data["name"],
            "Renamed (owned by peer)",
        )

    def test_auto_adopt_not_member_skips_any_card_im_on(self):
        left = self.runtime(8371)
        right = self.runtime(8372)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        connect(left, right)
        connect(left, right, board.uuid)
        right.logic.set_auto_adopt_mode("always")
        column = left.logic.columns(board)[0]
        right_id = right.logic.user_profile().uuid
        im_a_member = left.logic.create_card(
            column.uuid, "I'm a member", "", [right_id],
        ).value
        not_involved = left.logic.create_card(
            column.uuid, "Not involved", "", [],
        ).value
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
        right.logic.board_payload()

        right.logic.set_auto_adopt_mode("not_member")
        left.logic.update_card(im_a_member.uuid, "Renamed (member)", "", [right_id])
        left.logic.update_card(not_involved.uuid, "Renamed (uninvolved)", "", [])
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
        right.logic.board_payload()

        self.assertEqual(
            right.session.protocol.index[im_a_member.uuid].data["name"], "I'm a member",
        )
        self.assertEqual(
            right.session.protocol.index[not_involved.uuid].data["name"],
            "Renamed (uninvolved)",
        )

    def test_not_owner_declines_column_deletion_holding_my_card(self):
        # Deleting a container removes its whole subtree at the protocol level
        # (no orphans). So under not_owner, Kanban must decline a column
        # deletion while it still holds a card I own - otherwise a later prune
        # of the deleted column would take my card with it. The column stays
        # as a divergence to resolve by hand; both it and my card survive.
        left = self.runtime(8373)
        right = self.runtime(8374)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        connect(left, right)
        connect(left, right, board.uuid)
        right.logic.set_auto_adopt_mode("always")
        column = left.logic.columns(board)[0]
        right_id = right.logic.user_profile().uuid
        my_card = left.logic.create_card(
            column.uuid, "Right's card", "", [right_id], owner=right_id,
        ).value
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
        right.logic.board_payload()
        self.assertIn(column.uuid, right.session.protocol.index)
        self.assertIn(my_card.uuid, right.session.protocol.index)

        right.logic.set_auto_adopt_mode("not_owner")
        left.logic.delete_column(column.uuid)
        # Two sync rounds: the first has right decline the column deletion, the
        # second gives a prune the chance to (wrongly) collect it if it hadn't.
        for _ in range(2):
            left.adapter.execute_effects(left.session.sync_effects(board.uuid))
            right.logic.board_payload()

        self.assertIn(column.uuid, right.session.protocol.index)
        self.assertFalse(right.session.protocol.index[column.uuid].deleted)
        self.assertIn(my_card.uuid, right.session.protocol.index)
        self.assertFalse(right.session.protocol.index[my_card.uuid].deleted)

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
        connect(left, middle, board.uuid)
        connect(middle, right, board.uuid)
        middle.logic.set_auto_adopt_mode("always")
        right.logic.set_auto_adopt_mode("always")
        first, second = left.logic.columns(board)[:2]

        def tick():
            for runtime in (left, middle, right):
                result = runtime.logic.on_peer_update()
                runtime.adapter.execute_effects(result.effects)

        card = left.logic.create_card(first.uuid, "Move through chain", "", []).value
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
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
        connect(left, right)
        connect(left, right, board.uuid)
        first, second = left.logic.columns(board)[:2]

        card = left.logic.create_card(first.uuid, "Hash safe", "", []).value
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
        right.logic.board_payload()
        right.adapter.execute_effects(right.session.sync_effects(board.uuid))
        left.logic.board_payload()

        left.logic.move_card(card.uuid, second.uuid, 0)
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
        right.logic.board_payload()
        right.adapter.execute_effects(right.session.sync_effects(board.uuid))
        left.logic.board_payload()

        for runtime in (left, right):
            for node_uuid in runtime.session.protocol.index:
                subtree = runtime.session.get_subtree(node_uuid)
                ProtocolNode.from_dict(subtree["subtree"])

    def test_auto_adopt_off_keeps_difference_until_adopt(self):
        left = self.runtime(8305)
        right = self.runtime(8306)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        connect(left, right)
        connect(left, right, board.uuid)
        right.logic.set_auto_adopt_mode("never")

        column = left.logic.columns(board)[0]
        card = left.logic.create_card(column.uuid, "Needs adopt", "", []).value
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
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

    def test_roll_back_restores_my_previous_card_revision(self):
        runtime = self.runtime(8309)
        board = runtime.logic.ensure_board()
        column = runtime.logic.columns(board)[0]
        card = runtime.logic.create_card(column.uuid, "Original", "", []).value
        runtime.session.apply_peer_subtree(
            "http://peer",
            ProtocolNode.from_dict(runtime.session.protocol.index[board.uuid].to_dict()),
            runtime.session.protocol.root.uuid,
        )
        previous = runtime.session.get_cached_peer_subtree("http://peer", card.uuid)

        runtime.logic.update_card(card.uuid, "First", "", [])
        runtime.logic.update_card(card.uuid, "Second", "", [])
        changed = runtime.session.protocol.index[card.uuid]
        self.assertEqual(changed.base_hash, previous.base_hash)

        result = runtime.logic.rollback_peer_node("http://peer", card.uuid)

        self.assertEqual(result.status, "ok", result.reason)
        rolled_back = runtime.session.protocol.index[card.uuid]
        self.assertEqual(rolled_back.data["name"], "Original")
        self.assertEqual(rolled_back.state_hash, previous.state_hash)
        self.assertEqual(rolled_back.base_hash, previous.base_hash)

    def test_adopting_column_fields_preserves_changed_cards(self):
        left = self.runtime(8373)
        right = self.runtime(8374)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        column = left.logic.columns(board)[0]
        card = left.logic.create_card(column.uuid, "Original card", "", []).value
        connect(left, right, board.uuid)
        right.logic.set_auto_adopt_mode("never")

        left.logic.rename_column(column.uuid, "Renamed column")
        left.logic.update_card(card.uuid, "Peer card", "", [])
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
        right.logic.board_payload()

        adopted = right.logic.accept_peer_node(left.address, column.uuid)

        self.assertEqual(adopted.status, "ok")
        self.assertEqual(
            right.session.protocol.index[column.uuid].data["name"],
            "Renamed column",
        )
        self.assertEqual(
            right.session.protocol.index[card.uuid].data["name"],
            "Original card",
        )

    def test_adopting_board_fields_preserves_columns_and_cards(self):
        left = self.runtime(8375)
        right = self.runtime(8376)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        column = left.logic.columns(board)[0]
        card = left.logic.create_card(column.uuid, "Original card", "", []).value
        connect(left, right, board.uuid)
        right.logic.set_auto_adopt_mode("never")

        left.logic.rename_board(board.uuid, "Renamed board")
        left.logic.rename_column(column.uuid, "Peer column")
        left.logic.update_card(card.uuid, "Peer card", "", [])
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
        right.logic.board_payload()

        adopted = right.logic.accept_peer_node(left.address, board.uuid)

        self.assertEqual(adopted.status, "ok")
        self.assertEqual(
            right.session.protocol.index[board.uuid].data["name"],
            "Renamed board",
        )
        self.assertNotEqual(
            right.session.protocol.index[column.uuid].data["name"],
            "Peer column",
        )
        self.assertEqual(
            right.session.protocol.index[card.uuid].data["name"],
            "Original card",
        )

    def test_new_shared_board_defaults_to_auto_adopt_always(self):
        left = self.runtime(8361)
        right = self.runtime(8362)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()

        share = connect(left, right, board.uuid)

        self.assertEqual(share["status"], "ok")
        self.assertEqual(right.logic.ensure_board().uuid, board.uuid)
        self.assertEqual(right.logic.auto_adopt_mode(), "always")

    def test_reconnecting_existing_board_retains_auto_adopt_setting(self):
        left = self.runtime(8367)
        right = self.runtime(8368)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        connect(left, right, board.uuid)
        right.logic.set_auto_adopt_mode("not_member")

        reconnect = connect(left, right, board.uuid)

        self.assertEqual(reconnect["status"], "ok")
        self.assertEqual(right.logic.auto_adopt_mode(), "not_member")

    def test_adopt_peer_absence_deletes_local_card(self):
        left = self.runtime(8344)
        right = self.runtime(8345)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        connect(left, right, board.uuid)
        right.logic.set_auto_adopt_mode("never")
        right_board = right.logic.ensure_board()
        column = right.logic.columns(right_board)[0]
        card = right.logic.create_card(column.uuid, "Only local", "", []).value

        payload = right.logic.board_payload()
        self.assertEqual(
            payload["transition_by_node"][card.uuid]["type"],
            "in_transition",
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
        connect(left, right, board.uuid)
        right.logic.set_auto_adopt_mode("always")
        first, second = left.logic.columns(board)[:2]
        card = left.logic.create_card(first.uuid, "Move me", "", []).value
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
        right.logic.board_payload()
        right.logic.set_auto_adopt_mode("never")

        left.logic.move_card(card.uuid, second.uuid, 0)
        ProtocolNode.from_dict(left.session.protocol.root.to_dict())
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
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
        ProtocolNode.from_dict(right.session.protocol.root.to_dict())

    def test_transition_event_prevents_stale_peer_rollback(self):
        left = self.runtime(8307)
        right = self.runtime(8308)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        connect(left, right)
        connect(left, right, board.uuid)
        column = left.logic.columns(board)[0]

        card = left.logic.create_card(column.uuid, "Local", "", []).value
        payload = left.logic.board_payload()

        self.assertIn(card.uuid, left.session.protocol.index)
        # The new card stays in_transition until the peer observes it; the
        # board is not re-revisioned by a descendant creation, so it stays
        # in_agreement (see the node_hash/subtree_hash split).
        self.assertEqual(
            payload["transition_by_node"][card.uuid]["type"],
            "in_transition",
        )
        self.assertEqual(
            payload["transition_by_node"][board.uuid]["type"],
            "in_agreement",
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

    def test_transition_by_node_marks_local_same_origin_target_as_rollback(self):
        runtime = self.runtime(8312)
        local_identity = runtime.logic.user_profile().data["identity_key"]
        node_uuid = "node-1"

        out = runtime.logic.transition_by_node([
            {
                "node_uuid": node_uuid,
                "type": "divergence",
                "original_type": "local_made_changes",
                "peer_addr": "http://127.0.0.1:8002",
                "local_revision_origin": local_identity,
                "peer_revision_origin": local_identity,
                "local_base_hash": "base",
                "peer_base_hash": "base",
            },
        ])

        self.assertEqual(out[node_uuid]["reaction"], "rollback")
        self.assertEqual(out[node_uuid]["events"][0]["reaction"], "rollback")

    def test_transition_by_node_deduplicates_a_revision_forwarded_by_another_peer(self):
        runtime = self.runtime(8313)
        node_uuid = "node-1"
        addr_a = "http://127.0.0.1:8001"
        addr_c = "http://127.0.0.1:8003"
        runtime.session.set_peer_identity_key(addr_a, "identity-a")
        runtime.session.set_peer_identity_key(addr_c, "identity-c")
        common = {
            "node_uuid": node_uuid,
            "type": "peer_made_changes",
            "origin_identity": "identity-c",
            "local_state_hash": "old",
            "peer_state_hash": "new",
        }

        out = runtime.logic.transition_by_node([
            {**common, "peer_addr": addr_a},
            {**common, "peer_addr": addr_c},
        ])

        info = out[node_uuid]
        self.assertEqual(info["origin_identity"], "identity-c")
        self.assertEqual(info["peer_addr"], addr_c)
        self.assertEqual(len(info["events"]), 1)
        self.assertEqual(
            info["events"][0]["delivery_peer_addrs"],
            [addr_a, addr_c],
        )

    def test_structured_peer_changes_describe_card_fields_people_and_move(self):
        left = self.runtime(8317)
        right = self.runtime(8318)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        left.profile.set_profile("Alice")
        right.profile.set_profile("Bob")
        alice = left.logic.user_profile().uuid
        bob = right.logic.user_profile().uuid
        board = left.logic.ensure_board()
        connect(left, right)
        connect(left, right, board.uuid)
        first, second = left.logic.columns(board)[:2]
        card = left.logic.create_card(
            first.uuid, "Radar", "Initial", [alice], alice,
        ).value
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
        right.logic.board_payload()
        right.logic.set_auto_adopt_mode("never")

        left.logic.update_card(
            card.uuid, "Radar", "Revised", [alice, bob], bob,
        )
        left.logic.move_card(card.uuid, second.uuid, 0)
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))

        changes = right.logic.describe_peer_changes(left.address, card.uuid)
        by_field = {change["field"]: change for change in changes}

        self.assertEqual(
            set(by_field),
            {"parent_uuid", "description", "participants", "owner"},
        )
        self.assertEqual(by_field["parent_uuid"]["local_label"], "To Do")
        self.assertEqual(by_field["parent_uuid"]["peer_label"], "Doing")
        self.assertEqual(by_field["participants"]["added_labels"], ["Bob"])
        self.assertEqual(
            by_field["participants"]["summary"], "Add Bob as participant",
        )
        self.assertEqual(
            by_field["participants"]["local_summary"],
            "Remove Bob as participant",
        )
        self.assertEqual(by_field["owner"]["local_label"], "Alice")
        self.assertEqual(by_field["owner"]["peer_label"], "Bob")
        self.assertEqual(by_field["description"]["local_value"], "Initial")
        self.assertEqual(by_field["description"]["peer_value"], "Revised")

        payload = right.logic.board_payload(auto_adopt=False)
        info = payload["transition_by_node"][card.uuid]
        self.assertEqual(
            {change["field"] for change in info["changes"]}, set(by_field),
        )
        self.assertEqual(
            {change["field"] for change in info["events"][0]["changes"]},
            set(by_field),
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

        result = runtime.profile.set_profile("Alice", "https://example.test/a.png")

        self.assertEqual(result.status, "ok")
        profile = runtime.logic.user_profile()
        self.assertEqual(profile.data["type"], "shared_user_profile")
        self.assertEqual(profile.data["name"], "public_profile")
        self.assertEqual(profile.data["display_name"], "Alice")
        self.assertEqual(profile.data["picture"], "https://example.test/a.png")
        self.assertEqual(runtime.session.identity.uuid, profile.uuid)
        self.assertEqual(profile.children, [])

    def test_profile_avatar_is_a_blob_reference_and_remove_clears_legacy_url(self):
        runtime = self.runtime(8390)
        blob_id = "sha256:" + "a" * 64
        runtime.profile.set_profile("Alice", "https://example.test/legacy.png")

        attached = runtime.profile.set_avatar({
            "id": "avatar-1", "role": "avatar", "blob_id": blob_id,
            "name": "alice.png", "size": 123, "mime": "image/png",
        })

        self.assertEqual(attached.status, "ok")
        self.assertEqual(runtime.logic.users()[0]["picture"], f"/api/blob/{blob_id}")
        self.assertEqual(runtime.logic.user_profile().data["picture"], "")

        removed = runtime.profile.set_avatar(None)

        self.assertEqual(removed.status, "ok")
        self.assertEqual(runtime.logic.users()[0]["picture"], "")
        self.assertEqual(runtime.logic.user_profile().data["attachments"], [])

    def test_profile_topic_is_under_shared_user_data_and_not_adopted(self):
        left = self.runtime(8333)
        right = self.runtime(8334)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        left.profile.set_profile("Alice", "")

        board = left.logic.ensure_board()
        invite = connect(left, right, board.uuid)

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
        left.profile.set_profile("Alice", "")
        peer_identity = ProtocolNode.from_dict(left.logic.user_profile().to_dict())
        right.session.apply_peer_subtree(
            left.address,
            peer_identity,
            peer_identity.parent_uuid,
        )

        profile = right.logic.user_profile()

        self.assertNotEqual(profile.uuid, peer_identity.uuid)
        self.assertNotEqual(profile.data["identity_key"], peer_identity.data["identity_key"])

    def test_connect_shares_board_and_profile_topics(self):
        left = self.runtime(8322)
        right = self.runtime(8323)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        left.logic.ensure_board()
        left.profile.set_profile("Alice", "")

        board = left.logic.ensure_board()
        result = connect(left, right, board.uuid)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["channels_used"], ["http"])
        # A share carries the board *and* the sharer's profile, so each side
        # ends up tracking the other's identity topic as well as the board.
        left_identity = left.logic.user_profile().uuid
        right_identity = right.logic.user_profile().uuid
        self.assertEqual(
            left.session.protocol.index[left_identity].data["type"],
            "shared_user_profile",
        )
        self.assertIn(board.uuid, right.session.protocol.index)
        self.assertIn(right.address, left.session.members)
        self.assertIn(right_identity, left.session.fetch_topic_uuids(right.address))
        self.assertIn(left_identity, right.session.fetch_topic_uuids(left.address))

    def test_connect_adds_selected_board_topic(self):
        left = self.runtime(8326)
        right = self.runtime(8327)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        connect(left, right)

        share = connect(left, right, board.uuid)

        self.assertEqual(share["status"], "ok")
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

        share = connect(left, right, board)

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
        connect(left, right, board)

        unshare = left.logic.unshare_board(board)
        left.channel_manager.execute_effects(unshare.effects)

        self.assertEqual(unshare.status, "ok")
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
        connect(left, right, first)
        connect(left, right, second)

        unshare = left.logic.unshare_board(first)
        left.channel_manager.execute_effects(unshare.effects)

        self.assertEqual(unshare.status, "ok")
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
        connect(left, middle, board)
        connect(left, right, board)

        unshare = left.logic.unshare_board(board)
        left.channel_manager.execute_effects(unshare.effects)

        self.assertEqual(unshare.status, "ok")
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
        connect(first, middle)
        connect(first, middle, shared_board.uuid)
        connect(middle, third)

        share = connect(middle, third, shared_board.uuid)

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
        first.profile.set_profile("Alice", "https://example.test/a.png")
        shared_board = first.logic.ensure_board()
        connect(first, middle)
        connect(first, middle, shared_board.uuid)
        connect(middle, third)

        share = connect(middle, third, shared_board.uuid)

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
        first.profile.set_profile("Alice", "")
        middle.profile.set_profile("Bob", "")
        third.profile.set_profile("Cynthia", "")
        shared_board = first.logic.ensure_board()

        connect(first, middle, shared_board.uuid)
        share = connect(first, third, shared_board.uuid)

        self.assertEqual(share["status"], "ok")
        users = {user["address"]: user for user in middle.logic.users()}
        self.assertEqual(users[third.address]["name"], "Cynthia")

    def test_kanban_caches_topic_for_an_inactive_application(self):
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

        result = right.adapter.join_discussion(left.address, other.uuid)

        self.assertEqual(result["status"], "ok")
        self.assertNotIn(other.uuid, right.session.protocol.index)
        self.assertIsNotNone(right.session.get_cached_peer_subtree(
            left.address, other.uuid,
        ))
        self.assertIn(other.uuid, right.session.pending_topic_invitations)

    def test_first_participant_is_owner(self):
        runtime = self.runtime(8317)
        runtime.profile.set_profile("Alice", "")
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

    def test_update_card_rejects_stale_expected_state_hash(self):
        # Review U-3 lost-update guard.
        runtime = self.runtime(8319)
        board = runtime.logic.ensure_board()
        column = runtime.logic.columns(board)[0]
        card = runtime.logic.create_card(column.uuid, "Task", "", []).value
        stale_hash = card.state_hash

        # Someone else changes the card first.
        runtime.logic.update_card(card.uuid, "Changed by peer", "", [])

        # A save carrying the now-stale hash is rejected...
        rejected = runtime.logic.update_card(
            card.uuid, "My edit", "", [], expected_state_hash=stale_hash,
        )
        self.assertEqual(rejected.status, "error")
        self.assertIn("changed while you were editing", rejected.reason)
        self.assertEqual(
            runtime.session.get_node(card.uuid).data["name"], "Changed by peer",
        )

        # ...but with the current hash it goes through.
        current = runtime.session.get_node(card.uuid).state_hash
        accepted = runtime.logic.update_card(
            card.uuid, "My edit", "", [], expected_state_hash=current,
        )
        self.assertEqual(accepted.status, "ok")

        # And with no hash at all it stays last-write-wins (back-compat).
        nohash = runtime.logic.update_card(card.uuid, "No-hash edit", "", [])
        self.assertEqual(nohash.status, "ok")

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
            runtime.profile.set_profile(runtime.address, "")
        board = left.logic.ensure_board()
        connect(left, middle, board.uuid)
        connect(left, right, board.uuid)

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
        connect(left, right)
        connect(left, right, board1.uuid)
        connect(left, right, board2)
        right.logic.select_board(board2)
        right.logic.set_auto_adopt_mode("never")
        right.logic.select_board(board1.uuid)
        right.logic.set_auto_adopt_mode("always")
        right.logic.select_board(board2)

        column = left.logic.columns(board1)[0]
        card = left.logic.create_card(column.uuid, "Board 1 card", "", []).value
        left.adapter.execute_effects(left.session.sync_effects(board1.uuid))
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
            runtime.session.app_metadata["apps"]["kanban"]["selected_board_uuid"],
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

    def test_create_agenda_item_defaults_to_no_priority(self):
        # logic.ensure_board() returns a read-only snapshot (Session.protocol
        # is a ReadOnlyProtocolView) - it goes stale the moment a mutation
        # happens, so agenda_items() is called with no argument throughout,
        # letting it re-resolve the board fresh each time.
        runtime = self.runtime(8378)
        logic: KanbanLogic = runtime.logic

        result = logic.create_agenda_item("Discuss roadmap")

        self.assertEqual(result.status, "ok")
        items = logic.agenda_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].data["text"], "Discuss roadmap")
        self.assertIsNone(items[0].data["priority"])
        self.assertEqual(items[0].data["author"], logic.user_profile().uuid)

    def test_create_agenda_item_rejects_unknown_priority(self):
        runtime = self.runtime(8379)
        logic: KanbanLogic = runtime.logic

        logic.create_agenda_item("Something", priority="urgent!!")

        self.assertIsNone(logic.agenda_items()[0].data["priority"])

    def test_set_agenda_item_priority_updates_an_existing_item(self):
        runtime = self.runtime(8386)
        logic: KanbanLogic = runtime.logic
        item = logic.create_agenda_item("Something").value

        result = logic.set_agenda_item_priority(item.uuid, "high")

        self.assertEqual(result.status, "ok")
        self.assertEqual(logic.agenda_items()[0].data["priority"], "high")

    def test_set_agenda_item_priority_can_clear_it(self):
        runtime = self.runtime(8387)
        logic: KanbanLogic = runtime.logic
        item = logic.create_agenda_item("Something", priority="high").value

        logic.set_agenda_item_priority(item.uuid, None)

        self.assertIsNone(logic.agenda_items()[0].data["priority"])

    def test_set_agenda_item_priority_rejects_unknown_uuid(self):
        runtime = self.runtime(8388)
        logic: KanbanLogic = runtime.logic

        result = logic.set_agenda_item_priority("does-not-exist", "high")

        self.assertEqual(result.status, "error")

    def test_agenda_items_unset_priority_sorts_after_low(self):
        runtime = self.runtime(8389)
        logic: KanbanLogic = runtime.logic

        logic.create_agenda_item("No priority")
        logic.create_agenda_item("Low item", priority="low")

        items = logic.agenda_items()

        self.assertEqual([item.data["text"] for item in items], ["Low item", "No priority"])

    def test_agenda_items_sort_high_before_medium_before_low(self):
        runtime = self.runtime(8380)
        logic: KanbanLogic = runtime.logic

        logic.create_agenda_item("Low item", priority="low")
        logic.create_agenda_item("High item", priority="high")
        logic.create_agenda_item("Medium item", priority="medium")

        items = logic.agenda_items()

        self.assertEqual(
            [item.data["text"] for item in items],
            ["High item", "Medium item", "Low item"],
        )

    def test_agenda_items_same_priority_keeps_creation_order(self):
        runtime = self.runtime(8381)
        logic: KanbanLogic = runtime.logic

        logic.create_agenda_item("First", priority="high")
        logic.create_agenda_item("Second", priority="high")

        items = logic.agenda_items()

        self.assertEqual([item.data["text"] for item in items], ["First", "Second"])

    def test_delete_agenda_item_removes_it(self):
        runtime = self.runtime(8382)
        logic: KanbanLogic = runtime.logic
        item = logic.create_agenda_item("Temporary").value

        result = logic.delete_agenda_item(item.uuid)

        self.assertEqual(result.status, "ok")
        self.assertEqual(logic.agenda_items(), [])

    def test_delete_agenda_item_rejects_unknown_uuid(self):
        runtime = self.runtime(8383)
        logic: KanbanLogic = runtime.logic

        result = logic.delete_agenda_item("does-not-exist")

        self.assertEqual(result.status, "error")

    def test_board_payload_includes_agenda_items(self):
        runtime = self.runtime(8384)
        logic: KanbanLogic = runtime.logic
        logic.create_agenda_item("Discuss roadmap", priority="high")

        payload = logic.board_payload()

        self.assertEqual(len(payload["agenda_items"]), 1)
        self.assertEqual(payload["agenda_items"][0]["data"]["text"], "Discuss roadmap")
        self.assertEqual(payload["agenda_items"][0]["data"]["priority"], "high")

    def test_transition_by_node_carries_priority_field(self):
        # This field is read directly by kanban.html's discussion list, so its
        # absence would only ever surface as a silent UI bug, not a
        # backend error.
        runtime = self.runtime(8385)
        logic: KanbanLogic = runtime.logic

        out = logic.transition_by_node([
            {"node_uuid": "node-1", "type": "divergence", "peer_addr": "http://127.0.0.1:8002"},
        ])

        self.assertEqual(out["node-1"]["priority"], 6)
        self.assertEqual(out["node-1"]["events"][0]["priority"], 6)

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

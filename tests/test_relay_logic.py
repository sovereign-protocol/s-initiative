import base64
import json
import tempfile
import unittest
from pathlib import Path

from kanban_logic import KanbanLogic
from relay_logic import RelayLogic
from relay_storage import LocalFolderRelayStorage
from session import Session


class LocalFolderRelayStorageTests(unittest.TestCase):
    def test_write_then_read_round_trips_head_and_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            storage = LocalFolderRelayStorage(root)

            storage.write_snapshot("topic-1", "A", "hash-1", {"subtree": {"name": "x"}, "parent_uuid": None})

            head = storage.read_head("topic-1", "A")
            self.assertEqual(head["hash"], "hash-1")
            self.assertEqual(head["peer"], "A")
            snapshot = storage.read_snapshot("topic-1", "A", "hash-1")
            self.assertEqual(snapshot["subtree"], {"name": "x"})

    def test_read_missing_peer_or_topic_returns_none(self):
        with tempfile.TemporaryDirectory() as root:
            storage = LocalFolderRelayStorage(root)

            self.assertIsNone(storage.read_head("no-such-topic", "A"))
            self.assertIsNone(storage.read_snapshot("no-such-topic", "A", "hash-1"))
            self.assertEqual(storage.list_peers("no-such-topic"), [])

    def test_second_write_overwrites_head_but_keeps_old_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            storage = LocalFolderRelayStorage(root)
            storage.write_snapshot("topic-1", "A", "hash-1", {"subtree": {"n": 1}, "parent_uuid": None})

            storage.write_snapshot("topic-1", "A", "hash-2", {"subtree": {"n": 2}, "parent_uuid": None})

            self.assertEqual(storage.read_head("topic-1", "A")["hash"], "hash-2")
            self.assertEqual(storage.read_snapshot("topic-1", "A", "hash-1")["subtree"], {"n": 1})
            self.assertEqual(storage.read_snapshot("topic-1", "A", "hash-2")["subtree"], {"n": 2})

    def test_write_is_atomic_no_tmp_files_left_behind(self):
        with tempfile.TemporaryDirectory() as root:
            storage = LocalFolderRelayStorage(root)
            storage.write_snapshot("topic-1", "A", "hash-1", {"subtree": {}, "parent_uuid": None})

            leftovers = list(Path(root).rglob("*.tmp"))
            self.assertEqual(leftovers, [])


class RelayLogicTests(unittest.TestCase):
    def _relay_config(self, relay_root: str, identity: str, state_dir: str) -> dict:
        return {
            "relay_root": relay_root,
            "relay_identity": identity,
            "relay_state_file": str(Path(state_dir) / f"state-{identity}.json"),
        }

    def test_publish_then_apply_runs_through_existing_reconciliation(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            kanban_a = KanbanLogic(session_a, {})
            board_uuid = kanban_a.create_board("Shared Board").value
            board = kanban_a.ensure_board()
            self.assertEqual(board.uuid, board_uuid)
            todo = kanban_a.columns(board)[0]
            card = kanban_a.create_card(todo.uuid, "Test Card").value

            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            published = relay_a.publish_due_topics()
            self.assertEqual(published, [board.uuid])

            session_b = Session("addr-b")
            relay_b = RelayLogic(session_b, self._relay_config(relay_root, "B", state_dir))
            applied = relay_b.poll_and_apply()

            self.assertEqual(applied, [(board.uuid, "A")])
            cached = session_b.get_cached_peer_subtree("relay:A", card.uuid)
            self.assertIsNotNone(cached)
            self.assertEqual(cached.data["name"], "Test Card")
            # Same reconciliation Session already runs for a live HTTP peer
            # push (test_session.py's own analyze_peer_transitions tests use
            # this exact method) - proves the relay path isn't a special
            # case at the divergence-detection layer.
            events = session_b.analyze_peer_transitions("relay:A", board.uuid)
            self.assertTrue(
                any(e["node_uuid"] == card.uuid and e["type"] != "in_agreement" for e in events)
            )

    def test_republishing_unchanged_state_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            kanban_a = KanbanLogic(session_a, {})
            kanban_a.create_board("Board")
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            first = relay_a.publish_due_topics()
            self.assertEqual(len(first), 1)

            second = relay_a.publish_due_topics()

            self.assertEqual(second, [])

    def test_repolling_without_a_new_publish_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            KanbanLogic(session_a, {}).create_board("Board")
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a.publish_due_topics()
            session_b = Session("addr-b")
            relay_b = RelayLogic(session_b, self._relay_config(relay_root, "B", state_dir))
            first = relay_b.poll_and_apply()
            self.assertEqual(len(first), 1)

            second = relay_b.poll_and_apply()

            self.assertEqual(second, [])

    def test_bookkeeping_never_appears_as_prsp_data(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            KanbanLogic(session_a, {}).create_board("Board")
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a.publish_due_topics()
            session_b = Session("addr-b")
            relay_b = RelayLogic(session_b, self._relay_config(relay_root, "B", state_dir))
            relay_b.poll_and_apply()

            for node in session_b.protocol.index.values():
                self.assertNotIn("published", node.data)
                self.assertNotIn("relay_state", node.data)
            state_file = Path(relay_b._state_path)
            self.assertTrue(state_file.is_file())
            with state_file.open(encoding="utf-8") as f:
                state = json.load(f)
            self.assertIn("applied", state)

    def test_all_boards_sync_automatically_no_allow_list(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            kanban_a = KanbanLogic(session_a, {})
            first_uuid = kanban_a.create_board("Board One").value
            second_uuid = kanban_a.create_board("Board Two").value
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))

            self.assertEqual(set(relay_a.relay_topic_uuids()), {first_uuid, second_uuid})
            published = relay_a.publish_due_topics()
            self.assertEqual(set(published), {first_uuid, second_uuid})

    def test_relay_inactive_without_relay_root_configured(self):
        session_a = Session("addr-a")
        KanbanLogic(session_a, {}).create_board("Board")
        relay_a = RelayLogic(session_a, {})

        self.assertIsNone(relay_a.storage)
        self.assertEqual(relay_a.publish_due_topics(), [])
        self.assertEqual(relay_a.poll_and_apply(), [])
        self.assertFalse(relay_a.status_payload()["configured"])

    def test_accept_connect_token_grafts_a_never_before_seen_board(self):
        # The scenario the token-accept path exists for: A and B have never
        # joined directly (no add_peer/handle_join at all), only relayed
        # through the shared folder plus a token exchanged out-of-band -
        # proving a board can be shared via relay alone.
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            kanban_a = KanbanLogic(session_a, {})
            board_uuid = kanban_a.create_board("Shared Board").value
            token = base64.b64encode(
                json.dumps({"address": "addr-a", "topic_uuids": [board_uuid]}).encode("utf-8")
            ).decode("ascii")
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a.publish_due_topics()

            session_b = Session("addr-b")
            relay_b = RelayLogic(session_b, self._relay_config(relay_root, "B", state_dir))
            accept_result = relay_b.accept_connect_token(token)
            self.assertEqual(accept_result.status, "ok")
            applied = relay_b.poll_and_apply()

            self.assertEqual(applied, [(board_uuid, "A")])
            kanban_b = KanbanLogic(session_b, {})
            self.assertIn(board_uuid, [b.uuid for b in kanban_b.boards()])
            self.assertEqual(kanban_b.auto_adopt_mode(session_b.protocol.index[board_uuid]), "never")

    def test_accept_token_after_hash_already_cached_still_grafts(self):
        # Regression: if a poll already saw+cached this exact hash before
        # the token arrived (not desired yet at that point), bookkeeping
        # must not treat "hash unchanged since last_seen" as "nothing to
        # do" - the graft is still pending even though the content isn't new.
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            kanban_a = KanbanLogic(session_a, {})
            board_uuid = kanban_a.create_board("Shared Board").value
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a.publish_due_topics()

            session_b = Session("addr-b")
            relay_b = RelayLogic(session_b, self._relay_config(relay_root, "B", state_dir))
            relay_b.poll_and_apply()  # caches it before any token exists
            kanban_b = KanbanLogic(session_b, {})
            self.assertNotIn(board_uuid, [b.uuid for b in kanban_b.boards()])

            token = base64.b64encode(
                json.dumps({"address": "addr-a", "topic_uuids": [board_uuid]}).encode("utf-8")
            ).decode("ascii")
            relay_b.accept_connect_token(token)
            applied = relay_b.poll_and_apply()

            self.assertEqual(applied, [(board_uuid, "A")])
            self.assertIn(board_uuid, [b.uuid for b in kanban_b.boards()])

    def test_poll_without_a_matching_token_only_caches_never_grafts(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            kanban_a = KanbanLogic(session_a, {})
            board_uuid = kanban_a.create_board("Private Board").value
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a.publish_due_topics()

            session_b = Session("addr-b")
            relay_b = RelayLogic(session_b, self._relay_config(relay_root, "B", state_dir))
            relay_b.poll_and_apply()

            kanban_b = KanbanLogic(session_b, {})
            self.assertNotIn(board_uuid, [b.uuid for b in kanban_b.boards()])
            self.assertIsNotNone(session_b.get_cached_peer_subtree("relay:A", board_uuid))

    def test_accepted_board_keeps_syncing_on_later_polls(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            kanban_a = KanbanLogic(session_a, {})
            board_uuid = kanban_a.create_board("Shared Board").value
            board = kanban_a.ensure_board()
            todo = kanban_a.columns(board)[0]
            token = base64.b64encode(
                json.dumps({"address": "addr-a", "topic_uuids": [board_uuid]}).encode("utf-8")
            ).decode("ascii")
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a.publish_due_topics()

            session_b = Session("addr-b")
            relay_b = RelayLogic(session_b, self._relay_config(relay_root, "B", state_dir))
            relay_b.accept_connect_token(token)
            relay_b.poll_and_apply()

            card = kanban_a.create_card(todo.uuid, "Later Card").value
            relay_a.publish_due_topics()
            applied = relay_b.poll_and_apply()

            self.assertEqual(applied, [(board_uuid, "A")])
            kanban_b = KanbanLogic(session_b, {})
            # Accepted boards default to "never" (manual review), matching
            # the live-join accept path - opt in explicitly to prove the
            # later card is visible to auto-adopt once the user does so.
            kanban_b.set_auto_adopt_mode("always")
            changed = kanban_b.on_peer_update()
            self.assertTrue(changed.value)
            self.assertIsNotNone(session_b.protocol.index.get(card.uuid))

    def test_default_state_file_differs_per_identity_not_just_per_config(self):
        # Regression: two instances sharing one codebase checkout (the
        # "same machine, local folder" scenario) must not silently collide
        # on the same bookkeeping file just because they share app_module -
        # config has no reliable per-instance key (no "port" entry), only
        # relay_identity is guaranteed to differ.
        with tempfile.TemporaryDirectory() as relay_root:
            session_a = Session("addr-a")
            relay_a = RelayLogic(session_a, {"relay_root": relay_root, "relay_identity": "A"})
            session_b = Session("addr-b")
            relay_b = RelayLogic(session_b, {"relay_root": relay_root, "relay_identity": "B"})

            self.assertNotEqual(relay_a._state_path, relay_b._state_path)


if __name__ == "__main__":
    unittest.main()

import json
import os
import stat as stat_module
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from kanban_logic import KanbanLogic
from relay_logic import RelayLogic, channel_descriptor
from relay_storage import LocalFolderRelayStorage, SftpRelayStorage
from session import Session


class FakeSftpFile:
    def __init__(self, client, path, write, data=b""):
        self.client = client
        self.path = path
        self.write_mode = write
        self.buffer = bytearray()
        self.data = data

    def write(self, chunk):
        self.buffer.extend(chunk)

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        if self.write_mode:
            self.client.files[self.path] = bytes(self.buffer)
            self.client._touch(self.path)
        return False


class FakeSftpClient:
    """In-memory stand-in for paramiko.SFTPClient, covering only the calls
    SftpRelayStorage makes - lets us test path construction, atomic-rename
    fallback, and retry behavior without a real network/SSH server."""

    def __init__(self, supports_posix_rename=True):
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = {"/"}
        self.mtimes: dict[str, float] = {}
        self.supports_posix_rename = supports_posix_rename
        self.fail_next_calls = 0
        self._clock = 0.0

    def _maybe_fail(self):
        if self.fail_next_calls > 0:
            self.fail_next_calls -= 1
            raise OSError("simulated transient connection failure")

    def _touch(self, path):
        # A deterministic, strictly-increasing fake clock (not real
        # wall-clock time) - keeps mtime-ordering tests fast and exact
        # instead of depending on real elapsed time between writes.
        self._clock += 1.0
        self.mtimes[path] = self._clock

    def open(self, path, mode):
        self._maybe_fail()
        if "w" in mode:
            return FakeSftpFile(self, path, write=True)
        if path not in self.files:
            raise FileNotFoundError(path)
        return FakeSftpFile(self, path, write=False, data=self.files[path])

    def stat(self, path):
        self._maybe_fail()
        if path in self.dirs:
            return types.SimpleNamespace(st_mode=stat_module.S_IFDIR, st_mtime=self.mtimes.get(path))
        if path in self.files:
            return types.SimpleNamespace(st_mode=stat_module.S_IFREG, st_mtime=self.mtimes.get(path))
        raise FileNotFoundError(path)

    def mkdir(self, path):
        self._maybe_fail()
        self.dirs.add(path)

    def listdir_attr(self, path):
        self._maybe_fail()
        if path not in self.dirs:
            raise FileNotFoundError(path)
        prefix = path.rstrip("/") + "/"
        names = set()
        for existing in list(self.files) + list(self.dirs):
            if existing.startswith(prefix) and existing != path:
                names.add(existing[len(prefix):].split("/")[0])
        result = []
        for name in sorted(names):
            full = prefix + name
            mode = stat_module.S_IFDIR if full in self.dirs else stat_module.S_IFREG
            result.append(types.SimpleNamespace(filename=name, st_mode=mode))
        return result

    def remove(self, path):
        self._maybe_fail()
        if path not in self.files:
            raise FileNotFoundError(path)
        del self.files[path]
        self.mtimes.pop(path, None)

    def rmdir(self, path):
        self._maybe_fail()
        if path not in self.dirs:
            raise FileNotFoundError(path)
        self.dirs.discard(path)

    def rename(self, old, new):
        self._maybe_fail()
        if old not in self.files:
            raise FileNotFoundError(old)
        if new in self.files:
            raise OSError("destination already exists")
        self.files[new] = self.files.pop(old)
        self.mtimes[new] = self.mtimes.pop(old, None)

    def posix_rename(self, old, new):
        self._maybe_fail()
        if not self.supports_posix_rename:
            raise OSError("posix_rename extension not supported")
        if old not in self.files:
            raise FileNotFoundError(old)
        self.files[new] = self.files.pop(old)
        self.mtimes[new] = self.mtimes.pop(old, None)

    def close(self):
        pass


def _sftp_storage_with_fake(fake: FakeSftpClient) -> SftpRelayStorage:
    storage = SftpRelayStorage(
        host="example.test", username="u", remote_root="/relay",
    )
    storage._sftp = fake
    # _with_retry reconnects via _connect() on failure - stub it to hand
    # back the same fake (now past its simulated failure) rather than
    # attempting a real SSH connection.
    storage._connect = lambda: setattr(storage, "_sftp", fake)
    return storage


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

    def test_delete_topic_removes_all_peers_under_it(self):
        with tempfile.TemporaryDirectory() as root:
            storage = LocalFolderRelayStorage(root)
            storage.write_snapshot("topic-1", "A", "hash-1", {"subtree": {}, "parent_uuid": None})
            storage.write_snapshot("topic-1", "B", "hash-2", {"subtree": {}, "parent_uuid": None})
            storage.write_snapshot("topic-2", "A", "hash-3", {"subtree": {}, "parent_uuid": None})

            storage.delete_topic("topic-1")

            self.assertEqual(storage.list_topics(), ["topic-2"])
            self.assertIsNone(storage.read_head("topic-1", "A"))
            self.assertIsNone(storage.read_head("topic-1", "B"))

    def test_delete_topic_missing_topic_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as root:
            storage = LocalFolderRelayStorage(root)
            storage.delete_topic("no-such-topic")  # must not raise

    def test_write_presence_then_read_round_trips_with_mtime(self):
        with tempfile.TemporaryDirectory() as root:
            storage = LocalFolderRelayStorage(root)

            mtime = storage.write_presence("A", {"poll_interval_seconds": 3})

            self.assertIsInstance(mtime, float)
            content, read_mtime = storage.read_presence_with_mtime("A")
            self.assertEqual(content["poll_interval_seconds"], 3)
            self.assertEqual(read_mtime, mtime)

    def test_read_presence_missing_identity_returns_none_none(self):
        with tempfile.TemporaryDirectory() as root:
            storage = LocalFolderRelayStorage(root)

            content, mtime = storage.read_presence_with_mtime("nobody")

            self.assertIsNone(content)
            self.assertIsNone(mtime)


class SftpRelayStorageTests(unittest.TestCase):
    def test_write_then_read_round_trips_head_and_snapshot(self):
        fake = FakeSftpClient()
        storage = _sftp_storage_with_fake(fake)

        storage.write_snapshot("topic-1", "A", "hash-1", {"subtree": {"name": "x"}, "parent_uuid": None})

        head = storage.read_head("topic-1", "A")
        self.assertEqual(head["hash"], "hash-1")
        self.assertEqual(head["peer"], "A")
        snapshot = storage.read_snapshot("topic-1", "A", "hash-1")
        self.assertEqual(snapshot["subtree"], {"name": "x"})

    def test_read_missing_peer_or_topic_returns_none(self):
        fake = FakeSftpClient()
        storage = _sftp_storage_with_fake(fake)

        self.assertIsNone(storage.read_head("no-such-topic", "A"))
        self.assertIsNone(storage.read_snapshot("no-such-topic", "A", "hash-1"))
        self.assertEqual(storage.list_peers("no-such-topic"), [])
        self.assertEqual(storage.list_topics(), [])

    def test_second_write_overwrites_head_but_keeps_old_snapshot(self):
        fake = FakeSftpClient()
        storage = _sftp_storage_with_fake(fake)
        storage.write_snapshot("topic-1", "A", "hash-1", {"subtree": {"n": 1}, "parent_uuid": None})

        storage.write_snapshot("topic-1", "A", "hash-2", {"subtree": {"n": 2}, "parent_uuid": None})

        self.assertEqual(storage.read_head("topic-1", "A")["hash"], "hash-2")
        self.assertEqual(storage.read_snapshot("topic-1", "A", "hash-1")["subtree"], {"n": 1})
        self.assertEqual(storage.read_snapshot("topic-1", "A", "hash-2")["subtree"], {"n": 2})

    def test_write_leaves_no_tmp_files_behind(self):
        fake = FakeSftpClient()
        storage = _sftp_storage_with_fake(fake)

        storage.write_snapshot("topic-1", "A", "hash-1", {"subtree": {}, "parent_uuid": None})

        self.assertEqual([p for p in fake.files if p.endswith(".tmp")], [])

    def test_list_peers_and_topics(self):
        fake = FakeSftpClient()
        storage = _sftp_storage_with_fake(fake)
        storage.write_snapshot("topic-1", "A", "hash-1", {"subtree": {}, "parent_uuid": None})
        storage.write_snapshot("topic-1", "B", "hash-2", {"subtree": {}, "parent_uuid": None})
        storage.write_snapshot("topic-2", "A", "hash-3", {"subtree": {}, "parent_uuid": None})

        self.assertEqual(storage.list_peers("topic-1"), ["A", "B"])
        self.assertEqual(storage.list_topics(), ["topic-1", "topic-2"])

    def test_write_falls_back_to_remove_then_rename_without_posix_rename(self):
        # Not every SFTP server supports the posix_rename extension -
        # confirms the fallback path (remove existing, then plain rename)
        # still produces a correct, atomic-from-the-caller's-view result.
        fake = FakeSftpClient(supports_posix_rename=False)
        storage = _sftp_storage_with_fake(fake)
        storage.write_snapshot("topic-1", "A", "hash-1", {"subtree": {"n": 1}, "parent_uuid": None})

        storage.write_snapshot("topic-1", "A", "hash-2", {"subtree": {"n": 2}, "parent_uuid": None})

        self.assertEqual(storage.read_head("topic-1", "A")["hash"], "hash-2")

    def test_transient_failure_triggers_one_reconnect_and_retry(self):
        fake = FakeSftpClient()
        fake.fail_next_calls = 1
        storage = _sftp_storage_with_fake(fake)

        storage.write_snapshot("topic-1", "A", "hash-1", {"subtree": {"n": 1}, "parent_uuid": None})

        self.assertEqual(storage.read_head("topic-1", "A")["hash"], "hash-1")

    def test_delete_topic_removes_all_peers_under_it(self):
        fake = FakeSftpClient()
        storage = _sftp_storage_with_fake(fake)
        storage.write_snapshot("topic-1", "A", "hash-1", {"subtree": {}, "parent_uuid": None})
        storage.write_snapshot("topic-1", "B", "hash-2", {"subtree": {}, "parent_uuid": None})
        storage.write_snapshot("topic-2", "A", "hash-3", {"subtree": {}, "parent_uuid": None})

        storage.delete_topic("topic-1")

        self.assertEqual(storage.list_topics(), ["topic-2"])
        self.assertIsNone(storage.read_head("topic-1", "A"))
        self.assertIsNone(storage.read_head("topic-1", "B"))
        # No leftover empty directories either - a real listdir_attr on the
        # deleted path would now raise, same as the local backend's is_dir().
        self.assertNotIn("/relay/topics/topic-1", fake.dirs)

    def test_delete_topic_missing_topic_is_a_no_op(self):
        fake = FakeSftpClient()
        storage = _sftp_storage_with_fake(fake)

        storage.delete_topic("no-such-topic")  # must not raise

    def test_write_presence_then_read_round_trips_with_mtime(self):
        fake = FakeSftpClient()
        storage = _sftp_storage_with_fake(fake)

        mtime = storage.write_presence("A", {"poll_interval_seconds": 3})

        self.assertIsInstance(mtime, float)
        content, read_mtime = storage.read_presence_with_mtime("A")
        self.assertEqual(content["poll_interval_seconds"], 3)
        self.assertEqual(read_mtime, mtime)

    def test_read_presence_missing_identity_returns_none_none(self):
        fake = FakeSftpClient()
        storage = _sftp_storage_with_fake(fake)

        content, mtime = storage.read_presence_with_mtime("nobody")

        self.assertIsNone(content)
        self.assertIsNone(mtime)

    def test_second_write_presence_advances_mtime(self):
        fake = FakeSftpClient()
        storage = _sftp_storage_with_fake(fake)

        first_mtime = storage.write_presence("A", {"poll_interval_seconds": 3})
        second_mtime = storage.write_presence("A", {"poll_interval_seconds": 3})

        self.assertGreater(second_mtime, first_mtime)


class RelayLogicTests(unittest.TestCase):
    def _relay_config(self, relay_root: str, identity: str, state_dir: str) -> dict:
        return {
            "relay_root": relay_root,
            "relay_identity": identity,
            "relay_state_file": str(Path(state_dir) / f"state-{identity}.json"),
        }

    # Presence/liveness - peer_liveness's actual logic (distance vs.
    # threshold) is tested with a stubbed storage.read_presence_with_mtime
    # returning exact, controllable mtimes, rather than real file mtimes -
    # avoids sleep-based timing entirely, keeps the state-machine behavior
    # (alive/stale/unknown, negative-distance handling, peer-reported
    # interval) precise and fast.

    def test_write_presence_sets_own_reference_mtime(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            self.assertIsNone(relay_a._own_presence_mtime)

            relay_a.write_presence()

            self.assertIsInstance(relay_a._own_presence_mtime, float)

    def test_peer_liveness_unknown_before_write_presence_ever_ran(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))

            self.assertEqual(relay_a.peer_liveness("B")["state"], "unknown")

    def test_peer_liveness_unknown_for_a_peer_with_no_presence_file(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a.write_presence()

            self.assertEqual(relay_a.peer_liveness("never-seen")["state"], "unknown")

    def test_peer_liveness_alive_within_threshold(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a._own_presence_mtime = 100.0
            relay_a.storage.read_presence_with_mtime = lambda peer_id: (
                {"poll_interval_seconds": 3}, 99.0,
            )

            result = relay_a.peer_liveness("B")

            self.assertEqual(result["state"], "alive")
            self.assertEqual(result["last_seen_seconds_ago"], 1.0)

    def test_peer_liveness_alive_when_peer_heartbeat_is_newer_than_ours(self):
        # Negative distance (their last heartbeat is more recent than our
        # own reference point) is not a failure mode - it just means
        # they're doing fine, possibly better than we are.
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a._own_presence_mtime = 100.0
            relay_a.storage.read_presence_with_mtime = lambda peer_id: (
                {"poll_interval_seconds": 3}, 105.0,
            )

            result = relay_a.peer_liveness("B")

            self.assertEqual(result["state"], "alive")
            self.assertEqual(result["last_seen_seconds_ago"], -5.0)

    def test_peer_liveness_stale_past_threshold(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a._own_presence_mtime = 1000.0
            relay_a.storage.read_presence_with_mtime = lambda peer_id: (
                {"poll_interval_seconds": 3}, 900.0,
            )

            result = relay_a.peer_liveness("B")

            self.assertEqual(result["state"], "stale")
            self.assertEqual(result["last_seen_seconds_ago"], 100.0)

    def test_peer_liveness_threshold_uses_peers_own_reported_interval(self):
        # A peer polling every 30s legitimately checks in less often than
        # one polling every 3s - the margin has to account for the slower
        # side's own cadence, not just ours, or it'd falsely flag them
        # stale between their own perfectly normal heartbeats.
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a.poll_interval_seconds = 3.0
            relay_a._own_presence_mtime = 100.0
            relay_a.storage.read_presence_with_mtime = lambda peer_id: (
                {"poll_interval_seconds": 30}, 75.0,  # distance 25s
            )

            result = relay_a.peer_liveness("B")

            # threshold = 2.0 * (3 + 30) = 66, distance 25 <= 66
            self.assertEqual(result["state"], "alive")
            self.assertEqual(result["peer_poll_interval_seconds"], 30.0)

    def test_known_peer_identities_derived_from_applied_bookkeeping(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a._state["applied"] = {
                "topic-1": {"B": "hash-1", "C": "hash-2"},
                "topic-2": {"B": "hash-3"},
            }

            self.assertEqual(relay_a.known_peer_identities(), ["B", "C"])

    def test_status_payload_includes_presence_for_known_peers(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a._state["applied"] = {"topic-1": {"B": "hash-1"}}
            relay_a._own_presence_mtime = 100.0
            relay_a.storage.read_presence_with_mtime = lambda peer_id: (
                {"poll_interval_seconds": 3}, 99.0,
            )

            payload = relay_a.status_payload()

            self.assertEqual(payload["presence"]["B"]["state"], "alive")

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
            # Own identity is published alongside owned boards (so a peer
            # can pick up later display-name/picture edits over relay too).
            self.assertIn(board.uuid, published)

            session_b = Session("addr-b")
            relay_b = RelayLogic(session_b, self._relay_config(relay_root, "B", state_dir))
            applied = relay_b.poll_and_apply()

            self.assertIn((board.uuid, "A"), applied)
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
            self.assertEqual(len(first), 2)  # board + own identity

            second = relay_a.publish_due_topics()

            self.assertEqual(second, [])

    def test_delete_topic_clears_storage_and_local_bookkeeping(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            kanban_a = KanbanLogic(session_a, {})
            board_uuid = kanban_a.create_board("Board").value
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a.publish_due_topics()
            self.assertIn(board_uuid, relay_a.status_payload()["topics"])

            result = relay_a.delete_topic(board_uuid)

            self.assertEqual(result.status, "ok")
            # status_payload always includes locally-owned boards regardless
            # of relay state (Part 3b's own diagnostic-visibility fix) - the
            # bookkeeping fields resetting to "never published" is the
            # actual thing delete_topic is responsible for clearing.
            topic_status = relay_a.status_payload()["topics"][board_uuid]
            self.assertIsNone(topic_status["published_hash"])
            self.assertEqual(topic_status["applied"], {})
            # Own identity topic remains published independently of the
            # deleted board.
            self.assertNotIn(board_uuid, relay_a.storage.list_topics())
            # kanban_logic's own board is untouched - deletion is storage
            # cleanup only, never an app-level decision about local content.
            self.assertIn(board_uuid, [b.uuid for b in kanban_a.boards()])

    def test_delete_topic_without_storage_configured_is_an_error(self):
        session_a = Session("addr-a")
        relay_a = RelayLogic(session_a, {"relay_identity": "A"})

        result = relay_a.delete_topic("some-topic")

        self.assertEqual(result.status, "error")

    def test_repolling_without_a_new_publish_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            KanbanLogic(session_a, {}).create_board("Board")
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a.publish_due_topics()
            session_b = Session("addr-b")
            relay_b = RelayLogic(session_b, self._relay_config(relay_root, "B", state_dir))
            first = relay_b.poll_and_apply()
            self.assertEqual(len(first), 2)  # board + A's identity

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

            identity_uuid = session_a.identity.uuid
            self.assertEqual(
                set(relay_a.relay_topic_uuids()), {first_uuid, second_uuid, identity_uuid}
            )
            published = relay_a.publish_due_topics()
            self.assertEqual(set(published), {first_uuid, second_uuid, identity_uuid})

    def test_identity_updates_after_initial_connect_still_propagate_over_relay(self):
        # Regression: identity used to reach a relay peer only once, inline
        # in the connect token at accept time (Session.
        # apply_peer_identity_snapshot) - relay itself never published the
        # identity topic, so a later display-name/picture edit had no way
        # to reach an already-connected peer. relay_topic_uuids() now
        # includes our own identity node, so ordinary publish/poll keeps it
        # current, same as any board.
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            session_a.set_identity("Ann", picture="", email="ann@example.com")
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a.publish_due_topics()

            session_b = Session("addr-b")
            relay_b = RelayLogic(session_b, self._relay_config(relay_root, "B", state_dir))
            relay_b.poll_and_apply()

            cached = session_b.get_cached_peer_subtree("relay:A", session_a.identity.uuid)
            self.assertIsNotNone(cached)
            self.assertEqual(cached.data["display_name"], "Ann")

            # A changes their name well after the initial exchange - no new
            # token, just the ordinary poll loop.
            session_a.set_identity("Annabelle", picture="", email="ann@example.com")
            relay_a.publish_due_topics()
            relay_b.poll_and_apply()

            cached = session_b.get_cached_peer_subtree("relay:A", session_a.identity.uuid)
            self.assertEqual(cached.data["display_name"], "Annabelle")

    def test_relay_inactive_without_relay_root_configured(self):
        session_a = Session("addr-a")
        KanbanLogic(session_a, {}).create_board("Board")
        relay_a = RelayLogic(session_a, {})

        self.assertIsNone(relay_a.storage)
        self.assertEqual(relay_a.publish_due_topics(), [])
        self.assertEqual(relay_a.poll_and_apply(), [])
        self.assertFalse(relay_a.status_payload()["configured"])
        self.assertIsNone(relay_a.channel_descriptor())

    def test_channel_descriptor_shape_when_configured(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))

            descriptor = relay_a.channel_descriptor()

            self.assertEqual(descriptor["type"], "relay")
            self.assertEqual(descriptor["version"], 1)
            self.assertEqual(descriptor["identity"], "A")
            self.assertEqual(descriptor["root"], str(relay_a.storage.root))

    def test_sftp_backend_selected_via_config(self):
        session_a = Session("addr-a")
        config = {
            "relay_backend": "sftp",
            "relay_identity": "A",
            "relay_sftp_host": "example.test",
            "relay_sftp_username": "u",
            "relay_sftp_root": "/relay",
            "relay_sftp_password": "secret",
        }

        relay_a = RelayLogic(session_a, config)

        self.assertIsInstance(relay_a.storage, SftpRelayStorage)
        self.assertEqual(relay_a.storage.host, "example.test")
        self.assertEqual(relay_a.storage.root, "/relay")

    def test_sftp_host_strips_accidental_url_scheme(self):
        # Regression: a host value copied from an FTP client's connection
        # string (e.g. "sftp://ftp.example.com") fails DNS resolution
        # outright if passed through as-is - getaddrinfo has no idea what
        # to do with the scheme prefix.
        session_a = Session("addr-a")
        config = {
            "relay_backend": "sftp", "relay_identity": "A",
            "relay_sftp_host": "sftp://ftp.example.com",
            "relay_sftp_username": "u",
        }

        relay_a = RelayLogic(session_a, config)

        self.assertEqual(relay_a.storage.host, "ftp.example.com")

    def test_sftp_password_resolves_from_env_var_when_not_in_config(self):
        session_a = Session("addr-a")
        config = {
            "relay_backend": "sftp", "relay_identity": "A",
            "relay_sftp_host": "example.test", "relay_sftp_username": "u",
        }
        with patch.dict(os.environ, {"SKANBAN_SFTP_PASSWORD": "from-env"}):
            relay_a = RelayLogic(session_a, config)

        self.assertEqual(relay_a.storage.password, "from-env")

    def test_sftp_password_resolves_from_file_when_no_config_or_env(self):
        session_a = Session("addr-a")
        with tempfile.TemporaryDirectory() as tmp:
            secret_path = Path(tmp) / "password.txt"
            secret_path.write_text("from-file\n", encoding="utf-8")
            config = {
                "relay_backend": "sftp", "relay_identity": "A",
                "relay_sftp_host": "example.test", "relay_sftp_username": "u",
                "relay_sftp_password_file": str(secret_path),
            }

            relay_a = RelayLogic(session_a, config)

        self.assertEqual(relay_a.storage.password, "from-file")

    def test_sftp_backend_without_host_is_unconfigured(self):
        session_a = Session("addr-a")
        config = {"relay_backend": "sftp", "relay_identity": "A"}

        relay_a = RelayLogic(session_a, config)

        self.assertIsNone(relay_a.storage)
        self.assertIsNone(relay_a.channel_descriptor())

    def test_sftp_channel_descriptor_includes_credentials(self):
        # Deliberate reversal of the old "never includes credentials" rule
        # (DESIGN §1.6): the descriptor now carries the SFTP username +
        # password so a pure accepter can build storage straight from the
        # token. Safe only because the relay account is chroot-jailed and
        # the token is a bearer credential over a trusted channel. The
        # private-key passphrase is still never embedded (we carry a
        # password, never a key).
        session_a = Session("addr-a")
        config = {
            "relay_backend": "sftp",
            "relay_identity": "A",
            "relay_sftp_host": "example.test",
            "relay_sftp_port": 2222,
            "relay_sftp_username": "u",
            "relay_sftp_root": "/relay",
            "relay_sftp_password": "super-secret",
            "relay_sftp_private_key_passphrase": "also-secret",
        }
        relay_a = RelayLogic(session_a, config)

        descriptor = relay_a.channel_descriptor()

        self.assertEqual(descriptor, {
            "type": "sftp", "version": 1, "host": "example.test",
            "port": 2222, "root": "/relay", "identity": "A",
            "username": "u", "password": "super-secret",
        })
        # The password IS present now (that's the point); the key passphrase
        # is not (no key is ever embedded).
        self.assertNotIn("also-secret", json.dumps(descriptor))

    def test_adopt_storage_from_descriptor_builds_sftp_when_none(self):
        # A pure accepter (no relay config) builds its single storage from
        # the token's advertised location + credentials.
        session_b = Session("addr-b")
        relay_b = RelayLogic(session_b, {})  # no storage configured
        self.assertIsNone(relay_b.storage)
        boot_state_path = relay_b._state_path

        adopted = relay_b.adopt_storage_from_descriptor({
            "type": "sftp", "version": 1, "host": "example.test",
            "port": 2222, "root": "/relay", "identity": "A",
            "username": "u", "password": "super-secret",
        })

        self.assertTrue(adopted)
        self.assertIsInstance(relay_b.storage, SftpRelayStorage)
        self.assertEqual(relay_b.storage.host, "example.test")
        self.assertEqual(relay_b.storage.port, 2222)
        self.assertEqual(relay_b.storage.username, "u")
        self.assertEqual(relay_b.storage.password, "super-secret")
        self.assertEqual(relay_b.storage.root, "/relay")
        # Bookkeeping re-keyed to the real location, not the empty-config
        # boot default.
        self.assertNotEqual(relay_b._state_path, boot_state_path)

    def test_adopt_storage_from_descriptor_builds_local_relay(self):
        session_b = Session("addr-b")
        relay_b = RelayLogic(session_b, {})
        with tempfile.TemporaryDirectory() as root:
            adopted = relay_b.adopt_storage_from_descriptor({
                "type": "relay", "version": 1, "root": root, "identity": "A",
            })
            self.assertTrue(adopted)
            self.assertIsInstance(relay_b.storage, LocalFolderRelayStorage)

    def test_adopt_storage_from_descriptor_noop_when_storage_exists(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_b = Session("addr-b")
            relay_b = RelayLogic(session_b, self._relay_config(relay_root, "B", state_dir))
            existing = relay_b.storage
            self.assertIsNotNone(existing)

            adopted = relay_b.adopt_storage_from_descriptor({
                "type": "sftp", "version": 1, "host": "other.test",
                "port": 22, "root": "/elsewhere", "identity": "A",
                "username": "u", "password": "p",
            })

            self.assertFalse(adopted)
            self.assertIs(relay_b.storage, existing)

    def test_adopt_storage_from_descriptor_rejects_malformed(self):
        session_b = Session("addr-b")
        relay_b = RelayLogic(session_b, {})

        self.assertFalse(relay_b.adopt_storage_from_descriptor({"type": "sftp"}))  # no host
        self.assertFalse(relay_b.adopt_storage_from_descriptor({"type": "carrier_pigeon"}))
        self.assertIsNone(relay_b.storage)

    def test_accepter_with_no_config_grafts_via_adopted_storage(self):
        # End-to-end: an inviter publishes to a local root; a fresh accepter
        # with NO storage of its own adopts the inviter's descriptor and
        # grafts the board - neither side shared a config file, only a token.
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            kanban_a = KanbanLogic(session_a, {})
            board_uuid = kanban_a.create_board("Shared Board").value
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a.publish_due_topics()
            descriptor = relay_a.channel_descriptor()

            session_b = Session("addr-b")
            relay_b = RelayLogic(session_b, {"relay_state_file": str(Path(state_dir) / "b.json")})
            self.assertIsNone(relay_b.storage)

            self.assertTrue(relay_b.adopt_storage_from_descriptor(descriptor))
            relay_b.mark_topics_desired([board_uuid])
            applied = relay_b.poll_and_apply()

            self.assertIn((board_uuid, "A"), applied)
            kanban_b = KanbanLogic(session_b, {})
            self.assertIn(board_uuid, [b.uuid for b in kanban_b.boards()])

    def test_module_channel_descriptor_hook_delegates_to_stashed_instance(self):
        # This is the shape app_server.py's collect_channel_descriptors
        # actually calls - a module-level function taking (runtime, config),
        # delegating to whichever instance create_logic already stashed.
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            config = self._relay_config(relay_root, "A", state_dir)
            relay_a = RelayLogic(session_a, config)
            config["_relay_logic_instance"] = relay_a

            descriptor = channel_descriptor(runtime=None, config=config)

            self.assertEqual(descriptor["type"], "relay")

        self.assertIsNone(channel_descriptor(runtime=None, config={}))

    def test_users_includes_relay_only_peer_via_peer_perspectives(self):
        # kanban_logic.users() used to only look at session.members, which
        # a relay-only peer ("relay:A") never joins - note_relay_peer_topic
        # deliberately keeps relay peers out of the live-connection
        # machinery (add_peer), so they'd never show up here at all without
        # also unioning in peer_perspectives, where their cached identity
        # (delivered inline via a connect token, same as the real
        # /api/connect flow) actually lives.
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            kanban_a = KanbanLogic(session_a, {})
            kanban_a.set_user_profile("Ann", "")
            board_uuid = kanban_a.create_board("Shared Board").value
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a.publish_due_topics()

            session_b = Session("addr-b")
            kanban_b = KanbanLogic(session_b, {})
            relay_b = RelayLogic(session_b, self._relay_config(relay_root, "B", state_dir))
            relay_b.mark_topics_desired([board_uuid])
            session_b.apply_peer_identity_snapshot("relay:A", kanban_a.user_profile().to_dict())
            relay_b.poll_and_apply()

            users = {user["address"]: user for user in kanban_b.users()}

            self.assertIn("relay:A", users)
            self.assertEqual(users["relay:A"]["name"], "Ann")

    def test_users_never_misattributes_an_ungrafted_peer_board_as_their_profile(self):
        # Regression: kanban_logic._peer_profile_uuid used to fall back to
        # "the first topic tracked for this peer that isn't a board I
        # recognize locally" whenever their real identity wasn't cached yet
        # - safe back when a peer's only ever-fetched topics were exactly
        # one board plus one profile (join_discussion's own accept-time
        # guarantee), but relay now tracks every topic a peer publishes via
        # peer_topic_sets regardless of whether this side grafted it. A
        # second board this side never desired has no entry in this side's
        # own protocol.index either, so it was wrongly treated as "not a
        # board, must be the profile" - handing back a peer's own board as
        # if it were their identity (with a blank name/picture, since a
        # board node has no display_name field).
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            kanban_a = KanbanLogic(session_a, {})
            desired_board = kanban_a.create_board("Board One").value
            other_board = kanban_a.create_board("Board Two").value
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            # Publish only the boards, not identity yet - simulates the
            # window before the peer's own identity has ever been polled.
            relay_a.storage.write_snapshot(
                desired_board, "A", session_a.node_state_hash(desired_board),
                session_a.get_subtree(desired_board),
            )
            relay_a.storage.write_snapshot(
                other_board, "A", session_a.node_state_hash(other_board),
                session_a.get_subtree(other_board),
            )

            session_b = Session("addr-b")
            kanban_b = KanbanLogic(session_b, {})
            relay_b = RelayLogic(session_b, self._relay_config(relay_root, "B", state_dir))
            relay_b.mark_topics_desired([desired_board])  # other_board never desired
            relay_b.poll_and_apply()

            users = {user["address"]: user for user in kanban_b.users()}

            self.assertIn("relay:A", users)
            self.assertNotEqual(users["relay:A"]["id"], other_board)
            self.assertEqual(users["relay:A"]["name"], "?")  # unknown, not mislabeled

    def test_mark_topics_desired_rejects_empty_list(self):
        session_a = Session("addr-a")
        relay_a = RelayLogic(session_a, {})

        result = relay_a.mark_topics_desired([])

        self.assertEqual(result.status, "error")

    def test_mark_topics_shared_records_and_persists(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            config = self._relay_config(relay_root, "A", state_dir)
            relay_a = RelayLogic(session_a, config)

            result = relay_a.mark_topics_shared(["board-1", "board-2"])

            self.assertEqual(result.status, "ok")
            self.assertEqual(relay_a._state["shared"], ["board-1", "board-2"])

            # Survives a reload on the same state file.
            reloaded = RelayLogic(Session("addr-a"), config)
            self.assertEqual(reloaded._state["shared"], ["board-1", "board-2"])

    def test_mark_topics_shared_rejects_empty_list(self):
        relay_a = RelayLogic(Session("addr-a"), {})
        self.assertEqual(relay_a.mark_topics_shared([]).status, "error")

    def test_has_active_relationship_gates_on_intent_or_peers(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session = Session("addr-a")
            config = self._relay_config(relay_root, "A", state_dir)

            relay = RelayLogic(session, config)
            self.assertFalse(relay.has_active_relationship())  # fresh: idle

            relay.mark_topics_shared(["board-1"])  # issued a token
            self.assertTrue(relay.has_active_relationship())

            relay2 = RelayLogic(Session("addr-b"), self._relay_config(relay_root, "B", state_dir))
            relay2.mark_topics_desired(["board-1"])  # accepted a token
            self.assertTrue(relay2.has_active_relationship())

            session3 = Session("addr-c")
            session3.note_relay_peer_topic("relay:D", "board-1")  # a relay peer exists
            relay3 = RelayLogic(session3, self._relay_config(relay_root, "C", state_dir))
            self.assertTrue(relay3.has_active_relationship())

    def test_has_active_relationship_false_without_storage(self):
        # No relay_root/host configured => storage None => nothing to do,
        # even if some intent leaked into state.
        relay = RelayLogic(Session("addr-a"), {})
        self.assertIsNone(relay.storage)
        relay._state["shared"] = ["board-1"]
        self.assertFalse(relay.has_active_relationship())

    def test_accept_connect_token_grafts_a_never_before_seen_board(self):
        # The scenario the token-accept path exists for: A and B have never
        # joined directly (no add_peer/handle_join at all), only relayed
        # through the shared folder plus a token exchanged out-of-band -
        # proving a board can be shared via relay alone.
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            kanban_a = KanbanLogic(session_a, {})
            board_uuid = kanban_a.create_board("Shared Board").value
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a.publish_due_topics()

            session_b = Session("addr-b")
            relay_b = RelayLogic(session_b, self._relay_config(relay_root, "B", state_dir))
            accept_result = relay_b.mark_topics_desired([board_uuid])
            self.assertEqual(accept_result.status, "ok")
            applied = relay_b.poll_and_apply()

            self.assertIn((board_uuid, "A"), applied)
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

            relay_b.mark_topics_desired([board_uuid])
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
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a.publish_due_topics()

            session_b = Session("addr-b")
            relay_b = RelayLogic(session_b, self._relay_config(relay_root, "B", state_dir))
            relay_b.mark_topics_desired([board_uuid])
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

    def test_poll_and_apply_skips_and_clears_a_peer_already_known_directly(self):
        # The ongoing poll loop runs independently of connect-time channel
        # selection (accept_connect_token), so without its own guard it
        # would keep re-registering a second, relay-sourced view of a peer
        # already reachable through a preferred (non-relay) channel - the
        # exact "two channels for one peer" state exclusive selection
        # exists to prevent. Redundancy is only detectable once relay:A's
        # identity has been seen at least once (populating the registry),
        # which can lag the first poll by one cycle depending on topic
        # iteration order - so the deterministic guarantee is the state
        # after two polls, not after the first.
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            kanban_a = KanbanLogic(session_a, {})
            kanban_a.set_user_profile("Ann", "")
            board_uuid = kanban_a.create_board("Shared Board").value
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a.publish_due_topics()

            session_b = Session("addr-b")
            # Ann is already a live direct peer (member + cached identity),
            # exactly as accept_connect_token's http path would have
            # registered her, before relay ever enters the picture.
            session_b.add_peer("http://addr-a-direct", board_uuid)
            session_b.apply_peer_identity_snapshot(
                "http://addr-a-direct", kanban_a.user_profile().to_dict(),
            )
            relay_b = RelayLogic(session_b, self._relay_config(relay_root, "B", state_dir))
            relay_b.mark_topics_desired([board_uuid])

            relay_b.poll_and_apply()
            relay_b.poll_and_apply()

            self.assertNotIn("relay:A", session_b.peer_topic_sets)
            self.assertNotIn("relay:A", session_b.peer_perspectives)
            # The identity fact itself is retained (knowledge, not
            # registration) - it's what keeps the address suppressed on
            # every later poll without any relay-side bookkeeping.
            self.assertEqual(
                session_b.peer_identity_key.get("relay:A"),
                kanban_a.user_profile().data["identity_key"],
            )

    def test_poll_and_apply_keeps_ignoring_a_redundant_peers_later_topics(self):
        # Regression, caught live: once a peer is confirmed redundant,
        # remove_peer wipes relay:<peer>'s cached content - under the old
        # content-walking check that erased the very evidence redundancy
        # detection needed, so a topic published *after* that point (e.g.
        # a board that only starts publishing a few polls later) looked
        # like a brand-new, never-proven-redundant peer and got freely
        # re-applied. The registry entry survives remove_peer, so the
        # suppression holds for late-arriving topics too.
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            kanban_a = KanbanLogic(session_a, {})
            kanban_a.set_user_profile("Ann", "")
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a.publish_due_topics()  # identity only so far, no board yet

            session_b = Session("addr-b")
            session_b.add_peer("http://addr-a-direct", "some-shared-topic")
            session_b.apply_peer_identity_snapshot(
                "http://addr-a-direct", kanban_a.user_profile().to_dict(),
            )
            relay_b = RelayLogic(session_b, self._relay_config(relay_root, "B", state_dir))

            relay_b.poll_and_apply()  # may apply identity (registry learns)
            relay_b.poll_and_apply()  # detects + removes it
            self.assertNotIn("relay:A", session_b.peer_topic_sets)

            # Ann only creates (and so only publishes) a board after that.
            board_uuid = kanban_a.create_board("Shared Board").value
            relay_a.publish_due_topics()
            relay_b.mark_topics_desired([board_uuid])
            relay_b.poll_and_apply()

            self.assertNotIn("relay:A", session_b.peer_topic_sets)
            self.assertNotIn("relay:A", session_b.peer_perspectives)

    def test_poll_and_apply_resumes_after_direct_peer_disconnects(self):
        # Redundancy is a live check, not a permanent verdict: it requires
        # the direct address to *currently* be a member. Once the direct
        # peer is gone (user disconnected), freshly published relay content
        # from that identity applies again on the next poll - under the
        # old persisted-verdict approach the suppression held forever,
        # even with no direct channel left.
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            kanban_a = KanbanLogic(session_a, {})
            kanban_a.set_user_profile("Ann", "")
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a.publish_due_topics()

            session_b = Session("addr-b")
            session_b.add_peer("http://addr-a-direct", "some-shared-topic")
            session_b.apply_peer_identity_snapshot(
                "http://addr-a-direct", kanban_a.user_profile().to_dict(),
            )
            relay_b = RelayLogic(session_b, self._relay_config(relay_root, "B", state_dir))

            relay_b.poll_and_apply()
            relay_b.poll_and_apply()
            self.assertNotIn("relay:A", session_b.peer_topic_sets)

            session_b.remove_peer("http://addr-a-direct")
            kanban_a.set_user_profile("Ann Renamed", "")
            relay_a.publish_due_topics()
            relay_b.poll_and_apply()

            self.assertIn("relay:A", session_b.peer_topic_sets)

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

    def test_default_state_file_differs_per_backend_not_just_per_identity(self):
        # Regression, found live: an SFTP-backed instance and a
        # local-folder-backed instance sharing the same relay_identity (a
        # very plausible thing to do - "A" is a natural identity to reuse
        # across setups) must not share bookkeeping. Otherwise switching
        # backends silently inherits stale "already published/applied"
        # state from a completely different, unrelated storage location -
        # publish_due_topics/poll_and_apply would then wrongly believe
        # they'd already synced against a server they'd never contacted.
        session_a = Session("addr-a")
        relay_local = RelayLogic(session_a, {
            "relay_backend": "local", "relay_root": "/some/local/path",
            "relay_identity": "A",
        })
        session_b = Session("addr-b")
        relay_sftp = RelayLogic(session_b, {
            "relay_backend": "sftp", "relay_identity": "A",
            "relay_sftp_host": "example.test", "relay_sftp_username": "u",
            "relay_sftp_root": "/relay",
        })

        self.assertNotEqual(relay_local._state_path, relay_sftp._state_path)

    def test_default_state_file_differs_per_sftp_root_not_just_per_host(self):
        session_a = Session("addr-a")
        base_config = {
            "relay_backend": "sftp", "relay_identity": "A",
            "relay_sftp_host": "example.test", "relay_sftp_username": "u",
        }
        relay_one = RelayLogic(session_a, {**base_config, "relay_sftp_root": "/relay-one"})
        session_b = Session("addr-b")
        relay_two = RelayLogic(session_b, {**base_config, "relay_sftp_root": "/relay-two"})

        self.assertNotEqual(relay_one._state_path, relay_two._state_path)


if __name__ == "__main__":
    unittest.main()

import hashlib
import json
import os
import stat as stat_module
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from sovereign.blob_store import BlobStore
from sovereign.channel import ChannelManager
from sovereign.mailbox_channel import MailboxChannel
from sovereign.profile import CoreProfileService
from s_kanban.logic import KanbanLogic as _KanbanLogic
from sovereign.protocol import ProtocolNode
from sovereign.relay_logic import (
    RelayLogic, RelayManager, RelayTiming, _relay_fingerprint,
)
from sovereign.relay_storage import LocalFolderRelayStorage, SftpRelayStorage
from sovereign.session import Session


def KanbanLogic(session, config, channel_manager=None):
    """Construct registered app logic without a full ApplicationHost."""
    logic = _KanbanLogic(session, config, channel_manager)
    session.register_application(logic.application_registration())
    return logic


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
    def test_blob_manifest_and_lease_round_trip(self):
        with tempfile.TemporaryDirectory() as root:
            storage = LocalFolderRelayStorage(root)
            data = b"avatar"
            blob_id = BlobStore(Path(root) / "local").write_blob(data)

            storage.write_blob_lease(blob_id, "A", {"expires_at": 123})
            storage.write_blob(blob_id, data)
            storage.write_snapshot(
                "topic-1", "A", "hash-1", {"subtree": {}, "parent_uuid": None},
                blob_ids={blob_id},
            )

            self.assertEqual(storage.read_blob(blob_id), data)
            self.assertEqual(storage.read_head("topic-1", "A")["blobs"], [blob_id])
            self.assertEqual(storage.list_blob_leases()[blob_id][0]["expires_at"], 123)
            storage.delete_blob_lease(blob_id, "A")
            self.assertEqual(storage.list_blob_leases().get(blob_id), [])

    def test_timing_probe_returns_mtime_and_leaves_no_file(self):
        with tempfile.TemporaryDirectory() as root:
            storage = LocalFolderRelayStorage(root)

            mtime, roundtrip = storage.timing_probe()

            self.assertIsInstance(mtime, float)
            self.assertGreaterEqual(roundtrip, 0.0)
            self.assertEqual(list(Path(root).glob(".sovereign-timing-*")), [])

    def test_write_then_read_round_trips_head_and_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            storage = LocalFolderRelayStorage(root)

            storage.write_snapshot("topic-1", "A", "hash-1", {
                "subtree": {"name": "x"},
                "parent_uuid": None,
                "_relay_publication_seq": 7,
                "_relay_ack_requested": True,
                "_relay_ack_publication_seq": 7,
                "_relay_observed_publications": {"B": 4},
            })

            head = storage.read_head("topic-1", "A")
            self.assertEqual(head["hash"], "hash-1")
            self.assertEqual(head["peer"], "A")
            self.assertEqual(head["publication_seq"], 7)
            self.assertTrue(head["ack_requested"])
            self.assertEqual(head["ack_publication_seq"], 7)
            self.assertEqual(head["observed_publications"], {"B": 4})
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

    def test_superseded_snapshots_are_pruned(self):
        # Review R-4: every published revision used to stay forever. Keep
        # the current head and its immediate predecessor (a lagging peer may
        # be mid-fetch of that one); drop anything older.
        with tempfile.TemporaryDirectory() as root:
            storage = LocalFolderRelayStorage(root)
            storage.write_snapshot("topic-1", "A", "hash-1", {"subtree": {"n": 1}, "parent_uuid": None})
            storage.write_snapshot("topic-1", "A", "hash-2", {"subtree": {"n": 2}, "parent_uuid": None})

            storage.write_snapshot("topic-1", "A", "hash-3", {"subtree": {"n": 3}, "parent_uuid": None})

            self.assertIsNone(storage.read_snapshot("topic-1", "A", "hash-1"))
            self.assertIsNotNone(storage.read_snapshot("topic-1", "A", "hash-2"))
            self.assertIsNotNone(storage.read_snapshot("topic-1", "A", "hash-3"))
            self.assertEqual(storage.read_head("topic-1", "A")["hash"], "hash-3")
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
    def test_blob_manifest_and_lease_round_trip(self):
        fake = FakeSftpClient()
        storage = _sftp_storage_with_fake(fake)
        data = b"avatar"
        blob_id = "sha256:" + hashlib.sha256(data).hexdigest()

        storage.write_blob_lease(blob_id, "A", {"expires_at": 123})
        storage.write_blob(blob_id, data)
        storage.write_snapshot(
            "topic-1", "A", "hash-1", {"subtree": {}, "parent_uuid": None},
            blob_ids={blob_id},
        )

        self.assertEqual(storage.read_blob(blob_id), data)
        self.assertEqual(storage.list_blob_ids(), [blob_id])
        self.assertEqual(storage.read_head("topic-1", "A")["blobs"], [blob_id])
        self.assertEqual(storage.list_blob_leases()[blob_id][0]["expires_at"], 123)
        storage.delete_blob_lease(blob_id, "A")
        self.assertEqual(storage.list_blob_leases().get(blob_id), [])

    def test_timing_probe_returns_server_mtime_and_cleans_up(self):
        fake = FakeSftpClient()
        storage = _sftp_storage_with_fake(fake)

        mtime, roundtrip = storage.timing_probe()

        self.assertIsInstance(mtime, float)
        self.assertGreaterEqual(roundtrip, 0.0)
        self.assertFalse(any(".sovereign-timing-" in path for path in fake.files))

    def test_verify_access_writes_and_removes_probe(self):
        fake = FakeSftpClient()
        storage = _sftp_storage_with_fake(fake)

        storage.verify_access()

        self.assertFalse(any(".sovereign-probe-" in path for path in fake.files))

    def test_authentication_failure_is_not_retried(self):
        import paramiko

        storage = SftpRelayStorage(
            host="example.test", username="u", remote_root="/relay",
        )
        attempts = []

        def fail_authentication():
            attempts.append(True)
            raise paramiko.AuthenticationException("bad credentials")

        storage._connect = fail_authentication

        with self.assertRaises(paramiko.AuthenticationException):
            storage.list_topics()

        self.assertEqual(len(attempts), 1)

    def test_operations_on_shared_client_are_serialized(self):
        storage = SftpRelayStorage(
            host="example.test", username="u", remote_root="/relay",
        )
        storage._sftp = object()
        first_inside = threading.Event()
        release_first = threading.Event()
        second_inside = threading.Event()
        call_count = 0
        count_lock = threading.Lock()

        def operation(client):
            nonlocal call_count
            with count_lock:
                call_count += 1
                call_number = call_count
            if call_number == 1:
                first_inside.set()
                release_first.wait(timeout=1)
            else:
                second_inside.set()

        first = threading.Thread(target=storage._with_retry, args=(operation,))
        second = threading.Thread(target=storage._with_retry, args=(operation,))
        first.start()
        self.assertTrue(first_inside.wait(timeout=1))
        second.start()

        self.assertFalse(second_inside.wait(timeout=0.05))
        release_first.set()
        first.join(timeout=1)
        second.join(timeout=1)
        self.assertTrue(second_inside.is_set())

    def test_write_then_read_round_trips_head_and_snapshot(self):
        fake = FakeSftpClient()
        storage = _sftp_storage_with_fake(fake)

        storage.write_snapshot("topic-1", "A", "hash-1", {
            "subtree": {"name": "x"},
            "parent_uuid": None,
            "_relay_publication_seq": 7,
            "_relay_ack_requested": True,
            "_relay_ack_publication_seq": 7,
            "_relay_observed_publications": {"B": 4},
        })

        head = storage.read_head("topic-1", "A")
        self.assertEqual(head["hash"], "hash-1")
        self.assertEqual(head["peer"], "A")
        self.assertEqual(head["publication_seq"], 7)
        self.assertTrue(head["ack_requested"])
        self.assertEqual(head["ack_publication_seq"], 7)
        self.assertEqual(head["observed_publications"], {"B": 4})
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

    def test_superseded_snapshots_are_pruned(self):
        # Review R-4, sftp side - same keep-current-and-previous rule.
        fake = FakeSftpClient()
        storage = _sftp_storage_with_fake(fake)
        storage.write_snapshot("topic-1", "A", "hash-1", {"subtree": {"n": 1}, "parent_uuid": None})
        storage.write_snapshot("topic-1", "A", "hash-2", {"subtree": {"n": 2}, "parent_uuid": None})

        storage.write_snapshot("topic-1", "A", "hash-3", {"subtree": {"n": 3}, "parent_uuid": None})

        self.assertIsNone(storage.read_snapshot("topic-1", "A", "hash-1"))
        self.assertIsNotNone(storage.read_snapshot("topic-1", "A", "hash-2"))
        self.assertIsNotNone(storage.read_snapshot("topic-1", "A", "hash-3"))
        self.assertEqual(storage.read_head("topic-1", "A")["hash"], "hash-3")

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


class RelayManagerTests(unittest.TestCase):
    def _relay_config(self, relay_root: str, identity: str, state_dir: str) -> dict:
        return {
            "relay_root": relay_root,
            "relay_identity": identity,
            "relay_state_file": str(Path(state_dir) / f"state-{identity}.json"),
        }

    def test_manager_holds_the_implicit_connection_keyed_by_fingerprint(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session = Session("addr-a")
            config = self._relay_config(relay_root, "A", state_dir)
            manager = RelayManager(session, config)

            self.assertEqual(len(manager.all_connections()), 1)
            self.assertIs(manager.all_connections()[0], manager.primary)
            self.assertIn(_relay_fingerprint(manager.primary.storage), manager.connections)
            # Back-compat shims delegate to the primary connection.
            self.assertIs(manager.storage, manager.primary.storage)

    def test_persisted_target_overrides_legacy_startup_credentials(self):
        session = Session("addr-a")
        session.app_metadata["relay_targets"] = {
            "saved-target": {
                "name": "Configured relay", "backend": "sftp",
                "host": "relay.example", "port": 22, "username": "user",
                "root": "/boards", "password": "old-password",
                "poll_interval_seconds": 30, "configured": True,
            },
        }
        config = {
            "relay_backend": "sftp", "relay_identity": "A",
            "relay_sftp_host": "relay.example", "relay_sftp_port": 22,
            "relay_sftp_username": "user", "relay_sftp_root": "/boards",
            "relay_sftp_password": "new-password",
            "relay_poll_interval_seconds": 4,
        }

        manager = RelayManager(session, config)

        self.assertEqual(manager.primary.storage.password, "old-password")
        self.assertEqual(manager.primary.poll_interval_seconds, 30)
        saved = session.app_metadata["relay_targets"]["saved-target"]
        self.assertEqual(saved["password"], "old-password")
        self.assertEqual(saved["poll_interval_seconds"], 30)
        self.assertNotIn("configured", saved)

    def test_persisted_password_replaces_legacy_startup_private_key(self):
        session = Session("addr-a")
        session.app_metadata["relay_targets"] = {
            "saved-target": {
                "name": "Configured relay", "backend": "sftp",
                "host": "relay.example", "port": 22, "username": "user",
                "root": "/boards", "password": "stale-password",
                "poll_interval_seconds": 3, "configured": True,
            },
        }
        manager = RelayManager(session, {
            "relay_backend": "sftp", "relay_identity": "A",
            "relay_sftp_host": "relay.example", "relay_sftp_port": 22,
            "relay_sftp_username": "user", "relay_sftp_root": "/boards",
            "relay_sftp_private_key_path": "current-key.pem",
        })

        self.assertIsNone(manager.primary.storage.private_key_path)
        self.assertEqual(manager.primary.storage.password, "stale-password")

    def test_accepting_same_startup_target_keeps_local_poll_interval(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session = Session("addr-a")
            board_uuid = KanbanLogic(session, {}).create_board("Board").value
            manager = RelayManager(session, {
                **self._relay_config(relay_root, "A", state_dir),
                "relay_poll_interval_seconds": 4,
            })

            result = manager.accept_descriptor({
                "type": "relay", "descriptor_version": 1, "root": relay_root,
                "identity": "B", "poll_interval_seconds": 30,
            }, [board_uuid])

            self.assertEqual(result.status, "ok")
            self.assertEqual(manager.primary.poll_interval_seconds, 4)
            saved = manager.list_targets()[0]
            self.assertEqual(saved["poll_interval_seconds"], 4)
            self.assertNotIn("configured", saved)

    def test_imported_startup_target_can_be_deleted_and_is_not_recreated(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session = Session("addr-a")
            config = self._relay_config(relay_root, "A", state_dir)
            manager = RelayManager(session, config)
            target_id = manager.list_targets()[0]["id"]

            deleted = manager.delete_target(target_id)
            restarted = RelayManager(session, config)

            self.assertEqual(deleted.status, "ok")
            self.assertEqual(restarted.list_targets(), [])

    def test_edit_target_keeps_saved_password_when_field_is_blank(self):
        session = Session("addr-a")
        manager = RelayManager(session, {})
        with patch.object(SftpRelayStorage, "verify_access", return_value=None):
            target_id = manager.create_target({
                "name": "Old", "backend": "sftp", "host": "relay.example",
                "username": "user", "password": "saved-password", "root": "/old",
            }).value
            result = manager.update_target(target_id, {
                "name": "New", "backend": "sftp", "host": "relay.example",
                "username": "user", "password": "", "root": "/new",
                "poll_interval_seconds": 7,
            })

        self.assertEqual(result.status, "ok")
        record = session.app_metadata["relay_targets"][target_id]
        self.assertEqual(record["name"], "New")
        self.assertEqual(record["password"], "saved-password")
        self.assertEqual(record["root"], "/new")
        self.assertEqual(record["poll_interval_seconds"], 7)

    def test_edit_target_location_preserves_board_intent(self):
        with tempfile.TemporaryDirectory() as root_a, tempfile.TemporaryDirectory() as root_b, tempfile.TemporaryDirectory() as state_dir:
            session = Session("addr-a")
            board_uuid = KanbanLogic(session, {}).create_board("Board").value
            manager = RelayManager(session, {"relay_state_directory": state_dir})
            target_id = manager.create_target({
                "name": "Old", "backend": "local", "root": root_a,
            }).value
            old_connection = manager.connection_for_target(target_id)
            manager.assign_topic_target(board_uuid, target_id)
            old_connection.mark_topics_desired([board_uuid])

            result = manager.update_target(target_id, {
                "name": "New", "backend": "local", "root": root_b,
            })
            new_connection = manager.connection_for_target(target_id)

            self.assertEqual(result.status, "ok")
            self.assertIsNot(new_connection, old_connection)
            self.assertEqual(manager.target_for_topic(board_uuid), target_id)
            self.assertIn(board_uuid, new_connection._state["shared"])
            self.assertIn(board_uuid, new_connection._state["desired"])
            self.assertIsNone(old_connection.storage)

    def test_same_location_collapses_to_one_connection(self):
        # Two storages pointed at the same root share a fingerprint, so the
        # manager would key them to one connection (natural dedup).
        with tempfile.TemporaryDirectory() as relay_root:
            a = LocalFolderRelayStorage(relay_root)
            b = LocalFolderRelayStorage(relay_root)
            self.assertEqual(_relay_fingerprint(a), _relay_fingerprint(b))

    def test_different_locations_have_distinct_fingerprints(self):
        with tempfile.TemporaryDirectory() as root_a, tempfile.TemporaryDirectory() as root_b:
            self.assertNotEqual(
                _relay_fingerprint(LocalFolderRelayStorage(root_a)),
                _relay_fingerprint(LocalFolderRelayStorage(root_b)),
            )

    def test_manager_peer_liveness_prefers_the_connection_that_knows_the_peer(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session = Session("addr-a")
            manager = RelayManager(session, self._relay_config(relay_root, "A", state_dir))
            manager.primary._own_presence_mtime = 100.0
            manager.primary.storage.read_presence_with_mtime = lambda peer_id: (
                {"poll_interval_seconds": 3}, 99.0,
            )
            self.assertEqual(manager.peer_liveness("B")["state"], "alive")
            self.assertEqual(manager.peer_liveness("never-seen")["state"], "alive")  # single conn answers
            # A peer no connection has a presence file for stays unknown.
            manager.primary.storage.read_presence_with_mtime = lambda peer_id: (None, None)
            self.assertEqual(manager.peer_liveness("ghost")["state"], "unknown")

    def test_manager_peer_liveness_prefers_alive_over_stale_across_targets(self):
        session = Session("addr-a")
        manager = RelayManager(session, {})
        stale = types.SimpleNamespace(peer_liveness=lambda _peer: {
            "state": "stale", "last_seen_seconds_ago": 600,
        })
        alive = types.SimpleNamespace(peer_liveness=lambda _peer: {
            "state": "alive", "last_seen_seconds_ago": 2,
        })
        manager.connections = {"old": stale, "current": alive}

        self.assertEqual(manager.peer_liveness("A")["state"], "alive")

    def test_registry_builds_distinct_connections_and_dedupes_same_fingerprint(self):
        with tempfile.TemporaryDirectory() as root_a, tempfile.TemporaryDirectory() as root_b, tempfile.TemporaryDirectory() as state_dir:
            session = Session("addr-a")
            manager = RelayManager(session, {"relay_state_directory": state_dir})

            target_a = manager.create_target({
                "name": "A", "backend": "local", "root": root_a,
            }).value
            target_b = manager.create_target({
                "name": "B", "backend": "local", "root": root_b,
            }).value
            target_a2 = manager.create_target({
                "name": "A duplicate", "backend": "local", "root": root_a,
            }).value

            configured = [conn for conn in manager.all_connections() if conn.storage]
            self.assertEqual(len(configured), 2)
            self.assertIs(manager.connection_for_target(target_a), manager.connection_for_target(target_a2))
            self.assertIsNot(manager.connection_for_target(target_a), manager.connection_for_target(target_b))

    def test_board_assignments_scope_each_connection_and_unassign_stops_publishing(self):
        with tempfile.TemporaryDirectory() as root_a, tempfile.TemporaryDirectory() as root_b, tempfile.TemporaryDirectory() as state_dir:
            session = Session("addr-a")
            kanban = KanbanLogic(session, {})
            board_a = kanban.create_board("A board").value
            board_b = kanban.create_board("B board").value
            manager = RelayManager(session, {"relay_state_directory": state_dir})
            target_a = manager.create_target({"name": "A", "backend": "local", "root": root_a}).value
            target_b = manager.create_target({"name": "B", "backend": "local", "root": root_b}).value

            manager.assign_topic_target(board_a, target_a)
            manager.assign_topic_target(board_b, target_b)

            identity = session.identity.uuid
            self.assertEqual(
                set(manager.connection_for_target(target_a).relay_topic_uuids()),
                {board_a, identity},
            )
            self.assertEqual(
                set(manager.connection_for_target(target_b).relay_topic_uuids()),
                {board_b, identity},
            )

            manager.connection_for_target(target_a).mark_topics_desired([board_a])
            manager.assign_topic_target(board_a, None)
            self.assertEqual(manager.connection_for_target(target_a).relay_topic_uuids(), [identity])
            self.assertNotIn(board_a, manager.connection_for_target(target_a)._state["desired"])

    def test_accepting_board_on_new_target_cleans_previous_target_intent(self):
        with tempfile.TemporaryDirectory() as root_a, tempfile.TemporaryDirectory() as root_b, tempfile.TemporaryDirectory() as state_dir:
            session = Session("addr-a")
            board_uuid = KanbanLogic(session, {}).create_board("Board").value
            manager = RelayManager(session, {"relay_state_directory": state_dir})
            target_a = manager.create_target({
                "name": "Old", "backend": "local", "root": root_a,
            }).value
            old_connection = manager.connection_for_target(target_a)
            manager.assign_topic_target(board_uuid, target_a)
            old_connection.mark_topics_desired([board_uuid])

            result = manager.accept_descriptor({
                "type": "relay", "descriptor_version": 1, "root": root_b,
                "identity": "B", "poll_interval_seconds": 3,
            }, [board_uuid, "profile-b"], "profile-b")

            self.assertEqual(result.status, "ok")
            self.assertNotEqual(manager.target_for_topic(board_uuid), target_a)
            self.assertNotIn(board_uuid, old_connection._state["shared"])
            self.assertNotIn(board_uuid, old_connection._state["desired"])

    def test_session_protocol_lock_is_shared_by_every_relay_connection(self):
        with tempfile.TemporaryDirectory() as root_a, tempfile.TemporaryDirectory() as root_b, tempfile.TemporaryDirectory() as state_dir:
            session = Session("addr-a")
            manager = RelayManager(session, {"relay_state_directory": state_dir})
            manager.create_target({"name": "A", "backend": "local", "root": root_a})
            manager.create_target({"name": "B", "backend": "local", "root": root_b})

            self.assertTrue(all(
                connection._session_lock is session.lock
                for connection in manager.all_connections()
            ))

    def test_target_registry_keeps_sftp_password_local_but_hides_it_from_listing(self):
        session = Session("addr-a")
        with tempfile.TemporaryDirectory() as state_dir:
            manager = RelayManager(session, {"relay_state_directory": state_dir})
            with patch.object(SftpRelayStorage, "verify_access", return_value=None):
                target_id = manager.create_target({
                    "name": "Company", "backend": "sftp", "host": "sftp.example",
                    "username": "kanban", "password": "secret", "root": "/boards",
                }).value

        self.assertEqual(session.app_metadata["relay_targets"][target_id]["password"], "secret")
        listed = next(item for item in manager.list_targets() if item["id"] == target_id)
        self.assertNotIn("password", listed)
        self.assertTrue(listed["has_password"])


class RelayLogicTests(unittest.TestCase):
    def test_relay_syncs_an_application_topic_without_knowing_the_application(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            folder_a = session_a.create_child(
                session_a.protocol.root.uuid, {"type": "agreement_folder"}, {},
            ).value
            agreement = session_a.create_child(
                folder_a.uuid, {"type": "agreement", "title": "Terms"}, {},
            ).value
            clause = session_a.create_child(
                agreement.uuid, {"type": "clause", "text": "First version"}, {},
            ).value
            session_a.shared_topics.register(
                "test-agreement",
                {"agreement"},
                lambda: [agreement.uuid],
                lambda tree: session_a.accept_topic_invitation(tree, folder_a.uuid),
            )
            relay_a = RelayLogic(
                session_a, self._relay_config(relay_root, "A", state_dir),
            )
            relay_a.publish_due_topics()

            session_b = Session("addr-b")
            folder_b = session_b.create_child(
                session_b.protocol.root.uuid, {"type": "agreement_folder"}, {},
            ).value
            session_b.shared_topics.register(
                "test-agreement",
                {"agreement"},
                lambda: [
                    child.uuid for child in session_b.protocol.index[folder_b.uuid].children
                    if child.data.get("type") == "agreement"
                ],
                lambda tree: session_b.accept_topic_invitation(tree, folder_b.uuid),
            )
            relay_b = RelayLogic(
                session_b, self._relay_config(relay_root, "B", state_dir),
            )
            relay_b.mark_topics_desired([agreement.uuid])

            self.assertIn((agreement.uuid, "A"), relay_b.poll_and_apply())
            self.assertEqual(
                session_b.protocol.index[agreement.uuid].parent_uuid, folder_b.uuid,
            )
            self.assertEqual(session_b.protocol.index[clause.uuid].data["text"], "First version")

            session_a.modify(
                clause.uuid, {"type": "clause", "text": "Revised by A"}, {},
            )
            relay_a.publish_due_topics()
            relay_b.poll_and_apply()

            events = session_b.analyze_peer_transitions("relay:A", agreement.uuid)
            clause_event = next(event for event in events if event["node_uuid"] == clause.uuid)
            self.assertNotEqual(clause_event["type"], "in_agreement")

    def test_desired_unknown_topic_mounts_after_its_application_registers(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            folder_a = session_a.create_child(
                session_a.protocol.root.uuid, {"type": "agreement_folder"}, {},
            ).value
            agreement = session_a.create_child(
                folder_a.uuid, {"type": "agreement", "title": "Terms"}, {},
            ).value
            session_a.shared_topics.register(
                "test-agreement", {"agreement"}, lambda: [agreement.uuid],
                lambda tree: session_a.accept_topic_invitation(tree, folder_a.uuid),
            )
            relay_a = RelayLogic(
                session_a, self._relay_config(relay_root, "A", state_dir),
            )
            relay_a.publish_due_topics()

            session_b = Session("addr-b")
            relay_b = RelayLogic(
                session_b, self._relay_config(relay_root, "B", state_dir),
            )
            relay_b.mark_topics_desired([agreement.uuid])
            relay_b.poll_and_apply()
            self.assertNotIn(agreement.uuid, session_b.protocol.index)
            self.assertIsNotNone(
                session_b.get_cached_peer_subtree("relay:A", agreement.uuid),
            )

            folder_b = session_b.create_child(
                session_b.protocol.root.uuid, {"type": "agreement_folder"}, {},
            ).value
            session_b.shared_topics.register(
                "test-agreement", {"agreement"}, lambda: [],
                lambda tree: session_b.accept_topic_invitation(tree, folder_b.uuid),
            )
            relay_b.poll_and_apply()

            self.assertEqual(
                session_b.protocol.index[agreement.uuid].parent_uuid, folder_b.uuid,
            )

    def test_profile_blob_is_published_before_head_and_cached_by_peer(self):
        with tempfile.TemporaryDirectory() as relay_root, \
                tempfile.TemporaryDirectory() as state_dir, \
                tempfile.TemporaryDirectory() as blobs_a, \
                tempfile.TemporaryDirectory() as blobs_b:
            session_a = Session("addr-a")
            store_a = BlobStore(blobs_a)
            data = b"GIF89a-avatar"
            blob_id = store_a.write_blob(data)
            CoreProfileService(session_a).set_avatar({
                "id": "avatar-1", "role": "avatar", "blob_id": blob_id,
                "name": "avatar.gif", "size": len(data), "mime": "image/gif",
            })
            config_a = self._relay_config(relay_root, "A", state_dir)
            relay_a = RelayLogic(session_a, config_a, blob_store=store_a)

            self.assertIn(session_a.identity.uuid, relay_a.publish_due_topics())
            head = relay_a.storage.read_head(session_a.identity.uuid, "A")
            self.assertEqual(head["blobs"], [blob_id])
            self.assertEqual(relay_a.storage.read_blob(blob_id), data)

            session_b = Session("addr-b")
            store_b = BlobStore(blobs_b)
            config_b = self._relay_config(relay_root, "B", state_dir)
            relay_b = RelayLogic(session_b, config_b, blob_store=store_b)
            relay_b.poll_and_apply()
            self.assertEqual(store_b.read_blob(blob_id), data)

    def test_relay_gc_requires_two_complete_unreferenced_scans(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            relay = RelayLogic(
                Session("addr-a"), self._relay_config(relay_root, "A", state_dir),
            )
            data = b"orphan"
            blob_id = BlobStore(Path(state_dir) / "source").write_blob(data)
            relay.storage.write_blob(blob_id, data)

            first = relay.blob_gc_report()
            second = relay.blob_gc_report()

            self.assertEqual(first["candidates"], [blob_id])
            self.assertEqual(first["collectible"], [])
            self.assertEqual(second["collectible"], [blob_id])
            self.assertEqual(relay.storage.read_blob(blob_id), data)

    def test_timing_model_schedules_after_peer_poll_and_relay_work(self):
        timing = RelayTiming(timestamp_resolution_seconds=0.0)
        timing.observe_server_clock(
            10.0, 100.0, 10.1, 100.1, 100.05, roundtrip_seconds=0.1,
        )
        timing.observe_cycle(0.4)
        timing.observe_peer_presence("B", 99.0, 3.0)

        delay = timing.response_check_delay(
            3.0, published_server_time=100.0, local_wall=100.1,
        )

        # B's next phase is relay time 102.0; allow its 0.4s cycle plus
        # 0.05s clock uncertainty before looking for the response.
        self.assertAlmostEqual(delay, 2.35, places=6)

    def test_timing_model_uses_stable_period_for_stale_peer(self):
        timing = RelayTiming(timestamp_resolution_seconds=0.0)
        timing.observe_server_clock(
            10.0, 100.0, 10.0, 100.0, 100.0, roundtrip_seconds=0.1,
        )
        timing.observe_peer_presence("offline", 50.0, 3.0)

        self.assertEqual(
            timing.response_check_delay(3.0, published_server_time=100.0, local_wall=100.0),
            3.0,
        )

    def test_calibrate_timing_exposes_diagnostics_without_probe_artifacts(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            relay = RelayLogic(
                Session("addr-a"), self._relay_config(relay_root, "A", state_dir),
            )

            timing = relay.calibrate_timing(3)

            self.assertTrue(timing["calibrated"])
            self.assertEqual(timing["samples"], 3)
            self.assertIsNotNone(timing["roundtrip_ms"])
            self.assertIsNotNone(timing["server_clock_offset_ms"])
            self.assertEqual(list(Path(relay_root).glob(".sovereign-timing-*")), [])

    def test_state_save_retries_transient_windows_replace_denial(self):
        with tempfile.TemporaryDirectory() as state_dir:
            state_path = Path(state_dir) / "relay-state.json"
            logic = RelayLogic(Session("addr-a"), {
                "relay_identity": "A",
                "relay_state_file": str(state_path),
            })
            logic._state["desired"] = ["topic-1"]
            real_replace = os.replace
            attempts = 0

            def transient_denial(source, destination):
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise PermissionError(5, "Access denied", str(destination))
                return real_replace(source, destination)

            with patch("sovereign.relay_logic.os.replace", side_effect=transient_denial), \
                    patch("sovereign.relay_logic.time.sleep"):
                logic._save_state()

            self.assertEqual(attempts, 3)
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["desired"], [
                "topic-1",
            ])
            self.assertEqual(list(Path(state_dir).glob("*.tmp")), [])

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

    def test_scoped_poll_ignores_unrelated_topic_on_same_storage_root(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            kanban_a = KanbanLogic(session_a, {})
            wanted = kanban_a.create_board("Wanted").value
            unrelated = kanban_a.create_board("Unrelated").value
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a.set_scoped_topics({wanted, unrelated})
            relay_a.publish_due_topics()

            session_b = Session("addr-b")
            relay_b = RelayLogic(session_b, self._relay_config(relay_root, "B", state_dir))
            relay_b.set_scoped_topics({wanted})
            relay_b.mark_topics_desired([wanted])

            applied = relay_b.poll_and_apply()

            self.assertIn((wanted, "A"), applied)
            self.assertFalse(any(topic == unrelated for topic, _peer in applied))
            self.assertIsNone(session_b.get_cached_peer_subtree("relay:A", unrelated))

    def test_scoped_connection_ignores_relay_peers_from_other_targets(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session = Session("addr-a")
            relay = RelayLogic(session, self._relay_config(relay_root, "A", state_dir))
            relay.set_scoped_topics({"board-current"})
            session.peer_topic_sets["relay:B"] = {"board-other"}

            self.assertFalse(relay.has_active_relationship())

            session.peer_topic_sets["relay:B"].add("board-current")
            self.assertTrue(relay.has_active_relationship())

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

    def test_publication_sequence_persists_and_advances_after_restart(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            kanban_a = KanbanLogic(session_a, {})
            board_uuid = kanban_a.create_board("Board").value
            config = self._relay_config(relay_root, "A", state_dir)
            relay_a = RelayLogic(session_a, config)

            relay_a.publish_due_topics()

            first_head = relay_a.storage.read_head(board_uuid, "A")
            self.assertEqual(first_head["publication_seq"], 1)
            self.assertTrue(first_head["ack_requested"])
            self.assertEqual(first_head["ack_publication_seq"], 1)
            state = json.loads(
                Path(relay_a._state_path).read_text(encoding="utf-8"),
            )
            self.assertEqual(state["publication_seq"][board_uuid], 1)

            restarted = RelayLogic(session_a, config)
            board = kanban_a.ensure_board()
            kanban_a.create_card(
                kanban_a.columns(board)[0].uuid, "After restart",
            )
            restarted.publish_due_topics()

            second_head = restarted.storage.read_head(board_uuid, "A")
            self.assertEqual(second_head["publication_seq"], 2)
            self.assertTrue(second_head["ack_requested"])
            self.assertEqual(second_head["ack_publication_seq"], 2)

    def test_publication_acknowledgement_is_sequenced_without_ack_loop(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            kanban_a = KanbanLogic(session_a, {})
            board_uuid = kanban_a.create_board("Shared").value
            relay_a = RelayLogic(
                session_a, self._relay_config(relay_root, "A", state_dir),
            )
            relay_a.mark_topics_shared([board_uuid])
            relay_a.publish_due_topics()
            self.assertEqual(
                relay_a.storage.read_head(board_uuid, "A")["publication_seq"],
                1,
            )

            session_b = Session("addr-b")
            KanbanLogic(session_b, {})
            relay_b = RelayLogic(
                session_b, self._relay_config(relay_root, "B", state_dir),
            )
            relay_b.mark_topics_desired([board_uuid])
            relay_b.poll_and_apply()
            relay_b.publish_due_topics()

            head_b = relay_b.storage.read_head(board_uuid, "B")
            self.assertEqual(head_b["observed_publications"]["A"], 1)
            self.assertTrue(head_b["ack_requested"])

            relay_a.poll_and_apply()
            self.assertEqual(
                relay_a._state["peer_observed_publications"][board_uuid]["B"],
                1,
            )
            self.assertIn(board_uuid, relay_a.publish_due_topics())
            head_a = relay_a.storage.read_head(board_uuid, "A")
            self.assertEqual(head_a["publication_seq"], 2)
            self.assertFalse(head_a["ack_requested"])
            self.assertEqual(head_a["ack_publication_seq"], 1)
            self.assertEqual(head_a["observed_publications"]["B"], 1)

            relay_b.poll_and_apply()

            self.assertEqual(relay_b.publish_due_topics(), [])

            # A late peer sees acknowledgement-only A#2, but it still
            # acknowledges A#1: the semantic publication represented by
            # that unchanged-hash head.
            session_c = Session("addr-c")
            KanbanLogic(session_c, {})
            relay_c = RelayLogic(
                session_c, self._relay_config(relay_root, "C", state_dir),
            )
            relay_c.mark_topics_desired([board_uuid])
            relay_c.poll_and_apply()
            relay_c.publish_due_topics()
            head_c = relay_c.storage.read_head(board_uuid, "C")
            self.assertEqual(head_c["observed_publications"]["A"], 1)

    def test_relay_acknowledgement_confirms_divergence_without_timer(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            kanban_a = KanbanLogic(session_a, {})
            board_uuid = kanban_a.create_board("Shared").value
            board_a = kanban_a.ensure_board()
            card = kanban_a.create_card(
                kanban_a.columns(board_a)[0].uuid, "Before", "", [],
            ).value
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a.mark_topics_shared([board_uuid])
            relay_a.publish_due_topics()

            session_b = Session("addr-b")
            kanban_b = KanbanLogic(session_b, {})
            relay_b = RelayLogic(session_b, self._relay_config(relay_root, "B", state_dir))
            relay_b.mark_topics_desired([board_uuid])
            relay_b.poll_and_apply()
            kanban_b.set_auto_adopt_mode("never")
            relay_b.publish_due_topics()
            relay_a.poll_and_apply()

            kanban_a.update_card(card.uuid, "After", "", [], None)
            relay_a.publish_due_topics()
            waiting = kanban_a.transition_by_node(kanban_a.transition_events(board_uuid))
            self.assertEqual(waiting[card.uuid]["type"], "in_transition")

            relay_b.poll_and_apply()
            # B's board did not change, but its acknowledgement did, so its
            # head must be republished immediately with the same snapshot.
            self.assertIn(board_uuid, relay_b.publish_due_topics())
            self.assertIn((board_uuid, "B"), relay_a.poll_and_apply())

            confirmed = kanban_a.transition_by_node(kanban_a.transition_events(board_uuid))
            self.assertEqual(confirmed[card.uuid]["type"], "divergence")

    def test_peer_observation_waits_for_matching_changed_snapshot(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            kanban_a = KanbanLogic(session_a, {})
            board_uuid = kanban_a.create_board("Shared").value
            card = kanban_a.create_card(
                kanban_a.columns(kanban_a.ensure_board())[0].uuid,
                "Before", "", [],
            ).value
            relay_a = RelayLogic(
                session_a, self._relay_config(relay_root, "A", state_dir),
            )
            relay_a.mark_topics_shared([board_uuid])
            relay_a.publish_due_topics()

            session_b = Session("addr-b")
            KanbanLogic(session_b, {})
            relay_b = RelayLogic(
                session_b, self._relay_config(relay_root, "B", state_dir),
            )
            relay_b.mark_topics_desired([board_uuid])
            relay_b.poll_and_apply()
            relay_b.publish_due_topics()
            relay_a.poll_and_apply()

            kanban_a.update_card(card.uuid, "After", "", [], None)
            relay_a.publish_due_topics()
            relay_b.poll_and_apply()
            session_b.reconcile_peer_changes("relay:A", board_uuid)
            relay_b.publish_due_topics()

            original_read_snapshot = relay_a.storage.read_snapshot
            relay_a.storage.read_snapshot = lambda *_args, **_kwargs: None
            try:
                relay_a.poll_and_apply()
            finally:
                relay_a.storage.read_snapshot = original_read_snapshot

            current_card = session_a.protocol.index[card.uuid]
            self.assertFalse(
                session_a.peer_observed_node("relay:B", current_card),
            )
            waiting = kanban_a.transition_by_node(
                kanban_a.transition_events(board_uuid),
            )
            self.assertEqual(waiting[card.uuid]["type"], "in_transition")

            relay_a.poll_and_apply()

            self.assertTrue(
                session_a.peer_observed_node("relay:B", current_card),
            )
            agreed = kanban_a.transition_by_node(
                kanban_a.transition_events(board_uuid),
            )
            self.assertEqual(agreed[card.uuid]["type"], "in_agreement")

    def test_stale_peer_suppresses_only_unconfirmed_transition(self):
        session = Session("addr-a")
        liveness = {"state": "alive"}
        channels = types.SimpleNamespace(
            peer_liveness_for_address=lambda _addr, _topic: dict(liveness),
        )
        kanban = KanbanLogic(session, {}, channels)
        board_uuid = kanban.create_board("Shared").value
        card = kanban.create_card(
            kanban.columns(kanban.ensure_board())[0].uuid,
            "Before", "", [],
        ).value
        peer_board = ProtocolNode.from_dict(
            session.protocol.index[board_uuid].to_dict(),
        )
        session.apply_peer_subtree("relay:B", peer_board, None)
        session.note_indirect_peer_topic("relay:B", board_uuid)
        session.note_peer_channel("relay:B", "mailbox")

        kanban.update_card(card.uuid, "After", "", [], None)
        alive = kanban.transition_by_node(
            kanban.transition_events(board_uuid),
        )
        self.assertEqual(alive[card.uuid]["type"], "in_transition")

        liveness["state"] = "stale"
        stale = kanban.transition_by_node(
            kanban.transition_events(board_uuid),
        )
        self.assertNotIn(card.uuid, stale)

        current_card = session.protocol.index[card.uuid]
        session.record_peer_observations(
            "relay:B",
            {card.uuid: session.node_revision(current_card)},
        )
        confirmed = kanban.transition_by_node(
            kanban.transition_events(board_uuid),
        )
        self.assertEqual(confirmed[card.uuid]["type"], "divergence")

        liveness["state"] = "alive"
        online_again = kanban.transition_by_node(
            kanban.transition_events(board_uuid),
        )
        self.assertEqual(online_again[card.uuid]["type"], "divergence")

    def test_channel_descriptor_carries_host_poll_interval(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            config = self._relay_config(relay_root, "A", state_dir)
            config["relay_poll_interval_seconds"] = 7.5
            relay = RelayLogic(Session("addr-a"), config)

            descriptor = relay.channel_descriptor()
            accepter = RelayLogic(
                Session("addr-b"), self._relay_config(relay_root, "B", state_dir),
            )
            accepter.adopt_poll_interval_from_descriptor(descriptor)

            self.assertEqual(descriptor["poll_interval_seconds"], 7.5)
            self.assertEqual(accepter.poll_interval_seconds, 7.5)

    def test_token_adopted_storage_and_interval_survive_restart(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session = Session("addr-b")
            config = {
                "relay_state_file": str(Path(state_dir) / "state-b.json"),
            }
            descriptor = {
                "type": "relay", "descriptor_version": 1, "root": relay_root,
                "identity": "A", "poll_interval_seconds": 8,
            }
            first = RelayLogic(session, config)
            self.assertTrue(first.adopt_storage_from_descriptor(descriptor))
            first.adopt_poll_interval_from_descriptor(descriptor)
            first.mark_topics_desired(["board-1"])

            restarted = RelayLogic(session, config)

            self.assertIsNotNone(restarted.storage)
            self.assertEqual(str(restarted.storage.root), relay_root)
            self.assertEqual(restarted.poll_interval_seconds, 8)
            self.assertEqual(restarted._state["desired"], ["board-1"])

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
            # Durable bookkeeping is persisted; `applied` deliberately is
            # not (it tracks the in-memory peer cache - see _save_state).
            self.assertIn("published", state)
            self.assertNotIn("applied", state)

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
            session_a.set_identity("Ann", picture="")
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
            session_a.set_identity("Annabelle", picture="")
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
            self.assertEqual(descriptor["descriptor_version"], 1)
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

    def test_sftp_password_does_not_resolve_from_environment(self):
        session_a = Session("addr-a")
        config = {
            "relay_backend": "sftp", "relay_identity": "A",
            "relay_sftp_host": "example.test", "relay_sftp_username": "u",
        }
        with patch.dict(os.environ, {"SKANBAN_SFTP_PASSWORD": "from-env"}):
            relay_a = RelayLogic(session_a, config)

        self.assertIsNone(relay_a.storage.password)

    def test_sftp_password_does_not_resolve_from_file(self):
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

        self.assertIsNone(relay_a.storage.password)

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
            "type": "sftp", "descriptor_version": 1, "host": "example.test",
            "port": 2222, "root": "/relay", "identity": "A",
            "username": "u", "password": "super-secret",
            "poll_interval_seconds": 3.0,
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
            "type": "sftp", "descriptor_version": 1, "host": "example.test",
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
                "type": "relay", "descriptor_version": 1, "root": root, "identity": "A",
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
                "type": "sftp", "descriptor_version": 1, "host": "other.test",
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

    def test_ensure_usable_storage_adopts_only_after_probe_succeeds(self):
        session_b = Session("addr-b")
        relay_b = RelayLogic(session_b, {})
        candidate = types.SimpleNamespace(
            root="x",
            verify_access=lambda: None,
        )

        with patch.object(relay_b, "_storage_from_descriptor", return_value=candidate):
            result = relay_b.ensure_usable_storage({"type": "relay", "root": "x"})

        self.assertEqual(result.status, "ok")
        self.assertIs(relay_b.storage, candidate)

    def test_ensure_usable_storage_failure_does_not_adopt_candidate(self):
        session_b = Session("addr-b")
        relay_b = RelayLogic(session_b, {})
        original_state_path = relay_b._state_path

        def fail_probe():
            raise PermissionError("denied")

        candidate = types.SimpleNamespace(root="x", verify_access=fail_probe)
        with patch.object(relay_b, "_storage_from_descriptor", return_value=candidate):
            result = relay_b.ensure_usable_storage({"type": "relay", "root": "x"})

        self.assertEqual(result.status, "error")
        self.assertIn("PermissionError: denied", result.reason)
        self.assertIsNone(relay_b.storage)
        self.assertEqual(relay_b._state_path, original_state_path)

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
            kanban_b = KanbanLogic(session_b, {})
            relay_b = RelayLogic(session_b, {"relay_state_file": str(Path(state_dir) / "b.json")})
            self.assertIsNone(relay_b.storage)

            self.assertTrue(relay_b.adopt_storage_from_descriptor(descriptor))
            relay_b.mark_topics_desired([board_uuid])
            applied = relay_b.poll_and_apply()

            self.assertIn((board_uuid, "A"), applied)
            self.assertIn(board_uuid, [b.uuid for b in kanban_b.boards()])

    def test_board_payload_attaches_relay_liveness_for_relay_peers(self):
        # Review U-7: relay peers have no http reachability signal, so the
        # UI needs the presence-mtime liveness attached per relay peer.
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session = Session("addr-a")
            config = self._relay_config(relay_root, "A", state_dir)
            manager = RelayManager(session, config)
            channels = ChannelManager(session)
            channels.register(MailboxChannel(manager))
            kanban = KanbanLogic(session, config, channels)
            relay = manager.primary
            # A relay peer with a cached perspective + a stubbed liveness.
            bob = ProtocolNode({"type": "kanban_board", "name": "Bob board"})
            bob.refresh_hashes()
            session.apply_peer_subtree("relay:B", bob, None)
            session.note_peer_channel("relay:B", "mailbox")
            relay._own_presence_mtime = 100.0
            relay.storage.read_presence_with_mtime = lambda peer_id: (
                {"poll_interval_seconds": 3}, 99.0,
            )

            payload = kanban.board_payload(auto_adopt=False)

            peer = payload["network"]["peers"]["relay:B"]
            self.assertIn("channel_liveness", peer)
            self.assertEqual(peer["channel_liveness"]["state"], "alive")

    def test_unmark_topics_shared_disarms_relay(self):
        # Review R-3: `shared` had no shrink path - unsharing a board never
        # stopped relay publishing it, and has_active_relationship() stayed
        # armed forever once anything had ever been shared.
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            kanban_a = KanbanLogic(session_a, {})
            board_uuid = kanban_a.create_board("Shared Board").value
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a.mark_topics_shared([board_uuid])
            self.assertTrue(relay_a.has_active_relationship())

            relay_a.unmark_topics_shared([board_uuid])

            self.assertEqual(relay_a._state["shared"], [])
            self.assertFalse(relay_a.has_active_relationship())

    def test_unshare_board_unmarks_relay_shared(self):
        # The kanban unshare hook: even with no peers yet (token issued,
        # never accepted), unsharing must unassign the board from its target
        # and stop relay publishing it.
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            config = self._relay_config(relay_root, "A", state_dir)
            manager = RelayManager(session_a, config)
            channels = ChannelManager(session_a)
            channels.register(MailboxChannel(manager))
            kanban_a = KanbanLogic(session_a, config, channels)
            board_uuid = kanban_a.create_board("Shared Board").value
            target_id = manager.list_targets()[0]["id"]
            manager.assign_topic_target(board_uuid, target_id)
            connection = manager.connection_for_target(target_id)
            self.assertIn(board_uuid, connection._state["shared"])

            result = kanban_a.unshare_board(board_uuid=board_uuid)

            self.assertEqual(result.status, "ok")
            self.assertEqual(connection._state["shared"], [])
            self.assertIsNone(manager.target_for_topic(board_uuid))

    def test_delete_topic_clears_shared_too(self):
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            kanban_a = KanbanLogic(session_a, {})
            board_uuid = kanban_a.create_board("Shared Board").value
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a.mark_topics_shared([board_uuid])
            relay_a.publish_due_topics()

            relay_a.delete_topic(board_uuid)

            self.assertEqual(relay_a._state["shared"], [])
            self.assertNotIn(board_uuid, relay_a._state["published"])

    def test_mark_topics_shared_activates_shared_board_for_auto_adopt(self):
        # Regression, caught live: over relay-only there's no /p2p/join to
        # mark the issuer's board an active discussion, so auto-adopt never
        # ran and incoming changes were stuck (a diff with no Adopt button).
        # Sharing a board must activate it.
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            kanban_a = KanbanLogic(session_a, {})
            board_uuid = kanban_a.create_board("Shared Board").value
            self.assertNotIn(board_uuid, session_a.active_topic_uuids)
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))

            relay_a.mark_topics_shared([board_uuid])

            self.assertIn(board_uuid, session_a.active_topic_uuids)

    def test_shared_boards_reactivated_on_construction(self):
        # A board shared in a prior run must come back active when a fresh
        # RelayLogic is constructed on the same state file (a restart), or
        # the issuer silently loses auto-adopt for it. active_topic_uuids
        # is persisted, but a board shared before this fix existed would not
        # have been recorded active - __init__ re-derives it from `shared`.
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            kanban_a = KanbanLogic(session_a, {})
            board_uuid = kanban_a.create_board("Shared Board").value
            config = self._relay_config(relay_root, "A", state_dir)
            RelayLogic(session_a, config).mark_topics_shared([board_uuid])

            # Simulate the state where activation was lost (board still in
            # the index, `shared` still persisted, but not marked active).
            session_a.active_topic_uuids.discard(board_uuid)
            self.assertNotIn(board_uuid, session_a.active_topic_uuids)

            RelayLogic(session_a, config)  # __init__ re-activates from `shared`

            self.assertIn(board_uuid, session_a.active_topic_uuids)

    def test_applied_bookkeeping_not_persisted_across_restart(self):
        # Regression, caught live: `applied` tracks what's been pulled into
        # peer_perspectives, but that cache is in-memory only. If `applied`
        # survived a restart, poll_and_apply would skip re-fetching an
        # unchanged peer topic while holding an empty cache - the peer
        # silently vanishes. A restart must re-fetch and re-cache.
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            kanban_a = KanbanLogic(session_a, {})
            board_uuid = kanban_a.create_board("Shared Board").value
            relay_a = RelayLogic(session_a, self._relay_config(relay_root, "A", state_dir))
            relay_a.publish_due_topics()

            session_b = Session("addr-b")
            config_b = self._relay_config(relay_root, "B", state_dir)
            relay_b = RelayLogic(session_b, config_b)
            relay_b.mark_topics_desired([board_uuid])
            # A publishes its board + its own identity, so both are applied.
            self.assertIn((board_uuid, "A"), relay_b.poll_and_apply())
            self.assertIsNotNone(session_b.peer_perspectives.get("relay:A"))

            # Restart B: fresh session (empty peer_perspectives) + fresh
            # RelayLogic on the same persisted state file. A published
            # nothing new.
            session_b2 = Session("addr-b")
            kanban_b2 = KanbanLogic(session_b2, {})
            relay_b2 = RelayLogic(session_b2, config_b)
            self.assertEqual(relay_b2._state["applied"], {})  # not restored

            applied = relay_b2.poll_and_apply()

            # Re-fetched despite the unchanged hash, repopulating the cache.
            self.assertIn((board_uuid, "A"), applied)
            self.assertIsNotNone(session_b2.peer_perspectives.get("relay:A"))

    def test_users_includes_relay_only_peer_via_peer_perspectives(self):
        # kanban_logic.users() used to only look at session.members, which
        # a relay-only peer ("relay:A") never joins - note_indirect_peer_topic
        # deliberately keeps relay peers out of the live-connection
        # machinery (add_peer), so they'd never show up here at all without
        # also unioning in peer_perspectives, where their cached identity
        # (delivered inline via a connect token, same as the real
        # /api/connect flow) actually lives.
        with tempfile.TemporaryDirectory() as relay_root, tempfile.TemporaryDirectory() as state_dir:
            session_a = Session("addr-a")
            kanban_a = KanbanLogic(session_a, {})
            session_a.set_identity("Ann", "")
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

    def test_users_keeps_every_unresolved_peer_visible(self):
        # Review K-6: users() deduped on `id`, and unresolved identities all
        # share id "" - with two unresolved peers, the second vanished from
        # the list entirely.
        session = Session("addr-a")
        kanban = KanbanLogic(session, {})
        # Two peers whose content is cached but whose identity isn't - a
        # board subtree carries no identity_key, so both resolve to id "".
        board_b = ProtocolNode({"type": "kanban_board", "name": "B board"})
        board_b.refresh_hashes()
        session.apply_peer_subtree("relay:B", board_b, None)
        board_c = ProtocolNode({"type": "kanban_board", "name": "C board"})
        board_c.refresh_hashes()
        session.apply_peer_subtree("relay:C", board_c, None)

        users = {user["address"] for user in kanban.users()}

        self.assertIn("relay:B", users)
        self.assertIn("relay:C", users)

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
            session3.note_indirect_peer_topic("relay:D", "board-1")  # an indirect peer exists
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
            kanban_b = KanbanLogic(session_b, {})
            relay_b = RelayLogic(session_b, self._relay_config(relay_root, "B", state_dir))
            accept_result = relay_b.mark_topics_desired([board_uuid])
            self.assertEqual(accept_result.status, "ok")
            applied = relay_b.poll_and_apply()

            self.assertIn((board_uuid, "A"), applied)
            self.assertIn(board_uuid, [b.uuid for b in kanban_b.boards()])
            self.assertEqual(kanban_b.auto_adopt_mode(session_b.protocol.index[board_uuid]), "always")

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
            kanban_b = KanbanLogic(session_b, {})
            relay_b = RelayLogic(session_b, self._relay_config(relay_root, "B", state_dir))
            relay_b.poll_and_apply()  # caches it before any token exists
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
            kanban_b = KanbanLogic(session_b, {})
            relay_b = RelayLogic(session_b, self._relay_config(relay_root, "B", state_dir))
            relay_b.mark_topics_desired([board_uuid])
            relay_b.poll_and_apply()

            card = kanban_a.create_card(todo.uuid, "Later Card").value
            relay_a.publish_due_topics()
            applied = relay_b.poll_and_apply()

            self.assertEqual(applied, [(board_uuid, "A")])
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
            session_a.set_identity("Ann", "")
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
            session_a.set_identity("Ann", "")
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
            session_a.set_identity("Ann", "")
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
            session_a.set_identity("Ann Renamed", "")
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

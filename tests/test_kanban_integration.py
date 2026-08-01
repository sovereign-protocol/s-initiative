from contextlib import contextmanager
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def temporary_directory():
    path = Path(tempfile.mkdtemp())
    try:
        yield str(path)
    finally:
        for attempt in range(20):
            try:
                shutil.rmtree(path)
                break
            except FileNotFoundError:
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.1)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(method: str, url: str, payload: dict | None = None,
                 timeout: float = 5.0) -> dict:
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_server(port: int) -> None:
    deadline = time.monotonic() + 10
    last_error = None
    while time.monotonic() < deadline:
        try:
            request_json("GET", f"http://127.0.0.1:{port}/api/initiative/board")
            return
        except Exception as error:
            last_error = error
            time.sleep(0.1)
    raise RuntimeError(f"server on {port} did not start: {last_error}")


def add_relay(port: int, relay_root: str) -> str:
    """Give a live server a relay target, as Manage channels would."""
    created = request_json(
        "POST",
        f"http://127.0.0.1:{port}/api/core/channels",
        {
            "kind": "mailbox", "type": "local_relay",
            "name": f"relay {port}", "root": relay_root,
        },
        timeout=20,
    )
    # The channel row's ref, which is how every later call names it.
    return f"mailbox:{created['value']}"


def connect_over_relay(host_port: int, guest_port: int, board_uuid: str,
                       host_channel: str) -> dict:
    """Connect two live servers through Core's collaboration API: the host
    uses its relay for the board, composes an invitation, the guest accepts.
    """
    used = request_json(
        "POST",
        f"http://127.0.0.1:{host_port}/api/core/topics/{board_uuid}/channels",
        {"channel_ref": host_channel, "action": "use"},
        timeout=20,
    )
    assert used.get("status") != "error", used
    token = request_json(
        "POST",
        f"http://127.0.0.1:{host_port}/api/core/invitations",
        {"topic_uuid": board_uuid, "channel_ref": host_channel},
        timeout=20,
    )
    return request_json(
        "POST",
        f"http://127.0.0.1:{guest_port}/api/core/invitations/accept",
        {"token": token},
        timeout=20,
    )


def card_names(board: dict) -> list[str]:
    names = []
    for column in board["children"]:
        for card in column["children"]:
            names.append(card["data"]["name"])
    return names


def find_card_parent(board: dict, card_name: str) -> str | None:
    if board.get("data", {}).get("type") == "kanban_column":
        for card in board["children"]:
            if card.get("data", {}).get("name") == card_name:
                return board["uuid"]
    for child in board.get("children", []):
        found = find_card_parent(child, card_name)
        if found:
            return found
    return None


class ServerIntegrationTests(unittest.TestCase):
    def test_kanban_invite_and_sync(self):
        port_a = free_port()
        port_b = free_port()
        processes = []
        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            configs = []
            for port in (port_a, port_b):
                config = {
                    "app_module": "s_initiative.application",
                    "ui_file": "initiative.html",
                    "css_file": "initiative.css",
                    "storage_file": str(tmp_path / f"kanban_{port}.json"),
                    "debug": True,
                }
                config_path = tmp_path / f"config_{port}.json"
                config_path.write_text(json.dumps(config), encoding="utf-8")
                configs.append(config_path)

            for port, config_path in ((port_a, configs[0]), (port_b, configs[1])):
                processes.append(subprocess.Popen(
                    [sys.executable, "app_server.py", f"{port}:initiative", str(config_path)],
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                ))

            try:
                wait_for_server(port_a)
                wait_for_server(port_b)
                relay_root = str(tmp_path / "relay")
                Path(relay_root).mkdir()
                channel_a = add_relay(port_a, relay_root)
                add_relay(port_b, relay_root)

                board_a = request_json(
                    "GET", f"http://127.0.0.1:{port_a}/api/initiative/board"
                )["board"]
                column_uuid = board_a["children"][0]["uuid"]
                request_json(
                    "POST",
                    f"http://127.0.0.1:{port_a}/api/initiative/cards/create",
                    {
                        "column_uuid": column_uuid,
                        "name": "Synced Card",
                        "description": "",
                        "participants": [],
                    },
                )

                share = connect_over_relay(
                    port_a, port_b, board_a["uuid"], channel_a,
                )
                self.assertEqual(share["status"], "ok")

                deadline = time.monotonic() + 10
                final_a = final_b = None
                while time.monotonic() < deadline:
                    final_a = request_json(
                        "GET", f"http://127.0.0.1:{port_a}/api/initiative/board"
                    )
                    final_b = request_json(
                        "GET", f"http://127.0.0.1:{port_b}/api/initiative/board"
                    )
                    if "Synced Card" in card_names(final_b["board"]):
                        break
                    time.sleep(0.2)

                self.assertIn("Synced Card", card_names(final_a["board"]))
                self.assertIn("Synced Card", card_names(final_b["board"]))
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    final_a = request_json(
                        "GET", f"http://127.0.0.1:{port_a}/api/initiative/board"
                    )
                    final_b = request_json(
                        "GET", f"http://127.0.0.1:{port_b}/api/initiative/board"
                    )
                    if (
                        final_a["network"]["peer_addresses"]
                        and final_b["network"]["peer_addresses"]
                    ):
                        break
                    time.sleep(0.2)
                self.assertEqual(len(final_a["network"]["peer_addresses"]), 1)
                self.assertEqual(len(final_b["network"]["peer_addresses"]), 1)
            finally:
                for process in processes:
                    process.terminate()
                for process in processes:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    if process.stdout:
                        process.stdout.close()
                    if process.stderr:
                        process.stderr.close()

    def test_local_card_is_not_rolled_back_by_stale_peer_view(self):
        port_a = free_port()
        port_b = free_port()
        processes = []
        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            configs = []
            for port in (port_a, port_b):
                config = {
                    "app_module": "s_initiative.application",
                    "ui_file": "initiative.html",
                    "css_file": "initiative.css",
                    "storage_file": str(tmp_path / f"kanban_{port}.json"),
                    "debug": True,
                }
                config_path = tmp_path / f"config_{port}.json"
                config_path.write_text(json.dumps(config), encoding="utf-8")
                configs.append(config_path)

            for port, config_path in ((port_a, configs[0]), (port_b, configs[1])):
                processes.append(subprocess.Popen(
                    [sys.executable, "app_server.py", f"{port}:initiative", str(config_path)],
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                ))

            try:
                wait_for_server(port_a)
                wait_for_server(port_b)
                relay_root = str(tmp_path / "relay")
                Path(relay_root).mkdir()
                channel_a = add_relay(port_a, relay_root)
                add_relay(port_b, relay_root)
                board_a = request_json(
                    "GET", f"http://127.0.0.1:{port_a}/api/initiative/board"
                )["board"]
                share = connect_over_relay(
                    port_a, port_b, board_a["uuid"], channel_a,
                )
                self.assertEqual(share["status"], "ok")
                request_json(
                    "POST",
                    f"http://127.0.0.1:{port_b}/api/initiative/auto_adopt",
                    {"enabled": True},
                )
                column_uuid = board_a["children"][0]["uuid"]
                request_json(
                    "POST",
                    f"http://127.0.0.1:{port_a}/api/initiative/cards/create",
                    {
                        "column_uuid": column_uuid,
                        "name": "Local Card",
                        "description": "",
                        "participants": [],
                    },
                )

                deadline = time.monotonic() + 10
                final_a = final_b = None
                while time.monotonic() < deadline:
                    final_a = request_json(
                        "GET", f"http://127.0.0.1:{port_a}/api/initiative/board"
                    )
                    final_b = request_json(
                        "GET", f"http://127.0.0.1:{port_b}/api/initiative/board"
                    )
                    if "Local Card" in card_names(final_b["board"]):
                        break
                    time.sleep(0.2)

                self.assertIn("Local Card", card_names(final_a["board"]))
                self.assertIn("Local Card", card_names(final_b["board"]))
            finally:
                for process in processes:
                    process.terminate()
                for process in processes:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    if process.stdout:
                        process.stdout.close()
                    if process.stderr:
                        process.stderr.close()

    def test_connected_card_move_syncs_with_auto_adopt(self):
        port_a = free_port()
        port_b = free_port()
        processes = []
        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            configs = []
            for port in (port_a, port_b):
                config = {
                    "app_module": "s_initiative.application",
                    "ui_file": "initiative.html",
                    "css_file": "initiative.css",
                    "storage_file": str(tmp_path / f"kanban_{port}.json"),
                    "debug": True,
                }
                config_path = tmp_path / f"config_{port}.json"
                config_path.write_text(json.dumps(config), encoding="utf-8")
                configs.append(config_path)

            for port, config_path in ((port_a, configs[0]), (port_b, configs[1])):
                processes.append(subprocess.Popen(
                    [sys.executable, "app_server.py", f"{port}:initiative", str(config_path)],
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                ))

            try:
                wait_for_server(port_a)
                wait_for_server(port_b)
                relay_root = str(tmp_path / "relay")
                Path(relay_root).mkdir()
                channel_a = add_relay(port_a, relay_root)
                add_relay(port_b, relay_root)
                board_a = request_json(
                    "GET", f"http://127.0.0.1:{port_a}/api/initiative/board"
                )["board"]
                share = connect_over_relay(
                    port_a, port_b, board_a["uuid"], channel_a,
                )
                self.assertEqual(share["status"], "ok")
                request_json(
                    "POST",
                    f"http://127.0.0.1:{port_b}/api/initiative/auto_adopt",
                    {"enabled": True},
                )
                source_uuid = board_a["children"][0]["uuid"]
                target_uuid = board_a["children"][1]["uuid"]
                create = request_json(
                    "POST",
                    f"http://127.0.0.1:{port_a}/api/initiative/cards/create",
                    {
                        "column_uuid": source_uuid,
                        "name": "Moved Card",
                        "description": "",
                        "participants": [],
                    },
                )
                card_uuid = create["value"]["uuid"]
                # A relay carries the card on its own poll, so wait for it to
                # arrive before moving it - the point of the test is the move,
                # not how quickly the first version got there.
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    raw_b = request_json(
                        "GET", f"http://127.0.0.1:{port_b}/api/protocol"
                    )
                    if find_card_parent(raw_b, "Moved Card") == source_uuid:
                        break
                    time.sleep(0.2)
                self.assertEqual(find_card_parent(raw_b, "Moved Card"), source_uuid)
                request_json(
                    "POST",
                    f"http://127.0.0.1:{port_a}/api/initiative/cards/move",
                    {
                        "card_uuid": card_uuid,
                        "column_uuid": target_uuid,
                        "index": 0,
                    },
                )
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    raw_b = request_json(
                        "GET", f"http://127.0.0.1:{port_b}/api/protocol"
                    )
                    if find_card_parent(raw_b, "Moved Card") == target_uuid:
                        break
                    time.sleep(0.2)
                self.assertEqual(find_card_parent(raw_b, "Moved Card"), target_uuid)

                deadline = time.monotonic() + 10
                final_b = None
                while time.monotonic() < deadline:
                    final_b = request_json(
                        "GET", f"http://127.0.0.1:{port_b}/api/initiative/board"
                    )
                    if find_card_parent(final_b["board"], "Moved Card") == target_uuid:
                        break
                    time.sleep(0.2)

                self.assertEqual(
                    find_card_parent(final_b["board"], "Moved Card"),
                    target_uuid,
                )

                def peer_view(payload):
                    # Keyed by B's publication identity, not its URL: over a
                    # relay a peer is who it publishes as.
                    for addr, tree in (payload.get("peers") or {}).items():
                        if addr.startswith("relay:"):
                            return tree
                    return None

                deadline = time.monotonic() + 20
                final_a = None
                while time.monotonic() < deadline:
                    final_a = request_json(
                        "GET", f"http://127.0.0.1:{port_a}/api/initiative/board"
                    )
                    peer = peer_view(final_a)
                    if peer and find_card_parent(peer, "Moved Card") == target_uuid:
                        break
                    time.sleep(0.2)

                peer = peer_view(final_a)
                self.assertEqual(find_card_parent(peer, "Moved Card"), target_uuid)
            finally:
                for process in processes:
                    process.terminate()
                for process in processes:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    if process.stdout:
                        process.stdout.close()
                    if process.stderr:
                        process.stderr.close()

    def test_hub_joining_two_unrelated_boards_does_not_cross_introduce_peers(self):
        # Reproduces a real reported bug: A and C each have their own,
        # unrelated board, on their own relay. B accepts an invitation from
        # each, as two separate actions. A and C never share a relay and
        # never share a topic - so neither should ever learn about the
        # other, even though B legitimately knows both.
        port_a = free_port()
        port_b = free_port()
        port_c = free_port()
        processes = []
        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            configs = {}
            for port in (port_a, port_b, port_c):
                config = {
                    "app_module": "s_initiative.application",
                    "ui_file": "initiative.html",
                    "css_file": "initiative.css",
                    "storage_file": str(tmp_path / f"kanban_{port}.json"),
                    "debug": True,
                }
                config_path = tmp_path / f"config_{port}.json"
                config_path.write_text(json.dumps(config), encoding="utf-8")
                configs[port] = config_path

            for port in (port_a, port_b, port_c):
                processes.append(subprocess.Popen(
                    [sys.executable, "app_server.py", f"{port}:initiative", str(configs[port])],
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                ))

            try:
                wait_for_server(port_a)
                wait_for_server(port_b)
                wait_for_server(port_c)
                relay_a = str(tmp_path / "relay-a")
                relay_c = str(tmp_path / "relay-c")
                Path(relay_a).mkdir()
                Path(relay_c).mkdir()
                channel_a = add_relay(port_a, relay_a)
                channel_c = add_relay(port_c, relay_c)

                board_a = request_json(
                    "GET", f"http://127.0.0.1:{port_a}/api/initiative/board"
                )["board"]
                board_c = request_json(
                    "GET", f"http://127.0.0.1:{port_c}/api/initiative/board"
                )["board"]

                join_a = connect_over_relay(
                    port_a, port_b, board_a["uuid"], channel_a,
                )
                self.assertEqual(join_a["status"], "ok")
                join_c = connect_over_relay(
                    port_c, port_b, board_c["uuid"], channel_c,
                )
                self.assertEqual(join_c["status"], "ok")

                time.sleep(2.0)

                final_a = request_json(
                    "GET", f"http://127.0.0.1:{port_a}/api/initiative/board"
                )
                final_c = request_json(
                    "GET", f"http://127.0.0.1:{port_c}/api/initiative/board"
                )

                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    final_a = request_json(
                        "GET", f"http://127.0.0.1:{port_a}/api/initiative/board"
                    )
                    final_c = request_json(
                        "GET", f"http://127.0.0.1:{port_c}/api/initiative/board"
                    )
                    if (
                        final_a["network"]["peer_addresses"]
                        and final_c["network"]["peer_addresses"]
                    ):
                        break
                    time.sleep(0.2)

                # One peer each, and it is B in both cases: A and C have no
                # relay and no topic in common, so neither can see the other.
                self.assertEqual(len(final_a["network"]["peer_addresses"]), 1)
                self.assertEqual(len(final_c["network"]["peer_addresses"]), 1)
                self.assertNotIn(
                    board_c["uuid"], final_a["board"].get("children", []),
                )
                self.assertEqual(
                    final_a["network"]["peer_addresses"],
                    final_c["network"]["peer_addresses"],
                )
            finally:
                for process in processes:
                    process.terminate()
                for process in processes:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    if process.stdout:
                        process.stdout.close()
                    if process.stderr:
                        process.stderr.close()


if __name__ == "__main__":
    unittest.main()

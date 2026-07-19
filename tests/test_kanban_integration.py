import json
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
            request_json("GET", f"http://127.0.0.1:{port}/api/kanban/board")
            return
        except Exception as error:
            last_error = error
            time.sleep(0.1)
    raise RuntimeError(f"server on {port} did not start: {last_error}")


def connect_over_http(host_port: int, guest_port: int, board_uuid: str) -> dict:
    """Connect two live servers the way the UI does: fetch a connect token
    from the host and hand it to the guest's /api/connect.

    Replaces the removed address-based /api/kanban/invite +
    /api/kanban/boards/share endpoints, which had no UI caller left.
    """
    token = request_json(
        "POST",
        f"http://127.0.0.1:{host_port}/api/connect_token",
        {"topic_uuids": [board_uuid]},
        timeout=20,
    )
    return request_json(
        "POST",
        f"http://127.0.0.1:{guest_port}/api/connect",
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
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            configs = []
            for port in (port_a, port_b):
                config = {
                    "app_module": "kanban_logic",
                    "ui_file": "kanban.html",
                    "css_file": "kanban.css",
                    "storage_file": str(tmp_path / f"kanban_{port}.json"),
                    "debug": True,
                }
                config_path = tmp_path / f"config_{port}.json"
                config_path.write_text(json.dumps(config), encoding="utf-8")
                configs.append(config_path)

            for port, config_path in ((port_a, configs[0]), (port_b, configs[1])):
                processes.append(subprocess.Popen(
                    [sys.executable, "app_server.py", f"{port}:kanban", str(config_path)],
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                ))

            try:
                wait_for_server(port_a)
                wait_for_server(port_b)

                board_a = request_json(
                    "GET", f"http://127.0.0.1:{port_a}/api/kanban/board"
                )["board"]
                column_uuid = board_a["children"][0]["uuid"]
                request_json(
                    "POST",
                    f"http://127.0.0.1:{port_a}/api/kanban/cards/create",
                    {
                        "column_uuid": column_uuid,
                        "name": "Synced Card",
                        "description": "",
                        "participants": [],
                    },
                )

                share = connect_over_http(port_a, port_b, board_a["uuid"])
                self.assertEqual(share["status"], "ok")

                deadline = time.monotonic() + 10
                final_a = final_b = None
                while time.monotonic() < deadline:
                    final_a = request_json(
                        "GET", f"http://127.0.0.1:{port_a}/api/kanban/board"
                    )
                    final_b = request_json(
                        "GET", f"http://127.0.0.1:{port_b}/api/kanban/board"
                    )
                    if "Synced Card" in card_names(final_b["board"]):
                        break
                    time.sleep(0.2)

                self.assertIn("Synced Card", card_names(final_a["board"]))
                self.assertIn("Synced Card", card_names(final_b["board"]))
                self.assertEqual(
                    sorted(final_a["network"]["members"]),
                    [f"http://127.0.0.1:{port_a}", f"http://127.0.0.1:{port_b}"],
                )
                self.assertEqual(
                    sorted(final_b["network"]["members"]),
                    [f"http://127.0.0.1:{port_a}", f"http://127.0.0.1:{port_b}"],
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

    def test_local_card_is_not_rolled_back_by_stale_peer_view(self):
        port_a = free_port()
        port_b = free_port()
        processes = []
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            configs = []
            for port in (port_a, port_b):
                config = {
                    "app_module": "kanban_logic",
                    "ui_file": "kanban.html",
                    "css_file": "kanban.css",
                    "storage_file": str(tmp_path / f"kanban_{port}.json"),
                    "debug": True,
                }
                config_path = tmp_path / f"config_{port}.json"
                config_path.write_text(json.dumps(config), encoding="utf-8")
                configs.append(config_path)

            for port, config_path in ((port_a, configs[0]), (port_b, configs[1])):
                processes.append(subprocess.Popen(
                    [sys.executable, "app_server.py", f"{port}:kanban", str(config_path)],
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                ))

            try:
                wait_for_server(port_a)
                wait_for_server(port_b)
                board_a = request_json(
                    "GET", f"http://127.0.0.1:{port_a}/api/kanban/board"
                )["board"]
                share = connect_over_http(port_a, port_b, board_a["uuid"])
                self.assertEqual(share["status"], "ok")
                request_json(
                    "POST",
                    f"http://127.0.0.1:{port_b}/api/kanban/auto_adopt",
                    {"enabled": True},
                )
                column_uuid = board_a["children"][0]["uuid"]
                request_json(
                    "POST",
                    f"http://127.0.0.1:{port_a}/api/kanban/cards/create",
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
                        "GET", f"http://127.0.0.1:{port_a}/api/kanban/board"
                    )
                    final_b = request_json(
                        "GET", f"http://127.0.0.1:{port_b}/api/kanban/board"
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
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            configs = []
            for port in (port_a, port_b):
                config = {
                    "app_module": "kanban_logic",
                    "ui_file": "kanban.html",
                    "css_file": "kanban.css",
                    "storage_file": str(tmp_path / f"kanban_{port}.json"),
                    "debug": True,
                }
                config_path = tmp_path / f"config_{port}.json"
                config_path.write_text(json.dumps(config), encoding="utf-8")
                configs.append(config_path)

            for port, config_path in ((port_a, configs[0]), (port_b, configs[1])):
                processes.append(subprocess.Popen(
                    [sys.executable, "app_server.py", f"{port}:kanban", str(config_path)],
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                ))

            try:
                wait_for_server(port_a)
                wait_for_server(port_b)
                board_a = request_json(
                    "GET", f"http://127.0.0.1:{port_a}/api/kanban/board"
                )["board"]
                share = connect_over_http(port_a, port_b, board_a["uuid"])
                self.assertEqual(share["status"], "ok")
                request_json(
                    "POST",
                    f"http://127.0.0.1:{port_b}/api/kanban/auto_adopt",
                    {"enabled": True},
                )
                source_uuid = board_a["children"][0]["uuid"]
                target_uuid = board_a["children"][1]["uuid"]
                create = request_json(
                    "POST",
                    f"http://127.0.0.1:{port_a}/api/kanban/cards/create",
                    {
                        "column_uuid": source_uuid,
                        "name": "Moved Card",
                        "description": "",
                        "participants": [],
                    },
                )
                card_uuid = create["value"]["uuid"]
                raw_b = request_json(
                    "GET", f"http://127.0.0.1:{port_b}/api/protocol"
                )
                self.assertEqual(find_card_parent(raw_b, "Moved Card"), source_uuid)
                request_json(
                    "POST",
                    f"http://127.0.0.1:{port_a}/api/kanban/cards/move",
                    {
                        "card_uuid": card_uuid,
                        "column_uuid": target_uuid,
                        "index": 0,
                    },
                )
                raw_b = request_json(
                    "GET", f"http://127.0.0.1:{port_b}/api/protocol"
                )
                self.assertEqual(find_card_parent(raw_b, "Moved Card"), target_uuid)

                deadline = time.monotonic() + 10
                final_b = None
                while time.monotonic() < deadline:
                    final_b = request_json(
                        "GET", f"http://127.0.0.1:{port_b}/api/kanban/board"
                    )
                    if find_card_parent(final_b["board"], "Moved Card") == target_uuid:
                        break
                    time.sleep(0.2)

                self.assertEqual(
                    find_card_parent(final_b["board"], "Moved Card"),
                    target_uuid,
                )

                deadline = time.monotonic() + 10
                final_a = None
                while time.monotonic() < deadline:
                    final_a = request_json(
                        "GET", f"http://127.0.0.1:{port_a}/api/kanban/board"
                    )
                    peer = final_a["peers"].get(f"http://127.0.0.1:{port_b}")
                    if peer and find_card_parent(peer, "Moved Card") == target_uuid:
                        break
                    time.sleep(0.2)

                peer = final_a["peers"].get(f"http://127.0.0.1:{port_b}")
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
        # unrelated board. B connects to both via "Connect via token"
        # (/api/join_discussion) as two separate actions. A and C never
        # connect to each other and share nothing - so neither should ever
        # learn about the other, even though B legitimately knows both.
        port_a = free_port()
        port_b = free_port()
        port_c = free_port()
        processes = []
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            configs = {}
            for port in (port_a, port_b, port_c):
                config = {
                    "app_module": "kanban_logic",
                    "ui_file": "kanban.html",
                    "css_file": "kanban.css",
                    "storage_file": str(tmp_path / f"kanban_{port}.json"),
                    "debug": True,
                }
                config_path = tmp_path / f"config_{port}.json"
                config_path.write_text(json.dumps(config), encoding="utf-8")
                configs[port] = config_path

            for port in (port_a, port_b, port_c):
                processes.append(subprocess.Popen(
                    [sys.executable, "app_server.py", f"{port}:kanban", str(configs[port])],
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                ))

            try:
                wait_for_server(port_a)
                wait_for_server(port_b)
                wait_for_server(port_c)

                board_a = request_json(
                    "GET", f"http://127.0.0.1:{port_a}/api/kanban/board"
                )["board"]
                board_c = request_json(
                    "GET", f"http://127.0.0.1:{port_c}/api/kanban/board"
                )["board"]

                join_a = request_json(
                    "POST", f"http://127.0.0.1:{port_b}/api/join_discussion",
                    {
                        "address": f"http://127.0.0.1:{port_a}",
                        "topic_uuids": [board_a["uuid"]],
                    },
                    timeout=20,
                )
                self.assertEqual(join_a["status"], "ok")
                join_c = request_json(
                    "POST", f"http://127.0.0.1:{port_b}/api/join_discussion",
                    {
                        "address": f"http://127.0.0.1:{port_c}",
                        "topic_uuids": [board_c["uuid"]],
                    },
                    timeout=20,
                )
                self.assertEqual(join_c["status"], "ok")

                time.sleep(1.0)

                final_a = request_json(
                    "GET", f"http://127.0.0.1:{port_a}/api/kanban/board"
                )
                final_c = request_json(
                    "GET", f"http://127.0.0.1:{port_c}/api/kanban/board"
                )

                self.assertEqual(
                    sorted(final_a["network"]["members"]),
                    [f"http://127.0.0.1:{port_a}", f"http://127.0.0.1:{port_b}"],
                )
                self.assertEqual(
                    sorted(final_c["network"]["members"]),
                    [f"http://127.0.0.1:{port_b}", f"http://127.0.0.1:{port_c}"],
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

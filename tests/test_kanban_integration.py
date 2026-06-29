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


def card_names(board: dict) -> list[str]:
    names = []
    for column in board["children"]:
        for card in column["children"]:
            names.append(card["data"]["name"])
    return names


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
                    [sys.executable, "server.py", str(port), str(config_path)],
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
                    f"http://127.0.0.1:{port_a}/api/kanban/cards",
                    {
                        "column_uuid": column_uuid,
                        "card": {"name": "Synced Card", "description": "", "owners": ""},
                    },
                )

                invite = request_json(
                    "POST",
                    f"http://127.0.0.1:{port_a}/api/kanban/invite",
                    {"address": f"http://127.0.0.1:{port_b}"},
                    timeout=20,
                )
                self.assertEqual(invite["status"], "ok")

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
                    [sys.executable, "server.py", str(port), str(config_path)],
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                ))

            try:
                wait_for_server(port_a)
                wait_for_server(port_b)
                invite = request_json(
                    "POST",
                    f"http://127.0.0.1:{port_a}/api/kanban/invite",
                    {"address": f"http://127.0.0.1:{port_b}"},
                    timeout=20,
                )
                self.assertEqual(invite["status"], "ok")

                board_a = request_json(
                    "GET", f"http://127.0.0.1:{port_a}/api/kanban/board"
                )["board"]
                column_uuid = board_a["children"][0]["uuid"]
                request_json(
                    "POST",
                    f"http://127.0.0.1:{port_a}/api/kanban/cards",
                    {
                        "column_uuid": column_uuid,
                        "card": {"name": "Local Card", "description": "", "owners": ""},
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


if __name__ == "__main__":
    unittest.main()

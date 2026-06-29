import unittest
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import server
from s_protocol import SovereignProtocol


class ServerConfigTests(unittest.TestCase):
    def test_parse_plain_port(self):
        self.assertEqual(server._parse_target("8001"), (8001, None))

    def test_parse_port_app(self):
        self.assertEqual(server._parse_target("8001:kanban"), (8001, "kanban"))

    def test_app_name_defaults_without_config_file(self):
        config = server._load_config(None, "missing_test_app")

        self.assertEqual(config["app_module"], "missing_test_app_logic")
        self.assertEqual(config["ui_file"], "missing_test_app.html")
        self.assertEqual(config["css_file"], "missing_test_app.css")

    def test_existing_app_config_file_is_loaded(self):
        config = server._load_config(None, "kanban")

        self.assertEqual(config["app_module"], "kanban_logic")
        self.assertEqual(config["ui_file"], "kanban.html")
        self.assertEqual(config["css_file"], "kanban.css")

    def test_concurrent_saves_use_independent_temp_files(self):
        protocol = SovereignProtocol(9120)
        protocol.create_child(protocol.prsp.uuid, {"name": "saved"}, {})

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(
                    lambda _: server.save_prsp_to_file(protocol, str(path)),
                    range(32),
                ))

            loaded = SovereignProtocol(9121)
            self.assertTrue(server.load_prsp_from_file(loaded, str(path)))
            self.assertEqual(loaded.prsp.children[0].data["name"], "saved")
            self.assertFalse(list(path.parent.glob("state.json.*.tmp")))

    def test_file_replace_retries_transient_permission_error(self):
        calls = []

        def flaky_replace(source_path, target_path):
            calls.append((source_path, target_path))
            if len(calls) < 3:
                raise PermissionError("locked")

        with patch("server.os.replace", flaky_replace), patch("server.time.sleep"):
            server._replace_file_with_retry("source.tmp", "target.json")

        self.assertEqual(len(calls), 3)


if __name__ == "__main__":
    unittest.main()

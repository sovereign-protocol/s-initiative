import unittest
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sovereign import app_server
from sovereign.session import Session


class ServerConfigTests(unittest.TestCase):
    def test_parse_plain_port(self):
        self.assertEqual(app_server.parse_target("8001"), (8001, None))

    def test_parse_port_app(self):
        self.assertEqual(app_server.parse_target("8001:kanban"), (8001, "kanban"))

    def test_app_name_defaults_without_config_file(self):
        config = app_server.load_config(None, "missing_test_app")

        self.assertEqual(config["app_module"], "missing_test_app_logic")
        self.assertIsNone(config["ui_file"])
        self.assertIsNone(config["css_file"])

    def test_existing_app_config_file_is_loaded(self):
        config = app_server.load_config(None, "kanban")

        self.assertEqual(config["app_module"], "s_kanban.application")
        self.assertEqual(config["ui_file"], "kanban.html")
        self.assertEqual(config["css_file"], "kanban.css")

    def test_concurrent_saves_use_independent_temp_files(self):
        session = Session("http://127.0.0.1:9120")
        session.create_child(session.protocol.root.uuid, {"name": "saved"}, {})

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(
                    lambda _: app_server.save_session_to_file(session, str(path)),
                    range(32),
                ))

            loaded = Session("http://127.0.0.1:9121")
            self.assertTrue(app_server.load_session_from_file(loaded, str(path)))
            self.assertEqual(loaded.protocol.root.children[0].data["name"], "saved")
            self.assertFalse(list(path.parent.glob("state.json.*.tmp")))


if __name__ == "__main__":
    unittest.main()

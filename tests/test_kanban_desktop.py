import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from s_kanban import desktop as kanban_desktop
from sovereign import desktop

desktop_name = kanban_desktop.WINDOW_TITLE


class DesktopDataLocationTests(unittest.TestCase):
    """Where a frozen build keeps state, which is the whole risk here."""

    def test_storage_is_not_derived_from_the_port(self):
        # The desktop build picks a free port per launch. If the session file
        # followed the port, every launch would open an empty board.
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(desktop, "data_directory", return_value=Path(directory)):
                first = desktop.desktop_config("kanban", None, desktop_name)
                second = desktop.desktop_config("kanban", None, desktop_name)

        self.assertEqual(first["storage_file"], second["storage_file"])
        for port in (9105, 51789):
            self.assertNotIn(str(port), first["storage_file"])

    def test_storage_does_not_depend_on_the_working_directory(self):
        # An executable started from the shell has no meaningful cwd, so an
        # absolute per-user path is the only thing that survives.
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(desktop, "data_directory", return_value=Path(directory)):
                config = desktop.desktop_config("kanban", None, desktop_name)

        self.assertTrue(Path(config["storage_file"]).is_absolute())
        self.assertTrue(config["storage_file"].startswith(directory))

    def test_an_explicit_storage_file_still_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            chosen = str(Path(directory) / "chosen.json")
            config_file = Path(directory) / "config.json"
            config_file.write_text(
                json.dumps({"storage_file": chosen}), encoding="utf-8",
            )
            with patch.object(desktop, "data_directory", return_value=Path(directory)):
                config = desktop.desktop_config("kanban", None, desktop_name, str(config_file))

        self.assertEqual(config["storage_file"], chosen)

    def test_data_directory_is_user_scoped_and_named(self):
        directory = desktop.data_directory(desktop_name)

        self.assertTrue(directory.is_absolute())
        self.assertEqual(directory.name, desktop_name)

    @unittest.skipUnless(sys.platform == "win32", "Windows layout")
    def test_windows_uses_local_appdata(self):
        with patch.dict("os.environ", {"LOCALAPPDATA": r"X:\AppData\Local"}):
            self.assertEqual(
                desktop.data_directory(desktop_name),
                Path(r"X:\AppData\Local") / desktop_name,
            )


class DesktopServerTests(unittest.TestCase):
    def test_free_port_is_actually_bindable(self):
        port = desktop.free_port()

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", port))  # must not raise

    def test_binding_stays_on_loopback(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(desktop, "data_directory", return_value=Path(directory)):
                config = desktop.desktop_config("kanban", None, desktop_name)

        self.assertEqual(config["bind_host"], "127.0.0.1")

    def test_waiting_gives_up_rather_than_hanging(self):
        never = type("Server", (), {"started": False})()

        self.assertFalse(desktop._wait_until_serving(never, deadline=0.0))

    def test_waiting_returns_as_soon_as_the_server_is_up(self):
        started = type("Server", (), {"started": True})()

        self.assertTrue(
            desktop._wait_until_serving(started, deadline=float("inf")),
        )


class DesktopWindowSessionTests(unittest.TestCase):
    """Exercise the real server lifecycle with only the window faked."""

    def _fake_webview(self, opened: dict):
        import types
        import urllib.request

        module = types.ModuleType("webview")

        def create_window(title, url, **kwargs):
            opened["title"] = title
            opened["url"] = url
            # Real pywebview returns a window object, and Core hooks its
            # events.shown to darken the native titlebar. Returning None
            # made this fake diverge from the thing it stands in for, so a
            # Core change that is correct against pywebview broke it here.
            return MagicMock()

        def start():
            # The window would load this URL, so proving it serves here is
            # what makes the frozen build's blank-window failure visible.
            with urllib.request.urlopen(opened["url"], timeout=10) as response:
                opened["status"] = response.status
                opened["body"] = response.read(2048).decode("utf-8", "replace")

        module.create_window = create_window
        module.start = start
        return module

    def test_window_gets_a_served_page_and_the_session_is_saved_on_close(self):
        opened: dict = {}
        with tempfile.TemporaryDirectory() as directory:
            storage = Path(directory) / "desktop.json"
            config_file = Path(directory) / "config.json"
            config_file.write_text(
                json.dumps({
                    "storage_file": str(storage),
                    "blob_store_dir": str(Path(directory) / "blobs"),
                }),
                encoding="utf-8",
            )
            with patch.dict(sys.modules, {"webview": self._fake_webview(opened)}):
                code = desktop.run_desktop(
                    "kanban", desktop_name,
                    kanban_desktop.APPLICATION_ALIASES, str(config_file),
                )

            self.assertEqual(code, 0)
            self.assertEqual(opened["status"], 200)
            self.assertEqual(opened["title"], desktop_name)
            self.assertIn("<html", opened["body"].lower())
            # Closing the window must leave the boards on disk, or the next
            # launch silently starts empty.
            self.assertTrue(storage.is_file())

    def test_the_window_opens_on_loopback(self):
        opened: dict = {}
        with tempfile.TemporaryDirectory() as directory:
            config_file = Path(directory) / "config.json"
            config_file.write_text(
                json.dumps({
                    "storage_file": str(Path(directory) / "desktop.json"),
                    "blob_store_dir": str(Path(directory) / "blobs"),
                }),
                encoding="utf-8",
            )
            with patch.dict(sys.modules, {"webview": self._fake_webview(opened)}):
                desktop.run_desktop(
                    "kanban", desktop_name,
                    kanban_desktop.APPLICATION_ALIASES, str(config_file),
                )

        self.assertTrue(opened["url"].startswith("http://127.0.0.1:"))


class DesktopEntryPointTests(unittest.TestCase):
    def test_missing_pywebview_explains_the_install(self):
        # The window is the only thing pywebview is needed for, so its absence
        # must read as an install hint rather than a crash.
        with patch.object(desktop, "run_desktop", side_effect=ImportError("no webview")):
            code = kanban_desktop.main([])

        self.assertEqual(code, 1)

    def test_too_many_arguments_are_rejected(self):
        self.assertEqual(kanban_desktop.main(["one.json", "two.json"]), 1)

    def test_the_alias_matches_the_shipped_manifest(self):
        # The launcher names the application itself, so a manifest rename must
        # not leave the desktop build pointing at an application that is no
        # longer there.
        from s_kanban.application import APPLICATION_MANIFEST

        alias = kanban_desktop.APPLICATION_ALIASES["kanban"]
        self.assertEqual(alias["application_id"], APPLICATION_MANIFEST.application_id)
        self.assertEqual(alias["asset_package"], APPLICATION_MANIFEST.asset_package)
        self.assertEqual(alias["ui_file"], APPLICATION_MANIFEST.ui_file)


if __name__ == "__main__":
    unittest.main()

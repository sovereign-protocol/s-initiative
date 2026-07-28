"""What S-Kanban supplies to the desktop window, and nothing more.

The window, the runtime, the port and the shutdown are Core's, and so are the
tests for them - they live in Core's tests/test_desktop.py, where a change to
`sovereign.desktop` fails in the pull request that makes it. This file used to
hold them, which is how Core 0.1.2's change to `run_desktop` reached PyPI
green and only broke here afterwards.

What is left is the two things this application actually names: which module
the launcher starts, and what the window is called.
"""

import unittest
from unittest.mock import patch

from s_kanban import desktop as kanban_desktop
from s_kanban.application import APPLICATION_MANIFEST


class ApplicationAliasTests(unittest.TestCase):
    def test_the_alias_matches_the_shipped_manifest(self):
        # The launcher names the application itself, so a manifest rename must
        # not leave the desktop build pointing at an application that is no
        # longer there.
        alias = kanban_desktop.APPLICATION_ALIASES["kanban"]

        self.assertEqual(alias["application_id"], APPLICATION_MANIFEST.application_id)
        self.assertEqual(alias["asset_package"], APPLICATION_MANIFEST.asset_package)
        self.assertEqual(alias["ui_file"], APPLICATION_MANIFEST.ui_file)

    def test_the_alias_names_a_module_that_is_actually_importable(self):
        # app_module is the one alias field the manifest does not supply, so
        # nothing above would notice it pointing at a module that moved.
        import importlib

        alias = kanban_desktop.APPLICATION_ALIASES["kanban"]

        self.assertEqual(alias["app_module"], "s_kanban.application")
        self.assertIsNotNone(importlib.import_module(alias["app_module"]))

    def test_the_window_is_called_what_the_manifest_calls_the_application(self):
        self.assertEqual(
            kanban_desktop.WINDOW_TITLE, APPLICATION_MANIFEST.display_name,
        )

    def test_the_launcher_hands_core_this_application_and_this_title(self):
        # Everything past this call is Core's. All that is checked here is that
        # S-Kanban's own three answers arrive intact.
        seen = {}

        with patch.object(
            kanban_desktop, "desktop_main",
            lambda *args: seen.update(args=args) or 0,
        ):
            self.assertEqual(kanban_desktop.main([]), 0)

        _argv, app_name, window_title, aliases = seen["args"]
        self.assertEqual(app_name, "kanban")
        self.assertEqual(window_title, kanban_desktop.WINDOW_TITLE)
        self.assertIs(aliases, kanban_desktop.APPLICATION_ALIASES)


if __name__ == "__main__":
    unittest.main()

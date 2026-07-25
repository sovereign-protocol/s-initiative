"""Desktop entry point for S-Kanban.

The window, the runtime and the shutdown are Core's. This module supplies
only what is this application's: which module to start, and what to call the
window.
"""

from __future__ import annotations

import sys

from sovereign import desktop_main

from .application import APPLICATION_MANIFEST


WINDOW_TITLE = APPLICATION_MANIFEST.display_name

APPLICATION_ALIASES = {
    "kanban": {
        "app_module": "s_kanban.application",
        "application_id": APPLICATION_MANIFEST.application_id,
        "asset_package": APPLICATION_MANIFEST.asset_package,
        "ui_file": APPLICATION_MANIFEST.ui_file,
        "css_file": APPLICATION_MANIFEST.css_file,
    },
}


def main(argv: list[str] | None = None) -> int:
    return desktop_main(argv, "kanban", WINDOW_TITLE, APPLICATION_ALIASES)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""S-Kanban development launcher over the separately installed Core host."""

from sovereign.app_server import *  # noqa: F401,F403
from sovereign.app_server import (
    app_default_config as _core_app_default_config,
    load_config as _core_load_config,
    main as _core_main,
)


APPLICATION_ALIASES = {
    "kanban": {
        "app_module": "s_kanban.application",
        "application_id": "kanban",
        "asset_package": "s_kanban.assets",
        "ui_file": "kanban.html",
        "css_file": "kanban.css",
    },
}


def app_default_config(app_name: str) -> dict:
    return _core_app_default_config(app_name, APPLICATION_ALIASES)


def load_config(config_path: str | None = None, app_name: str | None = None) -> dict:
    return _core_load_config(config_path, app_name, APPLICATION_ALIASES)


def main(argv: list[str] | None = None) -> None:
    _core_main(argv, APPLICATION_ALIASES)


if __name__ == "__main__":
    main()

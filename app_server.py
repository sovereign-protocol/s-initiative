#!/usr/bin/env python3
"""S-Initiative development launcher over the separately installed Core host."""

from sovereign.app_server import *  # noqa: F401,F403
from sovereign.app_server import (
    app_default_config as _core_app_default_config,
    load_config as _core_load_config,
    main as _core_main,
)


APPLICATION_ALIASES = {
    "initiative": {
        "app_module": "s_initiative.application",
        "application_id": "initiative",
        "asset_package": "s_initiative.assets",
        "ui_file": "initiative.html",
        "css_file": "initiative.css",
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

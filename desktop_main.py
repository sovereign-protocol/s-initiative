#!/usr/bin/env python3
"""Frozen-executable entry point for the S-Kanban desktop window.

PyInstaller needs a real script to analyse. Keeping it to one call means the
frozen build and the installed `s-kanban-desktop` command share exactly one
implementation.
"""

from s_kanban.desktop import main


if __name__ == "__main__":
    raise SystemExit(main())

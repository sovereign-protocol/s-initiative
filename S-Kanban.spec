# -*- mode: python ; coding: utf-8 -*-
"""Freeze S-Kanban into a windowed executable.

Build from a checkout of this repository, with Core installed as a normal
dependency:

    pip install -e .[desktop]
    pip install pyinstaller
    pyinstaller S-Kanban.spec

Only this application and the Core it runs on are collected. The other
Sovereign applications live in their own repositories and are deliberately
not bundled: pulling them in here would cross both a repository and a
licence boundary for no gain.

Distribution note. `sovereign` is LGPL-3.0-or-later, so shipping this
executable to anyone else carries the licence's notice and
relinking obligations, which is why the project publishes source and wheels
first. Building it for yourself is not distribution and carries none of that.
"""
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []
# webview pulls its platform backend in dynamically, so static analysis alone
# leaves the frozen build without a window to draw into.
for package in ("uvicorn", "webview", "sovereign", "s_kanban"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports


a = Analysis(
    ["desktop_main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="S-Kanban",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    # The point of this build: its own window, and no console behind it.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

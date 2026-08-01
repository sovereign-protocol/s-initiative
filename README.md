# S-Initiative

S-Initiative is a local-first Kanban application built on Sovereign Core. Every
participant keeps an explicit local perspective; differences are visible and
resolved by human-controlled adopt or rollback reactions rather than silent
central overwrites.

## Quickstart

Requires Python 3.10+ and Sovereign Core `>=0.1.0,<0.2`.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\sovereign-host.exe 9305 config/initiative.example.json
```

Open <http://127.0.0.1:9305>. Direct HTTP is intended for LAN/VPN use. Local
folder and SFTP mailbox channels are configured through relay targets.

## Desktop window

The same host can draw into its own window instead of a browser tab:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[desktop]"
.\.venv\Scripts\s-initiative-desktop.exe
```

The window picks a free port at start-up, so boards are kept in a per-user
directory (`%LOCALAPPDATA%\S-Initiative` on Windows) rather than beside the port
number. Pass a config file to override anything, including `storage_file`.

### Building the executable

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\pyinstaller.exe S-Initiative.spec
```

The result is `dist/S-Initiative.exe`, bundling this application and the Core it
runs on. Building it for your own use carries no distribution obligations.
Passing that executable to anyone else does: `sovereign` is
LGPL-3.0-or-later, so its notices and relinking terms travel with the binary.
Publish source or wheels unless you have checked those terms.

## License

Application software and assets are Apache-2.0. Documentation is CC-BY-4.0.
Sovereign Core is a separately replaceable LGPL-3.0-or-later dependency.

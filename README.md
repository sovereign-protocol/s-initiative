# S-Kanban

S-Kanban is a local-first Kanban application built on Sovereign Core. Every
participant keeps an explicit local perspective; differences are visible and
resolved by human-controlled adopt or rollback reactions rather than silent
central overwrites.

## Quickstart

Requires Python 3.10+ and Sovereign Core `>=0.1.0,<0.2`.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\sovereign-host.exe 9305 config/kanban.example.json
```

Open <http://127.0.0.1:9305>. Direct HTTP is intended for LAN/VPN use. Local
folder and SFTP mailbox channels are configured through relay targets.

## License

Application software and assets are Apache-2.0. Documentation is CC-BY-4.0.
Sovereign Core is a separately replaceable LGPL-3.0-or-later dependency.

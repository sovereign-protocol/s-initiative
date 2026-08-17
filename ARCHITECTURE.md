# Architecture

S-Initiative owns Kanban node schemas, auto-adoption policy, controllers, facade, and
browser UI. It imports only the documented `sovereign` package root. Sovereign
Core owns protocol, Session, channels, hosting, identity, and blob mechanics and
contains no Kanban node-type knowledge.

References to other applications' topics follow the same division. Core owns
what a `topic_link` is and what following one does; this application owns only
where its links live — as direct children of the initiative — plus the rule
that there is at most one team. It never imports S-Team or S-Flow: what it
knows about a linked topic is an application id and a uuid, which is the whole
of what a link carries. See `DESIGN_INITIATIVE.md` and Core's
`DESIGN_TOPIC_LINKS.md`.

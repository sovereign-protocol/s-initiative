# Architecture

S-Initiative owns Kanban node schemas, auto-adoption policy, controllers, facade, and
browser UI. It imports only the documented `sovereign` package root. Sovereign
Core owns protocol, Session, channels, hosting, identity, and blob mechanics and
contains no Kanban node-type knowledge.

Relationships to other applications' topics are S-Initiative domain content.
`initiative_relationship` nodes live directly under the initiative and are
rendered in its Mandate; this application owns their schema and the rule that
there is at most one team. They do not grant access. Core separately owns only
the local navigation shortcuts below a title. See `DESIGN_INITIATIVE.md` and
Core's `DESIGN_NAVIGATION_LINKS.md`.

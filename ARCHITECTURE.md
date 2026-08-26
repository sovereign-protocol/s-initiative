# Architecture

S-Initiative owns Kanban node schemas, auto-adoption policy, controllers, facade, and
browser UI. It imports only the documented `sovereign` package root. Sovereign
Core owns protocol, Session, channels, hosting, identity, and blob mechanics and
contains no Kanban node-type knowledge.

Connections to other applications' topics — the team an initiative belongs
to, the flows it runs — are Core's own `sovereign_relationship`, reached
from the shared header rather than rendered here. This application owns
only the one domain rule Core cannot know, that there is at most one team,
enforced through the `validate_relationship` hook it registers. They do not
grant access. See `DESIGN_INITIATIVE.md` and Core's
`DESIGN_NAVIGATION_LINKS.md`.

# Architecture

S-Kanban owns Kanban node schemas, auto-adoption policy, controllers, facade, and
browser UI. It imports only the documented `sovereign` package root. Sovereign
Core owns protocol, Session, channels, hosting, identity, and blob mechanics and
contains no Kanban node-type knowledge.

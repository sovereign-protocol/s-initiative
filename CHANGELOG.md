# Changelog

## 0.1.0a3 - 2026-07-30

- Require Sovereign Core 0.1.5 for composite responses and Session-owned
  optimistic view support.
- `/api/kanban/board` now uses Core's atomic
  snapshot-observe-merge boundary, so a relay poll cannot tear one response.
- Board selection metadata is now read as a snapshot and written in one
  Session transaction, matching Core 0.1.5's locked metadata contract.
- **Fixed: auto-adopted column renames can return to an earlier name** without
  leaving both clients in false divergence.
- **Fixed: card hover now follows the active theme** instead of applying the
  dark-theme surface colour in light mode.
- Card dragging now suppresses accidental text selection and shows a floating
  card preview while leaving a clear placeholder at the drop position.
- **Fixed: concurrent move-only conflicts now converge on the last move.**
  This applies to both cards and agenda points; older relay publications can
  no longer undo the newer position. Card drag styling is also cleared after
  every drop instead of leaving cards dimmed until a page reload.
- **Fixed: an agenda drop could succeed and then immediately revert.** A
  client no longer adopts the stale order a peer published just before seeing
  the move; the peer still adopts the mover's new order on its next cycle.
- Expanded facade API v1 with explicit board, card, agenda, reaction, and
  policy commands for optional consumers. Returned nodes remain snapshots.
- Card and column moves now use Core's shared cross-parent fractional ordering.
- Application code uses Session queries and metadata namespaces instead of
  mutable registries.
- Mutation and peer-reaction routes now reject nodes outside a Kanban board,
  including same-typed nodes under another application's topic.
- Core retired the direct HTTP channel, so a board is shared over a relay or
  not at all. Two internal call sites went with it: `users()` read
  `Session.members`, which no longer exists (it now uses the public peer
  projection), and three logic methods returned sync effects nothing can deliver.
  No behaviour changed for a board already shared over a relay. The live
  two-server integration tests now stand up a shared folder and connect
  through it, which is the route users take.
- The multi-client tests connect over a relay folder instead of an
  in-process HTTP stand-in. `MemoryHttpClient` delivered a peer's message by
  calling the other runtime's handler; the new `tests/relay_clients.py`
  gives each client its own target on one shared folder, and a `sync()` the
  test calls when a cycle should happen. Slower - about 17s across the suite
  - and it exercises the route people actually use. Behaviour unchanged; see
  Core's `DESIGN_TOPIC_HOME_CHANNELS.md` section 3.
- The last board can be deleted. `delete_board` refused when only one board
  remained, which left no way to clear a host of boards it no longer wants.
  Nothing depended on a board existing: opening the board view calls
  `ensure_board()`, which makes a fresh empty one exactly as it does on a
  first run. Deleting the last board now also clears the remembered
  selection, so a board still awaiting its peers' confirmation of the
  deletion is not handed back as the current one.
- Removed `POST /api/kanban/boards/unshare` and `KanbanLogic.unshare_board`.
  No interface ever called the route - only its own tests did. Returning a
  board to private is Core's "stop using" on the channel carrying it, which
  now does so for relay channels too; `delete_board` never went through this
  path either, calling `Session.end_topic_sharing` directly.
  `_is_kanban_board_topic` went with it, having no other caller.

No wire or persistence change.

## 0.1.0a2 - 2026-07-26

- First release published to PyPI. `pip install s-kanban` now resolves,
  and with it Personal Cockpit's optional `test-kanban` extra.
- Card file attachments: attach, download and remove, stored as
  content-addressed blobs that sync with the board.
- Optional desktop window (`pip install s-kanban[desktop]`), and CI now
  builds the PyInstaller spec on every push so it cannot rot unnoticed.
- Core floor raised to `0.1.1`. The application runs fine against `0.1.0`,
  since the blob transfer tracing it gained is additive - but the tests
  shipped in this package assert those trace events, so a user running
  them against `0.1.0` would see a failure that is not their fault. The
  declared range now matches what is actually tested. No wire or
  persistence change.

## 0.1.0a1 - 2026-07-26

Tagged on GitHub only; never published to an index.

- Initial public alpha.
- Local boards, columns, cards, comments, collaboration topics, profiles, relay
  targets, explicit transitions/reactions, and versioned Personal Cockpit facade.

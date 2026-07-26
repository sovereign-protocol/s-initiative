# Changelog

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

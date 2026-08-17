# Changelog

## Unreleased

- **An initiative names the team it belongs to and the flows it runs.** Both
  are other applications' topics, referenced through Core's `topic_link` as
  direct children of the initiative. One team — a second is not a second
  opinion about whose initiative this is, and the way to change the answer is
  to remove the first — and as many flows as it likes. The picker offers only
  what this client already holds, because a topic nobody has told you about is
  not merely unlisted, it is unreachable.

  A link is a name for a topic and never a key to it: following one reaches
  only what a peer is already publishing where this client can see it, which
  is why naming the team hands nobody access they did not have. A link to
  something not held here is drawn as an invitation rather than an error, and
  clicking it takes it up if anybody here is publishing it. The recorded title
  is what the link says until the topic is held; after that its own name wins.
  Removing a link removes the reference and nothing else — the team or flow is
  untouched, and so is everybody else's reference to it. Links replicate and
  appear among the divergences, so two clients disagreeing about which team an
  initiative belongs to is something a human sees. One reference per topic
  whoever wrote it, which this application says for itself: Core allows two
  actors to reference one topic from one parent, because a team's list of what
  it runs is the union of its members' own references — but what an initiative
  belongs to and runs is a property of the initiative, so a second would only
  draw twice.

- `DESIGN_INITIATIVE.md` records what an initiative is beyond its board — the
  objective everybody shares, the needs it addresses, the approach, the
  timeline and its milestones, expected impact against assessed impact, and
  committed availability. Only the links in it are built.

- **Board highlighting takes the shared stage colours.** The board already
  keyed on the stage, but with a palette of its own, so the same fact was one
  colour here and another on a team page — and `in_flight` was grey on a
  column while being blue on a card. Both are the shared grey now. The purple
  that separated "awaiting a peer" from "in flight" is gone with it: the
  header counts both as in transition, and the pulse already says which is
  still travelling without spending a colour on it.

## 0.1.0a5 - 2026-08-16

- A ghost is now the seat a card has left, not the copy somebody else holds.
  Whoever made the move sees their card solid at its new position, and the
  position it came from stays as a ghost until the other side catches up — the
  same rule read from either screen, whether the move was yours or theirs. A
  deletion takes effect at once and leaves the same ghost behind. Ghosts only
  appear once the peer has been seen holding the old seat, so a peer who adopts
  automatically produces none at all, and your own move no longer flashes one up
  while it travels. Dragging a card the peer has just moved adopts their move
  first: setting your move against theirs from a base neither of you is at any
  more would classify as a conflict, out of two ordinary moves.
- Changing the auto-adopt mode now reconsiders what the old mode held. The mode
  is read at the moment each change is decided, so changing it changes answers
  already given; two of the four modes declare the same handling to Core, which
  is why the declaration alone could not be the trigger. A card held under the
  old mode used to wait for the peer's next message, and if none came, for good.
- The auto-adopt mode is now published to Core as declared adoption metadata:
  a topic default per board, plus `hold` on the cards the mode protects. A
  protected card still admits comments, which are additive and author-stamped;
  under "never" the topic default holds everything and no card needs an entry.
  The entries are rebuilt before each adoption pass, since they derive from the
  mode and from card ownership, both of which move. The eligibility callback,
  the protected-descendant walk and the shallow column pre-pass are all gone:
  Core enforces the declaration, and a node not yet held is classified at first
  sight — an incoming agenda item is never adopted, and a card arriving already
  marked as yours is held before it is taken rather than after.

- Kanban card-position last-write-wins reconciliation is now declared as a
  Core policy. Initiative retains only the position timestamp and its explicit
  card eligibility rules; comparison and adoption execution live in Core.
- Agenda items are projected directly from verified perspectives instead of
  being automatically adopted. The two-hour perspective limit is now Core's
  default rather than an Initiative declaration; the unused
  `agenda_perspective_*` configuration keys are gone.

- Reactions now use Core's shared control: a divergence with a single available
  act is a button naming that act rather than a "React" menu with one entry.
  The board's copies of the menu, its markup and its stylesheet are gone.
- Which peer's absence a reaction settles is now read from the transition event
  rather than from whether that peer's cached tree contains the node, so a peer
  whose tree has not arrived can no longer turn "adopt their change" into a
  local delete, and a contributing peer with no cached tree is still offered.
- Divergences can now be answered from the collaboration pane.

## 0.1.0a4 - 2026-08-01

- Renamed from S-Kanban to **S-Initiative**, distributed as
  `sovereign-initiative`. The application id is now `initiative`, routes are
  served under `/api/initiative/`, and the Python package is `s_initiative`.
  Board and card node types keep their `kanban_` prefix: a Kanban board stays
  one of the things an initiative is worked through, so that vocabulary is
  domain rather than application identity.
- Require Sovereign Core 0.1.6 for the shared UI kit.

## 0.1.0a3 - 2026-07-30

- Require Sovereign Core 0.1.5 for composite responses and Session-owned
  optimistic view support.
- `/api/initiative/board` now uses Core's atomic
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
- Removed `POST /api/initiative/boards/unshare` and `InitiativeLogic.unshare_board`.
  No interface ever called the route - only its own tests did. Returning a
  board to private is Core's "stop using" on the channel carrying it, which
  now does so for relay channels too; `delete_board` never went through this
  path either, calling `Session.end_topic_sharing` directly.
  `_is_initiative_board_topic` went with it, having no other caller.

No wire or persistence change.

## 0.1.0a2 - 2026-07-26

- First release published to PyPI. `pip install s-initiative` now resolves,
  and with it S-Cockpit's optional `test-kanban` extra.
- Card file attachments: attach, download and remove, stored as
  content-addressed blobs that sync with the board.
- Optional desktop window (`pip install s-initiative[desktop]`), and CI now
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
  targets, explicit transitions/reactions, and versioned S-Cockpit facade.

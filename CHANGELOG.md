# Changelog

## Unreleased

- **There is a second face, the Mandate, and the initiative has dates.** The
  application had one surface because the board *was* the topic; it is not any
  more. A switch in the content area — not the shell bar, per U7 — moves
  between the board and the mandate, and the mandate face opens with the head
  region `DESIGN_INITIATIVE_UI.md` §5 describes: the objective, editable
  here for the first time in this application, and the four dates on one
  line. Opening still lands on the board, every time, and the face is not
  remembered across a reload: a remembered face means one link opens two
  different pages for two people.

  **Mandate, and not Initiative.** The design first named the face after the
  thing it shows. That fails here: the topic *is* the initiative and the shell
  bar says so above both faces, so "Board | Initiative" would give one word to
  a part and to the whole — the stutter §9 takes trouble to avoid between a
  face and its topic, arriving where a reader actually looks. A mandate is what
  was agreed, which is what the face holds, and reporting against one is part
  of holding it, so Assessed Impact sits under the name without strain. The
  face's own identifiers say `mandate` — `#mandate`, `renderMandate` — while
  `state.initiative` stays what the face is showing.

- **`planned_start`, `planned_end`, `actual_start` and `actual_end`.** Four
  optional ISO dates on the initiative, and content rather than records —
  two people who disagree about when it started have a divergence worth
  seeing rather than two private truths, so all four are in the divergence
  labels. Two commands and not one, because they are two acts:
  `/initiatives/set_dates` plans, and `/initiatives/claim_date` claims. A
  date nobody has said is absent from the data rather than stored empty,
  and clearing a claim removes it, because undoing a claim is not the same
  act as tidying a field. The claim's control arrives with the milestone
  strip; the command exists now so the face can say what was claimed.

- **The mandate face rebuilds itself whole and does not use
  `reconcileDOM`.** §8's decision, and the reason is one this codebase has
  already paid for: the helper needs a create/update pair per node type,
  this face will have seven, and a pair falling out of step draws the old
  thing until something forces a rebuild. So the face re-renders on every
  payload, skips the render when the payload JSON is unchanged — not when
  `revision` is unchanged, because peer liveness is merged into the
  snapshot after it is read — and returns early rather than emptying a box
  somebody is typing in. The board face keeps `reconcileDOM`, because
  drag-and-drop needs stable element identity across renders. Both halves
  are asserted from the page's side.

- **Every identifier that means the *application* now says "initiative" too.**
  The app id was already `initiative`; the names around it were not.
  `_kanban_container()` and `_kanban_containers()` (they return the
  `initiative_app` folder) are `_initiative_container()` and
  `_initiative_containers()`, `KANBAN_POSITION_POLICY` is
  `INITIATIVE_POSITION_POLICY`, the page's templates are
  `initiativeHeaderControls` and `initiativeControls`, the auto-adopt trace
  event is `initiative.adopt_all_incoming_changes_check`, and the suites are
  `test_initiative_new_logic.py`, `test_initiative_integration.py`,
  `test_initiative_guard.py` and `test_initiative_desktop.py`. S-Cockpit made
  the matching move on its side. This is the third naming rule, recorded in
  `DESIGN_INITIATIVE_UI.md` §9: the route path and the payload trio name the
  face, everything else names the topic, and the application segment, facade
  accessor and application-scoped identifiers name the application.
  `kanban_column`, `kanban_card` and "Kanban board" are untouched — a Kanban
  board is still what organises an initiative.

- **`config/initiative.example.json` started the server again.** It still set
  `"primary_application_id": "kanban"`, and Core's `ApplicationHost` rejects a
  primary id that names no active application, so the shipped example config
  failed at startup with `primary application 'kanban' is not active`. It now
  says `"initiative"`, matching the manifest.

- **Every identifier that means the topic now says "initiative".** `boards()`,
  `create_board`, `ensure_board`, `_selected_board`, `board_uuid` and the
  `/api/initiative/boards/*` routes were the half of the root rename that
  `DESIGN_INITIATIVE_UI.md` §9 deferred, and they are done: the commands are
  `/api/initiative/initiatives/*`, the wire key is `initiative_uuid`, and the
  payload is keyed `initiative` and `initiatives`. What decided the line was
  S-Team's own arrangement rather than a fresh preference — the route path and
  the payload-building trio name the *face*, everything else names the
  *topic*, which is why `GET /api/team/document` carries `team` and `teams`.
  So `GET /api/initiative/board`, `board_payload`, `board_snapshot`,
  `merge_board_observation`, `#board` and `renderBoard` all stay: they are the
  board face, and §1 says the board is a face. "Board" now means the Kanban
  board and nothing else.

- **The facade is at version 2.** Renaming `boards()` and its neighbours
  breaks a versioned contract, so it says so. S-Cockpit's matching constant
  moves in the same change; released apart, the lookup in Core's `host.py`
  refuses the facade rather than silently degrading.

- **The page's payload keys are asserted from the page's side.** A source scan
  parses `board_payload` for the keys it writes and requires every
  `state.<key>` the page reads to be one of them. §8 already recorded why —
  S-Team renamed keys and node types in lockstep and nothing failed while the
  page read `undefined` — and this is the check that would have caught it.

- **A remembered initiative selection resets once.** `selected_board_uuid`
  became `selected_initiative_uuid` in local application metadata. Same
  disposable-data position as the root rename, on data that was never shared.

- **The topic root is the initiative, not the board.** `kanban_board` is gone
  as a node type and `kanban_app` with it: the root is `initiative`, its local
  container is `initiative_app`, and the columns hang off the initiative
  exactly where they hung off the board. A Kanban board is how the next few
  days are organised and is not the thing being organised — the application
  could not say that while the board *was* the topic. `kanban_column`,
  `kanban_card`, `card_comment`, `card_attachment` and `agenda_item` are
  untouched. This lands first because every type in `DESIGN_INITIATIVE.md`
  hangs off that root and the rename only got dearer with each one added
  beside it. No migration: a node's content is attested by three hashes and a
  signature, so a stored tree cannot be re-typed by hand, and pre-release data
  directories are replaced rather than converted. Board of Boards' settings
  guard and the stand-in root type in Core's and S-Team's fixtures moved with
  it. See `DESIGN_INITIATIVE_UI.md` §9.

- **How the new fields are shown is written down.** `DESIGN_INITIATIVE_UI.md`
  specifies two faces — the board for the next few days, the mandate for what
  was agreed — the rule that puts a field on the face where its *act*
  happens rather than beside its own siblings, and the milestone strip that is
  the one place they touch. Proposed; only §9 is built.

- **The auto-adopt square left the header.** A setting is not a header
  control, and a glyph nobody can read without its tooltip is not a control
  at all: adoption policy is now a labelled row in the collaboration pane
  beside the queue it governs. The two card-scoped modes are all this
  application supplies — "always" and "never" are worded by Core and
  inherited, where three copies of that table had been drifting. The bar's
  label is "Initiative" rather than "Board", and the mark is a board with its
  column names. See Core's `DESIGN_VOCABULARY.md` and
  `DESIGN_UI_CONSISTENCY.md` U7 and U8.

- **S-Initiative says how an initiative is made, and needs no facades at
  all.** Its registration carries the noun, the boards a new one can be
  copied from, and one `make_board` covering blank, copy-and-rename and
  from-snapshot. Making the team or flow it names goes through Core's
  registry, so the facade lookup this application briefly took is gone again
  and `LINKED_APPLICATIONS` is down to two words.

- **An initiative's links are the shell's, and it can make what it
  names.** `.initiative-link` and the section above the board are gone; the
  team and the flows appear on the navigation row beneath the topic name,
  where S-Team's work appears too. `create_linked_topic` makes a team or a flow through that
  application's facade and names it in one act, from a template or a snapshot
  file, using the shell's dialog. A kind whose application is not running
  here is not offered rather than refused after asking, and
  `LINKED_APPLICATIONS` no longer carries anybody's route.

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

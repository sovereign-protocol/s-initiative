# Implementation plan — the mandate face

`DESIGN_INITIATIVE.md` says what an initiative is. `DESIGN_INITIATIVE_UI.md`
says how it is shown. Neither says in what order to build it, and the order
matters here because seven node types, a second face and a seam between the two
faces are not one change.

This is that order. Where it names a file or a function, that is the thing as it
stands today, not as the design imagines it.

---

## 0. Where this starts

**One section of the specification is built.** `DESIGN_INITIATIVE_UI.md` §9 —
the topic root is `initiative`, `kanban_board` is gone, the `board_uuid` /
`create_board` identifier sweep followed. Everything else is unwritten:
`initiative_need`, `initiative_section`, `initiative_clause`,
`initiative_intent`, `initiative_milestone`, `initiative_reality` and
`initiative_investment` appear in no source file in any of the four
repositories. The only field of the mandate face that exists is `objective`,
and it is not on this application's page at all — it is editable only from Board
of Boards, through the Cockpit's forwarding route.

**The suite is green.** 265 passed in `s-initiative`, 56s.

**The groundwork is not landed, and that is Phase 0.** All four repositories sit
on `fix/node-classes-groundwork` with the §9 work uncommitted — in
`s-initiative` alone, 926 insertions and 3,386 deletions unstaged, the four
`test_kanban_*.py` → `test_initiative_*.py` renames among them, and
`DESIGN_INITIATIVE_UI.md` itself still untracked. Seven node types added on top
of an unlanded four-repository rename is a conflict in every file this plan
touches.

**Another agent edits these repositories in parallel.** Re-check `git status` in
each one before starting a phase, not only before the first.

---

## 1. What every phase repeats

The mechanics are the same each time and are easier to get wrong by omission
than by error. Two checklists, one per node class.

### A decidable type

1. **`OWNED_NODE_TYPES`** in `logic.py`. Without it `_node()` returns `None` for
   every node of the type and every command answers "not found" — it gates on
   `owns_node`, which gates on this set.
2. **`DISPLAYED_DIVERGENCE_TYPES`**, per §6. A decidable node with no divergence
   rendering is one that silently keeps two truths.
3. **The `node_label` map** in `describe_peer_changes` — without an entry a
   divergence describes itself as "Item".
4. **`scalar_labels`**, one line per new text field, or the field diverges
   invisibly.
5. **Logic commands**: create through `session.create_child` with
   `session.next_child_order`, edit through `session.modify`, delete through
   `session.delete`, reorder through `session.move_child_to_parent_index`. The
   `create_column` / `rename_column` / `move_column` trio is the shape.
6. **Facade method, controller route, and the API list** in the `logic.py`
   module docstring — that list is the only inventory of the wire surface.
7. **A payload key** in `board_payload`.
8. **The page reads it as `state.<key>`**.
   `test_the_page_only_reads_payload_keys_the_logic_writes` scans for exactly
   that spelling and asserts the page reads nothing the logic does not write; a
   destructured read passes the test without being covered by it.
9. **`reactionTools(node)`** for the divergence lamp. It is already generic — it
   takes any node and returns nothing when the node needs no reaction.
10. **Snapshot export and import**, because content is what a template is made
    of.

### A record type

1. **`OWNED_NODE_TYPES` only.** Never `DISPLAYED_DIVERGENCE_TYPES` — §6's
   load-bearing half is that an observation draws no lamp.
2. **The author field is written from `self.user_profile().uuid`** and checked on
   delete. `delete_card_comment` is the shape, one `if` and a refusal.
3. **No `order`, no move command.** Sorted by `created_at`, appended, in time.
4. **Nothing in `_classify_incoming_node`.** Its default is adopt, and adopt is
   what "persisted" means here; only `agenda_item` opts out, because it is the
   one read-through type. Both new record types want the default.
5. **The page draws author and date always, and offers no edit affordance to
   anybody but the author** — absent, not disabled.
6. **Not in snapshots.** A template carries what a team agreed, never what
   somebody observed.

---

## 2. The phases

Each one ends somewhere you can stop.

### Phase 0 — Land the groundwork  *(committed, not landed)*

Commit and merge `fix/node-classes-groundwork` in all four repositories. Nothing
else here starts first.

Verify the way CI does, not the way the working tree does: CI installs
non-editable, so run the four suites from a throwaway venv rather than the `-e`
one.

**Done when:** the branch is on `main` in four repositories and four suites are
green.

**Where it got to.** All five working trees are committed on
`fix/node-classes-groundwork`, each after its suite passed — s-core 511,
s-initiative 265, s-team 213, s-flow 33, s-cockpit 75. Nothing is merged and
nothing is pushed, because the merge turned out to be the wrong operation:
`origin/main` is not behind the branch, it is a **rebased copy** of the
branch's own history under different SHAs. Shared commit subjects run
142/96/74/14/55 across the five repos, with 0–1 unique to main — the release
commits — and 11/16/11/3/12 genuinely new on the branch. So a merge tries to
reconcile the duplicated history against itself, which is where the conflicts
in `logic.py`, the page and `CHANGELOG.md` in every repo came from.

**When it is landed, rebase; do not merge, and do not squash.** A rebase onto
`origin/main` replays only the 53 new commits — probed in `s-initiative`, where
git correctly dropped the first as "patch contents already upstream" and then
wanted the second resolved by hand. Anything that takes the branch's tree
wholesale, a squash-merge included, reverts the release commits sitting on
main: the branch predates `Release Sovereign Core 0.1.9` and
`Release S-Initiative 0.1.0a5` and would undo their version bumps.

---

### Phase 1 — The second face, with nothing in it but the head  *(done)*

The switch (§3), the head region (§5), and the render regime (§8). No new node
types at all.

**Page** — `initiative.html`:

- A `<section id="mandate">` beside `<section id="board">` in `<main>`, and
  the two-item switch above both, at the content area's top left. Not the shell
  bar: §3 gives two independent reasons and `setAppActions` is gone.
- `loadBoard()` fetches once and renders the visible face. The board face keeps
  `reconcileDOM`; the mandate face full-renders.
- An `ephemeral` object in JS for which face is showing, which disclosure is
  open, and any half-typed composer — keyed by node uuid, reapplied on render.
  Not in the DOM. The 1500ms poll closes anything the DOM is trusted to
  remember.
- The activeElement guard, copied from S-Team's `load()`: return early when the
  focus is inside the face and is a contenteditable, input, textarea or select.
- Skip the render when the payload JSON string is unchanged. Do not gate on
  `revision` — peer liveness is merged into the snapshot after it is read.
- **The face is never remembered across a reload.** §1: opening lands on the
  board, every time.

**Logic** — `logic.py`:

- Four date fields on the `initiative` node: `planned_start`, `planned_end`,
  `actual_start`, `actual_end`. Optional, ISO date, absent until written.
- `set_initiative_dates(initiative_uuid, planned_start, planned_end)` — the
  planning act, on this face.
- `claim_initiative_date(initiative_uuid, field, value)` for `actual_start` and
  `actual_end` — a separate command because it is a separate act, and an empty
  value undoes a claim rather than tidying a field. Phase 7 puts the control on
  the strip; the command exists from here so the face can show what was claimed.
- `objective` becomes editable here. The route exists already
  (`/api/initiative/initiatives/set_objective`) and Board of Boards keeps its own
  edit of the same field.
- Add all five fields to `scalar_labels`.

**Tests:** the dates round-trip; a claim with an empty value clears; the page
reads no payload key the logic does not write; a source scan asserting the
mandate face does not call `reconcileDOM` and the board face still does.

**Done when:** you can switch to the mandate face, see the objective and the
four dates, and edit the objective and the two planned dates without the poll
eating a keystroke.

**Done.** 283 tests pass, up from 265. The 18 new ones cover the dates
round-tripping, one end planned without disturbing the other, a cleared date
leaving the field absent rather than empty, a non-ISO date refused, a claim
taken back, the claim command refusing a planned field, both routes reaching
their commands, a two-client disagreement on `actual_start` described as
`Started` rather than merged, and — from the page's side — that the mandate
face rebuilds while the board face patches, that it will not rebuild under the
caret, that it skips on payload JSON and never on `revision`, that the switch
is inside `<main>`, and that no storage remembers the face.

Two things worth knowing for the phases after this. `_Runtime.notify_change`
in `tests/test_ownership_guards.py` had never taken Core's `change_kind`
argument, because every test in that file had only ever exercised a refusal;
the first success path through a controller found it. And `.board` is
`display: flex`, so `[hidden]` needs a rule of its own to outrank the layout —
the same will be true of any face added later.

---

### Phase 2 — Needs  *(done)*

`initiative_need`, region 2. One flat decidable type with no children, chosen
second because it exercises the entire content pipeline — create, edit, reorder,
delete, divergence lamp, composer — with nothing nested inside it.

- Fields: `text`, `beneficiary_label`, `beneficiary_actor_uuid` (optional),
  `order`.
- The beneficiary rule inverts S-Team's: the **label is the primary fact**,
  because the beneficiary of a need is often not an actor in this system at all.
  Where `beneficiary_actor_uuid` resolves to an actor this client holds, draw the
  live name **and** the label — the two disagreeing is information, not an error.
- `SovereignUI.addComposer` at the end of the list,
  `SovereignUI.reorderableList` for order, `SovereignUI.editableText` for the
  text. Core owns all three.

**Tests:** the CRUD; ordering after a reorder; a two-client divergence on `text`
showing a lamp; an ownership guard test that an `initiative_need`-typed node
outside an initiative is refused.

**Done when:** a need can be written, reordered, and shown diverging between two
clients.

**Done.** 299 tests pass, up from 284. Fifteen new ones: the CRUD and the
ordering, empty text refused, one field updated without disturbing the others,
the optional actor uuid absent until said and removed when cleared, an
unresolvable actor uuid still accepted, the four routes reaching their
commands, a `initiative_need`-typed node outside an initiative refused, a
two-client divergence on `text` labelled `Need`, snapshots carrying needs and
older snapshots without them still restoring, and — from the page's side — the
shared composer/reorder/editor, the lamp, and the label drawn beside the live
name rather than replaced by it.

**Checklist item 7 did not apply.** A need is a plain child of the initiative,
so it arrives inside `state.initiative` exactly as a column does and needs no
key of its own in `board_payload`; the page resolves the beneficiary from
`state.users`, which already carries every actor this client knows. The item is
for types whose display data has to be *prepared* — `comments_by_card`,
`links` — and the distinction is worth keeping in mind for the phases below:
Resources (Phase 6) will need one, because "whoever holds the topic" is not in
the subtree.

---

### Phase 3 — The Approach

`initiative_section` → `initiative_clause`, region 5. Two levels, the same shape
as a Team Agreement's sections and clauses, and the first phase with nesting.

- A new initiative is **seeded** with Strategy, Plan, Risks, Conditions for
  success — ordinary content from the moment they exist, so renamable,
  reorderable, deletable, and a fifth is added by the same composer. Nothing
  marks the four as special.
- The region **arrives collapsed**. It is the longest and least often changed,
  and S-Team learned in use that leading with the long text buries the team
  behind its own text.
- Seeding is written in `_create_initiative_node`, beside `DEFAULT_COLUMNS`.
- **This is the phase where snapshots and copies stop being free.**
  `export_snapshot` and `_import_snapshot_initiative_content` carry columns and
  cards and nothing else today; a copied or snapshotted initiative would lose its
  Approach silently. Both grow a `sections` branch here.

**Tests:** the seed exists on a new initiative and on one made from a snapshot; a
section renamed and a clause added round-trip through export and import; a clause
is refused under anything but a section or an intent.

**Done when:** a new initiative opens with four seeded sections you can rewrite,
and a snapshot of it restores them.

---

### Phase 4 — Intended Impact

`initiative_intent`, region 3, labelled **Intended Impact**. Exactly one per
parent, holding `initiative_clause` children — the type already built in
Phase 3. See §3 decision 2 for why it is not called Expected Impact.

- **The region has no "add intent" control**, only add-clause. That is not a
  validation rule, it is the absence of a surface: there is nowhere in the
  interface for a second one to come from, which is the only honest way to state
  the type rule.
- **The objective is not repeated here.** The objective is the aim in one
  sentence and it is everybody's; the Intended Impact is the specific changes
  that aim is supposed to produce, in clauses, and it is what Assessed Impact
  answers. The head region states the first, this region states the second, and
  neither draws the other's text.
- Create the intent node lazily, inside the add-clause command, rather than
  seeding it with the initiative. An initiative that has never stated an
  intended impact should not carry an empty node that syncs, and lazy creation
  keeps "exactly one" in one place.
- A second intent arriving from a peer is a divergence and renders as one. Do
  not silently merge or drop it.
- The strip's read-only line in Phase 7 says **`Intended:`**, not `Expected:`.

**Tests:** adding two clauses creates one intent node; the payload never carries
two; the page contains no add-intent control (source scan).

**Done when:** the initiative states the changes it means to produce, in
clauses, distinct from its one-line objective.

---

### Phase 5 — Assessed Impact

`initiative_reality` under the initiative, region 6. **The first record type**,
and the phase that establishes the record treatment the next two reuse.

- Fields: `author_actor_uuid`, `text`, `recorded_at`.
- Anybody holding the topic may write **their own**; only its author may delete
  it. Many of them, and none of them wins — the divergence machinery is not
  involved, and the absence of a lamp is the point.
- §6's treatment: author and date always shown, no reorder, no edit affordance
  for anybody else.

**Tests:** an author guard — a second client cannot delete the first's
observation; the node type is in `OWNED_NODE_TYPES` and **not** in
`DISPLAYED_DIVERGENCE_TYPES` (assert both, the second one explicitly); a reality
survives its author going unreachable.

**Done when:** two clients can each record what they think happened and both
observations stand.

---

### Phase 6 — Resources

`initiative_investment`, region 4. The second record type, and the one with a
chain.

- Fields: `actor_uuid`, `availability`, `previous_uuid` (empty for the first),
  `recorded_at`.
- **No roster.** The region lists whoever holds the topic — Session already knows
  — each with the head of their chain, or "no availability recorded".
- **Only your own line has the composer.** The guard refuses to write somebody
  else's, and the surface must not offer what the guard will refuse.
- Earlier commitments sit behind a disclosure on your own line, because the chain
  is how "two days a week became one" reads as a change rather than a correction.
- An actor whose replica is unreachable shows their last-seen commitment, **said
  as last seen**.

**Tests:** the guard refuses a write naming another actor; the chain reads back
in order; an unreachable peer's head is still drawn and is labelled as last seen.

**Done when:** each member's availability is on the page, written only by them.

---

### Phase 7 — Milestones, and the seam

`initiative_milestone` (region 1) and the strip on the board face (§4). Last,
because a milestone contains an intended impact (Phase 4) and realities
(Phase 5),
and because it is the only phase that touches both faces.

- Fields: `title`, `order`, `planned_at`, `reached_at`.
- Region 1 sits **first** on the face, ahead of Needs — it is the region the
  strip is a window onto, and landing next to the thing you just acted on from
  the other face is what makes the two feel like one initiative.
- The strip shows two zones and four claims: the bookends from the initiative's
  own dates, and the current milestone. Planned dates are shown and **not
  editable there**.
- **The current milestone is the lowest `order` with no `reached_at`.** Nothing
  more. Not a date comparison — §7 of the type doc refuses dependencies, a
  critical path and a derived schedule, and reading `order` respects all three.
  Reaching one out of order is allowed and the strip moves on.
- After a milestone is reached with an intended impact and no reality of
  **yours**,
  the strip offers `Record what happened`, once. It asks and does not insist, and
  it says nothing once the person reading it has written theirs — what is missing
  is their observation, not a field's value.
- Empty cases: "No milestones yet" with a link to the other face; all-reached
  keeps the bookends and offers `actual_end`.
- Not a progress bar, not a count, not date arithmetic.

**Tests:** the current milestone is derived from `order` and not from dates;
reaching out of order advances to the next unreached; the prompt disappears for
the author who wrote a reality and stays for one who has not; the no-milestones
strip renders and links to the other face.

**Done when:** the board opens on a strip that shows what is next and what it is
supposed to change, and marking it reached asks you what happened.

---

## 3. Three decisions to take before the code

**1. What a copy carries.** `copy_initiative` calls `session.copy` on the whole
subtree, so from Phase 5 a copied initiative would carry somebody's observations
and somebody's committed availability, with their author uuids on them. That is
wrong on the same grounds the type doc gives for everything else about records:
they are one person's, and a new initiative is nobody's yet. **Recommendation:
strip record nodes after the copy**, and state it in the type doc beside the
snapshot rule. Decide it before Phase 5, not after.

**2. Expected Impact is renamed Intended Impact, and the type with it.**
Region 3 and the `objective` both answer "what is this meant to change", and
naming one of them *Expected* made the overlap worse: it reads as a forecast of
the objective, which is a thing already said. **Intended Impact** names an act
instead of a prediction — these are the changes we mean to produce — and the
pair *intended / assessed* reads as intent against observation, which is what
§6 of the type doc says the two regions are. The node type follows the label to
`initiative_intent`: nothing is built, so it is free today and it is drift
tomorrow, and the strip's read-only line says `Intended:` for the same reason.

The cost is one split with the blueprint, and it is worth paying. Both
`Domain-Driven-Design.md` §3 entries — Initiative and Bet — say *Expected
Impact*, and S-Team's already-built Bet keeps it. That is correct there: a Bet
is signals, then a decision, then what the decider expects to follow from it,
and a forecast attached to a decision genuinely is an expectation. An
initiative's is a statement of intent held by everybody on the topic. The
blueprint collapsed two acts under one noun; this separates them, and the type
doc should say so where it maps the vocabulary.

**Also settled here: the objective stays.** Merging it into the first clause of
the Intended Impact would give Board of Boards no tagline to draw without a
"the first clause is the title" rule, which is exactly the kind of implicit
structure the type doc refuses elsewhere. One sentence and a set of clauses are
different grains of the same question, and both are worth having.

**Lazy or seeded.** Recommended lazy above, in Phase 4. The alternative —
seeding an empty intent with every initiative — makes "exactly one" true by
construction, at the cost of a node that syncs and says nothing. Worth a
moment's thought because it is awkward to change later.

**3. Where the date control lives.** Five date fields across the two faces, and
Core's `SovereignUI` has no date primitive. **Recommendation: build it locally
first** and promote it to Core when a second application needs one — a shared
control invented for one caller is a guess about the second. Note the debt in
`s-core/DESIGN_UI_CONSISTENCY.md` when it is taken.

---

## 4. Watch items

**Two render regimes in one page.** §8 requires it and gives the reason, but the
page will hold both from Phase 1. Keep the seam at one function — `loadBoard()`
dispatches, and neither renderer reaches into the other's DOM.

**The full-render/patch trap this codebase has already met.** The reason the new
face full-renders is that `reconcileDOM` needs a create/update pair per node type
and seven new types is seven new ways for a pair to fall out of step. If a patch
function is ever added to the mandate face, it arrives with its twin or it
does not arrive.

**Payload growth.** `board_payload` is fetched every 1500ms and already carries
every peer's tree. Seven node types are added to it across these phases. The page
skips the *render* when the JSON is unchanged; nothing skips the *fetch*. Not a
reason to change anything now, and a reason to watch it after Phase 7.

**Every phase adds to two type sets that are read in four places.** The
checklists in §1 exist because omitting one of them fails quietly rather than
loudly.

---

## 5. Not in this plan

From §10 of the UI doc and §7 of the type doc, and not by oversight:

- No third face for the records. No "History".
- No rollup, dashboard, chart, burndown, or percentage reached. Board of Boards
  is where portfolio questions are asked, and it gets nothing new here.
- No Gantt and no drawn timeline.
- No `?face=` route parameter, and no linking to a section.
- No per-member view of anything but what that person wrote.
- No dependencies between milestones, no critical path, no derived schedule.
- No money and no assets, and no `kind` field waiting for them.
- No migration. Stored trees are disposable, and every node carries four
  attestations that a hand-edited type string would contradict.

# Interface — S-Initiative

Status: **built.** `PLAN_INITIATIVE_FACE.md` records the implementation order.

`DESIGN_INITIATIVE.md` says what an initiative is. This says how it is shown.

Where this names something Core owns, Core is the source:
`s-core/DESIGN_UI_CONSISTENCY.md` (U1–U8) and `s-core/DESIGN_VOCABULARY.md`.
Where it names S-Team, that application has already solved the same problem
once and `s-team/DESIGN_UI.md` records what it cost.

---

## 1. Two faces

**The board** is the next few days. **The mandate** is what was agreed.

This is `DESIGN_INITIATIVE.md` §1 taken literally — "A Kanban board is how the
next few days of it are organised. It is not the initiative" — and it is the
only sentence needed to justify a second surface. The application has had one
face because the board *was* the topic. It is not any more.

**The second face is not called "Initiative", and that is a correction.** This
document first named it after the thing it shows, on the rule that doing so
needs no explanation. It does not work here: the topic *is* the initiative, the
shell bar already says so above both faces, and a switch reading
"Board | Initiative" gives one word to a part and to the whole. That is the
stutter §9 takes trouble to avoid between a face and its topic, arriving in the
one place a reader actually looks.

**Mandate**, because the face is where you agree (§2) and a mandate is what was
agreed: the Intention, whose needs it answers, what it
costs, and how it will be gone about. Assessed Impact sits under it without
strain — reporting against a mandate is part of holding one, which is more than
"Definition" could carry, and that was the objection to *that* name. Not
"Charter" or "Brief", which are still words a reader has to be taught; a
sovereign organisation's vocabulary already has this one.

**The face's own identifiers say `mandate`, and everything else still says
`initiative`.** `#mandate`, `renderMandate` and `ephemeral.face` are the face;
`state.initiative` is what the face is showing. That is §9's rule applied to
the second face, and it happens to resolve the ambiguity the first name left in
the page.

**Opening lands on the board, every time.** Not the last face used, and not
conditioned on how full the initiative is. A remembered face means one link
opens two different pages for two people, and the board is where the work is.
The one case that argues the other way — a just-created initiative, whose board
is three empty columns while its Approach already holds four seeded sections —
is answered by the strip in §4 rather than by an exception on day one.

---

## 2. Which face a thing is on

Not by node type. **By the act.**

The mandate face is where you agree. The board face is where you act. Sorted
that way, most of `DESIGN_INITIATIVE.md` §3 lands where you would expect and
three fields do not:

| Field                                                                                                  | Face                                                       | Why                                                          |
| ------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------- | ------------------------------------------------------------ |
| `objective` (shown as Intention), `initiative_need`, `initiative_section`, `initiative_clause`         | the mandate                                                | agreeing                                                     |
| `initiative_milestone.title`, `.order`, `.planned_at`, `.intention`                                    | the mandate                                                | planning                                                     |
| `planned_start`, `planned_end`                                                                         | the mandate                                                | planning                                                     |
| **`actual_start`, `actual_end`, `reached_at`**                                                         | **the board**                                              | a claim made on the day it is true                           |
| `initiative_reality`                                                                                   | both — written on the board, read on the mandate           | observing what just happened, reading what was observed      |
| `initiative_investment`                                                                                | the mandate                                                | committing what you have                                     |
| `topic_link`                                                                                           | neither — Core's, in the bar (U6, U7)                      |                                                              |

The three dates in bold are content by class, and §3 is right about why: two
people who disagree about when it started have a divergence worth seeing rather
than two private truths. But their *act* is not agreement, it is a claim, and
nobody navigates to a reference page to say "we got there". The data is
definition data; the control is on the board.

> **A field belongs to the face where its act happens, not to the face where
> its siblings live.**

That rule is the whole of this section, and it is what stops the second face
from being a filing cabinet.

---

## 3. The switch

**[DONE]** **In the content area, at its top left, above the board.** Two
items, the current one marked.

**Not in the shell bar.** U7 gives two independent reasons and either is
enough. The navigation row beneath the topic name is destinations *among
topics*, and the reason it can be uniformly clickable is that every item on it
is one class of thing; a row where some items change topic and some change view
teaches nothing about either. And `setAppActions` is gone — the bar holds no
application controls at all, which is why S-Initiative's auto-adopt square had
to move into the collaboration pane. A face switch is an application control.

**Rejected: one scrolling surface**, the initiative's sections collapsed above
the board, which is the S-Team pattern taken literally and needs no switch at
all. It fails on mechanics rather than taste: the board is a horizontal,
full-height, drag-and-drop surface, and a card cannot be dragged into a column
that has scrolled off the top. The staleness that a switch costs is paid for in
§4 instead.

**Rejected: the initiative as a pane.** Core's panes hold what is being worked
out and are entered transiently. This is the longer of the two documents and
the one with several regions in it.

**Not in the URL, and no `?face=`.** `?topic=` is the only parameter Core
composes routes from (U6), and a second one would have to be understood by
every surface that builds a link to an initiative — the Cockpit, the context
row, S-Team's Initiatives and Flows list. What that would buy is linking
somebody to a section, which nothing yet wants.

---

## 4. The seam — the milestone strip

One strip, above the columns, on the board face. It is the only place the two
faces touch, and it exists because a reference page nobody visits goes stale
and takes the whole expected-versus-assessed apparatus down with it.

**The milestone is the carrier** because it is the one object that is both a
plan and a fact. Everything else on the mandate face is one or the other.

### What it shows

One compact status line to the right of the face switch, separated from it by a
vertical rule:

```
Start: 12 Mar ⇒ Intention: two teams renew ⇒ End: 30 Jun |
Last: 8 Apr: Prototype ⇒ Intention: three teams pilot ⇒ Next: 30 Apr: Pilot
```

The initiative group reads `Start ⇒ Intention ⇒ End`; the milestone group reads
`Last ⇒ Intention ⇒ Next`. Actual dates replace planned dates where they exist.
The milestone Intention comes from the single field on the next milestone. Any
missing fact is omitted, including its label and separator. The entire line is
read-only and has no action buttons; planning, claims and assessments happen on
their respective surfaces.

### The current milestone is the first unreached one in order

Defined that way, and nothing more: the lowest `order` with no `reached_at`.

This is derivation from an authored field, not a schedule. §7 of
`DESIGN_INITIATIVE.md` refuses dependencies between milestones, a critical path
and a derived schedule, and this respects all three — the strip reads `order`,
which a human set by dragging, and never a date. A milestone whose `planned_at`
is in the past is still just the next one; the strip says the date has passed
and draws no conclusion about the others.

**Reaching one out of order is allowed** and the strip simply moves on to the
next unreached one. The alternative — refusing, or reordering silently — would
make the strip an authority over a list it does not own.

### The empty cases

Absence is quiet: no placeholder is drawn for a missing date, Intention or
milestone. If no status fact exists, the status line itself is hidden.

### What the strip is not

Not a progress bar, not a count of reached-out-of-total, not a burndown, not
date arithmetic of any kind. Four facts and four claims. A rollup is a portfolio
question and Board of Boards is where portfolio questions are asked.

---

## 5. The mandate face

One document, with Intention fixed at the top and five regions beneath it:

|      | Region                    | Holds                                                                     | Class            |
| ---- | ------------------------- | ------------------------------------------------------------------------- | ---------------- |
| head | **Intention and dates**   | `objective` (displayed as Intention), the four dates                      | content          |
| 1    | **Milestones**            | `initiative_milestone`, each with one Intention and its realities        | content + record |
| 2    | **Needs**                 | `initiative_need`                                                         | content          |
| 3    | **Resources**             | who holds the topic, each with their `initiative_investment`              | record           |
| 4    | **Approach**              | `initiative_section` → `initiative_clause`                                | content          |
| 5    | **Assessed Impact**       | the initiative's `initiative_reality`                                     | record           |

**The head is not a disclosure.** One Intention field and four dates, always
open, directly under the page's own rule. It is labelled **Intention** and asks
"What is this initiative meant to change and for whom?" It is everybody's
(§3), and a caret in front of it would suggest there is a version of this page
on which the Intention is not the first thing said. The dates read as
one line — planned, and claimed where claimed — with the same treatment the
strip uses, so the two faces do not describe the same four fields in two
vocabularies.

**Milestones first**, ahead of Needs, because it is the region people come back
to and the region the strip is a window onto. Landing next to the thing you just
acted on from the other face is what makes the two feel like one initiative.

**Approach is second to last and arrives collapsed.** It is the longest region
and the least often changed, and S-Team learned in use that leading with the
long text "buried the team behind its own text". Its four seeded sections —
Strategy, Plan, Risks, Conditions for success — are ordinary content from the
moment they exist (§3), so they are renamable, reorderable and deletable, and a
fifth is added by the same composer. Nothing marks the four as special, because
nothing about them is.

**Resources before Approach**, so the two regions about what this costs and who
is carrying it sit with the milestones and needs above them, and the reference
text falls to the bottom beside the retrospective. It also puts a record region
between two content regions, which is where §6's treatment difference does the
most teaching.

**Assessed Impact is last** because it is empty for most of an initiative's life
and grows at the end. A region that fills up late belongs where a reader finds
it by scrolling deliberately.

### Members, and what Resources draws

There is no roster (§4 of the type doc). Resources lists **whoever holds the
topic**, each with the head of their `initiative_investment` chain, or "no
availability recorded".

- Your own line has the composer. Nobody else's does — §3's guard enforces it,
  and the surface must not offer what the guard will refuse.
- Earlier commitments are behind a disclosure on your own line: the chain is how
  "two days a week became one" reads as a change rather than a correction, and a
  chain nobody can see does not do that.
- An actor whose replica is unreachable shows their last-seen commitment, **said
  as last seen**. A persisted copy of somebody else's record is a fallback and
  never a source of truth.

### Needs, and the beneficiary

`beneficiary_label` is drawn as the primary fact, because §3 inverts S-Team's
name rule for a stated reason: the beneficiary of a need is very often not an
actor in this system at all. Where `beneficiary_actor_uuid` resolves to an actor
this client holds, the live name is drawn and the label becomes what it was
called here — shown, not hidden, since the two disagreeing is information.

### Intention is one field

The initiative uses its existing `objective` field as Intention; there is no
separate Intended Impact region or clause collection. Each milestone likewise
has one editable `intention` text field. Assessments remain append-only and may
still be many, because perspectives on what happened are records rather than a
second agreed Intention.

---

## 6. Content and record, drawn apart

§2 of the type doc is a rule about who may write. It has to be legible without
reading the doc, so the two get different treatments and no region mixes them
without a visible break.

|                  | Content                                                      | Record                              |
| ---------------- | ------------------------------------------------------------ | ----------------------------------- |
| Edited in place  | yes — click, commit on blur or Enter, revert on Escape       | never                               |
| Author shown     | no                                                           | always, with a date                 |
| Added by         | a composer at the end of the list                            | a composer only your own line has   |
| Deletable by     | anybody holding the topic                                    | its author only                     |
| Divergence       | lamp and reaction control                                    | none — there is nothing to diverge  |
| Reordered        | yes, where it has `order`                                    | never — appended, in time           |

The absence of a divergence lamp on a record is the load-bearing half. An
observation cannot conflict with another observation, and drawing the same lamp
on both would teach that it could. This is the treatment S-Team already uses for
trustee realities and this application already uses, by feel, for a card
comment; it is being written down rather than invented.

**No record shows an edit affordance to anybody but its author** — not disabled,
absent. A greyed-out control on somebody else's observation says the system
considered letting you rewrite it.

**Every new decidable type needs adding to `DISPLAYED_DIVERGENCE_TYPES`** in
`logic.py`, which today holds the three board types and `topic_link`. A
decidable node with no divergence rendering is one that silently keeps two
truths — which is the failure the class exists to prevent.

---

## 7. What the board face keeps

Everything it has: the columns, the cards, comments, attachments, the
participant picker, ghost columns, the adopt tools, the agenda in Core's pane.
Nothing on it moves except `objective`, which stops being a tagline edited in
place on the board and becomes the Intention at the head of the other face. The
storage name stays because Board of Boards reads it; only its presentation and
where it is written change.

The strip in §4 is the one addition.

---

## 8. Rendering

**[DONE]** **The mandate face re-renders fully, guarded, and does not use
`reconcileDOM`.**

This is a decision against the pattern the board face uses, and the reason is
already recorded twice. `reconcileDOM(parent, dataItems, keyFn, createFn,
updateFn)` needs a create/update pair per node type; the board has two and the
mandate face adds several. S-Team considered copying the helper for five
new pairs and refused, naming the failure exactly: "a pair falling out of step
is a silent bug class this codebase has already met." It has — a full-render
function updated without its patch twin draws the old thing until something
forces a rebuild.

So the mandate face takes S-Team's arrangement wholesale:

- Full re-render of the region on every payload.
- `render()` returns early when `document.activeElement` is inside the document
  and is a contenteditable, input, textarea or select — otherwise the 1500ms
  poll empties the box mid-word.
- **No editable surface outside the guarded container**, or if one must exist,
  built once and re-attached rather than rebuilt per render.
- **Ephemeral UI state lives in JS, not in the DOM** — which section is open,
  which chain is expanded, a half-typed composer — keyed by node uuid and
  reapplied on render. A poll that closes what you opened is what happens
  otherwise, and DOM patching is not the fix for it.
- Skip the render when a JSON string of the payload is identical to the last.
  Do **not** gate on `revision`: peer liveness is merged into the snapshot after
  it is read, so transport changes may not advance it and the indicators would
  freeze.

The board face keeps `reconcileDOM`, because drag-and-drop needs stable element
identity across renders and it already has its pairs. The two faces render
differently for a reason that can be stated in one sentence each, which is the
condition for it not being drift.

**The payload keys are a contract with the page and are not the node types.**
S-Team renamed both together once, in lockstep with its tests, and nothing
failed while the page read `undefined` and drew an empty document beside a team
that was there the whole time. Whatever the mandate face reads, the suite
asserts from the page's side.

---

## 9. Before any of it — the root type

**[DONE]** The topic root is `initiative`. `kanban_board` is gone as a node
type, and the local container it hangs under is `initiative_app` — the name
§1 of the type doc gave it.

This came first because every node type in this document hangs off that root,
and the rename got more expensive with every field added beside it. What it
touched: the type string and the kind set in `logic.py`, the divergence label
map, the topic-root exclusion in the move description, the peer-node check in
`initiative.html`, Board of Boards' settings guard, and fixtures in all four
repositories that used `kanban_board` as a stand-in root type. `kanban_column`,
`kanban_card`, `card_comment`, `card_attachment` and `agenda_item` are
unchanged — the board is still a Kanban board, it is just no longer the topic.

**No migration, and stronger than a preference.** §7 of the type doc says the
data directories are disposable; the stored trees make that the only option.
Every node carries `content_hash`, `state_hash`, `base_hash` and an ed25519
`revision_signature` over its own content, so a stored tree cannot be re-typed
by hand at all — a substituted type string would leave four attestations
disagreeing with the data they attest to, which is the divergence machinery's
input rather than a migration. Existing session files under `data/` and
`testing/` hold a root type nothing recognises and are replaced by making new
initiatives.

**The identifiers followed separately, and the rule is the codebase's own.**
`board_uuid`, `create_board`, `boards()`, `ensure_board` and the
`/api/initiative/boards/*` routes were left behind here, because bundling them
would have put a route rename and a controller-facade-page sweep inside the
change that had to land before anything else could. They have since moved, and
what decided their names was not invented for the occasion:

> **The route path and the payload-building trio name the *face*. Every other
> identifier, and every payload key, names the *topic*.**

S-Team already works this way and had already met the case that argues about
it. Its topic and its application share a noun, so `GET /api/team/team` would
have stuttered; it reads `GET /api/team/document` and builds it with
`document_payload`, `document_snapshot` and `merge_document_observation`, while
the payload those carry is keyed `team` and `teams` and the commands are
`/api/team/teams/*`. S-Flow needs no such care - `flow` and `process` differ
already - and lands in the same place: `GET /api/flow/process`,
`process_payload`, `/api/flow/processes/*`.

So S-Initiative keeps `GET /api/initiative/board` and keeps `board_payload`,
`board_snapshot` and `merge_board_observation`, because §1 says the board is a
face and that route reads it. Everything the payload carries is an initiative:
`initiative` and `initiatives` are its topic keys, the commands are
`/api/initiative/initiatives/*`, and the wire key is `initiative_uuid`. In the
page the same seam runs between `#board`, `renderBoard` and `loadBoard` - which
are the face - and `state.initiative`, which is what the face is showing.

**"Board" now means the Kanban board and nothing else.** Where it survives it
is the columns and the cards: the board face, the `kanban_column` and
`kanban_card` types, "not part of a Kanban board", and S-Cockpit's product name
Board of Boards, whose tiles are drawn by `bob-board*` classes shared with the
team and flow tiles - those are tile styling and name no topic at all.

**A payload key rename has to be caught from the page's side**, which §8 says
and which S-Team learned the expensive way. Both pages now carry a source-scan
test that parses the payload builder for the keys it writes and asserts that
every `state.<key>` the page reads is one of them, so half a rename fails in
the suite instead of drawing an empty surface.

**What this cost that is not cosmetic:** the facade is a versioned contract,
so renaming `boards()` and its neighbours moved
`INITIATIVE_FACADE_API_VERSION` to 2, and S-Cockpit's matching constant with
it - the two must be released together or the lookup in Core's `host.py`
refuses the facade. Two local metadata keys changed with their accessors
(`selected_board_uuid`, and S-Cockpit's `board_settings`), so a remembered
selection and the per-tile expand/collapse settings reset once. That is the
same disposable-data position taken above, on data that was never shared.

**[DONE] The application is named `initiative` too, and the `kanban` segment
was wrong.** S-Cockpit's route segment is now `/api/cockpit/initiative/*`, its
accessor `_initiative()` with the `self.initiative` property beside `_team()`
and `_flow()`, and its forwarding methods `react_to_initiative_node`,
`create_initiative_agenda_item` and `set_initiative_auto_adopt`.
S-Initiative's are `_initiative_container()`, `_initiative_containers()` and
`INITIATIVE_POSITION_POLICY`; the page's templates are
`initiativeHeaderControls` and `initiativeControls`; the suites are
`test_initiative_*.py`; and S-Cockpit's optional extra is `test-initiative`.

**The segment's defence did not survive reading the code.** The argument for
keeping `kanban` was that it names the facade the call is forwarded to rather
than the application — which would hold only if there were a facade by that
name. There is not. Core's `_ApplicationFacadeRegistry` is keyed by
`facade.application_id` and nothing else, `_kanban()` looked its facade up
under `INITIATIVE_APPLICATION_ID`, and `_collaboration_context` mapped
`INITIATIVE_APPLICATION_ID` to `self._kanban` — the key and the value
disagreeing inside one dict literal. `kanban` named nothing that exists.
`/api/cockpit/team/teams/delete` and `/api/cockpit/flow/processes/delete` show
the shape it belongs to — the application, then the topic it acts on — so
`initiative/initiatives` is the stutter `team/teams` already accepted here,
and it is not the stutter §9 avoids above: that one was a *face* route, and
this is a forwarding namespace.

**So there is a third rule, beside the two above.** The route path and the
payload-building trio name the *face*; every other identifier and payload key
names the *topic*; and **the route's application segment, the facade accessor
and the application-scoped identifiers name the *application***. The board
vocabulary is untouched by all three: `kanban_column`, `kanban_card` and
"Kanban board" stay, because a Kanban board is still what organises an
initiative. Core's `assertNotIn("kanban", source)` boundary guard also stays —
it now guards Core against learning those node types, which is what it was
always for.

---

## 10. What this deliberately does not do

**No third face for the records.** Realities and investments are neither
definition nor day-by-day, and a "History" face would collect them. It would
also separate an observation from the intent it answers, which is the one
thing that makes it readable. They sit under what they answer, drawn as records.

**No rollup, no dashboard, no chart.** No burndown, no percentage reached, no
count of milestones done. Every one of those is a claim about the whole that
nobody wrote, and this application's answer to "how is it going" is a list of
observations with authors on them.

**No Gantt and no timeline drawing.** Milestones are an ordered list with a date
each. A drawn timeline implies the dependencies §7 refuses, and would have to
invent a start for each one.

**No linking to a face or a section.** §3's reasoning: a second route parameter
would have to be understood by every surface that builds a link to an
initiative.

**Nothing is required to move on.** No milestone must be reached before the next
is, no Intention must be written before a milestone, no reality before an
initiative ends. The strip asks; nothing blocks.

**No per-member view of any of it.** One Intention and it is everybody's (§3),
one Approach, one set of milestones. The only thing on either face that is
per-person is what that person wrote: their investment, their observations.

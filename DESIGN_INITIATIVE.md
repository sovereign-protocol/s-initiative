# Initiative — what the thing is

Status: proposed, except for the links in §3, the `initiative` root itself
and its four dates, which are built. The seven node types below have no code
against them yet; `PLAN_INITIATIVE_FACE.md` is the order they arrive in.

Where these are shown is `DESIGN_INITIATIVE_UI.md`: two faces, the board and
the initiative, and the milestone strip that is the one place they touch.

An initiative is a piece of work a team has decided to do: who it is for, what
it is meant to change, how it will be gone about, when, and what actually
happened. A Kanban board is how the next few days of it are organised. It is
not the initiative, and until now this application had no way to say so — the
board *was* the topic, and the only thing on it that was not a card was a
one-line objective added so that Board of Boards had something to show.

This records the shape that replaces it. Where it names something already
built, the built thing is the source; everything else is proposed.

---

## 1. The tree

```
initiative_app "S-Initiative"          local folder, never shared
└── initiative                         ← THE TOPIC
    ├── kanban_column → kanban_card → card_comment / card_attachment
    ├── agenda_item
    ├── topic_link                     the team it belongs to, the flows it runs
    ├── initiative_need
    ├── initiative_section → initiative_clause          the Approach
    ├── initiative_intent → initiative_clause           Intended Impact
    ├── initiative_milestone
    │     ├── initiative_intent → initiative_clause
    │     └── initiative_reality
    ├── initiative_reality
    └── initiative_investment
```

`kanban_board` is gone as a node type. The topic root is the initiative, and
the columns hang off it directly, exactly where they hung off the board. One
initiative holds one Kanban; §7 records why not 0-n, and what it would cost to
change that later.

Heterogeneous children are not new here — `agenda_item` has always sat beside
the columns. Nothing acquires a container it does not need, for the reason
S-Team gives for the same choice: a level that has to exist before a peer's
section can arrive is a level that can diverge on its own.

---

## 2. Content and record

Two kinds of node, and the difference decides everything else about them.

**Content** is what people are agreeing to. It is *edited*: the objective, the
needs, the approach, what is expected of a milestone. Anyone holding the topic
may write it, a second person editing it is a divergence, and the divergence is
shown and resolved by a human. **Decidable**, in the node-class vocabulary —
more than one writer, and changing it changes what everybody holds.

**Records** are what somebody did or saw. They are *appended*, they carry their
author, and nobody else ever rewrites them: an observation of how a milestone
actually went, an actor's commitment of their own availability. One possible
author, so there is nothing to diverge and nothing to adopt — **observed**, and
**persisted** rather than read through, because the point of an impact history
is that it outlives the moment somebody was reachable.

Two rules follow, and they are the whole of this section:

> **Nobody edits somebody else's observation. Nobody commits somebody else's
> time.**

This is the same split S-Team makes between its agreement and its governance
records, and the same one this application already makes by feel between a card
and a comment. It is being written down rather than invented.

---

## 3. The types

### `initiative`

The topic. One per topic, and the node every other one here hangs off.

| Field                       | Requirement                                              |
| --------------------------- | -------------------------------------------------------- |
| `name`                      | required — numbered on collision, as board names already are |
| `objective`                 | required, may be empty                                     |
| `planned_start`             | optional — ISO date                                        |
| `planned_end`               | optional — ISO date                                        |
| `actual_start`              | optional — ISO date                                        |
| `actual_end`                | optional — ISO date                                        |

**There is one objective and it is everybody's.** Not an objective per member —
the whole reason to be on the same initiative is to be pointed at the same
thing, and where somebody's own aim differs it is a card, a need, or a
disagreement worth having in the open. A per-member objective would make the
one question the initiative exists to answer the one question it never asks.

The four dates are content, not records. A planned date is a plan and is
edited; a reached date is a claim the team makes together, and two people who
disagree about when it started have a divergence worth seeing rather than two
private truths. They are fields rather than nodes because an initiative has
dates before it has a roadmap.

### `topic_link` — the team, and the flows

**Built.** Core's node type, described in `s-core/DESIGN_TOPIC_LINKS.md` and
`PUBLIC_API.md`; this application owns only where the links live, which is as
direct children of the initiative. It carries `topic_uuid`, `application_id`
and a `title`.

**One team, 0-n flows.** The cardinality is this application's rule and not
Core's: a second team is not a second opinion about whose initiative this is,
it is a contradiction, and the way to change the answer is to remove the first
link. Flows are uncounted because an initiative may well run several.

**A link is a name for a topic and never a key to it.** This is the answer to
the objection that recording the team hands everybody holding the initiative a
coordinate into it. Following a link reaches only what a peer is *already
publishing where this client can see it* — the uuid on its own opens nothing,
mounts nothing, and grants nothing. A link this client cannot resolve is not
broken and is not an error; it is an invitation that nobody here can honour
yet, and it renders as one.

**The recorded title is what the link says before the topic is held.** Once it
is held, the topic's own name wins, because a copied title is the name
something had on the day the link was made.

**Removing a link removes a reference and nothing else.** The team or flow is
untouched, and so is everybody else's reference to it. Destroying it stays with
the application that owns it, which is the only one that knows who may.

### `initiative_need`

What is being addressed, and whose it is. 0-n, ordered.

| Field                    | Requirement                                             |
| ------------------------ | -------------------------------------------------------- |
| `text`                   | required — the need, in the words of whoever holds it where possible |
| `beneficiary_label`      | required, may be empty — who it belongs to, as recorded here |
| `beneficiary_actor_uuid` | optional — set only where the beneficiary is an actor this system knows |
| `order`                  | required                                                  |

**Why a label, when a name is normally never copied.** S-Team's rule is that a
name is read from the Actor and never stored, because a name copied at
admission is the name somebody had that day. Here the rule inverts, for a
stated reason: the beneficiary of a need is very often not an actor in the
system at all — a customer, a neighbourhood, somebody who will never hold a
key. The label is therefore the primary fact and the uuid is the optional
refinement. Where the uuid does resolve to an actor this client holds, the live
name wins and the label is only what it was called here.

### `initiative_section` and `initiative_clause` — the Approach

Two levels, the same shape as a Team Agreement's sections and clauses: a
section holds clauses and a clause holds nothing.

| `initiative_section` | Requirement            |
| -------------------- | ----------------------- |
| `title`              | required                |
| `order`              | required                |

| `initiative_clause` | Requirement             |
| ------------------- | ------------------------ |
| `text`              | required                 |
| `order`             | required                 |

A new initiative is **seeded** with four sections — Strategy, Plan, Risks,
Conditions for success — which are ordinary content from the moment they exist:
renamable, reorderable, deletable, and a fifth can be added.

**Seeded and not fixed.** A risk and a condition for success read as different
things and behave identically: a line of text somebody wrote, in an order, that
anybody on the topic may edit. Making them separate node types would buy one
thing — a portfolio view that can say "here are the open risks across every
initiative" — and would charge every initiative that thinks in some other shape
for it. The seed gives the same structure to anybody who wants it without
closing the vocabulary. If the portfolio view is later worth the cost, the way
to pay it is a declared kind on the section, not four types.

### `initiative_intent` — Intended Impact

Exactly one per parent, holding `initiative_clause` children. Under the
initiative it is what the whole thing is for. Under a milestone it is what that
point is supposed to have changed by the time it is reached.

| Field   | Requirement |
| ------- | ------------ |
| *(none beyond `type`)* | the text lives in its clauses |

**Why one and not many.** A second intent is not a second opinion, it is a
disagreement about what we are doing — and a disagreement about content is
exactly what the divergence machinery is for. Two of them side by side would
let the initiative hold both without anybody having to notice.

### `initiative_milestone`

A point on the timeline. 0-n, ordered.

| Field        | Requirement                                                     |
| ------------ | ---------------------------------------------------------------- |
| `title`      | required                                                          |
| `order`      | required                                                          |
| `planned_at` | optional — ISO date                                               |
| `reached_at` | optional — ISO date; clearing it is undoing a claim, not tidying up |

A milestone is the same shape as the initiative's own bookends, repeated: a
planned date, a reached date, an intended impact, and the observations of how it
actually went. That is why "a roadmap with milestones", "expected impact at
certain milestones" and "actual impact at certain milestones" are one type here
and not three.

### `initiative_reality` — Assessed Impact

One actor's observation of how it went. 0-n under the initiative, 0-n under
each milestone. Appended, never edited by anybody but its author, never merged.

| Field              | Requirement                                              |
| ------------------ | --------------------------------------------------------- |
| `author_actor_uuid`| required — and the only actor who may write or delete it   |
| `text`             | required                                                   |
| `recorded_at`      | required                                                   |

**Many, and none of them wins.** Diverging perspectives on what an initiative
achieved are not a conflict to resolve; the blueprint files this under
Subjective Reality, and calls them fuel for organisational learning. Intended
impact is one and contested. Assessed impact is many and kept.

This is the same pair as S-Team's `team_trustee_action` and
`team_trustee_reality`, and takes the same name for the second half of it
deliberately.

**The trustee rule about not assessing your own outcomes does not carry over.**
A trusteeship's reality is written under a *different* trusteeship because the
holder is being observed. An initiative's members assessing their own
initiative is the ordinary case and the only one that would ever happen. What
keeps it honest is that every observation names its author and none of them is
*the* record.

### `initiative_investment` — Resources

An actor's commitment of what they have to this initiative. Today that is their
own availability.

| Field           | Requirement                                                    |
| --------------- | --------------------------------------------------------------- |
| `actor_uuid`    | required — the author, and the only actor who may write it        |
| `availability`  | required — free text: "two days a week until March"               |
| `previous_uuid` | required, empty for this actor's first                            |
| `recorded_at`   | required                                                          |

**Only the owner writes it.** Committing somebody else's time is precisely the
kind of thing this protocol exists to refuse to sign. A guard enforces it the
way the comment guard already does.

**Appended, not edited**, so that "I had two days a week and now I have one" is
legible as a change rather than as a correction of a mistake. The chain is per
actor; the current commitment is the head of it.

**No `kind` field yet.** Money and assets are coming, and a field with one
possible value is a comment. It is added when there is a second kind and not
before.

---

## 4. Members, and why there is no member list

**The members of an initiative are the actors holding its topic.** There is no
`initiative_member` node and no stored roster.

A roster would be a second truth about a fact the system already knows, and
the two would disagree the first time somebody left. It would also copy a name,
which S-Team refuses for the reason given there. And the one thing a roster
would genuinely add — naming somebody who has not taken the topic yet — is not
this application's to say: an initiative reaches people through the team that
runs it, and the team's own references are where "this is ours" is already
recorded, by each member, about themselves.

The Members panel therefore shows everyone on the topic, each with the head of
their `initiative_investment` chain, or "no availability recorded" where they
have written none. An actor whose replica is unreachable is shown with their
last-seen commitment, **said as last seen** — a persisted copy of somebody
else's record is a fallback and never a source of truth.

---

## 5. What each type is, in one table

`M` = more than one writer. `S` = materially significant. Decidable requires
both; everything else is observed.

| Node type                | M   | S   | Class          | Storage    |
| ------------------------ | --- | --- | -------------- | ---------- |
| `initiative`             | yes | yes | **decidable**  | —          |
| `topic_link`             | yes | yes | **decidable**  | —          |
| `initiative_need`        | yes | yes | **decidable**  | —          |
| `initiative_section`     | yes | yes | **decidable**  | —          |
| `initiative_clause`      | yes | yes | **decidable**  | —          |
| `initiative_intent`      | yes | yes | **decidable**  | —          |
| `initiative_milestone`   | yes | yes | **decidable**  | —          |
| `initiative_reality`     | no (the observer) | — | observed | persisted |
| `initiative_investment`  | no (the owner)    | — | observed | persisted |
| `kanban_column`          | yes | yes | **decidable**  | —          |
| `kanban_card`            | yes | yes | **decidable**  | —          |
| `card_comment`           | no (the author)   | — | observed | persisted |
| `card_attachment`        | no (the author)   | — | observed | persisted |
| `agenda_item`            | no (the author)   | — | observed | read through |

`topic_link` is decidable on both axes and by the same reasoning as the rest:
anybody holding the initiative may add or remove one, and which team an
initiative belongs to is worth a human noticing when two clients disagree. Its
data never changes once written — a reference is replaced, not edited — so what
a divergence shows is a link present on one side and absent on the other, which
is the whole of what there is to decide about it.

Both new observed types are persisted rather than read through, and that is a
departure from `agenda_item`, which is read through because there is no point
holding a discussion item belonging to somebody who is not there. An impact
observation and a commitment are the opposite case: they are worth exactly as
much when their author has gone quiet, and an initiative whose history
evaporated as people drifted off would have no history at all.

---

## 6. Where the blueprint's vocabulary lands

| Blueprint (`Domain-Driven-Design.md`)      | Here                                                    |
| ------------------------------------------- | -------------------------------------------------------- |
| `Initiative: 1 Team`                        | `topic_link` with `application_id` `team`, at most one — **built** |
| *(nothing)*                                 | `topic_link` to a flow — the blueprint has no way to say an initiative runs one |
| `0-n Investments`                           | `initiative_investment`, availability only               |
| `1 Expected Impact [C-Text]`                | `initiative_intent` on the initiative — renamed, see below |
| `0-n Assessed Impact [C-Text]`              | `initiative_reality`, one per observation, per author    |
| `0-n Boards [Kanban]`                       | exactly one, held as the columns of the initiative — §7  |
| `Resource: 1 Owner, 1 Availability, 1 Value`| collapsed into `initiative_investment` while availability is the only kind |
| *(nothing)*                                 | `initiative_need` — the blueprint puts Needs on the Individual, and never says which initiative addresses them |
| *(nothing)*                                 | the Approach, the milestones, and the four dates          |

**The one rename, and why.** The blueprint says *Expected Impact* in both
places it appears — on the Initiative and on the Bet. It keeps the name on the
Bet, where it is right: a bet is signals, then a decision, then what the
decider expects to follow from it, and a forecast attached to a decision
genuinely is an expectation. S-Team's built `team_trustee_action` carries it
under exactly that reading.

On an initiative it is not a forecast. It is what everybody holding the topic
means to bring about, and calling it *expected* put it beside the `objective`
as a prediction of the same thing — two fields answering "what is this meant
to change", one of them apparently guessing at the other. **Intended Impact**
names the act instead, and the pair *intended / assessed* then reads as intent
against observation, which is what §2 says these two kinds of node are.

**The objective is not folded into it.** One sentence and a set of clauses are
different grains of the same question. Board of Boards draws the sentence, and
recovering one from a clause list would need a "the first clause is the title"
rule — implicit structure of exactly the kind this document refuses elsewhere.

---

## 7. What this deliberately does not do

**No second record of what a team runs.** The initiative names its team, and
the team names its initiatives — both as `topic_link` now, in the topic that
holds each end. These are two different edges and not two truths: the one here
is a property of the initiative and there is one of it whoever wrote it, while
the one on the team is a member's own word and there is one per member, which
is how the team's list is a union and how removing yours leaves everybody
else's standing.

**One Kanban, not 0-n.** The blueprint allows several boards per initiative;
nothing yet wants one. Adding the level later means inserting a `kanban_board`
node between the initiative and its columns, which moves every column and card
one step down. That is a restructuring of stored trees, not of topic identity —
the topic stays the initiative, so adoption, divergence, invitations and relay
targets are untouched by it.

**No money and no assets**, and no `kind` field waiting for them.

**No dependencies between milestones**, no critical path, no derived schedule.
A milestone knows its own two dates and nothing about any other.

**No migration.** `kanban_board` disappears; a stored tree written before this
holds a root node type nothing recognises. This is pre-release and the data
directories are disposable.

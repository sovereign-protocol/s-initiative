# Initiative — what the thing is

Status: **built.** `PLAN_INITIATIVE_FACE.md` records the implementation order.

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
    ├── sovereign_relationship         the team it belongs to, the flows it runs (Core's)
    ├── initiative_need
    ├── initiative_section → initiative_clause          the Approach
    ├── initiative_milestone
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

**Content** is what people are agreeing to. It is *edited*: the Intention, the
needs, the approach, what is intended at a milestone. Anyone holding the topic
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
| `objective`                 | required, may be empty — displayed as **Intention**         |
| `planned_start`             | optional — ISO date                                        |
| `planned_end`               | optional — ISO date                                        |
| `actual_start`              | optional — ISO date                                        |
| `actual_end`                | optional — ISO date                                        |

**There is one Intention and it is everybody's.** The existing storage and API
name is `objective`, retained because Board of Boards reads it. It is not an
objective per member —
the whole reason to be on the same initiative is to be pointed at the same
thing, and where somebody's own aim differs it is a card, a need, or a
disagreement worth having in the open. A per-member objective would make the
one question the initiative exists to answer the one question it never asks.

The four dates are content, not records. A planned date is a plan and is
edited; a reached date is a claim the team makes together, and two people who
disagree about when it started have a divergence worth seeing rather than two
private truths. They are fields rather than nodes because an initiative has
dates before it has a roadmap.

### The team, and the flows

**Built, Core-owned.** The team an initiative belongs to and the flows it
runs are `sovereign_relationship` (s-core/DESIGN_NAVIGATION_LINKS.md), a
Core node type shared by every application, connected from the header
rather than a Mandate region. This application's own domain node,
`initiative_relationship`, did the same job until it was retired in favor
of the mechanism every application now shares.

**One team, 0-n flows.** The cardinality is this application's rule and not
Core's: a second team is not a second opinion about whose initiative this
is, it is a contradiction, and the way to change the answer is to remove
the first connection. Flows are uncounted because an initiative may well
run several. Enforced by `validate_team_relationship`, the one hook Core
calls before writing a new connection from an initiative.

**A connection is never a key.** Recording it grants and mounts nothing. A
client can navigate to the target only when it independently holds that
topic; otherwise the last-known title remains visible as domain
information.

**Removing a connection removes a reference and nothing else.** The team or
flow is untouched, and so is everybody else's reference to it. Destroying it
stays with the application that owns it, which is the only one that knows
who may.

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

A new initiative is **seeded** with two sections — Roadmap and Risks & Chances
— which are ordinary content from the moment they exist: renamable,
reorderable, deletable, and a third can be added.

**Seeded and not fixed.** A risk and a condition for success read as different
things and behave identically: a line of text somebody wrote, in an order, that
anybody on the topic may edit. Making them separate node types would buy one
thing — a portfolio view that can say "here are the open risks across every
initiative" — and would charge every initiative that thinks in some other shape
for it. The seed gives the same structure to anybody who wants it without
closing the vocabulary. If the portfolio view is later worth the cost, the way
to pay it is a declared kind on the section, not four types.

### `initiative_milestone`

A point on the timeline. 0-n, ordered.

| Field        | Requirement                                                     |
| ------------ | ---------------------------------------------------------------- |
| `title`      | required                                                          |
| `order`      | required                                                          |
| `intention`  | optional — one free-text Intention                                 |
| `planned_at` | optional — ISO date                                               |
| `reached_at` | optional — ISO date; clearing it is undoing a claim, not tidying up |

A milestone is the same shape as the initiative's own bookends, repeated: a
planned date, a reached date, one Intention, and the observations of how it
actually went. That is why "a roadmap with milestones", "expected impact at
certain milestones" and "actual impact at certain milestones" are one type here
and not three.

### Team views and author-scoped records

A team view has one canonical value and peer changes may therefore require a
reaction. An individual view or contribution is a separate record keyed by its
author: it is visible to the team, joins automatically, and only its author can
write or remove it. `initiative_reality` and `initiative_investment` are
individual views; comments and attachments are author-owned contributions.

The local **Team-view changes** setting offers, in order: review every change;
review changes involving me; review changes I am responsible for; adopt every
change automatically. “Involving” currently means a card participant/owner or
the named beneficiary of a need. “Responsible” currently means the owner of a
card. Both the current and proposed values are checked so adding or removing
the local actor is itself reviewable.

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
Subjective Reality, and calls them fuel for organisational learning. Intention
is one and contested. Assessed impact is many and kept.

That remains true under a local review-first adoption policy: an assessment is
added to the authored record automatically at both the initiative and milestone
scales, because there is no shared value for the recipient to accept or reject.

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
actor; the current commitment is the head of it. Like assessed impact, each
record joins automatically under every team-view review mode because no peer
can adopt, reject, or replace another actor's availability.

**No `kind` field yet.** Money and assets are coming, and a field with one
possible value is a comment. It is added when there is a second kind and not
before.

**Copies and snapshots carry content, never records.** A copied initiative
keeps its needs, approach, Intention, milestone intentions and dates, but strips
every `initiative_reality` and `initiative_investment`. A snapshot omits the
same two types. Their author uuids are claims about one topic; carrying them
into a new initiative would make actors say something they never wrote there.

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
| `initiative_need`        | yes | yes | **decidable**  | —          |
| `initiative_section`     | yes | yes | **decidable**  | —          |
| `initiative_clause`      | yes | yes | **decidable**  | —          |
| `initiative_milestone`   | yes | yes | **decidable**  | —          |
| `initiative_reality`     | no (the observer) | — | observed | persisted |
| `initiative_investment`  | no (the owner)    | — | observed | persisted |
| `kanban_column`          | yes | yes | **decidable**  | —          |
| `kanban_card`            | yes | yes | **decidable**  | —          |
| `card_comment`           | no (the author)   | — | observed | persisted |
| `card_attachment`        | no (the author)   | — | observed | persisted |
| `agenda_item`            | no (the author)   | — | observed | read through |

The team and flows are absent from this table on purpose: `sovereign_relationship`
is Core's node type, not this application's content, and its governance is
Core's to state (s-core/DESIGN_NAVIGATION_LINKS.md).

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
| `Initiative: 1 Team`                        | a `sovereign_relationship` with `application_id` `team`, at most one, enforced by `validate_team_relationship` — **built** |
| *(nothing)*                                 | a `sovereign_relationship` to a flow — the blueprint has no way to say an initiative runs one |
| `0-n Investments`                           | `initiative_investment`, availability only               |
| `1 Expected Impact [C-Text]`                | `initiative.objective`, displayed as **Intention**         |
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
means to bring about, so the interface calls the existing `objective` field
**Intention**. There is no second clause set answering the same question. The
pair *intention / assessed impact* reads as intent against observation, which
is what §2 says these two kinds of content are. Milestones use the same concept
at their own scale, as one `intention` text field each.

---

## 7. What this deliberately does not do

**No duplicated edge, and now no duplicated type either.** The Initiative's
own connection says what it belongs to or runs; a Team's separate connection
says one member considers a topic part of that Team's work. Different claims,
each still made from its own side — but both are the same Core-owned
`sovereign_relationship` now (s-core/DESIGN_NAVIGATION_LINKS.md), reached
from the header on both applications, rather than each maintaining its own
node type to say the same kind of thing.

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

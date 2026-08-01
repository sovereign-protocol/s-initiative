# Design: card comment history

**Status: shipped.** `create_card_comment`, `card_comments` and
`delete_card_comment` live in `src/s_initiative/logic.py`, the two routes in
`src/s_initiative/controller.py`, and `tests/test_card_comments.py` covers
them. The record is kept because the reasoning below — why history is a set
of child nodes rather than a list field — governs every feature with the
same shape, attachments included.

_Written 2026-07-19, before the repository split. Module names in the phase
plan are the pre-split ones: `kanban_logic.py` is now
`src/s_initiative/logic.py`. The node_hash/subtree_hash split it relies on is
described in `DESIGN_NODE_SUBTREE_HASH_SPLIT.md` in the Core repository._

## Goal

Per-card update history like Trello: an ordered list of `(time, originator,
text)` comments a user can add to a card, synced and merged across peers.

## Design: each comment is an immutable child node of the card

Do **not** store history as a list field in the card's `data`. In this CRDT a
plain appended list merges badly — two people commenting concurrently would
diverge the card and one list would win. Instead model each comment as its own
node, exactly the pattern `agenda_item` already uses:

- **Node:** `data = {type: "card_comment", text, author}`, parented to the
  card. `author` is a profile uuid; the timestamp is the node's existing
  `created_at` (already carried through `to_dict`/`from_dict`, and *not* part of
  `content_hash`, so it's stable).
- **Merge is free:** each comment has its own uuid, so concurrent comments are a
  set-union — both survive, no conflict.
- **Order on read** by `created_at` (author's wall clock; fine for a human log,
  same as any chat — no global order needed).
- **No churn on the card:** thanks to the node_hash/subtree_hash split, adding a
  comment changes the card's *subtree* hash but not its own `content_hash`, so
  the card never shows as "changed" or diverged just because someone commented.
- **Immutable for v1:** create + delete-own-comment; no edit (add later).
  Deletion is the normal node delete (tombstone + GC).

## Decision: comments should always auto-adopt (like agenda items)

Comments are **additive and author-stamped** — they never overwrite existing
content — so a peer's new comment should appear even under `not_owner` /
`not_member`, not wait for manual review the way a new column does. Give
`card_comment` its own eligibility path (always adopt an incoming comment from
any peer, mirroring `_adopt_originator_agenda_changes`' author-authority
handling), rather than letting it fall under the "new non-card node waits for
manual adoption" rule. Deleting a comment follows normal deletion/eligibility.

## API

```
POST /api/initiative/cards/comments/create  {card_uuid, text}
POST /api/initiative/cards/comments/delete  {comment_uuid}
```

`board_payload` includes each card's comments as a `comments` list, sorted by
`created_at`, each `{uuid, text, author, author_label, created_at}`.

## Phase plan (suite green after each)

1. **kanban_logic.** `create_card_comment(card_uuid, text)` (create_child with
   the comment shape + local author), `card_comments(card)` (live
   `card_comment` children sorted by `created_at`), `delete_comment(uuid)`
   (author-only). Wire comments into `board_payload`'s card summary. Add the
   always-adopt eligibility path for `card_comment`.
2. **API routes** for create/delete.
3. **UI** (`initiative.html`): a comments section in the card modal — a list
   (author, relative time, text) plus an add-comment box; a delete affordance
   on your own comments.
4. **Tests:** create/list/delete; ordering by time; two-client concurrent
   comments both survive; a peer's comment auto-adopts under `not_owner`;
   appending a comment leaves the card's own transition `in_agreement`.

## Effort

Small — roughly 1–2 sessions. No protocol or transport changes; it's the
`agenda_item` pattern applied to cards.

## Later (out of scope for v1)

- Editing a comment; reactions on comments.
- Auto-generated activity events (moved / renamed / member changed). Derivable
  but messy to generate consistently across clients — revisit after v1.
- A comment carrying a **blob attachment** — composes directly with
  [DESIGN_BLOBS.md] once that lands.

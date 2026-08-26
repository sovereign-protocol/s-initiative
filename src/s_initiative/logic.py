"""
S-Initiative: the initiative and the Kanban board that organises it.

Contract:
  Model:
    - The discussion topic is always the initiative node.
    - Initiative/columns/cards are regular protocol nodes.
    - Column data: {type: "kanban_column", name, order}
    - Card data: {type: "kanban_card", name, description, participants, owner, order}
      owner is a profile uuid that must also be present in participants, or None.
    Initiative data additionally carries: objective (the single free-text
      Intention, default ""), also used by the Board of Boards summary view, and four
      optional ISO dates - planned_start, planned_end, actual_start,
      actual_end. All five are content: edited, and a disagreement about
      one is a divergence. A date not yet said is absent from the data
      rather than stored empty. The planned pair is edited on the
      mandate face; the claimed pair is claimed from the board's
      milestone strip, on the day it is true.
    Agenda item data: {type: "agenda_item", text, priority, author}
      - a direct child of the initiative, same as columns. priority is one of
      "high"/"medium"/"low", or None if not set (optional - a card doesn't
      need one), author is a profile uuid. Purely async: created/deleted
      like any node and projected from each verified perspective via the
      initiative's existing topic; observed items are not adopted implicitly.

  API:
    GET  /api/initiative/board
    POST /api/initiative/initiatives/set_objective {initiative_uuid, objective}
    POST /api/initiative/initiatives/set_dates    {initiative_uuid, planned_start, planned_end}
      # either date omitted is left alone; empty clears it
    POST /api/initiative/initiatives/claim_date   {initiative_uuid, field, value}
      # field is actual_start or actual_end; empty value takes the claim back
    POST /api/initiative/needs/create         {text, beneficiary_label, beneficiary_actor_uuid}
    POST /api/initiative/needs/update         {need_uuid, text, beneficiary_label, beneficiary_actor_uuid}
      # any field omitted is left alone; empty clears the two beneficiary ones
    POST /api/initiative/needs/delete         {need_uuid}
    POST /api/initiative/needs/move           {need_uuid, index}
    POST /api/initiative/sections/create      {title}
    POST /api/initiative/sections/rename      {section_uuid, title}
    POST /api/initiative/sections/delete      {section_uuid}
    POST /api/initiative/sections/move        {section_uuid, index}
    POST /api/initiative/clauses/create       {parent_uuid, text}
    POST /api/initiative/clauses/update       {clause_uuid, text}
    POST /api/initiative/clauses/delete       {clause_uuid}
    POST /api/initiative/clauses/move         {clause_uuid, index}  # among its own siblings
    POST /api/initiative/realities/create     {parent_uuid, text}
    POST /api/initiative/realities/delete     {reality_uuid}
    POST /api/initiative/investments/create   {actor_uuid, availability}
    POST /api/initiative/investments/delete   {investment_uuid}
    POST /api/initiative/milestones/create    {title, planned_at, intention}
    POST /api/initiative/milestones/update    {milestone_uuid, title, planned_at, intention}
    POST /api/initiative/milestones/delete    {milestone_uuid}
    POST /api/initiative/milestones/move      {milestone_uuid, index}
    POST /api/initiative/milestones/reach     {milestone_uuid, value}
    POST /api/initiative/agenda/create        {text, priority}  # priority optional
    POST /api/initiative/agenda/delete        {item_uuid}
    POST /api/initiative/agenda/set_priority  {item_uuid, priority}  # priority optional, clears if omitted
    POST /api/initiative/auto_adopt          {mode}  # one of: never, not_member, not_owner, always
    POST /api/initiative/columns/create      {name}
    POST /api/initiative/columns/rename      {column_uuid, name}
    POST /api/initiative/columns/delete      {column_uuid}
    POST /api/initiative/columns/move        {column_uuid, index}
    POST /api/initiative/cards/create        {column_uuid, name, description, participants, owner}
    POST /api/initiative/cards/update        {card_uuid, name, description, participants, owner}
    POST /api/initiative/cards/delete        {card_uuid}
    POST /api/initiative/cards/move          {card_uuid, column_uuid, index}
    POST /api/initiative/cards/comments/create {card_uuid, text}
    POST /api/initiative/cards/comments/delete {comment_uuid}
    POST /api/initiative/react               {source_addr, node_uuid, reaction, absent}

  Naming:
    The route path and the payload-building trio name the *face*; every
    identifier and payload key names the *topic*. So the read is
    GET /api/initiative/board and its builders are board_payload /
    board_snapshot / merge_board_observation, while what they carry is an
    initiative and the collection routes are /initiatives/*. This is the
    arrangement S-Team already uses for GET /api/team/document over `team`
    and `teams`. "Board" therefore survives only where the Kanban board -
    the columns and the cards - is what is meant.

  Needs:
    initiative_need data: {type, text, beneficiary_label, order} and an
    optional beneficiary_actor_uuid - a direct child of the initiative, and
    decidable. The label is the primary fact and the uuid refines it where
    the beneficiary happens to be an actor this system knows; the page draws
    the live name beside the label rather than instead of it, because the
    two disagreeing is information. Needs travel inside the initiative's own
    subtree, so there is no payload key of their own.

  Approach:
    initiative_section {type, title, order} holding initiative_clause
    {type, text, order}, both direct content of the initiative and both
    decidable. A new initiative is seeded with two sections - Roadmap and
    Risks & Chances - which are ordinary content from the moment they exist:
    renamable, reorderable, deletable, and a third is added the same way.
    Nothing marks the two as special. A clause sits
    under a section and never at the top level; it reorders among its own
    siblings only.

  Impact, resources and milestones:
    The initiative's objective field is presented as its single Intention.
    initiative_milestone carries title, order, a single optional intention,
    and optional planned_at / reached_at ISO dates. Both are decidable
    content. initiative_reality {author_actor_uuid, text,
    recorded_at} and initiative_investment {actor_uuid, availability,
    previous_uuid, recorded_at} are immutable authored records: they are
    adopted, never diverged, and never copied or snapshotted.

  Connected work:
    An initiative's team (0-1) and the flows it runs (0-n) are Core's own
    sovereign_relationship, reached from the shared header rather than a
    route or payload key here. This application's one domain rule -
    at most one team - is validate_team_relationship, a hook Core calls
    before writing a new connection.
"""

from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any

from sovereign import (
    ApplicationRegistration, LastWriteWinsPolicy, ProtocolNode, Session, SessionResult,
    avatar_attachment, canonical_attachments,
)

# Core's `sovereign_relationship` (s-core/src/sovereign/relationships.py).
# Matched by literal name, the way this application already matches every
# node type it does not own, rather than importing a Core submodule.
RELATIONSHIP_TYPE = "sovereign_relationship"

DEFAULT_COLUMNS = ["To Do", "Doing", "Done"]
INITIATIVE_APP_NAME = "S-Initiative"
INITIATIVE_APPLICATION_ID = "initiative"
SNAPSHOT_FORMAT = "s-protocol.item-snapshot"
SNAPSHOT_FORMAT_VERSION = 1
AUTO_ADOPT_MODES = ("never", "not_member", "not_owner", "always")
AGENDA_PRIORITIES = ("high", "medium", "low")
DISPLAYED_DIVERGENCE_TYPES = frozenset({
    "initiative", "kanban_column", "kanban_card",
    # What the initiative addresses is agreed, not observed: anybody
    # holding the topic may write one, and two clients disagreeing about
    # what is being addressed is worth a human seeing.
    "initiative_need",
    # The Approach is agreed text: two clients holding different versions of
    # how this will be gone about is exactly what the lamp is for.
    "initiative_section", "initiative_clause",
    "initiative_milestone",
})
OWNED_NODE_TYPES = frozenset({
    *DISPLAYED_DIVERGENCE_TYPES,
    "agenda_item", "card_attachment", "card_comment",
    "initiative_reality", "initiative_investment",
    # Connected work lives in the header now, not the Mandate, so it is
    # reactable but not one of the content-area divergence types above.
    RELATIONSHIP_TYPE,
})
INDIVIDUAL_VIEW_NODE_TYPES = frozenset({
    "initiative_reality", "initiative_investment",
})
INDIVIDUAL_CONTRIBUTION_NODE_TYPES = frozenset({
    "card_comment", "card_attachment",
})
AUTHOR_SCOPED_NODE_TYPES = frozenset({
    *INDIVIDUAL_VIEW_NODE_TYPES,
    *INDIVIDUAL_CONTRIBUTION_NODE_TYPES,
})
INITIATIVE_POSITION_POLICY = LastWriteWinsPolicy(
    node_type="kanban_card",
    timestamp_field="position_updated_at",
    data_fields=("order",),
    include_parent=True,
)


class InitiativeLogic:
    def __init__(self, session: Session, config: dict | None = None,
                 collaboration=None):
        self.session = session
        self.config = config or {}
        self.collaboration = collaboration
        # Revision ownership must exist before the first initiative/card mutation.
        # Session.identity bootstraps its own origin without recursion.
        self.session.identity
        with self.session.lock:
            self.session.application_metadata(INITIATIVE_APPLICATION_ID)

    def application_registration(self) -> ApplicationRegistration:
        return ApplicationRegistration(
            INITIATIVE_APPLICATION_ID,
            frozenset({"initiative"}),
            self.initiatives,
            self.accept_initiative_invitation,
            assignment_scoped=True,
            mount_invitation=True,
            on_peer_update=self.on_peer_update,
            topic_noun="Initiative",
            # An initiative may start from nothing. What makes a template a
            # template here is that it is one of this client's own initiatives.
            list_templates=self.initiative_templates,
            create_topic=self.make_initiative,
            validate_relationship=self.validate_team_relationship,
        )

    def initiative_templates(self) -> list[dict]:
        return [
            {
                "value": initiative.uuid,
                "name": str(initiative.data.get("name") or "Untitled"),
            }
            for initiative in self.initiatives()
        ]

    def make_initiative(
        self, name: str, template: str = "", snapshot: dict | None = None,
    ) -> SessionResult:
        """One initiative, however it starts. Core's create contract.

        Copying names the copy after its source, so the name asked for here
        is applied afterwards - which is the whole difference between this
        and the three calls it wraps.
        """
        if snapshot is not None:
            return self.create_from_snapshot(snapshot, name)
        source = str(template or "").strip()
        if not source:
            return self.create_initiative(name)
        copied = self.copy_initiative(source)
        if copied.status != "ok":
            return copied
        initiative_uuid = str(getattr(copied.value, "uuid", copied.value) or "")
        renamed = self.rename_initiative(initiative_uuid, name)
        if renamed.status != "ok":
            return renamed
        return SessionResult(
            "ok", value=initiative_uuid,
            effects=[*copied.effects, *renamed.effects],
        )

    def board_payload(self, network: dict | None = None) -> dict:
        """Return the current board view without reconciling or creating data."""
        initiatives = self.initiatives()
        initiative = self._selected_initiative(initiatives)
        network = (
            self._network_info(initiative.uuid if initiative else None)
            if network is None else network
        )
        events = self.transition_events(initiative.uuid, network) if initiative else []
        return {
            "address": self.session.address,
            "initiative": initiative.to_dict() if initiative else None,
            "user_profile": self.user_profile().to_dict(),
            "users": self.users(),
            "network": network,
            "peers": {
                addr: tree.to_dict()
                for addr, tree in sorted(
                    self.session.peer_perspectives_for_topic().items(),
                )
            },
            "auto_adopt_mode": (
                self.auto_adopt_mode(initiative) if initiative else "always"
            ),
            # The shell renders the adoption control and the agenda, so it
            # needs this application's mode set and who "mine" is.
            "auto_adopt_modes": list(AUTO_ADOPT_MODES),
            "identity_uuid": self.session.identity.uuid,
            "known_identities": self.session.known_identities(),
            "transition_events": events,
            "transition_by_node": self._transition_groups(events, initiative),
            "agenda_items": (
                [item.to_dict() for item in self.agenda_items(initiative)]
                if initiative else []
            ),
            "comments_by_card": self._comments_by_card(initiative) if initiative else {},
            "attachments_by_card": (
                self._attachments_by_card(initiative) if initiative else {}
            ),
            "resources": (
                self.initiative_resources(initiative) if initiative else []
            ),
        }

    def board_snapshot(self) -> dict:
        """Build board state under Session without consulting transport."""
        payload = self.board_payload({"_include_all": True})
        initiative = payload.get("initiative") or {}
        return {
            "payload": payload,
            "topic_uuid": initiative.get("uuid"),
        }

    def merge_board_observation(
        self, snapshot: dict, network: dict,
    ) -> dict:
        """Decorate a detached board snapshot with current channel liveness."""
        payload = snapshot["payload"]
        events = [
            event for event in payload.get("transition_events", [])
            if self._transition_visible(event, network)
        ]
        payload["network"] = network
        payload["transition_events"] = events
        initiative = self._node(
            (payload.get("initiative") or {}).get("uuid"), "initiative",
        )
        payload["transition_by_node"] = self._filter_transition_groups(
            payload.get("transition_by_node", {}), network, initiative,
        )
        return payload

    def _filter_transition_groups(
        self, grouped: dict, network: dict,
        initiative: ProtocolNode | None,
    ) -> dict:
        visible_events = []
        for group in grouped.values():
            candidates = group.get("events") or [group]
            visible_events.extend(
                event for event in candidates
                if self._transition_visible(event, network)
            )
        return self._transition_groups(visible_events, initiative)

    def _transition_groups(
        self, events: list[dict], initiative: ProtocolNode | None,
    ) -> dict:
        """Add Initiative policy placement to Core's canonical grouping."""
        grouped = self.session.group_transition_events(events)
        mode = self.auto_adopt_mode(initiative) if initiative else "always"
        for node_uuid, info in grouped.items():
            pending = info.get("stage") not in {None, "settled", "in_flight"}
            auto_resolvable = info.get("type") in {
                "peer_made_changes", "local_missing_node",
            }
            policy_nodes = self._transition_policy_nodes(node_uuid, info)
            automatically_allowed = (
                all(
                    self._auto_adopt_allows_node(mode, node)
                    for node in policy_nodes
                )
                if policy_nodes else self._auto_adopt_allows_node(mode, None)
            )
            info["reactable"] = bool(
                pending
                and (
                    not auto_resolvable
                    or not automatically_allowed
                )
            )
        return grouped

    def _transition_policy_nodes(self, node_uuid: str, info: dict) -> list:
        """Current and proposed copies used by selective review modes."""
        nodes = []
        local = self.session.protocol.index.get(node_uuid)
        if local is not None:
            nodes.append(local)
        for event in info.get("events") or [info]:
            peer_addr = event.get("peer_addr")
            if not peer_addr:
                continue
            peer = self.session.get_cached_peer_subtree(peer_addr, node_uuid)
            if peer is not None and all(peer is not item for item in nodes):
                nodes.append(peer)
        return nodes

    @staticmethod
    def _transition_visible(event: dict, network: dict) -> bool:
        if event.get("stage") != "in_flight":
            return True
        peers = network.get("peers") or {}
        addresses = event.get("delivery_peer_addrs") or [
            event.get("peer_addr"),
        ]
        return any(
            (
                (peers.get(address) or {}).get("channel_liveness") or {}
            ).get("state") == "alive"
            for address in addresses if address
        )

    def _comments_by_card(self, initiative: ProtocolNode) -> dict:
        # UI-friendly view of card comments: resolved author labels, sorted by
        # time, keyed by card uuid. The comments also live in the initiative tree as
        # card children, so they sync via the initiative topic - this is just the
        # convenient shape for the card modal.
        names = {user["id"]: user["name"] for user in self.users() if user.get("id")}
        out = {}
        for column in self.columns(initiative):
            for card in self.cards(column):
                comments = self.card_comments(card)
                if not comments:
                    continue
                out[card.uuid] = [
                    {
                        "uuid": comment.uuid,
                        "text": comment.data.get("text", ""),
                        "author": comment.data.get("author"),
                        "author_label": names.get(comment.data.get("author"), ""),
                        "created_at": comment.created_at,
                    }
                    for comment in comments
                ]
        return out

    def _attachments_by_card(self, initiative: ProtocolNode) -> dict:
        # Same shape and reasoning as _comments_by_card: the files live in the
        # initiative tree as card children and sync with the initiative topic; this is
        # only the convenient view for the modal, with the download URL Core
        # already serves resolved for each blob.
        names = {user["id"]: user["name"] for user in self.users() if user.get("id")}
        out = {}
        for column in self.columns(initiative):
            for card in self.cards(column):
                entries = []
                for node in self.card_attachments(card):
                    for item in canonical_attachments(node.data.get("attachments")):
                        entries.append({
                            "uuid": node.uuid,
                            "name": item["name"],
                            "size": item["size"],
                            "mime": item["mime"],
                            "url": f"/api/blob/{item['blob_id']}",
                            "author": node.data.get("author"),
                            "author_label": names.get(node.data.get("author"), ""),
                            "created_at": node.created_at,
                        })
                if entries:
                    out[card.uuid] = entries
        return out

    def _network_info(self, initiative_uuid: str | None = None) -> dict:
        return (
            self.collaboration.network_info(initiative_uuid)
            if self.collaboration else self.session.get_network_info()
        )

    def network_info(self, initiative_uuid: str | None = None) -> dict:
        return self._network_info(initiative_uuid)

    def collaboration_context(
        self, topic_uuid: str, network: dict | None = None,
    ) -> dict:
        initiative = self._node(topic_uuid, "initiative")
        if not initiative:
            return {}
        network = (
            self.network_info(topic_uuid) if network is None else network
        )
        events = self.transition_events(topic_uuid, network)
        return {
            "agenda_items": [
                item.to_dict() for item in self.agenda_items(initiative)
            ],
            "transition_events": events,
            "transition_by_node": self._transition_groups(events, initiative),
            "identity_uuid": self.session.identity.uuid,
            "known_identities": self.session.known_identities(),
            "auto_adopt_mode": self.auto_adopt_mode(initiative),
            "auto_adopt_modes": list(AUTO_ADOPT_MODES),
        }

    # The policy is Session's; this application only adds the two modes that
    # are judged against card ownership. Session stores the mode as an opaque
    # string and never interprets the extra two, so its answer is normalised
    # here rather than trusted.
    def auto_adopt_mode(self, initiative: ProtocolNode | None = None) -> str:
        initiative = initiative or self.ensure_initiative()
        return self._normalize_auto_adopt_mode(
            self.session.auto_adopt_mode(initiative.uuid)
        )

    def set_auto_adopt_mode(
        self, mode: str, initiative_uuid: str | None = None,
    ) -> SessionResult:
        initiative = (
            self._node(initiative_uuid, "initiative")
            if initiative_uuid else self.ensure_initiative()
        )
        if not initiative:
            return SessionResult("error", reason="initiative not found")
        normalized = self._normalize_auto_adopt_mode(mode)
        result = self.session.set_auto_adopt_mode(
            initiative.uuid, normalized, AUTO_ADOPT_MODES,
        )
        if result.status != "ok":
            return result
        # The mode is read at the moment each decision is made, so changing it
        # changes answers already given. Two of the four modes declare the same
        # handling to Core, which means the declaration alone cannot be the
        # trigger: a card held under the old mode would sit there until the
        # peer happened to send something else, and if they sent nothing it
        # would sit there for good. Core does the reconsidering; this only says
        # that the ground it was decided on has moved.
        self.publish_adoption_metadata(initiative)
        self.session.reconsider_adoption(initiative.uuid)
        return result

    @staticmethod
    def _normalize_auto_adopt_mode(value: Any) -> str:
        if value in AUTO_ADOPT_MODES:
            return value
        return "always"

    def ensure_initiative(self) -> ProtocolNode:
        initiative = self._selected_initiative(self.initiatives())
        if initiative:
            self._remember_initiative(initiative.uuid)
            return initiative
        initiative = self._create_initiative_node("Initiative")
        self._remember_initiative(initiative.uuid)
        return initiative

    def _selected_initiative(
        self, initiatives: list[ProtocolNode],
    ) -> ProtocolNode | None:
        """Choose an initiative without changing selection metadata."""
        remembered_uuid = self._metadata().get("selected_initiative_uuid")
        explicit = bool(self._metadata().get("initiative_selection_explicit"))
        remembered = self.session.protocol.index.get(remembered_uuid) if remembered_uuid else None
        if explicit and remembered and remembered.data.get("type") == "initiative":
            return remembered
        for active in self.session.active_topics():
            if active.data.get("type") == "initiative":
                return active
            if active and self._is_initiative_app_topic(active):
                active_initiatives = self._initiatives_under(active)
                if active_initiatives:
                    return active_initiatives[0]
        if remembered and remembered.data.get("type") == "initiative":
            return remembered
        for node in initiatives:
            return node
        return None

    def initiatives(self) -> list[ProtocolNode]:
        containers = self._initiative_containers()
        initiatives = []
        for container in containers:
            initiatives.extend(self._initiatives_under(container))
        return sorted(initiatives, key=lambda node: (
            str(node.data.get("name", "")),
            node.created_at,
        ))

    # An initiative is named in the switcher, in Board of Boards and in every
    # invitation; two initiatives called the same thing are two different places
    # to work that read as one. What somebody typed is kept and numbered
    # rather than refused.
    _NUMBERED = re.compile(r"\s*\(\d+\)$")

    @classmethod
    def _distinct_name(cls, name: str, taken) -> str:
        existing = {str(entry or "").strip().casefold() for entry in taken}
        if name.casefold() not in existing:
            return name
        # A second "Roadmap (2)" becomes "Roadmap (3)", not "Roadmap (2) (2)".
        base = cls._NUMBERED.sub("", name) or name
        index = 2
        while f"{base} ({index})".casefold() in existing:
            index += 1
        return f"{base} ({index})"

    def _initiative_names(self, excluding: str = "") -> list[str]:
        return [
            initiative.data.get("name") for initiative in self.initiatives()
            if initiative.uuid != excluding
        ]

    def create_initiative(self, name: str = "Initiative") -> SessionResult:
        initiative = self._create_initiative_node(
            self._distinct_name(name or "Initiative", self._initiative_names()),
        )
        self._remember_initiative(initiative.uuid, explicit=True)
        return SessionResult("ok", value=initiative.uuid)

    def select_initiative(self, initiative_uuid: str) -> SessionResult:
        initiative = self._node(initiative_uuid, "initiative")
        if not initiative:
            return SessionResult("error", reason="initiative not found")
        self._remember_initiative(initiative.uuid, explicit=True)
        return SessionResult("ok", value=initiative.uuid)

    def accept_initiative_invitation(self, subtree: ProtocolNode) -> SessionResult:
        # Grafts an initiative first discovered through any channel into our own
        # initiative list. A genuinely new shared initiative starts
        # fully collaborative; reconnecting an existing initiative never reaches
        # this path and therefore retains its local per-initiative setting.
        was_known = subtree.uuid in self.session.protocol.index
        accepted = self.session.accept_topic_invitation(subtree, self._initiative_container().uuid)
        if accepted.status == "ok":
            if not was_known:
                self.session.set_auto_adopt_mode(
                    accepted.value, "always", AUTO_ADOPT_MODES,
                )
            self._remember_initiative(accepted.value)
        return accepted

    def rename_initiative(self, initiative_uuid: str, name: str) -> SessionResult:
        initiative = self._node(initiative_uuid, "initiative")
        if not initiative:
            return SessionResult("error", reason="initiative not found")
        data = dict(initiative.data)
        data["name"] = self._distinct_name(
            name or "Initiative", self._initiative_names(excluding=initiative.uuid),
        )
        return self.session.modify(initiative.uuid, data, initiative.weights)

    def set_initiative_objective(
        self, initiative_uuid: str, objective: str,
    ) -> SessionResult:
        initiative = self._node(initiative_uuid, "initiative")
        if not initiative:
            return SessionResult("error", reason="initiative not found")
        data = dict(initiative.data)
        data["objective"] = objective or ""
        return self.session.modify(initiative.uuid, data, initiative.weights)

    # The four dates are content and not records: a planned date is a plan
    # and is edited, and a reached date is a claim the team makes together,
    # so two people who disagree about when it started have a divergence
    # worth seeing rather than two private truths. They are fields rather
    # than nodes because an initiative has dates before it has a roadmap.
    #
    # Two commands and not one, because they are two acts. Planning happens
    # on the mandate face; claiming happens on the day it is true, from
    # the board's milestone strip. A field absent from the data has not been
    # said - not the same as one said to be empty - so clearing writes the
    # key away rather than storing "".
    PLANNED_DATE_FIELDS = ("planned_start", "planned_end")
    CLAIMED_DATE_FIELDS = ("actual_start", "actual_end")

    def set_initiative_dates(
        self, initiative_uuid: str,
        planned_start: str | None = None, planned_end: str | None = None,
    ) -> SessionResult:
        """Plan when the initiative runs. The other face's act."""
        return self._write_initiative_dates(
            initiative_uuid,
            dict(zip(self.PLANNED_DATE_FIELDS, (planned_start, planned_end))),
        )

    def claim_initiative_date(
        self, initiative_uuid: str, field: str, value: str = "",
    ) -> SessionResult:
        """Claim that the initiative started or ended, or take the claim back.

        Clearing one is undoing a claim rather than tidying up a field, and
        the strip says so; the command itself treats them alike, because
        what makes it an undoing is that somebody had claimed it.
        """
        if field not in self.CLAIMED_DATE_FIELDS:
            return SessionResult("error", reason="not a claimable date")
        return self._write_initiative_dates(initiative_uuid, {field: value})

    def _write_initiative_dates(
        self, initiative_uuid: str, fields: dict[str, str | None],
    ) -> SessionResult:
        initiative = self._node(initiative_uuid, "initiative")
        if not initiative:
            return SessionResult("error", reason="initiative not found")
        data = dict(initiative.data)
        for field, value in fields.items():
            # None is "leave this one alone", so that planning one end of an
            # initiative does not erase the other.
            if value is None:
                continue
            text = str(value).strip()
            if not text:
                data.pop(field, None)
                continue
            if not self._is_iso_date(text):
                return SessionResult(
                    "error", reason=f"{field} must be an ISO date (YYYY-MM-DD)",
                )
            data[field] = text
        return self.session.modify(initiative.uuid, data, initiative.weights)

    @staticmethod
    def _is_iso_date(value: str) -> bool:
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return False
        return True

    # An initiative's team and the flows it runs are Core's own connected
    # work now (s-core/DESIGN_NAVIGATION_LINKS.md, sovereign_relationship),
    # reached from the header rather than the Mandate. The one domain rule
    # only this application knows - an initiative belongs to at most one
    # team - is the validate_relationship hook Core asks before writing a
    # new connection; a flow carries no such limit.
    def validate_team_relationship(
        self, initiative: ProtocolNode, target: ProtocolNode,
    ) -> SessionResult | None:
        handler = self.session.shared_topic_handler_for(target)
        if not handler or handler.application_id != "team":
            return None
        has_team = any(
            not child.deleted
            and child.data.get("type") == RELATIONSHIP_TYPE
            and str(child.data.get("application_id") or "") == "team"
            for child in initiative.children
        )
        if has_team:
            return SessionResult(
                "error", reason="this initiative already belongs to a team",
            )
        return None

    def copy_initiative(self, initiative_uuid: str) -> SessionResult:
        initiative = self._node(initiative_uuid, "initiative")
        if not initiative:
            return SessionResult("error", reason="initiative not found")
        container = self._initiative_container()
        result = self.session.copy(initiative.uuid, container.uuid)
        if result.status != "ok":
            return result
        clone = result.value
        # A copy is a new initiative, and is nobody's yet. Observations and
        # commitments are authored records, not reusable content; copying
        # their author uuids would turn a person's statement about one topic
        # into a statement they never made about another.
        for node in list(self._descendants(clone)):
            if node.data.get("type") in {
                "initiative_reality", "initiative_investment",
            }:
                self.session.delete(node.uuid)
        clone = self.session.get_node(clone.uuid) or clone
        data = dict(clone.data)
        data["name"] = self._distinct_name(
            f"{data.get('name', 'Initiative')} copy",
            self._initiative_names(excluding=clone.uuid),
        )
        self.session.modify(clone.uuid, data, clone.weights)
        self._remember_initiative(clone.uuid, explicit=True)
        return SessionResult("ok", value=clone.uuid)

    def export_snapshot(
        self, initiative_uuid: str, name: str = "", description: str = "",
    ) -> SessionResult:
        initiative = self._node(initiative_uuid, "initiative")
        if not initiative:
            return SessionResult("error", reason="initiative not found")
        source_name = str(initiative.data.get("name") or "Untitled initiative")
        columns = []
        for column in self.columns(initiative):
            columns.append({
                "name": str(column.data.get("name") or "Column"),
                "order": column.data.get("order", 0),
                "cards": [
                    {
                        "name": str(card.data.get("name") or "Card"),
                        "description": str(card.data.get("description") or ""),
                        "order": card.data.get("order", 0),
                    }
                    for card in self.cards(column)
                ],
            })
        return SessionResult("ok", value={
            "format": SNAPSHOT_FORMAT,
            "format_version": SNAPSHOT_FORMAT_VERSION,
            "item_type": "initiative",
            "name": str(name or "").strip() or f"{source_name} snapshot",
            "description": str(description or "").strip(),
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "source_name": source_name,
            "content": {
                "objective": str(initiative.data.get("objective") or ""),
                **{
                    field: str(initiative.data[field])
                    for field in (
                        "planned_start", "planned_end",
                        "actual_start", "actual_end",
                    )
                    if initiative.data.get(field)
                },
                "columns": columns,
                # Content, so a template carries it. What a snapshot must
                # never carry is a record - somebody's observation or
                # somebody's committed time - because a new initiative is
                # nobody's yet.
                "needs": [
                    {
                        "text": str(need.data.get("text") or ""),
                        "beneficiary_label": str(
                            need.data.get("beneficiary_label") or "",
                        ),
                        "order": need.data.get("order", 0),
                        **(
                            {"beneficiary_actor_uuid": str(
                                need.data["beneficiary_actor_uuid"],
                            )}
                            if need.data.get("beneficiary_actor_uuid") else {}
                        ),
                    }
                    for need in self.needs(initiative)
                ],
                "sections": [
                    {
                        "title": str(section.data.get("title") or ""),
                        "order": section.data.get("order", 0),
                        "clauses": [
                            {
                                "text": str(clause.data.get("text") or ""),
                                "order": clause.data.get("order", 0),
                            }
                            for clause in self.clauses(section)
                        ],
                    }
                    for section in self.sections(initiative)
                ],
                "milestones": [
                    {
                        "title": str(milestone.data.get("title") or ""),
                        "order": milestone.data.get("order", 0),
                        **(
                            {"intention": str(milestone.data["intention"])}
                            if milestone.data.get("intention") else {}
                        ),
                        **(
                            {"planned_at": str(milestone.data["planned_at"])}
                            if milestone.data.get("planned_at") else {}
                        ),
                        **(
                            {"reached_at": str(milestone.data["reached_at"])}
                            if milestone.data.get("reached_at") else {}
                        ),
                    }
                    for milestone in self.milestones(initiative)
                ],
            },
        })

    def create_from_snapshot(
        self, document: dict, name: str = "",
    ) -> SessionResult:
        error = self._snapshot_error(document, "initiative")
        if error:
            return SessionResult("error", reason=error)
        content = document["content"]
        requested = str(name or "").strip() or str(
            document.get("source_name") or document.get("name") or "Initiative"
        )
        created = self.session.create_child(
            self._initiative_container().uuid,
            {
                "type": "initiative",
                "name": self._distinct_name(requested, self._initiative_names()),
                "objective": str(content.get("objective") or ""),
                **{
                    field: str(content[field])
                    for field in (
                        "planned_start", "planned_end",
                        "actual_start", "actual_end",
                    )
                    if content.get(field)
                },
            },
            {},
        )
        if created.status != "ok":
            return created
        copied = self._import_snapshot_initiative_content(content, created.value.uuid)
        if copied.status != "ok":
            self.session.delete(created.value.uuid)
            return copied
        self._remember_initiative(created.value.uuid, explicit=True)
        return SessionResult(
            "ok", value=created.value.uuid,
            effects=[*created.effects, *copied.effects],
        )

    def _import_snapshot_initiative_content(
        self, content: dict, target_uuid: str,
    ) -> SessionResult:
        effects = []
        # A snapshot written before needs existed simply has none, which is
        # not the same as a malformed one.
        for order, need in enumerate(content.get("needs") or []):
            if not isinstance(need, dict):
                return SessionResult("error", reason="snapshot need is invalid")
            made_need = self.session.create_child(
                target_uuid,
                {
                    "type": "initiative_need",
                    "text": str(need.get("text") or ""),
                    "beneficiary_label": str(need.get("beneficiary_label") or ""),
                    "order": need.get("order", order),
                    **(
                        {"beneficiary_actor_uuid": str(
                            need["beneficiary_actor_uuid"],
                        )}
                        if need.get("beneficiary_actor_uuid") else {}
                    ),
                },
                {},
            )
            if made_need.status != "ok":
                return made_need
            effects.extend(made_need.effects)
        # The seeded sections are ordinary content, so what a snapshot restores is
        # whatever it recorded - not the seed. An initiative whose Approach
        # was rewritten to two sections comes back with two.
        for order, section in enumerate(content.get("sections") or []):
            if not isinstance(section, dict):
                return SessionResult("error", reason="snapshot section is invalid")
            made_section = self.session.create_child(
                target_uuid,
                {
                    "type": "initiative_section",
                    "title": str(section.get("title") or "Section"),
                    "order": section.get("order", order),
                },
                {},
            )
            if made_section.status != "ok":
                return made_section
            effects.extend(made_section.effects)
            for index, clause in enumerate(section.get("clauses") or []):
                if not isinstance(clause, dict):
                    return SessionResult(
                        "error", reason="snapshot clause is invalid",
                    )
                made_clause = self.session.create_child(
                    made_section.value.uuid,
                    {
                        "type": "initiative_clause",
                        "text": str(clause.get("text") or ""),
                        "order": clause.get("order", index),
                    },
                    {},
                )
                if made_clause.status != "ok":
                    return made_clause
                effects.extend(made_clause.effects)
        for order, milestone in enumerate(content.get("milestones") or []):
            if not isinstance(milestone, dict):
                return SessionResult("error", reason="snapshot milestone is invalid")
            data = {
                "type": "initiative_milestone",
                "title": str(milestone.get("title") or "Milestone"),
                "order": milestone.get("order", order),
            }
            intention = str(milestone.get("intention") or "").strip()
            if intention:
                data["intention"] = intention
            for field in ("planned_at", "reached_at"):
                value = str(milestone.get(field) or "")
                if value:
                    if not self._is_iso_date(value):
                        return SessionResult(
                            "error", reason=f"snapshot milestone {field} is invalid",
                        )
                    data[field] = value
            made_milestone = self.session.create_child(target_uuid, data, {})
            if made_milestone.status != "ok":
                return made_milestone
            effects.extend(made_milestone.effects)
        columns = content.get("columns")
        if not isinstance(columns, list):
            return SessionResult("error", reason="snapshot columns are invalid")
        for column in columns:
            if not isinstance(column, dict) or not isinstance(column.get("cards"), list):
                return SessionResult("error", reason="snapshot column is invalid")
            made_column = self.session.create_child(
                target_uuid,
                {
                    "type": "kanban_column",
                    "name": str(column.get("name") or "Column"),
                    "order": column.get("order", 0),
                },
                {},
            )
            if made_column.status != "ok":
                return made_column
            effects.extend(made_column.effects)
            for card in column["cards"]:
                if not isinstance(card, dict):
                    return SessionResult("error", reason="snapshot card is invalid")
                data = {
                    "type": "kanban_card",
                    "name": str(card.get("name") or "Card"),
                    "description": str(card.get("description") or ""),
                    "participants": [],
                    "owner": None,
                    "order": card.get("order", 0),
                }
                made_card = self.session.create_child(
                    made_column.value.uuid, data, {},
                )
                if made_card.status != "ok":
                    return made_card
                effects.extend(made_card.effects)
        return SessionResult("ok", effects=effects)

    @staticmethod
    def _snapshot_error(document: object, item_type: str) -> str:
        if not isinstance(document, dict):
            return "snapshot file is invalid"
        if document.get("format") != SNAPSHOT_FORMAT:
            return "not an S-Protocol item snapshot"
        if document.get("format_version") != SNAPSHOT_FORMAT_VERSION:
            return "snapshot version is not supported"
        if document.get("item_type") != item_type:
            return f"snapshot does not contain an {item_type}"
        if not isinstance(document.get("content"), dict):
            return "snapshot content is invalid"
        return ""

    def delete_initiative(self, initiative_uuid: str) -> SessionResult:
        # The last initiative goes too. Refusing it left no way to clear a host
        # of initiatives it no longer wants, and there is nothing to protect:
        # ensure_initiative() makes a fresh empty one the next time the initiative
        # view is opened, exactly as it does on a first run.
        initiative = self._node(initiative_uuid, "initiative")
        if not initiative:
            return SessionResult("error", reason="initiative not found")
        release = self.session.end_topic_sharing(initiative_uuid)
        result = self.session.delete(initiative.uuid)
        if result.status != "ok":
            return result
        result.effects = [*release.effects, *result.effects]
        remaining = [item for item in self.initiatives() if item.uuid != initiative_uuid]
        # Clearing the selection matters when nothing is left: an initiative still
        # awaiting its peers' confirmation stays in the index as a deleted
        # node, and a remembered uuid would hand that corpse back as the
        # current initiative instead of letting ensure_initiative() start a new one.
        self._remember_initiative(remaining[0].uuid if remaining else "")
        return result

    def _create_initiative_node(self, name: str) -> ProtocolNode:
        container = self._initiative_container()
        initiative = self.session.create_child(
            container.uuid,
            {"type": "initiative", "name": name, "objective": ""},
            {},
        ).value
        for order, name in enumerate(DEFAULT_COLUMNS):
            self.session.create_child(
                initiative.uuid,
                {"type": "kanban_column", "name": name, "order": order},
                {},
            )
        for order, title in enumerate(self.DEFAULT_SECTIONS):
            self.session.create_child(
                initiative.uuid,
                {"type": "initiative_section", "title": title, "order": order},
                {},
            )
        return self.session.get_node(initiative.uuid) or initiative

    def user_profile(self) -> ProtocolNode:
        return self.session.identity

    def users(self) -> list[dict]:
        users = [self._user_info(self.session.address, self.user_profile())]
        addrs = set(self.session.peer_addresses()) - {self.session.address}
        for addr in sorted(addrs):
            profile = self._find_peer_user_profile(addr)
            users.append(self._user_info(addr, profile, self._peer_profile_uuid(addr, profile)))
        seen = set()
        out = []
        for user in users:
            # An empty id means "identity not resolved yet", not a real
            # identity - deduping on it would collapse every unresolved
            # peer into whichever one happened to come first (review K-6).
            if user["id"]:
                if user["id"] in seen:
                    continue
                seen.add(user["id"])
            out.append(user)
        return out

    # What the initiative addresses, and whose it is. Direct children of the
    # initiative, ordered, and decidable like everything else on the mandate:
    # anybody holding the topic may write one, and two clients disagreeing
    # about what is being addressed is worth a human noticing.
    #
    # The beneficiary is named by a *label* and only optionally by a uuid,
    # which inverts S-Team's rule that a name is read from the Actor and
    # never stored. The reason is in DESIGN_INITIATIVE.md 3: the beneficiary
    # of a need is very often not an actor in this system at all - a
    # customer, a neighbourhood, somebody who will never hold a key. So the
    # label is the primary fact and the uuid refines it where it can.
    def needs(self, initiative: ProtocolNode | None = None) -> list[ProtocolNode]:
        initiative = initiative or self.ensure_initiative()
        return sorted(
            [child for child in initiative.live_children()
             if child.data.get("type") == "initiative_need"],
            key=lambda node: (node.data.get("order", 0), node.uuid),
        )

    def create_need(
        self, text: str, beneficiary_label: str = "",
        beneficiary_actor_uuid: str = "",
    ) -> SessionResult:
        initiative = self.ensure_initiative()
        text = (text or "").strip()
        if not text:
            return SessionResult("error", reason="need text is required")
        data = {
            "type": "initiative_need",
            "text": text,
            "beneficiary_label": (beneficiary_label or "").strip(),
            "order": self.session.next_child_order(
                initiative.uuid, "initiative_need",
            ),
        }
        if actor := (beneficiary_actor_uuid or "").strip():
            data["beneficiary_actor_uuid"] = actor
        return self.session.create_child(initiative.uuid, data, {})

    def update_need(
        self, need_uuid: str, text: str | None = None,
        beneficiary_label: str | None = None,
        beneficiary_actor_uuid: str | None = None,
    ) -> SessionResult:
        """Rewrite a need. None leaves a field alone; "" clears it.

        The actor uuid is not checked against the actors this client holds.
        A beneficiary somebody else can resolve and we cannot is the ordinary
        case for a topic shared across two clients, and refusing it here
        would make the guard a statement about who this replica happens to
        know rather than about the need.
        """
        need = self._node(need_uuid, "initiative_need")
        if not need:
            return SessionResult("error", reason="need not found")
        data = dict(need.data)
        if text is not None:
            text = text.strip()
            if not text:
                return SessionResult("error", reason="need text is required")
            data["text"] = text
        if beneficiary_label is not None:
            data["beneficiary_label"] = beneficiary_label.strip()
        if beneficiary_actor_uuid is not None:
            actor = beneficiary_actor_uuid.strip()
            if actor:
                data["beneficiary_actor_uuid"] = actor
            else:
                data.pop("beneficiary_actor_uuid", None)
        return self.session.modify(need.uuid, data, need.weights)

    def delete_need(self, need_uuid: str) -> SessionResult:
        need = self._node(need_uuid, "initiative_need")
        if not need:
            return SessionResult("error", reason="need not found")
        return self.session.delete(need.uuid)

    def move_need(self, need_uuid: str, index: int) -> SessionResult:
        initiative = self.ensure_initiative()
        need = self._node(need_uuid, "initiative_need")
        if not need or need.parent_uuid != initiative.uuid:
            return SessionResult("error", reason="need not found")
        return self.session.move_child_to_parent_index(
            need.uuid, initiative.uuid, index,
        )

    # The Approach: sections holding clauses, the same two-level shape a Team
    # Agreement uses. A section holds clauses and a clause holds nothing; the
    # shape permits nesting and this document does not use it.
    #
    # A new initiative is seeded with two sections, and they are *ordinary
    # content from the moment they exist* - renamable, reorderable,
    # deletable, and a third is added by the same composer. Nothing marks the
    # two as special, because nothing about them is: roadmap and risk lines
    # behave identically. Making
    # them separate node types would buy a portfolio view that could ask for
    # every open risk, and would charge every initiative that thinks in some
    # other shape for it.
    DEFAULT_SECTIONS = ("Roadmap", "Risks & Chances")
    CLAUSE_PARENT_TYPES = frozenset({"initiative_section"})

    def sections(self, initiative: ProtocolNode | None = None) -> list[ProtocolNode]:
        initiative = initiative or self.ensure_initiative()
        return sorted(
            [child for child in initiative.live_children()
             if child.data.get("type") == "initiative_section"],
            key=lambda node: (node.data.get("order", 0), node.uuid),
        )

    def clauses(self, parent: ProtocolNode) -> list[ProtocolNode]:
        return sorted(
            [child for child in parent.live_children()
             if child.data.get("type") == "initiative_clause"],
            key=lambda node: (node.data.get("order", 0), node.uuid),
        )

    def create_section(self, title: str) -> SessionResult:
        initiative = self.ensure_initiative()
        title = (title or "").strip()
        if not title:
            return SessionResult("error", reason="section title is required")
        return self.session.create_child(
            initiative.uuid,
            {
                "type": "initiative_section",
                "title": title,
                "order": self.session.next_child_order(
                    initiative.uuid, "initiative_section",
                ),
            },
            {},
        )

    def rename_section(self, section_uuid: str, title: str) -> SessionResult:
        section = self._node(section_uuid, "initiative_section")
        if not section:
            return SessionResult("error", reason="section not found")
        title = (title or "").strip()
        if not title:
            return SessionResult("error", reason="section title is required")
        data = dict(section.data)
        data["title"] = title
        return self.session.modify(section.uuid, data, section.weights)

    def delete_section(self, section_uuid: str) -> SessionResult:
        section = self._node(section_uuid, "initiative_section")
        if not section:
            return SessionResult("error", reason="section not found")
        return self.session.delete(section.uuid)

    def move_section(self, section_uuid: str, index: int) -> SessionResult:
        initiative = self.ensure_initiative()
        section = self._node(section_uuid, "initiative_section")
        if not section or section.parent_uuid != initiative.uuid:
            return SessionResult("error", reason="section not found")
        return self.session.move_child_to_parent_index(
            section.uuid, initiative.uuid, index,
        )

    def create_clause(self, parent_uuid: str, text: str) -> SessionResult:
        parent = self._clause_parent(parent_uuid)
        if not parent:
            return SessionResult("error", reason="clause parent not found")
        text = (text or "").strip()
        if not text:
            return SessionResult("error", reason="clause text is required")
        return self.session.create_child(
            parent.uuid,
            {
                "type": "initiative_clause",
                "text": text,
                "order": self.session.next_child_order(
                    parent.uuid, "initiative_clause",
                ),
            },
            {},
        )

    def update_clause(self, clause_uuid: str, text: str) -> SessionResult:
        clause = self._node(clause_uuid, "initiative_clause")
        if not clause:
            return SessionResult("error", reason="clause not found")
        text = (text or "").strip()
        if not text:
            return SessionResult("error", reason="clause text is required")
        data = dict(clause.data)
        data["text"] = text
        return self.session.modify(clause.uuid, data, clause.weights)

    def delete_clause(self, clause_uuid: str) -> SessionResult:
        clause = self._node(clause_uuid, "initiative_clause")
        if not clause:
            return SessionResult("error", reason="clause not found")
        return self.session.delete(clause.uuid)

    def move_clause(self, clause_uuid: str, index: int) -> SessionResult:
        """Reorder a clause among its own siblings.

        Within its parent and never across one: moving a clause from the
        Risks section into Strategy is deleting it there and writing it here,
        and a drag that quietly rewrote which section a line belongs to would
        be the interface deciding something the section headings say.
        """
        clause = self._node(clause_uuid, "initiative_clause")
        if not clause:
            return SessionResult("error", reason="clause not found")
        return self.session.move_child_to_parent_index(
            clause.uuid, clause.parent_uuid, index,
        )

    def _clause_parent(self, parent_uuid: str) -> ProtocolNode | None:
        node = self.session.protocol.index.get(parent_uuid)
        if (
            node
            and node.data.get("type") in self.CLAUSE_PARENT_TYPES
            and self.owns_node(parent_uuid)
        ):
            return node
        return None

    # Assessed Impact ---------------------------------------------------

    REALITY_PARENT_TYPES = frozenset({"initiative", "initiative_milestone"})

    def realities(self, parent: ProtocolNode) -> list[ProtocolNode]:
        return sorted(
            [
                child for child in parent.live_children()
                if child.data.get("type") == "initiative_reality"
            ],
            key=lambda node: (node.created_at, node.uuid),
        )

    def create_reality(self, parent_uuid: str, text: str) -> SessionResult:
        parent = self.session.protocol.index.get(parent_uuid)
        if (
            not parent
            or parent.data.get("type") not in self.REALITY_PARENT_TYPES
            or not self.owns_node(parent.uuid)
        ):
            return SessionResult("error", reason="reality parent not found")
        text = (text or "").strip()
        if not text:
            return SessionResult("error", reason="assessment text is required")
        return self.session.create_child(
            parent.uuid,
            {
                "type": "initiative_reality",
                "author_actor_uuid": self.user_profile().uuid,
                "text": text,
                "recorded_at": self._recorded_now(),
            },
            {},
        )

    def delete_reality(self, reality_uuid: str) -> SessionResult:
        reality = self._node(reality_uuid, "initiative_reality")
        if not reality:
            return SessionResult("error", reason="assessment not found")
        if reality.data.get("author_actor_uuid") != self.user_profile().uuid:
            return SessionResult(
                "error", reason="only the author can delete an assessment",
            )
        return self.session.delete(reality.uuid)

    # Resources ---------------------------------------------------------

    def investments(
        self, initiative: ProtocolNode | None = None,
        actor_uuid: str | None = None,
    ) -> list[ProtocolNode]:
        initiative = (
            self.session.get_node(initiative.uuid) if initiative
            else self.ensure_initiative()
        ) or initiative
        return sorted(
            [
                child for child in initiative.live_children()
                if child.data.get("type") == "initiative_investment"
                and (
                    actor_uuid is None
                    or child.data.get("actor_uuid") == actor_uuid
                )
            ],
            key=lambda node: (node.created_at, node.uuid),
        )

    def create_investment(
        self, actor_uuid: str, availability: str,
    ) -> SessionResult:
        initiative = self.ensure_initiative()
        actor_uuid = (actor_uuid or "").strip()
        if actor_uuid != self.user_profile().uuid:
            return SessionResult(
                "error", reason="only the actor can record their availability",
            )
        availability = (availability or "").strip()
        if not availability:
            return SessionResult("error", reason="availability is required")
        chain = self.investments(initiative, actor_uuid)
        previous_uuid = chain[-1].uuid if chain else ""
        return self.session.create_child(
            initiative.uuid,
            {
                "type": "initiative_investment",
                "actor_uuid": actor_uuid,
                "availability": availability,
                "previous_uuid": previous_uuid,
                "recorded_at": self._recorded_now(),
            },
            {},
        )

    def delete_investment(self, investment_uuid: str) -> SessionResult:
        investment = self._node(investment_uuid, "initiative_investment")
        if not investment:
            return SessionResult("error", reason="availability record not found")
        if investment.data.get("actor_uuid") != self.user_profile().uuid:
            return SessionResult(
                "error", reason="only the actor can delete their availability",
            )
        return self.session.delete(investment.uuid)

    def initiative_resources(self, initiative: ProtocolNode) -> list[dict]:
        """Resolve topic holders and the persisted head of each record chain."""
        peers = self.session.peer_perspectives_for_topic()
        holder_addresses = {self.session.address, *peers.keys()}
        users = [
            user for user in self.users()
            if user.get("address") in holder_addresses
        ]
        resources = []
        for user in users:
            actor_uuid = str(user.get("id") or "")
            records = self.investments(initiative, actor_uuid) if actor_uuid else []
            # A reachable peer's own perspective is the primary source. The
            # adopted local record remains the last-seen fallback when that
            # perspective is not available.
            peer_root = peers.get(str(user.get("address") or ""))
            if peer_root and actor_uuid:
                peer_records = sorted(
                    [
                        node for node in self._descendants(peer_root)
                        if node.data.get("type") == "initiative_investment"
                        and node.data.get("actor_uuid") == actor_uuid
                        and not node.deleted
                    ],
                    key=lambda node: (node.created_at, node.uuid),
                )
                if peer_records:
                    records = peer_records
            resources.append({
                "actor_uuid": actor_uuid,
                "user": user,
                "head": records[-1].to_dict() if records else None,
                "history": [node.to_dict() for node in records[:-1]],
            })
        return resources

    # Milestones --------------------------------------------------------

    def milestones(
        self, initiative: ProtocolNode | None = None,
    ) -> list[ProtocolNode]:
        initiative = initiative or self.ensure_initiative()
        return sorted(
            [
                child for child in initiative.live_children()
                if child.data.get("type") == "initiative_milestone"
            ],
            key=lambda node: (node.data.get("order", 0), node.uuid),
        )

    def current_milestone(
        self, initiative: ProtocolNode | None = None,
    ) -> ProtocolNode | None:
        return next(
            (
                milestone for milestone in self.milestones(initiative)
                if not milestone.data.get("reached_at")
            ),
            None,
        )

    def create_milestone(
        self, title: str, planned_at: str = "", intention: str = "",
    ) -> SessionResult:
        initiative = self.ensure_initiative()
        title = (title or "").strip()
        if not title:
            return SessionResult("error", reason="milestone title is required")
        planned_at = (planned_at or "").strip()
        if planned_at and not self._is_iso_date(planned_at):
            return SessionResult("error", reason="planned date must be an ISO date")
        data = {
            "type": "initiative_milestone",
            "title": title,
            "order": self.session.next_child_order(
                initiative.uuid, "initiative_milestone",
            ),
        }
        if planned_at:
            data["planned_at"] = planned_at
        intention = (intention or "").strip()
        if intention:
            data["intention"] = intention
        return self.session.create_child(initiative.uuid, data, {})

    def update_milestone(
        self, milestone_uuid: str, title: str | None = None,
        planned_at: str | None = None, intention: str | None = None,
    ) -> SessionResult:
        milestone = self._node(milestone_uuid, "initiative_milestone")
        if not milestone:
            return SessionResult("error", reason="milestone not found")
        data = dict(milestone.data)
        if title is not None:
            title = title.strip()
            if not title:
                return SessionResult("error", reason="milestone title is required")
            data["title"] = title
        if planned_at is not None:
            planned_at = planned_at.strip()
            if planned_at and not self._is_iso_date(planned_at):
                return SessionResult(
                    "error", reason="planned date must be an ISO date",
                )
            if planned_at:
                data["planned_at"] = planned_at
            else:
                data.pop("planned_at", None)
        if intention is not None:
            intention = intention.strip()
            if intention:
                data["intention"] = intention
            else:
                data.pop("intention", None)
        return self.session.modify(milestone.uuid, data, milestone.weights)

    def claim_milestone_reached(
        self, milestone_uuid: str, value: str = "",
    ) -> SessionResult:
        milestone = self._node(milestone_uuid, "initiative_milestone")
        if not milestone:
            return SessionResult("error", reason="milestone not found")
        value = (value or "").strip()
        if value and not self._is_iso_date(value):
            return SessionResult("error", reason="reached date must be an ISO date")
        data = dict(milestone.data)
        if value:
            data["reached_at"] = value
        else:
            data.pop("reached_at", None)
        return self.session.modify(milestone.uuid, data, milestone.weights)

    def delete_milestone(self, milestone_uuid: str) -> SessionResult:
        milestone = self._node(milestone_uuid, "initiative_milestone")
        if not milestone:
            return SessionResult("error", reason="milestone not found")
        return self.session.delete(milestone.uuid)

    def move_milestone(self, milestone_uuid: str, index: int) -> SessionResult:
        initiative = self.ensure_initiative()
        milestone = self._node(milestone_uuid, "initiative_milestone")
        if not milestone or milestone.parent_uuid != initiative.uuid:
            return SessionResult("error", reason="milestone not found")
        return self.session.move_child_to_parent_index(
            milestone.uuid, initiative.uuid, index,
        )

    @staticmethod
    def _recorded_now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    @classmethod
    def _descendants(cls, node: ProtocolNode):
        for child in node.live_children():
            yield child
            yield from cls._descendants(child)

    def create_column(self, name: str) -> SessionResult:
        initiative = self.ensure_initiative()
        return self.session.create_child(
            initiative.uuid,
            {
                "type": "kanban_column",
                "name": name or "Column",
                "order": self.session.next_child_order(
                    initiative.uuid, "kanban_column",
                ),
            },
            {},
        )

    def rename_column(self, column_uuid: str, name: str) -> SessionResult:
        column = self._node(column_uuid, "kanban_column")
        if not column:
            return SessionResult("error", reason="column not found")
        data = dict(column.data)
        data["name"] = name or "Column"
        return self.session.modify(column.uuid, data, column.weights)

    def delete_column(self, column_uuid: str) -> SessionResult:
        column = self._node(column_uuid, "kanban_column")
        if not column:
            return SessionResult("error", reason="column not found")
        return self.session.delete(column.uuid)

    def move_column(self, column_uuid: str, index: int) -> SessionResult:
        initiative = self.ensure_initiative()
        column = self._node(column_uuid, "kanban_column")
        if not column or column.parent_uuid != initiative.uuid:
            return SessionResult("error", reason="column not found")
        return self.session.move_child_to_parent_index(
            column.uuid, initiative.uuid, index,
        )

    def create_card(self, column_uuid: str, name: str,
                    description: str = "",
                    participants: list[str] | None = None,
                    owner: str | None = None) -> SessionResult:
        column = self._node(column_uuid, "kanban_column")
        if not column:
            return SessionResult("error", reason="column not found")
        participants = participants or []
        return self.session.create_child(
            column.uuid,
            {
                "type": "kanban_card",
                "name": name or "Card",
                "description": description or "",
                "participants": participants,
                "owner": self._normalize_owner(owner, participants),
                "order": self.session.next_child_order(
                    column.uuid, "kanban_card",
                ),
            },
            {},
        )

    def update_card(self, card_uuid: str, name: str,
                    description: str = "",
                    participants: list[str] | None = None,
                    owner: str | None = None,
                    expected_content_hash: str | None = None) -> SessionResult:
        card = self._node(card_uuid, "kanban_card")
        if not card:
            return SessionResult("error", reason="card not found")
        # Lost-update guard (review U-3): the modal captured the card's
        # content_hash when it opened; if the card changed since (a peer
        # edit landed, or auto-adopt merged one) we'd silently overwrite
        # that with the stale form values. Reject instead so the user can
        # re-open against the merged card. Optional - callers that don't
        # pass a hash keep the old last-write-wins behavior.
        # Deliberately content_hash, not state_hash: only the fields this
        # form actually edits may block a save. state_hash also covers the
        # card's children, so a comment - which this very class documents as
        # touching the subtree hash but not the card's own revision - would
        # otherwise lock the user out of saving their own edit.
        if expected_content_hash is not None and expected_content_hash != card.content_hash:
            return SessionResult("error", reason="card changed while you were editing")
        participants = participants or []
        data = dict(card.data)
        data.update({
            "name": name or "Card",
            "description": description or "",
            "participants": participants,
            "owner": self._normalize_owner(owner, participants),
        })
        return self.session.modify(card.uuid, data, card.weights)

    @staticmethod
    def _normalize_owner(owner: str | None, participants: list[str]) -> str | None:
        return owner if owner and owner in participants else None

    def delete_card(self, card_uuid: str) -> SessionResult:
        card = self._node(card_uuid, "kanban_card")
        if not card:
            return SessionResult("error", reason="card not found")
        return self.session.delete(card.uuid)

    def create_card_comment(self, card_uuid: str, text: str) -> SessionResult:
        # A comment is an immutable child node of the card (the agenda_item
        # pattern): concurrent comments set-union merge, and appending one
        # touches only the card's subtree hash, not its own content revision.
        card = self._node(card_uuid, "kanban_card")
        if not card:
            return SessionResult("error", reason="card not found")
        text = (text or "").strip()
        if not text:
            return SessionResult("error", reason="comment text is required")
        return self.session.create_child(
            card.uuid,
            {
                "type": "card_comment",
                "text": text,
                "author": self.user_profile().uuid,
            },
            {},
        )

    def card_comments(self, card: ProtocolNode) -> list[ProtocolNode]:
        return sorted(
            [child for child in card.live_children()
             if child.data.get("type") == "card_comment"],
            key=lambda node: node.created_at,
        )

    def delete_card_comment(self, comment_uuid: str) -> SessionResult:
        comment = self._node(comment_uuid, "card_comment")
        if not comment:
            return SessionResult("error", reason="comment not found")
        if comment.data.get("author") != self.user_profile().uuid:
            return SessionResult("error", reason="only the author can delete a comment")
        return self.session.delete(comment.uuid)

    def create_card_attachment(self, card_uuid: str,
                               attachment: dict) -> SessionResult:
        # A child node, not a field on the card, for the same reason comments
        # are: two people attaching at once then set-union merge instead of
        # diverging the card's own content. Core's blob machinery walks every
        # node's "attachments", so publication, peer fetch and GC need no
        # S-Initiative-specific knowledge.
        card = self._node(card_uuid, "kanban_card")
        if not card:
            return SessionResult("error", reason="card not found")
        references = canonical_attachments([attachment])
        if not references:
            return SessionResult("error", reason="a valid file reference is required")
        return self.session.create_child(
            card.uuid,
            {
                "type": "card_attachment",
                "author": self.user_profile().uuid,
                "attachments": references,
            },
            {},
        )

    def card_attachments(self, card: ProtocolNode) -> list[ProtocolNode]:
        return sorted(
            [child for child in card.live_children()
             if child.data.get("type") == "card_attachment"],
            key=lambda node: node.created_at,
        )

    def delete_card_attachment(self, attachment_uuid: str) -> SessionResult:
        attachment = self._node(attachment_uuid, "card_attachment")
        if not attachment:
            return SessionResult("error", reason="attachment not found")
        if attachment.data.get("author") != self.user_profile().uuid:
            return SessionResult(
                "error", reason="only the author can remove an attachment",
            )
        return self.session.delete(attachment.uuid)

    def move_card(self, card_uuid: str, column_uuid: str, index: int) -> SessionResult:
        card = self._node(card_uuid, "kanban_card")
        column = self._node(column_uuid, "kanban_column")
        if not card or not column:
            return SessionResult("error", reason="card or column not found")

        moved = self.session.move_child_to_parent_index(
            card.uuid, column.uuid, index,
        )
        if moved.status != "ok":
            return moved
        card = self.session.protocol.index[card.uuid]
        stamped = self.session.modify(
            card.uuid,
            {**card.data, "position_updated_at": self._position_now()},
            card.weights,
        )
        if stamped.status != "ok":
            return stamped
        return SessionResult(
            "ok", value=card.uuid,
            effects=[*moved.effects, *stamped.effects],
        )

    def accept_peer_node(self, source_addr: str, node_uuid: str,
                         adopt_absence: bool = False) -> SessionResult:
        # Session adopts an existing node's own fields shallowly (containers
        # keep their cards) and grafts only a brand-new subtree - no
        # initiative-specific container handling is needed anymore.
        local_exists = node_uuid in self.session.protocol.index
        if ((local_exists and not self.owns_node(node_uuid))
                or (not adopt_absence
                    and not self.owns_node(node_uuid, source_addr))):
            return SessionResult("error", reason="node is not part of a Kanban board")
        return self.session.accept_peer_node(source_addr, node_uuid, adopt_absence)

    def rollback_peer_node(self, source_addr: str,
                           node_uuid: str,
                           rollback_absence: bool = False) -> SessionResult:
        if (not self.owns_node(node_uuid)
                or (not rollback_absence
                    and not self.owns_node(node_uuid, source_addr))):
            return SessionResult("error", reason="node is not part of a Kanban board")
        return self.session.rollback_peer_node(
            source_addr, node_uuid, rollback_absence,
        )

    def react_to_node(
        self, source_addr: str, node_uuid: str, reaction: str,
        absent: bool = False,
    ) -> SessionResult:
        if reaction == "adopt":
            return self.accept_peer_node(source_addr, node_uuid, absent)
        if reaction == "rollback":
            return self.rollback_peer_node(source_addr, node_uuid, absent)
        return SessionResult("error", reason="unknown reaction")

    def publish_adoption_metadata(
        self, initiative: ProtocolNode | None = None,
    ) -> None:
        """Translate this initiative's mode into Core's declared handling.

        Everything written here is derived from the mode alone, so it changes
        only when the mode does - republishing the same values decides nothing
        and Core treats it as a no-op. Which team-view records a selective
        mode protects is *not* written down: involvement and responsibility
        can move through the change itself, so the resolver answers from both
        versions at the moment it matters. See Core's
        DESIGN_ADOPTION_METADATA.md.
        """
        initiative = initiative or self.ensure_initiative()
        mode = self.auto_adopt_mode(initiative)
        # "Always" adopts team views outright. Selective modes hold existing
        # team-view nodes for the resolver and classify new ones individually.
        # "Never" holds every team-view change. Author-scoped records are
        # explicitly exempted below in every mode.
        self.session.set_topic_adoption_default(
            initiative.uuid,
            adopt="auto" if mode == "always" else "hold",
            additions="hold" if mode == "never" else "auto",
        )
        self.session.set_topic_reconciliation_policies(
            initiative.uuid, (INITIATIVE_POSITION_POLICY,),
        )
        self.session.set_adoption_resolver(
            initiative.uuid,
            lambda peer_node, local_node, peer_addr, uuid=initiative.uuid: (
                self._resolve_held_team_view(uuid, peer_node, local_node)
            ),
        )
        # Bound to this initiative: a client can hold several, each with its own
        # mode, and the one being reconciled is not necessarily the one on
        # screen.
        self.session.set_adoption_classifier(
            initiative.uuid,
            lambda node, default, uuid=initiative.uuid: (
                self._classify_incoming_node(uuid, node)
            ),
        )
        # Agendas are Session's: an agenda item is projected from its author's
        # perspective, never adopted, so a copy of one has no business in this
        # tree. The classifier covers an incoming item; this covers any already
        # held, which a classifier is never asked about.
        self.session.set_adoption_metadata_for_subtree(
            initiative.uuid, adopt="never", additions="never",
            node_type="agenda_item",
        )
        # Individual views and contributions are author-scoped records, not
        # candidate values for a shared field. They join the local projection
        # regardless of the team-view setting and remain writable only by the
        # author through the domain guards above.
        for node_type in AUTHOR_SCOPED_NODE_TYPES:
            self.session.set_adoption_metadata_for_subtree(
                initiative.uuid, adopt="auto", additions="auto",
                node_type=node_type,
            )

    def _resolve_held_team_view(
        self, initiative_uuid: str, peer_node, local_node,
    ) -> str:
        """Whether a held node settles now or waits for this client.

        Asked at the moment of decision rather than recorded, because what it
        reads moves: responsibility, participation, and beneficiaries can
        change through the peer edit itself. A verdict written down against
        only one side could therefore become false because of the act it lets
        through.

        Nothing is ever refused: the mode says which team-view changes this
        client wants to look at, not which changes are illegitimate.
        """
        initiative = self.session.protocol.index.get(initiative_uuid)
        mode = self.auto_adopt_mode(initiative) if initiative else "always"
        # Test both sides. Otherwise a change that removes me from a card
        # would cease to "involve me" in the proposed value and adopt itself.
        allows = all(
            self._auto_adopt_allows_node(mode, node)
            for node in (peer_node, local_node)
            if node is not None
        )
        return "adopt" if allows else "defer"

    def _classify_incoming_node(self, initiative_uuid, node):
        """How a node this initiative does not yet hold is to be handled.

        Asked once, at first sight, because a node that does not exist locally
        carries no entry and its parent's `additions` cannot tell one kind of
        incoming node from another. Two questions only:

        - an agenda item is Session's, projected from its author's perspective
          and never adopted, so a copy of one has no business in this tree;
        - a team-view record arriving already involving me or naming me as
          responsible is one the corresponding mode protects, and the entry
          has to say so before it is taken rather than after.
        """
        node_type = node.data.get("type")
        if node_type == "agenda_item":
            return {"adopt": "never", "additions": "never"}
        if node_type in AUTHOR_SCOPED_NODE_TYPES:
            return {"adopt": "auto", "additions": "auto"}
        initiative = self.session.protocol.index.get(initiative_uuid)
        if not self._auto_adopt_allows_node(
            self.auto_adopt_mode(initiative), node,
        ):
            return {"adopt": "hold", "additions": "auto"}
        return None

    def adopt_incoming_changes(self, initiative: ProtocolNode | None = None) -> bool:
        initiative = initiative or self.ensure_initiative()
        # Declaring can itself adopt: Core applies a changed declaration when
        # it is written, so by the time the pass below runs there may be
        # nothing left to do. Report what happened to the initiative, not which of
        # the two calls did it.
        held = self.session.protocol.index.get(initiative.uuid)
        before = held.state_hash if held else None
        self.publish_adoption_metadata(initiative)
        changed = self.session.reapply_adoption(initiative.uuid)
        held = self.session.protocol.index.get(initiative.uuid)
        return changed or (held.state_hash if held else None) != before

    @staticmethod
    def _position_now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")

    def _auto_adopt_allows_node(self, mode: str, node: ProtocolNode | None) -> bool:
        if mode == "always":
            return True
        if mode == "never":
            return False
        if not node:
            return True
        if mode == "not_owner":
            return not self._node_is_my_responsibility(node)
        if mode == "not_member":
            return not self._node_involves_me(node)
        return True

    def _node_involves_me(self, node: ProtocolNode) -> bool:
        data = node.data or {}
        my_id = self.user_profile().uuid
        if data.get("type") == "kanban_card":
            return (
                data.get("owner") == my_id
                or my_id in (data.get("participants") or [])
            )
        if data.get("type") == "initiative_need":
            return data.get("beneficiary_actor_uuid") == my_id
        return False

    def _node_is_my_responsibility(self, node: ProtocolNode) -> bool:
        data = node.data or {}
        return (
            data.get("type") == "kanban_card"
            and data.get("owner") == self.user_profile().uuid
        )

    def on_peer_update(self) -> SessionResult:
        changed = self.adopt_all_incoming_changes()
        if not changed:
            return SessionResult("ok", value=False)
        return SessionResult("ok", value=True)

    def adopt_all_incoming_changes(self) -> bool:
        changed = False
        for initiative in self.initiatives():
            active = self._is_active_discussion_node(initiative.uuid)
            mode = self.auto_adopt_mode(initiative)
            self.session.trace_event(
                "initiative.adopt_all_incoming_changes_check",
                initiative_uuid=initiative.uuid,
                active=active,
                mode=mode,
            )
            if active:
                changed = self.adopt_incoming_changes(initiative) or changed
        return changed

    def transition_events(
        self, initiative_uuid: str | None = None, network: dict | None = None,
    ) -> list[dict]:
        initiative = (
            self._node(initiative_uuid, "initiative")
            if initiative_uuid else self._selected_initiative(self.initiatives())
        )
        if not initiative:
            return []
        initiative_uuid = initiative_uuid or initiative.uuid
        events = []
        for addr in self.session.peer_addresses():
            if not self.session.peer_discusses_node(addr, initiative.uuid):
                continue
            liveness = self._peer_liveness(addr, initiative_uuid, network)
            for event in self.session.analyze_peer_transitions(addr, initiative_uuid):
                if not self._is_displayed_divergence(addr, event):
                    continue
                # A peer without a live, explicitly selected topic channel
                # cannot yet react to a local revision. Keep its cached
                # perspective, but do not turn that expected silence into a
                # lamp/counter entry. Confirmed divergences and incoming peer
                # changes remain visible.
                if (
                    event["stage"] == "in_flight"
                    and liveness.get("state") != "alive"
                ):
                    continue
                event["changes"] = (
                    [] if event["type"] == "in_agreement"
                    else self.describe_peer_changes(
                        addr, event.get("node_uuid"),
                        authored_locally=event["type"] in (
                            "local_made_changes", "peer_missing_node",
                        ),
                    )
                )
                events.append(event)
        return events

    def _is_displayed_divergence(self, peer_addr: str, event: dict) -> bool:
        node_uuid = event.get("node_uuid")
        local = self.session.protocol.index.get(node_uuid)
        peer = self.session.get_cached_peer_subtree(peer_addr, node_uuid)
        node = local or peer
        return bool(
            node and node.data.get("type") in DISPLAYED_DIVERGENCE_TYPES
        )

    def _peer_liveness(
        self, peer_addr: str, topic_uuid: str, network: dict | None = None,
    ) -> dict:
        if network and network.get("_include_all"):
            return {"state": "alive"}
        peer_info = ((network or {}).get("peers") or {}).get(peer_addr) or {}
        if peer_info.get("channel_liveness") is not None:
            return peer_info["channel_liveness"]
        if network is not None:
            return {"state": "unknown"}
        resolver = getattr(
            self.collaboration, "peer_liveness_for_address", None,
        )
        if not resolver:
            return {"state": "unknown"}
        return resolver(peer_addr, topic_uuid) or {"state": "unknown"}

    def describe_peer_changes(self, peer_addr: str,
                              node_uuid: str | None,
                              authored_locally: bool = False) -> list[dict]:
        """Describe the peer's current version relative to this client.

        These are semantic current-version differences, not an audit log:
        in a true two-sided divergence they intentionally say what the peer
        version contains relative to mine, without claiming which historical
        operation produced it.
        """
        if not node_uuid:
            return []
        local = self.session.protocol.index.get(node_uuid)
        peer = self.session.get_cached_peer_subtree(peer_addr, node_uuid)
        if not local and not peer:
            return []
        if (local and peer
                and not (local.state_hash != peer.state_hash
                         or local.parent_uuid != peer.parent_uuid)):
            return []
        node = peer or local
        node_type = node.data.get("type") or "node"
        node_label = {
            "kanban_card": "Card",
            "kanban_column": "Column",
            "initiative": "Initiative",
            "agenda_item": "Discussion topic",
            RELATIONSHIP_TYPE: "Connection",
            "initiative_need": "Need",
            "initiative_section": "Section",
            "initiative_clause": "Clause",
            "initiative_milestone": "Milestone",
            "initiative_reality": "Assessment",
            "initiative_investment": "Availability",
        }.get(node_type, "Item")
        if not local:
            return self._annotate_authorship([{
                "kind": "presence",
                "field": "node",
                "label": node_label,
                "local_value": None,
                "peer_value": "present",
                "summary": f"{node_label} exists only in the peer version",
                "local_summary": f"Keep {node_label.lower()} absent",
            }], node_label, authored_locally)
        if not peer:
            return self._annotate_authorship([{
                "kind": "presence",
                "field": "node",
                "label": node_label,
                "local_value": "present",
                "peer_value": None,
                "summary": f"{node_label} exists only in your version",
                "local_summary": f"Keep your {node_label.lower()}",
            }], node_label, authored_locally)

        changes: list[dict] = []

        def add_field(field: str, label: str, summary: str,
                      local_summary: str) -> None:
            changes.append({
                "kind": "field",
                "field": field,
                "label": label,
                "local_value": local.data.get(field),
                "peer_value": peer.data.get(field),
                "summary": summary,
                "local_summary": local_summary,
            })

        if local.deleted != peer.deleted:
            changes.append({
                "kind": "deletion",
                "field": "deleted",
                "label": node_label,
                "local_value": local.deleted,
                "peer_value": peer.deleted,
                "summary": (
                    f"{node_label} is deleted in the peer version"
                    if peer.deleted else
                    f"{node_label} is present in the peer version"
                ),
                "local_summary": (
                    f"Keep your {node_label.lower()} present"
                    if peer.deleted else
                    f"Keep your {node_label.lower()} deleted"
                ),
            })

        # An initiative is a topic root, and every peer grafts a topic under its
        # own local container - so the two parents always differ, and always
        # will. That is how topics are shared, not something either side
        # did: reporting it as a move told the reader the initiative had been
        # "moved to <the peer's container>", and offered them an initiative move
        # to adopt when all that changed was the name. Session already
        # excludes it from classification; this is the same exclusion for
        # the description.
        if node_type != "initiative" and local.parent_uuid != peer.parent_uuid:
            local_parent = self.session.protocol.index.get(local.parent_uuid)
            peer_parent = self.session.get_cached_peer_subtree(
                peer_addr, peer.parent_uuid,
            )
            local_parent_name = self._node_display_name(local_parent)
            peer_parent_name = self._node_display_name(peer_parent)
            changes.append({
                "kind": "move",
                "field": "parent_uuid",
                "label": "Column" if node_type == "kanban_card" else "Location",
                "local_value": local.parent_uuid,
                "peer_value": peer.parent_uuid,
                "local_label": local_parent_name,
                "peer_label": peer_parent_name,
                "summary": (
                    f'Move from "{local_parent_name}" to "{peer_parent_name}"'
                ),
                "local_summary": (
                    f'Move from "{peer_parent_name}" to "{local_parent_name}"'
                ),
            })
        elif (local.data.get("order") != peer.data.get("order")
              and "order" in (set(local.data) | set(peer.data))):
            changes.append({
                "kind": "position",
                "field": "order",
                "label": "Position",
                "local_value": local.data.get("order"),
                "peer_value": peer.data.get("order"),
                "summary": "Use peer position",
                "local_summary": "Keep your current position",
            })

        scalar_labels = {
            "name": "Name",
            "description": "Description",
            "objective": "Intention",
            "text": "Text",
            "priority": "Priority",
            # Content, so a disagreement about them is worth seeing. The
            # claimed pair reads as a claim rather than a correction.
            "planned_start": "Planned start",
            "planned_end": "Planned end",
            "actual_start": "Started",
            "actual_end": "Ended",
            "beneficiary_label": "Beneficiary",
            "title": "Title",
            "intention": "Intention",
            "planned_at": "Planned date",
            "reached_at": "Reached",
            "availability": "Availability",
        }
        for field, label in scalar_labels.items():
            local_value = local.data.get(field)
            peer_value = peer.data.get(field)
            if local_value == peer_value:
                continue
            if field in ("description", "objective"):
                summary = f"Use peer {label.lower()}"
                local_summary = f"Keep your {label.lower()}"
            else:
                summary = (
                    f"{label}: {self._display_value(local_value)}"
                    f" → {self._display_value(peer_value)}"
                )
                local_summary = (
                    f"{label}: {self._display_value(peer_value)}"
                    f" → {self._display_value(local_value)}"
                )
            add_field(field, label, summary, local_summary)

        if node_type == "kanban_card":
            local_participants = set(local.data.get("participants") or [])
            peer_participants = set(peer.data.get("participants") or [])
            added = sorted(peer_participants - local_participants)
            removed = sorted(local_participants - peer_participants)
            if added or removed:
                added_labels = [self._participant_name(item) for item in added]
                removed_labels = [self._participant_name(item) for item in removed]
                parts = []
                if added_labels:
                    if len(added_labels) == 1:
                        parts.append(f"Add {added_labels[0]} as participant")
                    else:
                        parts.append(f"Add {', '.join(added_labels)} as participants")
                if removed_labels:
                    if len(removed_labels) == 1:
                        parts.append(f"Remove {removed_labels[0]} as participant")
                    else:
                        parts.append(f"Remove {', '.join(removed_labels)} as participants")
                local_parts = []
                if removed_labels:
                    if len(removed_labels) == 1:
                        local_parts.append(f"Add {removed_labels[0]} as participant")
                    else:
                        local_parts.append(f"Add {', '.join(removed_labels)} as participants")
                if added_labels:
                    if len(added_labels) == 1:
                        local_parts.append(f"Remove {added_labels[0]} as participant")
                    else:
                        local_parts.append(f"Remove {', '.join(added_labels)} as participants")
                changes.append({
                    "kind": "participants",
                    "field": "participants",
                    "label": "Participants",
                    "local_value": sorted(local_participants),
                    "peer_value": sorted(peer_participants),
                    "added": added,
                    "removed": removed,
                    "added_labels": added_labels,
                    "removed_labels": removed_labels,
                    "summary": "; ".join(parts),
                    "local_summary": "; ".join(local_parts),
                })
            local_owner = local.data.get("owner")
            peer_owner = peer.data.get("owner")
            if local_owner != peer_owner:
                changes.append({
                    "kind": "owner",
                    "field": "owner",
                    "label": "Owner",
                    "local_value": local_owner,
                    "peer_value": peer_owner,
                    "local_label": self._participant_name(local_owner),
                    "peer_label": self._participant_name(peer_owner),
                    "summary": (
                        f"Owner: {self._participant_name(local_owner)}"
                        f" → {self._participant_name(peer_owner)}"
                    ),
                    "local_summary": (
                        f"Owner: {self._participant_name(peer_owner)}"
                        f" → {self._participant_name(local_owner)}"
                    ),
                })

        if local.weights != peer.weights:
            changes.append({
                "kind": "weights",
                "field": "weights",
                "label": "Weights",
                "local_value": local.weights,
                "peer_value": peer.weights,
                "summary": "Weights changed",
                "local_summary": "Keep your current weights",
            })
        return self._annotate_authorship(changes, node_label, authored_locally)

    @staticmethod
    def _annotate_authorship(changes: list[dict], node_label: str,
                             authored_locally: bool) -> list[dict]:
        """Name what the author did, in words neither side has to invert.

        The rest of a change record is deliberately peer-relative ("...in the
        peer version"), which is the right frame for choosing a version but
        the wrong one for saying what happened: it forces the person who made
        the change to read their own edit described from the far end. This
        one field states the act, so each side can render "<act> by me" or
        "<act> by <name>" from the same record.
        """
        for change in changes:
            kind = change.get("kind")
            detail = ""
            # Sits after the author rather than behind a colon: a move ends
            # in a phrase that belongs to the verb ("moved by me to Doing"),
            # while a modification ends in a list of what changed.
            suffix = ""
            if kind == "presence":
                act, noun = "created", "creation"
            elif kind == "deletion":
                peer_deleted = bool(change.get("peer_value"))
                deleted_by_author = peer_deleted != authored_locally
                act = "deleted" if deleted_by_author else "restored"
                noun = "deletion" if deleted_by_author else "restoration"
            elif kind == "move":
                act, noun = "moved", "move"
                # Name where the author put it, which is their own side's
                # column - the other end of the comparison is where it came
                # from, and saying that would describe the wrong end.
                target = change.get(
                    "local_label" if authored_locally else "peer_label",
                )
                if target:
                    suffix = f'to "{target}"'
                # Where the *other* side put it. Only a conflict needs this:
                # there both parties moved the card, and a sentence naming
                # one destination cannot say what the disagreement is.
                counterpart = change.get(
                    "peer_label" if authored_locally else "local_label",
                )
                if counterpart:
                    change["counter_suffix"] = f'to "{counterpart}"'
            elif kind == "position":
                act, noun = "reordered", "reordering"
            elif kind == "participants":
                # "added" / "removed" are peer-relative; from the author's
                # own end they swap when the author is this client.
                gained = change.get(
                    "removed_labels" if authored_locally else "added_labels",
                ) or []
                lost = change.get(
                    "added_labels" if authored_locally else "removed_labels",
                ) or []
                parts = []
                if gained:
                    parts.append(f"{', '.join(gained)} added")
                if lost:
                    parts.append(f"{', '.join(lost)} removed")
                act, noun = "modified", "modification"
                detail = "; ".join(parts)
            elif kind in ("field", "owner", "weights"):
                act, noun = "modified", "modification"
                detail = f"{str(change.get('label') or '').lower()} changed"
            else:
                act, noun = "changed", "change"
            # Kept apart so the author lands between the act and its detail
            # - "Card modified by me: Ana added", not "Card modified: Ana
            # added by me", which reads as though Ana added something. The
            # noun is the same act named for a button: "Take back my card
            # modification".
            change["node_label"] = node_label
            change["authored_act"] = act
            change["authored_noun"] = noun
            change["authored_suffix"] = suffix
            change["authored_detail"] = detail
        return changes

    @staticmethod
    def _node_display_name(node: ProtocolNode | None) -> str:
        if not node:
            return "Unknown"
        return str(node.data.get("name") or node.data.get("text") or "Untitled")

    @staticmethod
    def _display_value(value: Any) -> str:
        if value in (None, ""):
            return "None"
        return f'"{value}"'

    def _participant_name(self, participant: str | None) -> str:
        if not participant:
            return "Unassigned"
        for user in self.users():
            if participant in (
                user.get("id"), user.get("profile_uuid"),
                user.get("identity_key"), user.get("address"),
            ):
                name = user.get("name")
                if name and name != "?":
                    return name
        return str(participant)[:8]

    def columns(self, initiative: ProtocolNode | None = None) -> list[ProtocolNode]:
        initiative = initiative or self.ensure_initiative()
        return sorted(
            [child for child in initiative.live_children() if child.data.get("type") == "kanban_column"],
            key=lambda node: (float(node.data.get("order", 0)), node.created_at),
        )

    def cards(self, column: ProtocolNode) -> list[ProtocolNode]:
        return sorted(
            [child for child in column.live_children() if child.data.get("type") == "kanban_card"],
            key=lambda node: (float(node.data.get("order", 0)), node.created_at),
        )

    # Agendas are Session's - an agenda item is a child of the topic root, and
    # an initiative is one. These stay only to keep this application's
    # initiative-scoped calling convention; the rules live in one place.
    def agenda_items(self, initiative: ProtocolNode | None = None) -> list[ProtocolNode]:
        initiative = initiative or self.ensure_initiative()
        return self.session.agenda_projection(initiative.uuid)

    def create_agenda_item(
        self, text: str, priority: str | None = None,
        initiative_uuid: str | None = None,
    ) -> SessionResult:
        initiative = (
            self._node(initiative_uuid, "initiative")
            if initiative_uuid else self.ensure_initiative()
        )
        if not initiative:
            return SessionResult("error", reason="initiative not found")
        return self.session.create_agenda_item(initiative.uuid, text, priority)

    def delete_agenda_item(self, item_uuid: str) -> SessionResult:
        if not self.owns_node(item_uuid):
            return SessionResult("error", reason="agenda item not found")
        return self.session.delete_agenda_item(item_uuid)

    def update_agenda_item(self, item_uuid: str, text: str) -> SessionResult:
        if not self.owns_node(item_uuid):
            return SessionResult("error", reason="agenda item not found")
        return self.session.update_agenda_item_text(item_uuid, text)

    def set_agenda_item_priority(self, item_uuid: str, priority: str | None) -> SessionResult:
        if not self.owns_node(item_uuid):
            return SessionResult("error", reason="agenda item not found")
        return self.session.set_agenda_item_priority(item_uuid, priority)

    def move_agenda_item(self, item_uuid: str, index: int) -> SessionResult:
        if not self.owns_node(item_uuid):
            return SessionResult("error", reason="agenda item not found")
        return self.session.move_agenda_item(item_uuid, index)

    def _node(self, uuid: str, node_type: str) -> ProtocolNode | None:
        node = self.session.protocol.index.get(uuid)
        if (
            node
            and node.data.get("type") == node_type
            and self.owns_node(uuid)
        ):
            return node
        return None

    def owns_node(self, node_uuid: str, peer_addr: str | None = None) -> bool:
        """Whether one side's node belongs to an initiative topic and schema."""
        if peer_addr is not None:
            node = self.session.get_cached_peer_subtree(peer_addr, node_uuid)
            if not node or node.data.get("type") not in OWNED_NODE_TYPES:
                return False
            topic_uuids = set(
                self.session.peer_topics_for_node(peer_addr, node_uuid),
            )
            if local_topic := self._local_initiative_topic(node_uuid):
                topic_uuids.add(local_topic.uuid)
            return any(
                (topic := self.session.get_cached_peer_subtree(peer_addr, topic_uuid))
                and topic.data.get("type") == "initiative"
                and self._subtree_contains(topic, node_uuid)
                for topic_uuid in topic_uuids
            )

        node = self.session.protocol.index.get(node_uuid)
        if not node or node.data.get("type") not in OWNED_NODE_TYPES:
            return False
        return self._local_initiative_topic(node_uuid) is not None

    def _local_initiative_topic(self, node_uuid: str) -> ProtocolNode | None:
        node = self.session.protocol.index.get(node_uuid)
        if not node:
            return None
        seen = set()
        current = node
        while current and current.uuid not in seen:
            seen.add(current.uuid)
            if current.data.get("type") == "initiative":
                parent = self.session.protocol.index.get(current.parent_uuid)
                return current if self._is_initiative_app_topic(parent) else None
            current = self.session.protocol.index.get(current.parent_uuid)
        return None

    @staticmethod
    def _subtree_contains(root: ProtocolNode, node_uuid: str) -> bool:
        return root.uuid == node_uuid or any(
            InitiativeLogic._subtree_contains(child, node_uuid)
            for child in root.children
        )

    def _remember_initiative(self, initiative_uuid: str, explicit: bool = False) -> None:
        # Both keys are one selection decision, so they are written under a
        # single Session transaction rather than as two separate updates.
        with self.session.lock:
            metadata = self.session.application_metadata(INITIATIVE_APPLICATION_ID)
            metadata["selected_initiative_uuid"] = initiative_uuid
            if explicit:
                metadata["initiative_selection_explicit"] = True

    def _metadata(self) -> dict:
        """Return a detached read copy of this application's metadata.

        Session hands out the live namespace only to a caller holding its
        lock. This application's requests are not wrapped in a Session
        transaction, so readers take a snapshot and writers open their own
        transaction - see _remember_initiative.
        """
        with self.session.lock:
            return copy.deepcopy(
                self.session.application_metadata(INITIATIVE_APPLICATION_ID),
            )

    def _initiative_container(self) -> ProtocolNode:
        return self._folder(self._apps_folder(), INITIATIVE_APP_NAME, "initiative_app")

    def _initiative_containers(self) -> list[ProtocolNode]:
        active = [
            topic
            for topic in self.session.active_topics()
            if self._is_initiative_app_topic(topic)
        ]
        if active:
            return active
        apps = next(
            (
                child for child in self.session.protocol.root.live_children()
                if child.data.get("type") == "folder"
                and child.data.get("name") == "apps"
            ),
            None,
        )
        if not apps:
            return []
        return [
            child for child in apps.live_children()
            if self._is_initiative_app_topic(child)
        ]

    def _apps_folder(self) -> ProtocolNode:
        return self._folder(self.session.protocol.root, "apps")

    def _user_info(self, fallback_addr: str, profile: ProtocolNode | None,
                   profile_uuid: str | None = None) -> dict:
        data = profile.data if profile else {}
        address = fallback_addr
        user_id = profile_uuid or (profile.uuid if profile else None)
        display_name = data.get("display_name") or ""
        if display_name == address or display_name.startswith(("http://", "https://")):
            display_name = ""
        avatar = avatar_attachment(data)
        return {
            "id": user_id or "",
            "profile_uuid": user_id or "",
            "identity_key": data.get("identity_key") or "",
            "address": address,
            "name": display_name or "?",
            "picture": (
                f"/api/blob/{avatar['blob_id']}" if avatar
                else data.get("picture") or ""
            ),
            "picture_blob_id": avatar["blob_id"] if avatar else "",
        }

    def _find_peer_user_profile(self, address: str) -> ProtocolNode | None:
        return self.session.peer_identity(address)

    def _peer_profile_uuid(self, address: str, profile: ProtocolNode | None = None) -> str:
        if profile:
            return profile.uuid
        for topic_uuid in self.session.peer_topic_uuids(address):
            cached = self.session.get_cached_peer_subtree(address, topic_uuid)
            if cached and self._is_shared_user_topic(cached):
                return cached.uuid
        # No "assume it's the profile if it's not an initiative we recognize"
        # fallback here on purpose: that used to be safe when a peer's only
        # ever-fetched topics were exactly one initiative plus one profile, but a
        # mailbox channel may track every topic a peer publishes - an initiative
        # this side never grafted locally has no entry in
        # self.session.protocol.index either, so "not an initiative" and "is the
        # profile" stopped meaning the same thing. Returning "" (unknown
        # for now) is correct; guessing wrong hands a peer's own initiative back
        # as if it were their identity.
        return ""

    def _folder(self, parent: ProtocolNode, name: str,
                node_type: str = "folder") -> ProtocolNode:
        for child in parent.children:
            if child.data.get("name") == name and child.data.get("type") in ("folder", node_type):
                return child
        created = self.session.create_child(
            parent.uuid,
            {"type": node_type, "name": name},
            {},
        ).value
        return created

    def _initiatives_under(self, root: ProtocolNode) -> list[ProtocolNode]:
        out = []
        if root.data.get("type") == "initiative":
            out.append(root)
        for child in root.children:
            out.extend(self._initiatives_under(child))
        return out

    def _is_initiative_app_topic(self, node: ProtocolNode | None) -> bool:
        if not node:
            return False
        return (
            node.data.get("type") == "initiative_app"
            and node.data.get("name") == INITIATIVE_APP_NAME
        )

    def _is_shared_user_topic(self, node: ProtocolNode | None) -> bool:
        return self.session.is_identity_node(node)

    def _is_active_discussion_node(self, node_uuid: str) -> bool:
        return self.session.is_node_in_active_topic(node_uuid)

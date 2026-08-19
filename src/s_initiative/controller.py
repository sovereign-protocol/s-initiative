"""Starlette controller for S-Initiative."""

from __future__ import annotations

from sovereign import application_json_response
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


def build_routes(logic, runtime) -> list[Route]:
    async def api_board(request: Request):
        return runtime.composite_response(
            logic.board_snapshot,
            lambda snapshot: runtime.collaboration.network_info(
                snapshot.get("topic_uuid"),
            ),
            logic.merge_board_observation,
        )

    async def api_auto_adopt(request: Request):
        data = await request.json()
        return await _json_result(
            runtime, logic.set_auto_adopt_mode(
                data.get("mode", "always"), data.get("initiative_uuid"),
            ),
        )

    async def api_create_initiative(request: Request):
        data = await request.json()
        return await _json_result(
            runtime, logic.create_initiative(data.get("name", "Initiative")),
        )

    async def api_select_initiative(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.select_initiative(data["initiative_uuid"]))

    async def api_rename_initiative(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.rename_initiative(
            data["initiative_uuid"], data.get("name", "Initiative"),
        ))

    async def api_set_initiative_objective(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.set_initiative_objective(
            data["initiative_uuid"], data.get("objective", ""),
        ))

    async def api_set_initiative_dates(request: Request):
        data = await request.json()
        # Absent means "leave it alone" and empty means "clear it", so the
        # two ends can be planned one at a time.
        return await _json_result(runtime, logic.set_initiative_dates(
            data["initiative_uuid"],
            data.get("planned_start"), data.get("planned_end"),
        ))

    async def api_claim_initiative_date(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.claim_initiative_date(
            data["initiative_uuid"], data.get("field", ""),
            data.get("value", ""),
        ))

    async def api_copy_initiative(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.copy_initiative(data["initiative_uuid"]))

    async def api_delete_initiative(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.delete_initiative(data["initiative_uuid"]))

    async def api_create_need(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.create_need(
            data.get("text", ""), data.get("beneficiary_label", ""),
            data.get("beneficiary_actor_uuid", ""),
        ))

    async def api_update_need(request: Request):
        data = await request.json()
        # Absent means "leave it alone" and empty means "clear it", so one
        # field can be rewritten without restating the others.
        return await _json_result(runtime, logic.update_need(
            data["need_uuid"], data.get("text"),
            data.get("beneficiary_label"), data.get("beneficiary_actor_uuid"),
        ))

    async def api_delete_need(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.delete_need(data["need_uuid"]))

    async def api_move_need(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.move_need(
            data["need_uuid"], int(data.get("index", 0)),
        ))

    async def api_create_section(request: Request):
        data = await request.json()
        return await _json_result(
            runtime, logic.create_section(data.get("title", "")),
        )

    async def api_rename_section(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.rename_section(
            data["section_uuid"], data.get("title", ""),
        ))

    async def api_delete_section(request: Request):
        data = await request.json()
        return await _json_result(
            runtime, logic.delete_section(data["section_uuid"]),
        )

    async def api_move_section(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.move_section(
            data["section_uuid"], int(data.get("index", 0)),
        ))

    async def api_create_clause(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.create_clause(
            data["parent_uuid"], data.get("text", ""),
        ))

    async def api_update_clause(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.update_clause(
            data["clause_uuid"], data.get("text", ""),
        ))

    async def api_delete_clause(request: Request):
        data = await request.json()
        return await _json_result(
            runtime, logic.delete_clause(data["clause_uuid"]),
        )

    async def api_move_clause(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.move_clause(
            data["clause_uuid"], int(data.get("index", 0)),
        ))

    async def api_create_reality(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.create_reality(
            data["parent_uuid"], data.get("text", ""),
        ))

    async def api_delete_reality(request: Request):
        data = await request.json()
        return await _json_result(
            runtime, logic.delete_reality(data["reality_uuid"]),
        )

    async def api_create_investment(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.create_investment(
            data.get("actor_uuid", ""), data.get("availability", ""),
        ))

    async def api_delete_investment(request: Request):
        data = await request.json()
        return await _json_result(
            runtime, logic.delete_investment(data["investment_uuid"]),
        )

    async def api_create_milestone(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.create_milestone(
            data.get("title", ""), data.get("planned_at", ""),
            data.get("intention", ""),
        ))

    async def api_update_milestone(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.update_milestone(
            data["milestone_uuid"], data.get("title"), data.get("planned_at"),
            data.get("intention"),
        ))

    async def api_delete_milestone(request: Request):
        data = await request.json()
        return await _json_result(
            runtime, logic.delete_milestone(data["milestone_uuid"]),
        )

    async def api_move_milestone(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.move_milestone(
            data["milestone_uuid"], int(data.get("index", 0)),
        ))

    async def api_reach_milestone(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.claim_milestone_reached(
            data["milestone_uuid"], data.get("value", ""),
        ))

    async def api_create_column(request: Request):
        data = await request.json()
        return await _json_result(
            runtime, logic.create_column(data.get("name", "Column")),
        )

    async def api_rename_column(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.rename_column(
            data["column_uuid"], data.get("name", "Column"),
        ))

    async def api_delete_column(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.delete_column(data["column_uuid"]))

    async def api_move_column(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.move_column(
            data["column_uuid"], int(data.get("index", 0)),
        ))

    async def api_create_card(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.create_card(
            data["column_uuid"],
            data.get("name", "Card"),
            data.get("description", ""),
            _participants(data.get("participants")),
            data.get("owner"),
        ))

    async def api_update_card(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.update_card(
            data["card_uuid"],
            data.get("name", "Card"),
            data.get("description", ""),
            _participants(data.get("participants")),
            data.get("owner"),
            expected_content_hash=data.get("expected_content_hash"),
        ))

    async def api_delete_card(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.delete_card(data["card_uuid"]))

    async def api_move_card(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.move_card(
            data["card_uuid"], data["column_uuid"], int(data.get("index", 0)),
        ))

    async def api_create_card_comment(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.create_card_comment(
            data["card_uuid"], data.get("text", ""),
        ))

    async def api_delete_card_comment(request: Request):
        data = await request.json()
        return await _json_result(
            runtime, logic.delete_card_comment(data["comment_uuid"]),
        )

    async def api_create_card_attachment(request: Request):
        # The bytes already went to Core's own /api/blob endpoint, which owns
        # the size limit and content addressing. Only the reference reaches
        # the application.
        data = await request.json()
        return await _json_result(runtime, logic.create_card_attachment(
            data["card_uuid"], data.get("attachment") or {},
        ))

    async def api_delete_card_attachment(request: Request):
        data = await request.json()
        return await _json_result(
            runtime, logic.delete_card_attachment(data["attachment_uuid"]),
        )

    async def api_adopt(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.accept_peer_node(
            data["source_addr"],
            data["node_uuid"],
            bool(data.get("adopt_absence")),
        ))

    async def api_rollback(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.rollback_peer_node(
            data["source_addr"],
            data["node_uuid"],
            bool(data.get("rollback_absence")),
        ))

    async def api_create_agenda_item(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.create_agenda_item(
            data.get("text", ""), data.get("priority"), data.get("initiative_uuid"),
        ))

    async def api_delete_agenda_item(request: Request):
        data = await request.json()
        return await _json_result(
            runtime, logic.delete_agenda_item(data["item_uuid"]),
        )

    async def api_update_agenda_item(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.update_agenda_item(
            data["item_uuid"], data.get("text", ""),
        ))

    async def api_set_agenda_item_priority(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.set_agenda_item_priority(
            data["item_uuid"], data.get("priority"),
        ))

    async def api_create_link(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.link_topic(
            data["initiative_uuid"],
            data["topic_uuid"],
            data.get("application_id", ""),
            data.get("title", ""),
        ))

    async def api_make_link(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.create_linked_topic(
            data["initiative_uuid"],
            data.get("application_id", ""),
            data.get("title", ""),
            data.get("template", ""),
            data.get("snapshot"),
        ))

    async def api_remove_link(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.unlink_topic(data["link_uuid"]))

    async def api_follow_link(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.follow_link(data["link_uuid"]))

    async def api_move_agenda_item(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.move_agenda_item(
            data["item_uuid"], int(data.get("index", 0)),
        ))

    return [
        Route("/api/initiative/board", api_board),
        Route("/api/initiative/auto_adopt", api_auto_adopt, methods=["POST"]),
        Route("/api/initiative/initiatives/create", api_create_initiative,
              methods=["POST"]),
        Route("/api/initiative/initiatives/select", api_select_initiative,
              methods=["POST"]),
        Route("/api/initiative/initiatives/rename", api_rename_initiative,
              methods=["POST"]),
        Route("/api/initiative/initiatives/set_objective",
              api_set_initiative_objective, methods=["POST"]),
        Route("/api/initiative/initiatives/set_dates",
              api_set_initiative_dates, methods=["POST"]),
        Route("/api/initiative/initiatives/claim_date",
              api_claim_initiative_date, methods=["POST"]),
        Route("/api/initiative/initiatives/copy", api_copy_initiative,
              methods=["POST"]),
        Route("/api/initiative/initiatives/delete", api_delete_initiative,
              methods=["POST"]),
        Route("/api/initiative/needs/create", api_create_need, methods=["POST"]),
        Route("/api/initiative/needs/update", api_update_need, methods=["POST"]),
        Route("/api/initiative/needs/delete", api_delete_need, methods=["POST"]),
        Route("/api/initiative/needs/move", api_move_need, methods=["POST"]),
        Route("/api/initiative/sections/create", api_create_section, methods=["POST"]),
        Route("/api/initiative/sections/rename", api_rename_section, methods=["POST"]),
        Route("/api/initiative/sections/delete", api_delete_section, methods=["POST"]),
        Route("/api/initiative/sections/move", api_move_section, methods=["POST"]),
        Route("/api/initiative/clauses/create", api_create_clause, methods=["POST"]),
        Route("/api/initiative/clauses/update", api_update_clause, methods=["POST"]),
        Route("/api/initiative/clauses/delete", api_delete_clause, methods=["POST"]),
        Route("/api/initiative/clauses/move", api_move_clause, methods=["POST"]),
        Route("/api/initiative/realities/create", api_create_reality, methods=["POST"]),
        Route("/api/initiative/realities/delete", api_delete_reality, methods=["POST"]),
        Route("/api/initiative/investments/create", api_create_investment, methods=["POST"]),
        Route("/api/initiative/investments/delete", api_delete_investment, methods=["POST"]),
        Route("/api/initiative/milestones/create", api_create_milestone, methods=["POST"]),
        Route("/api/initiative/milestones/update", api_update_milestone, methods=["POST"]),
        Route("/api/initiative/milestones/delete", api_delete_milestone, methods=["POST"]),
        Route("/api/initiative/milestones/move", api_move_milestone, methods=["POST"]),
        Route("/api/initiative/milestones/reach", api_reach_milestone, methods=["POST"]),
        Route("/api/initiative/columns/create", api_create_column, methods=["POST"]),
        Route("/api/initiative/columns/rename", api_rename_column, methods=["POST"]),
        Route("/api/initiative/columns/delete", api_delete_column, methods=["POST"]),
        Route("/api/initiative/columns/move", api_move_column, methods=["POST"]),
        Route("/api/initiative/cards/create", api_create_card, methods=["POST"]),
        Route("/api/initiative/cards/update", api_update_card, methods=["POST"]),
        Route("/api/initiative/cards/delete", api_delete_card, methods=["POST"]),
        Route("/api/initiative/cards/move", api_move_card, methods=["POST"]),
        Route("/api/initiative/cards/comments/create", api_create_card_comment, methods=["POST"]),
        Route("/api/initiative/cards/comments/delete", api_delete_card_comment, methods=["POST"]),
        Route("/api/initiative/cards/attachments/create", api_create_card_attachment, methods=["POST"]),
        Route("/api/initiative/cards/attachments/delete", api_delete_card_attachment, methods=["POST"]),
        Route("/api/initiative/adopt", api_adopt, methods=["POST"]),
        Route("/api/initiative/rollback", api_rollback, methods=["POST"]),
        Route("/api/initiative/agenda/create", api_create_agenda_item, methods=["POST"]),
        Route("/api/initiative/agenda/delete", api_delete_agenda_item, methods=["POST"]),
        Route("/api/initiative/agenda/update", api_update_agenda_item, methods=["POST"]),
        Route("/api/initiative/agenda/set_priority", api_set_agenda_item_priority, methods=["POST"]),
        Route("/api/initiative/agenda/move", api_move_agenda_item, methods=["POST"]),
        Route("/api/initiative/links/create", api_create_link, methods=["POST"]),
        Route("/api/initiative/links/make", api_make_link, methods=["POST"]),
        Route("/api/initiative/links/remove", api_remove_link, methods=["POST"]),
        Route("/api/initiative/links/follow", api_follow_link, methods=["POST"]),
    ]


async def _json_result(runtime, result) -> JSONResponse:
    return await application_json_response(runtime, result)


def _participants(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]

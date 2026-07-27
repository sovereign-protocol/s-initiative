"""Starlette controller for S-Kanban."""

from __future__ import annotations

import asyncio

from sovereign import application_result_view, json_value
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


def build_routes(logic, runtime, config: dict) -> list[Route]:
    async def api_board(request: Request):
        result = await asyncio.to_thread(logic.on_peer_update)
        if result.value:
            await asyncio.to_thread(
                runtime.deliver_effects, result.effects,
            )
            runtime.notify_change()
        return JSONResponse(json_value(logic.board_payload(auto_adopt=False)))

    async def api_auto_adopt(request: Request):
        data = await request.json()
        return await _json_result(
            runtime, logic.set_auto_adopt_mode(
                data.get("mode", "always"), data.get("board_uuid"),
            ),
        )

    async def api_create_board(request: Request):
        data = await request.json()
        return await _json_result(
            runtime, logic.create_board(data.get("name", "Kanban Board")),
        )

    async def api_select_board(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.select_board(data["board_uuid"]))

    async def api_rename_board(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.rename_board(
            data["board_uuid"], data.get("name", "Kanban Board"),
        ))

    async def api_set_board_objective(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.set_board_objective(
            data["board_uuid"], data.get("objective", ""),
        ))

    async def api_copy_board(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.copy_board(data["board_uuid"]))

    async def api_delete_board(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.delete_board(data["board_uuid"]))

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
            data.get("text", ""), data.get("priority"), data.get("board_uuid"),
        ))

    async def api_delete_agenda_item(request: Request):
        data = await request.json()
        return await _json_result(
            runtime, logic.delete_agenda_item(data["item_uuid"]),
        )

    async def api_set_agenda_item_priority(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.set_agenda_item_priority(
            data["item_uuid"], data.get("priority"),
        ))

    async def api_move_agenda_item(request: Request):
        data = await request.json()
        return await _json_result(runtime, logic.move_agenda_item(
            data["item_uuid"], int(data.get("index", 0)),
        ))

    return [
        Route("/api/kanban/board", api_board),
        Route("/api/kanban/auto_adopt", api_auto_adopt, methods=["POST"]),
        Route("/api/kanban/boards/create", api_create_board, methods=["POST"]),
        Route("/api/kanban/boards/select", api_select_board, methods=["POST"]),
        Route("/api/kanban/boards/rename", api_rename_board, methods=["POST"]),
        Route("/api/kanban/boards/set_objective", api_set_board_objective, methods=["POST"]),
        Route("/api/kanban/boards/copy", api_copy_board, methods=["POST"]),
        Route("/api/kanban/boards/delete", api_delete_board, methods=["POST"]),
        Route("/api/kanban/columns/create", api_create_column, methods=["POST"]),
        Route("/api/kanban/columns/rename", api_rename_column, methods=["POST"]),
        Route("/api/kanban/columns/delete", api_delete_column, methods=["POST"]),
        Route("/api/kanban/columns/move", api_move_column, methods=["POST"]),
        Route("/api/kanban/cards/create", api_create_card, methods=["POST"]),
        Route("/api/kanban/cards/update", api_update_card, methods=["POST"]),
        Route("/api/kanban/cards/delete", api_delete_card, methods=["POST"]),
        Route("/api/kanban/cards/move", api_move_card, methods=["POST"]),
        Route("/api/kanban/cards/comments/create", api_create_card_comment, methods=["POST"]),
        Route("/api/kanban/cards/comments/delete", api_delete_card_comment, methods=["POST"]),
        Route("/api/kanban/cards/attachments/create", api_create_card_attachment, methods=["POST"]),
        Route("/api/kanban/cards/attachments/delete", api_delete_card_attachment, methods=["POST"]),
        Route("/api/kanban/adopt", api_adopt, methods=["POST"]),
        Route("/api/kanban/rollback", api_rollback, methods=["POST"]),
        Route("/api/kanban/agenda/create", api_create_agenda_item, methods=["POST"]),
        Route("/api/kanban/agenda/delete", api_delete_agenda_item, methods=["POST"]),
        Route("/api/kanban/agenda/set_priority", api_set_agenda_item_priority, methods=["POST"]),
        Route("/api/kanban/agenda/move", api_move_agenda_item, methods=["POST"]),
    ]


async def _json_result(runtime, result) -> JSONResponse:
    deliveries = []
    if result.status == "ok":
        deliveries = await asyncio.to_thread(
            runtime.deliver_effects, result.effects,
        )
        runtime.notify_change()
    view = application_result_view(result, deliveries)
    return JSONResponse(view.payload, status_code=200 if view.ok else 409)


def _participants(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]

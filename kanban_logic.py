from datetime import datetime, timezone
import asyncio

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from logic import DataLogic
from transport import PRSPNode, TransportLayer


def log_error(message: str, error: Exception | None = None) -> None:
    if error:
        print(f"[kanban] {message}: {error}", flush=True)
    else:
        print(f"[kanban] {message}", flush=True)


class KanbanLogic(DataLogic):
    def __init__(self, transport: TransportLayer):
        super().__init__(transport)
        self.local_change_pending = False

    def note_local_change(self) -> None:
        self.local_change_pending = True

    def _remember_board(self, board_uuid: str) -> None:
        if self.transport.prsp.data.get("kanban_board_uuid") == board_uuid:
            return
        self.transport.prsp.data["kanban_board_uuid"] = board_uuid
        self.transport._cascade_hash(self.transport.prsp.uuid)

    def ensure_board(self) -> PRSPNode:
        with self.transport.lock:
            active_topic_uuid = next(iter(self.transport.peer_topics.values()), None)
            if active_topic_uuid:
                existing = self.transport._index.get(active_topic_uuid)
                if existing:
                    self._remember_board(existing.uuid)
                    return existing
            remembered_uuid = self.transport.prsp.data.get("kanban_board_uuid")
            remembered = self.transport._index.get(remembered_uuid) if remembered_uuid else None
            if remembered and remembered.data.get("type") == "kanban_board":
                return remembered
            board = self._find_by_type(self.transport.prsp, "kanban_board")
        if board:
            with self.transport.lock:
                self._remember_board(board.uuid)
            return board

        board = self.transport.create_child(
            self.transport.prsp.uuid,
            {"type": "kanban_board", "name": "Kanban Board"},
            {},
        )
        for order, name in enumerate(["To Do", "Doing", "Done"]):
            self.transport.create_child(
                board.uuid,
                {"type": "kanban_column", "name": name, "order": order},
                {},
            )
        with self.transport.lock:
            self._remember_board(board.uuid)
        return board

    def accept_topic_invitation(self, tree: PRSPNode) -> str:
        topic_uuid = super().accept_topic_invitation(tree)
        with self.transport.lock:
            self._remember_board(topic_uuid)
        return topic_uuid

    def board_payload(self) -> dict:
        self.adopt_incoming_changes()
        board = self.ensure_board()
        return {
            "address": self.transport.address,
            "board": board.to_dict(),
            "network": self.transport.get_network_info(),
        }

    def adopt_incoming_changes(self) -> bool:
        changed = False
        with self.transport.lock:
            topic_uuid = next(iter(self.transport.peer_topics.values()), None)
            if not topic_uuid:
                return False
            local_board = self.transport._index.get(topic_uuid)
            if not local_board:
                return False
            if self.local_change_pending:
                if self._peers_aligned_with_local(topic_uuid, local_board.state_hash):
                    self.local_change_pending = False
            for addr in sorted(self.transport.peer_perspectives):
                if self.transport.peer_topics.get(addr) != topic_uuid:
                    continue
                peer_board = self.transport.peer_perspectives.get(addr)
                if not peer_board or peer_board.uuid != topic_uuid:
                    continue
                local_board = self.transport._index.get(topic_uuid)
                if local_board and local_board.state_hash == peer_board.state_hash:
                    continue
                imported = PRSPNode.from_dict(peer_board.to_dict())
                if not self._merge_peer_board(imported):
                    continue
                changed = True
        if changed:
            self.transport._trigger_sync(topic_uuid)
        return changed

    def _peers_aligned_with_local(self, topic_uuid: str, local_state_hash: str) -> bool:
        peers = [
            self.transport.peer_perspectives.get(addr)
            for addr, peer_topic_uuid in self.transport.peer_topics.items()
            if peer_topic_uuid == topic_uuid
        ]
        peer_boards = [peer for peer in peers if peer and peer.uuid == topic_uuid]
        if not peer_boards:
            return False
        return all(peer.state_hash == local_state_hash for peer in peer_boards)

    def _replace_local_subtree(self, tree: PRSPNode) -> bool:
        existing = self.transport._index.get(tree.uuid)
        if not existing:
            return False
        parent_uuid = existing.parent_uuid
        if not parent_uuid:
            return False
        parent = self.transport._index.get(parent_uuid)
        if not parent:
            return False
        self.transport._deindex_subtree(existing)
        tree.parent_uuid = parent_uuid
        parent.children = [
            tree if child.uuid == tree.uuid else child
            for child in parent.children
        ]
        self.transport._index_subtree(tree)
        self._refresh_subtree_hashes(tree)
        self.transport._cascade_hash(parent.uuid)
        return True

    def _merge_peer_board(self, peer_board: PRSPNode) -> bool:
        local_board = self.transport._index.get(peer_board.uuid)
        if not local_board:
            return False
        changed = self._merge_node(local_board, peer_board)
        if changed:
            self._refresh_subtree_hashes(local_board)
            self.transport._cascade_hash(local_board.uuid)
        return changed

    def _merge_node(self, local_node: PRSPNode, peer_node: PRSPNode) -> bool:
        changed = False
        if self._peer_is_newer(local_node, peer_node):
            local_node.data = dict(peer_node.data)
            local_node.weights = dict(peer_node.weights)
            local_node.updated_at = peer_node.updated_at
            changed = True

        local_children = {child.uuid: child for child in local_node.children}
        for peer_child in peer_node.children:
            local_child = local_children.get(peer_child.uuid)
            if local_child:
                changed = self._merge_node(local_child, peer_child) or changed
                continue

            imported = PRSPNode.from_dict(peer_child.to_dict())
            self._remove_existing_uuid_from_local_tree(imported.uuid)
            imported.parent_uuid = local_node.uuid
            local_node.children.append(imported)
            self.transport._index_subtree(imported)
            changed = True
        return changed

    def _remove_existing_uuid_from_local_tree(self, node_uuid: str) -> None:
        existing = self.transport._index.get(node_uuid)
        if not existing or not existing.parent_uuid:
            return
        parent = self.transport._index.get(existing.parent_uuid)
        if parent:
            parent.children = [child for child in parent.children if child.uuid != node_uuid]
        self.transport._deindex_subtree(existing)

    @staticmethod
    def _peer_is_newer(local_node: PRSPNode, peer_node: PRSPNode) -> bool:
        if local_node.content_hash == peer_node.content_hash:
            return False
        return KanbanLogic._timestamp(peer_node.updated_at) > KanbanLogic._timestamp(local_node.updated_at)

    @staticmethod
    def _timestamp(value: str) -> float:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except Exception:
            return 0.0

    def invite_to_discuss(self, peer_addr: str) -> dict:
        board = self.ensure_board()
        return self.transport.invite_to_discuss(peer_addr, board.uuid)

    def create_column(self, name: str) -> PRSPNode | None:
        board = self.ensure_board()
        order = self._next_order(board.children)
        return self.transport.create_child(
            board.uuid,
            {"type": "kanban_column", "name": name or "New column", "order": order},
            {},
        )

    def delete_column(self, column_uuid: str) -> bool:
        return self.transport.delete(column_uuid)

    def update_column(self, column_uuid: str, name: str) -> bool:
        column = self.transport._index.get(column_uuid)
        if not column or column.data.get("type") != "kanban_column":
            return False
        data = dict(column.data)
        data["name"] = name or "Column"
        return self.transport.modify(column_uuid, data, column.weights)

    def move_column(self, column_uuid: str, index: int) -> bool:
        board = self.ensure_board()
        columns = self._sorted_children(board, "kanban_column")
        column = self.transport._index.get(column_uuid)
        if not column or column.parent_uuid != board.uuid:
            return False
        columns = [c for c in columns if c.uuid != column_uuid]
        index = max(0, min(index, len(columns)))
        columns.insert(index, column)
        return self._write_order(columns)

    def create_card(self, column_uuid: str, card: dict) -> PRSPNode | None:
        column = self.transport._index.get(column_uuid)
        if not column or column.data.get("type") != "kanban_column":
            return None
        order = self._next_order(column.children)
        return self.transport.create_child(
            column_uuid,
            {
                "type": "kanban_card",
                "name": card.get("name") or "New card",
                "description": card.get("description") or "",
                "owners": self._owners(card.get("owners")),
                "order": order,
            },
            {},
        )

    def update_card(self, card_uuid: str, card: dict) -> bool:
        node = self.transport._index.get(card_uuid)
        if not node or node.data.get("type") != "kanban_card":
            return False
        data = dict(node.data)
        data.update({
            "type": "kanban_card",
            "name": card.get("name") or "Untitled card",
            "description": card.get("description") or "",
            "owners": self._owners(card.get("owners")),
        })
        return self.transport.modify(card_uuid, data, node.weights)

    def delete_card(self, card_uuid: str) -> bool:
        return self.transport.delete(card_uuid)

    def move_card(self, card_uuid: str, column_uuid: str, index: int) -> bool:
        card = self.transport._index.get(card_uuid)
        target = self.transport._index.get(column_uuid)
        if not card or card.data.get("type") != "kanban_card":
            return False
        if not target or target.data.get("type") != "kanban_column":
            return False
        old_parent_uuid = card.parent_uuid
        if old_parent_uuid != column_uuid and not self.transport.move(card_uuid, column_uuid):
            return False
        changed_columns = []
        old_parent = self.transport._index.get(old_parent_uuid)
        if old_parent:
            changed_columns.append(old_parent)
        target = self.transport._index.get(column_uuid)
        if target and target not in changed_columns:
            changed_columns.append(target)
        ok = True
        for column in changed_columns:
            cards = self._sorted_children(column, "kanban_card")
            if column.uuid == column_uuid:
                card = self.transport._index.get(card_uuid)
                cards = [c for c in cards if c.uuid != card_uuid]
                index = max(0, min(index, len(cards)))
                cards.insert(index, card)
            ok = self._write_order(cards) and ok
        return ok

    def _find_by_type(self, node: PRSPNode, node_type: str) -> PRSPNode | None:
        if node.data.get("type") == node_type:
            return node
        for child in node.children:
            found = self._find_by_type(child, node_type)
            if found:
                return found
        return None

    def _refresh_subtree_hashes(self, node: PRSPNode) -> None:
        for child in node.children:
            self._refresh_subtree_hashes(child)
        node._refresh_hashes()

    def _sorted_children(self, node: PRSPNode, node_type: str) -> list[PRSPNode]:
        children = [child for child in node.children if child.data.get("type") == node_type]
        return sorted(children, key=lambda child: (child.data.get("order", 0), child.created_at))

    def _next_order(self, children: list[PRSPNode]) -> int:
        if not children:
            return 0
        return max(int(child.data.get("order", 0)) for child in children) + 1

    def _write_order(self, nodes: list[PRSPNode]) -> bool:
        ok = True
        for order, node in enumerate(nodes):
            data = dict(node.data)
            data["order"] = order
            ok = self.transport.modify(node.uuid, data, node.weights) and ok
        return ok

    @staticmethod
    def _owners(value) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return []


def create_logic(transport: TransportLayer, config: dict) -> KanbanLogic:
    return KanbanLogic(transport)


def build_routes(logic: KanbanLogic, transport: TransportLayer, config: dict) -> list[Route]:
    def notify():
        callback = config.get("notify_clients")
        if callback:
            callback()

    async def board(request: Request):
        transport.reconcile_integrations()
        return JSONResponse(logic.board_payload())

    async def invite(request: Request):
        try:
            data = await request.json()
            peer_addr = data["address"].strip().rstrip("/")
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                transport.pool, logic.invite_to_discuss, peer_addr
            )
            if result.get("status") != "ok":
                log_error(f"invite to {peer_addr} failed: {result.get('reason', result)}")
                if _peer_connected_on_active_topic(transport, peer_addr):
                    result = {"status": "ok", "reason": "peer connected after delayed invite"}
                else:
                    for _ in range(12):
                        await asyncio.sleep(0.25)
                        if _peer_connected_on_active_topic(transport, peer_addr):
                            result = {"status": "ok", "reason": "peer connected after delayed invite"}
                            break
            if result.get("status") == "ok":
                notify()
                return JSONResponse(result)
            return JSONResponse(result, status_code=409)
        except Exception as e:
            log_error("invite route crashed", e)
            return JSONResponse({"status": "error", "reason": str(e)}, status_code=500)

    async def create_column(request: Request):
        try:
            data = await request.json()
            node = logic.create_column(data.get("name", "New column"))
            if node:
                logic.note_local_change()
                notify()
            return JSONResponse({"status": "ok", "uuid": node.uuid} if node else {"status": "error"})
        except Exception as e:
            log_error("create column crashed", e)
            return JSONResponse({"status": "error", "reason": str(e)}, status_code=500)

    async def update_column(request: Request):
        data = await request.json()
        ok = logic.update_column(data["column_uuid"], data.get("name", "Column"))
        if ok:
            logic.note_local_change()
            notify()
        return JSONResponse({"status": "ok" if ok else "error"}, status_code=200 if ok else 409)

    async def delete_column(request: Request):
        data = await request.json()
        ok = logic.delete_column(data["column_uuid"])
        if ok:
            logic.note_local_change()
            notify()
        return JSONResponse({"status": "ok" if ok else "error"}, status_code=200 if ok else 409)

    async def move_column(request: Request):
        data = await request.json()
        ok = logic.move_column(data["column_uuid"], int(data.get("index", 0)))
        if ok:
            logic.note_local_change()
            notify()
        return JSONResponse({"status": "ok" if ok else "error"}, status_code=200 if ok else 409)

    async def create_card(request: Request):
        try:
            data = await request.json()
            node = logic.create_card(data["column_uuid"], data.get("card", {}))
            if node:
                logic.note_local_change()
                notify()
            return JSONResponse({"status": "ok", "uuid": node.uuid} if node else {"status": "error"})
        except Exception as e:
            log_error("create card crashed", e)
            return JSONResponse({"status": "error", "reason": str(e)}, status_code=500)

    async def update_card(request: Request):
        data = await request.json()
        ok = logic.update_card(data["card_uuid"], data.get("card", {}))
        if ok:
            logic.note_local_change()
            notify()
        return JSONResponse({"status": "ok" if ok else "error"}, status_code=200 if ok else 409)

    async def delete_card(request: Request):
        data = await request.json()
        ok = logic.delete_card(data["card_uuid"])
        if ok:
            logic.note_local_change()
            notify()
        return JSONResponse({"status": "ok" if ok else "error"}, status_code=200 if ok else 409)

    async def move_card(request: Request):
        data = await request.json()
        ok = logic.move_card(data["card_uuid"], data["column_uuid"], int(data.get("index", 0)))
        if ok:
            logic.note_local_change()
            notify()
        return JSONResponse({"status": "ok" if ok else "error"}, status_code=200 if ok else 409)

    return [
        Route("/api/kanban/board", board),
        Route("/api/kanban/invite", invite, methods=["POST"]),
        Route("/api/kanban/columns", create_column, methods=["POST"]),
        Route("/api/kanban/columns/update", update_column, methods=["POST"]),
        Route("/api/kanban/columns/delete", delete_column, methods=["POST"]),
        Route("/api/kanban/columns/move", move_column, methods=["POST"]),
        Route("/api/kanban/cards", create_card, methods=["POST"]),
        Route("/api/kanban/cards/update", update_card, methods=["POST"]),
        Route("/api/kanban/cards/delete", delete_card, methods=["POST"]),
        Route("/api/kanban/cards/move", move_card, methods=["POST"]),
    ]


def _peer_connected_on_active_topic(transport: TransportLayer, peer_addr: str) -> bool:
    with transport.lock:
        topic_uuid = next(iter(transport.peer_topics.values()), None)
        return bool(topic_uuid and transport.peer_topics.get(peer_addr) == topic_uuid)

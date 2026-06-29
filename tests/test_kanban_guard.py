import time
import unittest

from kanban_logic import KanbanLogic
from s_protocol import PRSPNode, SovereignProtocol


class KanbanGuardTests(unittest.TestCase):
    def test_pending_guard_blocks_stale_peer_rollback_even_with_newer_timestamp(self):
        transport = SovereignProtocol(9201, address="si-a")
        logic = KanbanLogic(transport)
        board = logic.ensure_board()
        column = board.children[0]

        stale_peer_board = PRSPNode.from_dict(board.to_dict())
        stale_peer_board.children[0].updated_at = "2999-01-01T00:00:00.000+00:00"

        with transport.lock:
            transport.members.add("si-b")
            transport.peer_topics["si-b"] = board.uuid
            transport.peer_perspectives["si-b"] = stale_peer_board

        snapshot = logic.local_change_snapshot()
        self.assertTrue(logic.update_column(column.uuid, "Mine"))
        logic.note_local_change(snapshot, "update_column", [column.uuid])

        self.assertFalse(logic.adopt_incoming_changes())
        self.assertEqual(transport._index[column.uuid].data["name"], "Mine")

    def test_guard_expiry_allows_later_peer_state_to_be_adopted(self):
        transport = SovereignProtocol(9202, address="si-a")
        logic = KanbanLogic(transport)
        logic.guard_ttl_seconds = 0.01
        board = logic.ensure_board()
        column = board.children[0]

        stale_peer_board = PRSPNode.from_dict(board.to_dict())
        stale_peer_board.children[0].updated_at = "2999-01-01T00:00:00.000+00:00"

        with transport.lock:
            transport.members.add("si-b")
            transport.peer_topics["si-b"] = board.uuid
            transport.peer_perspectives["si-b"] = stale_peer_board

        snapshot = logic.local_change_snapshot()
        self.assertTrue(logic.update_column(column.uuid, "Mine"))
        logic.note_local_change(snapshot, "update_column", [column.uuid])
        time.sleep(0.02)

        self.assertTrue(logic.adopt_incoming_changes())
        self.assertEqual(transport._index[column.uuid].data["name"], "To Do")


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

import app_server
from tests.test_kanban_new_logic import MemoryHttpClient, connect


class KanbanGuardTests(unittest.TestCase):
    def test_local_change_is_not_rolled_back_by_stale_peer_view(self):
        left = self.runtime(9201)
        right = self.runtime(9202)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        board = left.logic.ensure_board()
        connect(left, right, board.uuid)
        column = left.logic.columns(board)[0]

        card = left.logic.create_card(column.uuid, "Mine", "", []).value
        payload = left.logic.board_payload()

        self.assertIn(card.uuid, left.session.protocol.index)
        self.assertEqual(
            payload["transition_by_node"][board.uuid]["type"],
            "in_transition",
        )

    @staticmethod
    def runtime(port: int):
        directory = tempfile.TemporaryDirectory()
        config = app_server.load_config(None, "kanban")
        config["storage_file"] = str(Path(directory.name) / f"{port}.json")
        runtime = app_server.create_runtime(port, config)
        runtime._test_tmp = directory
        return runtime


if __name__ == "__main__":
    unittest.main()

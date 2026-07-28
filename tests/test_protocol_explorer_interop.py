"""S-Kanban sharing a board with Core's Protocol Explorer.

This needs both distributions installed at once, so it cannot live in Core:
Core must not depend on an application. It lives here because S-Kanban
already depends on Core, which makes this the only repository where the
pair can be exercised.
"""

import unittest

from tests.relay_clients import connect, relay_runtime, shared_relay_root


class ProtocolExplorerInteropTests(unittest.TestCase):
    def test_protocol_explorer_caches_kanban_share_without_claiming_ownership(self):
        relay_root = shared_relay_root(self)
        kanban = relay_runtime(self, 8151, relay_root, app="kanban")
        manual = relay_runtime(self, 8152, relay_root, app="manual")

        board = kanban.logic.ensure_board()
        invite = connect(kanban, manual)
        share = connect(kanban, manual, board.uuid)

        self.assertEqual(invite["status"], "ok")
        self.assertEqual(share["status"], "ok")
        # The Explorer registers no topic handler, so a shared board must
        # arrive as a cached peer perspective and a pending invitation -
        # never grafted into its own tree as though it owned it.
        self.assertNotIn(board.uuid, manual.session.protocol.index)
        self.assertIn(board.uuid, manual.session.pending_topic_invitations)
        self.assertIsNotNone(manual.session.get_cached_peer_subtree(
            kanban.peer_addr, board.uuid,
        ))


if __name__ == "__main__":
    unittest.main()

"""S-Initiative sharing an initiative with Core's Protocol Explorer.

This needs both distributions installed at once, so it cannot live in Core:
Core must not depend on an application. It lives here because S-Initiative
already depends on Core, which makes this the only repository where the
pair can be exercised.
"""

import unittest

from tests.relay_clients import connect, relay_runtime, shared_relay_root


class ProtocolExplorerInteropTests(unittest.TestCase):
    def test_protocol_explorer_caches_initiative_share_without_claiming_ownership(self):
        relay_root = shared_relay_root(self)
        initiative_app = relay_runtime(self, 8151, relay_root, app="initiative")
        manual = relay_runtime(self, 8152, relay_root, app="manual")

        initiative = initiative_app.logic.ensure_initiative()
        invite = connect(initiative_app, manual)
        share = connect(initiative_app, manual, initiative.uuid)

        self.assertEqual(invite["status"], "ok")
        self.assertEqual(share["status"], "ok")
        # The Explorer registers no topic handler, so a shared initiative must
        # arrive as a cached peer perspective and a pending invitation -
        # never grafted into its own tree as though it owned it.
        self.assertNotIn(initiative.uuid, manual.session.protocol.index)
        self.assertIn(initiative.uuid, manual.session.pending_topic_invitations)
        self.assertIsNotNone(manual.session.get_cached_peer_subtree(
            initiative_app.peer_addr, initiative.uuid,
        ))


if __name__ == "__main__":
    unittest.main()

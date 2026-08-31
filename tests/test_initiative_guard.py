import unittest

from tests.relay_clients import connect, relay_runtime, shared_relay_root


class InitiativeGuardTests(unittest.TestCase):
    def test_local_change_is_not_rolled_back_by_stale_peer_view(self):
        left = self.runtime(9201)
        right = self.runtime(9202)
        initiative = left.logic.ensure_initiative()
        connect(left, right, initiative.uuid)
        column = left.logic.columns(initiative)[0]

        card = left.logic.create_card(column.uuid, "Mine", "", []).value
        payload = left.logic.board_payload()

        self.assertIn(card.uuid, left.session.protocol.index)
        # The fresh local card is what's in transition until the peer observes
        # it - not the initiative. A card creation no longer re-revisions the initiative
        # (its content_hash is unchanged; only its subtree_hash moved), so the
        # initiative's own transition stays in_agreement.
        self.assertEqual(
            payload["transition_by_node"][card.uuid]["stage"],
            "in_flight",
        )
        self.assertEqual(
            payload["transition_by_node"][initiative.uuid]["type"],
            "in_agreement",
        )

    def setUp(self):
        self._relay_root = shared_relay_root(self)

    def runtime(self, port: int):
        return relay_runtime(self, port, self._relay_root)


if __name__ == "__main__":
    unittest.main()

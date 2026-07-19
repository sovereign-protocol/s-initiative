import tempfile
import unittest
from pathlib import Path

from sovereign import app_server
from tests.test_kanban_new_logic import MemoryHttpClient, connect


class CardCommentTests(unittest.TestCase):
    @staticmethod
    def runtime(port: int):
        directory = tempfile.TemporaryDirectory()
        config = app_server.load_config(None, "kanban")
        config["storage_file"] = str(Path(directory.name) / f"{port}.json")
        runtime = app_server.create_runtime(port, config)
        runtime._test_tmp = directory
        return runtime

    def _board_card(self, runtime):
        board = runtime.logic.ensure_board()
        column = runtime.logic.columns(board)[0]
        card = runtime.logic.create_card(column.uuid, "Card", "", []).value
        return board, card

    def _pair(self, port_a, port_b):
        left = self.runtime(port_a)
        right = self.runtime(port_b)
        client = MemoryHttpClient({left.address: left, right.address: right})
        left.adapter.http = client
        right.adapter.http = client
        return left, right

    def test_create_list_and_delete_comment(self):
        rt = self.runtime(8401)
        _board, card = self._board_card(rt)
        comment = rt.logic.create_card_comment(card.uuid, "  hello  ").value

        comments = rt.logic.board_payload()["comments_by_card"][card.uuid]
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["text"], "hello")  # trimmed
        self.assertEqual(comments[0]["author"], rt.logic.user_profile().uuid)

        self.assertEqual(rt.logic.delete_card_comment(comment.uuid).status, "ok")
        self.assertNotIn(card.uuid, rt.logic.board_payload()["comments_by_card"])

    def test_empty_comment_is_rejected(self):
        rt = self.runtime(8402)
        _board, card = self._board_card(rt)
        self.assertEqual(rt.logic.create_card_comment(card.uuid, "   ").status, "error")

    def test_comments_are_ordered_by_time(self):
        rt = self.runtime(8403)
        _board, card = self._board_card(rt)
        for text in ("first", "second", "third"):
            rt.logic.create_card_comment(card.uuid, text)
        texts = [c["text"] for c in rt.logic.board_payload()["comments_by_card"][card.uuid]]
        self.assertEqual(texts, ["first", "second", "third"])

    def test_adding_a_comment_does_not_diverge_the_card(self):
        # A comment changes the card's subtree hash but not its own content
        # hash, so the card's own transition stays in_agreement.
        left, right = self._pair(8404, 8405)
        board = left.logic.ensure_board()
        column = left.logic.columns(board)[0]
        card = left.logic.create_card(column.uuid, "Card", "", []).value
        connect(left, right, board.uuid)
        right.logic.set_auto_adopt_mode("never")

        left.logic.create_card_comment(card.uuid, "note")
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
        payload = right.logic.board_payload()

        card_transition = payload["transition_by_node"].get(card.uuid, {}).get("type")
        self.assertIn(card_transition, (None, "in_agreement"))

    def test_only_the_author_can_delete_a_comment(self):
        left, right = self._pair(8406, 8407)
        board = left.logic.ensure_board()
        column = left.logic.columns(board)[0]
        card = left.logic.create_card(column.uuid, "Card", "", []).value
        connect(left, right, board.uuid)
        right.logic.set_auto_adopt_mode("always")

        comment = left.logic.create_card_comment(card.uuid, "A's note").value
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
        right.logic.board_payload()

        self.assertIn(comment.uuid, right.session.protocol.index)
        self.assertEqual(right.logic.delete_card_comment(comment.uuid).status, "error")

    def test_peer_comment_auto_adopts_even_under_not_owner(self):
        left, right = self._pair(8408, 8409)
        board = left.logic.ensure_board()
        column = left.logic.columns(board)[0]
        card = left.logic.create_card(column.uuid, "Card", "", []).value
        connect(left, right, board.uuid)
        right.logic.set_auto_adopt_mode("not_owner")

        comment = left.logic.create_card_comment(card.uuid, "hi from A").value
        left.adapter.execute_effects(left.session.sync_effects(board.uuid))
        right.logic.board_payload()

        self.assertIn(comment.uuid, right.session.protocol.index)
        self.assertIn(card.uuid, right.logic.board_payload()["comments_by_card"])

    def test_concurrent_comments_from_two_clients_both_survive(self):
        left, right = self._pair(8410, 8411)
        board = left.logic.ensure_board()
        column = left.logic.columns(board)[0]
        card = left.logic.create_card(column.uuid, "Card", "", []).value
        connect(left, right, board.uuid)
        left.logic.set_auto_adopt_mode("always")
        right.logic.set_auto_adopt_mode("always")

        from_a = left.logic.create_card_comment(card.uuid, "from A").value
        from_b = right.logic.create_card_comment(card.uuid, "from B").value

        for _ in range(2):
            left.adapter.execute_effects(left.session.sync_effects(board.uuid))
            right.logic.board_payload()
            right.adapter.execute_effects(right.session.sync_effects(board.uuid))
            left.logic.board_payload()

        for runtime in (left, right):
            self.assertIn(from_a.uuid, runtime.session.protocol.index)
            self.assertIn(from_b.uuid, runtime.session.protocol.index)


if __name__ == "__main__":
    unittest.main()

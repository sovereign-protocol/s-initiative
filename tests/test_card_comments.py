import unittest

from tests.relay_clients import (
    connect, relay_runtime, shared_relay_root, sync,
)


class CardCommentTests(unittest.TestCase):
    def setUp(self):
        self._relay_root = shared_relay_root(self)

    def runtime(self, port: int):
        return relay_runtime(self, port, self._relay_root)

    def _board_card(self, runtime):
        board = runtime.logic.ensure_board()
        column = runtime.logic.columns(board)[0]
        card = runtime.logic.create_card(column.uuid, "Card", "", []).value
        return board, card

    def _pair(self, port_a, port_b):
        return self.runtime(port_a), self.runtime(port_b)

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
        sync(left, right)
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
        sync(left, right)
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
        sync(left, right)
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
            sync(left, right)
            right.logic.board_payload()
            sync(left, right)
            left.logic.board_payload()

        for runtime in (left, right):
            self.assertIn(from_a.uuid, runtime.session.protocol.index)
            self.assertIn(from_b.uuid, runtime.session.protocol.index)


class CardAttachmentTests(unittest.TestCase):
    setUp = CardCommentTests.setUp
    runtime = CardCommentTests.runtime
    _board_card = CardCommentTests._board_card
    _pair = CardCommentTests._pair

    @staticmethod
    def _reference(runtime, payload: bytes = b"report-bytes", name="report.pdf"):
        blob_id = runtime.blob_store.write_blob(payload)
        return {
            "id": "attachment-1",
            "role": "attachment",
            "blob_id": blob_id,
            "name": name,
            "size": len(payload),
            "mime": "application/pdf",
        }

    def test_attach_list_and_remove_a_file(self):
        rt = self.runtime(8420)
        _board, card = self._board_card(rt)
        reference = self._reference(rt)

        node = rt.logic.create_card_attachment(card.uuid, reference).value

        files = rt.logic.board_payload()["attachments_by_card"][card.uuid]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["name"], "report.pdf")
        self.assertEqual(files[0]["size"], len(b"report-bytes"))
        self.assertEqual(files[0]["url"], f"/api/blob/{reference['blob_id']}")
        self.assertEqual(files[0]["author"], rt.logic.user_profile().uuid)

        self.assertEqual(rt.logic.delete_card_attachment(node.uuid).status, "ok")
        self.assertNotIn(
            card.uuid, rt.logic.board_payload()["attachments_by_card"],
        )

    def test_a_reference_without_a_real_blob_id_is_rejected(self):
        rt = self.runtime(8421)
        _board, card = self._board_card(rt)

        self.assertEqual(
            rt.logic.create_card_attachment(card.uuid, {}).status, "error",
        )
        self.assertEqual(
            rt.logic.create_card_attachment(
                card.uuid, {"id": "x", "blob_id": "not-a-hash", "size": 1},
            ).status,
            "error",
        )

    def test_attaching_a_file_does_not_diverge_the_card(self):
        # Same reasoning as comments: a file is a child node, so it moves the
        # card's subtree hash while leaving its own content untouched.
        left, right = self._pair(8422, 8423)
        board = left.logic.ensure_board()
        column = left.logic.columns(board)[0]
        card = left.logic.create_card(column.uuid, "Card", "", []).value
        connect(left, right, board.uuid)
        right.logic.set_auto_adopt_mode("never")

        left.logic.create_card_attachment(card.uuid, self._reference(left))
        sync(left, right)
        payload = right.logic.board_payload()

        card_transition = payload["transition_by_node"].get(card.uuid, {}).get("type")
        self.assertIn(card_transition, (None, "in_agreement"))

    def test_only_the_author_can_remove_an_attachment(self):
        left, right = self._pair(8424, 8425)
        board = left.logic.ensure_board()
        column = left.logic.columns(board)[0]
        card = left.logic.create_card(column.uuid, "Card", "", []).value
        connect(left, right, board.uuid)
        right.logic.set_auto_adopt_mode("always")

        node = left.logic.create_card_attachment(
            card.uuid, self._reference(left),
        ).value
        sync(left, right)
        right.logic.board_payload()

        self.assertIn(node.uuid, right.session.protocol.index)
        self.assertEqual(
            right.logic.delete_card_attachment(node.uuid).status, "error",
        )

    def test_attached_file_is_reachable_as_a_blob_reference(self):
        # Core's GC/transfer walker must see a card attachment without any
        # Kanban-specific knowledge, or the bytes would be collected as
        # unreferenced while the card still points at them.
        from sovereign.blob_store import referenced_blob_ids

        rt = self.runtime(8426)
        board, card = self._board_card(rt)
        reference = self._reference(rt)

        rt.logic.create_card_attachment(card.uuid, reference)

        self.assertIn(
            reference["blob_id"],
            referenced_blob_ids(rt.session.protocol.index[board.uuid]),
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from agent_memory_kit.errors import PublicError
from agent_memory_kit.knowledge import Snapshot
from support import CONTENT, make_principal


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = Snapshot(CONTENT)
        self.principal = make_principal()

    def test_context_separates_required_and_reference_documents(self):
        context = self.snapshot.context(
            "demo", "demo-app", "a" * 40, "development", "test", self.principal
        )
        self.assertEqual(context["application_id"], "demo-app")
        self.assertEqual(context["required_documents"][0]["status"], "approved")
        self.assertEqual({item["status"] for item in context["reference_documents"]}, {"observed", "draft"})
        self.assertFalse(context["grants_permissions"])
        with self.assertRaises(PublicError):
            self.snapshot.context("demo", "unknown", None, "development", "", self.principal)
        with self.assertRaises(PublicError):
            self.snapshot.context("demo", "demo-app", "short", "development", "", self.principal)

    def test_document_pagination_and_stale_snapshot(self):
        document_id = "memory-contract"
        expected = self.snapshot.docs[document_id]["content"]
        first = self.snapshot.document(document_id, limit=25)
        second = self.snapshot.document(document_id, snapshot_id=self.snapshot.id, cursor=first["next_cursor"], limit=5000)
        self.assertEqual(first["content"] + second["content"], expected)
        with self.assertRaises(PublicError):
            self.snapshot.document(document_id, snapshot_id="0" * 64)
        with self.assertRaises(PublicError):
            self.snapshot.safe_read("../pyproject.toml")

    def test_fts_search_and_filters(self):
        result = self.snapshot.search("восстановление", limit=5)
        self.assertEqual(result["results"][0]["id"], "operations-guide")
        draft = self.snapshot.search("векторное", profile="future")
        self.assertEqual(draft["results"][0]["status"], "draft")
        self.assertTrue(draft["drafts_are_reference_only"])


if __name__ == "__main__":
    unittest.main()

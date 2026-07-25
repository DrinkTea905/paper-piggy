# -*- coding: utf-8 -*-
"""人工核验版与 Agent 待审稿的双轨安全测试；全程只使用临时目录。"""
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))

import server  # noqa: E402
import wiki_store as W  # noqa: E402


class WikiPendingReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "wiki"
        self.stack = ExitStack()
        paths = {
            "WIKI_DIR": self.root,
            "WIKI_ANSWERS_DIR": self.root / "answers",
            "WIKI_CONCEPTS_DIR": self.root / "concepts",
            "WIKI_TOPICS_DIR": self.root / "topics",
            "WIKI_DIGEST_DIR": self.root / "digests",
            "WIKI_OUTLINE_DIR": self.root / "outlines",
            "WIKI_ENTITY_DIR": self.root / "entities",
            "WIKI_OVERVIEW_DIR": self.root / "overviews",
            "WIKI_INDEX": self.root / "index.json",
            "WIKI_SCHEMA_MD": self.root / "WIKI.md",
            "WIKI_HISTORY_DIR": self.root / ".history",
        }
        for name, value in paths.items():
            self.stack.enter_context(mock.patch.object(W.C, name, value))
        self.stack.enter_context(mock.patch.object(W, "_paper_keys", return_value={"K1", "K2"}))
        self.stack.enter_context(mock.patch.object(
            W, "_resolve_citation", side_effect=lambda key, fallback="": fallback or f"引文-{key}"))
        self.snap = self.stack.enter_context(mock.patch.object(W, "_snapshot"))
        self.retriever = SimpleNamespace(
            index_wiki_page=mock.Mock(return_value=True),
            delete_wiki_page=mock.Mock(return_value=True),
            M={"wiki": {}},
        )
        self.stack.enter_context(mock.patch.dict(sys.modules, {"retriever": self.retriever}))
        W.ensure_scaffold()

    def tearDown(self):
        self.stack.close()
        self.tmp.cleanup()

    def create_page(self, page_id="p1", links=None):
        meta = W.save_research_page(
            page_id, "digest", f"标题-{page_id}", f"问题-{page_id}",
            "## 原结论\n\n原正文 [1]", ["K1"], generated_by="agent-v1", by_agent=True,
        )
        if links is not None:
            W.set_links(page_id, links, by_agent=False)
        W.set_verified(page_id, True)
        return meta

    def test_agent_update_stages_pending_without_touching_verified_page_or_index(self):
        self.create_page()
        canonical = W.page_path("p1", "digest").read_text(encoding="utf-8")
        index_before = W.index_map()["p1"].copy()
        self.retriever.index_wiki_page.reset_mock()

        result = W.update_page(
            "p1", title="新标题", content="## 新结论\n\nAgent 新正文 [1]",
            sources=["K2"], generated_by="agent-v2", by_agent=True,
        )

        self.assertTrue(result["pending_review"])
        self.assertEqual(canonical, W.page_path("p1", "digest").read_text(encoding="utf-8"))
        self.assertEqual(index_before, W.index_map()["p1"])
        self.assertTrue(W.pending_page_path("p1", "digest").exists())
        self.assertTrue(W.get_page("p1")["pending_review"])
        self.retriever.index_wiki_page.assert_not_called()

        diff = W.pending_review_diff("p1")
        self.assertIn("Agent 新正文", diff["body_diff"])
        self.assertEqual(diff["sources_added"], ["K2"])
        self.assertEqual(diff["sources_removed"], ["K1"])

    def test_accept_publishes_pending_as_new_verified_version(self):
        self.create_page()
        W.update_page(
            "p1", title="新标题", content="## 新结论\n\n接受后的正文 [1]",
            sources=["K2"], generated_by="agent-v2", by_agent=True,
        )
        self.retriever.index_wiki_page.reset_mock()

        result = W.accept_pending_review("p1")

        page = W.get_page("p1")
        self.assertFalse(page["pending_review"])
        self.assertEqual(page["title"], "新标题")
        self.assertIn("接受后的正文", page["markdown"])
        self.assertTrue(result["verified_at"])
        self.assertTrue(page["verified_at"])
        self.assertFalse(W.pending_page_path("p1", "digest").exists())
        self.retriever.index_wiki_page.assert_called_once()
        self.assertTrue(any(
            call.args[1] == "人接受并核验 Agent 待审修改"
            for call in self.snap.call_args_list
        ))

    def test_discard_keeps_verified_page_unchanged(self):
        self.create_page()
        canonical = W.page_path("p1", "digest").read_text(encoding="utf-8")
        W.update_page("p1", content="将被放弃的正文", generated_by="agent-v2", by_agent=True)

        result = W.discard_pending_review("p1")

        self.assertTrue(result["discarded"])
        self.assertEqual(canonical, W.page_path("p1", "digest").read_text(encoding="utf-8"))
        self.assertFalse(W.has_pending_review("p1"))

    def test_human_edit_of_pending_draft_publishes_and_verifies(self):
        self.create_page()
        W.update_page("p1", content="Agent 待审正文", sources=["K2"],
                      generated_by="agent-v2", by_agent=True)

        result = W.edit_page_by_human("p1", "## 人工修订\n\n最终正文 [1]")

        page = W.get_page("p1")
        self.assertIn("最终正文", page["markdown"])
        self.assertEqual([x["key"] for x in page["sources"]], ["K2"])
        self.assertTrue(result["verified_at"])
        self.assertFalse(page["pending_review"])

    def test_agent_link_change_is_staged_and_can_be_discarded(self):
        self.create_page("p2")
        self.create_page("p1")
        canonical_before = list(W.index_map()["p1"].get("links") or [])

        result = W.set_links("p1", ["p2"], by_agent=True)

        self.assertTrue(result["pending_review"])
        self.assertEqual(canonical_before, W.index_map()["p1"].get("links") or [])
        self.assertEqual(W.pending_review_diff("p1")["links_added"], ["p2"])
        W.discard_pending_review("p1")
        self.assertEqual(canonical_before, W.index_map()["p1"].get("links") or [])

    def test_verification_toggle_is_blocked_until_pending_is_reviewed(self):
        self.create_page()
        W.update_page("p1", content="Agent 待审正文", generated_by="agent-v2", by_agent=True)
        with self.assertRaisesRegex(ValueError, "先查看差异"):
            W.set_verified("p1", False)


class WikiActorBoundaryTests(unittest.TestCase):
    def test_agent_update_endpoint_does_not_trust_client_actor_flag(self):
        returned = {"id": "p1", "kind": "digest", "title": "页", "sources": []}
        query = server.WikiUpdateQ(content="正文", by_agent=False)
        with mock.patch.object(server.W, "update_page", return_value=returned) as update:
            response = server.wiki_update_page("p1", query)
        self.assertTrue(response["ok"])
        self.assertTrue(update.call_args.kwargs["by_agent"])

    def test_agent_link_endpoint_does_not_trust_client_actor_flag(self):
        query = server.WikiLinksQ(links=["p2"], by_agent=False)
        with mock.patch.object(server.W, "set_links", return_value={"links": ["p2"], "skipped": []}) as links:
            response = server.wiki_set_links("p1", query)
        self.assertTrue(response["ok"])
        self.assertTrue(links.call_args.kwargs["by_agent"])


if __name__ == "__main__":
    unittest.main()

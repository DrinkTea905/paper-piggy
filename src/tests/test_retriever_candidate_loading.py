# -*- coding: utf-8 -*-
"""候选按需读取回归测试：不需要模型、API 或真实知识库。"""
import sys
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))

import retriever as R  # noqa: E402


class _Reranker:
    def __init__(self, by_text):
        self.by_text = by_text

    def scores(self, _query, texts):
        return [self.by_text[t] for t in texts]


class CandidateLoadingTests(unittest.TestCase):
    def setUp(self):
        self._old_m = dict(R.M)
        R.M.clear()
        R.M.update({"rerank": _Reranker({"meta": 3.0, "chunk": 2.0, "other": 1.0}),
                    "wiki": {}, "statute_status": {}})

    def tearDown(self):
        R.M.clear()
        R.M.update(self._old_m)

    def test_full_search_uses_only_fused_candidates_without_global_records(self):
        rows = {
            # meta 先按 reranker 入选，随后同篇 chunk 应顶替它（保持旧去重语义）。
            "a": {"chunk_id": "a", "key": "K1", "text": "meta", "row_type": "meta",
                  "title": "题录", "journal_tier": "普通"},
            "b": {"chunk_id": "b", "key": "K1", "text": "chunk", "row_type": "chunk",
                  "title": "正文", "journal_tier": "普通"},
            "c": {"chunk_id": "c", "key": "K2", "text": "other", "row_type": "chunk",
                  "title": "另一篇", "journal_tier": "普通"},
        }
        seen = {}

        def fetch(ids):
            seen["ids"] = list(ids)
            return {cid: rows[cid] for cid in ids if cid in rows}

        with (mock.patch.object(R, "dense_search", return_value=["a", "b"]),
              mock.patch.object(R, "bm25_search", return_value=["b", "c"]),
              mock.patch.object(R, "fetch_records", side_effect=fetch),
              mock.patch.object(R, "_weight_res", return_value=None)):
            out = R.search_full("测试", 2, "relevance")

        self.assertEqual([x["chunk_id"] for x in out], ["b", "c"])
        self.assertEqual(seen["ids"], R.rrf(["a", "b"], ["b", "c"]))
        self.assertNotIn("records", R.M)
        self.assertFalse(any("vector" in x for x in out))

    def test_result_columns_never_include_vector(self):
        self.assertNotIn("vector", R._RESULT_COLUMNS)

    def test_wiki_and_repealed_penalties_accept_candidate_dicts(self):
        R.M["wiki"] = {"W": {"stale": True}}
        R.M["statute_status"] = {"LAW": "已废止"}
        wiki = {"chunk_id": "W::wiki", "key": "W", "row_type": "wiki"}
        law = {"chunk_id": "LAW::p1", "key": "LAW", "row_type": "chunk"}
        self.assertAlmostEqual(R._wiki_effective(10.0, wiki), 10.0 * R.C.WIKI_STALE_FACTOR)
        self.assertAlmostEqual(R._statute_eff(10.0, law),
                               10.0 * R.C.STATUTE_REPEALED_FACTOR)

    def test_statute_heading_uses_key_scoped_rows(self):
        rows = [{"page": 3, "heading": "第一条"}, {"page": 4, "heading": "第二条"}]
        with mock.patch.object(R, "_rows_for_key", return_value=rows) as q:
            self.assertEqual(R.find_statute_heading("LAW", 4), "第二条")
        q.assert_called_once_with("LAW", ("page", "heading"), row_type="chunk", limit=8,
                                  page=4)

    def test_delete_wiki_only_decrements_count_when_table_row_exists(self):
        class _Table:
            def delete(self, _predicate):
                pass

        R.M.update({"tbl": _Table(), "row_count": 10, "wiki": {"W": {}}})
        with mock.patch.object(R, "existing_chunk_ids", return_value=set()):
            self.assertTrue(R.delete_wiki_page("W"))
        self.assertEqual(R.M["row_count"], 10)

        R.M["wiki"] = {"W": {}}
        with mock.patch.object(R, "existing_chunk_ids", return_value={"W::wiki"}):
            self.assertTrue(R.delete_wiki_page("W"))
        self.assertEqual(R.M["row_count"], 9)


if __name__ == "__main__":
    unittest.main()

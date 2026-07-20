# -*- coding: utf-8 -*-
"""Wiki 来源与正文外壳质量护栏：纯临时数据，不接触真实综合层。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))

import wiki_store as W  # noqa: E402


class WikiSourceQualityTests(unittest.TestCase):
    def test_invalid_source_rejects_whole_save_and_suggests_nearest_key(self):
        with mock.patch.object(W, "ensure_scaffold"), \
                mock.patch.object(W, "_paper_keys", return_value={"GOODKEY1", "H3HVZ5YW"}), \
                mock.patch.object(W, "_atomic_write") as write, \
                mock.patch.object(W, "_upsert_index") as upsert:
            with self.assertRaisesRegex(ValueError, r"H3HVZYW.*H3HVZ5YW.*整页未写入"):
                W.save_answer("测试问题", "这是足够长的正文。", [
                    {"key": "GOODKEY1"}, {"key": "H3HVZYW"},
                ])
        write.assert_not_called()
        upsert.assert_not_called()

    def test_valid_sources_are_deduplicated_after_validation(self):
        with mock.patch.object(W, "_paper_keys", return_value={"GOODKEY1"}), \
                mock.patch.object(W, "_resolve_citation", return_value="规范引文"):
            result = W._norm_sources(["GOODKEY1", {"key": "GOODKEY1", "citation": "旧引文"}])
        self.assertEqual(result, [{"key": "GOODKEY1", "citation": "规范引文"}])

    def test_wiki_search_hit_is_expanded_to_its_paper_sources(self):
        wiki_page = {"sources": [{"key": "GOODKEY1", "citation": "页级引文"}]}
        with mock.patch.object(W, "_paper_keys", return_value={"GOODKEY1"}), \
                mock.patch.object(W, "index_map", return_value={"overview-test": wiki_page}), \
                mock.patch.object(W, "_resolve_citation", side_effect=lambda key, fallback="": fallback or key):
            result = W._norm_sources(["overview-test", "GOODKEY1"])
        self.assertEqual(result, [{"key": "GOODKEY1", "citation": "页级引文"}])

    def test_wiki_search_hit_without_paper_provenance_is_rejected(self):
        with mock.patch.object(W, "_paper_keys", return_value={"GOODKEY1"}), \
                mock.patch.object(W, "index_map", return_value={"overview-empty": {"sources": []}}):
            with self.assertRaisesRegex(ValueError, "overview-empty.*整页未写入"):
                W._norm_sources(["overview-empty"])

    def test_source_validation_fails_closed_when_catalog_is_unavailable(self):
        with mock.patch.object(W, "_paper_keys", return_value=set()):
            with self.assertRaisesRegex(ValueError, "文献目录尚未加载.*整页未写入"):
                W._norm_sources(["ANYKEY"])

    def test_replace_can_repair_legacy_invalid_source_before_validation(self):
        existing = {
            "id": "topic-fenliu-zhuanchu", "kind": "topic", "title": "分流转处",
            "subject": "分流转处", "sources": [{"key": "H3HVZYW"}],
        }
        with mock.patch.object(W, "ensure_scaffold"), \
                mock.patch.object(W, "index_map", return_value={existing["id"]: existing}), \
                mock.patch.object(W, "_paper_keys", return_value={"H3HVZ5YW"}), \
                mock.patch.object(W, "_resolve_citation", return_value="规范引文"), \
                mock.patch.object(W, "_persist_page", side_effect=lambda *a, **k: {
                    "id": a[0], "kind": a[1], "title": a[2], "sources": a[5],
                }) as persist, \
                mock.patch.object(W, "_snapshot"):
            result = W.update_page(
                existing["id"], content="更正后的完整正文", sources=[{"key": "H3HVZ5YW"}], mode="replace",
            )

        self.assertEqual(result["sources"], [{"key": "H3HVZ5YW", "citation": "规范引文"}])
        self.assertEqual(persist.call_args.args[5], result["sources"])


class WikiScaffoldQualityTests(unittest.TestCase):
    def test_exact_leading_title_and_question_are_removed_once(self):
        content = "# 少年司法\n\n> **研究问题**：制度如何发展\n\n## 结论\n正文"
        cleaned = W._strip_leading_scaffold(content, "少年司法", "制度如何发展")
        self.assertEqual(cleaned, "## 结论\n正文")

    def test_nonmatching_or_later_heading_is_preserved(self):
        content = "开篇说明\n\n# 少年司法\n正文"
        self.assertEqual(W._strip_leading_scaffold(content, "少年司法", "制度如何发展"), content)

    def test_lint_reports_legacy_invalid_key_and_duplicate_scaffold(self):
        page = {
            "id": "topic-test", "kind": "topic", "title": "少年司法", "subject": "制度如何发展",
            "sources": [{"key": "H3HVZYW"}], "links": [], "generated_by": "model",
        }
        with tempfile.TemporaryDirectory() as td:
            page_file = Path(td) / "topic-test.md"
            page_file.write_text(W._render_md(
                page, page["subject"],
                "# 少年司法\n\n> **研究问题**：制度如何发展\n\n## 结论\n正文",
                page["sources"],
            ), encoding="utf-8")
            with mock.patch.object(W, "load_index", return_value={"pages": [page]}), \
                    mock.patch.object(W, "_paper_keys", return_value={"H3HVZ5YW"}), \
                    mock.patch.object(W, "page_path", return_value=page_file):
                result = W.lint()

        self.assertEqual(result["issues"]["invalid_source"][0]["key"], "H3HVZYW")
        self.assertEqual(result["issues"]["invalid_source"][0]["suggestions"], ["H3HVZ5YW"])
        self.assertEqual(result["issues"]["duplicate_scaffold"][0]["id"], "topic-test")


if __name__ == "__main__":
    unittest.main()

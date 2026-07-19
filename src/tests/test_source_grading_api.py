# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path
from unittest import mock


SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import server
import grading_svc as GS


class SourceGradingApiTests(unittest.TestCase):
    def test_papers_filters_recognized_source_type_and_returns_contract(self):
        rows = {
            "r": {"key": "r", "itemtype": "webpage", "title": "司法保护白皮书",
                  "institution": "最高人民法院", "has_pdf": True},
            "b": {"key": "b", "itemtype": "book", "title": "重要著作", "has_pdf": True},
        }
        with mock.patch.multiple(
            server,
            _load_papers=mock.Mock(return_value=rows),
            _load_cats=mock.Mock(return_value={}),
            _deep_keys=mock.Mock(return_value=set()),
            _deep_no_text_keys=mock.Mock(return_value=set()),
            _deep_extract_items=mock.Mock(return_value={}),
            _summary_keys=mock.Mock(return_value=set()),
            _summary_issues=mock.Mock(return_value={}),
            _extract_record=mock.Mock(return_value={"status": "not_indexed"}),
            _rec_score=mock.Mock(return_value=0.0),
        ):
            got = server.papers(source_type="report", limit=20)
            by_label = server.papers(objective_label="书籍", limit=20)
            by_title = server.papers(query="重要著作", sort="match", limit=20)
            by_author = server.papers(query="最高人民法院", sort="match", limit=20)
        self.assertEqual(1, got["total"])
        self.assertEqual("r", got["papers"][0]["key"])
        self.assertEqual(("report", "官方报告", "core"),
                         tuple(got["papers"][0][k] for k in
                               ("source_type", "objective_label", "band")))
        self.assertEqual(1, got["source_type_counts"]["report"])
        self.assertEqual(1, got["source_type_counts"]["book"])
        self.assertEqual(1, by_label["total"])
        self.assertEqual("b", by_label["papers"][0]["key"])
        self.assertEqual("书籍", by_label["objective_label"])
        self.assertEqual(1, by_label["objective_label_counts"]["书籍"])
        self.assertEqual(["b"], [x["key"] for x in by_title["papers"]])
        self.assertEqual(["r"], [x["key"] for x in by_author["papers"]])
        self.assertEqual("最高人民法院", by_author["query"])

    def test_wiki_sources_are_enriched_in_one_response(self):
        page = {"id": "w", "sources": [{"key": "b", "citation": "某书"}]}
        papers = {"b": {"key": "b", "itemtype": "book", "title": "某书"}}
        with (
            mock.patch.object(server.W, "get_page", return_value=page),
            mock.patch.object(server, "_load_papers", return_value=papers),
        ):
            got = server.wiki_page("w")
        self.assertEqual("书籍", got["sources"][0]["objective_label"])
        self.assertEqual("authority", got["sources"][0]["band"])
        self.assertEqual([{"label": "书籍", "count": 1}], got["source_composition"])

    def test_reset_all_mappings_uses_current_discipline_without_touching_index(self):
        with (
            mock.patch.object(GS, "clear_mapping_overrides", return_value={
                "discipline": "law_personal_fun", "canonical_discipline": "law_personal", "removed": 1,
            }) as clear,
            mock.patch.object(GS, "warm_async") as warm,
            mock.patch.object(server, "_load_papers", return_value={"a": {"key": "a"}}),
        ):
            got = server.reset_grading_mappings()
        self.assertTrue(got["ok"])
        self.assertEqual(1, got["removed"])
        clear.assert_called_once_with()
        warm.assert_called_once()

    def test_dashboard_polish_contract(self):
        app = (SRC / "web" / "app.js").read_text(encoding="utf-8")
        html = (SRC / "web" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn('aria-hidden="true">→</span>', app)
        self.assertIn("点击项目查看文献", app)
        self.assertIn("恢复全部默认", app)
        self.assertIn("无需重建索引", app)
        self.assertIn('id="lib-search-mode"', html)
        self.assertIn('id="bl-label-filter"', html)
        self.assertNotIn('data-tab="search"', html)
        self.assertNotIn('id="bl-cite-sel"', html)
        self.assertNotIn("值得先读", app)
        self.assertIn("topics: true, zot: true", app)


if __name__ == "__main__":
    unittest.main()

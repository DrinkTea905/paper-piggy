# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path
from unittest import mock


SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import server


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
        self.assertEqual(1, got["total"])
        self.assertEqual("r", got["papers"][0]["key"])
        self.assertEqual(("report", "官方报告", "core"),
                         tuple(got["papers"][0][k] for k in
                               ("source_type", "objective_label", "band")))
        self.assertEqual(1, got["source_type_counts"]["report"])
        self.assertEqual(1, got["source_type_counts"]["book"])

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


if __name__ == "__main__":
    unittest.main()

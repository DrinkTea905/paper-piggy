# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import research_assistant as RA


class ReferenceMiningTests(unittest.TestCase):
    def _write_extracted(self, root, key, pages):
        from textutil import safe_name
        p = root / f"{safe_name(key)}.json"
        p.write_text(json.dumps({"title": key, "pages": pages}, ensure_ascii=False), encoding="utf-8")

    def test_mines_chinese_and_merges_same_doi_with_provenance(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_extracted(root, "S1", [{"page": 3, "text":
                "参见 张三：《数字法治》，载《法学研究》2021年第2期，DOI:10.1234/Ab.Cd。"}])
            self._write_extracted(root, "S2", [{"page": 9, "text":
                "另见 张三：《数字法治》，载《法学研究》，2021年，https://doi.org/10.1234/ab.cd"}])
            hits = [{"key": "S1", "title": "来源甲"}, {"key": "S2", "title": "来源乙"}]
            with mock.patch.object(RA.C, "EXTRACTED", root):
                got = RA._mine_citations(hits)

        self.assertEqual(1, len(got))
        item = got[0]
        self.assertEqual("张三", item["author"])
        self.assertEqual("数字法治", item["title"])
        self.assertEqual("2021", item["year"])
        self.assertEqual("10.1234/ab.cd", item["doi"])
        self.assertEqual("法学研究", item["journal"])
        self.assertEqual(2, item["freq"])
        self.assertEqual({"S1", "S2"}, {p["source_key"] for p in item["provenance"]})
        self.assertEqual({3, 9}, {p["pdf_page"] for p in item["provenance"]})
        self.assertTrue(all(p["raw"] for p in item["provenance"]))

    def test_mines_numbered_english_reference(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            raw = ("[12] Smith, John. (2020). Comparative Juvenile Justice. "
                   "Yale Law Journal, 129(3), 100-120. https://doi.org/10.5555/YLJ.2020.1")
            self._write_extracted(root, "EN1", [{"page": 8, "text": raw}])
            with mock.patch.object(RA.C, "EXTRACTED", root):
                got = RA._mine_citations([{"key": "EN1", "title": "English source"}])

        self.assertEqual(1, len(got))
        item = got[0]
        self.assertEqual("Smith, John", item["author"])
        self.assertEqual("Comparative Juvenile Justice", item["title"])
        self.assertEqual("2020", item["year"])
        self.assertEqual("Yale Law Journal", item["journal"])
        self.assertEqual("10.5555/ylj.2020.1", item["doi"])
        self.assertEqual(12, item["provenance"][0]["ref_index"])
        self.assertEqual(8, item["provenance"][0]["pdf_page"])

    def test_doi_merges_ocr_title_variants_across_sources(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_extracted(root, "A", [{"page": 1, "text":
                "[1] Smith, John. (2020). Comparative Juvenile Justice. Law Review. doi:10.7777/SAME"}])
            self._write_extracted(root, "B", [{"page": 2, "text":
                "[2] Smith, J. (2020). Comparative Juvenile Justice A Study. Law Review. https://doi.org/10.7777/same"}])
            with mock.patch.object(RA.C, "EXTRACTED", root):
                got = RA._mine_citations([{"key": "A", "title": "甲"}, {"key": "B", "title": "乙"}])

        self.assertEqual(1, len(got))
        self.assertEqual(2, got[0]["freq"])
        self.assertEqual("10.7777/same", got[0]["doi"])
        self.assertEqual({"A", "B"}, {p["source_key"] for p in got[0]["provenance"]})

    def test_suggest_sources_marks_library_items_and_separates_network_section(self):
        mined = [
            {"author": "A", "title": "Different title", "year": "2020",
             "doi": "10.1000/same", "journal": "J", "freq": 1, "provenance": []},
            {"author": "B", "title": "Same   Title!", "year": "2019",
             "doi": "", "journal": "K", "freq": 1, "provenance": []},
            {"author": "C", "title": "Missing Work", "year": "2018",
             "doi": "10.1000/missing", "journal": "L", "freq": 1, "provenance": []},
        ]
        papers = {
            "K1": {"title": "Unrelated", "doi": "https://doi.org/10.1000/SAME"},
            "K2": {"title": "same title", "doi": ""},
        }
        fake_retriever = types.SimpleNamespace(M={"papers": papers})
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(RA, "_recall", return_value=([], None)), \
             mock.patch.object(RA, "_mine_citations", return_value=mined), \
             mock.patch.object(RA.C, "STATE", Path(td)), \
             mock.patch.dict(sys.modules, {"retriever": fake_retriever}):
            got = RA.suggest_sources("topic")

        all_items = got["reference_chain_candidates"]
        self.assertTrue(all_items[0]["in_library"])
        self.assertEqual("K1", all_items[0]["library_key"])
        self.assertTrue(all_items[1]["in_library"])
        self.assertEqual("K2", all_items[1]["library_key"])
        self.assertFalse(all_items[2]["in_library"])
        self.assertEqual(["Missing Work"], [x["title"] for x in got["missing_cited"]])
        self.assertEqual(all_items, got["candidate_sections"]["library_reference_chain"])
        self.assertEqual([], got["candidate_sections"]["network_supplement"])
        self.assertFalse(got["network_search"]["performed"])


if __name__ == "__main__":
    unittest.main()

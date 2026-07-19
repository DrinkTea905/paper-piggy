# -*- coding: utf-8 -*-
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path


SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class SourceGradingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_data = os.environ.get("LOCALKB_DATA")
        os.environ["LOCALKB_DATA"] = cls._tmp.name

        cls.SR = importlib.import_module("source_rules")
        cls.GS = importlib.import_module("grading_svc")
        cls.JG = importlib.import_module("journal_grading")
        cls.JG.reload()

        root = Path(cls._tmp.name)
        cls.SR.OVERRIDE_FILE = root / "tier_overrides.json"
        cls.GS.MEMO_FILE = root / "grading_memo.json"
        cls.GS.DIST_FILE = root / "grading_dist.json"
        cls.GS.MAPPING_FILE = root / "grading_mappings.json"

    @classmethod
    def tearDownClass(cls):
        if cls._old_data is None:
            os.environ.pop("LOCALKB_DATA", None)
        else:
            os.environ["LOCALKB_DATA"] = cls._old_data
        cls._tmp.cleanup()

    def setUp(self):
        try:
            self.SR.OVERRIDE_FILE.unlink()
        except FileNotFoundError:
            pass
        self.SR._OV = {"mtime": None, "data": {}}
        self.GS._MEMO = {}
        self.GS._MEMO_LOADED = True
        self.GS._MEMO_DIRTY = False
        self.GS._DIST = {}
        self.GS._DIST_LOADED = True
        try:
            self.GS.MAPPING_FILE.unlink()
        except FileNotFoundError:
            pass

    def ev(self, paper, discipline="law_personal"):
        return self.GS.evaluate_paper(paper, discipline=discipline)

    def test_non_journal_presets(self):
        cases = [
            ({"itemtype": "book", "title": "书"}, "书籍", "authority"),
            ({"itemtype": "bookSection", "title": "章"}, "书章", "authority"),
            ({"itemtype": "thesis", "title": "论文", "thesis_type": "博士"}, "博士论文", "top"),
            ({"itemtype": "thesis", "title": "论文", "thesis_type": "硕士"}, "硕士论文", "top"),
            ({"itemtype": "statute", "title": "某法"}, "法源", "top"),
            ({"itemtype": "case", "title": "某案"}, "案例", "top"),
            ({"itemtype": "standard", "title": "某标准"}, "标准", "top"),
            ({"itemtype": "report", "title": "研究报告"}, "研究报告", "top"),
            ({"itemtype": "preprint", "title": "预印本"}, "预印本", "normal"),
            ({"itemtype": "conferencePaper", "title": "会议论文"}, "会议论文", "normal"),
        ]
        for paper, label, band in cases:
            with self.subTest(paper=paper):
                got = self.ev(paper)
                self.assertEqual(label, got["objective_label"])
                self.assertEqual(band, got["band"])

    def test_dataset_and_container_content_override(self):
        official = self.ev({"itemtype": "dataset", "title": "人口数据", "institution": "国家统计局"})
        self.assertEqual(("权威数据", "core"), (official["objective_label"], official["band"]))
        ordinary = self.ev({"itemtype": "dataset", "title": "研究数据"})
        self.assertEqual(("数据集", "normal"), (ordinary["objective_label"], ordinary["band"]))

        report = self.ev({"itemtype": "webpage", "title": "未成年人司法保护白皮书",
                          "institution": "最高人民法院"})
        self.assertEqual(("report", "官方报告", "top"),
                         (report["source_type"], report["objective_label"], report["band"]))
        standard_report = self.ev({"itemtype": "report", "title": "研究报告"}, "law")
        self.assertEqual("core", standard_report["band"])
        law = self.ev({"itemtype": "newspaperArticle", "title": "最高人民法院关于审理某案的指导意见"})
        self.assertEqual(("legal_source", "法源", "top"),
                         (law["source_type"], law["objective_label"], law["band"]))

    def test_journal_unique_labels_and_weights(self):
        top3 = self.ev({"itemtype": "journalArticle", "title": "文", "journal": "中国法学"})
        self.assertEqual(("三大刊", "authority"), (top3["objective_label"], top3["band"]))

        clsci = self.ev({"itemtype": "journalArticle", "title": "文", "journal": "中外法学"})
        self.assertEqual(("CLSCI", "top"), (clsci["objective_label"], clsci["band"]))

        cssci_ext = self.ev({"itemtype": "journalArticle", "title": "文", "journal": "上海金融"})
        pku = self.ev({"itemtype": "journalArticle", "title": "文", "journal": "上海城市规划"})
        self.assertEqual(("CSSCI扩展", "core"), (cssci_ext["objective_label"], cssci_ext["band"]))
        self.assertEqual(("北大核心", "core"), (pku["objective_label"], pku["band"]))
        self.assertGreater(cssci_ext["weight"], pku["weight"])

    def test_tssci_and_taiwan_priority(self):
        ordinary = self.ev({"itemtype": "journalArticle", "title": "文", "journal": "东吴法律学报"})
        self.assertEqual(("TSSCI", "authority"), (ordinary["objective_label"], ordinary["band"]))

        highlighted = self.ev({"itemtype": "journalArticle", "title": "文", "journal": "台湾大学法学论丛"})
        self.assertEqual("TSSCI", highlighted["objective_label"])
        self.assertEqual("authority", highlighted["band"])

        personal_only = self.ev({"itemtype": "journalArticle", "title": "文", "journal": "全国律师"})
        self.assertEqual(("台湾法学", "top"),
                         (personal_only["objective_label"], personal_only["band"]))

    def test_personal_factory_mapping_matches_user_preset(self):
        expected = {
            "label:SSCI Q1": "authority", "label:SSCI Q2": "authority",
            "label:SSCI Q3": "top", "label:SSCI Q4": "top",
            "label:CSSCI": "top", "nature:report": "top",
            "label:SJR Q1": "core", "label:SJR Q2": "core",
            "label:SJR Q3": "core", "label:SJR Q4": "core",
            "label:SSCI": "core", "label:TSSCI": "authority",
            "label:精选外文权威": "authority", "label:台湾法学": "top",
        }
        original = self.GS._requested_disc
        try:
            self.GS._requested_disc = lambda: "law_personal"
            overview = self.GS.overview([])
            actual = {x["mapping_id"]: x["band"] for x in overview["mappings"]}
            self.assertEqual(expected, {k: actual[k] for k in expected})
            self.GS._requested_disc = lambda: "law_personal_fun"
            fun = self.GS.overview([])
            fun_actual = {x["mapping_id"]: x["band"] for x in fun["mappings"]}
            self.assertEqual(expected, {k: fun_actual[k] for k in expected})
        finally:
            self.GS._requested_disc = original

    def test_manual_override_changes_only_evaluation(self):
        paper = {"key": "p1", "itemtype": "journalArticle", "title": "文", "journal": "中国法学"}
        auto = self.ev(paper)
        self.assertEqual((auto["band"], auto["band_name"]),
                         (auto["auto_band"], auto["auto_band_name"]))
        self.SR.set_override("p1", "normal")
        manual = self.ev(paper)
        self.assertEqual(auto["objective_label"], manual["objective_label"])
        self.assertEqual("三大刊", manual["objective_label"])
        self.assertEqual("normal", manual["band"])
        self.assertEqual(("authority", "权威"),
                         (manual["auto_band"], manual["auto_band_name"]))
        self.assertTrue(manual["manual"])
        self.assertEqual("manual", manual["src"])
        self.SR.set_override("p1", None)
        restored = self.ev(paper)
        self.assertEqual(("authority", False), (restored["band"], restored["manual"]))

    def test_legacy_override_fold(self):
        expected = {"T1": "authority", "T1b": "top", "T2": "core", "T3": "core",
                    "T4": "normal", "T5": "normal"}
        for old, band in expected.items():
            key = "legacy-" + old
            self.SR.set_override(key, old)
            got = self.ev({"key": key, "itemtype": "book", "title": "书"})
            self.assertEqual(band, got["band"])
            self.assertEqual(old, got["internal_tier"])

    def test_fun_discipline_is_alias_with_display_only_difference(self):
        paper = {"itemtype": "journalArticle", "title": "文", "journal": "中国法学"}
        standard = self.ev(paper, "law_personal")
        fun = self.ev(paper, "law_personal_fun")
        for field in ("source_type", "objective_label", "band", "standard_band_name",
                      "internal_tier", "weight", "rank", "band_rank", "hit_catalogs"):
            self.assertEqual(standard[field], fun[field], field)
        self.assertEqual("权威", standard["band_name"])
        self.assertEqual("夯", fun["band_name"])
        self.assertEqual("law_personal", self.GS.canonical_discipline("law_personal_fun"))
        self.assertIn("law_personal", self.GS._MEMO)
        self.assertNotIn("law_personal_fun", self.GS._MEMO)

    def test_unknown_journal_is_normal_without_pending_label(self):
        got = self.ev({"itemtype": "journalArticle", "title": "文", "journal": "完全不存在的某刊XYZ"})
        self.assertEqual("normal", got["band"])
        self.assertEqual("普通", got["standard_band_name"])
        self.assertNotEqual("待确认", got["band_name"])
        self.assertFalse(got["needs_review"])

    def test_mapping_override_is_per_discipline_and_keeps_objective_label(self):
        paper = {"itemtype": "book", "title": "书"}
        self.GS.set_mapping_override("nature:book", "normal", "law_personal")
        changed = self.ev(paper, "law_personal")
        untouched = self.ev(paper, "law")
        self.assertEqual(("书籍", "normal", "mapping"),
                         (changed["objective_label"], changed["band"], changed["src"]))
        self.assertEqual(("书籍", "authority"),
                         (untouched["objective_label"], untouched["band"]))
        self.GS.set_mapping_override("nature:book", None, "law_personal")
        self.assertEqual("authority", self.ev(paper, "law_personal")["band"])

    def test_default_value_is_not_stored_and_all_defaults_can_be_restored(self):
        # 与出厂值相同的选择不应制造“伪自定义”。娱乐显示名恢复的是共用的 law_personal 配置。
        self.GS.set_mapping_override("label:三大刊", "authority", "law_personal")
        self.assertNotIn("law_personal", self.GS._load_mapping_overrides())

        self.GS.set_mapping_override("label:SSCI Q4", "core", "law_personal")
        self.GS.set_mapping_override("nature:book", "normal", "law")
        result = self.GS.clear_mapping_overrides("law_personal_fun")
        saved = self.GS._load_mapping_overrides()
        self.assertEqual(("law_personal", 1),
                         (result["canonical_discipline"], result["removed"]))
        self.assertNotIn("law_personal", saved)
        self.assertEqual("normal", saved["law"]["nature:book"])
        restored = self.GS._apply_mapping_override({
            "objective_label": "SSCI Q4", "source_type": "journal_article",
            "band": "normal", "band_name": "普通", "standard_band_name": "普通", "weight": 0.25,
        }, "law_personal")
        self.assertEqual("top", restored["band"])

    def test_authority_dataset_has_an_independent_mapping(self):
        authority = {"itemtype": "dataset", "title": "人口数据", "institution": "国家统计局"}
        ordinary = {"itemtype": "dataset", "title": "研究数据"}
        self.GS.set_mapping_override("nature:dataset_authority", "top", "law_personal")
        self.assertEqual("top", self.ev(authority)["band"])
        self.assertEqual("normal", self.ev(ordinary)["band"])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""检索金标资产与指标口径回归测试；纯离线、零 API 调用。"""
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))

import retrieval_eval as E  # noqa: E402
import retrieval_calibrate as C  # noqa: E402


class RetrievalGoldsetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = E.load_goldset()

    def test_goldset_is_complete_and_internally_consistent(self):
        self.assertEqual(E.validate_goldset(self.rows), [])
        self.assertEqual(len(self.rows), 38)
        self.assertEqual(sum(row["answerable"] for row in self.rows), 30)
        self.assertEqual(sum(row["split"] == "validation" for row in self.rows), 13)

    def test_full_baseline_reproduces_application_evaluation(self):
        relevance = E.ranking_metrics(self.rows, "relevance", "all")
        blend = E.ranking_metrics(self.rows, "blend", "all")
        self.assertAlmostEqual(relevance["hit_at_3"], 0.9666666667)
        self.assertAlmostEqual(relevance["recall_at_8"], 1.0)
        self.assertAlmostEqual(relevance["ndcg_at_8"], 0.9129720767)
        self.assertAlmostEqual(relevance["unique_at_8"], 0.7208333333)
        self.assertAlmostEqual(blend["hit_at_3"], 1.0)
        self.assertAlmostEqual(blend["recall_at_8"], 0.95)
        self.assertAlmostEqual(blend["ndcg_at_8"], 0.8805415935)
        self.assertAlmostEqual(blend["unique_at_8"], 0.7208333333)

    def test_default_report_hides_validation_split(self):
        result = E.report(self.rows)
        self.assertEqual(result["split"], "calibration")
        self.assertEqual(result["ranking"]["relevance"]["queries"], 18)
        self.assertNotIn("C02", result["ambiguous_queries"])
        self.assertEqual(result["ambiguous_queries"], ["C03"])

    def test_off_topic_scores_and_blend_demotions_are_preserved(self):
        off_topic = E.off_topic_metrics(self.rows, "relevance", "all")
        self.assertAlmostEqual(off_topic["max_top_score"], 0.2951)
        shifts = E.blend_shifts(self.rows, "all")
        worst = shifts["largest_demotions"][:4]
        self.assertEqual([(x["query_id"], x["key"], x["delta"]) for x in worst], [
            ("D07", "UZRWXHI6", 6),
            ("C01", "UZRWXHI6", 6),
            ("C05", "WPB65UH2", 5),
            ("D04", "2L7CH5C4", 4),
        ])

    def test_api_weight_calibration_uses_only_calibration_and_selects_030(self):
        candidates = C.load_candidates()
        self.assertEqual(C.validate_candidates(self.rows, candidates), [])
        chosen = C.choose_scale(self.rows, candidates)["chosen"]
        self.assertEqual(chosen["scale"], 0.3)
        self.assertEqual(chosen["hit_at_3"], 1.0)
        self.assertEqual(chosen["recall_at_8"], 1.0)

    def test_locked_030_beats_old_050_on_blind_validation(self):
        candidates = C.load_candidates()
        chosen = C.ranking_metrics(self.rows, candidates, "validation", 0.3)
        old = C.ranking_metrics(self.rows, candidates, "validation", 0.5)
        self.assertEqual(chosen["hit_at_3"], old["hit_at_3"])
        self.assertEqual(chosen["recall_at_8"], old["recall_at_8"])
        self.assertGreater(chosen["ndcg_at_8"], old["ndcg_at_8"])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""检索摘要质量闸门回归测试：不调用 LLM、不接触真实摘要库。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))

import embed_index as E  # noqa: E402
import sac as SAC  # noqa: E402
import server  # noqa: E402
import settings as S  # noqa: E402


GOOD = ("本文研究程序正义如何影响当事人对裁判的接受，采用案例比较与访谈材料，"
        "发现参与机会、被尊重感和理由说明共同提升制度信任，并讨论了不同程序场景下的适用边界。")


class SacQualityTests(unittest.TestCase):
    def test_gate_accepts_concise_useful_summary_and_rejects_known_corruption_shapes(self):
        self.assertTrue(SAC.validate_summary(GOOD)[0])
        bad = [
            "?" * 180,
            "on " * 300,
            "该文仅有标题与期刊信息，无摘要和正文，因此无法概括研究方法与结论。" * 2,
            GOOD * 10,
            "太短",
        ]
        for text in bad:
            with self.subTest(text=text[:20]):
                self.assertFalse(SAC.validate_summary(text)[0])

    def test_agent_batch_is_atomic_when_one_summary_is_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "summaries.json"
            with mock.patch.object(SAC, "SUM_FILE", target):
                result = SAC.write_summaries([
                    {"key": "GOOD", "summary": GOOD},
                    {"key": "BAD", "summary": "?" * 180},
                ])
                self.assertEqual(result["written"], 0)
                self.assertEqual(result["errors"][0]["key"], "BAD")
                self.assertFalse(target.exists())

    def test_deep_agent_does_not_embed_a_rejected_summary_batch(self):
        q = server.DeepAgentQ(summaries=[
            server.DeepAgentSummary(key="BAD", summary="?" * 180)
        ])
        with mock.patch.object(server, "_run_stage_blocking") as run:
            result = server._deep_agent_run(q)
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "summary_validation")
        self.assertEqual(result["summary_errors"][0]["key"], "BAD")
        run.assert_not_called()

    def test_only_valid_summaries_are_counted_and_loaded_for_embedding(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "summaries.json"
            target.write_text(json.dumps({"GOOD": GOOD, "BAD": "on " * 300}, ensure_ascii=False),
                              encoding="utf-8")
            with mock.patch.object(SAC, "SUM_FILE", target):
                self.assertEqual(SAC.summary_keys(), {"GOOD"})
                self.assertIn("BAD", SAC.summary_issues())
                self.assertEqual(set(E.load_summaries()), {"GOOD"})

    def test_summary_snapshot_can_restore_replaced_and_new_items(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "summaries.json"
            target.write_text(json.dumps({"OLD": GOOD}, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(SAC, "SUM_FILE", target):
                snap = SAC.snapshot(["OLD", "NEW"])
                SAC.write_summaries([{"key": "OLD", "summary": GOOD + "补充"},
                                     {"key": "NEW", "summary": GOOD}])
                SAC.restore(snap)
                restored = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(restored, {"OLD": GOOD})

    def test_agent_maintenance_repair_writes_and_verifies_selected_summary(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "summaries.json"
            q = server.AgentSummaryRepairQ(summaries=[server.AgentSummaryItem(key="KEY", summary=GOOD)])
            with mock.patch.object(SAC, "SUM_FILE", target), \
                    mock.patch.object(S, "sac_conf", return_value={"generator": "agent"}), \
                    mock.patch.object(server, "_run_stage_blocking", return_value=0) as run_stage, \
                    mock.patch.object(server, "_unmark_deep"), \
                    mock.patch.object(server, "_deep_keys", return_value={"KEY"}), \
                    mock.patch.object(server.R, "load_all"), \
                    mock.patch.object(server, "_wiki_suggest_async"), \
                    mock.patch.object(server, "_drain_deep_queue"):
                result = server.maintenance_agent_summaries(q)
            self.assertTrue(result["ok"])
            self.assertEqual(result["written"], 1)
            self.assertIn("KEY", json.loads(target.read_text(encoding="utf-8")))
            run_stage.assert_called_once_with("deep_embed", ["--only-stem", "KEY"])


if __name__ == "__main__":
    unittest.main()

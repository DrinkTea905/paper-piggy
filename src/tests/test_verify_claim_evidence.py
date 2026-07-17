# -*- coding: utf-8 -*-
"""Agent 两阶段取证与证据清洁回归测试。"""
import sys
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))

import retriever as R  # noqa: E402
import verify_claim as V  # noqa: E402


class VerifyClaimEvidenceTests(unittest.TestCase):
    def test_directed_verification_can_take_more_passages_and_deduplicates_evidence(self):
        claim = "程序参与机会能够提升当事人对裁判结果的接受程度"
        hit = {"key": "K", "title": "程序正义", "page": 3,
               "context": claim + "，并通过访谈材料得到验证。", "score": 5.0}
        with (mock.patch.dict(R.STATE, {"ready": True, "mode": "full"}),
              mock.patch.object(R, "search", return_value=[hit, dict(hit)]) as search,
              mock.patch("settings.is_api", return_value=False),
              mock.patch("page_map.printed", return_value={"display": "12"}),
              mock.patch.object(V.TL, "locate", return_value={"matches": []})):
            result = V.verify(claim, keys=["K"], topk=8)
        self.assertEqual(result["verdict"], "supported")
        self.assertEqual(len(result["evidence"]), 1)
        search.assert_called_once_with(claim, 8, "relevance", keys={"K"}, max_per_key=8)

    def test_empty_quotes_are_not_returned_as_evidence(self):
        hit = {"key": "K", "title": "无关文献", "page": 1,
               "context": "这是一段完全不同主题的正文内容，讨论天气与农业。", "score": 3.0}
        with (mock.patch.dict(R.STATE, {"ready": True, "mode": "full"}),
              mock.patch.object(R, "search", return_value=[hit]) as search,
              mock.patch("settings.is_api", return_value=False)):
            result = V.verify("算法透明度能够显著降低量刑差异", topk=6)
        self.assertEqual(result["verdict"], "not_in_lib")
        self.assertEqual(result["evidence"], [])
        search.assert_called_once_with("算法透明度能够显著降低量刑差异", 6, "relevance",
                                      keys=None, max_per_key=None)


if __name__ == "__main__":
    unittest.main()

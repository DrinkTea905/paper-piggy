# -*- coding: utf-8 -*-
"""MCP 深索状态措辞回归测试。"""
import sys
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))

import mcp_server as MCP  # noqa: E402


class _Response:
    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


class McpDeepStatusTests(unittest.TestCase):
    def test_empty_queue_is_idle_and_summary_anomalies_are_visible(self):
        body = {"pending": 0, "in_flight": 0, "paused": False, "building": False,
                "deep_done": 10, "with_pdf": 12,
                "sac_done": 7, "sac_invalid": 2, "sac_missing": 1,
                "invalid_pdf": 1, "missing_pdf": 0, "ocr_failed": 0, "ocr_pending": 0,
                "eta_seconds": None, "items": []}
        with (mock.patch.object(MCP, "ensure_up", return_value=True),
              mock.patch.object(MCP.requests, "get", return_value=_Response(body))):
            text = MCP.do_tool("deep_status", {})
        self.assertIn("空闲（队列已清空）", text)
        self.assertNotIn("运行中", text)
        self.assertIn("异常 2", text)
        self.assertIn("PDF损坏 1", text)


if __name__ == "__main__":
    unittest.main()

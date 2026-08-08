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
    def __init__(self, body, status_code=200):
        self.body = body
        self.status_code = status_code

    def json(self):
        return self.body


class McpDeepStatusTests(unittest.TestCase):
    def test_empty_queue_is_idle_and_summary_anomalies_are_visible(self):
        body = {"pending": 0, "in_flight": 0, "paused": False, "building": False,
                "deep_done": 10, "with_pdf": 12,
                "sac_done": 7, "sac_invalid": 2, "sac_missing": 1,
                "sac_missing_statutes": 1,
                "invalid_pdf": 1, "missing_pdf": 0, "ocr_failed": 0, "ocr_pending": 0,
                "eta_seconds": None, "items": []}
        with (mock.patch.object(MCP, "ensure_up", return_value=True),
              mock.patch.object(MCP.requests, "get", return_value=_Response(body))):
            text = MCP.do_tool("deep_status", {})
        self.assertIn("空闲（队列已清空）", text)
        self.assertNotIn("运行中", text)
        self.assertIn("异常 2", text)
        self.assertIn("PDF损坏 1", text)
        self.assertIn("摘要缺失中有法规 1 篇", text)
        self.assertIn("submit_agent_summaries", text)

    def test_add_statute_summary_is_optional_forwarded_and_missing_hint_is_visible(self):
        tool = next(x for x in MCP.TOOLS if x["name"] == "add_statute")
        self.assertIn("summary", tool["inputSchema"]["properties"])
        self.assertNotIn("summary", tool["inputSchema"]["required"])
        sent = {}

        def post(url, json, timeout):
            sent.update(json)
            return _Response({
                "ok": True, "status": "added", "key": "STAT-TEST",
                "validity_status": "现行有效", "building": True,
                "summary_hint": "本篇尚无检索摘要，请调用 submit_agent_summaries 补写，否则会被统计为缺失。",
            })

        args = {"title": "测试法", "issuing_authority": "测试机关",
                "source_url": "https://www.gov.cn/test", "body_markdown": "正文",
                "summary": "检索摘要"}
        with mock.patch.object(MCP, "ensure_up", return_value=True), \
                mock.patch.object(MCP.requests, "post", side_effect=post):
            text = MCP.do_tool("add_statute", args)
        self.assertEqual("检索摘要", sent["summary"])
        self.assertIn("submit_agent_summaries", text)

    def test_maintenance_and_workflow_tools_are_exposed(self):
        names = {x["name"] for x in MCP.TOOLS}
        self.assertTrue({"list_workflows", "read_workflow", "maintenance_audit",
                         "get_template_upgrade_diff", "merge_template_upgrade",
                         "submit_agent_summaries", "resolve_wiki_suggestion"} <= names)
        self.assertIn("用户只要提到“维护”", MCP.instructions())

    def test_source_meta_names_bibliographic_and_retrieval_summaries_separately(self):
        body = {
            "key": "KEY", "title": "标题", "abstract": "题录里的摘要",
            "bibliographic_abstract": "题录里的摘要", "retrieval_summary": "给检索用的摘要",
            "retrieval_summary_valid": True, "cited_by_wiki": [],
        }
        with mock.patch.object(MCP, "ensure_up", return_value=True), \
                mock.patch.object(MCP.requests, "get", return_value=_Response(body)):
            text, structured = MCP.do_tool("get_source_meta", {"key": "KEY"})
        self.assertIn("题录摘要（来自 Zotero / 文献元数据）", text)
        self.assertIn("检索摘要（SAC，用于语义检索）", text)
        self.assertEqual(structured["retrieval_summary"], "给检索用的摘要")


if __name__ == "__main__":
    unittest.main()

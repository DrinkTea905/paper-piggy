# -*- coding: utf-8 -*-
import inspect
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mcp_server as MCP
import agent_ws as AW


class _Response:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data


def _run_rpc(requests, tool_result=None):
    stdin = io.StringIO("\n".join(json.dumps(x, ensure_ascii=False) for x in requests) + "\n")
    stdout = io.StringIO()
    patches = [
        mock.patch.object(sys, "stdin", stdin),
        mock.patch.object(sys, "stdout", stdout),
        mock.patch.object(MCP.threading, "Thread"),
    ]
    if tool_result is not None:
        patches.append(mock.patch.object(MCP, "do_tool", return_value=tool_result))
    with patches[0], patches[1], patches[2]:
        if len(patches) == 4:
            with patches[3]:
                MCP.main()
        else:
            MCP.main()
    return [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]


class McpContractTests(unittest.TestCase):
    def test_list_workflows_keeps_builtin_order_and_separates_custom_files(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(AW, "base_dir", return_value=Path(td)):
            AW.ensure_scaffold()
            custom = AW.skills_dir() / "我的自定义流程.md"
            custom.write_text("# 自定义\n", encoding="utf-8")

            text = MCP.do_tool("list_workflows", {})

        names = [
            "论文初稿（少年司法版）", "综述（少年司法版）",
            "论文初稿（通用暂用版）", "综述", "维护综述库", "跨学科发散与补文献",
        ]
        positions = [text.index(f"- {name}：") for name in names]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("内置工作流（固定顺序）", text)
        self.assertIn("自定义或迁移保留文件", text)
        self.assertGreater(text.index("- 我的自定义流程："), positions[-1])

    def test_every_tool_has_new_contract_and_dispatch(self):
        names = [tool["name"] for tool in MCP.TOOLS]
        self.assertEqual(40, len(names))
        self.assertEqual(len(names), len(set(names)))
        dispatch = inspect.getsource(MCP.do_tool)
        for tool in MCP.TOOLS:
            self.assertTrue(tool.get("title"), tool["name"])
            self.assertEqual("object", tool["inputSchema"].get("type"), tool["name"])
            annotations = tool.get("annotations") or {}
            for key in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"):
                self.assertIsInstance(annotations.get(key), bool, (tool["name"], key))
            self.assertIn(f'if name == "{tool["name"]}"', dispatch, tool["name"])

        self.assertEqual(
            set(MCP._OUTPUT_SCHEMAS),
            {tool["name"] for tool in MCP.TOOLS if "outputSchema" in tool},
        )

    def test_legacy_protocol_gets_legacy_tools_and_content_only(self):
        output = _run_rpc([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": MCP.LEGACY_PROTO}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "search_localkb", "arguments": {"query": "测试"}}},
        ], tool_result=("可见文本", {"query": "测试", "results": []}))
        self.assertEqual(MCP.LEGACY_PROTO, output[0]["result"]["protocolVersion"])
        first_tool = output[1]["result"]["tools"][0]
        self.assertEqual({"name", "description", "inputSchema"}, set(first_tool))
        self.assertEqual("可见文本", output[2]["result"]["content"][0]["text"])
        self.assertNotIn("structuredContent", output[2]["result"])

    def test_intermediate_protocol_is_accepted_with_conservative_contract(self):
        output = _run_rpc([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": MCP.INTERMEDIATE_PROTO}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "search_localkb", "arguments": {"query": "测试"}}},
        ], tool_result=("可见文本", {"query": "测试", "results": []}))
        self.assertEqual(MCP.INTERMEDIATE_PROTO, output[0]["result"]["protocolVersion"])
        self.assertEqual(
            {"name", "description", "inputSchema"},
            set(output[1]["result"]["tools"][0]))
        self.assertNotIn("structuredContent", output[2]["result"])

    def test_new_protocol_gets_new_contract_and_structured_results(self):
        output = _run_rpc([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": MCP.PROTO}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "search_localkb", "arguments": {"query": "测试"}}},
        ], tool_result=("可见文本", {"query": "测试", "results": []}))
        self.assertEqual(MCP.PROTO, output[0]["result"]["protocolVersion"])
        first_tool = output[1]["result"]["tools"][0]
        self.assertIn("title", first_tool)
        self.assertIn("annotations", first_tool)
        self.assertIn("outputSchema", first_tool)
        self.assertEqual({"query": "测试", "results": []},
                         output[2]["result"]["structuredContent"])

    def test_unknown_tool_is_invalid_params_error(self):
        output = _run_rpc([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": MCP.PROTO}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "not_a_tool", "arguments": {}}},
        ])
        self.assertEqual(-32602, output[1]["error"]["code"])

    def test_missing_required_argument_is_invalid_params_error(self):
        output = _run_rpc([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": MCP.PROTO}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "read_source", "arguments": {}}},
        ])
        self.assertEqual(-32602, output[1]["error"]["code"])
        self.assertIn("key", output[1]["error"]["message"])

    def test_wrong_argument_type_and_enum_are_rejected_before_dispatch(self):
        output = _run_rpc([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": MCP.PROTO}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "search_localkb",
                        "arguments": {"query": ["不是字符串"], "sort": "unknown"}}},
        ])
        self.assertEqual(-32602, output[1]["error"]["code"])
        self.assertIn("query", output[1]["error"]["message"])

    def test_new_protocol_structured_tool_has_schema_shaped_empty_result(self):
        output = _run_rpc([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": MCP.PROTO}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "list_wiki", "arguments": {"offset": 20}}},
        ], tool_result="没有符合条件的页面")
        self.assertEqual(
            {"total": 0, "offset": 20, "pages": []},
            output[1]["result"]["structuredContent"])

    def test_read_source_rpc_never_emits_competing_structured_content(self):
        output = _run_rpc([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": MCP.PROTO}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "read_source", "arguments": {"key": "ABC"}}},
        ], tool_result="正文必须显示")
        self.assertEqual("正文必须显示", output[1]["result"]["content"][0]["text"])
        self.assertNotIn("structuredContent", output[1]["result"])

    def test_unknown_requested_protocol_negotiates_latest_supported(self):
        output = _run_rpc([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2099-01-01"}},
        ])
        self.assertEqual(MCP.PROTO, output[0]["result"]["protocolVersion"])

    @mock.patch.object(MCP, "ensure_up", return_value=True)
    @mock.patch.object(MCP.requests, "get")
    def test_read_source_body_is_standard_text_not_structured_tuple(self, get, _ensure):
        get.return_value = _Response({
            "ok": True, "title": "测试论文", "author": "甲", "year": "2026",
            "journal": "测试刊", "fulltext_format": "pdf", "n_pages_total": 1,
            "returned_pages": 1, "chars": 8, "truncated": False,
            "pages": [{"pdf_page": 1, "position": 1, "printed_page": "10",
                       "locator": "PDF 第 1 页", "text": "这是必须可见的正文。"}],
        })
        result = MCP.do_tool("read_source", {"key": "ABC", "from_page": 1, "to_page": 1})
        self.assertIsInstance(result, str)
        self.assertIn("这是必须可见的正文", result)
        self.assertIn("印刷页 10", result)

    @mock.patch.object(MCP, "ensure_up", return_value=True)
    @mock.patch.object(MCP.requests, "post")
    def test_set_wiki_theme_only_calls_metadata_endpoint(self, post, _ensure):
        post.return_value = _Response({
            "ok": True, "id": "concept-a",
            "theme": {"name": "少年司法", "source": "manual"},
        })
        text = MCP.do_tool("set_wiki_theme",
                           {"page_id": "concept-a", "theme": "少年司法"})
        post.assert_called_once_with(
            MCP.URL + "/wiki/page/concept-a/theme",
            json={"name": "少年司法"}, timeout=30)
        self.assertIn("正文、引用和人工核验状态均未改动", text)


if __name__ == "__main__":
    unittest.main()

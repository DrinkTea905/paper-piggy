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


def _run_rpc(requests, tool_result=None, read_memory=True):
    wire_requests = []
    for request in requests:
        wire_requests.append(request)
        if read_memory and request.get("method") == "initialize":
            wire_requests.append({
                "jsonrpc": "2.0", "id": "__memory_gate__", "method": "tools/call",
                "params": {"name": "read_project_memory", "arguments": {}},
            })
    stdin = io.StringIO("\n".join(json.dumps(x, ensure_ascii=False) for x in wire_requests) + "\n")
    stdout = io.StringIO()
    original_do_tool = MCP.do_tool

    def dispatch(name, args):
        if name == "read_project_memory" and read_memory:
            return "项目记忆已完整读取"
        if tool_result is not None:
            return tool_result
        return original_do_tool(name, args)

    patches = [
        mock.patch.object(sys, "stdin", stdin),
        mock.patch.object(sys, "stdout", stdout),
        mock.patch.object(MCP.threading, "Thread"),
        mock.patch.object(MCP, "do_tool", side_effect=dispatch),
    ]
    with patches[0], patches[1], patches[2], patches[3]:
        MCP.main()
    return [item for item in
            (json.loads(line) for line in stdout.getvalue().splitlines() if line.strip())
            if item.get("id") != "__memory_gate__"]


class McpContractTests(unittest.TestCase):
    def test_memory_gate_blocks_other_tools_until_full_project_memory_read(self):
        output = _run_rpc([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": MCP.PROTO}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "search_localkb", "arguments": {"query": "测试"}}},
        ], tool_result="不应执行", read_memory=False)
        blocked = output[1]["result"]
        self.assertTrue(blocked["isError"])
        self.assertIn("必须先调用 read_project_memory", blocked["content"][0]["text"])

    def test_read_project_memory_unlocks_following_tools(self):
        output = _run_rpc([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": MCP.PROTO}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "read_project_memory", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "search_localkb", "arguments": {"query": "测试"}}},
        ], tool_result="已执行", read_memory=False)
        self.assertNotIn("isError", output[1]["result"])
        self.assertEqual("已执行", output[2]["result"]["content"][0]["text"])

    def test_failed_project_memory_read_keeps_gate_locked(self):
        calls = []

        def result(name, _args):
            calls.append(name)
            return "读项目记忆失败：模拟错误" if name == "read_project_memory" else "不应执行"

        with mock.patch.object(MCP, "do_tool", side_effect=result):
            output = _run_rpc([
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": MCP.PROTO}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": {"name": "read_project_memory", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "search_localkb", "arguments": {"query": "测试"}}},
            ], read_memory=False)
        self.assertEqual(["read_project_memory"], calls)
        self.assertTrue(output[2]["result"]["isError"])

    def test_memory_resource_read_also_unlocks_tools(self):
        with mock.patch.object(MCP, "read_resource", return_value=("完整项目记忆", "text/markdown")):
            output = _run_rpc([
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": MCP.PROTO}},
                {"jsonrpc": "2.0", "id": 2, "method": "resources/read",
                 "params": {"uri": "localkb://memory"}},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "search_localkb", "arguments": {"query": "测试"}}},
            ], tool_result="已执行", read_memory=False)
        self.assertEqual("完整项目记忆", output[1]["result"]["contents"][0]["text"])
        self.assertEqual("已执行", output[2]["result"]["content"][0]["text"])

    def test_initialize_declares_shared_memory_mirroring_contract(self):
        body = MCP.instructions()
        self.assertIn("开始任何任务前，必须先调用 read_project_memory", body)
        self.assertIn("服务器会拒绝其他工具调用", body)
        self.assertIn("auto memory", body)
        self.assertIn("同一实质内容同步到项目记忆", body)

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

    def test_list_workflows_keeps_companion_handbook_out_of_top_level(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(AW, "base_dir", return_value=Path(td)):
            AW.ensure_scaffold()
            recovery = AW.skills_dir() / (
                "论文初稿（少年司法版）.user-backup-auto-upgrade-test.md")
            recovery.write_text("# 自动升级恢复备份\n", encoding="utf-8")
            migration_dir = AW.skills_dir() / (
                ".agent-ws-migration-backup-test")
            migration_dir.mkdir()
            (migration_dir / "写论文与综述.md").write_text(
                "# 迁移恢复件\n", encoding="utf-8")
            text = MCP.do_tool("list_workflows", {})

        self.assertEqual(6, sum(f"- {name}：" in text for name in (
            "论文初稿（少年司法版）", "综述（少年司法版）",
            "论文初稿（通用暂用版）", "综述",
            "维护综述库", "跨学科发散与补文献",
        )))
        self.assertNotIn("成文技艺手册", text)
        self.assertNotIn("user-backup-auto-upgrade", text)
        self.assertNotIn("agent-ws-migration-backup", text)

    def test_read_juvenile_draft_automatically_includes_actual_handbook(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(AW, "base_dir", return_value=Path(td)):
            AW.ensure_scaffold()
            text = MCP.do_tool("read_workflow", {"name": "论文初稿（少年司法版）"})

        self.assertIn("工作流文件：", text)
        self.assertIn(AW._WF_JJ_DRAFT, text)
        self.assertIn("自动附带参考手册：", text)
        self.assertIn(AW._JJ_DRAFT_CRAFT_HANDBOOK, text)
        self.assertIn("不是独立工作流", text)
        # DOCX 交付仍是硬要求（v3 措辞：工作流卷首 + 「交付与沉淀」节 + 手册自检表）
        self.assertIn("最终成稿必须是可打开的 `.docx`", text)
        self.assertIn("最终论文成稿固定写为 `0_Agent交付物/<主题>/<题目>.docx`", text)
        self.assertIn("不能替代最终成稿", text)
        self.assertIn("DOCX 打开检查", text)

    def test_read_juvenile_draft_preserves_user_main_and_handbook(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(AW, "base_dir", return_value=Path(td)):
            AW.ensure_scaffold()
            main = AW.skills_dir() / "论文初稿（少年司法版）.md"
            handbook = AW.handbooks_dir() / "论文初稿（少年司法版）—成文技艺手册.md"
            main.write_text("# 用户主流程\n保留我的第一闸门。\n", encoding="utf-8")
            handbook.write_text("# 用户手册\n保留我的段落规则。\n", encoding="utf-8")

            text = MCP.do_tool("read_workflow", {"name": "论文初稿（少年司法版）"})

        self.assertIn("保留我的第一闸门", text)
        self.assertIn("保留我的段落规则", text)
        self.assertNotIn(AW._WF_JJ_DRAFT, text)
        self.assertNotIn(AW._JJ_DRAFT_CRAFT_HANDBOOK, text)

    def test_read_workflow_ignores_handbook_sidecar_and_other_workflows_have_no_companion(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(AW, "base_dir", return_value=Path(td)):
            AW.ensure_scaffold()
            handbook = AW.handbooks_dir() / "论文初稿（少年司法版）—成文技艺手册.md"
            sidecar = handbook.with_name(handbook.stem + ".new" + handbook.suffix)
            sidecar.write_text("# 尚未合并的旁本\n不得自动使用。\n", encoding="utf-8")

            juvenile = MCP.do_tool("read_workflow", {"name": "论文初稿（少年司法版）"})
            review = MCP.do_tool("read_workflow", {"name": "综述（少年司法版）"})

        self.assertNotIn("不得自动使用", juvenile)
        self.assertIn(AW._JJ_DRAFT_CRAFT_HANDBOOK, juvenile)
        self.assertNotIn("自动附带参考手册", review)
        self.assertIn(AW._WF_JJ_REVIEW, review)

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

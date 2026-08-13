# -*- coding: utf-8 -*-
"""记忆分区（项目记忆 / 工具经验 / 主题档案 / 变更日志）+ 检索排除综合层。

这些断言对应 2026-08-13 需求的六条验收标准，外加「硬约束」与「一主题一档案」两组。
为什么值得写死：分区的价值全在**读不到**——只要哪天 core 视图顺手把主题正文塞回去，
功能表面照常工作，只有污染会悄悄回来。这里的 test_core_view_excludes_topic_body 就是
那道防线。
"""
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


def _rpc(requests, tool_result=None):
    """跑一轮 stdio 协议，返回响应列表（照抄 test_mcp_contract 的做法，但不自动开闸）。"""
    stdin = io.StringIO("\n".join(json.dumps(x, ensure_ascii=False) for x in requests) + "\n")
    stdout = io.StringIO()
    original = MCP.do_tool

    def dispatch(name, args):
        if tool_result is not None and name != "read_project_memory":
            return tool_result
        return original(name, args)

    with mock.patch.object(sys, "stdin", stdin), \
         mock.patch.object(sys, "stdout", stdout), \
         mock.patch.object(MCP.threading, "Thread"), \
         mock.patch.object(MCP, "do_tool", side_effect=dispatch):
        MCP.main()
    return [json.loads(l) for l in stdout.getvalue().splitlines() if l.strip()]


def _text(resp):
    return resp["result"]["content"][0]["text"]


class MemoryPartitionTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.base = Path(self._td.name)
        patcher = mock.patch.object(AW, "base_dir", return_value=self.base)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._td.cleanup)
        AW.ensure_scaffold()

    # ── 落点与脚手架 ────────────────────────────────────────────
    def test_scaffold_creates_all_four_memory_partitions(self):
        self.assertTrue(AW.memory_file().exists(), "项目记忆.md")
        self.assertTrue(AW.tools_file().exists(), "工具经验.md")
        self.assertTrue(AW.changelog_file().exists(), "变更日志.md")
        self.assertTrue(AW.topics_dir().is_dir(), "主题档案/ 目录")

    def test_paths_info_exposes_every_partition(self):
        """别让 mcp_server 自己拼路径——落点 SSOT 只有 agent_ws 一处（CLAUDE.md §5）。"""
        p = AW.paths_info()
        self.assertEqual(str(AW.memory_file()), p["memory_file"])
        self.assertEqual(str(AW.tools_file()), p["memory_tools_file"])
        self.assertEqual(str(AW.topics_dir()), p["memory_topics_dir"])
        self.assertEqual(str(AW.changelog_file()), p["memory_changelog_file"])

    # ── 验收 4：四个 target 各写一条，落点正确、不覆盖 ──────────
    def test_append_routes_to_four_targets_without_overwriting(self):
        before = {k: AW.read_memory_section(k)[1] for k in ("core", "tools", "log")}
        self.assertTrue(AW.append_memory("core", "偏好脚注引注。")[0])
        self.assertTrue(AW.append_memory("tools", "LibreOffice 首次转 PDF 慢属正常。")[0])
        self.assertTrue(AW.append_memory("log", "今天建了分流转处主题。")[0])
        self.assertTrue(AW.append_memory("topic:分流转处", "已定五章结构。")[0])

        self.assertIn("偏好脚注引注。", AW.read_memory_section("core")[1])
        self.assertIn("LibreOffice", AW.read_memory_section("tools")[1])
        self.assertIn("今天建了分流转处主题。", AW.read_memory_section("log")[1])
        found, _disp, body = AW.read_memory_topic("分流转处")
        self.assertTrue(found)
        self.assertIn("已定五章结构。", body)
        # 纯追加：原有出厂正文一个字都不能少
        for k, old in before.items():
            self.assertIn(old.strip()[:80], AW.read_memory_section(k)[1])
        # 不串区
        self.assertNotIn("LibreOffice", AW.read_memory_section("core")[1])
        self.assertNotIn("已定五章结构。", AW.read_memory_section("core")[1])

    # ── 硬约束（写入时拒绝，不是劝告）────────────────────────────
    def test_core_rejects_oversized_entry_and_points_at_the_right_partition(self):
        ok, msg = AW.append_memory("core", "x" * (AW.MEMORY_CORE_MAX_ENTRY + 1))
        self.assertFalse(ok)
        self.assertIn("topic:", msg)          # 必须告诉他该写哪儿，否则拒绝等于添堵

    def test_core_rejects_when_line_budget_is_full(self):
        AW.memory_file().write_text("\n".join(f"行{i}" for i in range(AW.MEMORY_CORE_MAX_LINES + 5)),
                                    encoding="utf-8")
        n, full, _warn = AW.check_core_budget()
        self.assertTrue(full, n)
        ok, msg = AW.append_memory("core", "再来一条")
        self.assertFalse(ok)
        self.assertIn(str(AW.MEMORY_CORE_MAX_LINES), msg)

    def test_unknown_target_is_rejected(self):
        ok, msg = AW.append_memory("随便写哪", "x")
        self.assertFalse(ok)
        self.assertIn("core / tools / log / topic:<主题名>", msg)

    def test_topic_status_must_be_one_of_three(self):
        AW.append_memory("topic:甲主题", "内容")
        self.assertFalse(AW.set_topic_status("甲主题", "差不多了")[0])
        ok, _ = AW.set_topic_status("甲主题", "已作废", "以用户新文件为准重写")
        self.assertTrue(ok)
        self.assertIn("已作废", AW.list_memory_topics()[0]["status"])

    def test_new_topic_archive_always_carries_a_status_line(self):
        """状态行由产品写入，agent 无从跳过——结构保证，不是模板提醒。"""
        AW.append_memory("topic:乙主题", "内容")
        _f, _d, body = AW.read_memory_topic("乙主题")
        self.assertEqual("进行中", AW.parse_topic_status(body))

    def test_missing_status_line_reads_as_unlabelled_not_in_progress(self):
        """照 定时任务 的经验：缺省不得默认成一个积极状态，否则没写全的档案会伪装成正在推进。"""
        self.assertEqual("未标注", AW.parse_topic_status("# 无状态行\n\n正文"))

    # ── 一个研究主题一个档案 ────────────────────────────────────
    def test_similar_topic_name_is_refused_and_points_back_to_the_existing_archive(self):
        AW.append_memory("topic:少年司法分流转处", "第一条")
        ok, msg = AW.append_memory("topic:少年司法分流", "另起炉灶")
        self.assertFalse(ok)
        self.assertIn("少年司法分流转处", msg)
        self.assertEqual(1, len(AW.list_memory_topics()))

    def test_same_topic_written_with_different_spacing_lands_in_one_file(self):
        AW.append_memory("topic:少年司法分流转处", "第一条")
        ok, _ = AW.append_memory("topic:少年司法 分流转处", "第二条")
        self.assertTrue(ok)
        self.assertEqual(1, len(AW.list_memory_topics()))
        _f, _d, body = AW.read_memory_topic("少年司法分流转处")
        self.assertIn("第一条", body)
        self.assertIn("第二条", body)

    def test_genuinely_different_topic_is_allowed(self):
        AW.append_memory("topic:少年司法分流转处", "甲")
        self.assertTrue(AW.append_memory("topic:企业合规不起诉", "乙")[0])
        self.assertEqual(2, len(AW.list_memory_topics()))

    def test_topic_filename_survives_windows_traps(self):
        self.assertEqual("带_斜杠", AW.topic_filename("带/斜杠"))
        self.assertEqual("_CON", AW.topic_filename("CON"))        # 保留设备名
        self.assertEqual("尾点", AW.topic_filename("尾点."))       # Windows 会静默剥尾点
        self.assertEqual("未命名主题", AW.topic_filename("   "))
        self.assertLessEqual(len(AW.topic_filename("长" * 400)), 121)

    # ── 验收 1 & 3：core 视图的内容与体积 ───────────────────────
    def test_core_view_excludes_topic_body_but_lists_them(self):
        AW.append_memory("topic:分流转处", "秘密结论：本主题认定 A 优于 B。")
        AW.set_topic_status("分流转处", "已作废", "以用户新文件为准重写")
        head, body = MCP._memory_view_text(AW, full=False)
        self.assertIn("分流转处", body)                       # 清单里有
        self.assertIn("已作废", body)                          # 状态可见
        self.assertNotIn("秘密结论", body)                     # ★ 正文绝不能进 core
        self.assertIn("core", head)

    def test_core_view_stays_under_the_budget(self):
        for i in range(30):
            AW.append_memory(f"topic:主题{i:02d}", "正文" * 3000)
        out = MCP.do_tool("read_project_memory", {})
        self.assertLess(len(out), MCP.MEMORY_VIEW_MAX_CHARS)
        self.assertNotIn("正文正文", out)

    def test_all_scope_does_include_topic_bodies(self):
        AW.append_memory("topic:分流转处", "秘密结论：本主题认定 A 优于 B。")
        _head, body = MCP._memory_view_text(AW, full=True)
        self.assertIn("秘密结论", body)

    # ── 验收 1（续）：分段 ──────────────────────────────────────
    def test_long_view_is_paged_with_explicit_remaining_count(self):
        body = "\n".join(f"第{i}行内容内容内容内容内容" for i in range(4000))
        first = MCP._memory_paged("抬头", body, "core", 1)
        self.assertLess(len(first), MCP.MEMORY_VIEW_MAX_CHARS)
        self.assertIn("段未读", first)
        self.assertIn('read_project_memory(scope="core", part=2)', first)
        second = MCP._memory_paged("抬头", body, "core", 2)
        self.assertNotEqual(first, second)
        # 末段不再喊「继续」，否则 agent 会无限翻页
        import re
        total = int(re.search(r"共 (\d+) 段", first).group(1))
        last = MCP._memory_paged("抬头", body, "core", total)
        self.assertNotIn("段未读", last)

    def test_paging_never_drops_content(self):
        body = "\n".join(f"L{i}" for i in range(9000))
        import re
        first = MCP._memory_paged("", body, "core", 1)
        total = int(re.search(r"共 (\d+) 段", first).group(1))
        seen = "".join(MCP._memory_paged("", body, "core", p).split("━━ 本段结束")[0]
                       for p in range(1, total + 1))
        for probe in ("L0", "L4500", "L8999"):
            self.assertIn(probe, seen)

    # ── 验收 2：闸门判定 ────────────────────────────────────────
    def test_core_scope_opens_the_gate_and_topic_scope_does_not(self):
        self.assertTrue(MCP._memory_scope_opens_gate({}))                     # 不传 = core
        self.assertTrue(MCP._memory_scope_opens_gate({"scope": "core"}))
        self.assertTrue(MCP._memory_scope_opens_gate({"scope": "all"}))
        self.assertFalse(MCP._memory_scope_opens_gate({"scope": "topic:分流转处"}))

    def test_reading_only_core_releases_other_tools(self):
        out = _rpc([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": MCP.PROTO}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "read_project_memory", "arguments": {"scope": "core"}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "search_localkb", "arguments": {"query": "测试"}}},
        ], tool_result="已执行")
        self.assertNotIn("isError", out[1]["result"])
        self.assertEqual("已执行", _text(out[2]))

    def test_reading_a_single_topic_does_not_release_other_tools(self):
        """★ 本次分区的核心：主题档案不读也能开工，读了主题却不算读过项目记忆。"""
        AW.append_memory("topic:分流转处", "结论")
        out = _rpc([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": MCP.PROTO}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "read_project_memory", "arguments": {"scope": "topic:分流转处"}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "search_localkb", "arguments": {"query": "测试"}}},
        ], tool_result="不应执行")
        self.assertIn("结论", _text(out[1]))
        self.assertTrue(out[2]["result"]["isError"])

    def test_bad_scope_keeps_the_gate_locked(self):
        """失败文案必须落在 _project_memory_read_succeeded 的前缀黑名单上，否则会误放行。"""
        out = MCP.do_tool("read_project_memory", {"scope": "乱写"})
        self.assertTrue(out.startswith("读项目记忆失败："), out[:40])
        self.assertFalse(MCP._project_memory_read_succeeded(out))

    def test_missing_topic_keeps_the_gate_locked_and_lists_candidates(self):
        AW.append_memory("topic:分流转处", "结论")
        out = MCP.do_tool("read_project_memory", {"scope": "topic:不存在的题"})
        self.assertTrue(out.startswith("读项目记忆失败："))
        self.assertIn("分流转处", out)

    # ── 验收 5：旧布局（只有单文件）不报错、行为不变 ──────────────
    def _strip_to_legacy_layout(self):
        AW.tools_file().unlink()
        for p in AW.topics_dir().glob("*.md"):
            p.unlink()
        AW.topics_dir().rmdir()

    def test_legacy_single_file_layout_returns_the_old_body_verbatim(self):
        """兼容分支在 _memory_view_text 这一层：只有 项目记忆.md 时，返回体与改造前一字不差。

        注意它在工具层**走不到**——do_tool 会先 ensure_scaffold() 把缺的分区建回来
        （见下一条）。留着这个分支是安全网：ensure_scaffold 吞异常，用户也可能中途删文件。
        """
        self._strip_to_legacy_layout()
        head, body = MCP._memory_view_text(AW, full=False)
        self.assertEqual("", head)
        self.assertTrue(body.startswith("项目记忆（"), body[:40])
        # 断结构标记而不是断字眼：项目记忆.md 的正文本身就讲了分区表，会提到「工具经验.md」。
        # 兼容分支要保证的是**不拼接其他分区**——没有分节抬头、没有主题清单。
        self.assertNotIn("━━ 工具经验.md ━━", body)
        self.assertNotIn("━━ 主题档案", body)
        self.assertEqual(AW.read_memory_section("core")[1].strip(),
                         body.split("：\n\n", 1)[1])
        self.assertTrue(MCP._project_memory_read_succeeded(body))

    def test_old_installs_are_upgraded_on_first_read_instead_of_requiring_migration(self):
        """需求 §5「不要求用户先迁移才能用新版」——老用户一接入就自动补齐分区。"""
        self._strip_to_legacy_layout()
        out = MCP.do_tool("read_project_memory", {})
        self.assertTrue(AW.tools_file().exists())
        self.assertTrue(AW.topics_dir().is_dir())
        self.assertIn("工具经验.md", out)
        self.assertTrue(MCP._project_memory_read_succeeded(out))

    def test_append_recreates_missing_topic_dir(self):
        self._strip_to_legacy_layout()
        self.assertTrue(AW.append_memory("topic:新主题", "内容")[0])
        self.assertTrue(AW.topics_dir().is_dir())

    def test_append_recreates_a_deleted_partition_file(self):
        AW.tools_file().unlink()
        self.assertTrue(AW.append_memory("tools", "重建后的第一条")[0])
        self.assertIn("重建后的第一条", AW.read_memory_section("tools")[1])

    # ── 工具契约 ────────────────────────────────────────────────
    def test_scope_and_target_are_free_strings_not_enums(self):
        """_validate_json_value 的 enum 是精确集合比对且跑在 dispatch 之前——
        scope 写 enum 会让 topic:<名> 直接被判 -32602 参数无效。"""
        read = next(t for t in MCP.TOOLS if t["name"] == "read_project_memory")
        append = next(t for t in MCP.TOOLS if t["name"] == "append_project_memory")
        self.assertNotIn("enum", read["inputSchema"]["properties"]["scope"])
        self.assertNotIn("enum", append["inputSchema"]["properties"]["target"])
        self.assertEqual("core", read["inputSchema"]["properties"]["scope"]["default"])
        self.assertEqual("core", append["inputSchema"]["properties"]["target"]["default"])
        # text 不能是必填：只改状态行时可以不带正文
        self.assertNotIn("text", append["inputSchema"].get("required", []))

    def test_status_tool_is_registered_as_a_write_tool(self):
        self.assertIn("set_memory_topic_status", MCP._TOOL_TITLES)
        self.assertIn("set_memory_topic_status", MCP._WRITE_TOOLS)

    def test_set_status_tool_refuses_to_create_archives(self):
        out = MCP.do_tool("set_memory_topic_status", {"topic": "没有这个题", "status": "已完成"})
        self.assertIn("被拒绝", out)
        self.assertEqual([], AW.list_memory_topics())


class SearchWikiScopeTests(unittest.TestCase):
    """验收 6：include_wiki=false 时结果里的综合层页数为 0。"""

    def test_search_localkb_declares_include_wiki(self):
        tool = next(t for t in MCP.TOOLS if t["name"] == "search_localkb")
        prop = tool["inputSchema"]["properties"]["include_wiki"]
        self.assertEqual("boolean", prop["type"])
        self.assertTrue(prop["default"])
        self.assertIn("综合", prop["description"])

    def test_search_full_drops_wiki_rows_before_rerank(self):
        """必须在 cand 过滤（截池/重排之前）：出口处再滤会让结果少于 topk，
        因为 wiki 行已经占掉了名额（retriever 里 C6 记过这个坑）。"""
        import retriever as R

        records = {
            "P1::0": {"chunk_id": "P1::0", "key": "P1", "text": "论文一", "row_type": "paper"},
            "W1::wiki": {"chunk_id": "W1::wiki", "key": "W1", "text": "综合页", "row_type": "wiki"},
            "P2::0": {"chunk_id": "P2::0", "key": "P2", "text": "论文二", "row_type": "paper"},
        }
        order = ["P1::0", "W1::wiki", "P2::0"]
        seen = {}

        class _RR:
            def scores(self, _q, texts):
                seen["texts"] = list(texts)
                return [1.0] * len(texts)

        with mock.patch.object(R, "dense_search", return_value=order), \
             mock.patch.object(R, "bm25_search", return_value=[]), \
             mock.patch.object(R, "rrf", return_value=order), \
             mock.patch.object(R, "fetch_records", return_value=records), \
             mock.patch.dict(R.M, {"rerank": _RR()}, clear=False):
            R.search_full("q", 8, "relevance", include_wiki=False)
        self.assertNotIn("综合页", seen["texts"])   # 重排都没花在它身上
        self.assertEqual(["论文一", "论文二"], seen["texts"])

    def test_search_full_keeps_wiki_rows_by_default(self):
        import retriever as R

        records = {
            "P1::0": {"chunk_id": "P1::0", "key": "P1", "text": "论文一", "row_type": "paper"},
            "W1::wiki": {"chunk_id": "W1::wiki", "key": "W1", "text": "综合页", "row_type": "wiki"},
        }
        order = ["P1::0", "W1::wiki"]
        seen = {}

        class _RR:
            def scores(self, _q, texts):
                seen["texts"] = list(texts)
                return [1.0] * len(texts)

        with mock.patch.object(R, "dense_search", return_value=order), \
             mock.patch.object(R, "bm25_search", return_value=[]), \
             mock.patch.object(R, "rrf", return_value=order), \
             mock.patch.object(R, "fetch_records", return_value=records), \
             mock.patch.dict(R.M, {"rerank": _RR()}, clear=False):
            R.search_full("q", 8, "relevance")
        self.assertIn("综合页", seen["texts"])

    def test_server_forwards_include_wiki_to_retriever(self):
        import server

        with mock.patch.object(server.R, "search", return_value=[]) as s, \
             mock.patch.dict(server.R.STATE, {"ready": True, "mode": "full"}, clear=False):
            server.search(server.SearchQ(query="测试", include_wiki=False))
        self.assertFalse(s.call_args.kwargs["include_wiki"])

    def test_include_wiki_defaults_to_true_for_old_callers(self):
        import server

        with mock.patch.object(server.R, "search", return_value=[]) as s, \
             mock.patch.dict(server.R.STATE, {"ready": True, "mode": "full"}, clear=False):
            server.search(server.SearchQ(query="测试"))
        self.assertTrue(s.call_args.kwargs["include_wiki"])


if __name__ == "__main__":
    unittest.main()

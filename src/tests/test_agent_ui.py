# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import agent_ws as AW  # noqa: E402
import server  # noqa: E402
from fastapi import HTTPException  # noqa: E402


class AgentOutputTests(unittest.TestCase):
    def test_all_factory_workflows_have_mandatory_contract_sections(self):
        required = ("## 触发条件", "## 开工前检查", "## 用户决策点", "## 完成标准", "## 最终报告")
        workflows = (
            AW._WF_JJ_DRAFT, AW._WF_JJ_REVIEW, AW._WF_GENERAL_DRAFT,
            AW._WF_REVIEW, AW._WF_WIKI, AW._WF_DIVERGENCE,
        )
        self.assertEqual(len(workflows), 6)
        for body in workflows:
            for heading in required:
                self.assertIn(heading, body)
        self.assertIn("全量审查", AW._WF_WIKI)
        self.assertIn("简单事项直接处理", AW._WF_WIKI)

    def test_review_workflows_classify_themes_and_hide_raw_keys_from_deliverables(self):
        self.assertIn("set_wiki_theme", AW._WF_WIKI)
        self.assertIn("主题归类", AW._WF_WIKI)
        research = (AW._WF_JJ_DRAFT, AW._WF_JJ_REVIEW, AW._WF_GENERAL_DRAFT, AW._WF_REVIEW)
        for body in research:
            self.assertIn("既有主题", body)
        for body in (AW._README_OUTPUT, *research, AW._WF_WIKI, AW._WF_DIVERGENCE):
            self.assertIn("key", body)
            self.assertTrue("裸 key" in body or "裸文献 key" in body)

    def test_agent_safety_copy_matches_available_maintenance_tools(self):
        for body in (AW._WF_JJ_DRAFT, AW._WF_JJ_REVIEW, AW._WF_GENERAL_DRAFT, AW._WF_REVIEW):
            self.assertTrue(any(x in body for x in ("不直接修改原始文献", "不修改原始文献", "不改原始文献")))
            self.assertIn("用户明确要求", body)
        self.assertIn("云端检索或使用外部 AI 助手", AW._rules_summary_text())
        self.assertNotIn("不联网、不含大模型", AW._TASKS_README)

    def test_juvenile_draft_has_two_route_cards_and_stop_gate(self):
        body = AW._WF_JJ_DRAFT
        for route in ("教义学路线", "理论—制度建构路线"):
            self.assertIn(route, body)
        for field in (
            "适配度", "完整拟题", "核心问题", "中心观点", "三级提纲",
            "各部分主要内容", "所需规范、理论、事实材料", "优势", "风险", "不适用边界",
        ):
            self.assertIn(field, body)
        self.assertIn("用户选择前不得起草", body)
        self.assertIn("明确选定路线", body)
        self.assertIn("明确表示“不需要比较”", body)
        for gate in (
            "最有利于未成年人", "发展中能力", "支持性参与", "关系自主",
            "最小干预", "比例原则", "机构支持义务", "有效审查与救济",
        ):
            self.assertIn(gate, body)
        for consent_check in (
            "持续、知情、可撤回且受支持", "说明", "资源核验", "合理调整",
            "履行能力", "条件变更", "技术性违约", "撤销", "救济",
        ):
            self.assertIn(consent_check, body)
        self.assertIn("严重案件", body)
        self.assertIn("低风险", body)

    def test_juvenile_draft_v2_has_craft_contract_and_companion_handbook(self):
        body = AW._WF_JJ_DRAFT
        for phrase in (
            "写作设计单", "问题切入与创新诊断", "理论选择与操作化",
            "路线内结构原型推荐", "章节功能表", "第二人工闸门",
            "分节论证契约", "正文起草顺序", "首尾兑现", "五轮独立修改",
        ):
            self.assertIn(phrase, body)
        for revision in ("结构轮", "论证轮", "证据与引注轮", "语言轮", "原创性轮"):
            self.assertIn(revision, body)
        self.assertIn("自动附带", body)
        self.assertIn("不得改写本文件的路线、两个人工闸门", body)

        manual = AW._JJ_DRAFT_CRAFT_HANDBOOK
        for phrase in (
            "教义学路线", "理论—制度建构路线", "问题切入与创新",
            "理论选择与操作化", "争点与论证方法矩阵", "章节功能",
            "分节与段落动作", "标题、摘要、引言与结论",
            "功能性中性法学表达", "常见失败", "成稿检查表",
            "负例（自拟）", "正例（自拟）", "2 万字",
            "425—975 字", "7—15 句", "引注",
        ):
            self.assertIn(phrase, manual)
        for heading in ("## 触发条件", "## 开工前检查", "## 用户决策点", "## 完成标准", "## 最终报告"):
            self.assertNotIn(heading, manual)
        companions = AW.workflow_companion_specs("论文初稿（少年司法版）")
        self.assertEqual(1, len(companions))
        self.assertEqual(AW._JJ_DRAFT_HANDBOOK_KEY, companions[0][0])

    def test_juvenile_review_has_reproducible_log_and_numeric_recalculation(self):
        body = AW._WF_JJ_REVIEW
        for field in (
            "轮次", "精确检索式", "范围/排序/Top-k", "命中数", "去重后新增数",
            "纳入/排除/待核数", "新词及来源", "下一轮理由",
        ):
            self.assertIn(field, body)
        self.assertIn("分母、小计、类别数", body)
        self.assertIn("作者原说", body)
        self.assertIn("同作者纵向重构", body)
        self.assertIn("跨作者综合", body)

    def test_general_draft_is_explicitly_temporary_and_unvalidated(self):
        body = AW._WF_GENERAL_DRAFT
        self.assertIn("尚未经过其他部门法训练验证", body)
        self.assertIn("暂用版", body)
        self.assertNotIn("少年司法八项迁移闸门", body)

    def test_root_entry_routes_by_task_then_domain(self):
        body = AW._ROOT_AGENTS
        self.assertIn("任务类型", body)
        self.assertIn("领域", body)
        for name in (
            "论文初稿（少年司法版）.md", "综述（少年司法版）.md",
            "论文初稿（通用暂用版）.md", "综述.md",
        ):
            self.assertIn(name, body)
        self.assertIn("用户选择前不得起草全文", body)

    def test_frontend_destructive_and_error_guards_are_wired(self):
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        index_html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('jpost("/setup/purge_deleted", { preview: true })', app_js)
        self.assertIn('jpost("/setup/purge_deleted", { confirm: true })', app_js)
        self.assertIn('title: "开启删除同步？"', app_js)
        self.assertNotIn('fetch("/build"', app_js)
        self.assertIn('const s = await jget("/build/status")', app_js)
        self.assertIn('id="ed-gokey" class="ag-link ag-linkbtn" type="button"', index_html)
        self.assertIn('id="ag-open-skills" type="button"', index_html)
        self.assertIn('id="settings-modal" class="modal" role="dialog"', index_html)
        self.assertIn('id="btn-release-memory"', index_html)
        self.assertIn('jpost("/setup/retrieval_memory/release", {})', app_js)
        self.assertIn('flashToast(j.msg || "已有维护任务在运行', app_js)

    def test_verified_wiki_updates_have_visible_review_controls(self):
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        index_html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        style_css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
        for element_id in (
            "wiki-review-banner", "wiki-review-panel", "wiki-review-diff",
            "wiki-review-accept", "wiki-review-edit", "wiki-review-discard",
        ):
            self.assertIn(f'id="{element_id}"', index_html)
        self.assertIn('jget("/wiki/review/" + encodeURIComponent(pageId))', app_js)
        self.assertIn('`/wiki/review/${encodeURIComponent(p.id)}/${action}`', app_js)
        self.assertIn("p.pending_review", app_js)
        self.assertIn(".wk-flag.pending", style_css)
        self.assertIn("当前显示和检索的仍是你核验过的版本", index_html)

    def test_metadata_lookup_is_debounced_but_fulltext_stays_explicit(self):
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        index_html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('class="library-searchbar" data-mode="metadata"', index_html)
        self.assertIn('id="go" hidden>检索全文</button>', index_html)
        self.assertIn("const METADATA_SEARCH_DELAY_MS = 300;", app_js)
        self.assertIn('"input", scheduleMetadataSearch', app_js)
        self.assertIn('"compositionstart"', app_js)
        self.assertIn('"compositionend"', app_js)
        self.assertIn("go.hidden = !semantic", app_js)
        self.assertIn("点击「检索全文」或按 Enter 开始", app_js)

    def test_output_topics_default_to_five_and_can_expand_all(self):
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        index_html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("<span>📦 交付物主题</span>", index_html)
        self.assertIn('id="ag-outputs-toggle"', index_html)
        self.assertIn('aria-expanded="false"', index_html)
        self.assertIn("const AGENT_OUTPUT_COLLAPSED_COUNT = 5;", app_js)
        self.assertIn('jget("/agent/outputs?limit=0")', app_js)
        self.assertIn("agentOutputs.slice(0, AGENT_OUTPUT_COLLAPSED_COUNT)", app_js)

    def test_scaffold_creates_codex_and_claude_workflow_entry_files(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(AW, "base_dir", return_value=Path(td)):
            AW.ensure_scaffold()
            agents = Path(td) / "AGENTS.md"; claude = Path(td) / "CLAUDE.md"
            self.assertTrue(agents.exists()); self.assertTrue(claude.exists())
            self.assertIn("工作流闸门", agents.read_text(encoding="utf-8"))
            self.assertIn("用户只要提到“维护”", claude.read_text(encoding="utf-8"))

    def test_scaffold_removes_only_obsolete_catalog_check_task(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(AW, "base_dir", return_value=Path(td)):
            obsolete = AW.tasks_dir() / "文献目录半年检查"
            keep = AW.tasks_dir() / "我的定时任务"
            obsolete.mkdir(parents=True); keep.mkdir(parents=True)
            (obsolete / "任务.md").write_text("旧开发任务", encoding="utf-8")
            (keep / "任务.md").write_text("用户任务", encoding="utf-8")
            AW.ensure_scaffold()
            self.assertFalse(obsolete.exists())
            self.assertTrue((keep / "任务.md").exists())

    def test_recursive_output_stats_include_nested_files(self):
        with tempfile.TemporaryDirectory() as td:
            topic = Path(td) / "定时任务"
            task = topic / "少年司法周报"
            task.mkdir(parents=True)
            (task / "周报.md").write_text("a", encoding="utf-8")
            (task / "重点摘录.md").write_text("b", encoding="utf-8")

            stats = server._scan_agent_output_tree(topic)

            self.assertEqual(stats["file_count"], 2)
            self.assertEqual(stats["subdir_count"], 1)
            self.assertGreater(stats["latest_mtime"], 0)
            self.assertEqual(stats["scan_errors"], 0)

    def test_output_listing_uses_recursive_counts(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "outputs"
            nested = out / "定时任务" / "少年司法周报"
            nested.mkdir(parents=True)
            (nested / "周报.md").write_text("a", encoding="utf-8")
            (nested / "重点摘录.md").write_text("b", encoding="utf-8")
            with mock.patch.object(AW, "ensure_scaffold"), \
                    mock.patch.object(AW, "output_dir", return_value=out):
                result = server.agent_outputs()

            item = result["outputs"][0]
            self.assertEqual(item["name"], "定时任务")
            self.assertEqual(item["file_count"], 2)
            self.assertEqual(item["subdir_count"], 1)

    def test_output_listing_limit_zero_returns_all(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "outputs"
            for i in range(10):
                topic = out / f"主题{i}"
                topic.mkdir(parents=True)
                (topic / "成果.md").write_text(str(i), encoding="utf-8")
            with mock.patch.object(AW, "ensure_scaffold"), \
                    mock.patch.object(AW, "output_dir", return_value=out):
                all_result = server.agent_outputs(limit=0)
                limited_result = server.agent_outputs(limit=5)

            self.assertEqual(all_result["total"], 10)
            self.assertEqual(len(all_result["outputs"]), 10)
            self.assertEqual(limited_result["total"], 10)
            self.assertEqual(len(limited_result["outputs"]), 5)

    def test_open_output_accepts_only_existing_real_child(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "outputs"
            topic = out / "合法主题"
            topic.mkdir(parents=True)
            opened = []
            with mock.patch.object(AW, "ensure_scaffold"), \
                    mock.patch.object(AW, "output_dir", return_value=out), \
                    mock.patch.object(server, "_open_system_dir", side_effect=opened.append):
                result = server.agent_open_output(server.AgentOpenOutputQ(name="合法主题"))
                self.assertTrue(result["ok"])
                self.assertEqual(opened, [topic.resolve()])
                for unsafe in ("..", str(topic.resolve()), "合法主题/..", "不存在"):
                    with self.assertRaises(HTTPException):
                        server.agent_open_output(server.AgentOpenOutputQ(name=unsafe))

    def test_open_output_rejects_directory_symlink_when_supported(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            out = base / "outputs"
            outside = base / "outside"
            out.mkdir(); outside.mkdir()
            link = out / "链接主题"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("当前 Windows 权限不允许创建目录符号链接")
            with mock.patch.object(AW, "ensure_scaffold"), \
                    mock.patch.object(AW, "output_dir", return_value=out), \
                    mock.patch.object(server, "_open_system_dir"):
                with self.assertRaises(HTTPException):
                    server.agent_open_output(server.AgentOpenOutputQ(name="链接主题"))

    def test_open_output_rejects_link_or_junction_probe(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "outputs"
            (out / "链接主题").mkdir(parents=True)
            with mock.patch.object(AW, "ensure_scaffold"), \
                    mock.patch.object(AW, "output_dir", return_value=out), \
                    mock.patch.object(server, "_is_link_or_junction", return_value=True), \
                    mock.patch.object(server, "_open_system_dir"):
                with self.assertRaises(HTTPException):
                    server.agent_open_output(server.AgentOpenOutputQ(name="链接主题"))


class AgentTaskTests(unittest.TestCase):
    def test_tasks_report_missing_and_unreadable_definitions(self):
        with tempfile.TemporaryDirectory() as td:
            tasks = Path(td) / "tasks"
            valid = tasks / "有效任务"
            missing = tasks / "缺定义"
            broken = tasks / "读失败"
            valid.mkdir(parents=True); missing.mkdir(); broken.mkdir()
            (valid / "任务.md").write_text(
                "---\n名称: 每周简报\n频率: 每周一\n启用: true\n---\n搜什么：少年司法",
                encoding="utf-8",
            )
            (broken / "任务.md").write_text("占位", encoding="utf-8")
            original_read_text = Path.read_text

            def selective_read(path_obj, *args, **kwargs):
                if path_obj.parent.name == "读失败":
                    raise OSError("模拟读取失败")
                return original_read_text(path_obj, *args, **kwargs)

            with mock.patch.object(AW, "ensure_scaffold"), \
                    mock.patch.object(AW, "tasks_dir", return_value=tasks), \
                    mock.patch.object(Path, "read_text", selective_read):
                result = server.agent_tasks()

            self.assertEqual([t["name"] for t in result["tasks"]], ["每周简报"])
            reasons = {x["name"]: x["reason"] for x in result["unrecognized"]}
            self.assertEqual(reasons["缺定义"], "missing_task_file")
            self.assertEqual(reasons["读失败"], "read_error")
            self.assertEqual(result["unrecognized_count"], 2)

    def test_tasks_folder_is_an_explicit_open_whitelist_entry(self):
        with tempfile.TemporaryDirectory() as td:
            tasks = Path(td) / "tasks"
            opened = []
            with mock.patch.object(AW, "ensure_scaffold"), \
                    mock.patch.object(AW, "tasks_dir", return_value=tasks), \
                    mock.patch.object(server, "_open_system_dir", side_effect=opened.append):
                result = server.agent_open_folder(server.AgentOpenQ(which="tasks"))
            self.assertTrue(result["ok"])
            self.assertTrue(tasks.is_dir())
            self.assertEqual(opened, [tasks])


if __name__ == "__main__":
    unittest.main()

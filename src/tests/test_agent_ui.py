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
        for body in research[1:]:   # v6 少年司法初稿只起草、不写回 wiki，不涉主题归类
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

    def test_juvenile_draft_v6_shows_skeleton_once_then_keeps_writing(self):
        """v6（2026-08-20 上线）：选型决策树与两张骨架卡整体废止，闸门收敛成一处。

        v4 的决定性教训——可数指标能被优化到全绿而质量并不改善——所以 v6 只划禁区、
        不设配额；用户看一眼骨架、没异议就接着写，不再停下来等。
        """
        body = AW._WF_JJ_DRAFT
        for stale in ("决策树", "骨架卡", "准入自检", "选型前侦查", "硬失败",
                      "用户选定前不得写摘要、引言或任何章节正文"):
            self.assertNotIn(stale, body, f"v4/v5 的「{stale}」不得回潮")
        self.assertIn("## 用户决策点", body)
        self.assertIn("逐字标题树", body)
        self.assertIn("每章准备写什么", body)
        self.assertIn("没有异议就接着写全文，不必停下来等", body)

    def test_juvenile_draft_reads_sources_before_choosing_structure(self):
        """2026-08-10：选型不得发生在读文献之前，但侦查要有上限、且不产出引注。

        旧流程把完整精读放在第一闸门之后，等于凭题目猜结构。
        """
        body = AW._WF_JJ_DRAFT
        self.assertIn("## 开工前检查", body)
        self.assertLess(body.index("## 开工前检查"), body.index("## 二、骨架与段落"),
                        "读文献必须排在搭骨架之前")
        self.assertIn("读文献，再动笔", body)
        self.assertIn("不许凭题目猜结构", body)
        self.assertIn("骨架是从读到的东西里长出来的", body)
        # 片段/摘要不算读过——防止用检索片段伪造页码
        self.assertIn("检索片段、摘要、SAC 摘要都不算读过", body)
        # 通用暂用版同样先摸底再定路线
        self.assertIn("先做小规模摸底再谈路线", AW._WF_GENERAL_DRAFT)
        self.assertIn("不得直接落成引注", AW._WF_GENERAL_DRAFT)

    def test_research_workflows_ask_user_before_spawning_subagents(self):
        """四条研究工作流都必须先问用户是否派子代理，由用户决定。"""
        for body in (AW._WF_JJ_REVIEW, AW._WF_GENERAL_DRAFT, AW._WF_REVIEW):
            self.assertIn("## 分工：是否派子代理（开工前问一次，由用户决定）", body)
            self.assertIn("用户回答前不要派", body)
            self.assertIn("子代理交回的是材料，不是成稿", body)
        # 少年司法初稿（v6）按 2026-08-12 的固定规则改为默认派，不再开工前询问
        jj = AW._WF_JJ_DRAFT
        self.assertIn("默认派子代理", jj)
        self.assertIn("起草正文各章不派", jj)   # 跨章约束，并行起草必然打破
        self.assertIn("跨章约束", jj)
        self.assertIn("你自己做", jj)           # 用户一句话即可全程不派
        self.assertIn("默认派子代理", AW._ROOT_AGENTS)

    def test_research_workflows_require_retrieval_discovery_log(self):
        """2026-08-10：应用不落盘任何检索历史，工作流的发现日志是唯一真实记录。

        没有它，「查询 → 该返回什么」的判断永久丢失，检索质量就没法校准。
        """
        # 表头必须**逐字一致**：列名或顺序一变，历史日志就聚合不起来了。
        # 逐个断言子串是测不出重排或删列的，所以这里断言整条表头。
        HEADER = "| 轮次 | 查询词（逐字） | 名次 | key | 题名 | 处置 | 理由 |"
        for body in (AW._WF_JJ_DRAFT, AW._WF_JJ_REVIEW, AW._WF_GENERAL_DRAFT, AW._WF_REVIEW):
            self.assertIn("检索发现日志.md", body)
            self.assertIn(HEADER, body, "表头列名与顺序不得改动（改了历史日志就聚合不起来）")
            self.assertIn("弃用", body)
            self.assertIn("漏召回", body)
            self.assertIn("不得事后凭记忆补写", body)

    def test_juvenile_draft_v6_bans_and_companion_structure_ref(self):
        """v6：护栏要的五节外壳齐全；三条新禁区与两条旧账都已落进文件。

        三条禁区来自 2026-08-20 的对照——用户亲手成稿 vs 同题 AI 稿。
        """
        body = AW._WF_JJ_DRAFT
        for heading in ("## 触发条件", "## 开工前检查", "## 用户决策点",
                        "## 完成标准", "## 最终报告"):
            self.assertIn(heading, body)
        self.assertIn("把作业过程写进正文", body)          # 证据强度自白不进正文
        self.assertIn("结论进正文，出处、样本与限度进脚注", body)
        self.assertIn("制度建议要嫁接，不要发明", body)
        self.assertIn("五条不能破", body)
        self.assertIn("显式标注为核验结果", body)          # 引注可靠性双口径
        self.assertIn("结构指标整体降为参考", body)        # 用户已给大纲时的降级开关

        # 自动附带材料只保留三大刊结构台账；成文技艺手册不再进入模板清单
        companions = AW.workflow_companion_specs("论文初稿（少年司法版）")
        self.assertEqual(1, len(companions))
        self.assertEqual(AW._JJ_DRAFT_COMPANION_KEY, companions[0][0])
        self.assertIn("三大刊结构", companions[0][0])
        self.assertFalse(any("成文技艺手册" in key for key, *_ in AW._template_specs()))
        for heading in ("## 触发条件", "## 开工前检查", "## 用户决策点", "## 最终报告"):
            self.assertNotIn(heading, AW._JJ_DRAFT_STRUCTURE_REF,
                             "伴随材料不得复制主工作流的规程章节")

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

    def test_public_jj_draft_copy_matches_v6(self):
        surfaces = (
            (ROOT / "web" / "index.html").read_text(encoding="utf-8"),
            (ROOT / "MCP接入说明.md").read_text(encoding="utf-8"),
            (ROOT.parent / "README.md").read_text(encoding="utf-8"),
        )
        for body in surfaces:
            self.assertIn("三大刊结构", body)
            self.assertNotIn("选型决策树", body)
            self.assertNotIn("七类结构原型", body)
            self.assertNotIn("两张骨架卡", body)

    def test_root_entry_routes_by_task_then_domain(self):
        body = AW._ROOT_AGENTS
        self.assertIn("项目记忆闸门（最高优先级）", body)
        self.assertIn("开始任何任务前，必须先完整读取", body)
        self.assertIn("凡写进其他私有记忆", body)
        self.assertIn("append_project_memory", body)
        self.assertIn("任务类型", body)
        self.assertIn("领域", body)
        for name in (
            "论文初稿（少年司法版）.md", "综述（少年司法版）.md",
            "论文初稿（通用暂用版）.md", "综述.md",
        ):
            self.assertIn(name, body)
        self.assertIn("没有异议就接着写全文", body)

    def test_frontend_destructive_and_error_guards_are_wired(self):
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        index_html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('jpost("/setup/purge_deleted", { preview: true })', app_js)
        self.assertIn('jpost("/setup/purge_deleted", { confirm: true })', app_js)
        self.assertIn('title: "开启删除同步？"', app_js)
        self.assertNotIn('fetch("/build"', app_js)
        self.assertIn('const s = await jget("/build/status")', app_js)
        self.assertIn('id="ed-gokey" class="ag-link ag-linkbtn" type="button"', index_html)
        self.assertIn(
            'data-open="rely">📂 打开资料库文件夹</button><button class="ghost2 ag-openbtn" '
            'data-open="skills">📂 打开技能文件夹</button>',
            index_html,
        )
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
            self.assertIn("项目记忆闸门", agents.read_text(encoding="utf-8"))
            self.assertIn("auto memory", claude.read_text(encoding="utf-8"))
            self.assertIn("工作流闸门", agents.read_text(encoding="utf-8"))
            self.assertIn("用户只要提到“维护”", claude.read_text(encoding="utf-8"))

    def test_project_memory_seed_is_cross_agent_canonical_memory(self):
        self.assertIn("唯一共享的项目记忆", AW._PROJECT_MEMORY)
        self.assertIn("开始任务前必须先完整读取", AW._PROJECT_MEMORY)
        self.assertIn("私有记忆不能替代本文件", AW._PROJECT_MEMORY)

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

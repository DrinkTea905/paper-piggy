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
        for body in (AW._WF_PAPER, AW._WF_WIKI, AW._WF_DIVERGENCE):
            for heading in required:
                self.assertIn(heading, body)
        self.assertIn("全量审查", AW._WF_WIKI)
        self.assertIn("简单事项直接处理", AW._WF_WIKI)

    def test_scaffold_creates_codex_and_claude_workflow_entry_files(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(AW, "base_dir", return_value=Path(td)):
            AW.ensure_scaffold()
            agents = Path(td) / "AGENTS.md"; claude = Path(td) / "CLAUDE.md"
            self.assertTrue(agents.exists()); self.assertTrue(claude.exists())
            self.assertIn("工作流闸门", agents.read_text(encoding="utf-8"))
            self.assertIn("用户只要提到“维护”", claude.read_text(encoding="utf-8"))

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

# -*- coding: utf-8 -*-
import json, sys, tempfile, unittest, zipfile
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))

import agent_ws as AW  # noqa: E402
import updater  # noqa: E402
import wiki_store as W  # noqa: E402


class AgentTemplateUpgradeTests(unittest.TestCase):
    @staticmethod
    def _historical_paper_workflow():
        """读取末代 _WF_PAPER 的固定夹具；测试不得依赖 .git 或当前提交的父历史。"""
        return (SRC / "tests" / "fixtures" / "legacy_combined_paper_v8.md").read_text(
            encoding="utf-8")

    def test_customized_template_is_visible_diffable_acknowledgeable_and_reversible(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(AW, "base_dir", return_value=Path(td)):
            AW.ensure_scaffold()
            spec = next(x for x in AW._template_specs() if x[0] == "rely/技能/综述.md")
            key, path, current, mask, _seed = spec
            path.write_text("# 我的写作工作流\n必须先让我确认提纲。\n", encoding="utf-8")

            AW.ensure_scaffold()
            status = AW.upgrade_status()
            item = next(x for x in status["items"] if x["key"] == key)
            self.assertEqual(item["status"], "pending")
            self.assertTrue(Path(item["new_path"]).exists())
            self.assertIn("我的写作工作流", AW.template_diff(key))

            AW.acknowledge_update(key, item["current_hash"])
            self.assertFalse(any(x["key"] == key for x in AW.upgrade_status()["items"]))

            backup = Path(AW.replace_with_factory(key, item["current_hash"]))
            self.assertTrue(backup.exists())
            self.assertIn("必须先让我确认提纲", backup.read_text(encoding="utf-8"))
            self.assertEqual(AW._norm_hash(path.read_text(encoding="utf-8"), mask), AW._norm_hash(current, mask))

    def test_all_historical_combined_workflow_hashes_are_preserved_for_migration(self):
        expected = {
            "5027f5d8e6c6837907e5ddbc294de7b2f10d5de3",
            "ee9f25dc732f19acc62a95094b9159669ef74326",
            "04951705502b6cced56dca9cca145ec64e01a876",
            "6f1c82b020fad509e00b8bf956f88d34d5e81429",
            "35f90edf1c4dfbf899f82c9036efb6acca685e2b",
            "7ef669e2afd43e55a97a0091bbcdcab908aa6f5e",
            "e428cd8eef744cde51f4097a0d3b4b5929db39bd",
            "12151bfb320db1cd52fb91bb0fa1580e900c736e",
        }
        legacy = AW._FACTORY_HASHES["rely/技能/写论文与综述.md"]
        self.assertEqual(legacy, expected)
        self.assertEqual(len(AW._LEGACY_COMBINED_PAPER_EXACT_HASHES), 8)
        real = self._historical_paper_workflow()
        self.assertIn(AW._norm_hash(real), legacy)
        self.assertIn(AW._exact_hash(real), AW._LEGACY_COMBINED_PAPER_EXACT_HASHES)

    def test_factory_combined_workflow_moves_to_review_and_upgrades(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(AW, "base_dir", return_value=Path(td)):
            skills = AW.skills_dir()
            skills.mkdir(parents=True)
            old = skills / "写论文与综述.md"
            old_text = self._historical_paper_workflow()
            old.write_text(old_text, encoding="utf-8")
            actions = AW.ensure_scaffold()

            review = skills / "综述.md"
            self.assertFalse(old.exists())
            self.assertTrue(review.exists())
            self.assertEqual(review.read_text(encoding="utf-8"), AW._WF_REVIEW)
            self.assertEqual(actions["migration/写论文与综述.md"], "factory_renamed")
            self.assertEqual(actions["rely/技能/综述.md"], "current")

    def test_existing_review_is_never_overwritten_by_factory_combined_workflow(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(AW, "base_dir", return_value=Path(td)):
            skills = AW.skills_dir()
            skills.mkdir(parents=True)
            old = skills / "写论文与综述.md"
            target = skills / "综述.md"
            old_text = self._historical_paper_workflow()
            target_text = "# 用户自己的综述流程\n保留我的结构。\n"
            old.write_text(old_text, encoding="utf-8")
            target.write_text(target_text, encoding="utf-8")
            actions = AW.ensure_scaffold()

            self.assertFalse(old.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), target_text)
            self.assertEqual(actions["migration/写论文与综述.md"], "factory_removed")
            self.assertTrue((skills / "综述.new.md").exists())

    def test_review_created_during_migration_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(AW, "base_dir", return_value=Path(td)):
            skills = AW.skills_dir()
            skills.mkdir(parents=True)
            old = skills / "写论文与综述.md"
            target = skills / "综述.md"
            old.write_text(self._historical_paper_workflow(), encoding="utf-8")
            user_text = "# 用户刚刚创建的综述流程\n不得覆盖。\n"
            real_open = Path.open

            def race_open(path, mode="r", *args, **kwargs):
                if path == target and mode == "x":
                    target.write_text(user_text, encoding="utf-8")
                    raise FileExistsError(str(target))
                return real_open(path, mode, *args, **kwargs)

            with mock.patch.object(Path, "open", autospec=True, side_effect=race_open):
                actions = AW.ensure_scaffold()

            self.assertFalse(old.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), user_text)
            self.assertEqual(actions["migration/写论文与综述.md"], "factory_removed")
            self.assertEqual((skills / "综述.new.md").read_text(encoding="utf-8"), AW._WF_REVIEW)

    def test_whitespace_semantic_change_is_preserved_when_review_target_is_absent(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(AW, "base_dir", return_value=Path(td)):
            skills = AW.skills_dir()
            skills.mkdir(parents=True)
            old = skills / "写论文与综述.md"
            factory = self._historical_paper_workflow()
            changed = factory.replace("\n- 用户要求", "\n  - 用户要求", 1)
            self.assertNotEqual(changed, factory)
            self.assertEqual(AW._norm_hash(changed), AW._norm_hash(factory))
            self.assertNotEqual(AW._exact_hash(changed), AW._exact_hash(factory))
            old.write_text(changed, encoding="utf-8")

            actions = AW.ensure_scaffold()

            kept = list(skills.glob("写论文与综述(你改过的·请并入综述或通用初稿)*.md"))
            self.assertEqual(actions["migration/写论文与综述.md"], "custom_preserved")
            self.assertEqual(len(kept), 1)
            self.assertEqual(kept[0].read_text(encoding="utf-8"), changed)
            self.assertEqual((skills / "综述.md").read_text(encoding="utf-8"), AW._WF_REVIEW)

    def test_whitespace_semantic_change_is_preserved_when_review_target_exists(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(AW, "base_dir", return_value=Path(td)):
            skills = AW.skills_dir()
            skills.mkdir(parents=True)
            old = skills / "写论文与综述.md"
            target = skills / "综述.md"
            factory = self._historical_paper_workflow()
            changed = factory.replace("\n- 用户要求", "\n  - 用户要求", 1)
            target_text = "# 用户现有综述\n"
            old.write_text(changed, encoding="utf-8")
            target.write_text(target_text, encoding="utf-8")

            actions = AW.ensure_scaffold()

            kept = list(skills.glob("写论文与综述(你改过的·请并入综述或通用初稿)*.md"))
            self.assertEqual(actions["migration/写论文与综述.md"], "custom_preserved")
            self.assertEqual(len(kept), 1)
            self.assertEqual(kept[0].read_text(encoding="utf-8"), changed)
            self.assertEqual(target.read_text(encoding="utf-8"), target_text)

    def test_customized_combined_workflow_is_renamed_and_preserved_verbatim(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(AW, "base_dir", return_value=Path(td)):
            skills = AW.skills_dir()
            skills.mkdir(parents=True)
            old = skills / "写论文与综述.md"
            custom = "# 我的旧工作流\n这些规则不能丢。\n"
            old.write_text(custom, encoding="utf-8")

            actions = AW.ensure_scaffold()

            kept = list(skills.glob("写论文与综述(你改过的·请并入综述或通用初稿)*.md"))
            self.assertFalse(old.exists())
            self.assertEqual(len(kept), 1)
            self.assertEqual(kept[0].read_text(encoding="utf-8"), custom)
            self.assertEqual(actions["migration/写论文与综述.md"], "custom_preserved")
            self.assertEqual((skills / "综述.md").read_text(encoding="utf-8"), AW._WF_REVIEW)

    def test_current_workflow_with_only_markdown_indent_change_gets_sidecar(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(AW, "base_dir", return_value=Path(td)):
            AW.ensure_scaffold()
            key, path, current, _mask, _seed = next(
                x for x in AW._template_specs() if x[0] == "rely/技能/综述.md")
            changed = current.replace("\n- 用户要求", "\n  - 用户要求", 1)
            self.assertNotEqual(changed, current)
            self.assertEqual(AW._norm_hash(changed), AW._norm_hash(current))
            path.write_text(changed, encoding="utf-8")

            action = AW.ensure_scaffold()[key]

            self.assertEqual(action, "forked")
            self.assertEqual(path.read_text(encoding="utf-8"), changed)
            self.assertEqual(path.with_name("综述.new.md").read_text(encoding="utf-8"), current)

    def test_very_old_single_workflow_is_always_preserved_and_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(AW, "base_dir", return_value=Path(td)):
            skills = AW.skills_dir()
            skills.mkdir(parents=True)
            old = skills / "工作流.md"
            content = "# 很早的单文件工作流\n  - 我调整过缩进\n"
            old.write_text(content, encoding="utf-8")

            first = AW.ensure_scaffold()
            second = AW.ensure_scaffold()

            kept = list(skills.glob("工作流(*请并入对应新文件)*.md"))
            self.assertFalse(old.exists())
            self.assertEqual(len(kept), 1)
            self.assertEqual(kept[0].read_text(encoding="utf-8"), content)
            self.assertEqual(first["migration/写论文与综述.md"], "absent")
            self.assertEqual(second["migration/写论文与综述.md"], "absent")

    def test_edited_sidecar_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(AW, "base_dir", return_value=Path(td)):
            AW.ensure_scaffold()
            key, path, _current, _mask, _seed = next(
                x for x in AW._template_specs() if x[0] == "rely/参考格式/说明.md")
            path.write_text("用户主文件", encoding="utf-8")
            old_sidecar = path.with_name(path.stem + ".new" + path.suffix)
            old_sidecar.write_text("用户在旁本里的合并笔记", encoding="utf-8")
            AW.ensure_scaffold()
            self.assertEqual(old_sidecar.read_text(encoding="utf-8"), "用户在旁本里的合并笔记")
            item = next(x for x in AW.upgrade_status()["items"] if x["key"] == key)
            self.assertTrue(item["new_path"].endswith(".new.2.md"))

    def test_agent_merge_keeps_user_text_backs_up_and_clears_current_notice(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(AW, "base_dir", return_value=Path(td)):
            AW.ensure_scaffold()
            key, path, current, _mask, _seed = next(
                x for x in AW._template_specs() if x[0] == "rely/参考格式/说明.md")
            path.write_text("用户自己的格式清单\n", encoding="utf-8")
            AW.ensure_scaffold()
            item = next(x for x in AW.upgrade_status()["items"] if x["key"] == key)
            merged = current + "\n## 我的格式\n用户自己的格式清单\n"
            backup = Path(AW.merge_template(key, item["current_hash"], item["main_hash"], merged))
            self.assertIn("用户自己的格式清单", backup.read_text(encoding="utf-8"))
            self.assertEqual(path.read_text(encoding="utf-8"), merged)
            self.assertFalse(any(x["key"] == key for x in AW.upgrade_status()["items"]))

    def test_agent_merge_rejects_concurrent_change(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(AW, "base_dir", return_value=Path(td)):
            AW.ensure_scaffold()
            key, path, current, _mask, _seed = next(
                x for x in AW._template_specs() if x[0] == "rely/参考格式/说明.md")
            path.write_text("用户版", encoding="utf-8"); AW.ensure_scaffold()
            item = next(x for x in AW.upgrade_status()["items"] if x["key"] == key)
            path.write_text("用户刚刚又改了", encoding="utf-8")
            with self.assertRaises(ValueError):
                AW.merge_template(key, item["current_hash"], item["main_hash"], current)


class WikiTemplateUpgradeTests(unittest.TestCase):
    def test_custom_old_wiki_gets_sidecar_and_backup_before_replace(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); wiki = root / "wiki"; schema = wiki / "WIKI.md"
            with mock.patch.object(W.C, "WIKI_DIR", wiki), \
                    mock.patch.object(W.C, "WIKI_SCHEMA_MD", schema), \
                    mock.patch.object(W, "KIND_DIRS", {}):
                wiki.mkdir(); schema.write_text("# 我的旧规约 schema v1\n不要覆盖人工结论。", encoding="utf-8")
                W.ensure_scaffold()
                item = W.upgrade_status()["items"][0]
                self.assertEqual(item["status"], "pending")
                self.assertTrue(Path(item["new_path"]).exists())
                backup = Path(W.replace_with_factory(item["current_hash"]))
                self.assertIn("我的旧规约", backup.read_text(encoding="utf-8"))
                self.assertEqual(W._norm_hash(schema.read_text(encoding="utf-8")), W._norm_hash(W.WIKI_MD_SEED))


class RuntimeFingerprintTests(unittest.TestCase):
    def test_incremental_update_stops_before_touching_app_when_runtime_differs(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td); app = home / "app"; py = home / "python"; app.mkdir(); py.mkdir()
            (py / ".paperpiggy-runtime.sha256").write_text("old-runtime", encoding="utf-8")
            package = home / "update.zip"
            with zipfile.ZipFile(package, "w") as z:
                z.writestr("version.json", json.dumps({"version": "next", "runtime_fingerprint": "new-runtime"}))
            with mock.patch.object(updater, "BUNDLE_DIR", home), mock.patch.object(updater, "APP_DIR", app), \
                    mock.patch.object(updater, "_wait_pid_exit", return_value=True), \
                    mock.patch.object(updater, "_writable", return_value=True):
                result = updater.apply(package, relaunch=False)
            self.assertFalse(result["ok"])
            self.assertIn("完整安装器", result["error"])
            self.assertTrue(app.exists())


class UpgradeUiContractTests(unittest.TestCase):
    def test_agent_page_exposes_merge_controls_without_native_dialogs(self):
        html = (SRC / "web" / "index.html").read_text(encoding="utf-8")
        js = (SRC / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="ag-upgrade"', html)
        self.assertIn("复制给 Agent 合并", js)
        self.assertIn("/upgrade/replace", js)
        self.assertIn("singleSystemDetail", js)
        self.assertIn("x.detail", js)
        self.assertNotIn("window.confirm(", js)


if __name__ == "__main__":
    unittest.main()

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
    def test_customized_template_is_visible_diffable_acknowledgeable_and_reversible(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(AW, "base_dir", return_value=Path(td)):
            AW.ensure_scaffold()
            spec = next(x for x in AW._template_specs() if x[0] == "rely/技能/写论文与综述.md")
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
        self.assertNotIn("window.confirm(", js)


if __name__ == "__main__":
    unittest.main()

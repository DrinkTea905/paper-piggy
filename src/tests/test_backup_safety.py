# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import backup as B


def _manifest(n_files=1, includes_index=False):
    return {
        "format": B.FORMAT,
        "app_version": "test",
        "wiki_schema": None,
        "created": "2026-07-22 00:00:00",
        "includes_index": includes_index,
        "has_api_key": False,
        "counts": {},
        "n_files": n_files,
    }


class BackupSafetyTests(unittest.TestCase):
    def test_backup_refuses_link_or_reparse_root(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(B, "_is_link_or_reparse", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "链接或重解析点"):
                list(B._iter_files(Path(td)))

    def test_inspect_rejects_unknown_or_traversal_member(self):
        with tempfile.TemporaryDirectory() as td:
            zpath = Path(td) / "bad.zip"
            with zipfile.ZipFile(zpath, "w") as z:
                z.writestr("manifest.json", json.dumps(_manifest()))
                z.writestr("data/wiki/../../../outside.md", "bad")
            got = B.inspect(zpath)
            self.assertFalse(got["ok"])
            self.assertIn("不安全", got["err"])

    def test_create_never_finalizes_a_backup_with_a_missing_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            data.mkdir()
            (data / "valuable.json").write_text("valuable", encoding="utf-8")
            out = root / "backups"
            with mock.patch.object(B.C, "DATA", data), \
                    mock.patch.object(B, "CORE_IN_DATA", ["valuable.json"]), \
                    mock.patch.object(B, "CORE_IN_HOME", []), \
                    mock.patch.object(B, "INDEX_IN_DATA", []), \
                    mock.patch.object(B, "backup_dir", return_value=out), \
                    mock.patch.object(B, "_sanitized_settings", return_value=(None, False)), \
                    mock.patch.object(zipfile.ZipFile, "write", side_effect=OSError("locked")):
                with self.assertRaisesRegex(RuntimeError, "无法读取备份文件"):
                    B.create()
            self.assertEqual([], list(out.glob("*.zip")))

    def test_cancelled_backup_removes_part_file_and_never_finalizes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            data.mkdir()
            (data / "valuable.json").write_text("valuable", encoding="utf-8")
            out = root / "backups"
            checks = {"n": 0}

            def should_cancel():
                checks["n"] += 1
                return checks["n"] >= 3

            with mock.patch.object(B.C, "DATA", data), \
                    mock.patch.object(B, "CORE_IN_DATA", ["valuable.json"]), \
                    mock.patch.object(B, "CORE_IN_HOME", []), \
                    mock.patch.object(B, "INDEX_IN_DATA", []), \
                    mock.patch.object(B, "backup_dir", return_value=out), \
                    mock.patch.object(B, "_sanitized_settings", return_value=(None, False)):
                with self.assertRaises(B.BackupCancelled):
                    B.create(should_cancel=should_cancel)
            self.assertEqual([], list(out.glob("*.zip")))
            self.assertEqual([], list(out.glob("*.part")))

    def test_restore_stages_then_moves_old_data_to_recoverable_stash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            old = data / "meta" / "paper.txt"
            old.parent.mkdir(parents=True)
            old.write_text("old", encoding="utf-8")
            zpath = root / "good.zip"
            with zipfile.ZipFile(zpath, "w") as z:
                z.writestr("manifest.json", json.dumps(_manifest()))
                z.writestr("data/meta/paper.txt", "new")
            with mock.patch.object(B.C, "DATA", data), \
                    mock.patch.object(B, "CORE_IN_DATA", ["meta"]), \
                    mock.patch.object(B, "CORE_IN_HOME", []), \
                    mock.patch.object(B, "INDEX_IN_DATA", []):
                got = B.restore(zpath)
            self.assertTrue(got["ok"], got)
            self.assertEqual("new", old.read_text(encoding="utf-8"))
            stash = Path(got["stash"])
            self.assertEqual("old", (stash / "data" / "meta" / "paper.txt").read_text(encoding="utf-8"))

    def test_restore_includes_sanitized_settings_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            data.mkdir()
            (data / "settings.json").write_text('{"backend":"local"}', encoding="utf-8")
            package = root / "settings.zip"
            with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("manifest.json", json.dumps(_manifest(n_files=0)))
                z.writestr("data/settings.json", '{"backend":"api","api":{"key":""}}')
            with mock.patch.object(B.C, "DATA", data), \
                    mock.patch.object(B, "CORE_IN_DATA", []), \
                    mock.patch.object(B, "CORE_IN_HOME", []), \
                    mock.patch.object(B, "INDEX_IN_DATA", []):
                result = B.restore(package)
            self.assertTrue(result["ok"], result)
            restored = json.loads((data / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(restored["backend"], "api")
            old_path = Path(result["stash"]) / "data" / "settings.json"
            self.assertEqual(json.loads(old_path.read_text(encoding="utf-8"))["backend"], "local")


if __name__ == "__main__":
    unittest.main()

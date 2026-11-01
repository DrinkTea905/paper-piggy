import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup_archive as A
import backup_jobs as J
from backup_jobs import BackupError, BackupCancelled, defaults


class BackupArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / "source"
        self.source.mkdir()
        self.out = self.base / "out"
        self.job = defaults("zotero", "test-machine")
        self.job["destination"] = str(self.out)
        self.roots = [{"id": "zotero_data", "source": str(self.source)}]

    def make(self, text="original"):
        (self.source / "paper.pdf").write_text(text)
        return A.create(self.job, self.roots)

    def rewrite(self, result, change_manifest=None, extra=None, replace=None):
        p = Path(result["path"])
        with zipfile.ZipFile(p) as z:
            values = {n: z.read(n) for n in z.namelist()}
        if change_manifest:
            m = json.loads(values["manifest.json"])
            change_manifest(m)
            values["manifest.json"] = json.dumps(m).encode()
        values.update(extra or {})
        values.update(replace or {})
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
            for n, data in values.items():
                z.writestr(n, data)
        return p

    def test_independent_complete_archive_and_original_source(self):
        self.roots[0]["original_source"] = str(self.base / "original-volume")
        r = self.make()
        info = A.inspect(r["path"])
        self.assertTrue(info["ok"])
        self.assertEqual(info["n_entries"], 1)
        self.assertEqual(info["manifest"]["roots"][0]["original_source"], self.roots[0]["original_source"])
        self.assertEqual(info["manifest"]["files"][0]["sha256"], hashlib.sha256(b"original").hexdigest())

    def test_include_exclude_and_overlay_preserve_stored_content(self):
        (self.source / "wiki").mkdir()
        (self.source / "wiki/page.md").write_text("page")
        (self.source / "wiki/cache").mkdir()
        (self.source / "wiki/cache/data").write_text("cache")
        (self.source / "settings.json").write_text('{"key":"private"}')
        (self.source / "unselected").mkdir()
        (self.source / "unselected/private").write_text("not included")
        self.roots[0].update(include=["wiki", "settings.json"], exclude=["wiki/cache"],
                             overlay_files={"settings.json": b'{"key":""}'})
        r = A.create(self.job, self.roots)
        with zipfile.ZipFile(r["path"]) as z:
            self.assertEqual(z.read("roots/zotero_data/settings.json"), b'{"key":""}')
            self.assertNotIn("roots/zotero_data/wiki/cache/data", z.namelist())
            self.assertNotIn("roots/zotero_data/unselected/private", z.namelist())
        self.assertIn("private", (self.source / "settings.json").read_text())

    def test_corrupt_sqlite_fails_without_removing_existing_success(self):
        good = self.make()
        (self.source / "zotero.sqlite").write_bytes(b"not a database")
        with self.assertRaises(BackupError):
            A.create(self.job, self.roots)
        self.assertTrue(Path(good["path"]).exists())
        self.assertEqual(len(list(self.out.glob("*.zip"))), 1)
        self.assertFalse(list(self.out.glob("*.part")))

    def test_nested_sqlite_wal_family_validates_without_changing_source(self):
        nested = self.source / "plugin"
        nested.mkdir()
        db = nested / "notes.sqlite"
        conn = sqlite3.connect(db)
        self.addCleanup(conn.close)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE notes (v TEXT)")
        conn.execute("INSERT INTO notes VALUES ('saved')")
        conn.commit()
        before = {p.name: p.read_bytes() for p in nested.iterdir()}
        r = A.create(self.job, self.roots)
        checked = A.inspect(r["path"])
        self.assertEqual(checked["database_checks"][0]["path"], "roots/zotero_data/plugin/notes.sqlite")
        self.assertEqual(before, {p.name: p.read_bytes() for p in nested.iterdir()})

    def test_direct_first_sqlite_archive_creates_checked_work_parent(self):
        conn = sqlite3.connect(self.source / "zotero.sqlite")
        try:
            conn.execute("CREATE TABLE notes(value TEXT)")
            conn.execute("INSERT INTO notes VALUES ('first-run')")
            conn.commit()
        finally:
            conn.close()
        work = self.base / "never-created/parent/work"
        self.assertFalse(work.exists())
        result = A.create(self.job, self.roots, work_dir=work)
        self.assertTrue(result["ok"])
        self.assertEqual(result["database_count"], 1)
        self.assertTrue(work.is_dir())
        self.assertFalse(list(work.iterdir()))
        self.assertEqual(A.inspect(result["path"], work_dir=work)["database_checks"][0]["integrity_check"], "ok")

    def test_file_tampering_is_detected_even_with_valid_zip_crc(self):
        r = self.make()
        path = self.rewrite(r, replace={"roots/zotero_data/paper.pdf": b"tampered"})
        with self.assertRaises(BackupError):
            A.inspect(path)

    def test_extensionless_browser_sqlite_and_corrupt_wal(self):
        db = self.source / "Cookies"
        conn = sqlite3.connect(db)
        self.addCleanup(conn.close)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE cookies (v TEXT)")
        conn.execute("INSERT INTO cookies VALUES ('session')")
        conn.commit()
        before = {p.name: p.read_bytes() for p in self.source.iterdir()}
        self.roots[0]["id"] = "browser"
        self.job["kind"] = "paperpiggy"
        good = A.create(self.job, self.roots)
        self.assertEqual(A.inspect(good["path"])["database_checks"][0]["path"], "roots/browser/Cookies")
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.source.iterdir()})
        wal = self.source / "Cookies-wal"
        broken = bytearray(wal.read_bytes())
        broken[60] ^= 0x5A
        wal.write_bytes(broken)
        with self.assertRaisesRegex(BackupError, "WAL"):
            A.create(self.job, self.roots)
        self.assertTrue(Path(good["path"]).is_file())
        broken = bytearray(before["Cookies-wal"])
        broken[40] ^= 0x5A  # 已提交帧 salt 损坏不能伪装成 reset 后的旧尾帧。
        wal.write_bytes(broken)
        with self.assertRaisesRegex(BackupError, "WAL"):
            A.create(self.job, self.roots)

    def test_archive_kind_cannot_hide_another_app_root(self):
        (self.source / "paper.pdf").write_text("original")
        result = A._create_v2(self.job, self.roots)
        path = self.rewrite(result, change_manifest=lambda manifest: manifest.update(kind="paperpiggy"))
        with self.assertRaisesRegex(BackupError, "类型与来源"):
            A.inspect(path)

    def test_known_browser_database_bad_header_cannot_pass_as_ordinary_file(self):
        self.roots[0]["id"] = "browser"
        self.job["kind"] = "paperpiggy"
        (self.source / "Cookies").write_bytes(b"damaged database header")
        with self.assertRaisesRegex(BackupError, "文件头无效"):
            A.create(self.job, self.roots)
        (self.source / "Cookies").unlink()
        (self.source / "opaque").write_bytes(b"damaged database header")
        (self.source / "opaque-wal").write_bytes(b"wal")
        with self.assertRaisesRegex(BackupError, "文件头无效"):
            A.create(self.job, self.roots)

    @unittest.skipUnless(os.name == "nt", "Windows VSS设备路径语义")
    def test_only_mapped_snapshot_root_can_use_bounded_source_guard(self):
        source = r"\\?\GLOBALROOT\Device\HarddiskVolumeShadowCopy37\Users\Fixture"
        trusted = J._snapshot_source(source)
        roots = [{"id": "browser", "source": source, "_snapshot_source": trusted}]
        seen = []
        with patch.object(J, "is_reparse", side_effect=lambda p: seen.append(str(p)) or False), \
             patch.object(J.os.path, "exists", return_value=True), patch.object(A.os.path, "isdir", return_value=True), \
             patch.object(A.os, "walk", return_value=[]):
            files, dirs = A._walk_roots(roots)
        self.assertEqual(files, [])
        self.assertEqual(dirs, ["roots/browser"])
        self.assertTrue(seen[-1].endswith("HarddiskVolumeShadowCopy37\\"))
        self.assertFalse(any(p.endswith("\\Device") or p.endswith("\\GLOBALROOT") for p in seen))
        for token in (None, True, {"source": source}):
            root = {"id": "browser", "source": source}
            if token is not None:
                root["_snapshot_source"] = token
            with self.assertRaises(BackupError):
                A._walk_roots([root])

    def test_device_namespace_destination_rejected_even_for_trusted_source(self):
        self.job["destination"] = r"\\?\GLOBALROOT\Device\HarddiskVolumeShadowCopy37\backups"
        with self.assertRaisesRegex(BackupError, "设备命名空间"):
            A.create(self.job, self.roots)

    def test_custom_sqlite_collation_failure_is_explicit(self):
        good = self.make()
        conn = sqlite3.connect(self.source / "plugin.sqlite")
        conn.create_collation("pluginCustom", lambda a, b: (a > b) - (a < b))
        conn.executescript("CREATE TABLE t(v TEXT COLLATE pluginCustom); CREATE INDEX ix ON t(v);")
        conn.execute("INSERT INTO t VALUES ('a')")
        conn.commit(); conn.close()
        with self.assertRaisesRegex(BackupError, "缺少原程序的排序规则"):
            A.create(self.job, self.roots)
        self.assertTrue(Path(good["path"]).is_file())
        self.assertEqual(len(list(self.out.glob("*.zip"))), 1)

    def test_unknown_extra_file_and_path_traversal_rejected(self):
        for extra in ({"outside.txt": b"x"}, {"roots/zotero_data/../../escape": b"x"}):
            with self.subTest(extra=extra):
                r = self.make()
                path = self.rewrite(r, extra=extra)
                with self.assertRaises(BackupError):
                    A.inspect(path)

    def test_case_collision_rejected(self):
        r = self.make()
        path = self.rewrite(r, extra={"roots/zotero_data/PAPER.pdf": b"x"})
        with self.assertRaises(BackupError):
            A.inspect(path)

    def test_symlink_zip_member_rejected(self):
        r = self.make()
        with zipfile.ZipFile(r["path"], "a") as z:
            info = zipfile.ZipInfo("roots/zotero_data/link")
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            z.writestr(info, "/outside")
        with self.assertRaises(BackupError):
            A.inspect(r["path"])

    def test_cancel_keeps_success_and_never_promotes_partial(self):
        good = self.make()
        called = []
        with self.assertRaises(BackupCancelled):
            A.create(self.job, self.roots, progress=lambda d, t: called.append(d), cancel=lambda: bool(called))
        self.assertTrue(Path(good["path"]).exists())
        self.assertFalse(list(self.out.glob("*.part")))

    def test_space_insufficient_never_deletes_old_version(self):
        good = self.make()
        with patch.object(A.shutil, "disk_usage", return_value=type("Usage", (), {"free": 0})()):
            with self.assertRaises(BackupError):
                A.create(self.job, self.roots)
        self.assertTrue(Path(good["path"]).exists())

    def test_hardlink_source_archived_and_restored_as_independent_files(self):
        (self.source / "paper.pdf").write_bytes(b"x")
        try:
            os.link(self.source / "paper.pdf", self.source / "linked.pdf")
        except OSError:
            self.skipTest("本机不允许测试硬链接")
        result = A.create(self.job, self.roots)
        self.assertEqual(A.inspect(result["path"])["n_entries"], 2)
        import backup_restore as R
        restored = self.base / "restored"
        R.extract_to(result["path"], restored)
        original = restored / "zotero_data/paper.pdf"
        linked = restored / "zotero_data/linked.pdf"
        self.assertEqual(original.read_bytes(), b"x")
        self.assertEqual(linked.read_bytes(), b"x")
        self.assertFalse(os.path.samefile(original, linked))
        linked.write_bytes(b"changed")
        self.assertEqual(original.read_bytes(), b"x")
        self.assertEqual((self.source / "paper.pdf").read_bytes(), b"x")

    def test_destination_inside_source_or_historical_directory_rejected(self):
        for dest in (self.source / "backup", self.base / "过时备份"):
            self.job["destination"] = str(dest)
            with self.assertRaises(BackupError):
                self.make()

    def test_windows_device_and_invalid_paths_rejected(self):
        for name in ("roots/browser/CON.txt", "roots/browser/COM¹", "roots/browser/a?b",
                     "roots/browser/file. ", "roots/browser/a\x01b"):
            with self.subTest(name=name), self.assertRaises(BackupError):
                A.safe_name(name)

    def test_default_pp_destination_inside_unselected_data_is_allowed(self):
        home = self.base / "app"
        data = home / "data"
        (data / "wiki").mkdir(parents=True)
        (data / "wiki/page.md").write_text("page")
        (home / "output").mkdir()
        (home / "output/paper.md").write_text("paper")
        self.job["kind"] = "paperpiggy"
        self.job["destination"] = str(data / "backups")
        result = A.create(self.job, [
            {"id": "paperpiggy_data", "source": str(data), "include": ["wiki"], "exclude": ["backups"]},
            {"id": "paperpiggy_home", "source": str(home), "include": ["output"]}])
        self.assertTrue(Path(result["path"]).is_file())
        self.assertEqual(len(A.inspect(result["path"])["manifest"]["files"]), 2)

    def test_destination_in_selected_prefix_is_rejected(self):
        (self.source / "wiki").mkdir()
        self.job["destination"] = str(self.source / "wiki" / "backup")
        with self.assertRaises(BackupError):
            A.create(self.job, [{"id": "paperpiggy_data", "source": str(self.source), "include": ["wiki"]}])


if __name__ == "__main__":
    unittest.main()

import json
from pathlib import Path
import sys
import sqlite3
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup_archive as A
import backup_restore as R
from backup_jobs import BackupError, defaults, atomic_json, io_path


class BackupRestoreV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.data = self.base / "data-source"
        self.profile = self.base / "profile-source"
        self.data.mkdir()
        self.profile.mkdir()
        (self.data / "paper.pdf").write_text("backup paper")
        (self.profile / "prefs.js").write_text('user_pref("sync.autoSync", true);')
        job = defaults("zotero", "test-machine")
        job["destination"] = str(self.base / "out")
        self.archive = A.create(job, [{"id": "zotero_data", "source": str(self.data)},
                                      {"id": "zotero_profile", "source": str(self.profile)}])

    def live(self):
        data, profile = self.base / "live-data", self.base / "live-profile"
        data.mkdir()
        profile.mkdir()
        (data / "current").write_text("current data")
        (profile / "current").write_text("current profile")
        return {"zotero_data": str(data), "zotero_profile": str(profile)}

    def test_extract_new_directory_preserves_all_originals_and_sync_setting(self):
        target = self.base / "new"
        result = R.extract_to(self.archive["path"], target)
        self.assertTrue(result["ok"])
        self.assertEqual((target / "zotero_data/paper.pdf").read_text(), "backup paper")
        self.assertIn("true", (target / "zotero_profile/prefs.js").read_text())
        self.assertTrue((self.data / "paper.pdf").exists())

    def test_first_restore_workdir_is_created_for_new_directory_and_in_place(self):
        conn = sqlite3.connect(self.data / "zotero.sqlite")
        try:
            conn.execute("CREATE TABLE notes(value TEXT)")
            conn.execute("INSERT INTO notes VALUES ('verified static restore')")
            conn.commit()
        finally:
            conn.close()
        job = defaults("zotero", "test-machine")
        job["destination"] = str(self.base / "out")
        self.archive = A.create(job, [{"id": "zotero_data", "source": str(self.data)},
                                      {"id": "zotero_profile", "source": str(self.profile)}])
        new_work = self.base / "missing-new-work/parent/work"
        self.assertFalse(new_work.exists())
        new = R.extract_to(self.archive["path"], self.base / "new-with-work", work_dir=new_work)
        self.assertTrue(new["ok"])
        self.assertTrue(new_work.is_dir())
        self.assertEqual((self.base / "new-with-work/zotero_data/zotero.sqlite").read_bytes(),
                         (self.data / "zotero.sqlite").read_bytes())
        targets = self.live()
        inplace_work = self.base / "missing-inplace-work/parent/work"
        self.assertFalse(inplace_work.exists())
        restored = R.restore_in_place(self.archive["path"], targets, confirm=True,
                                      ensure_stopped=lambda m, t: True, work_dir=inplace_work)
        self.assertTrue(restored["ok"])
        self.assertTrue(inplace_work.is_dir())
        self.assertFalse(list(inplace_work.iterdir()))

    def test_restore_workdir_file_is_rejected_without_changing_original_targets(self):
        work = self.base / "not-a-work-directory"
        work.write_text("keep")
        targets = self.live()
        with self.assertRaisesRegex(BackupError, "不是目录"):
            R.restore_in_place(self.archive["path"], targets, confirm=True,
                               ensure_stopped=lambda m, t: True, work_dir=work)
        self.assertEqual(work.read_text(), "keep")
        self.assertEqual((Path(targets["zotero_data"]) / "current").read_text(), "current data")

    def _long_root(self, label):
        top = self.base / label
        self.assertIn(self.base, top.parents)
        self.addCleanup(lambda: shutil.rmtree(io_path(top), ignore_errors=False) if os.path.exists(io_path(top)) else None)
        result = top
        for number in range(5):
            result /= str(number) + "x" * 53
        self.assertGreater(len(str(result)), 260)
        return result

    @unittest.skipUnless(os.name == "nt", "Windows extended-path实际I/O")
    def test_long_new_restore_creates_real_files_without_changing_public_paths(self):
        target = self._long_root("new-long")
        result = R.extract_to(self.archive["path"], target)
        self.assertTrue(result["ok"])
        self.assertEqual(result["destination"], str(target))
        self.assertFalse(result["destination"].startswith("\\\\?\\"))
        paper = Path(io_path(target / "zotero_data/paper.pdf"))
        self.assertGreater(len(str(target / "zotero_data/paper.pdf")), 260)
        self.assertEqual(paper.read_text(), "backup paper")
        manifest = Path(io_path(target / "restore-manifest.json"))
        self.assertTrue(manifest.is_file())
        self.assertEqual(R._logical(io_path(target)), target)

    @unittest.skipUnless(os.name == "nt", "Windows extended-path实际I/O")
    def test_long_inplace_work_journal_rollback_and_rename_use_io_paths_only(self):
        parent = self._long_root("inplace-long")
        work = self._long_root("work-long")
        targets = {"zotero_data": str(parent / "data"), "zotero_profile": str(parent / "profile")}
        for path in targets.values():
            folder = Path(io_path(path))
            folder.mkdir(parents=True)
            (folder / "original").write_text("keep original")
        result = R.restore_in_place(self.archive["path"], targets, confirm=True,
                   ensure_stopped=lambda m, t: True, work_dir=work, journal_dir=parent / "journals")
        self.assertTrue(result["ok"])
        self.assertEqual(result["roots"], targets)
        self.assertFalse(result["journal"].startswith("\\\\?\\"))
        self.assertEqual(Path(io_path(Path(targets["zotero_data"]) / "paper.pdf")).read_text(), "backup paper")
        self.assertTrue(all((Path(io_path(p)) / "original").read_text() == "keep original" for p in result["rollback_dirs"]))
        self.assertTrue(Path(io_path(result["journal"])).is_file())
        self.assertFalse(list(Path(io_path(work)).iterdir()))

    @unittest.skipUnless(os.name == "nt", "Windows extended-path实际I/O")
    def test_long_inplace_failure_recovers_both_originals(self):
        parent = self._long_root("failure-long")
        targets = {"zotero_data": str(parent / "data"), "zotero_profile": str(parent / "profile")}
        for path in targets.values():
            folder = Path(io_path(path)); folder.mkdir(parents=True)
            (folder / "original").write_text("preserved")
        rename, failed = Path.rename, []
        def fail_once(path, destination):
            if path.name.startswith(".profile.paperpiggy-stage-") and not failed:
                failed.append(True)
                raise OSError("long path rollback fixture")
            return rename(path, destination)
        with patch.object(Path, "rename", fail_once):
            with self.assertRaisesRegex(BackupError, "rolled_back"):
                R.restore_in_place(self.archive["path"], targets, confirm=True,
                          ensure_stopped=lambda m, t: True, journal_dir=parent / "journals")
        self.assertTrue(all((Path(io_path(path)) / "original").read_text() == "preserved" for path in targets.values()))

    def test_nonempty_target_not_modified(self):
        target = self.base / "existing"
        target.mkdir()
        (target / "note").write_text("keep")
        with self.assertRaises(BackupError):
            R.extract_to(self.archive["path"], target)
        self.assertEqual((target / "note").read_text(), "keep")

    def test_invalid_archive_does_not_remove_selected_empty_target(self):
        target = self.base / "empty"
        target.mkdir()
        bad = self.base / "bad.zip"
        bad.write_bytes(b"bad")
        with self.assertRaises(BackupError):
            R.extract_to(bad, target)
        self.assertTrue(target.is_dir())

    def test_confirmation_and_stopped_process_check_are_required(self):
        targets = self.live()
        for kwargs in ({}, {"confirm": True}, {"confirm": True, "ensure_stopped": lambda m, t: False}):
            with self.assertRaises(BackupError):
                R.restore_in_place(self.archive["path"], targets, **kwargs)
        self.assertEqual((Path(targets["zotero_data"]) / "current").read_text(), "current data")

    def test_in_place_keeps_rollback_and_original_sync_setting(self):
        targets = self.live()
        result = R.restore_in_place(self.archive["path"], targets, confirm=True,
                                    ensure_stopped=lambda m, t: True)
        self.assertTrue(result["ok"])
        self.assertEqual((Path(targets["zotero_data"]) / "paper.pdf").read_text(), "backup paper")
        self.assertIn("true", (Path(targets["zotero_profile"]) / "prefs.js").read_text())
        self.assertEqual(len(result["rollback_dirs"]), 2)
        self.assertTrue(all((Path(p) / "current").exists() for p in result["rollback_dirs"]))
        self.assertEqual(json.loads(Path(result["journal"]).read_text())["state"], "completed")

    def test_failure_on_second_volume_rolls_back_both_targets(self):
        targets = self.live()
        rename = Path.rename
        failed = []

        def fail_second(path, destination):
            if path.name.startswith(".live-profile.paperpiggy-stage-") and not failed:
                failed.append(True)
                raise OSError("simulated second-volume failure")
            return rename(path, destination)

        with patch.object(Path, "rename", fail_second):
            with self.assertRaisesRegex(BackupError, "rolled_back"):
                R.restore_in_place(self.archive["path"], targets, confirm=True,
                                   ensure_stopped=lambda m, t: True)
        self.assertEqual((Path(targets["zotero_data"]) / "current").read_text(), "current data")
        self.assertEqual((Path(targets["zotero_profile"]) / "current").read_text(), "current profile")

    def test_prepare_can_map_new_paths_without_changing_archive(self):
        targets = self.live()
        original = A.file_sha256(self.archive["path"])

        def prepare(staging, manifest, mapping):
            p = staging / "roots/zotero_profile/prefs.js"
            p.write_text(p.read_text() + '\nuser_pref("dataDir", "new-dir");')

        R.restore_in_place(self.archive["path"], targets, confirm=True,
                           ensure_stopped=lambda m, t: True, prepare=prepare)
        self.assertIn("new-dir", (Path(targets["zotero_profile"]) / "prefs.js").read_text())
        self.assertEqual(A.file_sha256(self.archive["path"]), original)

    def test_paperpiggy_partial_root_preserves_unselected_assets(self):
        source = self.base / "pp-source"
        (source / "wiki").mkdir(parents=True)
        (source / "wiki/page").write_text("restored")
        job = defaults("paperpiggy", "test-machine")
        job["destination"] = str(self.base / "out")
        archive = A.create(job, [{"id": "paperpiggy_data", "source": str(source), "include": ["wiki"]}])
        live = self.base / "pp-live"
        (live / "wiki").mkdir(parents=True)
        (live / "wiki/old").write_text("old")
        (live / "index").mkdir()
        (live / "index/value").write_text("keep existing index")
        R.restore_in_place(archive["path"], {"paperpiggy_data": str(live)}, confirm=True,
                           ensure_stopped=lambda m, t: True)
        self.assertEqual((live / "wiki/page").read_text(), "restored")
        self.assertEqual((live / "index/value").read_text(), "keep existing index")

    def test_prepare_can_exclude_current_backup_jobs_from_restore(self):
        source = self.base / "pp-source"
        (source / "backup_jobs").mkdir(parents=True)
        (source / "backup_jobs/jobs.json").write_text("old-job-config")
        (source / "wiki").mkdir()
        (source / "wiki/page").write_text("restored")
        job = defaults("paperpiggy", "test-machine")
        job["destination"] = str(self.base / "out")
        archive = A.create(job, [{"id": "paperpiggy_data", "source": str(source),
                                 "include": ["wiki", "backup_jobs"]}])
        live = self.base / "pp-live"
        (live / "backup_jobs").mkdir(parents=True)
        (live / "backup_jobs/jobs.json").write_text("current-job-config")

        def prepare(staging, manifest, targets):
            manifest["roots"][0]["include"].remove("backup_jobs")

        R.restore_in_place(archive["path"], {"paperpiggy_data": str(live)}, confirm=True,
                           ensure_stopped=lambda m, t: True, prepare=prepare)
        self.assertEqual((live / "backup_jobs/jobs.json").read_text(), "current-job-config")


if __name__ == "__main__":
    unittest.main()

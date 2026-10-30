from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup_jobs as J
import backup_archive as A
import backup_runner as RUN


class BackupJobsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.store = J.JobStore(self.base / "jobs", machine_id="test-machine")
        self.jobs = self.store.ensure_defaults()
        self.job = next(j for j in self.jobs if j["kind"] == "zotero")
        self.source = self.base / "source"
        self.source.mkdir()
        (self.source / "file").write_text("data")
        self.job = self.store.save(self.job["job_id"], {
            "destination": str(self.base / "out"),
            "sources": {"data_dir": str(self.source), "profile_dir": str(self.source)},
            "every_days": 1})

    def archive(self):
        result = A.create(self.job, [{"id": "zotero_data", "source": str(self.source)}])
        self.store.record_success(self.job["job_id"], result)
        return result

    def test_defaults_separate_and_zotero_keep_two(self):
        self.assertEqual({j["kind"] for j in self.jobs}, {"zotero", "paperpiggy"})
        self.assertEqual(self.job["keep"], 2)
        self.assertFalse(self.job["enabled"])
        self.assertEqual(self.store.ensure_defaults(), self.store.list())

    def test_legacy_paperpiggy_settings_preserve_selected_options(self):
        store = J.JobStore(self.base / "migration", machine_id="test-machine")
        old = {"auto": True, "dir": str(self.base / "old-backups"), "every_days": 3,
               "keep": 4, "include_index": True, "include_key": True,
               "at_time": "09:18", "catch_up": False}
        rows = store.ensure_defaults(old)
        job = next(j for j in rows if j["kind"] == "paperpiggy")
        self.assertTrue(job["enabled"])
        for key in ("every_days", "keep", "include_index", "include_key", "at_time", "catch_up"):
            self.assertEqual(job[key], old[key])

    def test_cannot_enable_without_destination_or_zotero_sources(self):
        with self.assertRaises(J.BackupError):
            self.store.save(self.job["job_id"], {"enabled": True, "destination": ""})
        with self.assertRaises(J.BackupError):
            self.store.save(self.job["job_id"], {"enabled": True, "sources": {}})

    def test_invalid_setting_never_overwrites_previous_configuration(self):
        before = self.store.get(self.job["job_id"])
        for patch_value in ({"keep": 0}, {"at_time": "25:00"}, {"enabled": "false"}):
            with self.assertRaises(J.BackupError):
                self.store.save(self.job["job_id"], patch_value)
        self.assertEqual(before, self.store.get(self.job["job_id"]))

    def test_claim_cross_thread_prevents_double_start(self):
        errors = []
        with self.store.claim(self.job["job_id"]):
            def attempt():
                try:
                    with self.store.claim(self.job["job_id"]):
                        pass
                except J.BackupBusy:
                    errors.append(True)
            thread = threading.Thread(target=attempt)
            thread.start()
            thread.join()
            self.store.finish(self.job["job_id"], "cancelled")
        self.assertEqual(errors, [True])

    def test_failure_persisted_and_previous_success_retained(self):
        result = self.archive()
        success = self.store.status(self.job["job_id"])["last_success"]
        with self.assertRaisesRegex(ValueError, "failure"):
            with self.store.claim(self.job["job_id"], slot="timer:test"):
                raise ValueError("failure")
        restarted = J.JobStore(self.base / "jobs", machine_id="test-machine")
        status = restarted.status(self.job["job_id"])
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["last_success"], success)
        self.assertTrue(Path(result["path"]).exists())

    def test_retention_same_second_keeps_newest_registration(self):
        results = [self.archive() for _ in range(3)]
        self.assertFalse(Path(results[0]["path"]).exists())
        self.assertTrue(all(Path(r["path"]).exists() for r in results[1:]))
        self.assertEqual(len(self.store.ledger(self.job["job_id"])), 2)

    def test_unknown_and_historical_files_not_adopted_or_deleted(self):
        first = self.archive()
        unknown = Path(first["path"]).with_name("PaperPiggy-backup-very-old.zip")
        unknown.write_bytes(Path(first["path"]).read_bytes())
        history = Path(self.job["destination"]) / "过时备份"
        history.mkdir()
        (history / "old.zip").write_bytes(b"untouched")
        self.archive()
        self.archive()
        self.assertTrue(unknown.exists())
        self.assertEqual((history / "old.zip").read_bytes(), b"untouched")

    def test_modified_registered_archive_is_retained_with_warning(self):
        old = self.archive()
        with Path(old["path"]).open("ab") as f:
            f.write(b"user-note")
        self.archive()
        result = A.create(self.job, [{"id": "zotero_data", "source": str(self.source)}])
        final = self.store.record_success(self.job["job_id"], result)
        self.assertTrue(final["warnings"])
        self.assertTrue(Path(old["path"]).exists())
        self.assertEqual(self.store.status(self.job["job_id"])["state"], "success_with_warning")

    def test_other_source_versions_never_pruned_by_new_source(self):
        first = self.archive()
        another = self.base / "another"
        another.mkdir()
        (another / "x").write_text("x")
        for _ in range(3):
            result = A.create(self.job, [{"id": "zotero_data", "source": str(another)}])
            self.store.record_success(self.job["job_id"], result)
        self.assertTrue(Path(first["path"]).exists())

    def test_damaged_recent_pack_does_not_count_toward_valid_retention(self):
        older = self.archive()
        recent = self.archive()
        with Path(recent["path"]).open("ab") as f:
            f.write(b"modified")
        current = self.archive()
        self.assertTrue(Path(older["path"]).exists())
        self.assertTrue(Path(recent["path"]).exists())
        self.assertTrue(Path(current["path"]).exists())

    def test_status_and_history_do_not_duplicate_large_manifest(self):
        result = self.archive()
        status = self.store.status(self.job["job_id"])
        history = self.store.history(self.job["job_id"])
        self.assertNotIn("manifest", status["result"])
        self.assertNotIn("files", json.dumps(history))
        self.assertEqual(status["result"]["file_count"], 1)
        self.assertIn("manifest", result)

    def test_due_failure_not_repeated_each_minute_but_new_login_can_retry(self):
        with patch.object(J, "now_text", return_value="2026-10-29T06:00:00+00:00"):
            self.store.save(self.job["job_id"], {"enabled": True, "at_time": "07:00"})
        now = datetime(2026, 10, 30, 8, 0, tzinfo=timezone.utc)
        due = self.store.due(self.job["job_id"], now)
        self.assertTrue(due["due"])
        with self.store.claim(self.job["job_id"], slot=due["slot"]):
            self.store.finish(self.job["job_id"], "failed", "offline")
        self.assertFalse(self.store.due(self.job["job_id"], now + timedelta(minutes=1))["due"])
        self.assertTrue(self.store.due(self.job["job_id"], now, trigger="login", login_id="new-session")["due"])

    def test_login_or_manual_attempt_does_not_forget_failed_timer_slot(self):
        with patch.object(J, "now_text", return_value="2026-10-29T06:00:00+00:00"):
            self.store.save(self.job["job_id"], {"enabled": True, "at_time": "07:00"})
        now = datetime(2026, 10, 30, 8, tzinfo=timezone.utc)
        timer = self.store.due(self.job["job_id"], now)
        with self.store.claim(self.job["job_id"], slot=timer["slot"]):
            self.store.finish(self.job["job_id"], "failed", "offline")
        login = self.store.due(self.job["job_id"], now, trigger="login", login_id="new-session")
        with self.store.claim(self.job["job_id"], trigger="login", slot=login["slot"]):
            self.store.finish(self.job["job_id"], "failed", "offline")
        self.assertFalse(self.store.due(self.job["job_id"], now + timedelta(minutes=1))["due"])
        with self.store.claim(self.job["job_id"], trigger="manual"):
            self.store.finish(self.job["job_id"], "failed", "offline")
        self.assertFalse(self.store.due(self.job["job_id"], now + timedelta(minutes=2))["due"])
        self.assertFalse(self.store.due(self.job["job_id"], now, trigger="login", login_id="new-session")["due"])

    def test_stale_running_status_recovers_by_lock_not_pid(self):
        with patch.object(J, "now_text", return_value="2026-10-29T06:00:00+00:00"):
            self.store.save(self.job["job_id"], {"enabled": True})
        self.store.set_status(self.job["job_id"], running=True, state="running", pid=999999)
        due = self.store.due(self.job["job_id"], datetime(2026, 10, 30, 8, tzinfo=timezone.utc))
        self.assertTrue(due["due"])
        self.assertEqual(self.store.status(self.job["job_id"])["state"], "interrupted")

    def test_queued_request_not_claimed_as_stale_or_scheduled_again(self):
        self.store.save(self.job["job_id"], {"enabled": True})
        self.store.set_status(self.job["job_id"], running=True, pending=True,
                              operation="restore", state="queued")
        with patch.object(self.store, "finish") as finish:
            result = self.store.due(self.job["job_id"], datetime(2026, 10, 30, 8, tzinfo=timezone.utc))
        self.assertFalse(result["due"])
        self.assertEqual(result["reason"], "pending")
        finish.assert_not_called()
        with self.store.claim(self.job["job_id"]):
            self.assertFalse(self.store.status(self.job["job_id"])["pending"])
            self.store.finish(self.job["job_id"], "success")
        self.assertFalse(self.store.status(self.job["job_id"])["pending"])

    def test_existing_defaults_do_not_rewrite_jobs_json_during_polling(self):
        before = (self.base / "jobs/jobs.json").stat().st_mtime_ns
        with patch.object(J, "atomic_json") as write:
            self.store.ensure_defaults()
            self.store.ensure_defaults()
        write.assert_not_called()
        self.assertEqual((self.base / "jobs/jobs.json").stat().st_mtime_ns, before)

    @unittest.skipUnless(os.name == "nt", "Windows VSS设备路径语义")
    def test_snapshot_source_parent_chain_stops_at_volume_with_trailing_separator(self):
        volume = r"\\?\GLOBALROOT\Device\HarddiskVolumeShadowCopy37"
        path = volume + r"\Users\Fixture\profile"
        seen = []
        def lstat(value):
            text = os.fspath(value)
            seen.append(text)
            if text in (volume, r"\\?\GLOBALROOT\Device", r"\\?\GLOBALROOT"):
                raise OSError(6, "invalid namespace/root handle")
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0)
        with patch.object(J.os, "lstat", side_effect=lstat), patch.object(J.os.path, "exists", return_value=True):
            self.assertEqual(J.check_snapshot_source(path, J._snapshot_source(path)), Path(path))
        self.assertEqual(seen, [path, volume + r"\Users\Fixture", volume + r"\Users", volume + "\\"])
        seen.clear()
        with patch.object(J.os, "lstat", side_effect=lstat), patch.object(J.os.path, "exists", return_value=True):
            J.check_snapshot_source(volume, J._snapshot_source(volume))
        self.assertEqual(seen, [volume + "\\"])

    @unittest.skipUnless(os.name == "nt", "Windows VSS设备路径语义")
    def test_snapshot_internal_junction_and_forged_capability_are_rejected(self):
        source = r"\\?\GLOBALROOT\Device\HarddiskVolumeShadowCopy37\Users\Fixture"
        trusted = J._snapshot_source(source)
        with patch.object(J, "is_reparse", side_effect=lambda p: str(p).endswith("\\Users")):
            with self.assertRaisesRegex(J.BackupError, "目录联接"):
                J.check_snapshot_source(source, trusted)
        for token in (True, {"source": source}, J._snapshot_source(source + "\\other")):
            with self.assertRaisesRegex(J.BackupError, "受信任"):
                J.check_snapshot_source(source, token)
        with self.assertRaises(J.BackupError):
            J._snapshot_source(source + r"\..\..\..\escape")

    def test_device_namespace_never_accepted_by_normal_path_guard(self):
        for value in (r"\\?\GLOBALROOT\Device\HarddiskVolumeShadowCopy37\folder",
                      r"\\.\PhysicalDrive0", "//?/GLOBALROOT/Device/HarddiskVolumeShadowCopy37/folder"):
            with patch.object(J, "is_reparse") as stat_path:
                with self.assertRaisesRegex(J.BackupError, "设备命名空间"):
                    J.check_path(value)
            stat_path.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows普通extended-drive路径")
    def test_normal_extended_drive_path_allowed_without_device_namespace_bypass(self):
        extended = J.io_path(self.source)
        self.assertTrue(extended.startswith("\\\\?\\"))
        self.assertEqual(J.check_path(extended, allow_missing=False), Path(extended))
        self.assertEqual(J.check_path(self.source, allow_missing=False), self.source)

    def test_restored_foreign_jobs_get_new_disabled_local_tasks_without_taking_ownership(self):
        old = self.archive()
        self.store.set_status(self.job["job_id"], running=True, pending=True, state="queued", operation="backup")
        state_path = self.store.base / "status" / (self.job["job_id"] + ".json")
        ledger_path = self.store.base / "ledger" / (self.job["job_id"] + ".json")
        before = {p: p.read_bytes() for p in (state_path, ledger_path, Path(old["path"]))}
        foreign_jobs = self.store.list()
        second = J.JobStore(self.store.base, machine_id="different-computer")
        all_jobs = second.ensure_defaults({"auto": True, "dir": str(self.base / "old-destination"), "keep": 9})
        local = [j for j in all_jobs if j["machine_id"] == second.machine_id]
        self.assertEqual(len(local), 2)
        self.assertEqual({j["kind"] for j in local}, {"paperpiggy", "zotero"})
        self.assertTrue(all(not j["enabled"] and j["destination"] == "" for j in local))
        self.assertTrue({j["job_id"] for j in local}.isdisjoint({j["job_id"] for j in foreign_jobs}))
        self.assertEqual([j for j in second.list() if j["machine_id"] == self.store.machine_id], foreign_jobs)
        self.assertEqual(RUN.get_job(second, "zotero")["machine_id"], second.machine_id)
        for action in (lambda: second.save(self.job["job_id"], {"enabled": False}),
                       lambda: second.cancel(self.job["job_id"]),
                       lambda: second.prune(self.job["job_id"]),
                       lambda: second.set_status(self.job["job_id"], running=False)):
            with self.assertRaises(J.BackupError):
                action()
        self.assertFalse(second.due(self.job["job_id"])["due"])
        second.note_login("new-machine-login")
        pp = RUN.get_job(second, "paperpiggy")
        configured = second.save(pp["job_id"], {"enabled": True, "destination": str(self.base / "new-backups")})
        self.assertTrue(configured["enabled"])
        self.assertEqual(configured["machine_id"], second.machine_id)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def _enable_at(self, timestamp, **values):
        with patch.object(J, "now_text", return_value=timestamp):
            return self.store.save(self.job["job_id"], {"enabled": True, "at_time": "07:00", **values})

    def test_missed_first_backup_is_due_before_next_daily_clock(self):
        self._enable_at("2026-10-29T06:00:00+00:00")
        due = self.store.due(self.job["job_id"], datetime(2026, 10, 30, 6, tzinfo=timezone.utc),
                             trigger="login", login_id="boot2")
        self.assertTrue(due["due"])
        self.assertEqual(due["planned_at"], "2026-10-29T07:00:00+00:00")

    def test_reenable_starts_with_next_planned_clock_not_disabled_history(self):
        self._enable_at("2026-10-25T06:00:00+00:00")
        self.store.set_status(self.job["job_id"], last_success="2026-10-25T07:00:00+00:00")
        with patch.object(J, "now_text", return_value="2026-10-26T06:00:00+00:00"):
            self.store.save(self.job["job_id"], {"enabled": False})
        self._enable_at("2026-10-30T08:00:00+00:00")
        due = self.store.due(self.job["job_id"], datetime(2026, 10, 30, 9, tzinfo=timezone.utc),
                             trigger="login", login_id="new")
        self.assertFalse(due["due"])
        self.assertEqual(due["next_at"], "2026-10-31T07:00:00+00:00")

    def test_no_catchup_skips_previous_session_but_runs_late_within_session(self):
        self._enable_at("2026-10-29T06:00:00+00:00", catch_up=False)
        login = datetime(2026, 10, 30, 6, tzinfo=timezone.utc)
        self.store.note_login("new", login)
        self.assertFalse(self.store.due(self.job["job_id"], login, trigger="login", login_id="new")["due"])
        later = datetime(2026, 10, 30, 7, 20, tzinfo=timezone.utc)
        due = self.store.due(self.job["job_id"], later, trigger="timer")
        self.assertTrue(due["due"])
        self.assertEqual(due["planned_at"], "2026-10-30T07:00:00+00:00")

    def test_no_catchup_login_after_clock_cannot_be_bypassed_by_next_tick(self):
        self._enable_at("2026-10-29T06:00:00+00:00", catch_up=False)
        login = datetime(2026, 10, 30, 8, tzinfo=timezone.utc)
        self.assertFalse(self.store.due(self.job["job_id"], login, trigger="login", login_id="new")["due"])
        later = login + timedelta(minutes=5)
        due = self.store.due(self.job["job_id"], later, trigger="timer")
        self.assertFalse(due["due"])
        self.assertEqual(due["next_at"], "2026-10-31T07:00:00+00:00")


if __name__ == "__main__":
    unittest.main()

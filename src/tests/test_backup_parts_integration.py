"""成套恢复点与轮换测试；仅使用临时静态来源，不运行真实 VSS 或系统任务。"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup_archive as A
import backup_jobs as J
import backup_legacy as L
import backup_restore as R


class BackupPartsIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.data = self.base / "source"
        self.profile = self.base / "profile"
        self.data.mkdir()
        self.profile.mkdir()
        self.payload = os.urandom(145000)
        (self.data / "large.bin").write_bytes(self.payload)
        (self.profile / "prefs.js").write_text('user_pref("sync.autoSync", true);', encoding="utf-8")
        with closing(sqlite3.connect(self.data / "zotero.sqlite")) as db:
            with db:
                db.execute("CREATE TABLE notes(value TEXT)")
                db.execute("INSERT INTO notes VALUES ('one whole recovery point')")
        self.store = J.JobStore(self.base / "jobs", machine_id="parts-fixture")
        self.store.ensure_defaults()
        raw = next(j for j in self.store.list() if j["kind"] == "zotero")
        self.job = self.store.save(raw["job_id"], {"destination": str(self.base / "backups"), "keep": 2})
        self.roots = [{"id": "zotero_data", "source": str(self.data)},
                      {"id": "zotero_profile", "source": str(self.profile)}]

    def create(self, register=True):
        result = A.create(self.job, self.roots, max_part_size=64 * 1024)
        self.assertGreater(result["part_count"], 1)
        if register:
            result = self.store.record_success(self.job["job_id"], result)
        return result

    @staticmethod
    def members(result):
        return [Path(p["path"]) for p in result["parts"]] + [Path(result["index_path"])]

    def snapshots(self, result):
        return {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.members(result)}

    def assert_snapshot(self, snapshot):
        for path, value in snapshot.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), value, str(path))

    def test_success_row_counts_whole_set_and_keeps_history_small(self):
        result = self.create()
        row, = self.store.ledger(self.job["job_id"])
        self.assertEqual(row["part_count"], len(result["parts"]))
        self.assertEqual(row["archive_format"], 3)
        self.assertEqual(row["parts"], result["parts"])
        self.assertEqual(row["size"], sum(p.stat().st_size for p in self.members(result)))
        self.assertEqual(row["total_size"], row["size"])
        self.assertEqual(row["index_sha256"], A.file_sha256(result["index_path"]))
        summary = self.store.status(self.job["job_id"])["result"]
        self.assertEqual(summary["part_count"], row["part_count"])
        self.assertEqual(summary["index_path"], row["index_path"])
        self.assertNotIn("parts", summary)
        self.assertNotIn("manifest", summary)
        self.assertEqual(L.format_of(result["parts"][-1]["path"]), 3)

    def test_any_part_restores_complete_files_and_database(self):
        result = self.create()
        target = self.base / "restored"
        R.extract_to(result["parts"][-1]["path"], target)
        self.assertEqual((target / "zotero_data/large.bin").read_bytes(), self.payload)
        self.assertEqual((target / "zotero_profile/prefs.js").read_text(),
                         'user_pref("sync.autoSync", true);')
        with closing(sqlite3.connect(target / "zotero_data/zotero.sqlite")) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
            self.assertEqual(db.execute("SELECT value FROM notes").fetchone()[0], "one whole recovery point")

    def test_any_part_in_place_restore_keeps_original_rollback(self):
        result = self.create()
        live = self.base / "live"
        live.mkdir()
        (live / "current").write_text("keep rollback")
        restored = R.restore_in_place(result["parts"][-1]["path"], {"zotero_data": str(live)},
                                      confirm=True, ensure_stopped=lambda *args: True)
        self.assertEqual((live / "large.bin").read_bytes(), self.payload)
        self.assertEqual((Path(restored["rollback_dirs"][0]) / "current").read_text(), "keep rollback")

    def test_missing_part_rejected_before_new_target_is_created(self):
        result = self.create()
        Path(result["parts"][0]["path"]).unlink()
        target = self.base / "must-not-exist"
        with self.assertRaises(J.BackupError):
            R.extract_to(result["parts"][-1]["path"], target)
        self.assertFalse(target.exists())

    def test_all_parts_and_index_are_protected_from_in_place_targets(self):
        result = self.create()
        before = self.snapshots(result)
        for victim in (result["parts"][0]["path"], result["index_path"]):
            with self.subTest(victim=victim), self.assertRaisesRegex(J.BackupError, "分包或总清单"):
                R.restore_in_place(result["parts"][-1]["path"], {"zotero_data": victim},
                                   confirm=True, ensure_stopped=lambda *args: True)
        self.assert_snapshot(before)

    def test_legacy_single_zip_and_multipart_share_two_point_retention(self):
        legacy = A._create_v2(self.job, self.roots)
        self.store.record_success(self.job["job_id"], legacy)
        self.assertEqual(L.format_of(legacy["path"]), 2)
        first = self.create()
        self.assertTrue(Path(legacy["path"]).exists())
        second = self.create()
        self.assertFalse(Path(legacy["path"]).exists())
        self.assertEqual(len(self.store.ledger(self.job["job_id"])), 2)
        self.assertTrue(all(p.exists() for p in self.members(first) + self.members(second)))

    def test_existing_format_two_single_zip_still_restores_without_conversion(self):
        legacy = A._create_v2(self.job, self.roots)
        before = A.file_sha256(legacy["path"])
        target = self.base / "legacy-v2-restore"
        restored = R.extract_to(legacy["path"], target)
        self.assertTrue(restored["ok"])
        self.assertEqual((target / "zotero_data/large.bin").read_bytes(), self.payload)
        self.assertEqual(A.file_sha256(legacy["path"]), before)
        self.assertEqual(list(Path(self.job["destination"]).iterdir()), [Path(legacy["path"])])

    def test_three_multipart_sets_keep_two_whole_sets(self):
        old = self.create()
        first = self.create()
        second = self.create()
        self.assertFalse(any(p.exists() for p in self.members(old)))
        self.assertTrue(all(p.exists() for p in self.members(first) + self.members(second)))
        self.assertEqual(len(self.store.ledger(self.job["job_id"])), 2)

    def test_missing_or_modified_old_set_never_deleted_or_counted_as_valid(self):
        old = self.create()
        Path(old["parts"][1]["path"]).unlink()
        remaining = {p: A.file_sha256(p) for p in self.members(old) if p.exists()}
        middle = self.create()
        recent = self.create()
        self.assertTrue(recent["warnings"])
        self.assert_snapshot(remaining)
        self.assertTrue(all(p.exists() for p in self.members(middle) + self.members(recent)))
        self.assertEqual(len(self.store.ledger(self.job["job_id"])), 3)

    def test_unregistered_complete_new_set_cannot_trigger_old_deletion(self):
        old, middle = self.create(), self.create()
        before = self.snapshots(old) | self.snapshots(middle)
        candidate = self.create(register=False)
        atomic = J.atomic_json
        def fail_registration(path, value):
            if Path(path).parent.name == "ledger":
                raise OSError("fixture ledger commit failed")
            return atomic(path, value)
        with patch.object(J, "atomic_json", fail_registration):
            with self.assertRaisesRegex(OSError, "ledger commit"):
                self.store.record_success(self.job["job_id"], candidate)
        self.assert_snapshot(before)
        self.assertTrue(A.inspect(candidate["parts"][-1]["path"])["ok"])
        self.assertEqual(len(self.store.ledger(self.job["job_id"])), 2)

    def test_staging_move_failure_rolls_back_every_member_and_retries(self):
        old, middle = self.create(), self.create()
        before = self.snapshots(old) | self.snapshots(middle)
        candidate = self.create(register=False)
        rename = J.os.rename
        attempts = []
        def fail_second(source, target):
            if ".pruning-" in os.fspath(target):
                attempts.append(source)
                if len(attempts) == 2:
                    raise PermissionError("fixture move failed")
            return rename(source, target)
        with patch.object(J.os, "rename", fail_second):
            result = self.store.record_success(self.job["job_id"], candidate)
        self.assertTrue(result["warnings"])
        self.assert_snapshot(before)
        self.assertFalse(self.store.ledger(self.job["job_id"])[0].get("cleanup_pending", False))
        self.assertFalse(list((self.store.base / "prune_journals").glob("*.json")))
        self.assertFalse(self.store.prune(self.job["job_id"]))
        self.assertFalse(any(p.exists() for p in self.members(old)))

    def test_partial_delete_is_not_a_successful_restore_point_and_resumes(self):
        old, middle = self.create(), self.create()
        candidate = self.create(register=False)
        unlink = Path.unlink
        attempts = []
        def fail_second(path, *args, **kwargs):
            if ".pruning-" in path.name:
                attempts.append(str(path))
                if len(attempts) == 2:
                    raise PermissionError("fixture unlink failed")
            return unlink(path, *args, **kwargs)
        with patch.object(Path, "unlink", fail_second):
            result = self.store.record_success(self.job["job_id"], candidate)
        self.assertTrue(result["warnings"])
        rows = self.store.ledger(self.job["job_id"])
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[0]["cleanup_pending"])
        self.assertFalse(any(p.exists() for p in self.members(old)))
        self.assertTrue(list((self.store.base / "prune_journals").glob("*.json")))
        self.assertFalse(self.store.prune(self.job["job_id"]))
        self.assertEqual(len(self.store.ledger(self.job["job_id"])), 2)
        self.assertFalse(list(Path(self.job["destination"]).glob("*.pruning-*")))
        self.assertFalse(list((self.store.base / "prune_journals").glob("*.json")))
        self.assertTrue(all(p.exists() for p in self.members(middle) + self.members(candidate)))

    def test_process_interruption_during_moves_recovers_whole_old_set(self):
        old, middle = self.create(), self.create()
        candidate = self.create(register=False)
        rename = J.os.rename
        attempts = []
        def interrupt_second(source, target):
            if ".pruning-" in os.fspath(target):
                attempts.append(source)
                if len(attempts) == 2:
                    raise KeyboardInterrupt("fixture process interruption")
            return rename(source, target)
        with patch.object(J.os, "rename", interrupt_second), self.assertRaises(KeyboardInterrupt):
            self.store.record_success(self.job["job_id"], candidate)
        self.assertTrue(self.store.ledger(self.job["job_id"])[0]["cleanup_pending"])
        self.assertFalse(self.store.prune(self.job["job_id"]))
        self.assertEqual(len(self.store.ledger(self.job["job_id"])), 2)
        self.assertFalse(any(p.exists() for p in self.members(old)))
        self.assertTrue(all(p.exists() for p in self.members(middle) + self.members(candidate)))

    def test_forged_cleanup_journal_cannot_delete_an_outside_file(self):
        old, middle = self.create(), self.create()
        candidate = self.create(register=False)
        journal_path = self.store._cleanup_path(self.job["job_id"], old["manifest"]["backup_id"])
        valuable = self.base / "valuable"
        valuable.write_text("do not touch")
        J.atomic_json(journal_path, {"schema": 1, "job_id": self.job["job_id"],
            "backup_id": old["manifest"]["backup_id"], "machine_id": self.store.machine_id,
            "destination": self.job["destination"], "transaction_id": "a" * 32,
            "phase": "deleting", "members": [{"path": str(valuable), "size": valuable.stat().st_size,
                                                "sha256": A.file_sha256(valuable), "staged": str(valuable)}]})
        result = self.store.record_success(self.job["job_id"], candidate)
        self.assertTrue(result["warnings"])
        self.assertEqual(valuable.read_text(), "do not touch")
        self.assertTrue(all(p.exists() for p in self.members(old) + self.members(middle)))

    def test_cleanup_ledger_write_failure_is_warning_and_retries_safely(self):
        old, middle = self.create(), self.create()
        candidate = self.create(register=False)
        atomic = J.atomic_json
        def fail_final_ledger(path, value):
            if Path(path).parent.name == "ledger" and len(value) == 2:
                raise OSError("fixture final cleanup ledger commit")
            return atomic(path, value)
        with patch.object(J, "atomic_json", fail_final_ledger):
            result = self.store.record_success(self.job["job_id"], candidate)
        self.assertTrue(result["warnings"])
        self.assertEqual(self.store.status(self.job["job_id"])["state"], "success_with_warning")
        self.assertEqual(len(self.store.ledger(self.job["job_id"])), 3)
        self.assertTrue(self.store.ledger(self.job["job_id"])[0]["cleanup_pending"])
        self.assertFalse(any(p.exists() for p in self.members(old)))
        self.assertTrue(all(p.exists() for p in self.members(middle) + self.members(candidate)))
        self.assertFalse(self.store.prune(self.job["job_id"]))
        self.assertEqual(len(self.store.ledger(self.job["job_id"])), 2)

    def test_retained_point_changed_after_hashing_does_not_allow_old_deletion(self):
        old, middle = self.create(), self.create()
        candidate = self.create(register=False)
        before = self.snapshots(old)
        original = A.archive_files
        def change_retained_after_old_check(path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            if Path(path) == Path(old["path"]):
                with Path(candidate["parts"][0]["path"]).open("ab") as f:
                    f.write(b"changed after the retained hash check")
            return info
        with patch.object(A, "archive_files", change_retained_after_old_check):
            result = self.store.record_success(self.job["job_id"], candidate)
        self.assertTrue(result["warnings"])
        self.assert_snapshot(before)
        self.assertEqual(len(self.store.ledger(self.job["job_id"])), 3)

    def test_task_destination_changed_during_staging_restores_original_set(self):
        old, middle = self.create(), self.create()
        candidate = self.create(register=False)
        before = self.snapshots(old)
        rename = J.os.rename
        changed = []
        def change_destination(source, target):
            value = rename(source, target)
            if ".pruning-" in os.fspath(target) and not changed:
                changed.append(True)
                self.store.save(self.job["job_id"], {"destination": str(self.base / "different-destination")})
            return value
        with patch.object(J.os, "rename", change_destination):
            result = self.store.record_success(self.job["job_id"], candidate)
        self.assertTrue(result["warnings"])
        self.assert_snapshot(before)
        self.assertFalse(list(Path(self.job["destination"]).glob("*.pruning-*")))

    def test_same_length_retained_change_with_restored_mtime_stops_cleanup(self):
        old, middle = self.create(), self.create()
        candidate = self.create(register=False)
        before = self.snapshots(old)
        original = A.archive_files
        def change_without_metadata_difference(path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            if Path(path) == Path(old["path"]):
                p = Path(candidate["parts"][0]["path"])
                st = p.stat()
                with p.open("r+b") as f:
                    f.seek(60)
                    value = f.read(1)
                    f.seek(60)
                    f.write(bytes([value[0] ^ 1]))
                os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
            return info
        with patch.object(A, "archive_files", change_without_metadata_difference):
            result = self.store.record_success(self.job["job_id"], candidate)
        self.assertTrue(result["warnings"])
        self.assert_snapshot(before)
        self.assertEqual(len(self.store.ledger(self.job["job_id"])), 3)

    @unittest.skipUnless(os.name == "nt", "Windows stable sharing lease")
    def test_retained_read_leases_deny_writes_until_old_deletion_finishes(self):
        old, middle = self.create(), self.create()
        candidate = self.create(register=False)
        original = self.store._delete_set
        denied = []
        def attempt_write_inside_cleanup(*args, **kwargs):
            with self.assertRaises(PermissionError):
                Path(candidate["path"]).open("r+b")
            with self.assertRaises(PermissionError):
                Path(middle["index_path"]).unlink()
            denied.append(True)
            return original(*args, **kwargs)
        with patch.object(self.store, "_delete_set", attempt_write_inside_cleanup):
            result = self.store.record_success(self.job["job_id"], candidate)
        self.assertEqual(denied, [True])
        self.assertFalse(result["warnings"])
        self.assertFalse(any(p.exists() for p in self.members(old)))

    def test_keep_increased_before_staging_never_hides_old_set(self):
        old, middle = self.create(), self.create()
        candidate = self.create(register=False)
        before = self.snapshots(old)
        original = self.store._delete_set
        def raise_keep_before_entry(*args, **kwargs):
            self.store.save(self.job["job_id"], {"keep": 3})
            return original(*args, **kwargs)
        with patch.object(self.store, "_delete_set", raise_keep_before_entry):
            result = self.store.record_success(self.job["job_id"], candidate)
        self.assertTrue(result["warnings"])
        self.assert_snapshot(before)
        self.assertFalse(any(x.get("cleanup_pending") for x in self.store.ledger(self.job["job_id"])))
        self.assertFalse(self.store.prune(self.job["job_id"]))
        self.assertEqual(len(self.store.ledger(self.job["job_id"])), 3)

    def test_keep_increased_after_first_move_rolls_back_complete_set(self):
        old, middle = self.create(), self.create()
        candidate = self.create(register=False)
        before = self.snapshots(old)
        rename = J.os.rename
        changed = []
        def raise_keep_after_first_move(source, target):
            result = rename(source, target)
            if ".pruning-" in os.fspath(target) and not changed:
                changed.append(True)
                self.store.save(self.job["job_id"], {"keep": 3})
            return result
        with patch.object(J.os, "rename", raise_keep_after_first_move):
            result = self.store.record_success(self.job["job_id"], candidate)
        self.assertTrue(result["warnings"])
        self.assert_snapshot(before)
        self.assertFalse(list(Path(self.job["destination"]).glob("*.pruning-*")))
        self.assertFalse(any(x.get("cleanup_pending") for x in self.store.ledger(self.job["job_id"])))

    def test_settings_changed_after_deleting_marker_rolls_back_before_first_unlink(self):
        old, middle = self.create(), self.create()
        candidate = self.create(register=False)
        before = self.snapshots(old)
        atomic = J.atomic_json
        changed = []
        def change_after_all_moved(path, value):
            result = atomic(path, value)
            if Path(path).parent.name == "prune_journals" and value.get("phase") == "deleting" and not changed:
                changed.append(True)
                self.store.save(self.job["job_id"], {"destination": str(self.base / "new-location"), "keep": 3})
            return result
        with patch.object(J, "atomic_json", change_after_all_moved):
            result = self.store.record_success(self.job["job_id"], candidate)
        self.assertTrue(result["warnings"])
        self.assert_snapshot(before)
        self.assertFalse(list(Path(self.job["destination"]).glob("*.pruning-*")))
        self.assertFalse(any(x.get("cleanup_pending") for x in self.store.ledger(self.job["job_id"])))

    def test_manual_complete_removal_retires_metadata_without_reclaiming_reappeared_files(self):
        old = self.create()
        old_bytes = {p: p.read_bytes() for p in self.members(old)}
        for p in old_bytes:
            p.unlink()
        middle = self.create()
        self.assertFalse(middle["warnings"])
        row = self.store.ledger(self.job["job_id"])[0]
        self.assertTrue(row["retired"])
        self.assertEqual(row["retired_reason"], "all_files_removed")
        self.assertEqual(row["parts"], old["parts"])
        for p, content in old_bytes.items():
            p.write_bytes(content)
        self.create()
        recent = self.create()
        self.assertFalse(recent["warnings"])
        for p, content in old_bytes.items():
            self.assertEqual(p.read_bytes(), content)
        active = [r for r in self.store.ledger(self.job["job_id"]) if not r.get("retired")]
        self.assertEqual(len(active), 2)
        self.assertTrue(self.store.ledger(self.job["job_id"])[0]["retired"])

    def test_partial_missing_set_is_not_retired_or_deleted(self):
        old = self.create()
        Path(old["parts"][0]["path"]).unlink()
        remaining = {p: A.file_sha256(p) for p in self.members(old) if p.exists()}
        result = self.create()
        self.assertTrue(result["warnings"])
        self.assertFalse(self.store.ledger(self.job["job_id"])[0].get("retired", False))
        self.assert_snapshot(remaining)


if __name__ == "__main__":
    unittest.main()

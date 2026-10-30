"""只使用临时 fixture；不启动真实浏览器、Zotero、VSS 或系统任务。"""
import configparser
import builtins
import json
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from types import SimpleNamespace
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup_archive as A
import backup_runner as R
from backup_jobs import BackupError, BackupBusy, JobStore, atomic_json, paperpiggy_maintenance_lock


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.current = JobStore(self.root / "live/data/backup_jobs", machine_id="fixture")
        self.current.ensure_defaults()

    def job(self, kind):
        job = R.get_job(self.current, kind)
        return self.current.save({**job, "destination": str(self.root / (kind + "-backups")),
                                  "include_browser": False})

    def archive(self, job, rid="zotero_profile", content=None, include=None, metadata=None):
        source = self.root / ("source-" + uuid.uuid4().hex)
        source.mkdir()
        for relative, value in (content or {"prefs.js": 'user_pref("sync.autoSync", true);'}).items():
            file = source / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(value, encoding="utf-8")
        roots = [{"id": rid, "source": str(source), "include": include}]
        return A.create(job, roots, metadata=metadata)

    def test_fake_snapshot_is_only_content_source_and_registered(self):
        job = self.job("paperpiggy")
        live, static = self.root / "active", self.root / "snapshot"
        live.mkdir(); static.mkdir()
        (live / "wiki").mkdir(); (static / "wiki").mkdir()
        (live / "wiki/page.md").write_text("later live write")
        (static / "wiki/page.md").write_text("snapshot content")
        class FakeSnapshot:
            set_id, snapshots = "fixture-set", [{"volume": "fixture"}]
            def __init__(inner, paths, cancel=None):
                self.assertEqual(paths, [str(live)])
            def __enter__(inner): return inner
            def __exit__(inner, *args): return False
            def map_path(inner, path):
                self.assertEqual(path, str(live))
                return static
        roots = [{"id": "paperpiggy_data", "source": str(live), "original_source": str(live), "include": ["wiki"]}]
        with patch("backup_sources.validate_sources", return_value=(roots, {})):
            result = R.run_backup(self.current, job, snapshot_factory=FakeSnapshot)
        with zipfile.ZipFile(result["path"]) as package:
            self.assertEqual(package.read("roots/paperpiggy_data/wiki/page.md"), b"snapshot content")
        self.assertEqual(result["manifest"]["metadata"]["snapshot"]["set_id"], "fixture-set")
        record = self.current.ledger(job["job_id"])[0]
        self.assertEqual(record["include_browser"], False)
        self.assertEqual(self.current.status(job["job_id"])["state"], "success")

    def _static_snapshot_factory(self, mapping):
        flags = {"entered": False, "exited": False}
        class FakeSnapshot:
            set_id, snapshots = "fresh-static-set", [{"volume": "fixture-volume"}]
            def __init__(inner, paths, cancel=None):
                self.assertEqual(set(paths), set(mapping))
            def __enter__(inner):
                flags["entered"] = True
                return inner
            def __exit__(inner, *args):
                flags["exited"] = True
                return False
            def map_path(inner, path):
                return mapping[str(path)]
        return FakeSnapshot, flags

    def _sqlite_fixture(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        try:
            conn.execute("CREATE TABLE notes(value TEXT)")
            conn.execute("INSERT INTO notes VALUES(?)", (value,))
            conn.commit()
        finally:
            conn.close()

    def test_first_paperpiggy_sqlite_backup_runs_real_archive_and_registration_from_no_workdir(self):
        data = self.root / "fresh-pp/data"
        self.assertFalse(data.exists())
        current = JobStore(data / "backup_jobs", machine_id="fixture")
        current.ensure_defaults()
        job = current.save({**R.get_job(current, "paperpiggy"), "include_browser": False,
                            "destination": str(self.root / "fresh-pp-packages")})
        static_data, static_home = self.root / "pp-snapshot/data", self.root / "pp-snapshot/home"
        static_home.mkdir(parents=True)
        database = static_data / "meta/fixture.sqlite"
        self._sqlite_fixture(database, "static complete snapshot")
        before = database.read_bytes()
        factory, flags = self._static_snapshot_factory({str(data): static_data, str(data.parent): static_home})
        self.assertFalse((current.base / "work").exists())
        result = R.run_backup(current, job, snapshot_factory=factory)
        self.assertTrue(result["ok"])
        self.assertEqual(result["database_count"], 1)
        self.assertTrue(flags["entered"] and flags["exited"])
        self.assertTrue((current.base / "work").is_dir())
        self.assertFalse(list((current.base / "work").iterdir()))
        self.assertEqual(len(current.ledger(job["job_id"])), 1)
        self.assertEqual(current.status(job["job_id"])["state"], "success")
        self.assertEqual(database.read_bytes(), before)
        checked = A.inspect(result["path"], work_dir=self.root / "another-missing-parent/work")
        self.assertEqual(checked["database_checks"][0]["path"], "roots/paperpiggy_data/meta/fixture.sqlite")

    def test_first_zotero_sqlite_backup_has_full_source_mapping_archive_hash_and_ledger(self):
        data = self.root / "fresh-zotero-app/data"
        self.assertFalse(data.exists())
        current = JobStore(data / "backup_jobs", machine_id="fixture")
        current.ensure_defaults()
        library, profile = self.root / "selected-library", self.root / "selected-profile"
        static_library, static_profile = self.root / "frozen-library", self.root / "frozen-profile"
        for directory in (library, profile, static_library, static_profile):
            directory.mkdir()
        prefs = 'user_pref("extensions.zotero.dataDir", ' + json.dumps(str(library)) + ');\nuser_pref("sync.autoSync", true);'
        for directory in (profile, static_profile):
            (directory / "prefs.js").write_text(prefs, encoding="utf-8")
        for directory, value in ((library, "live marker"), (static_library, "frozen marker")):
            conn = sqlite3.connect(directory / "zotero.sqlite")
            try:
                conn.executescript("CREATE TABLE items(itemID INTEGER,key TEXT); "
                                   "CREATE TABLE itemAttachments(itemID INTEGER,linkMode INTEGER,path TEXT); "
                                   "CREATE TABLE marker(value TEXT);")
                conn.execute("INSERT INTO marker VALUES(?)", (value,))
                conn.commit()
            finally:
                conn.close()
        self._sqlite_fixture(static_profile / "extensions/plugin/settings.sqlite", "plugin settings")
        job = current.save({**R.get_job(current, "zotero"), "destination": str(self.root / "fresh-zotero-packages"),
                            "sources": {"data_dir": str(library), "profile_dir": str(profile)}})
        live_bytes = (library / "zotero.sqlite").read_bytes()
        static_bytes = (static_library / "zotero.sqlite").read_bytes()
        factory, flags = self._static_snapshot_factory({str(library): static_library, str(profile): static_profile})
        self.assertFalse((current.base / "work").exists())
        with patch("backup_sources._registry_root", return_value=self.root / "absent-registry"):
            result = R.run_backup(current, job, snapshot_factory=factory)
        self.assertTrue(result["ok"])
        self.assertTrue(flags["entered"] and flags["exited"])
        self.assertEqual(result["database_count"], 2)
        self.assertEqual(len(current.ledger(job["job_id"])), 1)
        self.assertEqual(current.status(job["job_id"])["state"], "success")
        with zipfile.ZipFile(result["path"]) as package:
            self.assertEqual(package.read("roots/zotero_data/zotero.sqlite"), static_bytes)
        entry = next(f for f in result["manifest"]["files"] if f["path"] == "roots/zotero_data/zotero.sqlite")
        self.assertEqual(entry["sha256"], hashlib.sha256(static_bytes).hexdigest())
        self.assertEqual((library / "zotero.sqlite").read_bytes(), live_bytes)
        self.assertFalse(list((current.base / "work").iterdir()))

    def test_first_workdir_reparse_guard_fails_before_snapshot_creation(self):
        import backup_jobs
        job = self.job("paperpiggy")
        work = self.current.base / "work"
        real_check = backup_jobs.is_reparse
        with patch.object(backup_jobs, "is_reparse", side_effect=lambda path: Path(path) == work or real_check(path)), \
             patch("backup_vss.snapshot") as snapshot:
            with self.assertRaisesRegex(BackupError, "目录联接"):
                R.run_backup(self.current, job)
        snapshot.assert_not_called()
        self.assertFalse(self.current.ledger(job["job_id"]))

    def test_vss_failure_never_falls_back_or_deletes_success(self):
        job = self.job("paperpiggy")
        old = self.archive(job, rid="paperpiggy_data", content={"wiki/page.md": "old"})
        self.current.record_success(job["job_id"], old)
        def fail(*args, **kwargs):
            raise BackupError("VSS fixture failure")
        with patch("backup_sources.validate_sources", return_value=([{"id": "paperpiggy_data", "source": str(self.root)}], {})), \
             patch("backup_archive.create") as create:
            with self.assertRaisesRegex(BackupError, "VSS fixture failure"):
                R.run_backup(self.current, job, snapshot_factory=fail)
        create.assert_not_called()
        self.assertTrue(Path(old["path"]).is_file())
        self.assertEqual(len(self.current.ledger(job["job_id"])), 1)
        self.assertIn("VSS fixture failure", self.current.status(job["job_id"])["error"])

    def test_build_maintenance_lock_prevents_snapshot_and_records_failure(self):
        job = self.job("paperpiggy")
        with paperpiggy_maintenance_lock(self.current.base.parent), \
             patch("backup_sources.validate_sources") as sources, patch("backup_vss.snapshot") as snapshot:
            with self.assertRaises(BackupBusy):
                R.run_backup(self.current, job)
        sources.assert_not_called(); snapshot.assert_not_called()
        self.assertFalse(self.current.ledger(job["job_id"]))

    def test_timer_late_tick_runs_after_previous_long_request(self):
        job = self.job("paperpiggy")
        with patch("backup_jobs.now_text", return_value="2026-10-29T06:00:00+00:00"):
            self.current.save(job["job_id"], {"enabled": True, "at_time": "07:00"})
        args = SimpleNamespace(login=False, login_id="", data=str(self.current.base.parent))
        with patch.object(R, "process_requests", return_value=True), \
             patch.object(R, "run_backup") as backup, patch.object(R, "datetime") as clock:
            clock.now.return_value = datetime(2026, 10, 30, 7, 12, tzinfo=timezone.utc)
            self.assertEqual(R._run_once(self.current, args), 0)
        self.assertEqual(backup.call_count, 1)
        self.assertEqual(backup.call_args.args[1]["kind"], "paperpiggy")

    def test_new_restore_preserves_original_sync_preferences(self):
        job = self.job("zotero")
        prefs = 'user_pref("extensions.zotero.dataDir", "old");\nuser_pref("sync.autoSync", true);\nuser_pref("plugin.setting", "keep");\n'
        result = self.archive(job, content={"prefs.js": prefs})
        destination = self.root / "new"
        R.run_restore(self.current, job, {"path": result["path"], "destination": str(destination), "mode": "new"})
        value = (destination / "zotero_profile/prefs.js").read_text()
        self.assertEqual(value, prefs)  # 无数据根时不凭空改关联。
        roots = destination
        (roots / "zotero_data").mkdir()
        R._prepare_restored(roots, result["manifest"], {"zotero_data": str(destination / "zotero_data")})
        value = (destination / "zotero_profile/prefs.js").read_text()
        self.assertIn('user_pref("sync.autoSync", true);', value)
        self.assertIn('user_pref("plugin.setting", "keep");', value)
        self.assertIn(json.dumps(str(destination / "zotero_data")), value)

    def test_profile_registration_preserves_case_other_profiles_and_install_binding(self):
        target, staging = self.root / "registry", self.root / "staging"
        target.mkdir(); staging.mkdir()
        original = "[General]\nStartWithLastProfile=0\n[Profile0]\nName=Other\nIsRelative=1\nPath=Profiles/other\nDefault=1\n[InstallABC]\nDefault=Profiles/other\nLocked=1\n"
        (target / "profiles.ini").write_text(original)
        (target / "installs.ini").write_text("[ABC]\nDefault=Profiles/other\nLocked=1\n")
        (staging / "installs.ini").write_text("[Foreign]\nDefault=foreign\n")
        manifest = {"roots": [{"id": "zotero_registry", "include": ["profiles.ini", "installs.ini"]}],
                    "metadata": {"zotero": {"profile_name": "restored"}}}
        selected = self.root / "selected-profile"
        R._prepare_profile_registry(staging, manifest, {"zotero_registry": str(target), "zotero_profile": str(selected)}, True)
        ini = configparser.RawConfigParser(); ini.optionxform = str
        ini.read(staging / "profiles.ini")
        self.assertEqual(ini.get("Profile0", "Default"), "1")
        self.assertEqual(ini.get("Profile0", "Path"), "Profiles/other")
        self.assertEqual(ini.get("Profile1", "Path"), str(selected))
        self.assertFalse(ini.has_option("Profile1", "Default"))
        self.assertEqual(ini.get("General", "StartWithLastProfile"), "0")
        self.assertEqual(ini.get("InstallABC", "Locked"), "1")
        self.assertEqual((staging / "installs.ini").read_bytes(), (target / "installs.ini").read_bytes())

    def test_browser_mapping_preserves_extra_metadata_and_saved_port(self):
        exe = self.root / "chrome.exe"; exe.touch()
        data = self.root / "extracted/paperpiggy_data"; data.mkdir(parents=True)
        cfg = {"schema": 1, "executable": str(exe), "user_data_dir": "old", "port": 9239,
               "source": "existing-user-association", "future_metadata": {"keep": True}}
        (data / "dedicated_browser.json").write_text(json.dumps(cfg))
        manifest = {"roots": [], "metadata": {"browser": {k: cfg[k] for k in ("schema", "executable", "user_data_dir", "port")}}}
        R._prepare_restored(data.parent, manifest, {"browser": str(self.root / "restored-browser")}, str(exe))
        restored = json.loads((data / "dedicated_browser.json").read_text())
        self.assertEqual(restored["port"], 9239)
        self.assertEqual(restored["source"], cfg["source"])
        self.assertEqual(restored["future_metadata"], cfg["future_metadata"])

    def test_in_place_rechecks_mcp_and_keeps_current_jobs_and_rollback(self):
        job = self.job("paperpiggy")
        data = self.current.base.parent
        (data / "wiki").mkdir(); (data / "wiki/page.md").write_text("current")
        result = self.archive(job, rid="paperpiggy_data", content={"wiki/page.md": "restored", "backup_jobs/jobs.json": "[]"},
                              include=["wiki", "backup_jobs"])
        with patch("backup_sources.ensure_paperpiggy_mcp_stopped") as mcp, \
             patch("backup_sources.ensure_restore_stopped"):
            restored = R.run_restore(self.current, job, {"path": result["path"], "mode": "in_place", "confirm": True,
                                    "targets": {"paperpiggy_data": str(data)}, "app_dir": str(self.root / "app")})
        self.assertGreaterEqual(mcp.call_count, 3)
        self.assertEqual((data / "wiki/page.md").read_text(), "restored")
        self.assertTrue((self.current.base / "jobs.json").is_file())
        self.assertTrue(Path(restored["rollback_dirs"][0]).is_dir())
        self.assertFalse(R.active_paperpiggy_restore(data))
        self.assertTrue(list(self.current.base.glob("restored-jobs-*.json")))

    def test_mcp_still_running_prevents_any_original_changes(self):
        job = self.job("paperpiggy")
        data = self.current.base.parent
        (data / "wiki").mkdir(); (data / "wiki/page.md").write_text("keep")
        result = self.archive(job, rid="paperpiggy_data", content={"wiki/page.md": "new"}, include=["wiki"])
        with patch("backup_sources.ensure_paperpiggy_mcp_stopped", side_effect=BackupError("MCP still running")):
            with self.assertRaisesRegex(BackupError, "MCP still running"):
                R.run_restore(self.current, job, {"path": result["path"], "mode": "in_place", "confirm": True,
                            "targets": {"paperpiggy_data": str(data)}, "app_dir": str(self.root / "app")})
        self.assertEqual((data / "wiki/page.md").read_text(), "keep")
        self.assertFalse(list(data.glob(".*paperpiggy*")))

    def test_queue_sets_persistent_gate_before_worker_claim(self):
        job = self.job("paperpiggy")
        with patch.object(R, "store", return_value=self.current):
            req = R.enqueue("paperpiggy", "restore", {"mode": "in_place", "app_dir": str(self.root / "app")}, self.current.base.parent)
        self.assertTrue(R.active_paperpiggy_restore(self.current.base.parent))
        self.assertEqual(json.loads((self.current.base / R.GATE_FILE).read_text())["request_id"], req["request_id"])
        self.assertFalse(self.current.due(job["job_id"])["due"])

    def test_queued_backup_cancel_consumes_request_without_snapshot_or_archive(self):
        job = self.job("paperpiggy")
        with patch.object(R, "store", return_value=self.current):
            request = R.enqueue("paperpiggy", "backup", {}, self.current.base.parent)
        self.current.cancel(job["job_id"])
        with patch.object(R, "run_backup") as run, patch("backup_vss.snapshot") as snapshot, \
             patch("backup_archive.create") as archive:
            R.process_requests(self.current)
        run.assert_not_called(); snapshot.assert_not_called(); archive.assert_not_called()
        self.assertEqual(self.current.status(job["job_id"])["state"], "cancelled")
        self.assertFalse((self.current.base / "requests" / (request["request_id"] + ".json")).exists())

    def test_cancel_racing_after_queue_check_survives_claim(self):
        from backup_jobs import BackupCancelled
        job = self.job("paperpiggy")
        with patch.object(R, "store", return_value=self.current):
            request = R.enqueue("paperpiggy", "backup", {}, self.current.base.parent)
        self.current.cancel(job["job_id"])
        with patch("backup_sources.validate_sources") as sources, patch("backup_vss.snapshot") as snapshot:
            with self.assertRaises(BackupCancelled):
                R.run_backup(self.current, job, request_id=request["request_id"])
        sources.assert_not_called(); snapshot.assert_not_called()
        self.assertEqual(self.current.status(job["job_id"])["state"], "cancelled")

    def test_worker_cannot_reject_request_while_ui_is_publishing_status(self):
        job = self.job("paperpiggy")
        import backup_jobs
        original = backup_jobs.atomic_json
        def write_then_worker(path, value):
            original(path, value)
            if Path(path).parent.name == "requests":
                R.process_requests(self.current)
        with patch.object(R, "store", return_value=self.current), \
             patch("backup_jobs.atomic_json", side_effect=write_then_worker), patch.object(R, "run_backup") as run:
            request = R.enqueue("paperpiggy", "backup", {}, self.current.base.parent)
            run.assert_not_called()
            self.assertTrue((self.current.base / "requests" / (request["request_id"] + ".json")).is_file())
            R.process_requests(self.current)
        run.assert_called_once()

    def test_cancelled_request_does_not_cancel_new_queue(self):
        job = self.job("paperpiggy")
        with patch.object(R, "store", return_value=self.current):
            R.enqueue("paperpiggy", "backup", {}, self.current.base.parent)
            self.current.cancel(job["job_id"])
            R.process_requests(self.current)
            R.enqueue("paperpiggy", "backup", {}, self.current.base.parent)
        self.assertFalse(self.current.status(job["job_id"]).get("cancel_requested"))

    def test_restore_gate_not_cleared_during_atomic_enqueue(self):
        import backup_jobs
        self.job("paperpiggy")
        original = backup_jobs.atomic_json
        def write_then_worker(path, value):
            original(path, value)
            if Path(path).name == R.GATE_FILE:
                self.assertTrue(R.recover_interrupted_restores(self.current))
                self.assertTrue(Path(path).is_file())
        with patch.object(R, "store", return_value=self.current), patch("backup_jobs.atomic_json", side_effect=write_then_worker):
            R.enqueue("paperpiggy", "restore", {"mode": "in_place", "app_dir": str(self.root / "app")}, self.current.base.parent)
        self.assertTrue(R.active_paperpiggy_restore(self.current.base.parent))

    def test_interrupted_directory_exchange_rolls_back_before_gate_opens(self):
        job = self.job("paperpiggy")
        oid, bid, rid = [uuid.uuid4().hex for _ in range(3)]
        data = self.current.base.parent
        live = data / "wiki"; live.mkdir(); (live / "page.md").write_text("new partial")
        rollback = data / (".wiki.paperpiggy-rollback-" + oid)
        rollback.mkdir(); (rollback / "page.md").write_text("original")
        op = {"root_id": "paperpiggy_data", "live": str(live), "stage": str(data / (".wiki.paperpiggy-stage-" + oid)),
              "rollback": str(rollback), "failed_new": str(data / (".wiki.paperpiggy-failed-new-" + oid)),
              "had_original": True, "phase": "installed"}
        directory = self.current.base / "restore_journals"
        atomic_json(directory / ("PaperPiggy-restore-" + oid + ".json"),
                    {"operation_id": oid, "backup_id": bid, "state": "prepared", "operations": [op]})
        context = {"schema": 1, "backup_id": bid, "job_id": job["job_id"], "kind": "paperpiggy", "request_id": rid,
                   "targets": {"paperpiggy_data": str(data)}, "options": {"app_dir": str(self.root / "app")}}
        atomic_json(directory / (bid + ".context.json"), context)
        atomic_json(self.current.base / R.GATE_FILE, {**context, "operation": "restore"})
        atomic_json(self.current.base / "requests" / (rid + ".json"), {"schema": 1})
        self.assertTrue(R.active_paperpiggy_restore(data))
        with patch.object(R, "_ensure_stopped"):
            self.assertTrue(R.recover_interrupted_restores(self.current))
        self.assertEqual((live / "page.md").read_text(), "original")
        self.assertEqual((Path(op["failed_new"]) / "page.md").read_text(), "new partial")
        self.assertFalse(R.active_paperpiggy_restore(data))
        self.assertTrue((self.current.base / "requests" / (rid + ".recovered")).is_file())

    def test_missing_context_or_corrupt_gate_keeps_startup_blocked(self):
        self.job("paperpiggy")
        directory = self.current.base / "restore_journals"
        atomic_json(directory / ("PaperPiggy-restore-" + uuid.uuid4().hex + ".json"),
                    {"backup_id": uuid.uuid4().hex, "state": "prepared", "operations": [{"root_id": "paperpiggy_data"}]})
        self.assertFalse(R.recover_interrupted_restores(self.current))
        self.assertTrue(R.active_paperpiggy_restore(self.current.base.parent))

    def test_startup_gate_never_imports_config_or_creates_directories(self):
        missing = self.root / "not-created"
        original = builtins.__import__
        def restricted(name, *args, **kwargs):
            if name == "config":
                self.fail("startup gate must not initialize config")
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=restricted):
            self.assertFalse(R.active_paperpiggy_restore(missing))
        self.assertFalse(missing.exists())

    def test_existing_config_store_does_not_leak_data_environment(self):
        with patch.dict(os.environ, {"LOCALKB_DATA": "original-environment"}), \
             patch("settings.load", return_value={}):
            self.assertIn("config", sys.modules)
            R.store(self.root / "explicit-other-data")
            self.assertEqual(os.environ["LOCALKB_DATA"], "original-environment")

    def test_failed_recovery_keeps_gate_and_original_rollback(self):
        self.job("paperpiggy")
        atomic_json(self.current.base / R.GATE_FILE, {"schema": 1, "kind": "paperpiggy", "operation": "restore",
                                                    "request_id": uuid.uuid4().hex})
        (self.current.base / "restore_journals").mkdir()
        (self.current.base / "restore_journals/PaperPiggy-restore-bad.json").write_text("{broken")
        self.assertFalse(R.recover_interrupted_restores(self.current))
        self.assertTrue(R.active_paperpiggy_restore(self.current.base.parent))


if __name__ == "__main__":
    unittest.main()

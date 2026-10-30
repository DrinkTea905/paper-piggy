import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup_archive as A
import backup_legacy as L
import backup_runner as R
from backup_jobs import BackupError, JobStore


class LegacyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.current = JobStore(self.root / "data/backup_jobs", machine_id="fixture")
        self.current.ensure_defaults()
        self.job = R.get_job(self.current, "paperpiggy")
        self.path = self.root / "legacy.zip"

    def make(self, include_index=False, has_key=False):
        manifest = {"format": 1, "created": "2026-01-01T00:00:00", "n_files": 1,
                    "includes_index": include_index, "has_api_key": has_key}
        with zipfile.ZipFile(self.path, "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            archive.writestr("data/wiki/page.md", "old page")
            archive.writestr("data/settings.json", '{"api":{"key":""}}')

    def test_old_format_converts_only_in_temp_and_retains_truthful_flags(self):
        self.make(False, False)
        before = self.path.read_bytes()
        with L.resolved_archive(self.path, self.current, {**self.job, "include_index": True, "include_key": True}) as path:
            self.assertNotEqual(Path(path), self.path)
            info = A.inspect(path)
            self.assertFalse(info["manifest"]["include_index"])
            self.assertFalse(info["manifest"]["include_key"])
            self.assertFalse(info["manifest"]["include_browser"])
            self.assertTrue(info["manifest"]["metadata"]["not_a_new_snapshot"])
            self.assertTrue(info["manifest"]["metadata"]["legacy_warnings"])
        self.assertFalse(Path(path).exists())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.current.ledger(self.job["job_id"]))

    def test_legacy_new_restore_keeps_warning_and_original(self):
        self.make()
        destination = self.root / "restored"
        result = R.run_restore(self.current, self.job, {"path": str(self.path), "mode": "new", "destination": str(destination)})
        self.assertEqual((destination / "paperpiggy_data/wiki/page.md").read_text(), "old page")
        self.assertTrue(any("旧格式" in x for x in result["warnings"]))
        self.assertTrue(self.path.is_file())
        self.assertFalse(self.current.ledger(self.job["job_id"]))

    def test_legacy_in_place_restores_only_declared_items_and_keeps_rollback(self):
        self.make()
        data = self.current.base.parent
        (data / "wiki").mkdir(); (data / "wiki/page.md").write_text("current")
        (data / "lancedb").mkdir(); (data / "lancedb/unselected").write_text("keep index")
        with patch.object(R, "_ensure_stopped"):
            result = R.run_restore(self.current, self.job, {"path": str(self.path), "mode": "in_place", "confirm": True,
                                    "targets": {"paperpiggy_data": str(data)}, "app_dir": str(self.root / "app")})
        self.assertEqual((data / "wiki/page.md").read_text(), "old page")
        self.assertEqual((data / "lancedb/unselected").read_text(), "keep index")
        self.assertTrue(Path(result["rollback_dirs"][0]).is_dir())
        self.assertFalse(R.active_paperpiggy_restore(data))

    def test_legacy_path_traversal_and_invalid_container_rejected(self):
        self.make()
        with zipfile.ZipFile(self.path, "a") as archive:
            archive.writestr("data/wiki/../../outside", "malicious")
        with self.assertRaises(BackupError):
            with L.resolved_archive(self.path, self.current, self.job):
                self.fail("must reject before restore")
        self.path.write_text("not zip")
        with self.assertRaisesRegex(BackupError, "有效"):
            L.format_of(self.path)


if __name__ == "__main__":
    unittest.main()

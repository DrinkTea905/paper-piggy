import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup_sources as BS
import backup_jobs as J


class BackupSourcesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_browser_cache_exclusion_does_not_discard_site_data(self):
        for name in ("Default/Cache", "Default/Code Cache", "Default/IndexedDB",
                     "Default/Service Worker/CacheStorage", "Default/Local Storage/Cache",
                     "Profile 2/GPUCache", "component_crx_cache"):
            (self.root / name).mkdir(parents=True)
        self.assertEqual(BS.browser_excludes(self.root),
                         ["Default/Cache", "Default/Code Cache", "Profile 2/GPUCache", "component_crx_cache"])

    def test_profile_matching_uses_data_association(self):
        registry = self.root / "Zotero"
        profile = registry / "Profiles/test.default"
        profile.mkdir(parents=True)
        data = self.root / "Library"
        data.mkdir()
        (data / "zotero.sqlite").touch()
        (profile / "prefs.js").write_text('user_pref("extensions.zotero.dataDir", ' + json.dumps(str(data)) + ');')
        (registry / "profiles.ini").write_text("[Profile0]\nPath=Profiles/test.default\nIsRelative=1\nDefault=1\n")
        with patch.object(BS, "_registry_root", return_value=registry), \
             patch("settings.load", return_value={"zotero_dir": str(data)}), \
             patch.dict("os.environ", {"LOCALKB_ZOTERO_DIR": ""}):
            found = BS.discover_zotero()
        self.assertTrue(found["found"])
        self.assertEqual(found["profile_dir"], str(profile))

    def test_snapshot_settings_sanitized_without_changing_source(self):
        (self.root / "settings.json").write_text(json.dumps({"api": {"key": "secret"}, "other": "keep"}))
        class Snapshot:
            def map_path(_, path): return self.root
        roots = BS.prepare_snapshot_roots({"include_key": False},
                 [{"id": "paperpiggy_data", "source": "/not-read-live"}], Snapshot())
        transformed = json.loads(roots[0]["overlay_files"]["settings.json"])
        self.assertEqual(transformed["api"]["key"], "")
        self.assertEqual(transformed["other"], "keep")
        self.assertEqual(json.loads((self.root / "settings.json").read_text())["api"]["key"], "secret")

    @unittest.skipUnless(sys.platform == "win32", "Windows VSS设备路径语义")
    def test_snapshot_mapper_attaches_in_memory_capability_only_for_vss_device(self):
        mapped = r"\\?\GLOBALROOT\Device\HarddiskVolumeShadowCopy37\Fixture"
        class Snapshot:
            def map_path(_, path): return mapped
        with patch.object(J, "check_snapshot_source", return_value=Path(mapped)):
            roots = BS.prepare_snapshot_roots({"include_key": True},
                       [{"id": "browser", "source": str(self.root)}], Snapshot())
        self.assertIsInstance(roots[0]["_snapshot_source"], J._SnapshotSource)
        self.assertEqual(roots[0]["source"], mapped)
        self.assertEqual(roots[0]["original_source"], str(self.root))
        class FakeSnapshot:
            def map_path(_, path): return self.root
        roots = BS.prepare_snapshot_roots({"include_key": True},
                       [{"id": "browser", "source": str(self.root)}], FakeSnapshot())
        self.assertNotIn("_snapshot_source", roots[0])

    def _attachment_db(self, link_mode=0):
        con = sqlite3.connect(self.root / "zotero.sqlite")
        con.executescript("CREATE TABLE items(itemID INTEGER,key TEXT); "
                          "CREATE TABLE itemAttachments(itemID INTEGER,linkMode INTEGER,path TEXT);")
        con.execute("INSERT INTO items VALUES(1,'ABCDEFGH')")
        con.execute("INSERT INTO itemAttachments VALUES(1,?,?)", (link_mode, "storage:test.pdf"))
        con.commit(); con.close()
        return [{"id": "zotero_data", "source": str(self.root)}]

    def test_snapshot_attachment_coverage(self):
        roots = self._attachment_db()
        (self.root / "storage/ABCDEFGH").mkdir(parents=True)
        file = self.root / "storage/ABCDEFGH/test.pdf"
        file.write_bytes(b"test attachment")
        coverage = BS.validate_zotero_attachments(roots)
        self.assertEqual((coverage["checked"], coverage["existing"], coverage["missing"]), (1, 1, 0))
        file.unlink()
        coverage = BS.validate_zotero_attachments(roots)
        self.assertEqual((coverage["checked"], coverage["existing"], coverage["missing"]), (1, 0, 1))
        self.assertEqual(coverage["missing_files"], [{"key": "ABCDEFGH", "relative_path": "storage/ABCDEFGH/test.pdf"}])

    def test_external_attachment_recorded_without_accessing_or_adding_external_path(self):
        roots = self._attachment_db(2)
        con = sqlite3.connect(self.root / "zotero.sqlite")
        con.execute("UPDATE itemAttachments SET path=?", (str(self.root.parent / "never-read/secret.pdf"),))
        con.commit(); con.close()
        original_stat, stat_paths = Path.stat, []
        def track(path, *args, **kwargs):
            stat_paths.append(str(path))
            return original_stat(path, *args, **kwargs)
        with patch.object(Path, "stat", track):
            coverage = BS.validate_zotero_attachments(roots)
        self.assertEqual((coverage["linked"], coverage["checked"]), (1, 0))
        self.assertTrue(coverage["linked_files"][0]["not_added"])
        self.assertFalse(any("never-read" in path for path in stat_paths))

    def test_linked_url_has_no_local_file_requirement(self):
        roots = self._attachment_db(3)
        coverage = BS.validate_zotero_attachments(roots)
        self.assertEqual((coverage["records_total"], coverage["linked_url"], coverage["checked"], coverage["missing"]), (1, 1, 0, 0))

    def test_unreadable_attachment_is_not_reported_as_preexisting_missing(self):
        roots = self._attachment_db()
        original_stat = Path.stat
        def denied(path, *args, **kwargs):
            if path.name == "test.pdf":
                raise PermissionError("fixture unreadable attachment")
            return original_stat(path, *args, **kwargs)
        with patch.object(Path, "stat", denied):
            with self.assertRaisesRegex(PermissionError, "unreadable"):
                BS.validate_zotero_attachments(roots)

    def test_storage_path_escape_is_still_rejected(self):
        roots = self._attachment_db()
        con = sqlite3.connect(self.root / "zotero.sqlite")
        con.execute("UPDATE itemAttachments SET path='storage:../../outside.pdf'")
        con.commit(); con.close()
        with self.assertRaisesRegex(BS.SourceError, "越过"):
            BS.validate_zotero_attachments(roots)

    def test_regular_hardlinked_attachment_counts_as_existing_without_mutation(self):
        roots = self._attachment_db()
        source = self.root / "original.pdf"; source.write_bytes(b"same source bytes")
        folder = self.root / "storage/ABCDEFGH"; folder.mkdir(parents=True)
        os.link(source, folder / "test.pdf")
        before = (self.root / "zotero.sqlite").read_bytes()
        coverage = BS.validate_zotero_attachments(roots)
        self.assertEqual((coverage["existing"], coverage["missing"]), (1, 0))
        self.assertEqual(source.read_bytes(), b"same source bytes")
        self.assertEqual((self.root / "zotero.sqlite").read_bytes(), before)

    def test_corrupt_static_database_is_not_silently_accepted(self):
        (self.root / "zotero.sqlite").write_bytes(b"corrupt fixture")
        with self.assertRaises(sqlite3.DatabaseError):
            BS.validate_zotero_attachments([{"id": "zotero_data", "source": str(self.root)}])

    def test_missing_browser_association_never_guesses_daily_profile(self):
        self.root.mkdir(exist_ok=True)
        with patch.object(BS, "discover_browser", return_value={"configured": False, "error": "missing"}):
            with self.assertRaisesRegex(BS.SourceError, "missing"):
                BS.validate_sources({"kind": "paperpiggy", "include_browser": True,
                                     "destination": str(self.root.parent / "out")}, self.root)

    def test_destination_inside_source_rejected(self):
        data, profile = self.root / "library", self.root / "profile"
        data.mkdir(); profile.mkdir()
        (data / "zotero.sqlite").touch()
        (profile / "prefs.js").write_text('user_pref("extensions.zotero.dataDir", ' + json.dumps(str(data)) + ');')
        with patch.object(BS, "_registry_root", return_value=self.root / "none"):
            with self.assertRaisesRegex(BS.SourceError, "目的地"):
                BS.validate_sources({"kind": "zotero", "sources": {"data_dir": str(data), "profile_dir": str(profile)},
                                     "destination": str(data / "backup")})

    def test_unknown_browser_process_prevents_overwrite(self):
        with patch.object(BS.sys, "platform", "win32"), \
             patch("dedicated_browser._processes", return_value=[{"unreadable": True}]):
            with self.assertRaisesRegex(BS.SourceError, "无法核对"):
                BS.ensure_restore_stopped("paperpiggy", {"browser": str(self.root)})

    def test_mcp_probe_blocks_same_install_but_keeps_other_install_alone(self):
        app = self.root / "app"
        rows = [{"pid": 101, "name": "pythonw.exe"}]
        with patch.object(BS.sys, "platform", "win32"), patch.object(BS, "_process_entries", return_value=rows), \
             patch("dedicated_browser._process", return_value={"args": ["pythonw.exe", str(app / "mcp_server.py")]}) as probe:
            with self.assertRaisesRegex(BS.SourceError, "MCP 仍在运行"):
                BS.ensure_paperpiggy_mcp_stopped(app)
            probe.return_value = {"args": ["pythonw.exe", str(self.root / "other/mcp_server.py")]}
            BS.ensure_paperpiggy_mcp_stopped(app)
            probe.return_value = None
            with self.assertRaisesRegex(BS.SourceError, "无法核对"):
                BS.ensure_paperpiggy_mcp_stopped(app)

    def test_relative_or_module_mcp_cannot_bypass_probe(self):
        with patch.object(BS.sys, "platform", "win32"), \
             patch.object(BS, "_process_entries", return_value=[{"pid": 1, "name": "python.exe"}]), \
             patch("dedicated_browser._process") as probe:
            for argv in (["python", "mcp_server.py"], ["python", "-m", "mcp_server"]):
                probe.return_value = {"args": argv}
                with self.assertRaises(BS.SourceError):
                    BS.ensure_paperpiggy_mcp_stopped(self.root)

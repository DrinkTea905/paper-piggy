# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import retriever as R
import server


class PurgeDeletedSafetyTests(unittest.TestCase):
    def setUp(self):
        server.BUILD["running"] = False
        server.BACKUP["running"] = False

    @mock.patch("settings.source", return_value="zotero")
    @mock.patch("zotero_source.load_papers")
    @mock.patch.object(server, "_load_papers")
    def test_preview_is_required_before_small_zotero_purge(self, load_indexed, load_live, _source):
        load_indexed.return_value = {f"K{i}": {} for i in range(10)}
        load_live.return_value = [{"key": f"K{i}"} for i in range(9)]
        result = server.purge_deleted(server.PurgeDeletedQ(preview=True))
        self.assertFalse(result["ok"])
        self.assertTrue(result["need_confirm"])
        self.assertEqual(result["would_remove"], 1)

    @mock.patch("settings.source", return_value="zotero")
    @mock.patch("zotero_source.load_papers")
    @mock.patch.object(server, "_load_papers")
    def test_half_library_threshold_is_independent_of_twenty(self, load_indexed, load_live, _source):
        load_indexed.return_value = {f"K{i}": {} for i in range(10)}
        load_live.return_value = [{"key": f"K{i}"} for i in range(4)]
        response = server.purge_deleted(server.PurgeDeletedQ(confirm=True))
        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.body)
        self.assertEqual(payload["would_remove"], 6)

    @mock.patch("settings.save")
    @mock.patch("settings.load", return_value={"auto_update": {"delete_sync": False}})
    def test_enabling_delete_sync_requires_server_side_confirmation(self, _load, save):
        with self.assertRaises(server.HTTPException):
            server.set_auto_update(server.AutoUpdateQ(delete_sync=True))
        save.assert_not_called()
        result = server.set_auto_update(
            server.AutoUpdateQ(delete_sync=True, confirm_delete_sync=True))
        self.assertTrue(result["ok"])


class MaintenanceTransactionTests(unittest.TestCase):
    def setUp(self):
        server.BUILD.update({"running": False, "stage": "", "proc": None, "job": None})
        server.BACKUP["running"] = False

    def tearDown(self):
        server.BUILD.update({"running": False, "stage": "", "proc": None, "job": None})
        server.BACKUP["running"] = False

    def test_build_refuses_while_backup_slot_is_held(self):
        self.assertIsNone(server._claim_backup("测试"))
        self.assertFalse(server._run_build("all"))

    def test_topic_rebuild_refuses_while_backup_slot_is_held(self):
        self.assertIsNone(server._claim_backup("测试"))
        with mock.patch.dict(server.R.STATE, {"mode": "full"}):
            result = server.topics_rebuild()
        self.assertFalse(result["ok"])
        self.assertTrue(result["busy"])

    @mock.patch("settings.source", return_value="zotero")
    def test_manual_build_reports_agent_deep_task_as_exact_busy_reason(self, _source):
        server.BUILD.update({"running": True, "stage": "deep_agent"})
        result = server.build_ep()
        self.assertFalse(result["ok"])
        self.assertTrue(result["busy"])
        self.assertEqual(result["stage"], "deep_agent")
        self.assertIn("Agent", result["msg"])
        self.assertIn("深索", result["msg"])

    @mock.patch.object(server.R, "release_retrieval_if_idle")
    @mock.patch.object(server.R, "retrieval_status")
    def test_manual_memory_release_refuses_active_retrieval(self, status, release):
        status.return_value = {"loaded": True, "loading": False, "active": 2, "idle_s": 0}
        result = server.release_retrieval_memory()
        self.assertFalse(result["ok"])
        self.assertTrue(result["busy"])
        self.assertEqual(result["reason"], "active_retrievals")
        self.assertIn("2 个检索请求", result["msg"])
        release.assert_not_called()

    @mock.patch.object(server, "_retrieval_memory_view")
    @mock.patch.object(server.R, "release_retrieval_if_idle", return_value=True)
    @mock.patch.object(server.R, "retrieval_status")
    def test_manual_memory_release_drops_idle_components(self, status, release, view):
        status.return_value = {"loaded": True, "loading": False, "active": 0, "idle_s": 30}
        view.return_value = {"loaded": False, "loading": False, "active": 0, "idle_s": 0}
        result = server.release_retrieval_memory()
        self.assertTrue(result["ok"])
        self.assertTrue(result["released"])
        release.assert_called_once_with(0, force=True)

    @mock.patch("settings.save")
    @mock.patch("settings.load")
    @mock.patch.object(server, "_reset_vectors_for_reembed", return_value=False)
    @mock.patch.object(server.C, "META_EMBEDDED")
    def test_backend_setting_is_not_saved_when_vector_reset_fails(
            self, embedded_path, _reset, load_settings, save_settings):
        embedded_path.exists.return_value = True
        load_settings.return_value = {
            "backend": "local",
            "api": {"embed_model": "BAAI/bge-m3", "rerank_model": "BAAI/bge-reranker-v2-m3"},
        }
        result = server.setup_backend(server.BackendQ(backend="api"))
        self.assertFalse(result["ok"])
        save_settings.assert_not_called()

    @mock.patch("settings.save")
    @mock.patch("settings.load")
    @mock.patch.object(server, "_reset_vectors_for_reembed", return_value=True)
    @mock.patch.object(server.C, "META_EMBEDDED")
    def test_changing_api_base_with_same_model_forces_reembed(
            self, embedded_path, reset_vectors, load_settings, save_settings):
        embedded_path.exists.return_value = True
        old = {"backend": "api", "api": {"base": "https://old.example/v1",
               "embed_model": "BAAI/bge-m3", "rerank_model": "BAAI/bge-reranker-v2-m3", "key": "x"}}
        load_settings.return_value = old
        save_settings.return_value = {**old, "api": {**old["api"], "base": "https://new.example/v1"}}
        result = server.setup_backend(server.BackendQ(
            backend="api", base="https://new.example/v1", embed_model="BAAI/bge-m3"))
        self.assertTrue(result["ok"])
        self.assertTrue(result["reembed"])
        reset_vectors.assert_called_once()


class RetrieverBackendGuardTests(unittest.TestCase):
    def test_existing_vector_table_refuses_mismatched_backend(self):
        old_state = dict(R.STATE)
        old_m = dict(R.M)
        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                manifest = root / "index_manifest.json"
                manifest.write_text(json.dumps({"backend": "local"}), encoding="utf-8")
                meta = root / "bm25_meta"
                meta.mkdir()
                fake_db = mock.Mock()
                fake_db.table_names.return_value = [R.C.TABLE_NAME]
                with mock.patch.object(R.C, "INDEX_MANIFEST", manifest), \
                     mock.patch.object(R.C, "LANCEDB_DIR", root / "lance"), \
                     mock.patch.object(R.C, "BM25_META_DIR", meta), \
                     mock.patch.object(R.lancedb, "connect", return_value=fake_db), \
                     mock.patch("settings.backend", return_value="api"):
                    R._load_catalog_locked()
                self.assertFalse(R.STATE["ready"])
                self.assertEqual(R.STATE["mode"], "backend_mismatch")
                self.assertIn("清空并重建索引", R.STATE["error"])
                fake_db.open_table.assert_not_called()
        finally:
            R.STATE.clear(); R.STATE.update(old_state)
            R.M.clear(); R.M.update(old_m)


if __name__ == "__main__":
    unittest.main()

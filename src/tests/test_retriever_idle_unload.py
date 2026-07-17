# -*- coding: utf-8 -*-
"""检索组件按需加载 / 空闲释放回归测试；不读取真实库、不加载模型。"""
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))

import retriever as R  # noqa: E402


class RetrievalIdleUnloadTests(unittest.TestCase):
    def setUp(self):
        self.old_m = dict(R.M)
        self.old_state = dict(R.STATE)
        R.M.clear()
        R.M["tbl"] = object()
        R.STATE.clear()
        R.STATE.update({
            "ready": True, "mode": "full", "last_active": time.time(),
            "retrieval_loaded": False, "retrieval_loading": False, "active_retrievals": 0,
        })

    def tearDown(self):
        R.M.clear(); R.M.update(self.old_m)
        R.STATE.clear(); R.STATE.update(self.old_state)

    @staticmethod
    def fake_load():
        R.M.update({"embed": object(), "rerank": object(), "bm25": object(), "bm25_ids": []})
        R.STATE["retrieval_loaded"] = True

    def test_public_search_loads_once_and_releases_activity_claim(self):
        with (mock.patch.object(R, "_load_retrieval_locked", side_effect=self.fake_load) as load,
              mock.patch.object(R, "_search_loaded", return_value=[{"key": "K"}])):
            self.assertEqual(R.search("测试", 1), [{"key": "K"}])
            self.assertEqual(R.search("再次", 1), [{"key": "K"}])
        load.assert_called_once()
        self.assertEqual(R.STATE["active_retrievals"], 0)
        self.assertTrue(R.STATE["retrieval_loaded"])

    def test_idle_release_drops_only_heavy_components(self):
        self.fake_load()
        R.M.update({"row_count": 12, "papers": {"K": {}}, "wiki": {}})
        R.STATE["last_active"] = time.time() - 601
        self.assertTrue(R.release_retrieval_if_idle(600))
        self.assertFalse(R.STATE["retrieval_loaded"])
        self.assertNotIn("embed", R.M)
        self.assertNotIn("bm25", R.M)
        self.assertIn("tbl", R.M)
        self.assertEqual(R.M["row_count"], 12)
        self.assertIn("papers", R.M)

    def test_active_retrieval_cannot_be_released(self):
        self.fake_load()
        R.STATE["active_retrievals"] = 1
        R.STATE["last_active"] = time.time() - 3600
        self.assertFalse(R.release_retrieval_if_idle(1, force=True))
        self.assertTrue(R.STATE["retrieval_loaded"])

    def test_running_search_is_not_released_mid_request(self):
        self.fake_load()
        entered, finish = threading.Event(), threading.Event()

        def slow_search(*_args, **_kwargs):
            entered.set()
            self.assertTrue(finish.wait(2))
            return []

        with mock.patch.object(R, "_search_loaded", side_effect=slow_search):
            t = threading.Thread(target=lambda: R.search("慢查询", 1))
            t.start(); self.assertTrue(entered.wait(1))
            self.assertFalse(R.release_retrieval_if_idle(0, force=True))
            finish.set(); t.join(2)
        self.assertFalse(t.is_alive())
        self.assertTrue(R.release_retrieval_if_idle(0, force=True))

    def test_loading_status_is_observable_without_waiting_for_model_lock(self):
        entered, finish, status_done = threading.Event(), threading.Event(), threading.Event()
        result = {}

        def hold_loading_lock():
            with R._RETRIEVAL_CV:
                R.STATE["retrieval_loading"] = True
                entered.set()
                finish.wait(2)

        def read_status():
            result.update(R.retrieval_status())
            status_done.set()

        holder = threading.Thread(target=hold_loading_lock)
        holder.start(); self.assertTrue(entered.wait(1))
        reader = threading.Thread(target=read_status)
        reader.start()
        try:
            self.assertTrue(status_done.wait(0.3), "状态接口被模型加载锁阻塞")
            self.assertTrue(result.get("loading"))
        finally:
            finish.set(); holder.join(2); reader.join(2)

    def test_cold_wiki_save_defers_embedding_without_loading(self):
        with mock.patch.object(R, "_load_retrieval_locked") as load:
            self.assertFalse(R.index_wiki_page("W", "标题", "正文", {"stale": False}))
        load.assert_not_called()
        self.assertIn("W", R.M["wiki"])


if __name__ == "__main__":
    unittest.main()

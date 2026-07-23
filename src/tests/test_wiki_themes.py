# -*- coding: utf-8 -*-
"""综述主题书架：自动归类、人工覆盖与安全删除。纯内存测试，不接触真实库。"""
import sys
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))

import wiki_store as W  # noqa: E402


class WikiThemeTests(unittest.TestCase):
    def test_auto_theme_uses_source_overlap_without_llm(self):
        page = {"sources": [{"key": "A"}, {"key": "B"}, {"key": "X"}]}
        topics = [
            {"id": 1, "name": "证据 · evidence", "keys": ["A"]},
            {"id": 2, "name": "少年司法 · juvenile justice", "keys": ["A", "B"]},
        ]
        result = W._auto_theme(page, topics)
        self.assertEqual(result["name"], "少年司法")
        self.assertEqual(result["overlap"], 2)
        self.assertEqual(result["source"], "auto")

    def test_manual_theme_wins_over_auto_theme(self):
        page = {"theme": "我的论文", "sources": [{"key": "A"}]}
        result = W._effective_theme(page, [{"id": 1, "name": "证据", "keys": ["A"]}])
        self.assertEqual(result["name"], "我的论文")
        self.assertEqual(result["source"], "manual")

    def test_set_page_theme_keeps_body_and_registers_custom_theme(self):
        page = {"id": "answer-1", "kind": "answer", "sources": []}
        index = {"pages": [page]}
        with mock.patch.object(W, "load_index", return_value=index), \
                mock.patch.object(W, "_save_index") as save_index, \
                mock.patch.object(W, "_sync_theme_frontmatter") as sync_frontmatter, \
                mock.patch.object(W, "_load_theme_state", return_value={"themes": []}), \
                mock.patch.object(W, "_save_theme_state") as save_themes, \
                mock.patch.object(W, "_snapshot"):
            result = W.set_page_theme("answer-1", "少年司法")
        self.assertEqual(page["theme"], "少年司法")
        self.assertEqual(result["theme"]["source"], "manual")
        save_index.assert_called_once_with(index)
        save_themes.assert_called_once_with(["少年司法"])
        sync_frontmatter.assert_called_once_with(page)

    def test_delete_theme_never_deletes_pages(self):
        pages = [
            {"id": "a", "kind": "answer", "theme": "待整理"},
            {"id": "b", "kind": "topic", "theme": "其它"},
        ]
        index = {"pages": pages}
        with mock.patch.object(W, "load_index", return_value=index), \
                mock.patch.object(W, "_save_index") as save_index, \
                mock.patch.object(W, "_sync_theme_frontmatter"), \
                mock.patch.object(W, "_load_theme_state", return_value={"themes": ["待整理", "其它"]}), \
                mock.patch.object(W, "_save_theme_state") as save_themes:
            result = W.delete_theme("待整理")
        self.assertEqual(len(index["pages"]), 2)
        self.assertEqual(pages[0]["theme"], "")
        self.assertEqual(pages[1]["theme"], "其它")
        self.assertEqual(result["reset_pages"], 1)
        save_index.assert_called_once_with(index)
        save_themes.assert_called_once_with(["其它"])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import retriever as R


class _RejectLongEmbedder:
    def __init__(self):
        self.segments = []

    def encode(self, texts, max_length=None):
        self.segments.extend(texts)
        if any(len(text) > R._WIKI_EMBED_SEGMENT_CHARS for text in texts):
            raise RuntimeError("input too long")
        rows = []
        for i, text in enumerate(texts):
            rows.append([1.0, float((i % 5) + 1), float(len(text) % 7 + 1)])
        rows = np.asarray(rows, dtype=np.float32)
        return rows / np.linalg.norm(rows, axis=1, keepdims=True)


class _Table:
    schema = []

    def __init__(self):
        self.rows = {}

    def delete(self, _predicate):
        return None

    def add(self, rows):
        for row in rows:
            self.rows[row["chunk_id"]] = row


class LongWikiEmbeddingTests(unittest.TestCase):
    def setUp(self):
        self.old_state = dict(R.STATE)
        self.old_m = dict(R.M)

    def tearDown(self):
        R.STATE.clear()
        R.STATE.update(self.old_state)
        R.M.clear()
        R.M.update(self.old_m)

    def test_long_chinese_page_is_segmented_aggregated_and_upserted_once(self):
        body = "\n\n".join(
            f"## 第{i}节\n" + ("关系自主与监督考察条件控制。" * 45)
            for i in range(40)
        )
        embedder = _RejectLongEmbedder()
        table = _Table()
        R.STATE.update({"mode": "full"})
        R.M.clear()
        R.M.update({"embed": embedder, "tbl": table, "wiki": {}, "row_count": 7})

        with mock.patch.object(R, "existing_chunk_ids",
                               side_effect=[set(), {"overview-long::wiki"}]), \
             mock.patch.object(R, "_fit_row_to_schema",
                               side_effect=lambda full, _vec: full):
            self.assertTrue(R._index_wiki_page_loaded(
                "overview-long", "少年司法总论", body, {"stale": False}))
            self.assertTrue(R._index_wiki_page_loaded(
                "overview-long", "少年司法总论", body, {"stale": False}))

        self.assertGreater(len(embedder.segments), 2)
        self.assertLessEqual(
            max(len(text) for text in embedder.segments),
            R._WIKI_EMBED_SEGMENT_CHARS)
        self.assertIn("overview-long::wiki", table.rows)
        self.assertEqual(1, len(table.rows))
        self.assertEqual(8, R.M["row_count"])
        vector = np.asarray(table.rows["overview-long::wiki"]["vector"])
        self.assertAlmostEqual(1.0, float(np.linalg.norm(vector)), places=5)

    def test_segment_sampler_keeps_beginning_and_end(self):
        body = "\n\n".join(f"## 段落{i}\n" + "甲" * 1300 for i in range(60))
        segments = R._wiki_embedding_segments("标题", body)
        self.assertEqual(R._WIKI_EMBED_MAX_SEGMENTS, len(segments))
        self.assertIn("段落0", segments[0])
        self.assertIn("段落59", segments[-1])


if __name__ == "__main__":
    unittest.main()

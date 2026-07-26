# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import siliconflow_embedder as S


class _Response:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data
        self.headers = {}

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(str(self.status_code))


class _Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, json, timeout):
        self.calls.append((url, json, timeout))
        return self.response


class SiliconFlowLimitTests(unittest.TestCase):
    def test_encode_applies_deterministic_remote_length_guard(self):
        response = _Response(200, {
            "data": [{"index": 0, "embedding": [3.0, 4.0]}],
        })
        embedder = S.SiliconFlowEmbedder(key="x", dim=2, max_retries=1)
        embedder._sess = _Session(response)
        vector = embedder.encode(["甲" * 10000], max_length=512)
        sent = embedder._sess.calls[0][1]["input"][0]
        self.assertEqual(512 * S.CHARS_PER_REQUEST_TOKEN, len(sent))
        self.assertAlmostEqual(1.0, float((vector[0] ** 2).sum()), places=5)

    def test_400_error_preserves_short_server_reason(self):
        response = _Response(400, {
            "error": {"message": "input is too long"},
        })
        embedder = S.SiliconFlowEmbedder(key="x", dim=2, max_retries=1)
        embedder._sess = _Session(response)
        with self.assertRaises(S.EmbedClientError) as caught:
            embedder.encode(["文本"], max_length=512)
        self.assertIn("输入长度", str(caught.exception))
        self.assertIn("input is too long", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

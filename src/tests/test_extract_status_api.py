# -*- coding: utf-8 -*-
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


class ExtractStatusApiTests(unittest.TestCase):
    def test_pending_ocr_is_not_a_blocked_deep_candidate(self):
        items = {
            "pending": {"status": "ocr_pending"},
            "missing": {"status": "missing_pdf"},
            "invalid": {"status": "invalid_pdf"},
            "failed": {"status": "ocr_failed"},
            "ok": {"status": "ok_ocr"},
        }
        with mock.patch.object(server, "_deep_extract_items", return_value=items):
            self.assertEqual(server._deep_no_text_keys(), {"missing", "invalid", "failed"})

    def test_extract_counts_keep_failure_reasons_separate(self):
        items = {
            "a": {"status": "ocr_pending"},
            "b": {"status": "missing_pdf"},
            "c": {"status": "ok_native"},
        }
        counts = server._deep_extract_counts(items)
        self.assertEqual(counts["ocr_pending"], 1)
        self.assertEqual(counts["missing_pdf"], 1)
        self.assertEqual(counts["invalid_pdf"], 0)
        self.assertEqual(counts["ok_native"], 1)

    def test_read_source_keeps_late_page_after_an_unrecognized_gap(self):
        with tempfile.TemporaryDirectory() as td:
            extracted = Path(td)
            (extracted / "K.json").write_text(json.dumps({
                "total_pages": 3,
                "pages": [
                    {"page": 1, "text": "第一页"},
                    {"page": 3, "text": "第三页"},
                ],
            }, ensure_ascii=False), encoding="utf-8")
            papers = {"K": {"key": "K", "stem": "K", "title": "混合PDF",
                             "has_pdf": True}}
            with mock.patch.object(server.C, "EXTRACTED", extracted), \
                    mock.patch.object(server, "_load_papers", return_value=papers), \
                    mock.patch("page_map.printed", return_value={}):
                result = server.read_source("K")
        self.assertTrue(result["ok"])
        self.assertEqual([p["pdf_page"] for p in result["pages"]], [1, 3])
        self.assertEqual(result["n_pages_total"], 3)


class UpdateInstallerEndpointTests(unittest.TestCase):
    def test_opens_only_official_release_installer(self):
        version = server.C.APP_VERSION
        url = ("https://github.com/DrinkTea905/paper-piggy/releases/download/"
               f"v{version}/PaperPiggy-{version}-win64.exe")
        with mock.patch.dict(server.UPDATE, {"info": {"installer_url": url}}), \
                mock.patch.object(server.os, "startfile", create=True) as start:
            result = server.update_open_installer()
        self.assertTrue(result["ok"])
        start.assert_called_once_with(url)

    def test_rejects_non_official_installer_url(self):
        with mock.patch.dict(server.UPDATE,
                             {"info": {"installer_url": "https://example.com/fake.exe"}}):
            result = server.update_open_installer()
        self.assertEqual(result.status_code, 400)


if __name__ == "__main__":
    unittest.main()

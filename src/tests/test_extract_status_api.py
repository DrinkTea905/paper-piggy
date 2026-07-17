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

    def test_browse_filters_ocr_and_summary_statuses(self):
        papers = {
            "OCR": {"key": "OCR", "stem": "OCR", "title": "扫描件", "has_pdf": True},
            "NATIVE": {"key": "NATIVE", "stem": "NATIVE", "title": "文字层", "has_pdf": True},
            "INVALID": {"key": "INVALID", "stem": "INVALID", "title": "异常摘要", "has_pdf": True},
            "MISSING": {"key": "MISSING", "stem": "MISSING", "title": "缺失摘要", "has_pdf": True},
            "LEGACY": {"key": "LEGACY", "stem": "LEGACY", "title": "旧版文字层", "has_pdf": True},
            "UNDEEP": {"key": "UNDEEP", "stem": "UNDEEP", "title": "未深索", "has_pdf": True},
        }
        extract = {
            "OCR": {"status": "ok_ocr"},
            "NATIVE": {"status": "ok_native"},
            "INVALID": {"status": "ok_native"},
            "MISSING": {"status": "ok_native"},
        }
        deep = {"OCR", "NATIVE", "INVALID", "MISSING", "LEGACY"}
        patches = (
            mock.patch.object(server, "_load_papers", return_value=papers),
            mock.patch.object(server, "_load_cats", return_value={}),
            mock.patch.object(server, "_deep_keys", return_value=deep),
            mock.patch.object(server, "_deep_no_text_keys", return_value=set()),
            mock.patch.object(server, "_deep_extract_items", return_value=extract),
            mock.patch.object(server, "_summary_keys", return_value={"OCR", "NATIVE"}),
            mock.patch.object(server, "_summary_issues", return_value={"INVALID": "连续重复"}),
            mock.patch("grading_svc.grade_paper", return_value=None),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
            cases = {
                "ocr": {"OCR"},
                "native": {"NATIVE", "INVALID", "MISSING", "LEGACY"},
                "summary_yes": {"OCR", "NATIVE"},
                "summary_invalid": {"INVALID"},
                # 未深索文献不应混进摘要缺失；异常摘要也有自己的单独入口。
                "summary_no": {"MISSING", "LEGACY"},
            }
            for browse_filter, expected in cases.items():
                with self.subTest(browse_filter=browse_filter):
                    result = server.papers(deep=browse_filter)
                    self.assertEqual({p["key"] for p in result["papers"]}, expected)
                    self.assertEqual(result["total"], len(expected))

            counts = server.papers()["filter_counts"]
            self.assertEqual(counts, {
                "all": 6, "yes": 5, "no": 1, "ocr": 1, "native": 4,
                "summary_yes": 2, "summary_invalid": 1, "summary_no": 2,
            })

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

    def test_read_source_range_does_not_shrink_reported_total_pages(self):
        with tempfile.TemporaryDirectory() as td:
            extracted = Path(td)
            # 兼容旧提取记录：没有 total_pages，但页数组本身能证明全文至少到第 3 页。
            (extracted / "K.json").write_text(json.dumps({"pages": [
                {"page": 1, "text": "第一页"}, {"page": 3, "text": "第三页"},
            ]}, ensure_ascii=False), encoding="utf-8")
            papers = {"K": {"key": "K", "stem": "K", "title": "旧记录", "has_pdf": True}}
            with mock.patch.object(server.C, "EXTRACTED", extracted), \
                    mock.patch.object(server, "_load_papers", return_value=papers), \
                    mock.patch("page_map.printed", return_value={}):
                result = server.read_source("K", from_page=1, to_page=1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["n_pages_total"], 3)
        self.assertEqual([p["pdf_page"] for p in result["pages"]], [1])


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

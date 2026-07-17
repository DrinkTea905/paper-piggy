import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))

import deep_extract_status as DES
import extract as E


class _CopyableImage:
    def copy(self):
        return self


class _Bitmap:
    def __init__(self, owner):
        self.owner = owner

    def to_numpy(self):
        return _CopyableImage()

    def close(self):
        self.owner.bitmap_closed = True


class _TextPage:
    def __init__(self, text):
        self.text = text

    def get_text_range(self):
        return self.text

    def close(self):
        pass


class _Page:
    def __init__(self, text):
        self.text = text
        self.rendered = False
        self.bitmap_closed = False

    def get_textpage(self):
        return _TextPage(self.text)

    def render(self, scale):
        self.rendered = True
        self.scale = scale
        return _Bitmap(self)

    def close(self):
        pass


class _Document:
    def __init__(self, texts):
        self.pages = [_Page(text) for text in texts]
        self.accessed = []

    def __len__(self):
        return len(self.pages)

    def __getitem__(self, index):
        self.accessed.append(index)
        return self.pages[index]

    def close(self):
        pass


class _OCRResult:
    def __init__(self, texts, scores):
        self.txts = texts
        self.scores = scores


class OCRExtractionTests(unittest.TestCase):
    def _run(self, texts, engine, **kwargs):
        doc = _Document(texts)
        with mock.patch.object(E._pdfium, "PdfDocument", return_value=doc):
            result = E._extract_document("fake.pdf", _tries=1,
                                         ocr_engine=engine, **kwargs)
        return doc, result

    def test_native_text_never_invokes_ocr_or_render(self):
        engine = mock.Mock(side_effect=AssertionError("不应调用 OCR"))
        doc, result = self._run(["原生文字"], engine, ocr_mode="empty_pages")
        self.assertEqual(result["pages"][0]["source"], "native")
        self.assertEqual(result["native_pages"], 1)
        self.assertEqual(result["ocr_pages"], 0)
        self.assertFalse(doc.pages[0].rendered)
        engine.assert_not_called()

    def test_empty_page_is_rendered_once_and_ocr_runs_in_memory(self):
        engine = mock.Mock(return_value=_OCRResult(["识别文字"], [0.8]))
        doc, result = self._run([""], engine, ocr_mode="empty_pages")
        self.assertEqual(result["pages"], [{"page": 1, "text": "识别文字",
                                             "source": "ocr", "confidence": 0.8}])
        self.assertTrue(doc.pages[0].rendered)
        self.assertTrue(doc.pages[0].bitmap_closed)
        self.assertEqual(engine.call_count, 1)

    def test_mixed_pdf_keeps_native_pages_when_one_ocr_page_fails(self):
        calls = 0

        def engine(_image):
            nonlocal calls
            calls += 1
            if calls == 1:
                return _OCRResult(["第二页"], [0.9])
            raise E.OCRUnavailable("本地 OCR 组件未安装")

        _doc, result = self._run(["第一页", "", ""], engine,
                                 ocr_mode="empty_pages")
        self.assertEqual([p["page"] for p in result["pages"]], [1, 2])
        self.assertEqual(result["native_pages"], 1)
        self.assertEqual(result["ocr_pages"], 1)
        self.assertEqual(result["empty_pages"], 1)
        self.assertIn("OCRUnavailable", result["ocr_errors"][0])

    def test_max_pages_limits_pdf_access_before_ocr(self):
        engine = mock.Mock(side_effect=AssertionError("题录阶段不应 OCR"))
        doc, result = self._run(["一", "二", "三"], engine,
                                max_pages=2, ocr_mode="off")
        self.assertEqual(doc.accessed, [0, 1])
        self.assertEqual(result["total_pages"], 2)
        engine.assert_not_called()

    def test_v3_score_array_is_not_used_as_boolean(self):
        class Scores(list):
            def __bool__(self):
                raise AssertionError("数组不能做真假判断")

        text, confidence = E._ocr_result_text(
            _OCRResult(["甲", "乙"], Scores([0.7, 0.9])))
        self.assertEqual(text, "甲\n乙")
        self.assertAlmostEqual(confidence, 0.8)

    def test_locked_rapidocr_api_is_constructed_with_v3_default_models(self):
        factory = mock.Mock(return_value=object())
        fake_module = types.SimpleNamespace(RapidOCR=factory)
        old_engine, old_error = E._OCR_ENGINE, E._OCR_ENGINE_ERROR
        try:
            E._OCR_ENGINE = None
            E._OCR_ENGINE_ERROR = None
            with mock.patch.dict(sys.modules, {"rapidocr": fake_module}):
                engine = E._get_ocr_engine()
            factory.assert_called_once_with(params={"Global.log_level": "critical"})
            self.assertIs(engine, factory.return_value)
        finally:
            E._OCR_ENGINE = old_engine
            E._OCR_ENGINE_ERROR = old_error

    def test_rapidocr_initialization_failure_has_install_hint(self):
        factory = mock.Mock(side_effect=RuntimeError("模型文件损坏"))
        fake_module = types.SimpleNamespace(RapidOCR=factory)
        old_engine, old_error = E._OCR_ENGINE, E._OCR_ENGINE_ERROR
        try:
            E._OCR_ENGINE = None
            E._OCR_ENGINE_ERROR = None
            with mock.patch.dict(sys.modules, {"rapidocr": fake_module}):
                with self.assertRaises(E.OCRUnavailable) as ctx:
                    E._get_ocr_engine()
            self.assertIn("重新安装或更新 PaperPiggy", str(ctx.exception))
        finally:
            E._OCR_ENGINE = old_engine
            E._OCR_ENGINE_ERROR = old_error

    def test_pending_status_write_failure_does_not_invalidate_pdf(self):
        engine = mock.Mock(return_value=_OCRResult(["识别文字"], [0.8]))
        doc = _Document([""])
        with mock.patch.object(E._pdfium, "PdfDocument", return_value=doc):
            result = E._extract_document(
                "fake.pdf", _tries=1, ocr_mode="empty_pages", ocr_engine=engine,
                on_ocr_pending=mock.Mock(side_effect=OSError("状态目录暂不可写")))
        self.assertEqual(result["ocr_pages"], 1)
        self.assertEqual(result["pages"][0]["text"], "识别文字")

    def test_extract_one_records_partial_ocr_failure_in_both_sidecars(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            extracted = root / "extracted"
            extracted.mkdir()
            pdf = root / "paper.pdf"
            pdf.write_bytes(b"fake")
            status_file = root / "state" / "deep_extract_status.json"
            fake_result = {
                "pages": [{"page": 1, "text": "原生", "source": "native",
                           "confidence": 1.0}],
                "total_pages": 2, "native_pages": 1, "ocr_pages": 0,
                "empty_pages": 1, "ocr_confidence": None,
                "ocr_errors": ["p2: OCRUnavailable: 组件缺失"],
            }
            paper = {"key": "K", "stem": "paper", "pdf_path": str(pdf)}
            with mock.patch.object(E.C, "EXTRACTED", extracted), \
                    mock.patch.object(DES, "STATUS_FILE", status_file), \
                    mock.patch.object(E, "_extract_document", return_value=fake_result):
                self.assertEqual(E.extract_one(paper), "ok")
                saved = json.loads((extracted / "paper.json").read_text("utf-8"))
                structured = DES.get("paper")
            self.assertEqual(saved["status"], "ok_native")
            self.assertEqual(saved["empty_pages"], 1)
            self.assertIn("OCR 有 1 页失败", saved["error"])
            self.assertIn("OCR 有 1 页失败", structured["error"])

    def test_missing_rapidocr_is_ocr_failed_not_invalid_pdf(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            extracted = root / "extracted"
            extracted.mkdir()
            pdf = root / "scan.pdf"
            pdf.write_bytes(b"fake")
            status_file = root / "state" / "deep_extract_status.json"
            fake_result = {
                "pages": [], "total_pages": 1, "native_pages": 0,
                "ocr_pages": 0, "empty_pages": 1, "ocr_confidence": None,
                "ocr_errors": ["p1: OCRUnavailable: 请重新安装 PaperPiggy"],
            }
            paper = {"key": "K", "stem": "scan", "pdf_path": str(pdf)}
            with mock.patch.object(E.C, "EXTRACTED", extracted), \
                    mock.patch.object(DES, "STATUS_FILE", status_file), \
                    mock.patch.object(E, "_extract_document", return_value=fake_result):
                self.assertEqual(E.extract_one(paper), "empty")
                saved = json.loads((extracted / "scan.json").read_text("utf-8"))
            self.assertEqual(saved["status"], "ocr_failed")
            self.assertIn("重新安装", saved["error"])


class LegacyStatusMigrationTests(unittest.TestCase):
    def test_pending_item_leaves_legacy_exclusion_and_can_retry(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = root / "state"
            extracted = root / "extracted"
            state.mkdir()
            extracted.mkdir()
            (state / "deep_no_text.txt").write_text(
                "missing\ninvalid\nscan\n", encoding="utf-8")
            (extracted / "missing.json").write_text(
                json.dumps({"error": "no_pdf_on_disk"}), encoding="utf-8")
            (extracted / "invalid.json").write_text(
                json.dumps({"error": "PdfiumError: Data format error"}), encoding="utf-8")
            status_file = state / "deep_extract_status.json"
            with mock.patch.object(DES.C, "STATE", state), \
                    mock.patch.object(DES.C, "EXTRACTED", extracted), \
                    mock.patch.object(DES, "STATUS_FILE", status_file):
                self.assertEqual(DES.reconcile_legacy(), 3)
                items = DES.load_items()
                legacy = (state / "deep_no_text.txt").read_text("utf-8")
                self.assertEqual(DES.reconcile_legacy(), 0)
            self.assertEqual(items["missing"]["status"], "missing_pdf")
            self.assertEqual(items["invalid"]["status"], "invalid_pdf")
            self.assertEqual(items["scan"]["status"], "ocr_pending")
            self.assertEqual(legacy, "")


if __name__ == "__main__":
    unittest.main()

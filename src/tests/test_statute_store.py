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

import cite_format as CF
import folder_source
import index_light as IL
import server
import settings
import statute_store as SS
import wiki_store as W


def statute_payload(year="2025", effective="2026-01-01", status="现行有效"):
    title = "中华人民共和国测试法"
    body = f"""# {title}

第一章 总则

第一条 为了测试独立法规来源的安全入库、版本管理、检索和引注功能，根据宪法，制定本法。

第二条 本法适用于测试环境中的法规原文保存、哈希核验、按条读取与统一检索。

第三条 任何入库操作都不得修改 Zotero 原始数据，也不得把残缺网页冒充完整法规正文。
"""
    return {
        "title": title, "short_title": "测试法", "issuing_authority": "全国人民代表大会常务委员会",
        "passed_date": f"{year}-06-27", "revision_dates": [{"date": f"{year}-06-27", "label": "修订"}],
        "effective_date": effective, "legal_level": "法律", "document_number": f"主席令第{year}号",
        "source_url": f"https://www.npc.gov.cn/test/{year}.html", "version_label": f"{year}年修订",
        "validity_status": status, "body_markdown": body, "confirm_unofficial": False,
    }


class StatuteStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "statutes"
        self.patch = mock.patch.object(SS.C, "STATUTES_DIR", self.root)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def _add(self, payload):
        draft = SS.validate_draft(payload)
        return SS.add_confirmed(payload, draft["confirmation_token"])

    def test_preview_is_pure_and_confirm_token_binds_content(self):
        payload = statute_payload()
        draft = SS.validate_draft(payload)
        self.assertFalse(self.root.exists())
        changed = dict(payload, body_markdown=payload["body_markdown"] + "\n第四条 内容变化。\n")
        with self.assertRaisesRegex(SS.StatuteValidationError, "令牌不匹配"):
            SS.add_confirmed(changed, draft["confirmation_token"])
        self.assertFalse(self.root.exists())

        timed = dict(payload, fetched_at="2026-08-08 10:00:00")
        timed_draft = SS.validate_draft(timed)
        timed["fetched_at"] = "2026-08-08 10:01:00"
        with self.assertRaisesRegex(SS.StatuteValidationError, "令牌不匹配"):
            SS.add_confirmed(timed, timed_draft["confirmation_token"])

    def test_unofficial_url_needs_explicit_second_confirmation(self):
        payload = statute_payload()
        payload["source_url"] = "https://example.com/law"
        with self.assertRaisesRegex(SS.StatuteValidationError, "confirm_unofficial"):
            SS.validate_draft(payload)
        payload["confirm_unofficial"] = True
        draft = SS.validate_draft(payload)
        self.assertFalse(draft["official_source"])

    def test_confirmed_record_is_hashed_immutable_and_projected_as_markdown_source(self):
        result = self._add(statute_payload())
        self.assertEqual("added", result["status"])
        record = result["record"]
        record_dir = self.root / record["key"]
        self.assertTrue((record_dir / "snapshot.md").is_file())
        self.assertTrue((record_dir / "body.md").is_file())
        papers = SS.load_papers()
        self.assertEqual(1, len(papers))
        self.assertEqual("statute", papers[0]["itemtype"])
        self.assertEqual("statute_store", papers[0]["source_origin"])
        self.assertEqual("markdown", papers[0]["fulltext_format"])
        self.assertTrue(papers[0]["has_fulltext"])
        duplicate = self._add(statute_payload())
        self.assertEqual("duplicate", duplicate["status"])

    def test_adding_old_version_after_current_keeps_newest_current(self):
        current = statute_payload("2025", "2026-01-01")
        old = statute_payload("2012", "2013-01-01")
        self._add(current)
        self._add(old)
        records = {m["statute_version_label"]: m for m in SS.load_metadata()}
        self.assertEqual("现行有效", records["2025年修订"]["validity_status"])
        self.assertEqual("已修订", records["2012年修订"]["validity_status"])

    def test_incomplete_or_html_body_is_rejected(self):
        short = statute_payload(); short["body_markdown"] = "中华人民共和国测试法 第一条"
        with self.assertRaisesRegex(SS.StatuteValidationError, "过短"):
            SS.validate_draft(short)
        html = statute_payload(); html["body_markdown"] = "<html>" + html["body_markdown"]
        with self.assertRaisesRegex(SS.StatuteValidationError, "HTML"):
            SS.validate_draft(html)

    def test_server_preview_writes_nothing_and_confirm_starts_build(self):
        payload = statute_payload()
        preview = server.add_statute(server.StatuteAddQ(**payload))
        self.assertTrue(preview["requires_confirmation"])
        self.assertFalse(self.root.exists())

        payload["confirm"] = True
        payload["confirmation_token"] = preview["confirmation_token"]
        build_calls = []

        def run_build(stage, extra=None, on_done=None, **kwargs):
            build_calls.append((stage, extra))
            if stage == "all" and on_done:
                on_done(0)
            return True

        with mock.patch.object(server, "_run_build", side_effect=run_build), \
                mock.patch.object(server.W, "backlinks", return_value={"cited_by": []}):
            added = server.add_statute(server.StatuteAddQ(**payload))
        self.assertEqual("added", added["status"])
        self.assertTrue(added["building"])
        self.assertEqual("all", build_calls[0][0])
        self.assertEqual("statute", build_calls[1][0])
        self.assertIn("keys:" + added["key"], build_calls[1][1])
        self.assertIn("--skip-sac", build_calls[1][1])


class StatuteIntegrationTests(unittest.TestCase):
    def test_light_index_merges_primary_source_and_statutes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            primary = [{"key": "PAPER", "itemtype": "journalArticle"}]
            laws = [{"key": "LAW", "itemtype": "statute"}]
            with mock.patch.object(settings, "source", return_value="folder"), \
                    mock.patch.object(settings, "folder_dir", return_value=str(root)), \
                    mock.patch.object(folder_source, "load_papers", return_value=primary), \
                    mock.patch.object(SS, "load_papers", return_value=laws):
                papers, source_name = IL.get_papers()
        self.assertEqual(["PAPER", "LAW"], [p["key"] for p in papers])
        self.assertTrue(source_name.endswith("+statutes:1"))

    def test_wiki_lint_accepts_statute_source_with_zero_issues(self):
        with tempfile.TemporaryDirectory() as td:
            papers = Path(td) / "papers.jsonl"
            papers.write_text(json.dumps({"key": "STAT-ABC"}, ensure_ascii=False) + "\n",
                              encoding="utf-8")
            page = {
                "id": "answer-statute", "title": "法规来源页", "subject": "法规来源",
                "kind": "answer", "links": ["answer-statute"], "stale": False,
                "sources": [{"key": "STAT-ABC", "citation": "《测试法》第一条"}],
                "generated_by": "agent",
            }
            with mock.patch.object(W.C, "PAPERS_JSONL", papers), \
                    mock.patch.object(W, "load_index", return_value={"pages": [page]}), \
                    mock.patch.object(W, "page_path", return_value=Path(td) / "missing.md"), \
                    mock.patch.dict(sys.modules, {"retriever": None}):
                result = W.lint()
        self.assertEqual(0, result["n_issues"])

    def test_statute_citation_uses_version_and_article(self):
        hit = {"itemtype": "statute", "title": "中华人民共和国测试法",
               "statute_version_label": "2025年修订"}
        self.assertEqual("《中华人民共和国测试法》（2025年修订）第二条。",
                         CF.footnote(hit, heading="第二条"))
        self.assertEqual([], CF.missing_fields(hit))

    def test_read_source_can_return_one_article(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); extracted = root / "extracted"; chunks = root / "chunks"
            extracted.mkdir(); chunks.mkdir()
            (extracted / "LAW.json").write_text(json.dumps({
                "pages": [{"page": 1, "text": "第一条 甲。\n第二条 乙。"}], "total_pages": 1,
            }, ensure_ascii=False), encoding="utf-8")
            (chunks / "LAW.json").write_text(json.dumps([
                {"chunk_id": "LAW::p1::c0", "page": 1, "heading": "第一条", "text": "第一条 甲。"},
                {"chunk_id": "LAW::p1::c1", "page": 1, "heading": "第二条", "text": "第二条 乙。"},
            ], ensure_ascii=False), encoding="utf-8")
            paper = {"key": "LAW", "stem": "LAW", "title": "测试法", "itemtype": "statute",
                     "has_fulltext": True, "fulltext_format": "markdown", "author": "", "year": "2025"}
            with mock.patch.object(server, "_load_papers", return_value={"LAW": paper}), \
                    mock.patch.object(server.C, "EXTRACTED", extracted), \
                    mock.patch.object(server.C, "CHUNKS", chunks):
                result = server.read_source("LAW", article="第二条")
        self.assertTrue(result["ok"])
        self.assertEqual("第二条", result["article"])
        self.assertEqual("第二条 乙。", result["pages"][0]["text"])

    def test_search_scope_builds_a_real_key_whitelist(self):
        papers = {"LAW": {"itemtype": "statute"}, "PAPER": {"itemtype": "journalArticle"}}
        with mock.patch.object(server.R, "STATE", {"ready": True, "mode": "full"}), \
                mock.patch.object(server, "_load_papers", return_value=papers), \
                mock.patch.object(server, "_resolve_category_keys", return_value=None), \
                mock.patch.object(server.R, "search", return_value=[]) as search:
            result = server.search(server.SearchQ(query="测试", source_scope="statute"))
        self.assertEqual("statute", result["source_scope"])
        self.assertEqual({"LAW"}, search.call_args.kwargs["keys"])


if __name__ == "__main__":
    unittest.main()

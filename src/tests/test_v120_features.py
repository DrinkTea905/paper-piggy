# -*- coding: utf-8 -*-
import contextlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agent_ws as AW
import config as C
import extract as EX
import folder_ingest as FI
import folder_meta as FM
import footnote_miner as FNM
import jump_link as JL
import kb as KB
import research_assistant as RA
import retriever as R
import server
import table_store as TS
import textloc as TL
import verify_claim as VC
import wiki_store as W


def _build_minimal_pdf(pages_lines: list[list[tuple[float, float, float, str]]], draw_rule: bool = True) -> bytes:
    """用标准 Type1 Helvetica 构造多页真实 PDF，含底部分栏横线与不同字号（供 pypdfium2 实测）。"""
    objs: list[bytes] = []
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    page_refs = []
    next_id = 4
    page_objs = []
    for lines in pages_lines:
        pid = next_id
        cid = next_id + 1
        next_id += 2
        page_refs.append(f"{pid} 0 R")
        stream_parts = []
        if draw_rule:
            stream_parts.append("0.6 w 72 220 m 240 220 l S")
        for x, y, size, txt in lines:
            esc_txt = txt.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream_parts.append(f"BT /F1 {size} Tf 1 0 0 1 {x} {y} Tm ({esc_txt}) Tj ET")
        content_bytes = "\n".join(stream_parts).encode("latin-1")
        page_objs.append((
            pid,
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {cid} 0 R >>".encode("latin-1"),
            cid,
            f"<< /Length {len(content_bytes)} >>\nstream\n".encode("latin-1") + content_bytes + b"\nendstream",
        ))
    kids = " ".join(page_refs).encode("latin-1")
    objs.append(b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(page_refs)).encode("latin-1") + b" >>")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for _, pbody, _, cbody in page_objs:
        objs.append(pbody)
        objs.append(cbody)

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for idx, body in enumerate(objs, 1):
        offsets.append(len(out))
        out.extend(f"{idx} 0 obj\n".encode("latin-1"))
        out.extend(body)
        out.extend(b"\nendobj\n")
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objs) + 1}\n".encode("latin-1"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("latin-1"))
    out.extend(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("latin-1"))
    return bytes(out)


class JumpLinkTests(unittest.TestCase):
    def test_extract_att_key_and_resolve_targets(self):
        papers = {
            "ITEM0001": {
                "key": "ITEM0001",
                "title": "少年司法分流制度研究",
                "source": "zotero",
                "pdf_attachment_key": "ATT99999",
                "pdf_path": r"D:\Zotero\storage\ATT99999\paper.pdf",
            },
            "ITEMOLD2": {
                "key": "ITEMOLD2",
                "title": "旧索引迁移条目",
                "source": "zotero",
                "pdf_path": r"D:\Zotero\storage\OLDKEY12\legacy.pdf",
            },
            "ITEMNOAT": {
                "key": "ITEMNOAT",
                "title": "仅题录条目",
                "source": "zotero",
            },
            "FOLDER01": {
                "key": "FOLDER01",
                "title": "文件夹模式论文",
                "source": "folder",
                "pdf_path": __file__,
            },
        }
        self.assertEqual(JL.extract_zotero_att_key(papers["ITEM0001"]), "ATT99999")
        self.assertEqual(JL.extract_zotero_att_key(papers["ITEMOLD2"]), "OLDKEY12")

        s1, t1 = JL.resolve_open_target(papers["ITEM0001"], key="ITEM0001", pdf_page=15)
        self.assertEqual(s1, "zotero")
        self.assertEqual(t1, "zotero://open-pdf/library/items/ATT99999?page=15")

        s2, t2 = JL.resolve_open_target(papers["ITEMOLD2"], key="ITEMOLD2", pdf_page=4)
        self.assertEqual(s2, "zotero")
        self.assertEqual(t2, "zotero://open-pdf/library/items/OLDKEY12?page=4")

        s3, t3 = JL.resolve_open_target(papers["ITEMNOAT"], key="ITEMNOAT", pdf_page=2)
        self.assertEqual(s3, "zotero")
        self.assertEqual(t3, "zotero://select/library/items/ITEMNOAT")

        s4, t4 = JL.resolve_open_target(papers["FOLDER01"], key="FOLDER01", pdf_page=1)
        self.assertEqual(s4, "file")
        self.assertTrue(t4.endswith("test_v120_features.py"))
        self.assertEqual(JL.make_jump_url("ITEM0001", 15, base_url="http://127.0.0.1:8771"),
                         "http://127.0.0.1:8771/jump?key=ITEM0001&page=15")

    def test_server_jump_endpoint_renders_fallback_page_without_real_launch(self):
        papers = {
            "ITEM0001": {
                "key": "ITEM0001",
                "title": "附条件不起诉考察研究",
                "source": "zotero",
                "pdf_attachment_key": "ATT12345",
            }
        }
        with mock.patch.object(server, "_load_papers", return_value=papers), \
                mock.patch.object(server, "_launch_jump_target") as launch:
            resp = server.jump_to_source(key="ITEM0001", page=7, json_mode=0)
        self.assertEqual(resp.status_code, 200)
        body = resp.body.decode("utf-8")
        self.assertIn("附条件不起诉考察研究", body)
        self.assertIn("第 7 页", body)
        launch.assert_called_once_with("zotero", "zotero://open-pdf/library/items/ATT12345?page=7")


class KbMemoryAlignTests(unittest.TestCase):
    def test_kb_uses_tooldom_and_no_stale_200_line_limit(self):
        kb_text = (ROOT / "kb.py").read_text(encoding="utf-8")
        self.assertNotIn("上限 200 行", kb_text)
        self.assertNotIn("MEM_FILES", kb_text)
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(AW, "base_dir", return_value=Path(td)):
            AW.ensure_scaffold()
            ok, _ = AW.append_memory(target="tools:表格", text="何时该读：做样本编码表时\n优先导出 Word 三线表。")
            self.assertTrue(ok)
            with mock.patch.object(KB, "_agent_ws", return_value=AW):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    KB.do_memory(SimpleNamespace(target="core", scope="core", part=1, append="", status=None))
                self.assertIn("表格", buf.getvalue())
                buf2 = io.StringIO()
                with contextlib.redirect_stdout(buf2):
                    KB.do_memory(SimpleNamespace(target="tools:表格", scope="tools:表格", part=1, append="", status=None))
                self.assertIn("Word 三线表", buf2.getvalue())


class FolderMetaAndIngestTests(unittest.TestCase):
    def test_extract_identifiers_and_crossref_and_manual_lock(self):
        sample = "中国法学 ISSN 1003-1707\nDOI: 10.14111/j.cnki.zgfx.2024.01.002.\n正文内容"
        ids = FM.extract_identifiers(sample)
        self.assertEqual(ids["doi"], "10.14111/j.cnki.zgfx.2024.01.002")
        self.assertEqual(ids["issn"], "1003-1707")

        fake_cr = {
            "title": "未成年人附条件不起诉的实证检验",
            "author": "张三",
            "year": "2024",
            "journal": "中国法学",
            "volume": "42",
            "issue": "1",
            "issn": "1003-1707",
            "doi": "10.14111/j.cnki.zgfx.2024.01.002",
        }
        fake_conf = {"enabled": True, "key": "test-key", "base": "https://api.example.com", "model": "qwen"}
        fake_llm = json.dumps({"title": "未成年人附条件不起诉的实证检验", "author": "张三", "year": "2024"})
        with mock.patch.object(FM, "_conf", return_value=fake_conf), \
                mock.patch.object(FM.L, "chat_once", return_value=fake_llm), \
                mock.patch.object(FM, "lookup_crossref", return_value=fake_cr):
            meta, needs_review, err = FM.extract_meta(sample)
        self.assertIsNone(err)
        self.assertFalse(needs_review)
        self.assertEqual(meta["journal"], "中国法学")
        self.assertEqual(meta["issue"], "1")
        self.assertEqual(meta["doi"], "10.14111/j.cnki.zgfx.2024.01.002")

        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "locked.txt"
            f.write_text("稳定文本", encoding="utf-8")
            key = FI.FS.stable_key(Path(td), f)
            cache = {key: {"meta": {"title": "人工定稿标题"}, "meta_source": "manual", "sha1": FI._file_sha1(f)}}
            with mock.patch.object(FI.FM, "extract_meta", side_effect=AssertionError("manual locked should not re-extract")), \
                    mock.patch.object(FI, "_wait_file_settled"):
                res = FI.ingest_one(Path(td), f, cache)
            self.assertEqual(res, "skip")
            self.assertEqual(cache[key]["meta"]["title"], "人工定稿标题")
            self.assertEqual(cache[key]["meta_source"], "manual")


class PrecisionLocAndExtractTests(unittest.TestCase):
    def test_textloc_ligature_and_hyphen_and_verify_claim_body_page_pref(self):
        with tempfile.TemporaryDirectory() as td:
            ex_dir = Path(td) / "extracted"
            ex_dir.mkdir(parents=True)
            doc = {
                "key": "K1",
                "stem": "K1",
                "title": "Youth Justice Study",
                "pages": [
                    {"page": 1, "printed_page": "1", "text": "Abstract: An eﬀective ﬁnding on juvenile diversion requires procedural fairness."},
                    {"page": 5, "printed_page": "5", "text": "In practice, an effective finding on juvenile diversion requires inter-\nnational review."},
                ],
            }
            (ex_dir / "K1.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(C, "EXTRACTED", ex_dir), \
                    mock.patch.object(C, "PAPERS_JSONL", Path(td) / "missing.jsonl"):
                res_lig = TL.locate("effective finding on juvenile diversion", key="K1", fuzzy=True, max_matches=5)
                self.assertEqual(len(res_lig["matches"]), 2)
                res_hyph = TL.locate("requires international review", key="K1", fuzzy=True)
                self.assertEqual(res_hyph["matches"][0]["pdf_page"], 5)

                fake_hits = [{
                    "key": "K1",
                    "title": "Youth Justice Study",
                    "page": 5,
                    "score": 9.5,
                    "context": "In practice, an effective finding on juvenile diversion requires international review.",
                    "text": "In practice, an effective finding on juvenile diversion requires international review.",
                }]
                with mock.patch.dict(R.STATE, {"ready": True, "mode": "full"}, clear=False), \
                        mock.patch.object(R, "search", return_value=fake_hits):
                    vres = VC.verify("an effective finding on juvenile diversion", keys=["K1"])
                self.assertEqual(vres["verdict"], "supported")
                self.assertEqual(vres["evidence"][0]["pdf_page"], 5)

            f = Path(td) / "demo.txt"
            f.write_text("第一行内容。\n第二行内容。", encoding="utf-8")
            rev1 = EX._file_source_rev(f)
            f.write_text("修改后内容。", encoding="utf-8")
            rev2 = EX._file_source_rev(f)
            self.assertIn(":", rev1)
            self.assertNotEqual(rev1, rev2)


class FootnoteMinerTests(unittest.TestCase):
    def test_pdfium_region_separation_and_chinese_legal_citations(self):
        pdf_bytes = _build_minimal_pdf([
            [
                (72, 740, 11.0, "Juvenile diversion reduces recidivism when supported by community."),
                (72, 720, 11.0, "Empirical studies also highlight procedural fairness [1]."),
                (72, 180, 8.5, "(1) Smith J, Youth Justice Reform,"),
                (72, 168, 8.5, "Harvard Law Review, 2023, Vol. 136."),
            ]
        ], draw_rule=True)
        with tempfile.TemporaryDirectory() as td:
            pdf_p = Path(td) / "law_demo.pdf"
            pdf_p.write_bytes(pdf_bytes)
            paper = {"key": "P1", "stem": "P1", "pdf_path": str(pdf_p), "fulltext_format": "pdf"}
            with mock.patch.object(FNM, "FOOTNOTES_DIR", Path(td) / "footnotes"):
                res = FNM.mine_paper_footnotes(paper)
                fns_joined = " ".join(fn.get("text", "") for fn in res["footnotes"])
                self.assertIn("Smith J", fns_joined)
                self.assertIn("Harvard Law Review", fns_joined)
                self.assertNotIn("Juvenile diversion", fns_joined)
                self.assertTrue((Path(td) / "footnotes" / "P1.json").exists())

        # 法学引注手册解析（带载、不带载、专著、过滤法条）
        footnotes = [
            {"pdf_page": 4, "marker": "①", "text": "张明楷：《刑事政策与刑法解释》，载《中国法学》2023年第2期，第45页。"},
            {"pdf_page": 5, "marker": "②", "text": "陈瑞华：《少年司法的程序正义问题》，《法学研究》2021年第4期，第110页。"},
            {"pdf_page": 6, "marker": "③", "text": "李四：《未成年人保护法原理》，北京大学出版社2022年版，第88页。"},
            {"pdf_page": 7, "marker": "④", "text": "参见《中华人民共和国未成年人保护法》第30条。"},
        ]
        parsed = FNM.parse_citations_from_footnotes(footnotes)
        titles = [x["title"] for x in parsed]
        self.assertIn("刑事政策与刑法解释", titles)
        self.assertIn("少年司法的程序正义问题", titles)
        self.assertIn("未成年人保护法原理", titles)
        self.assertFalse(any(t == "中华人民共和国未成年人保护法" for t in titles))


class TableStoreTests(unittest.TestCase):
    def test_crud_conflict_idempotency_security_and_docx_export(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(AW, "base_dir", return_value=Path(td)):
            AW.ensure_scaffold()
            with self.assertRaises(TS.TableError):
                TS.create_or_evolve_table("../escape.table.json", title="非法", columns=[{"id": "a", "name": "A"}])

            c_res = TS.create_or_evolve_table(
                "研究表格/样本编码.table.json",
                title="附条件不起诉实证样本表",
                columns=[
                    {"id": "case_id", "name": "案号", "type": "text", "required": True},
                    {"id": "stage", "name": "阶段", "type": "select", "options": ["审查起诉", "审判"]},
                    {"id": "months", "name": "考察月数", "type": "number", "unit": "月"},
                    {"id": "src", "name": "来源", "type": "paper_key"},
                    {"id": "pg", "name": "页码", "type": "printed_page"},
                ],
                request_id="req-create-1",
            )
            self.assertTrue(c_res["created"])
            self.assertEqual(c_res["revision"], 1)

            with mock.patch.object(TS, "_load_paper_map", return_value={"K_LAW1": {"key": "K_LAW1", "author": "张三", "year": "2024", "title": "少年分流实证"}}):
                u1 = TS.upsert_rows(
                    "研究表格/样本编码.table.json",
                    rows=[{"case_id": "2024沪01未检01", "stage": "审查起诉", "months": 6, "src": "K_LAW1", "pg": "45-46"}],
                    key_column="case_id",
                    if_revision=1,
                    request_id="req-up-1",
                )
                self.assertEqual(u1["inserted"], 1)
                self.assertEqual(u1["revision"], 2)
                u1_rep = TS.upsert_rows(
                    "研究表格/样本编码.table.json",
                    rows=[{"case_id": "2024沪01未检01", "stage": "审查起诉", "months": 6, "src": "K_LAW1", "pg": "45-46"}],
                    key_column="case_id",
                    request_id="req-up-1",
                )
                self.assertTrue(u1_rep.get("idempotent_replay"))
                self.assertEqual(u1_rep["revision"], 2)

                with self.assertRaises(TS.RevisionConflictError):
                    TS.upsert_rows("研究表格/样本编码.table.json", rows=[{"case_id": "2024沪01未检02"}], if_revision=1)

                q = TS.query_table("研究表格/样本编码.table.json", filters=[{"column": "stage", "equals": "审查起诉"}])
                self.assertEqual(q["filtered_rows"], 1)
                rid = q["rows"][0]["row_id"]

                p_res = TS.patch_cells("研究表格/样本编码.table.json", patches=[{"row_id": rid, "cells": {"months": 8}}], if_revision=2)
                self.assertEqual(p_res["revision"], 3)

                csv_res = TS.export_table("研究表格/样本编码.table.json", fmt="csv")
                csv_bytes = Path(csv_res["path"]).read_bytes()
                self.assertTrue(csv_bytes.startswith(b"\xef\xbb\xbf"))
                self.assertIn("2024沪01未检01", csv_bytes.decode("utf-8-sig"))

                dx_res = TS.export_table("研究表格/样本编码.table.json", fmt="docx")
                with zipfile.ZipFile(dx_res["path"]) as zf:
                    doc_xml = zf.read("word/document.xml").decode("utf-8")
                self.assertIn('w:val="single"', doc_xml)
                self.assertIn('w:val="none"', doc_xml)
                self.assertIn("张三", doc_xml)


class WikiSafeguardsTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.base = Path(self.td.name)
        self.wiki_dir = self.base / "wiki"
        self.extracted_dir = self.base / "extracted"
        self.wiki_dir.mkdir(parents=True)
        self.extracted_dir.mkdir(parents=True)
        self.patches = [
            mock.patch.object(W.C, "WIKI_DIR", self.wiki_dir),
            mock.patch.object(W.C, "WIKI_ANSWERS_DIR", self.wiki_dir / "answers"),
            mock.patch.object(W.C, "WIKI_CONCEPTS_DIR", self.wiki_dir / "concepts"),
            mock.patch.object(W.C, "WIKI_TOPICS_DIR", self.wiki_dir / "topics"),
            mock.patch.object(W.C, "WIKI_DIGEST_DIR", self.wiki_dir / "digests"),
            mock.patch.object(W.C, "WIKI_OUTLINE_DIR", self.wiki_dir / "outlines"),
            mock.patch.object(W.C, "WIKI_ENTITY_DIR", self.wiki_dir / "entities"),
            mock.patch.object(W.C, "WIKI_OVERVIEW_DIR", self.wiki_dir / "overviews"),
            mock.patch.object(W.C, "WIKI_INDEX", self.wiki_dir / "index.json"),
            mock.patch.object(W.C, "WIKI_SCHEMA_MD", self.wiki_dir / "WIKI.md"),
            mock.patch.object(W.C, "WIKI_HISTORY_DIR", self.wiki_dir / ".history"),
            mock.patch.object(W, "_paper_keys", return_value={"K1"}),
            mock.patch.object(W, "_resolve_citation", return_value="张三（2024），《测试文献》"),
            mock.patch.object(W, "_snapshot"),
            mock.patch.object(C, "EXTRACTED", self.extracted_dir),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.td.cleanup()

    def test_replace_section_lock_revision_idempotency_and_source_replaced(self):
        self.assertEqual(W.SCHEMA_VERSION, "v5")
        (self.extracted_dir / "K1.json").write_text(json.dumps({"source_rev": "rev_aaa"}), encoding="utf-8")

        m1 = W.update_page(
            "concept-test",
            kind="concept",
            title="测试概念",
            content="## 概念界定\n\n第一版界定 [1]。\n\n## 实务争议\n\n保留的争议内容 [1]。",
            sources=[{"key": "K1"}],
            by_agent=True,
            request_id="w-req-1",
        )
        rev1 = m1["revision"]
        self.assertTrue(rev1)
        m1_rep = W.update_page("concept-test", content="ignored", by_agent=True, request_id="w-req-1")
        self.assertTrue(m1_rep.get("idempotent_replay"))

        with self.assertRaises(W.WikiWriteDenied):
            W.update_page("concept-test", content="坏修改", mode="replace_section",
                          section="概念界定", if_revision="badrev", by_agent=True)

        m2 = W.update_page(
            "concept-test",
            content="第二版界定（局部更新）[1]。",
            mode="replace_section",
            section="概念界定",
            if_revision=rev1,
            by_agent=True,
        )
        pg = W.get_page("concept-test")
        self.assertIn("第二版界定（局部更新）", pg["markdown"])
        self.assertIn("保留的争议内容", pg["markdown"])
        self.assertNotEqual(m2["revision"], rev1)

        lk = W.set_locked("concept-test", True)
        self.assertTrue(lk["locked"])
        with self.assertRaises(W.WikiWriteDenied):
            W.update_page("concept-test", content="覆盖", mode="replace", by_agent=True)
        with self.assertRaises(W.WikiWriteDenied):
            W.set_links("concept-test", [], by_agent=True)
        with self.assertRaises(W.WikiWriteDenied):
            W.set_stale("concept-test", True, "reason", by_agent=True)
        hum = W.edit_page_by_human("concept-test", "## 概念界定\n\n人工定稿正文 [1]。\n\n## 实务争议\n\n保留。")
        self.assertTrue(hum["verified_at"])

        (self.extracted_dir / "K1.json").write_text(json.dumps({"source_rev": "rev_bbb"}), encoding="utf-8")
        lint_res = W.lint()
        self.assertTrue(lint_res["issues"].get("source_replaced"))


class AgentWorkflowsAndDoctorTests(unittest.TestCase):
    def test_workflows_open_workflow_and_doctor_readonly(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(AW, "base_dir", return_value=Path(td)):
            AW.ensure_scaffold()
            custom_wf = AW.skills_dir() / "我的实证编码工作流.md"
            custom_wf.write_text("# 工作流：我的实证编码\n\n> 专门用于少年司法裁量样本编码。\n", encoding="utf-8")

            res = server.agent_workflows()
            self.assertTrue(res["ok"])
            fnames = [x["filename"] for x in res["workflows"]]
            self.assertNotIn("说明.md", fnames)
            self.assertIn("论文初稿（少年司法版）.md", fnames)
            self.assertIn("我的实证编码工作流.md", fnames)
            self.assertIn("远程会话直读桥（无MCP时）.md", fnames)
            jj = next(x for x in res["workflows"] if x["filename"] == "论文初稿（少年司法版）.md")
            self.assertTrue(jj["builtin"])
            self.assertFalse(jj["customized"])
            self.assertEqual(len(jj["companions"]), 1)

            with self.assertRaises(Exception):
                server.agent_open_workflow(server.AgentOpenWorkflowQ(filename="../evil.md"))
            with mock.patch("subprocess.Popen") as popen:
                op = server.agent_open_workflow(server.AgentOpenWorkflowQ(filename="我的实证编码工作流.md"))
                self.assertTrue(op["ok"])
                popen.assert_called_once()

            doc = server.agent_doctor()
            self.assertTrue(doc["ok"])
            self.assertEqual({c["id"] for c in doc["checks"]},
                             {"service", "models", "source", "mcp_clients", "workspace_and_wiki"})


if __name__ == "__main__":
    unittest.main()

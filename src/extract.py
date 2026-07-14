# -*- coding: utf-8 -*-
"""
F 档 · 提取：从 papers.jsonl 中【有 PDF】的篇提取逐页文本 → data/extracted/<stem>.json。
数据源 = index_light 生成的 papers.jsonl（含 pdf_path / collections / official_pages / itemtype 等）。
--scope 决定深索范围（支持"选择性深索"）：
    all                 全部有 PDF 的
    collection:<path>   某收藏夹（papers 的 collections 含该 path）
    keys:K1,K2,...       指定若干篇（前端勾选/按推荐深索）
断点续跑：已提取的跳过。ThreadPool（避开本机多进程 spawn 坑）。
用法: python extract.py [--scope all] [--workers 4] [--limit N]
"""
import sys, os, json, argparse, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, str(Path(__file__).parent))
import config as C

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

META_FIELDS = ("title", "author", "year", "journal", "doi", "langid",
               "official_pages", "itemtype", "journal_tier", "has_pdf")

# PDF 逐页取文本：用 pypdfium2（Google PDFium 的绑定，BSD-3/Apache-2.0）。
#
# 【为什么不是 PyMuPDF/pymupdf4llm】——2026-07 换掉，三个理由，缺一不可：
#   ① 许可证：pymupdf4llm 强制依赖 pymupdf_layout（Polyform Noncommercial，禁止商业使用、
#      非 OSI 开源），PyMuPDF 本身是 AGPL。随开源包分发它们与本项目的 Apache-2.0 冲突。
#   ② 质量：实测 6 篇真实法学 PDF，pymupdf4llm 的 markdown 转换会把**中文标点错序**
#      （句号/引号跑到句首），而三者提取的中文字符数完全相同——markdown 那点结构收益
#      根本不抵这个损失。pypdfium2 的断行最少、标点最准。
#   ③ 速度与稳定性：pymupdf4llm 单篇 9s 量级（pypdfium2 0.1~0.4s），且它 import 时会无条件
#      `import onnxruntime`（其 OCR 模块）——打包版若缺 VC++ 运行库，会让整个 import 抛错，
#      表现为**每篇 PDF 提取都失败、深索静默产出空正文**。换掉后这条隐患一并消失。
#
# 与之前一样：不做 OCR，扫描版 PDF 取不到文本（返回空页，由上层跳过）。不联网。
import pypdfium2 as _pdfium

def _extract_pages(pdf):
    """返回 [{'page': i, 'text': ...}]（只保留有文本的页）。"""
    doc = _pdfium.PdfDocument(pdf)
    try:
        pages = []
        for i in range(len(doc)):
            page = doc[i]
            tp = page.get_textpage()
            try:
                t = (tp.get_text_range() or "").strip()
            finally:
                tp.close()
                page.close()
            if t:
                pages.append({"page": i + 1, "text": t})
        return pages
    finally:
        doc.close()

def load_papers():
    if not C.PAPERS_JSONL.exists():
        return []
    return [json.loads(l) for l in open(C.PAPERS_JSONL, encoding="utf-8") if l.strip()]

def filter_scope(papers, scope):
    todo = [p for p in papers if p.get("has_pdf") and p.get("pdf_path")]
    if not scope or scope == "all":
        return todo
    if scope.startswith("collection:"):
        col = scope.split(":", 1)[1]
        return [p for p in todo if col in (p.get("collections") or [])]
    if scope.startswith("keys:"):
        ks = set(k.strip() for k in scope.split(":", 1)[1].split(",") if k.strip())
        return [p for p in todo if p.get("key") in ks]
    return todo

def _prev_ok(out):
    """读既有提取产物的 ok 标志：读不到/损坏 → None。"""
    try:
        return json.loads(out.read_text(encoding="utf-8")).get("ok")
    except Exception:
        return None

def extract_one(p):
    out = C.EXTRACTED / f"{p['stem']}.json"
    if out.exists() and out.stat().st_size > 0:
        prev = _prev_ok(out)
        # 成功产物（有正文）→ 跳过；损坏/读不出 → 也跳过（重抽也是同样输入）。
        # 但失败产物(ok=False：曾 PDF 不在盘/被占用/坏 PDF) 若现在 PDF 可读 → 重抽，
        # 别再因『产物文件已存在』把修好的 PDF 永久跳过（BF：Zotero 链接附件/OneDrive 占用等瞬时错误）。
        if prev is None or prev:
            return "skip"
        _pdf = p.get("pdf_path")
        if not (_pdf and os.path.exists(_pdf)):
            return "skip"     # PDF 仍不可读，重抽无意义（避免每轮空转坏 PDF）
        # 落到下面重抽
    pdf = p.get("pdf_path")
    meta = {k: p.get(k) for k in META_FIELDS}
    meta["collections"] = p.get("collections", [])
    rec = {"key": p["key"], "stem": p["stem"], "meta": meta, "pages": [], "ok": False}
    if not pdf or not os.path.exists(pdf):
        rec["error"] = "no_pdf_on_disk"
        out.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        return "nofile"
    try:
        rec["pages"] = _extract_pages(pdf)
        rec["ok"] = len(rec["pages"]) > 0
        if not rec["ok"]:
            rec["error"] = "no_extractable_text（疑似扫描件/图片型 PDF，需 OCR）"
        out.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        return "ok" if rec["ok"] else "empty"
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"
        try:
            out.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        return "error"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", default="all")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    papers = load_papers()
    todo = filter_scope(papers, args.scope)
    if args.limit:
        todo = todo[:args.limit]
    def _needs(p):
        f = C.EXTRACTED / f"{p['stem']}.json"
        if not (f.exists() and f.stat().st_size > 0):
            return True
        ok = _prev_ok(f)
        if ok is None or ok:
            return False              # 成功/损坏产物：跳过
        pdf = p.get("pdf_path")       # 失败产物：仅当 PDF 现在可读才重抽
        return bool(pdf and os.path.exists(pdf))
    todo = [p for p in todo if _needs(p)]
    print(f"[extract] scope={args.scope}  待提取 {len(todo)} 篇（有 PDF、未提取或失败可重抽）", flush=True)

    t0 = time.time(); done = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(extract_one, p): p["key"] for p in todo}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                s = f.result()
            except Exception:
                s = "error"
            done[s] = done.get(s, 0) + 1
            if i % 10 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)}  {time.time()-t0:.0f}s  {done}", flush=True)
    print(f"[extract] 完成 {done}  用时 {time.time()-t0:.0f}s", flush=True)

if __name__ == "__main__":
    main()

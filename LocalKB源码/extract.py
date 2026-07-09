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

# PDF 逐页取文本：优先 pymupdf4llm（markdown 结构更规整），但新版 pymupdf4llm 会在
# import 时无条件 `import onnxruntime`（其 OCR 模块），而打包版 onnxruntime 若缺 VC++
# 运行库/DLL 初始化失败，会让整个 import 抛错——从而**每篇 PDF 提取都失败、深索静默产出
# 空正文**。因此这里探测一次：能用就用 markdown，不能用（或运行期异常）就回退到纯 pymupdf
# (fitz) 逐页文本。数字版 PDF（法学期刊）用 fitz 足够，且完全不依赖 onnxruntime / 联网。
try:
    import pymupdf4llm as _p4l          # 触发其（可能失败的）onnxruntime import
except Exception as _e:
    _p4l = None
    print(f"[extract] pymupdf4llm 不可用（{type(_e).__name__}），改用 pymupdf 纯文本提取。", flush=True)
import pymupdf as _fitz                 # 始终可用；不依赖 onnxruntime

def _extract_pages(pdf):
    """返回 [{'page': i, 'text': ...}]（只保留有文本的页）。pymupdf4llm 优先，异常回退 fitz。"""
    if _p4l is not None:
        try:
            md = _p4l.to_markdown(pdf, page_chunks=True, show_progress=False)
            pages = [{"page": i + 1, "text": (pg.get("text") or "").strip()}
                     for i, pg in enumerate(md) if (pg.get("text") or "").strip()]
            if pages:
                return pages
            # markdown 解析出 0 页文本时也回退 fitz 再试一次（更稳）
        except Exception:
            pass
    doc = _fitz.open(pdf)
    try:
        return [{"page": i + 1, "text": t}
                for i, t in enumerate((pg.get_text() or "").strip() for pg in doc) if t]
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

def extract_one(p):
    out = C.EXTRACTED / f"{p['stem']}.json"
    if out.exists() and out.stat().st_size > 0:
        return "skip"
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
    todo = [p for p in todo
            if not ((C.EXTRACTED / f"{p['stem']}.json").exists()
                    and (C.EXTRACTED / f"{p['stem']}.json").stat().st_size > 0)]
    print(f"[extract] scope={args.scope}  待提取 {len(todo)} 篇（有 PDF、未提取）", flush=True)

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

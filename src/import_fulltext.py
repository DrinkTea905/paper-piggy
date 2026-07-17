# -*- coding: utf-8 -*-
"""
导入 rag 知识库现成的全文块到本库（复用 bge-m3 向量，免去几小时重新提取+嵌入）。
- key 映射：rag 用 bib key，LocalKB 用 zotero item key，靠 DOI / 归一化标题匹配。
- 只导入能映射到 LocalKB 题录、且尚未深索的篇；补 row_type=chunk + 新列；page 已是 int64。
- meta 行不删（检索端 retriever 已做"同篇有 chunk 就优先 chunk"的去重）。
- 完成后重建 bm25。
⚠️ 建议先停 LocalKB 服务再跑（避免并发写表）。约 10-20 分钟（19 万块）。
用法: python import_fulltext.py --rag-lancedb <旧rag的lancedb目录> [--limit N]
"""
import os, sys, json, re, time, argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C
import lancedb

# 旧 rag 库的 lancedb 目录：必须显式给（--rag-lancedb 或环境变量 LOCALKB_RAG_LANCEDB）。
# 这里以前写死过开发机的个人路径，开源前清掉——每台机器路径都不一样，写死只会让别人跑出
# 「未找到 rag 全文库」然后一脸茫然。没给就直接报错退出，不猜。
ENV_RAG_LANCEDB = "LOCALKB_RAG_LANCEDB"
RAG_TABLE = "chunks"
COLS = ("chunk_id", "key", "page", "heading", "text", "parent_text", "title",
        "author", "year", "journal", "doi", "langid", "vector", "journal_tier",
        "row_type", "itemtype", "official_pages", "has_pdf", "ingested_at")

def norm_title(t):
    return re.sub(r'[\s\W_]+', '', (t or '').lower())

def load_maps():
    by_doi, by_title, meta = {}, {}, {}
    for line in open(C.PAPERS_JSONL, encoding="utf-8"):
        if not line.strip():
            continue
        p = json.loads(line); k = p["key"]; meta[k] = p
        if p.get("doi"):
            by_doi[p["doi"].lower().strip()] = k
        nt = norm_title(p.get("title"))
        if nt:
            by_title.setdefault(nt, k)
    return by_doi, by_title, meta

def main():
    ap = argparse.ArgumentParser(description="把旧 rag 库的全文块（含向量）导入 LocalKB")
    ap.add_argument("--rag-lancedb", default=os.environ.get(ENV_RAG_LANCEDB, ""),
                    help=f"旧 rag 库的 lancedb 目录（内含 {RAG_TABLE} 表）；也可用环境变量 {ENV_RAG_LANCEDB}")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if not args.rag_lancedb:
        ap.error(f"必须指定旧 rag 库的 lancedb 目录：--rag-lancedb <路径>（或设环境变量 {ENV_RAG_LANCEDB}）")
    rag_lancedb = Path(args.rag_lancedb).expanduser()
    if not rag_lancedb.is_dir():
        ap.error(f"目录不存在：{rag_lancedb}")

    t0 = time.time()
    by_doi, by_title, meta = load_maps()
    print(f"[import] LocalKB 题录 {len(meta)}（可映射 doi {len(by_doi)}）", flush=True)

    rdb = lancedb.connect(str(rag_lancedb))
    if RAG_TABLE not in rdb.table_names():
        print(f"[import] 未找到 rag 全文库（{rag_lancedb} 内没有表 {RAG_TABLE}）"); return
    rtbl = rdb.open_table(RAG_TABLE)
    n_rows = rtbl.count_rows()
    print(f"[import] rag 全文块 {n_rows}，开始映射导入 ...", flush=True)

    ldb = lancedb.connect(str(C.LANCEDB_DIR))
    ltbl = ldb.open_table(C.TABLE_NAME) if C.TABLE_NAME in ldb.table_names() else None
    existing_deep = set()
    if ltbl is not None:
        d0 = ltbl.search(None).select(["key", "row_type"]).to_arrow()
        for k, rt in zip(d0.column("key").to_pylist(), d0.column("row_type").to_pylist()):
            if rt == "chunk":
                existing_deep.add(k)

    now = time.strftime("%Y-%m-%d %H:%M:%S")
    matched, n_in, n_skip, done_rag, buf = set(), 0, 0, 0, []

    def flush():
        nonlocal ltbl, n_in, buf
        if not buf:
            return
        if ltbl is None:
            ltbl = ldb.create_table(C.TABLE_NAME, data=buf, mode="overwrite")
        else:
            ltbl.add(buf)
        n_in += len(buf); buf = []

    # 旧库可能很大；直接从 Lance scanner 分批读，避免先 rtbl.to_arrow() 把整张原始向量表
    # 物化成一个 Arrow Table。这里仍需迁移 vector，但峰值被限制在单批 8000 行。
    for batch in rtbl.search(None).to_batches(batch_size=8000):
        for r in batch.to_pylist():
            done_rag += 1
            ik = None
            doi = (r.get("doi") or "").lower().strip()
            if doi:
                ik = by_doi.get(doi)
            if not ik:
                ik = by_title.get(norm_title(r.get("title")))
            if not ik or ik in existing_deep:
                n_skip += 1; continue
            m = meta[ik]
            pg = r.get("page")
            try:
                pg = int(pg) if pg is not None else None
            except Exception:
                pg = None
            buf.append({
                "chunk_id": f"{ik}::{r.get('chunk_id','')}", "key": ik,
                "page": pg, "heading": r.get("heading", ""),
                "text": r.get("text", ""), "parent_text": r.get("parent_text", ""),
                "title": r.get("title", ""), "author": r.get("author", ""),
                "year": r.get("year", ""), "journal": r.get("journal", ""),
                "doi": r.get("doi", ""), "langid": r.get("langid", ""),
                "vector": r["vector"],
                "journal_tier": m.get("journal_tier", "") or r.get("journal_tier", ""),
                "row_type": "chunk", "itemtype": m.get("itemtype", ""),
                "official_pages": m.get("official_pages", ""), "has_pdf": True,
                "ingested_at": now,
            })
            matched.add(ik)
            if len(buf) >= 5000:
                flush()
        if done_rag % 40000 < 8000:
            print(f"  rag {done_rag}/{n_rows}  入 {n_in} 跳 {n_skip}  {time.time()-t0:.0f}s", flush=True)
        if args.limit and done_rag >= args.limit:
            break
    flush()

    with open(C.STATE / "embedded_keys.txt", "a", encoding="utf-8") as f:
        for k in matched:
            f.write(k + "\n")
    print(f"[import] 导入 {n_in} 块，覆盖 {len(matched)} 篇，跳过(未映射/已深索) {n_skip}，用时 {time.time()-t0:.0f}s", flush=True)

    import bm25s
    from textutil import tokenize
    d = ltbl.search(None).select(["chunk_id", "text"]).to_arrow().to_pydict()
    ids, texts = d["chunk_id"], d["text"]
    print(f"[import] 重建 bm25（{len(texts)} 行）...", flush=True)
    rr = bm25s.BM25(); rr.index([tokenize(t) for t in texts]); rr.save(str(C.BM25_DIR))
    (C.BM25_DIR / "bm25_ids.json").write_text(json.dumps(ids, ensure_ascii=False), encoding="utf-8")
    print(f"[import] 完成，表总行数 {ltbl.count_rows()}，用时 {time.time()-t0:.0f}s", flush=True)

if __name__ == "__main__":
    main()

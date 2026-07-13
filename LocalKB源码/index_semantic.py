# -*- coding: utf-8 -*-
"""
Stage S — 快速语义层（bge-m3 嵌入题录，约 1-2 分钟）。
读 papers.jsonl 中尚未嵌入的篇 → bge-m3 向量 → LanceDB 表 row_type="meta" 行 → 重建主 bm25。
断点续跑：已嵌入的 stem 记在 data/state/meta_embedded.txt。
用法: python index_semantic.py [--batch 64]
"""
import sys, json, time, argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C
import lancedb
from textutil import tokenize
from dbutil import key_predicate

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

def load_done():
    return set(C.META_EMBEDDED.read_text(encoding="utf-8").split()) if C.META_EMBEDDED.exists() else set()

def mark(stem):
    with open(C.META_EMBEDDED, "a", encoding="utf-8") as f:
        f.write(stem + "\n")

def meta_row(p, vec):
    """一篇题录 → 表行（与全文 chunk 行同 schema，便于统一表检索）。"""
    return {
        "chunk_id": f"{p['key']}::meta",
        "key": p["key"], "page": None, "heading": "",
        "text": p.get("text") or p.get("title", ""),          # 命中展示 = 标题+摘要+关键词
        "parent_text": p.get("abstract", "") or p.get("title", ""),
        "title": p.get("title", ""), "author": p.get("author", ""),
        "year": p.get("year", ""), "journal": p.get("journal", ""),
        "doi": p.get("doi", ""), "langid": p.get("langid", ""),
        "vector": [float(x) for x in vec],
        "journal_tier": p.get("journal_tier", ""),
        "row_type": "meta",
        "itemtype": p.get("itemtype", ""),
        "official_pages": p.get("official_pages", ""),
        "has_pdf": bool(p.get("has_pdf", False)),
        "ingested_at": p.get("ingested_at", ""),
    }

def main(batch=64):
    t0 = time.time()
    if not C.PAPERS_JSONL.exists():
        print("[semantic] 未找到 papers.jsonl，请先跑 index_light", flush=True)
        return
    papers = [json.loads(l) for l in open(C.PAPERS_JSONL, encoding="utf-8") if l.strip()]
    done = load_done()
    todo = [p for p in papers if p.get("stem") not in done]
    print(f"[semantic] 题录 {len(papers)}，待嵌入 {len(todo)}（已嵌 {len(done)}）", flush=True)

    db = lancedb.connect(str(C.LANCEDB_DIR))
    tbl = db.open_table(C.TABLE_NAME) if C.TABLE_NAME in db.table_names() else None

    if todo:
        from embedder import get_embedder
        import settings as S
        print("[semantic] 加载嵌入器（" + ("API" if S.is_api() else "本地 bge-m3 ONNX-INT8") + "）...", flush=True)
        model = get_embedder(batch_size=batch)
        n = 0
        for i in range(0, len(todo), batch):
            grp = todo[i:i + batch]
            vecs = model.encode([p["text"] for p in grp], batch_size=batch, max_length=512)
            rows = [meta_row(p, v) for p, v in zip(grp, vecs)]
            if tbl is None:
                import pyarrow as pa
                t = pa.Table.from_pylist(rows)
                # meta 行 page 全 None → pyarrow 推断成 Null type，会让后续深索(page=int)加不进(cast 报错)。
                # 建表即把 page 强制成 int64（全 null 可安全 cast），根治"新用户 L→S→F 崩"。
                if not pa.types.is_int64(t.schema.field("page").type):
                    ns = t.schema.set(t.schema.get_field_index("page"), pa.field("page", pa.int64()))
                    t = t.cast(ns)
                tbl = db.create_table(C.TABLE_NAME, data=t, mode="overwrite")
            else:
                # BF4：只删 meta 行——同 key 的 chunk 行（深索成果，最贵的资产）与 meta 行共存
                # 于一张表（见 import_fulltext.py:6），裸 key 谓词会把它们连带删掉；
                # 旧表无 row_type 列时退回裸谓词。
                pred = key_predicate([r["key"] for r in rows],
                                     row_type="meta" if "row_type" in tbl.schema.names else None)
                if pred:
                    tbl.delete(pred)
                tbl.add(rows)
            for p in grp:
                mark(p["stem"])
            n += len(rows)
            if (i // batch) % 5 == 0 or i + batch >= len(todo):
                print(f"  {min(i+batch, len(todo))}/{len(todo)}  {time.time()-t0:.0f}s", flush=True)
        print(f"[semantic] 嵌入完成 {n} 篇，用时 {time.time()-t0:.0f}s", flush=True)

    # 重建主 bm25（覆盖全表 text = meta + 将来的 chunk 行）
    if tbl is not None:
        import bm25s
        d = tbl.to_arrow().to_pydict()
        ids, texts = d["chunk_id"], d["text"]
        print(f"[semantic] 重建 bm25（{len(texts)} 行）...", flush=True)
        r = bm25s.BM25()
        r.index([tokenize(t) for t in texts])
        r.save(str(C.BM25_DIR))
        (C.BM25_DIR / "bm25_ids.json").write_text(json.dumps(ids, ensure_ascii=False), encoding="utf-8")
        print(f"[semantic] bm25 完成，{len(ids)} 行", flush=True)
    print(f"[semantic] 表总行数 ≈ {tbl.count_rows() if tbl else 0}，总用时 {time.time()-t0:.0f}s", flush=True)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=64)
    main(ap.parse_args().batch)

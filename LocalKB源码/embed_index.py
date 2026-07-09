# -*- coding: utf-8 -*-
"""
步骤3：bge-m3 稠密向量 -> LanceDB；jieba 分词 -> bm25s 词法索引。
- 断点续跑：已嵌入的 key 记录在 data/state/embedded_keys.txt，重跑只补新增（支持库更新后的增量入库）。
- bm25 索引每次基于全表重建（很快）。
用法: python 03_embed_index.py [--limit N] [--batch 32]
"""
import sys, json, argparse, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C          # 先导入以设置 HF_HOME
import numpy as np
import lancedb
from textutil import tokenize
import journal_tiers as JT
from dbutil import key_predicate

KEYS_FILE = C.STATE / "embedded_keys.txt"
NO_TEXT_FILE = C.STATE / "deep_no_text.txt"   # C1/A2: 扫描件/无可抽文本的 stem（需 OCR，非真深索）
SUM_FILE  = C.DATA / "summaries" / "summaries.json"   # M2: {stem: 文档级摘要}

def load_summaries():
    """M2 文档摘要前缀。返回 {stem(safe_name): summary}；不存在则空字典（退化为纯文本嵌入）。"""
    if SUM_FILE.exists():
        try:
            return json.loads(SUM_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            import sys
            print(f"[embed] 警告：summaries.json 解析失败，本轮按纯文本嵌入(无 SAC 前缀)："
                  f"{e}", file=sys.stderr, flush=True)
            return {}
    return {}

def load_done():
    if KEYS_FILE.exists():
        return set(KEYS_FILE.read_text(encoding="utf-8").split())
    return set()

def mark_done(key):
    with open(KEYS_FILE, "a", encoding="utf-8") as f:
        f.write(key + "\n")

def load_no_text():
    """C1/A2：已判定为扫描件/无文本的 stem 集合（不算深索、下次跳过、不反复重抽）。"""
    if NO_TEXT_FILE.exists():
        return set(NO_TEXT_FILE.read_text(encoding="utf-8").split())
    return set()

def mark_no_text(key):
    """C1/A2：把空 chunks 的 stem 记进 deep_no_text.txt（而非 embedded_keys.txt），供前端标「扫描件·需OCR」。"""
    with open(NO_TEXT_FILE, "a", encoding="utf-8") as f:
        f.write(key + "\n")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--skip-bm25", action="store_true")
    args = ap.parse_args()

    db = lancedb.connect(str(C.LANCEDB_DIR))
    tbl = db.open_table(C.TABLE_NAME) if C.TABLE_NAME in db.table_names() else None

    # 是否写 journal_tier：新建表 或 现表已含该列时才写（避免增量 add 与旧表 schema 不匹配）。
    # 旧表加列由 migrate_journal_tier.py 完成；加列后此处自动开始写。
    want_tier = (tbl is None) or ("journal_tier" in tbl.schema.names)

    done = load_done()
    no_text = load_no_text()   # C1/A2：已判定扫描件的跳过，避免反复重抽
    files = sorted(C.CHUNKS.glob("*.json"))
    if args.limit:
        files = files[:args.limit]
    todo = [f for f in files if f.stem not in done and f.stem not in no_text]
    bm25_exists = (C.BM25_DIR / "bm25_ids.json").exists()
    # bm25 是否覆盖全表：比对 bm25_ids 数量与表行数。若进程曾在「新行已入表、bm25 未重建」
    # 的窗口被杀（本机休眠会杀进程树），bm25 会陈旧且行数不符，此时**不能**走快退。
    bm25_covers = False
    if bm25_exists and tbl is not None:
        try:
            n_bm25 = len(json.loads((C.BM25_DIR / "bm25_ids.json").read_text(encoding="utf-8")))
            bm25_covers = (n_bm25 == tbl.count_rows())
        except Exception:
            bm25_covers = False
    print(f"[embed] chunk文件 {len(files)}，待嵌入 {len(todo)}（已嵌 {len(done)}）")

    # 无新增、且 bm25 已覆盖全表 -> 秒退（不加载模型、不重建 bm25）。每日自动任务的常态。
    if not todo and bm25_covers:
        print("[embed] 无新增文献，索引已最新，跳过。")
        print(f"[done] 表总行数 ≈ {tbl.count_rows()}")
        return
    if not todo and bm25_exists and not bm25_covers:
        print("[embed] 无新增文献，但 bm25 与表行数不符（疑似上次重建前被中断），将重建 bm25。")

    summaries = load_summaries()
    # 自动 SAC（M2）：若在设置里开启，先给待办篇里"缺摘要"的用 LLM(SiliconFlow免费) 生成，再嵌入时自动拼前缀。
    try:
        import sac as _sac
        if todo and _sac.enabled():
            print("[sac] 自动摘要已开启，检查待办篇 …", flush=True)
            def _sac_items():
                for f in todo:
                    if summaries.get(f.stem, "").strip():
                        continue
                    try:
                        cs = json.loads(f.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    if not cs:
                        continue
                    title = cs[0].get("title", "") or f.stem
                    body = "\n".join(c.get("text", "") for c in cs[:6])
                    yield (f.stem, title, "", body)
            if _sac.ensure_for(_sac_items(), log=print):
                summaries = load_summaries()   # 重新加载含新生成的摘要
    except Exception as e:
        print(f"[embed] 自动 SAC 跳过（{e}）", flush=True)
    n_sum = 0
    n_chunks = 0
    if todo:
        from embedder import get_embedder
        import settings as S
        print("[model] 加载嵌入器（" + ("API" if S.is_api() else "本地 bge-m3 ONNX-INT8") + "）...")
        print(f"[model] 文档摘要前缀(SAC): 已加载 {len(summaries)} 篇摘要" if summaries
              else "[model] 未发现 summaries.json，按纯文本嵌入（无 SAC 前缀）")
        tm = time.time()
        model = get_embedder(batch_size=args.batch)
        print(f"[model] 就绪 {time.time()-tm:.0f}s")
        t0 = time.time()
        _now = time.strftime("%Y-%m-%d %H:%M:%S")
        for i, f in enumerate(todo, 1):
            chunks = json.loads(f.read_text(encoding="utf-8"))
            if not chunks:
                # C1/A2：空 chunks = 扫描件/无可抽文本。不写 embedded_keys.txt（否则虚标已深索），
                # 改记 deep_no_text.txt，前端标「🚫 扫描件·需OCR」；下次跳过不重抽。
                mark_no_text(f.stem); no_text.add(f.stem); continue
            # M2：摘要只拼进"嵌入文本"，存表的 text 仍是原文（展示/重排/BM25 用）。
            summ = summaries.get(f.stem, "")
            if summ:
                n_sum += 1
                embed_texts = [f"{summ}\n\n{c['text']}" for c in chunks]
            else:
                embed_texts = [c["text"] for c in chunks]
            vecs = model.encode(embed_texts, batch_size=args.batch, max_length=512)
            rows = []
            for c, v in zip(chunks, vecs):
                r = dict(c)
                r["vector"] = [float(x) for x in v]
                # 与 meta 行同 schema（F 档 chunk 行）
                r.setdefault("row_type", "chunk")
                r.setdefault("official_pages", "")
                r.setdefault("itemtype", "")
                r.setdefault("has_pdf", True)
                r.setdefault("ingested_at", _now)
                if want_tier and not r.get("journal_tier"):
                    r["journal_tier"] = JT.tier_of(r.get("journal", ""))
                rows.append(r)
            if tbl is None:
                tbl = db.create_table(C.TABLE_NAME, data=rows, mode="overwrite")
            else:
                # 幂等：add 前先按原始 key 删可能残留的旧行（防上次在 add 后、mark_done 前
                # 被杀导致同篇重复入库）。正常增量时该 key 不在表，删 0 行无副作用。
                pred = key_predicate([rows[0].get("key")])
                if pred:
                    tbl.delete(pred)
                tbl.add(rows)
            mark_done(f.stem); done.add(f.stem); n_chunks += len(rows)
            if i % 50 == 0 or i == len(todo):
                el = time.time() - t0
                rate = n_chunks / el if el else 0
                print(f"  {i}/{len(todo)}  块 {n_chunks}  {el:.0f}s  ({rate:.0f} 块/s)")
        print(f"[embed] 完成，新增 {n_chunks} 块（含 SAC 摘要前缀的文献 {n_sum} 篇），用时 {time.time()-t0:.0f}s")

    # ---- bm25s 重建：仅当有新增或索引缺失 ----
    if not args.skip_bm25 and tbl is not None and (n_chunks > 0 or not bm25_covers):
        import bm25s
        print("[bm25] 读取全表 ...")
        d = tbl.to_arrow().to_pydict()
        ids, texts = d["chunk_id"], d["text"]
        print(f"[bm25] 分词 {len(texts)} 块 ...")
        t1 = time.time()
        corpus_tokens = [tokenize(t) for t in texts]
        print(f"[bm25] 分词用时 {time.time()-t1:.0f}s，建索引 ...")
        retriever = bm25s.BM25()
        retriever.index(corpus_tokens)
        retriever.save(str(C.BM25_DIR))
        (C.BM25_DIR / "bm25_ids.json").write_text(json.dumps(ids, ensure_ascii=False), encoding="utf-8")
        print(f"[bm25] 完成，{len(ids)} 文档 -> {C.BM25_DIR}")

    print(f"[done] 表总行数 ≈ {tbl.count_rows() if tbl is not None else 0}")

if __name__ == "__main__":
    main()

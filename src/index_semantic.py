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
import document_formats as DF

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

def _purge_deleted(papers, tbl):
    """清掉数据源里已删条目的残留：Zotero 删条目（含进回收站）后 index_light 重写的 papers.jsonl
       已无该篇，但表内 meta/chunk 行、进度文件、产物全部残留——会继续出现在语义/深索结果里，
       点开走 404，/stats 的深索数也把它算进去。folder 模式有对称清理，zotero 模式此前完全没有。
       用 papers.jsonl 现有 key 集合与表内 key 作差，对消失的 key 调 folder_ingest 同款 purge。"""
    if tbl is None:
        return
    try:
        import folder_ingest as FI
        live = {p["key"] for p in papers}
        # 这里只需 key。旧写法 tbl.to_arrow() 会把 1024 维 vector 和全文列一并读进来，
        # 随后 to_pydict 又把向量膨胀成海量 Python float。
        key_col = tbl.search(None).select(["key"]).to_arrow().column("key")
        tbl_keys = set(k for k in key_col.to_pylist() if k)
        gone = [k for k in tbl_keys if k not in live]
        if not gone:
            return
        # ★ 安全阈值（2026-07-15，对齐手动「删除同步」按钮 server.py:323/329）：活库为空、或一次要删过半 →
        #    拒绝清理。防数据源读取异常（配置库掉线回退空库 / Zotero 正在写时读到半截 / WAL 半状态）
        #    把整库深索成果误抹（零备份不可恢复）。宁可暂留几条已删残留，也绝不整库清空。
        if not live or len(gone) > max(20, len(tbl_keys) // 2):
            print(f"[semantic] ⚠ 拒绝清理 {len(gone)}/{len(tbl_keys)} 条（活库仅 {len(live)} 篇）—— "
                  f"疑似数据源读取异常，已跳过删除以防误抹整库。请确认 Zotero 库完整后再更新。", flush=True)
            return
        print(f"[semantic] 清理 {len(gone)} 条已从数据源删除的残留（表行+进度+产物）", flush=True)
        FI._purge_db_rows(gone)
        FI._purge_key_artifacts(gone)
    except Exception as e:
        print(f"[semantic] 清理残留失败（不阻断）：{e!r}", flush=True)


def main(batch=64):
    t0 = time.time()
    if not C.PAPERS_JSONL.exists():
        print("[semantic] 未找到 papers.jsonl，请先跑 index_light", flush=True)
        return
    papers = [DF.normalize_record(json.loads(l))
              for l in open(C.PAPERS_JSONL, encoding="utf-8") if l.strip()]

    db = lancedb.connect(str(C.LANCEDB_DIR))
    tbl = db.open_table(C.TABLE_NAME) if C.TABLE_NAME in db.table_names() else None
    _purge_deleted(papers, tbl)   # 先清已删条目残留，再算待嵌入（done 里被删 stem 已被 purge 剔除）

    done = load_done()
    todo = [p for p in papers if p.get("stem") not in done]
    print(f"[semantic] 题录 {len(papers)}，待嵌入 {len(todo)}（已嵌 {len(done)}）", flush=True)

    aborted = None       # 非 None=致命中止原因；末尾据此非0退出（★铁律：rc=0 ⟺ 全部 todo 已嵌）
    n_fail = 0           # 因限流/网络被跳过（未嵌）的批数；>0 即末尾非0退出，杜绝卡死进度条谎报完成
    consec = 0
    if todo:
        from embedder import get_embedder
        from siliconflow_embedder import EmbedClientError, EmbedTransientError
        import settings as S
        print("[semantic] 加载嵌入器（" + ("API" if S.is_api() else "本地 bge-m3 ONNX-INT8") + "）...", flush=True)
        model = get_embedder(batch_size=batch)
        n = 0
        for i in range(0, len(todo), batch):
            grp = todo[i:i + batch]
            try:
                vecs = model.encode([p["text"] for p in grp], batch_size=batch, max_length=512)
                consec = 0
            except EmbedClientError as e:
                aborted = f"提升检索质量中止（密钥/余额/模型名问题，重试无用）：{e}"; break
            except EmbedTransientError as e:
                n_fail += 1; consec += 1
                print(f"[semantic] 第 {min(i+batch,len(todo))}/{len(todo)} 批嵌入失败（{e}），已跳过；连续失败 {consec}/3", flush=True)
                if consec >= 3:
                    aborted = f"连续 3 批失败，判定 API 不可用，中止：{e}"; break
                continue
            # 其它异常不吞：子进程带 traceback 退出，前端仍 rc≠0，日志有栈可查
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
        # BM25 只消费 id+text；明确选列，绝不把向量/父块/题录字段物化进 Python。
        d = tbl.search(None).select(["chunk_id", "text"]).to_arrow().to_pydict()
        ids, texts = d["chunk_id"], d["text"]
        print(f"[semantic] 重建 bm25（{len(texts)} 行）...", flush=True)
        r = bm25s.BM25()
        r.index([tokenize(t) for t in texts])
        r.save(str(C.BM25_DIR))
        (C.BM25_DIR / "bm25_ids.json").write_text(json.dumps(ids, ensure_ascii=False), encoding="utf-8")
        print(f"[semantic] bm25 完成，{len(ids)} 行", flush=True)
    # 向量空间一致性基准：只有真正产出向量的这里写 backend + embedding_identity（light 不覆写）。
    # 换后端后的“强制全量重嵌”路径（清进度+删表）会让这里以新后端重建、并写回新 backend。
    if tbl is not None:
        try:
            import settings as S
            man = json.loads(C.INDEX_MANIFEST.read_text(encoding="utf-8")) if C.INDEX_MANIFEST.exists() else {}
            man["backend"] = S.backend()
            man["embedding_identity"] = S.embedding_identity()
            # 这里只证明语义向量已按当前规则生成；不要顺手替 deep/light 洗成“已更新”。
            import upgrade_health as UH
            fps = man.get("pipeline_fingerprints")
            if not isinstance(fps, dict):
                fps = UH.pipeline_fingerprints()
            fps["semantic"] = UH.pipeline_fingerprints()["semantic"]
            man["pipeline_fingerprints"] = fps
            C.INDEX_MANIFEST.write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[semantic] 写 manifest.backend 失败：{e}", flush=True)
    print(f"[semantic] 表总行数 ≈ {tbl.count_rows() if tbl else 0}，总用时 {time.time()-t0:.0f}s", flush=True)
    # ★ 铁律：有一批没嵌成功就非0退出——否则前端进度条会永远卡在「正在提升检索质量 X/Y」谎报进行中。
    #   已嵌的批与 bm25 已落盘，重跑「更新知识库」即从断点续跑。
    if aborted:
        print(f"[semantic] {aborted}", flush=True); sys.exit(2)
    if n_fail:
        print(f"[semantic] {n_fail} 批因限流/网络未嵌（其余已保存）；稍后点「更新知识库」即可续。", flush=True)
        sys.exit(2)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=64)
    main(ap.parse_args().batch)

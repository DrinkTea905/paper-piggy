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
    # BF27：历史上曾出现两个构建进程并发双跑，同 stem 被追加两遍、深索计数虚高——
    # 追加前先读现有集合防重（文件只有几千行，每次全读的代价可忽略）。
    if KEYS_FILE.exists() and key in set(KEYS_FILE.read_text(encoding="utf-8").split()):
        return
    with open(KEYS_FILE, "a", encoding="utf-8") as f:
        f.write(key + "\n")

def load_no_text():
    """C1/A2：已判定为扫描件/无文本的 stem 集合（不算深索、下次跳过、不反复重抽）。"""
    if NO_TEXT_FILE.exists():
        return set(NO_TEXT_FILE.read_text(encoding="utf-8").split())
    return set()

def mark_no_text(key):
    """C1/A2：把空 chunks 的 stem 记进 deep_no_text.txt（而非 embedded_keys.txt），供前端标「扫描件·需OCR」。"""
    # BF27 同款去重：并发/孤儿双跑会把同 stem 追加多遍、扫描件计数虚高——追加前先读现有集合防重。
    if NO_TEXT_FILE.exists() and key in set(NO_TEXT_FILE.read_text(encoding="utf-8").split()):
        return
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
    # BF7b：本文件所有阶段性 print 统一 flush——孤儿进程靠写 stdout 触发 OSError 自灭，缓冲会拖慢这一步
    print(f"[embed] chunk文件 {len(files)}，待嵌入 {len(todo)}（已嵌 {len(done)}）", flush=True)

    # 无新增、且 bm25 已覆盖全表 -> 秒退（不加载模型、不重建 bm25）。每日自动任务的常态。
    if not todo and bm25_covers:
        print("[embed] 无新增文献，索引已最新，跳过。", flush=True)
        print(f"[done] 表总行数 ≈ {tbl.count_rows()}", flush=True)
        return
    if not todo and bm25_exists and not bm25_covers:
        print("[embed] 无新增文献，但 bm25 与表行数不符（疑似上次重建前被中断），将重建 bm25。", flush=True)

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
    aborted = None       # 非 None=致命中止原因；末尾据此非0退出（★铁律：rc=0 ⟺ 全部 todo 已嵌）
    n_fail = 0           # 因限流/网络被跳过（未嵌）的篇数；>0 即末尾非0退出，杜绝「半个库报完成」
    consec = 0           # 连续传输失败计数（连续 3 篇判死，避免整库每篇重撞同一堵墙、疯狂打 API）
    if todo:
        from embedder import get_embedder
        from siliconflow_embedder import EmbedClientError, EmbedTransientError
        import settings as S
        print("[model] 加载嵌入器（" + ("API" if S.is_api() else "本地 bge-m3 ONNX-INT8") + "）...", flush=True)
        print(f"[model] 文档摘要前缀(SAC): 已加载 {len(summaries)} 篇摘要" if summaries
              else "[model] 未发现 summaries.json，按纯文本嵌入（无 SAC 前缀）", flush=True)
        tm = time.time()
        model = get_embedder(batch_size=args.batch)
        print(f"[model] 就绪 {time.time()-tm:.0f}s", flush=True)
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
            try:
                vecs = model.encode(embed_texts, batch_size=args.batch, max_length=512)
                consec = 0
            except EmbedClientError as e:
                # 4xx：密钥/余额/模型名——重试无用，立即整批中止（别再往下逐篇打 API）
                aborted = f"嵌入中止（密钥/余额/模型名问题，重试无用）：{e}"; break
            except EmbedTransientError as e:
                # 限流/网络/服务端——跳过本篇（不 mark_done，下次续跑）；连续 3 篇则判死中止
                n_fail += 1; consec += 1
                print(f"[embed] 第 {i}/{len(todo)} 篇嵌入失败（{e}），已跳过；连续失败 {consec}/3", flush=True)
                if consec >= 3:
                    aborted = f"嵌入连续 3 篇失败，判定 API 不可用，中止：{e}"; break
                continue
            # 其它异常（本地模型故障 / 真 bug）不吞：让子进程带 traceback 退出，前端仍 rc≠0，日志有栈可查
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
                # BF4：只删 chunk 行——meta 行与 chunk 行同 key 共存（见 import_fulltext.py:6），
                # 裸 key 谓词会把语义层的 meta 行连带删掉；旧表无 row_type 列时退回裸谓词。
                pred = key_predicate([rows[0].get("key")],
                                     row_type="chunk" if "row_type" in tbl.schema.names else None)
                if pred:
                    tbl.delete(pred)
                # BF：chunk.py 现在恒发 journal_tier，但旧表可能没有该列，直接 add 会
                # 抛 ValueError: field 'journal_tier' does not exist in table schema。
                # add 前把每行裁到表真实列集，多出的键丢弃，确保 add 永不因列不匹配失败。
                cols = set(tbl.schema.names)
                tbl.add([{k: v for k, v in r.items() if k in cols} for r in rows])
            mark_done(f.stem); done.add(f.stem); n_chunks += len(rows)
            if i % 50 == 0 or i == len(todo):
                el = time.time() - t0
                rate = n_chunks / el if el else 0
                # BF7b：循环内进度必须 flush——父进程（server）被杀后管道断裂，带缓冲的 print
                # 要攒满 8KB 才触发 OSError，孤儿进程能多跑几十分钟；flush 让它尽快自灭。
                print(f"  {i}/{len(todo)}  块 {n_chunks}  {el:.0f}s  ({rate:.0f} 块/s)", flush=True)
        print(f"[embed] 完成，新增 {n_chunks} 块（含 SAC 摘要前缀的文献 {n_sum} 篇），用时 {time.time()-t0:.0f}s", flush=True)

    # ---- bm25s 重建：仅当有新增或索引缺失 ----
    if not args.skip_bm25 and tbl is not None and (n_chunks > 0 or not bm25_covers):
        import bm25s
        print("[bm25] 读取全表 ...", flush=True)
        d = tbl.to_arrow().to_pydict()
        ids, texts = d["chunk_id"], d["text"]
        print(f"[bm25] 分词 {len(texts)} 块 ...", flush=True)
        t1 = time.time()
        corpus_tokens = [tokenize(t) for t in texts]
        print(f"[bm25] 分词用时 {time.time()-t1:.0f}s，建索引 ...", flush=True)
        retriever = bm25s.BM25()
        retriever.index(corpus_tokens)
        retriever.save(str(C.BM25_DIR))
        (C.BM25_DIR / "bm25_ids.json").write_text(json.dumps(ids, ensure_ascii=False), encoding="utf-8")
        print(f"[bm25] 完成，{len(ids)} 文档 -> {C.BM25_DIR}", flush=True)

    print(f"[done] 表总行数 ≈ {tbl.count_rows() if tbl is not None else 0}", flush=True)
    # ★ 铁律：只要有一篇没嵌成功，就非0退出。rc=0 必须严格等价于「全部 todo 已嵌」——
    #   否则 build_all 当成功、前端误报「深索完成」，而半个库其实没入。已嵌的篇/bm25 已落盘，可续跑。
    if aborted:
        print(f"[embed] {aborted}", flush=True); sys.exit(2)
    if n_fail:
        print(f"[embed] {n_fail} 篇因限流/网络未嵌（其余已保存）；稍后重跑「深索」即可续。", flush=True)
        sys.exit(2)

if __name__ == "__main__":
    main()

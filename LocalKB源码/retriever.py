# -*- coding: utf-8 -*-
"""
检索核心（被 server.py in-process import：R.load_all / R.search / R.STATE / R.M）。
三档就绪分级：
  - L-only 词法模式：只有 bm25_meta（无 LanceDB 表）→ 纯词法搜题录，0 模型加载、秒级就绪。
  - full 模式：有 LanceDB 表 → bge-m3 稠密 + bm25 词法 → RRF → reranker 精排（表已含 meta/chunk 行）。
standalone（python retriever.py）为备用，产品不用。
"""
import sys, json, time, threading, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C
from textutil import tokenize

import lancedb
import bm25s
import journal_tiers as JT
try:
    import journal_grading as JG   # 期刊权重引擎（可选；缺失/出错则回退旧离散档，不影响检索）
except Exception as _jg_e:
    JG = None
    print("[retriever] journal_grading 未加载，期刊权重回退旧离散档：", _jg_e, flush=True)
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional
import uvicorn

STATE = {"last_active": time.time(), "ready": False, "mode": None}
M = {}  # 模型与索引句柄

def log(*a): print("[retriever]", *a, flush=True)

# ═══ 加载 ════════════════════════════════════════════════════════
def load_all():
    t0 = time.time()
    db = lancedb.connect(str(C.LANCEDB_DIR))
    tbl_exists = C.TABLE_NAME in db.table_names()
    meta_ready = (C.BM25_META_DIR / "bm25_meta_ids.json").exists()

    if not tbl_exists and not meta_ready:
        STATE["mode"] = None; STATE["ready"] = False
        log("未建库（无表、无 L 档）——等首启向导 POST /index/light")
        return

    if not tbl_exists and meta_ready:
        _load_light()
        _load_wiki_index()
        STATE["mode"] = "light"; STATE["ready"] = True
        log(f"L档就绪(词法)：{len(M['papers'])} 篇，用时 {time.time()-t0:.1f}s")
        return

    # full 模式：加载嵌入/重排后端（本地 ONNX 或 API）+ 全表 + bm25
    from embedder import get_embedder
    from reranker import get_reranker
    import settings as S
    _api = S.is_api()
    log("加载嵌入器（" + ("API" if _api else "本地 ONNX-INT8") + "）..."); M["embed"] = get_embedder()
    log("加载重排器（" + ("API" if _api else "本地 ONNX") + "）..."); M["rerank"] = get_reranker()
    M["tbl"] = db.open_table(C.TABLE_NAME)
    log("载入全表到内存 ...")
    M["records"] = {r["chunk_id"]: r for r in M["tbl"].to_arrow().to_pylist()}
    M["bm25"] = bm25s.BM25.load(str(C.BM25_DIR), load_corpus=False)
    M["bm25_ids"] = json.loads((C.BM25_DIR / "bm25_ids.json").read_text(encoding="utf-8"))
    # meta 词法索引若在，也载入（L 兜底：full 下极少用，但保留统一）
    if meta_ready:
        try:
            M["meta_bm25"] = bm25s.BM25.load(str(C.BM25_META_DIR), load_corpus=False)
            M["meta_ids"] = json.loads((C.BM25_META_DIR / "bm25_meta_ids.json").read_text(encoding="utf-8"))
        except Exception:
            pass
    # full 模式也载入题录字典：① 顶栏正确篇数（否则显示 0） ② 非深索篇的 meta 兜底展示
    if C.PAPERS_JSONL.exists():
        pp = {}
        with open(C.PAPERS_JSONL, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    _p = json.loads(line); pp[_p["key"]] = _p
        M["papers"] = pp
    _load_wiki_index()
    STATE["mode"] = "full"; STATE["ready"] = True
    log(f"full 就绪：{len(M['records'])} 块 / {len(M.get('papers', {}))} 篇"
        f" / {len(M.get('wiki', {}))} 综合页，用时 {time.time()-t0:.0f}s")

def _load_light():
    papers = {}
    if C.PAPERS_JSONL.exists():
        with open(C.PAPERS_JSONL, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    p = json.loads(line)
                    papers[p["key"]] = p
    M["papers"] = papers
    M["meta_bm25"] = bm25s.BM25.load(str(C.BM25_META_DIR), load_corpus=False)
    M["meta_ids"] = json.loads((C.BM25_META_DIR / "bm25_meta_ids.json").read_text(encoding="utf-8"))

def _load_wiki_index():
    """载入综合层页元数据 index.json → M["wiki"]（id→meta），供检索期标注/降权/展示。
       顺带存量自愈：把降级页（未配 key / LLM 失败 / 无命中 → 正文其实是原文片段清单）
       移出检索表。新写入已由 wiki_store._persist_page 拦下；这里清历史遗留。只删表行，不删 .md。
       注意：本函数在 STATE["mode"] 被置为 full **之前**调用，故按 M["tbl"] 判断而非 mode。"""
    try:
        import wiki_store as W
        M["wiki"] = W.index_map()
    except Exception as e:
        M["wiki"] = {}
        log("wiki index 载入失败（视为空）：", e)
        return
    if "tbl" not in M or not M.get("records"):
        return
    stale_rows = [pid for pid, meta in M["wiki"].items()
                  if W.is_degraded(meta.get("generated_by", "")) and f"{pid}::wiki" in M["records"]]
    for pid in stale_rows:
        try:
            delete_wiki_page(pid)      # 删表行 + 内存登记（幂等）；页面仍可在综述库阅读
            log(f"降级综合页 {pid} 已移出检索表（仍可在综述库阅读）")
        except Exception as e:
            log(f"清理降级页 {pid} 失败：", e)
    if stale_rows:
        M["wiki"] = W.index_map()      # delete_wiki_page 会 pop 掉条目，重载回其余页的 meta

# ═══ 检索：full 模式 ════════════════════════════════════════════
def dense_search(q, k):
    qv = M["embed"].encode([q], max_length=256)[0]
    hits = M["tbl"].search(qv.tolist()).metric("cosine").limit(k).to_list()
    return [h["chunk_id"] for h in hits]

def bm25_search(q, k):
    toks = tokenize(q)
    if not toks:
        return []
    res, _ = M["bm25"].retrieve([toks], k=min(k, len(M["bm25_ids"])))
    return [M["bm25_ids"][int(i)] for i in res[0]]

def rrf(dense_ids, bm25_ids, k=C.RRF_K):
    score = {}
    for rank, cid in enumerate(dense_ids):
        score[cid] = score.get(cid, 0) + 1.0 / (k + rank + 1)
    for rank, cid in enumerate(bm25_ids):
        score[cid] = score.get(cid, 0) + 1.0 / (k + rank + 1)
    return [cid for cid, _ in sorted(score.items(), key=lambda x: -x[1])]

def _tier_of(r):
    t = r.get("journal_tier")
    return t if t else JT.tier_of(r.get("journal", ""))

def _active_discipline():
    """整库锁定学科（journal_grading）。从 settings 实时读——改设置即时生效、不用重启/重建。"""
    try:
        import settings as S
        return S.discipline()
    except Exception:
        return "law"

def _weight_res(r):
    """算一条候选的期刊权重（检索期动态）。引擎缺失/出错 → None（排序回退旧离散档）。"""
    if JG is None:
        return None
    try:
        return JG.resolve_journal_weight(
            {"journal": r.get("journal", "") or "", "issn": r.get("issn", "") or ""},
            _active_discipline())
    except Exception:
        return None

# F38-B：tier code → 面向用户中文档名（与 grading_svc.TIER_CN 保持一致；前端 tierBadge 统一读中文 weight_tier）
_TIER_CN = {"T1": "权威", "T1b": "准权威", "T2": "核心", "T3": "次核心",
            "T4": "一般", "T5": "普通", "待确认": "待确认"}

def _attach_weight(d, wr):
    """把权重结果挂到输出条目（供前端显示/加权/过滤）。weight_tier 统一输出中文档名。"""
    d["journal_weight"] = wr.get("weight") if wr else None
    _t = wr.get("tier") if wr else None
    d["weight_tier"] = _TIER_CN.get(_t, _t) if _t else None      # 中文档名（前端主徽标源）
    d["weight_tier_code"] = _t                                    # 原始 T? 码（调试/兼容用）
    d["weight_needs_review"] = bool(wr.get("needsReview")) if wr else False
    return d

def _is_wiki(r):
    """综合层 wiki 行：row_type=="wiki" 或 chunk_id 以 "::wiki" 结尾
       （后者兼容旧表无 row_type 列的情形，见 index_wiki_page 的按需列适配）。"""
    return r.get("row_type") == "wiki" or str(r.get("chunk_id", "")).endswith("::wiki")

def _wiki_meta(r):
    """取该 wiki 行的页元数据（generated_at/stale/sources…），来自 M["wiki"]（index.json 载入）。"""
    wid = r.get("key") or str(r.get("chunk_id", "")).replace("::wiki", "")
    return (M.get("wiki") or {}).get(wid, {})

def _page_cite(r):
    """全文块引用：优先官方页码，退回 PDF 内页码。wiki 行给"本地综合"引用 + 过时提示。"""
    if _is_wiki(r):
        wm = _wiki_meta(r)
        n = len(wm.get("sources", []))
        gen = (wm.get("generated_at", "") or "")[:10]
        s = f"本地综合《{r.get('title', '')}》"
        if n:
            s += f" · 基于 {n} 篇"
        if gen:
            s += f" · 生成于 {gen}"
        return s + "（来自既有综合，可能已过时）"
    parts = []
    if r.get("author"): parts.append(r["author"].split(";")[0].strip() + ("等" if ";" in r["author"] else ""))
    if r.get("year"):   parts.append(f"({r['year']})")
    s = " ".join(parts) + f"《{r.get('title','')}》"
    if r.get("journal"): s += f"，{r['journal']}"
    # Phase A：全文块引用用「期刊印刷页码」（读者翻期刊看到的那页），而非 PDF 顺序页/整篇范围。
    pg = ""
    if r.get("page") is not None and r.get("key"):
        try:
            import page_map as PM
            pg = PM.printed(r["key"], r.get("page")).get("display") or ""
        except Exception:
            pg = ""
    if not pg:
        pg = r.get("official_pages") or (str(r.get("page")) if r.get("page") is not None else "")
    if pg: s += f"，第{pg}页"
    return s.strip()

def search_full(query, topk, sort, keys=None):
    di = dense_search(query, C.DENSE_TOPK)
    bi = bm25_search(query, C.BM25_TOPK)
    # 重排池与最终 topk 解耦：重排(cross-encoder)是最贵的一步，topk 调大(无限滚动)时不让候选爆炸。
    # dense/bm25 各取 50 → RRF 去重后约 ~100，池上限 90 足够；避免 topk=40 时重排 320 条拖到十几秒。
    pool = min(max(50, topk * 2), 64)
    fused = rrf(di, bi)[:pool]
    # F11：有 key 白名单时在 rerank 前过滤候选（最省算力）；keys=None 时行为与旧版逐字一致
    cand = [cid for cid in fused
            if cid in M["records"]
            and (keys is None or M["records"][cid].get("key") in keys)]
    if not cand:
        return []
    scores = M["rerank"].scores(query, [M["records"][cid]["text"] for cid in cand])
    ranked = sorted(zip(cand, scores), key=lambda x: -x[1])
    # 同 key 去重（chunk 行优先于 meta 行：更具体）+ MAX_PER_KEY
    per_key, picked, overflow = {}, [], []
    for cid, sc in ranked:
        r = M["records"][cid]
        k = r.get("key", cid)
        # 若该 key 已有 chunk 行入选，跳过它的 meta 行（避免同篇既出摘要又出正文）
        if r.get("row_type") == "meta" and any(M["records"][c].get("key") == k and M["records"][c].get("row_type") == "chunk"
                                                for c, _ in picked):
            continue
        if per_key.get(k, 0) >= C.MAX_PER_KEY:
            overflow.append((cid, sc)); continue
        per_key[k] = per_key.get(k, 0) + 1
        picked.append((cid, sc))
        if len(picked) >= topk:
            break
    if len(picked) < topk:
        picked.extend(overflow[:topk - len(picked)])
    top = [(cid, float(sc), _tier_of(M["records"][cid]), _weight_res(M["records"][cid])) for cid, sc in picked]
    _apply_sort(top, sort)
    out = []
    for cid, sc, tier, wr in top:
        r = M["records"][cid]
        deep = (r.get("row_type") != "meta")
        d = {
            "chunk_id": cid, "score": round(sc, 4),
            "journal_tier": tier, "tier_rank": JT.rank_of(tier),
            "title": r.get("title", ""), "author": r.get("author", ""),
            "year": r.get("year", ""), "journal": r.get("journal", ""),
            "doi": r.get("doi", ""), "page": r.get("page"), "key": r.get("key", ""),
            "official_pages": r.get("official_pages", ""),
            "row_type": r.get("row_type", "chunk"),
            "depth": "full" if deep else "abstract",
            "has_pdf": r.get("has_pdf", True),
            "heading": r.get("heading", ""),
            "text": r.get("text", ""), "context": r.get("parent_text", ""),
            "citation": _page_cite(r),
        }
        if _is_wiki(r):
            wm = _wiki_meta(r)
            d["row_type"] = "wiki"
            d["is_wiki"] = True
            d["depth"] = "synthesis"           # 综合页非 PDF 全文块
            d["has_pdf"] = False
            d["generated_at"] = wm.get("generated_at", "")
            d["generated_by"] = wm.get("generated_by", "")
            d["stale"] = bool(wm.get("stale"))
            d["by_agent"] = bool(wm.get("by_agent"))     # 🤖 agent 写回、未核验（供前端标记/剔除）
            d["wiki_sources"] = wm.get("sources", [])
        else:
            d["is_wiki"] = False
        _attach_weight(d, wr)
        out.append(d)
    return out

# ═══ C4/F6：向量「找相似」——用已存向量取近邻 ═══════════════════
def neighbors(key, topk=8):
    """给一篇 key 返回向量近邻（cosine），排除自身、剔除 wiki 行、按 key 聚合去重。
       优先复用该 key 已入表的向量（chunk 行优先于 meta 行）；都取不到则现场 encode 其标题。
       返回 list（每条结构与 search_full 输出一致，前端复用 resultCard）；
       light 模式 / 无表 / 取不到向量 / 无标题 → None（上层回 {ok:false}，前端回退抽词法）。"""
    if STATE.get("mode") != "full" or "tbl" not in M:
        return None
    recs = M.get("records", {})
    qv, title = None, ""
    for r in recs.values():                       # 找该 key 的一条已存向量（chunk 优先）
        if r.get("key") != key:
            continue
        title = r.get("title", "") or title
        v = r.get("vector")
        if v is not None:
            if r.get("row_type") != "meta":       # chunk 行向量最贴近全文，命中即用
                qv = v; break
            if qv is None:                         # 暂存 meta 行向量作兜底
                qv = v
    if qv is None:                                # 无已存向量 → 现场 encode 标题
        if not title:
            p = (M.get("papers") or {}).get(key)
            title = (p or {}).get("title", "") if p else ""
        if not title or "embed" not in M:
            return None
        try:
            qv = [float(x) for x in M["embed"].encode([title], max_length=256)[0]]
        except Exception:
            return None
    try:
        hits = M["tbl"].search(list(qv)).metric("cosine").limit(max(topk * 5, 40)).to_list()
    except Exception:
        return None
    seen, out = set(), []
    for h in hits:
        k = h.get("key", "")
        if not k or k == key or k in seen or _is_wiki(h):
            continue
        seen.add(k)
        r = recs.get(h.get("chunk_id")) or h
        sim = round(1.0 - float(h.get("_distance", 0.0)), 4)   # cosine 距离→相似度
        tier = _tier_of(r)
        deep = (r.get("row_type") != "meta")
        d = {
            "chunk_id": h.get("chunk_id"), "score": sim,
            "journal_tier": tier, "tier_rank": JT.rank_of(tier),
            "title": r.get("title", ""), "author": r.get("author", ""),
            "year": r.get("year", ""), "journal": r.get("journal", ""),
            "doi": r.get("doi", ""), "page": r.get("page"), "key": k,
            "official_pages": r.get("official_pages", ""),
            "row_type": r.get("row_type", "chunk"),
            "depth": "full" if deep else "abstract",
            "has_pdf": r.get("has_pdf", True),
            "heading": r.get("heading", ""),
            "text": r.get("text", ""), "context": r.get("parent_text", ""),
            "citation": _page_cite(r), "is_wiki": False,
        }
        _attach_weight(d, _weight_res(r))
        out.append(d)
        if len(out) >= topk:
            break
    return out


# ═══ 综合层：wiki 页嵌入入表（进程内即时可搜）════════════════════
def _fit_row_to_schema(full, vec):
    """按当前表 schema 逐列取值：旧表无 row_type/ingested_at 等列时自动省略，
       表有而 full 无的列填安全默认。复用 embed_index.py:54 的"按现表 schema 决定列"思路，
       保证向文献库那张真实表插 wiki 行时不会 schema 不匹配。"""
    import pyarrow as pa
    row = {}
    for f in M["tbl"].schema:
        name = f.name
        if name in full:
            row[name] = full[name]
        elif pa.types.is_boolean(f.type):
            row[name] = False
        elif pa.types.is_integer(f.type) or pa.types.is_floating(f.type):
            row[name] = None
        elif pa.types.is_list(f.type) or pa.types.is_fixed_size_list(f.type):
            row[name] = vec           # 唯一的向量列
        else:
            row[name] = ""            # string/其它 → 空串（覆盖 not-null 的 journal_tier）
    return row

def index_wiki_page(page_id, title, body, meta):
    """把一个 wiki 页嵌入并写进同一张 LanceDB 表（chunk_id="{id}::wiki"），
       并即时登记进内存（M["records"]/M["wiki"]），使**本进程内立刻可检索**。
       仅 full 模式（有表 + 嵌入器）能入表；否则返回 False（页面已存盘，重建索引后可搜）。"""
    M.setdefault("wiki", {})[page_id] = meta   # 无论能否入表，先登记页元数据（供列表/标注/降权）
    if STATE.get("mode") != "full" or "tbl" not in M or "embed" not in M:
        return False
    try:
        vec = [float(x) for x in M["embed"].encode([f"{title}\n{body}"], max_length=512)[0]]
    except Exception as e:
        log("wiki 嵌入失败：", e); return False
    cid = f"{page_id}::wiki"
    full = {
        "chunk_id": cid, "key": page_id, "page": None, "heading": title,
        "text": body, "parent_text": body, "title": title,
        "author": "", "year": "", "journal": "", "doi": "", "langid": "zh",
        "vector": vec, "journal_tier": "",
        "row_type": "wiki", "itemtype": "wiki", "official_pages": "",
        "has_pdf": False, "ingested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        try:
            M["tbl"].delete(f"chunk_id = '{cid}'")   # 幂等：重生/覆盖先删同 id 旧行
        except Exception:
            pass
        M["tbl"].add([_fit_row_to_schema(full, vec)])
        M["records"][cid] = full
        return True
    except Exception as e:
        log("wiki 入表失败：", e); return False

def delete_wiki_page(page_id):
    """一键"不保存"的表侧：删该 wiki 页的表行 + 内存登记（幂等）。仅 full 模式有表。
       wiki 行 key==page_id，复用 dbutil.key_predicate 精确幂等删（0 行无副作用）。
       返回该页此前是否存在（用于上层判定 deleted，保证重复删不误报）。

       按 M["tbl"] 而非 STATE["mode"] 判断有无表：_load_wiki_index 的存量清理在
       STATE["mode"] 被置为 "full" **之前**就调本函数，用 mode 判断会静默跳过删表行
       —— 内存清了、表行残留，日志却报成功。"""
    cid = f"{page_id}::wiki"
    existed = (page_id in (M.get("wiki") or {})) or (cid in (M.get("records") or {}))
    (M.get("wiki") or {}).pop(page_id, None)
    (M.get("records") or {}).pop(cid, None)
    if "tbl" not in M:
        return existed
    try:
        from dbutil import key_predicate
        pred = key_predicate([page_id])
        if pred:
            M["tbl"].delete(pred)
        return existed
    except Exception as e:
        log("wiki 删表行失败：", e); return False

# ═══ 检索：L-only 词法模式 ══════════════════════════════════════
def search_light(query, topk, sort, keys=None):
    toks = tokenize(query)
    if not toks:
        return []
    k = min(max(50, topk * 4), len(M["meta_ids"]))
    res, scs = M["meta_bm25"].retrieve([toks], k=k)
    items = []
    for i, s in zip(res[0], scs[0]):
        kk = M["meta_ids"][int(i)]
        if keys is not None and kk not in keys:      # F11：分类白名单过滤
            continue
        p = M["papers"].get(kk)
        if p:
            items.append((p, float(s), p.get("journal_tier", "未知"), _weight_res(p)))
    _apply_sort(items, sort, lex=True)
    out = []
    for p, sc, tier, wr in items[:topk]:
        d = {
            "chunk_id": f"{p['key']}::meta", "score": round(sc, 4),
            "journal_tier": tier, "tier_rank": JT.rank_of(tier),
            "title": p.get("title", ""), "author": p.get("author", ""),
            "year": p.get("year", ""), "journal": p.get("journal", ""),
            "doi": p.get("doi", ""), "page": None, "key": p.get("key", ""),
            "official_pages": p.get("official_pages", ""),
            "row_type": "meta", "depth": "abstract", "has_pdf": p.get("has_pdf", False),
            "heading": "",
            "text": (p.get("abstract") or p.get("title") or ""),
            "context": p.get("abstract", ""),
            "citation": _page_cite(p),
        }
        _attach_weight(d, wr)
        out.append(d)
    return out

def _wiki_effective(score, obj):
    """wiki 行的**有效排序分**（仅 full 模式，obj 为 chunk_id 字符串）。

    - 新鲜综合页：减一个小常数，只在同分时让位于原始文献（provenance 居中）。
    - 过时(stale)综合页：**乘性**重罚。必须是乘法：reranker 分尺度 0~10+，而 answer 页的标题
      就是用户原问题，reranker 拿 query 对 query 打分，分数天然虚高（实测 7.99，同题最相关的
      真论文才 4.34）。减 0.5 拉不动它——被新文献推翻的旧综合会继续霸占第一，
      agent 下次又把它当事实引用。这是幻觉复利的引擎，只有乘法能真正把它压到真论文之下。

    light 模式 obj 是题录 dict → 原分返回（wiki 本就不在 light 模式召回）。"""
    if not isinstance(obj, str):
        return score
    r = (M.get("records") or {}).get(obj)
    if not r or not _is_wiki(r):
        return score
    if _wiki_meta(r).get("stale"):
        return score * C.WIKI_STALE_FACTOR
    return score - C.WIKI_BASE_PENALTY

def _blend_bonus(x, lex):
    """blend 排序加成：优先用 journal_weight∈[0,1]（连续、按学科），回退旧离散 TIER_BONUS。
       x = (obj, score, tier[, weight_res])。wiki 降权不在这里，见 _wiki_effective。"""
    scale = 3 if lex else 1
    wr = x[3] if len(x) > 3 else None
    if wr and wr.get("weight") is not None:
        b = wr["weight"] * C.WEIGHT_BONUS_SCALE * scale
    else:
        b = C.TIER_BONUS.get(x[2], 0.0) * scale
    return b

def _apply_sort(items, sort, lex=False):
    """items: list of (obj, score, tier[, weight_res])。lex=True 时词法分尺度不同，加成放大。

    wiki 降权在**三种排序下都必须生效**：sort 是 MCP search_localkb 开放给 agent 的参数，
    若只有 blend 降权，agent 传 sort=relevance 即可让自己写回的未核验综合页与真论文平起平坐
    （幻觉复利的直通车）。light 模式 obj 是题录 dict，_wiki_effective 原样返回，不受影响。"""
    if sort == "relevance":
        items.sort(key=lambda x: -_wiki_effective(x[1], x[0]))
    elif sort == "tier":
        items.sort(key=lambda x: (JT.rank_of(x[2]), -_wiki_effective(x[1], x[0])))
    else:  # blend
        items.sort(key=lambda x: -(_wiki_effective(x[1], x[0]) + _blend_bonus(x, lex)))

# ═══ 统一入口 ════════════════════════════════════════════════════
def search(query, topk, sort=None, min_weight=0.0, keys=None):
    sort = sort if sort in ("relevance", "tier", "blend") else C.DEFAULT_SORT
    topk = max(1, int(topk))
    try:
        min_weight = float(min_weight or 0.0)
    except Exception:
        min_weight = 0.0
    # 有权重下限时多取些候选再过滤，尽量凑够 topk；无权重/待确认的条目保留、不误杀。
    fetch = topk if min_weight <= 0 else min(topk * 5, 200)
    # F11：限定分类时候选易被 topk 截断后所剩无几 → 放大 fetch，尽量凑够 topk。
    if keys is not None:
        fetch = max(fetch, min(topk * 10, 300))
    if STATE.get("mode") == "light":
        out = search_light(query, fetch, sort, keys=keys)
    else:
        out = search_full(query, fetch, sort, keys=keys)
    if min_weight > 0:
        out = [d for d in out
               if d.get("journal_weight") is None or d.get("journal_weight", 0) >= min_weight]
    return out[:topk]

# ═══ standalone（备用）═══════════════════════════════════════════
app = FastAPI()

class Q(BaseModel):
    query: str
    topk: int = C.RERANK_TOPK
    sort: Optional[str] = None

@app.get("/health")
def health():
    n = len(M.get("records", {})) if STATE.get("mode") == "full" else len(M.get("papers", {}))
    return {"ready": STATE["ready"], "mode": STATE.get("mode"), "n": n,
            "idle_s": round(time.time() - STATE["last_active"])}

@app.post("/search")
def do_search(q: Q):
    STATE["last_active"] = time.time()
    t0 = time.time()
    res = search(q.query, q.topk, q.sort)
    eff = q.sort if q.sort in ("relevance", "tier", "blend") else C.DEFAULT_SORT
    return {"query": q.query, "sort": eff, "took_ms": round((time.time()-t0)*1000), "results": res}

@app.post("/shutdown")
def shutdown():
    threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()
    return {"ok": True}

if __name__ == "__main__":
    load_all()
    uvicorn.run(app, host=C.DAEMON_HOST, port=C.DAEMON_PORT, log_level="warning")

# -*- coding: utf-8 -*-
"""
检索核心（被 server.py in-process import：R.load_all / R.search / R.STATE / R.M）。
三档就绪分级：
  - L-only 词法模式：只有 bm25_meta（无 LanceDB 表）→ 纯词法搜题录，0 模型加载、秒级就绪。
  - full 模式：有 LanceDB 表 → bge-m3 稠密 + bm25 词法 → RRF → reranker 精排（表已含 meta/chunk 行）。
standalone（python retriever.py）为备用，产品不用。
"""
import sys, json, time, threading, os, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C
from textutil import tokenize, load_legal_synonyms

import lancedb
import bm25s
import journal_tiers as JT
import source_rules as SR          # 法源/报告规则定档 + 手动改档（优先级高于期刊分级引擎）
try:
    import journal_grading as JG   # 期刊权重引擎（可选；缺失/出错则回退旧离散档，不影响检索）
except Exception as _jg_e:
    JG = None
    print("[retriever] journal_grading 未加载，期刊权重回退旧离散档：", _jg_e, flush=True)
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional
import uvicorn

STATE = {
    "last_active": time.time(), "ready": False, "mode": None,
    "retrieval_loaded": False, "retrieval_loading": False, "active_retrievals": 0,
}
M = {}  # 模型与索引句柄
_DICT_MTIME = 0   # 上次载入时 jieba 法律词典的 mtime；变化才热重载（见 load_all）
_RETRIEVAL_CV = threading.Condition(threading.RLock())
_RETRIEVAL_KEYS = ("embed", "rerank", "bm25", "bm25_ids", "meta_bm25", "meta_ids")

def log(*a): print("[retriever]", *a, flush=True)

# ═══ 加载 ════════════════════════════════════════════════════════
def load_all():
    """刷新库目录与轻量句柄；检索模型/BM25 留到首次检索再加载。"""
    victims = []
    try:
        with _RETRIEVAL_CV:
            # 建库收尾可能与上一条慢查询相撞；等它退出后再换目录/表句柄，避免半新半旧。
            while STATE.get("active_retrievals", 0) > 0:
                _RETRIEVAL_CV.wait(timeout=0.5)
            victims = _drop_retrieval_locked()
            STATE["ready"] = False
            try:
                _load_catalog_locked()
            finally:
                _RETRIEVAL_CV.notify_all()
    finally:
        # ONNX Session / BM25 数组在锁外析构，避免下一条请求长时间卡在生命周期锁上。
        victims.clear()
        try:
            import gc; gc.collect()
        except Exception:
            pass


def _load_catalog_locked():
    t0 = time.time()
    STATE["mode"] = None
    _WEIGHT_MEMO.clear()   # 换库/改档/重载后清权重缓存，避免串旧值
    # 重载前先丢掉旧目录/句柄。最重要的是清掉历史版本留下的 records：它曾把 LanceDB
    # 全表（含 1024 维向量）物化成 Python dict/list/float，20 万行会膨胀到约 10GB。
    for _k in ("tbl", "row_count", "records", "papers", "statute_status", "wiki"):
        M.pop(_k, None)
    # 词典/分级热重载：build 子进程重写了 jieba 法律词典与 journal_tiers.json 后，server 进程若不重载，
    # 查询侧分词/期刊分级会一直用旧数据、新入典术语搜不到（#48/#49）。按 mtime 变化才重载，避免每次重建 FREQ。
    try:
        import textutil as _TU
        global _DICT_MTIME
        _mt = os.path.getmtime(C.LEGAL_DICT) if C.LEGAL_DICT.exists() else 0
        if _mt != _DICT_MTIME:
            _TU.reload_userdict()
            _DICT_MTIME = _mt
    except Exception as e:
        log("jieba 词典热重载跳过：", e)
    try:
        JT.reload()
    except Exception as e:
        log("journal_tiers 热重载跳过：", e)
    db = lancedb.connect(str(C.LANCEDB_DIR))
    tbl_exists = C.TABLE_NAME in db.table_names()
    meta_ready = (C.BM25_META_DIR / "bm25_meta_ids.json").exists()

    if not tbl_exists and not meta_ready:
        STATE["mode"] = None; STATE["ready"] = False
        log("未建库（无表、无 L 档）——等首启向导 POST /index/light")
        return

    if not tbl_exists and meta_ready:
        _load_light_catalog()
        _load_wiki_index()
        STATE["mode"] = "light"; STATE["ready"] = True
        log(f"L档目录就绪：{len(M['papers'])} 篇；词法索引将在首次检索时加载，用时 {time.time()-t0:.1f}s")
        return

    # full 模式启动只开轻量表句柄、读题录；ONNX/API 客户端与 BM25 首次检索才加载。
    M["tbl"] = db.open_table(C.TABLE_NAME)
    # ★ 绝不再 tbl.to_arrow().to_pylist() 全表物化。向量留在 LanceDB；每次检索只取
    # RRF 后的几十/百余条候选，且候选详情明确不读取 vector 列。
    M["row_count"] = M["tbl"].count_rows()
    # full 模式也载入题录字典：① 顶栏正确篇数（否则显示 0） ② 非深索篇的 meta 兜底展示
    if C.PAPERS_JSONL.exists():
        pp = {}
        with open(C.PAPERS_JSONL, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    _p = json.loads(line); pp[_p["key"]] = _p
        M["papers"] = pp
    _build_statute_map()   # EN-L5：full 模式同样要有 key→statute_status（输出徽标+已废止降权）
    _load_wiki_index()
    STATE["mode"] = "full"; STATE["ready"] = True
    log(f"full 目录就绪（检索组件按需加载）：{M['row_count']} 块 / {len(M.get('papers', {}))} 篇"
        f" / {len(M.get('wiki', {}))} 综合页，用时 {time.time()-t0:.1f}s")

def _load_light_catalog():
    papers = {}
    if C.PAPERS_JSONL.exists():
        with open(C.PAPERS_JSONL, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    p = json.loads(line)
                    papers[p["key"]] = p
    M["papers"] = papers
    _build_statute_map()


def _drop_retrieval_locked():
    """在 _RETRIEVAL_CV 内移走重组件，返回旧对象以便锁外析构。LanceDB 表句柄刻意保留。"""
    victims = [M.pop(k) for k in _RETRIEVAL_KEYS if k in M]
    STATE["retrieval_loaded"] = False
    STATE["retrieval_loading"] = False
    return victims


def _load_retrieval_locked():
    """首次使用时加载当前模式所需组件。调用者必须持有 _RETRIEVAL_CV。"""
    if STATE.get("retrieval_loaded"):
        return
    if not STATE.get("ready") or STATE.get("mode") not in ("light", "full"):
        raise RuntimeError("检索索引尚未就绪")
    STATE["retrieval_loading"] = True
    t0 = time.time()
    new = {}
    try:
        if STATE.get("mode") == "light":
            new["meta_bm25"] = bm25s.BM25.load(str(C.BM25_META_DIR), load_corpus=False)
            new["meta_ids"] = json.loads(
                (C.BM25_META_DIR / "bm25_meta_ids.json").read_text(encoding="utf-8"))
        else:
            from embedder import get_embedder
            from reranker import get_reranker
            import settings as S
            api = S.is_api()
            log("按需加载嵌入器（" + ("API" if api else "本地 ONNX-INT8") + "）...")
            new["embed"] = get_embedder()
            log("按需加载重排器（" + ("API" if api else "本地 ONNX") + "）...")
            new["rerank"] = get_reranker()
            new["bm25"] = bm25s.BM25.load(str(C.BM25_DIR), load_corpus=False)
            new["bm25_ids"] = json.loads(
                (C.BM25_DIR / "bm25_ids.json").read_text(encoding="utf-8"))
        M.update(new)
        STATE["retrieval_loaded"] = True
        STATE["last_active"] = time.time()
        log(f"检索组件已按需加载（{STATE.get('mode')}），用时 {time.time()-t0:.1f}s")
        # 冷启动时缺失的 wiki 检索行在组件真正可用后补嵌；失败不影响本次检索。
        if STATE.get("mode") == "full":
            try:
                import wiki_store as W
                W.reindex_missing_pages()
            except Exception as e:
                log("wiki 检索行按需回灌跳过：", e)
    except Exception:
        for k in _RETRIEVAL_KEYS:
            M.pop(k, None)
        STATE["retrieval_loaded"] = False
        raise
    finally:
        STATE["retrieval_loading"] = False
        _RETRIEVAL_CV.notify_all()


def _begin_retrieval(load_if_cold=True):
    """登记一次检索使用；活动计数保证空闲线程不会在请求中途释放组件。"""
    with _RETRIEVAL_CV:
        if not STATE.get("retrieval_loaded"):
            if not load_if_cold:
                return False
            _load_retrieval_locked()
        STATE["active_retrievals"] = int(STATE.get("active_retrievals", 0)) + 1
        STATE["last_active"] = time.time()
        return True


def _end_retrieval():
    with _RETRIEVAL_CV:
        STATE["active_retrievals"] = max(0, int(STATE.get("active_retrievals", 0)) - 1)
        STATE["last_active"] = time.time()  # 从最后一次检索完成开始计空闲时间
        _RETRIEVAL_CV.notify_all()


def release_retrieval_if_idle(timeout_s, force=False):
    """达到空闲阈值后释放 ONNX/API 客户端与 BM25；活动检索期间绝不释放。"""
    victims = []
    with _RETRIEVAL_CV:
        if not STATE.get("retrieval_loaded") or STATE.get("active_retrievals", 0) > 0:
            return False
        idle_s = max(0.0, time.time() - float(STATE.get("last_active", time.time())))
        if not force and (float(timeout_s or 0) <= 0 or idle_s < float(timeout_s)):
            return False
        victims = _drop_retrieval_locked()
        _RETRIEVAL_CV.notify_all()
    victims.clear()
    try:
        import gc; gc.collect()
    except Exception:
        pass
    log("检索组件已释放：下次检索会自动重新加载")
    return True


def retrieval_status():
    def _snapshot():
        return {
            "loaded": bool(STATE.get("retrieval_loaded")),
            "loading": bool(STATE.get("retrieval_loading")),
            "active": int(STATE.get("active_retrievals", 0)),
            "idle_s": max(0, round(time.time() - float(STATE.get("last_active", time.time())))),
        }

    # 首次加载模型时 _load_retrieval_locked 会持有生命周期锁，避免别的请求看到半套组件。
    # 状态查询只读几个原子标量；若已明确处于 loading，就直接快照返回，设置页才能及时显示
    # “正在加载”，而不是跟着首条检索一起等到模型全载完。
    if STATE.get("retrieval_loading"):
        return _snapshot()
    with _RETRIEVAL_CV:
        return _snapshot()

def _build_statute_map():
    """EN-L5：从已载入的题录建 key→statute_status 映射（契约11）。只存非空值——
       法条只占库的小头，内存可忽略；LanceDB 表 schema 不加列，检索输出侧按此现算。"""
    M["statute_status"] = {k: p.get("statute_status") for k, p in (M.get("papers") or {}).items()
                           if p.get("statute_status")}

def _statute_status_of(key):
    """EN-L5：取一篇的时效标识（""｜"已修订"｜"已废止"），无映射/非法条 → ""。"""
    return (M.get("statute_status") or {}).get(key or "", "")


# 检索输出/重排实际需要的列。故意不含 vector：1024 维向量只有 dense 检索和“找相似”的
# 查询向量需要，候选详情若把它一并 to_list()，仍会制造大量 Python float。
_RESULT_COLUMNS = (
    "chunk_id", "key", "page", "heading", "text", "parent_text", "title", "author",
    "year", "journal", "issn", "doi", "langid", "journal_tier", "row_type", "itemtype",
    "official_pages", "has_pdf", "ingested_at",
)


def _existing_columns(columns):
    """按真实表 schema 裁列，兼容没有 row_type/issn 等字段的历史表。"""
    names = set(M["tbl"].schema.names)
    return [c for c in columns if c in names]


def _sql_str(value):
    """Lance SQL 字符串字面量；chunk_id/key 虽由程序生成，仍完整转义单引号。"""
    return "'" + str(value).replace("'", "''") + "'"


def _scan_where(predicate, columns, limit=None):
    """LanceDB 纯过滤查询；只读取指定列，绝不隐式带回 vector。"""
    if "tbl" not in M:
        return []
    cols = _existing_columns(columns)
    if not cols:
        return []
    q = M["tbl"].search(None).where(predicate)
    q = q.select(cols)
    if limit is not None:
        q = q.limit(max(1, int(limit)))
    return q.to_list()


def fetch_records(chunk_ids):
    """按 chunk_id 批量取候选详情，返回 id→row；向量始终留在 LanceDB。"""
    ids = list(dict.fromkeys(str(x) for x in (chunk_ids or []) if x))
    if not ids or "tbl" not in M:
        return {}
    pred = "chunk_id IN (" + ",".join(_sql_str(x) for x in ids) + ")"
    rows = _scan_where(pred, _RESULT_COLUMNS, limit=len(ids))
    return {r.get("chunk_id"): r for r in rows if r.get("chunk_id")}


def existing_chunk_ids(chunk_ids):
    """返回确实存在于表内的 chunk_id 集合（wiki 回灌/删除使用）。"""
    ids = list(dict.fromkeys(str(x) for x in (chunk_ids or []) if x))
    if not ids or "tbl" not in M:
        return set()
    pred = "chunk_id IN (" + ",".join(_sql_str(x) for x in ids) + ")"
    return {r.get("chunk_id") for r in _scan_where(pred, ("chunk_id",), limit=len(ids))
            if r.get("chunk_id")}


def _rows_for_key(key, columns, row_type=None, limit=64, page=None):
    if not key or "tbl" not in M:
        return []
    from dbutil import key_predicate
    use_type = row_type if row_type and "row_type" in M["tbl"].schema.names else None
    pred = key_predicate([key], row_type=use_type)
    if page is not None and "page" in M["tbl"].schema.names:
        try:
            # page 是 LanceDB 的整数列；把过滤下推，避免一部长法典超过 512 块时漏掉后面的条文。
            pred = f"({pred}) AND page = {int(page)}"
        except (TypeError, ValueError):
            pass
    return _scan_where(pred, columns, limit=limit) if pred else []


def find_statute_heading(key, page):
    """按 (key,page) 查法条 heading；代替遍历全表内存字典。"""
    if not key or page is None:
        return ""
    rows = _rows_for_key(key, ("page", "heading"), row_type="chunk", limit=8, page=page)
    for r in rows:
        if r.get("page") == page and "条" in (r.get("heading") or ""):
            return r.get("heading") or ""
    return ""

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
    if "tbl" not in M:
        return
    present = existing_chunk_ids([f"{pid}::wiki" for pid in M["wiki"]])
    stale_rows = [pid for pid, meta in M["wiki"].items()
                  if W.is_degraded(meta.get("generated_by", "")) and f"{pid}::wiki" in present]
    for pid in stale_rows:
        try:
            delete_wiki_page(pid)      # 删表行 + 内存登记（幂等）；页面仍可在综述库阅读
            log(f"降级综合页 {pid} 已移出检索表（仍可在综述库阅读）")
        except Exception as e:
            log(f"清理降级页 {pid} 失败：", e)
    if stale_rows:
        M["wiki"] = W.index_map()      # delete_wiki_page 会 pop 掉条目，重载回其余页的 meta
    # 存量回灌需要嵌入器。冷启动故意不加载检索组件，等首次检索的
    # _load_retrieval_locked() 再补；重载发生在组件已加载态时仍可立即补。
    if STATE.get("retrieval_loaded"):
        try:
            W.reindex_missing_pages()
        except Exception as e:
            log("综合页回灌失败（不影响其余检索）：", e)

# ═══ 检索：full 模式 ════════════════════════════════════════════
def dense_search(q, k):
    qv = M["embed"].encode([q], max_length=256)[0]
    hits = (M["tbl"].search(qv.tolist()).metric("cosine")
            .select(_existing_columns(("chunk_id",)) + ["_distance"]).limit(k).to_list())
    return [h["chunk_id"] for h in hits]

# EN-L3：同义词命中判定里的"含中文"检查（英文缩写组员如 ai/dpa 只走 token 全等通道，
# 不走 substring 通道——"ai" 是 "chain"/"detail" 的子串，substring 会大面积误命中）
_CJK_RE = re.compile(r'[一-鿿]')

def _expand_tokens(query, toks):
    """EN-L3：查询侧同义词 OR 扩展（契约12）。**只扩 bm25 的查询词袋**——索引侧一个字节不动，
       所以零重建成本；dense 不参与（语义向量本身能泛化同义词，再扩是画蛇添足）。
       命中判定双通道：① 分词后 token 恰等于组员（组员已由 build_dict 进 jieba 词典，
       正常能整词切出）；② 中文组员兜底用原始查询串包含判断（词典未重建的旧库里组员会被
       切碎，token 全等失配，substring 仍能接住）。命中后把组内**其它**词 tokenize 并入
       词袋——bm25 词袋天然 OR 语义，加词只增召回不丢原词。C.SYN_EXPAND=False 一键关闭。"""
    if not getattr(C, "SYN_EXPAND", True) or not toks:
        return toks
    try:
        groups = load_legal_synonyms()
    except Exception:
        return toks
    if not groups:
        return toks
    ql = (query or "").lower()
    tset = set(toks)
    extra = []
    for g in groups:
        # substring 兜底通道要求组员 ≥3 字：2 字简称（未检/法援/家暴…）跨词边界误命中率高
        # （如「尚未检验」含「未检」）；它们已进 jieba 词典，走 token 全等通道即可命中
        hit = {w for w in g if w in tset or (len(w) >= 3 and _CJK_RE.search(w) and w in ql)}
        if not hit:
            continue
        for w in g - hit:
            for t in tokenize(w):
                if t not in tset:
                    tset.add(t)
                    extra.append(t)
    return toks + extra

def bm25_search(q, k):
    toks = _expand_tokens(q, tokenize(q))   # EN-L3：查询侧同义扩展（索引侧不动）
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

_WEIGHT_MEMO = {}   # (discipline, journal, issn) -> weight_result；load_all 时清空（改档/换库后不串旧值）

def _weight_res(r):
    """从全类型统一事实源取得评价；检索命中的少量候选允许惰性计算期刊目录。"""
    if _is_wiki(r):
        return None
    try:
        import grading_svc as GS
        base = dict((M.get("papers") or {}).get(r.get("key", "")) or {})
        base.update({k: v for k, v in r.items() if v not in (None, "", [])})
        return GS.evaluate_paper(base, compute=True)
    except Exception:
        return None

def _attach_weight(d, wr):
    """把统一评价挂到检索结果；旧字段继续保留供老客户端降级。"""
    d["journal_weight"] = wr.get("weight") if wr else None
    _t = wr.get("tier") if wr else None
    d["weight_tier"] = wr.get("band_name") if wr else None
    d["weight_tier_code"] = _t                                    # 原始 T? 码（调试/兼容用）
    d["weight_needs_review"] = bool(wr.get("needs_review") or wr.get("needsReview")) if wr else False
    d["weight_src"] = wr.get("src") if wr else None               # manual=手动改档 / rule=法源报告规则（前端标记）
    for key in ("source_type", "source_type_name", "objective_label", "band", "band_name",
                "standard_band_name", "band_rank", "internal_tier", "manual", "hit_catalogs", "explain"):
        d[key] = wr.get(key) if wr else None
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

def search_full(query, topk, sort, keys=None, max_per_key=None):
    # BF6：白名单场景源头召回放大到 150（见 config.FILTER_SRC_TOPK）——限定分类后
    # 50 条粗召回被过滤剩不下几条，池子先天不足；常规检索维持 50 不拖慢。
    src_k = C.FILTER_SRC_TOPK if keys is not None else 0
    di = dense_search(query, src_k or C.DENSE_TOPK)
    bi = bm25_search(query, src_k or C.BM25_TOPK)
    # 重排池与最终 topk 解耦：重排(cross-encoder)是最贵的一步，topk 调大(无限滚动)时不让候选爆炸。
    # 无 keys：dense/bm25 各取 50 → RRF 去重后约 ~100，池上限 64 足够；避免 topk=40 时重排几百条拖到十几秒。
    # 有 keys（BF6）：候选已按白名单过滤过、rerank 每条都花在有效候选上，池放宽到 128 提召回。
    pool = (min(max(100, topk * 4), 128) if keys is not None
            else min(max(50, topk * 2), 64))
    # BF6：F11 的白名单过滤必须在截池**之前**——先截 64 再过滤，限定的小分类可能一条都不剩；
    # 对 RRF 全量融合结果先过滤再截池，keys=None 时行为与旧版一致。
    fused = rrf(di, bi)
    records = fetch_records(fused)
    cand = [cid for cid in fused
            if cid in records
            and (keys is None or records[cid].get("key") in keys)][:pool]
    if not cand:
        return []
    scores = M["rerank"].scores(query, [records[cid].get("text") or "" for cid in cand])
    ranked = sorted(zip(cand, scores), key=lambda x: -x[1])
    # 发现型检索默认每篇最多 C.MAX_PER_KEY 段；不拿同一篇的溢出段硬凑 topk。
    # 只有 verify_claim/read-source 这类明确的定向核验才会传入更高的 max_per_key。
    per_key_limit = max(1, int(max_per_key or C.MAX_PER_KEY))
    per_key, picked = {}, []
    meta_idx = {}   # key -> 已入选 meta 行在 picked 中的下标，供随后到达的同篇 chunk 顶替
    for cid, sc in ranked:
        r = records[cid]
        k = r.get("key", cid)
        rtype = r.get("row_type")
        # 若该 key 已有 chunk 行入选，跳过它的 meta 行（避免同篇既出摘要又出正文、前端深索徽章自相矛盾）
        if rtype == "meta" and any(records[c].get("key") == k and records[c].get("row_type") == "chunk"
                                   for c, _ in picked):
            continue
        # meta 先入选、随后到了同篇更具体的 chunk：用 chunk 顶替那条 meta（名额不变，不新造共存）
        if rtype == "chunk" and k in meta_idx:
            picked[meta_idx.pop(k)] = (cid, sc)
            continue
        if per_key.get(k, 0) >= per_key_limit:
            continue
        per_key[k] = per_key.get(k, 0) + 1
        picked.append((cid, sc))
        if rtype == "meta":
            meta_idx[k] = len(picked) - 1
        if len(picked) >= topk:
            break
    top = [(records[cid], float(sc), _tier_of(records[cid]), _weight_res(records[cid]))
           for cid, sc in picked]
    _apply_sort(top, sort)
    out = []
    for r, sc, tier, wr in top:
        cid = r.get("chunk_id", "")
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
            # EN-L2/L5：itemtype 供引注模板分派（statute/report），statute_status 是
            # 法条时效徽标（契约11：""｜"已修订"｜"已废止"；按 papers.jsonl 现算，不动表 schema）
            "itemtype": r.get("itemtype", ""),
            "statute_status": _statute_status_of(r.get("key", "")),
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
            d["by_agent"] = bool(wm.get("by_agent"))     # 🤖 agent 写回（供前端标记/剔除）
            d["verified_at"] = wm.get("verified_at", "") or ""   # 人工核验章：供 agent 侧文本标"未核实"、供降权豁免
            d["wiki_sources"] = wm.get("sources", [])
        else:
            d["is_wiki"] = False
        _attach_weight(d, wr)
        out.append(d)
    return out

# ═══ C4/F6：向量「找相似」——用已存向量取近邻 ═══════════════════
def _neighbors_loaded(key, topk=8):
    """给一篇 key 返回向量近邻（cosine），排除自身、剔除 wiki 行、按 key 聚合去重。
       优先复用该 key 已入表的向量（chunk 行优先于 meta 行）；都取不到则现场 encode 其标题。
       返回 list（每条结构与 search_full 输出一致，前端复用 resultCard）；
       light 模式 / 无表 / 取不到向量 / 无标题 → None（上层回 {ok:false}，前端回退抽词法）。"""
    if STATE.get("mode") != "full" or "tbl" not in M:
        return None
    qv, title = None, ""
    # 只为这一篇读取一条向量：优先 chunk，退回 meta；不再遍历 20 万行 records。
    source_cols = ("vector", "title", "row_type")
    src_rows = _rows_for_key(key, source_cols, row_type="chunk", limit=1)
    if not src_rows:
        src_rows = _rows_for_key(key, source_cols, row_type="meta", limit=1)
    if not src_rows:                               # 旧表无 row_type 时裸 key 兜底
        src_rows = _rows_for_key(key, source_cols, limit=1)
    if src_rows:
        title = src_rows[0].get("title", "") or ""
        qv = src_rows[0].get("vector")
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
        hits = (M["tbl"].search(list(qv)).metric("cosine")
                .select(_existing_columns(_RESULT_COLUMNS) + ["_distance"])
                .limit(max(topk * 5, 40)).to_list())
    except Exception:
        return None
    seen, out = set(), []
    for h in hits:
        k = h.get("key", "")
        if not k or k == key or k in seen or _is_wiki(h):
            continue
        seen.add(k)
        r = h
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
            # EN-L2/L5：与 search_full 输出保持同构（前端复用 resultCard）
            "itemtype": r.get("itemtype", ""),
            "statute_status": _statute_status_of(k),
            "text": r.get("text", ""), "context": r.get("parent_text", ""),
            "citation": _page_cite(r), "is_wiki": False,
        }
        _attach_weight(d, _weight_res(r))
        out.append(d)
        if len(out) >= topk:
            break
    return out


def neighbors(key, topk=8):
    """公开的相似文献入口；与普通检索共用按需加载和活动计数。"""
    if STATE.get("mode") != "full" or "tbl" not in M:
        return None
    _begin_retrieval(load_if_cold=True)
    try:
        return _neighbors_loaded(key, topk)
    finally:
        _end_retrieval()


# ═══ 综合层：wiki 页嵌入入表（组件热态即时；冷态下次检索回灌）══════
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

def _index_wiki_page_loaded(page_id, title, body, meta):
    """把一个 wiki 页嵌入并写进同一张 LanceDB 表（chunk_id="{id}::wiki"），
       并即时登记进 M["wiki"]，使**本进程内立刻可检索**。正文行无需常驻内存，
       下一次 dense/候选查询会直接从 LanceDB 读到。
       仅 full 模式且检索组件热态时调用；冷态由公开包装器推迟到下一次检索回灌。

       已知限制（C5，minor·自愈）：这里只即时进【稠密向量】通道，不更新进程内 BM25 倒排
       （M["bm25"]/M["bm25_ids"] 是建库期从表整体构建的，增量维护要重算 IDF/文档长度，
       在最热的检索路径上动刀风险大）。因此新写回的 wiki 页在【下一次索引重建】前，纯词法/专名/
       法条号查询可能召不回它（answer 页因标题≈原问、稠密通道天然高分不受影响；concept/entity 靠
       生僻专名命中才暴露）。每天的自动更新会重建索引、把 wiki 行纳入 BM25——即自愈，无需手动干预。"""
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
        existed = cid in existing_chunk_ids([cid])
        try:
            M["tbl"].delete(f"chunk_id = '{cid}'")   # 幂等：重生/覆盖先删同 id 旧行
        except Exception:
            pass
        M["tbl"].add([_fit_row_to_schema(full, vec)])
        if not existed:
            M["row_count"] = int(M.get("row_count", 0)) + 1
        return True
    except Exception as e:
        log("wiki 入表失败：", e); return False


def index_wiki_page(page_id, title, body, meta):
    """保存 wiki 元数据；检索组件已驻留时即时嵌入，冷态则留待下一次检索自动回灌。"""
    M.setdefault("wiki", {})[page_id] = meta
    if STATE.get("mode") != "full" or "tbl" not in M:
        return False
    if not _begin_retrieval(load_if_cold=False):
        return False
    try:
        return _index_wiki_page_loaded(page_id, title, body, meta)
    finally:
        _end_retrieval()


def delete_wiki_page(page_id):
    """一键"不保存"的表侧：删该 wiki 页的表行 + 内存登记（幂等）。仅 full 模式有表。
       wiki 行 key==page_id，复用 dbutil.key_predicate 精确幂等删（0 行无副作用）。
       返回该页此前是否存在（用于上层判定 deleted，保证重复删不误报）。

       按 M["tbl"] 而非 STATE["mode"] 判断有无表：_load_wiki_index 的存量清理在
    STATE["mode"] 被置为 "full" **之前**就调本函数，用 mode 判断会静默跳过删表行
    —— 内存清了、表行残留，日志却报成功。"""
    cid = f"{page_id}::wiki"
    row_existed = cid in existing_chunk_ids([cid])
    existed = (page_id in (M.get("wiki") or {})) or row_existed
    (M.get("wiki") or {}).pop(page_id, None)
    if "tbl" not in M:
        return existed
    try:
        from dbutil import key_predicate
        pred = key_predicate([page_id])
        if pred:
            M["tbl"].delete(pred)
            if row_existed:
                M["row_count"] = max(0, int(M.get("row_count", 0)) - 1)
        return existed
    except Exception as e:
        log("wiki 删表行失败：", e); return False

# ═══ 检索：L-only 词法模式 ══════════════════════════════════════
def search_light(query, topk, sort, keys=None):
    toks = _expand_tokens(query, tokenize(query))   # EN-L3：L 档同为 bm25，同义扩展一并生效
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
            # EN-L2/L5：itemtype/时效徽标（light 行直接来自 papers.jsonl，字段现成）
            "itemtype": p.get("itemtype", ""),
            "statute_status": _statute_status_of(p.get("key", "")),
            "text": (p.get("abstract") or p.get("title") or ""),
            "context": p.get("abstract", ""),
            "citation": _page_cite(p),
        }
        _attach_weight(d, wr)
        out.append(d)
    return out

def _wiki_effective(score, obj):
    """wiki 行的**有效排序分**（full 模式 obj 为候选行 dict）。

    - 新鲜综合页：减一个小常数，只在同分时让位于原始文献（provenance 居中）。
    - 过时(stale)综合页：**乘性**重罚。必须是乘法：reranker 分尺度 0~10+，而 answer 页的标题
      就是用户原问题，reranker 拿 query 对 query 打分，分数天然虚高（实测 7.99，同题最相关的
      真论文才 4.34）。减 0.5 拉不动它——被新文献推翻的旧综合会继续霸占第一，
      agent 下次又把它当事实引用。这是幻觉复利的引擎，只有乘法能真正把它压到真论文之下。

    light 模式 obj 也是题录 dict，但不会满足 _is_wiki，故原分返回。"""
    r = obj if isinstance(obj, dict) else None
    if not r or not _is_wiki(r):
        return score
    wm = _wiki_meta(r)
    if wm.get("stale"):
        # BF5：乘法只在正分域是"降权"——reranker 分可为负（不相关时 -5 上下），
        # 负分 ×0.3 反而离 0 更近 = 反向提权（config.py:135-142 只量了正分尺度，漏了负分域）。
        # 负分改除以 factor：更负 = 更靠后，正负两域都是货真价实的重罚。
        # stale 最重、独占一档。
        return score * C.WIKI_STALE_FACTOR if score > 0 else score / C.WIKI_STALE_FACTOR
    # 可信度分层：answer 折减 与 未核验折减 可叠乘。
    # - answer 页：标题≈原查询、reranker 分虚高 3 分+，乘 WIKI_ANSWER_FACTOR 压回真论文之下。
    # - agent 写回且未经人工核验(by_agent 且 verified_at 空)：乘 WIKI_UNVERIFIED_FACTOR，
    #   把"自己上次没核过的草稿"压到人工页/真论文之后；人工核验过的页(verified_at 有值)豁免此刀。
    mult = 1.0
    if wm.get("kind") == "answer":
        mult *= C.WIKI_ANSWER_FACTOR
    if wm.get("by_agent") and not wm.get("verified_at"):
        mult *= getattr(C, "WIKI_UNVERIFIED_FACTOR", 0.6)
    if mult >= 1.0:
        # 新鲜、非 answer、且人工写/已核验的页：只减一个小常数，同分让位于原始文献。
        return score - C.WIKI_BASE_PENALTY
    # 乘法折减；负分域同 stale 用除法，避免"负分×factor 反而提权"的老坑。
    return score * mult if score > 0 else score / mult

def _statute_eff(score, obj):
    """EN-L5：已废止法条的排序降权（已修订不降权、只出徽标）。
       写法沿用 BF5 的负分安全式：正分乘 factor、负分**除** factor——reranker 分可为负，
       负分×0.5 反而更靠近 0 = 反向提权（wiki 降权踩过的同一个坑，别再踩）。
       obj：full/light 模式都直接传候选行 dict，不依赖全表内存字典。"""
    key = ""
    if isinstance(obj, dict):
        key = obj.get("key", "")
    if key and _statute_status_of(key) == "已废止":
        f = getattr(C, "STATUTE_REPEALED_FACTOR", 0.5)
        return score * f if score > 0 else score / f
    return score

def _effective(score, obj):
    """排序用的有效分 = wiki 降权（BF5）∘ 已废止法条降权（EN-L5），两者独立叠加。"""
    return _statute_eff(_wiki_effective(score, obj), obj)

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
    （幻觉复利的直通车）。light 模式 obj 是题录 dict，_wiki_effective 原样返回，不受影响。
    EN-L5：已废止法条降权同理走 _effective，三种排序统一生效。"""
    if sort == "relevance":
        items.sort(key=lambda x: -_effective(x[1], x[0]))
    elif sort == "tier":
        # 手动改档/法源规则的结果带 rank（0~5，与旧离散 rank 同尺度）——tier 排序也要跟着走；
        # 期刊分级引擎结果不带 rank，维持旧离散档主键（行为不变）。
        def _rk(x):
            wr = x[3] if len(x) > 3 else None
            if wr and wr.get("rank") is not None:
                return wr["rank"]
            return JT.rank_of(x[2])
        items.sort(key=lambda x: (_rk(x), -_effective(x[1], x[0])))
    else:  # blend
        items.sort(key=lambda x: -(_effective(x[1], x[0]) + _blend_bonus(x, lex)))

# ═══ 统一入口 ════════════════════════════════════════════════════
def _search_loaded(query, topk, sort=None, min_weight=0.0, keys=None, max_per_key=None):
    sort = sort if sort in ("relevance", "tier", "blend") else C.DEFAULT_SORT
    topk = max(1, int(topk))
    try:
        min_weight = float(min_weight or 0.0)
    except Exception:
        min_weight = 0.0
    # 有权重下限时多取些候选再过滤，尽量凑够 topk；无权重/待确认的条目保留、不误杀。
    # C6：基础档也多取一小截缓冲——被降权的 wiki 行（stale/未核验/answer）此前在 picked 阶段按【原始分】
    #     占掉一个 topk 名额，_apply_sort 只把它排到末尾却踢不出去，等于挤掉一篇真论文。多取缓冲后，
    #     _apply_sort 把降权行沉底、最后 out[:topk] 时它才真正让位给真论文。无降权行时结果不变。
    fetch = (topk + 12) if min_weight <= 0 else min(topk * 5, 200)
    # F11：限定分类时候选易被 topk 截断后所剩无几 → 放大 fetch，尽量凑够 topk。
    if keys is not None:
        fetch = max(fetch, min(topk * 10, 300))
    if STATE.get("mode") == "light":
        out = search_light(query, fetch, sort, keys=keys)
    else:
        out = search_full(query, fetch, sort, keys=keys, max_per_key=max_per_key)
    if min_weight > 0:
        out = [d for d in out
               if d.get("journal_weight") is None or d.get("journal_weight", 0) >= min_weight]
    return out[:topk]


def search(query, topk, sort=None, min_weight=0.0, keys=None, max_per_key=None):
    """统一公开入口。

    默认每篇最多返回 C.MAX_PER_KEY 段，保证发现型检索的文献覆盖面；定向核验可显式提高
    ``max_per_key``，在已经选中的文献内寻找更多相互印证的段落。
    """
    _begin_retrieval(load_if_cold=True)
    try:
        return _search_loaded(query, topk, sort, min_weight, keys, max_per_key)
    finally:
        _end_retrieval()

# ═══ standalone（备用）═══════════════════════════════════════════
app = FastAPI()

class Q(BaseModel):
    query: str
    topk: int = C.RERANK_TOPK
    sort: Optional[str] = None

@app.get("/health")
def health():
    n = int(M.get("row_count", 0)) if STATE.get("mode") == "full" else len(M.get("papers", {}))
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

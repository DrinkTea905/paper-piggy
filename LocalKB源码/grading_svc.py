# -*- coding: utf-8 -*-
"""
期刊分级服务层门面（F38-B）——学科感知 + 中文档名 + 持久缓存 + 后台预热。

为什么要缓存/预热：resolve_journal_weight 对未精确命中的刊走 fuzzy（遍历上万归一名，~40ms/刊），
2000 篇库去重刊名 ~700 个 → 冷算全库分布要 20+ 秒。方案 A（现算）配两层缓存化解：
  ① 逐刊 memo（按学科持久到 data/grading_memo.json）——跨重启复用，首次算过就永久快。
  ② 分布缓存（按 (学科, papers.jsonl mtime) 持久到 data/grading_dist.json）——/stats 直接命中。
再加后台预热（startup / 切学科后异步 warm），用户几乎不会同步撞上冷算。
引擎缺失/出错 → grade 返回 None，调用方回退旧 journal_tier，绝不 500。
"""
import sys, json, os, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C
import settings as S

try:
    import journal_grading as JG
except Exception as _e:
    JG = None
    print("[grading_svc] journal_grading 未加载，分级回退旧离散档：", _e, flush=True)

# tier code → 面向用户中文档名 / 排序 rank（前后端统一口径）
TIER_CN = {"T1": "权威", "T1b": "准权威", "T2": "核心", "T3": "次核心",
           "T4": "一般", "T5": "普通", "待确认": "待确认"}
TIER_RANK = {"T1": 0, "T1b": 1, "T2": 2, "T3": 3, "T4": 4, "T5": 5, "待确认": 6}

_LOCK = threading.RLock()
_MEMO = {}            # {disc: {journal: {tier,weight,cn,rank,needs_review}}}
_MEMO_LOADED = False
_MEMO_DIRTY = False
_DIST = {}            # {disc: {"mtime": float, "by_tier": [...], "by_journal": [...]}}
_DIST_LOADED = False
_WARMING = set()      # 正在预热的 (disc, mtime)，防重复起线程

MEMO_FILE = C.DATA / "grading_memo.json"
DIST_FILE = C.DATA / "grading_dist.json"


def _disc():
    try:
        return S.discipline()
    except Exception:
        return "law"


def _load_memo():
    global _MEMO, _MEMO_LOADED
    if _MEMO_LOADED:
        return
    try:
        if MEMO_FILE.exists():
            _MEMO = json.loads(MEMO_FILE.read_text(encoding="utf-8"))
    except Exception:
        _MEMO = {}
    _MEMO_LOADED = True


def _load_dist():
    global _DIST, _DIST_LOADED
    if _DIST_LOADED:
        return
    try:
        if DIST_FILE.exists():
            _DIST = json.loads(DIST_FILE.read_text(encoding="utf-8"))
    except Exception:
        _DIST = {}
    _DIST_LOADED = True


def _atomic_write(path, obj):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(obj, ensure_ascii=False)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(data, encoding="utf-8")
        # OneDrive/杀软临时占用会让 os.replace 抛 WinError 5：重试几次，仍失败则退化直写。
        for i in range(6):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                time.sleep(0.15 * (i + 1))
        path.write_text(data, encoding="utf-8")
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
    except Exception as e:
        print("[grading_svc] 写缓存失败：", e, flush=True)


def flush():
    """把脏 memo 落盘（预热末尾/进程退出前调）。"""
    global _MEMO_DIRTY
    with _LOCK:
        if _MEMO_DIRTY:
            _atomic_write(MEMO_FILE, _MEMO)
            _MEMO_DIRTY = False


def grade(journal, issn="", compute=True):
    """按当前锁定学科算一刊分级：{tier,weight,cn,rank,needs_review} 或 None。
       compute=False：只查 memo，命中返回、否则 None（绝不触发 fuzzy，供 /stats /papers 快路径用）。"""
    if JG is None or not journal:
        return None
    disc = _disc()
    with _LOCK:
        _load_memo()
        dm = _MEMO.get(disc)
        if dm is not None and journal in dm:
            return dm[journal]
    if not compute:
        return None
    try:
        r = JG.resolve_journal_weight({"journal": journal or "", "issn": issn or ""}, disc)
        t = r.get("tier") or "待确认"
        out = {"tier": t, "weight": r.get("weight"),
               "cn": TIER_CN.get(t, t), "rank": TIER_RANK.get(t, 6),
               "needs_review": bool(r.get("needsReview"))}
    except Exception:
        out = None
    with _LOCK:
        global _MEMO_DIRTY
        _MEMO.setdefault(disc, {})[journal] = out
        _MEMO_DIRTY = True
    return out


def weight_dist(papers):
    """返回当前学科的 (by_tier, by_journal) 分布；仅命中缓存时返回，否则 None（并异步预热）。
       papers: {key: paper_dict}。缓存键 = (学科, papers.jsonl mtime)。"""
    disc = _disc()
    mt = _papers_mtime()
    with _LOCK:
        _load_dist()
        d = _DIST.get(disc)
        if d and abs(float(d.get("mtime", -1)) - mt) < 1e-6:
            return d.get("by_tier", []), d.get("by_journal", [])
    warm_async(papers)          # 未命中 → 后台预热，本次先返回 None（调用方兜旧分布）
    return None


def _papers_mtime():
    try:
        return C.PAPERS_JSONL.stat().st_mtime if C.PAPERS_JSONL.exists() else 0.0
    except Exception:
        return 0.0


def _compute_dist(papers, disc):
    from collections import Counter
    tier_n = Counter(); jn = Counter(); jtier = {}
    for p in papers.values():
        j = p.get("journal", "")
        if not j:
            continue
        g = grade(j, p.get("issn", ""), compute=True)   # 预热期允许冷算
        cn = g["cn"] if g else (p.get("journal_tier") or "未知")
        rnk = g["rank"] if g else 6
        tier_n[(cn, rnk)] += 1
        jn[j] += 1; jtier[j] = (cn, rnk)
    by_tier = sorted(({"tier": cn, "rank": r, "n": n} for (cn, r), n in tier_n.items()),
                     key=lambda x: x["rank"])
    by_journal = [{"journal": j, "tier": jtier[j][0], "rank": jtier[j][1], "n": n}
                  for j, n in jn.most_common(15)]
    return by_tier, by_journal


def warm(papers):
    """同步预热：算全库去重刊名 grade（填 memo）+ 算分布并落盘。设计跑在后台线程里。"""
    if JG is None:
        return None
    disc = _disc(); mt = _papers_mtime()
    by_tier, by_journal = _compute_dist(papers, disc)
    with _LOCK:
        _load_dist()
        _DIST[disc] = {"mtime": mt, "by_tier": by_tier, "by_journal": by_journal}
        _atomic_write(DIST_FILE, _DIST)
    flush()
    return by_tier, by_journal


def warm_async(papers):
    """若该 (学科, mtime) 尚未预热且无进行中的预热，则起后台线程 warm。"""
    if JG is None:
        return
    disc = _disc(); mt = _papers_mtime(); tag = (disc, round(mt, 3))
    with _LOCK:
        _load_dist()
        d = _DIST.get(disc)
        if d and abs(float(d.get("mtime", -1)) - mt) < 1e-6:
            return                      # 已缓存
        if tag in _WARMING:
            return                      # 已在预热
        _WARMING.add(tag)

    def _run():
        t0 = time.time()
        try:
            warm(papers)
            print(f"[grading_svc] 预热完成 学科={disc} 用时 {time.time()-t0:.1f}s", flush=True)
        except Exception as e:
            print("[grading_svc] 预热失败：", e, flush=True)
        finally:
            with _LOCK:
                _WARMING.discard(tag)
    threading.Thread(target=_run, daemon=True).start()

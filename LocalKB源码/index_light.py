# -*- coding: utf-8 -*-
"""
Stage L — 即时词法索引（秒级，0 嵌入、0 AI、0 token）。
数据源：直接读 zotero.sqlite（绕过 Better BibTeX）。
输出：data/meta/papers.jsonl + bm25_meta + stats_cache + manifest。
产品"上来就能用"的核心：连上库几秒后就能按 标题/摘要/关键词/作者/刊名 搜。
用法: python index_light.py
"""
import sys, os, json, time, re
from pathlib import Path
from collections import Counter
sys.path.insert(0, str(Path(__file__).parent))
import config as C
from textutil import tokenize, clean, safe_name, de_emoji, EMOJI
import journal_tiers as JT
import source_rules as SR

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

def _is_cjk(t):
    return bool(t) and len(re.findall(r'[一-鿿]', t)) >= max(1, len(t) * 0.2)

def _replace_retry(tmp, path):
    """BF3：Windows 上若 server 恰在读目标文件，os.replace 会瞬时 PermissionError——
       短重试跨过句柄占用窗口，别让整轮建库因此 rc≠0。"""
    for i in range(3):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == 2:
                raise
            time.sleep(0.2)

def _atomic_write_text(path, text):
    """BF3：先写同目录 .tmp 再 os.replace 原子替换——server 是边跑边读这些产物的，
       直接覆盖写会让读方拿到半截 JSON（构建期间刷新首页就炸）。"""
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    _replace_retry(tmp, path)

def _atomic_write_lines(path, lines):
    """BF3：jsonl 版原子写（逐行拼好一次落盘）。"""
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")
    _replace_retry(tmp, path)

def get_papers():
    """数据源分派：zotero（读 zotero.sqlite）| folder（受管文件夹，读 meta_cache）。返回 (papers, source_name)。"""
    import settings as S
    if S.source() == "folder":
        import folder_source as F
        d = S.folder_dir()
        if not d or not Path(d).exists():
            raise RuntimeError("文件夹模式未配置受管库文件夹（请在向导/设置里指定）")
        return F.load_papers(d), f"folder:{d}"
    import zotero_source as Z
    if not Z.available():
        raise RuntimeError("未探测到 zotero.sqlite（请确认已安装 Zotero 且库中有文献，"
                           "或在向导里手动指定 Zotero 数据目录）")
    return Z.load_papers(), "zotero.sqlite"

def build_dict(papers):
    """从题录 keywords 生成 jieba 法律词典（数据源无关）。"""
    terms = set()
    for p in papers:
        for t in re.split(r'[,;，；]', p.get("keywords", "") or ""):
            t = clean(t)
            if 2 <= len(t) <= 12 and re.search(r'[一-鿿]', t) and not EMOJI.search(t):
                terms.add(t)
    C.LEGAL_DICT.write_text("\n".join(f"{t} 100 n" for t in sorted(terms)), encoding="utf-8")
    return len(terms)

def enrich(m, now, old_ingested=None):
    """给一条题录记录补 text/stem/tier/lang/ingested_at（数据源无关）。"""
    m.setdefault("official_pages", "")
    m.setdefault("has_pdf", False)
    m.setdefault("collections", [])
    m["text"] = "\n".join(x for x in [m.get("title", ""), m.get("abstract", ""),
                                      de_emoji(m.get("keywords", ""))] if x)
    m["stem"] = safe_name(m["key"])
    # 法源/报告按条目类型+标题规则定档（手动改档在检索期动态层生效，不写建库层）
    _rt = SR.rule_tier(m.get("itemtype", ""), m.get("title", ""))
    m["journal_tier"] = SR.OLD_TIER_LABEL.get(_rt) or JT.tier_of(m.get("journal", ""))
    m["tier_rank"] = JT.rank_of(m["journal_tier"])
    m["lang"] = ("中文" if (str(m.get("langid", "")).lower() in ("zh", "chinese", "中文") or _is_cjk(m.get("title", "")))
                 else ("外文" if m.get("title") else "未知"))
    # BF1：入库时间三级来源——meta 自带（zotero dateAdded）＞ 旧 papers.jsonl 同 key 继承 ＞ now。
    # 此前每次重建全体打 now，「最近入库」永远是重建时间，毫无信息量。
    m["ingested_at"] = m.get("ingested_at") or (old_ingested or {}).get(m["key"]) or now
    return m

def compute_stats(papers):
    now_iso = time.strftime("%Y-%m-%d %H:%M:%S")
    with_pdf = sum(1 for p in papers if p["has_pdf"])
    ek = C.STATE / "embedded_keys.txt"
    # BF26：历史并发双跑曾把同 stem 追加两遍，len(split()) 会虚报深索篇数——去重后再数
    deep = len(set(ek.read_text(encoding="utf-8").split())) if ek.exists() else 0
    by_year = Counter(p["year"] or "未标注" for p in papers)
    by_tier = Counter(p["journal_tier"] for p in papers)
    by_lang = Counter(p["lang"] for p in papers)
    by_type = Counter(p["itemtype"] or "未知" for p in papers)
    jc = Counter(p["journal"] for p in papers if p["journal"])
    jt = {p["journal"]: p["journal_tier"] for p in papers if p["journal"]}
    col = Counter(c for p in papers for c in p.get("collections", []))
    no_abstract = sum(1 for p in papers if not p["abstract"])
    return {
        "coverage": {"total": len(papers), "with_pdf": with_pdf, "no_pdf": len(papers) - with_pdf,
                     "meta_indexed": len(papers), "deep_indexed": deep, "chunks": 0, "no_abstract": no_abstract},
        "by_year": sorted([{"year": y, "n": n} for y, n in by_year.items()],
                          key=lambda x: (x["year"] == "未标注", x["year"])),
        "by_tier": sorted([{"tier": t, "rank": JT.rank_of(t), "n": n} for t, n in by_tier.items()],
                          key=lambda x: x["rank"]),
        "by_journal": [{"journal": j, "tier": jt.get(j, "未知"), "n": n} for j, n in jc.most_common(15)],
        "by_lang": [{"lang": l, "n": n} for l, n in by_lang.most_common()],
        "by_type": [{"itemtype": t, "n": n} for t, n in by_type.most_common()],
        "by_collection": [{"name": c, "n": n} for c, n in col.most_common(20)],
        # BF1：最近入库 = 按真实 ingested_at 降序取前 8（sorted 稳定：同时刻保持原有相对序），
        # 不再按文件尾 8 条猜——那只在"追加式"数据源里碰巧对
        "recent": [{"key": p["key"], "title": p["title"], "ingested_at": p["ingested_at"]}
                   for p in sorted(papers, key=lambda x: x.get("ingested_at", ""), reverse=True)[:8]],
        "health": {"meta_coverage": round(1 - no_abstract / max(1, len(papers)), 2),
                   "one_liner": f"题录索引就绪·{len(papers)}篇（{with_pdf}篇有PDF可深索）"},
        "updated_at": now_iso,
    }

def main():
    t0 = time.time()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    papers, source = get_papers()
    # 仅导入有 PDF 的条目（Zotero 模式向导可选；文件夹模式本就全是 PDF）
    try:
        import settings as _S
        if _S.source() == "zotero" and _S.load().get("import_only_pdf"):
            before = len(papers)
            papers = [p for p in papers if p.get("has_pdf")]
            print(f"[light] 仅导入有 PDF：{before} → {len(papers)} 篇", flush=True)
    except Exception:
        pass
    print(f"[light] 数据源={source}，{len(papers)} 篇", flush=True)
    print(f"[light] jieba 法律词典 {build_dict(papers)} 词", flush=True)
    # BF1：重建前先把旧 papers.jsonl 的 {key: ingested_at} 读出来——meta 不带入库时间的
    # 数据源（folder）靠继承旧值保住"首次入库时间"，否则每次重建全体变 now
    old_ingested = {}
    if C.PAPERS_JSONL.exists():
        try:
            with open(C.PAPERS_JSONL, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        _o = json.loads(line)
                        if _o.get("ingested_at"):
                            old_ingested[_o["key"]] = _o["ingested_at"]
        except Exception:
            old_ingested = {}
    papers = [enrich(m, now, old_ingested) for m in papers]

    C.META_DIR.mkdir(parents=True, exist_ok=True)
    # BF3：原子写，见 _atomic_write_lines
    _atomic_write_lines(C.PAPERS_JSONL, (json.dumps(p, ensure_ascii=False) for p in papers))
    print(f"[light] papers.jsonl -> {len(papers)} 篇", flush=True)

    import bm25s
    keys = [p["key"] for p in papers]
    corpus = [tokenize(p["text"]) for p in papers]
    r = bm25s.BM25()
    r.index(corpus)
    r.save(str(C.BM25_META_DIR))
    (C.BM25_META_DIR / "bm25_meta_ids.json").write_text(json.dumps(keys, ensure_ascii=False), encoding="utf-8")
    print(f"[light] bm25_meta -> {len(keys)} 篇", flush=True)

    stats = compute_stats(papers)
    # BF3：stats/manifest 同样原子写——server 轮询读它们，半截 JSON 会让仪表盘/状态页报解析错
    _atomic_write_text(C.STATS_CACHE, json.dumps(stats, ensure_ascii=False, indent=2))
    import settings as _S
    _atomic_write_text(C.INDEX_MANIFEST, json.dumps(
        {"source": source, "light_done": True, "light_at": now,
         "papers": len(papers), "with_pdf": stats["coverage"]["with_pdf"],
         "backend": _S.backend()},   # 记录建库时的检索引擎，供一致性校验
        ensure_ascii=False, indent=2))
    print(f"[light] 完成 {len(papers)} 篇（源：{source}），用时 {time.time()-t0:.1f}s", flush=True)
    return stats

if __name__ == "__main__":
    s = main()
    print(json.dumps(s["coverage"], ensure_ascii=False))

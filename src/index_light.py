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
from textutil import tokenize, clean, safe_name, de_emoji, EMOJI, load_core_terms, load_legal_synonyms
import journal_tiers as JT
import source_rules as SR
import document_formats as DF

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

# EN-L4①：词典来源条目的 itemtype 白名单——只有学术/法源类条目的 keywords 才可信；
# webpage/blogPost 等网摘的标签常是「EXCEL技巧」「《三体》」这类与法学无关的噪声，
# 混进 jieba 词典会扭曲建库/查询两侧的分词。
DICT_ITEMTYPES = {"journalArticle", "statute", "report", "case", "thesis", "book", "bookSection"}

def build_dict(papers):
    """从题录 keywords 生成 jieba 法律词典（数据源无关）。
       EN-L4 治理：①来源条目按 itemtype 白名单过滤；②丢弃含书名号/教程式纯英文词条；
       ③长度上限 12→8——超长短语进词典后，查询里只出现其子串时分词错配反而搜不到
       （案例：「以审判为中心的诉讼制度」整词入典，查「以审判为中心」被切碎、无法命中）；
       ④叠加出厂核心术语与同义词组成员（后者必须整词切分，查询侧同义扩展才能按 token 命中）。"""
    terms = set()
    for p in papers:
        if (p.get("itemtype") or "") not in DICT_ITEMTYPES:      # EN-L4①
            continue
        for t in re.split(r'[,;，；]', p.get("keywords", "") or ""):
            t = clean(t)
            if not t or "《" in t or "》" in t:                   # EN-L4②：含书名号=作品名，不是术语
                continue
            if " " in t and not re.search(r'[一-鿿]', t):        # EN-L4②：纯英文含空格=教程式短语
                continue
            if 2 <= len(t) <= 8 and re.search(r'[一-鿿]', t) and not EMOJI.search(t):   # EN-L4③
                terms.add(t)
    # EN-L4④：叠加核心术语 + 同义词组成员（只收含中文的；上限放宽到 12——出厂表是人工
    # 核过的真术语，如「帮助信息网络犯罪活动罪」11 字，不受 keywords 噪声上限约束）
    try:
        terms.update(t for t in load_core_terms()
                     if 2 <= len(t) <= 12 and re.search(r'[一-鿿]', t))
        for g in load_legal_synonyms():
            terms.update(w for w in g if 2 <= len(w) <= 12 and re.search(r'[一-鿿]', w))
    except Exception:
        pass                                                     # 词表异常不阻塞建库
    C.LEGAL_DICT.write_text("\n".join(f"{t} 100 n" for t in sorted(terms)), encoding="utf-8")
    return len(terms)

def enrich(m, now, old_ingested=None):
    """给一条题录记录补 text/stem/tier/lang/ingested_at（数据源无关）。"""
    DF.normalize_record(m)
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
    # EN-L5：法条时效标识，写进 papers.jsonl（检索输出侧按它现算徽标/降权，不改 LanceDB 表 schema）。
    # 判据来自 title/extra——Zotero 里法规版本状态通常直接写在标题（如"（2012修正）〔已被修订〕"）
    # 或 extra 备注里。已废止/已失效 → "已废止"（检索降权）；YYYY年修正/修订 或 已修订 → "已修订"（只标识）。
    if (m.get("itemtype") or "") == "statute":
        blob = f"{m.get('title', '') or ''} {m.get('extra', '') or ''}"
        if re.search(r"已废止|已失效", blob):
            m["statute_status"] = "已废止"
        elif re.search(r"\d{4}\s*年?\s*(修正|修订)|已修订|已被修订", blob):
            m["statute_status"] = "已修订"
        else:
            m["statute_status"] = ""
    return m

def compute_stats(papers):
    now_iso = time.strftime("%Y-%m-%d %H:%M:%S")
    with_pdf = sum(1 for p in papers if p["has_pdf"])
    with_fulltext = sum(1 for p in papers if p["has_fulltext"])
    by_format = Counter(p.get("fulltext_format") or "" for p in papers if p.get("has_fulltext"))
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
        "coverage": {"total": len(papers), "with_fulltext": with_fulltext,
                     "no_fulltext": len(papers) - with_fulltext,
                     "with_pdf": with_pdf, "no_pdf": len(papers) - with_pdf,
                     "by_fulltext_format": {fmt: by_format.get(fmt, 0) for fmt in DF.FORMAT_PRIORITY},
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
                   "one_liner": f"题录索引就绪·{len(papers)}篇（{with_fulltext}篇有全文可深索）"},
        "updated_at": now_iso,
    }

# ═══ 去重（Zotero 侧同一篇多次入库=不同 itemKey）══════════════════════════
_DEDUP_LAW_TYPES = {"statute", "case", "standard", "report"}


def _protect_stems():
    """带『深索产物』的 stem 并集——去重绝不能删这些 key。
    铁律：papers.jsonl 掉一个 key → 下一步 index_semantic._purge_deleted 会把它的
    LanceDB chunk 行 + 磁盘 chunks/extracted/summaries 一并清掉。故凡带深索产物者一律保留。
    ⚠ **不含 meta_embedded**：语义层给全库每篇都嵌了题录向量，若护它=护全库、去重就白做；
      而题录向量很便宜、survivor 副本本就覆盖，删掉无损。真正贵的是 chunk（深索成果）。
    额外扫磁盘 chunks（>10B=有真实块）——防 embedded_keys.txt 漏标却已有块的历史不一致(BF26/27/34)。"""
    prot = set()
    for fn in ("embedded_keys.txt", "deep_no_text.txt"):
        f = C.STATE / fn
        if f.exists():
            try:
                prot |= set(f.read_text(encoding="utf-8").split())
            except Exception:
                pass
    try:
        for cf in C.CHUNKS.glob("*.json"):
            try:
                if cf.stat().st_size > 10:
                    prot.add(cf.stem)
            except Exception:
                pass
    except Exception:
        pass
    return prot


# 泛标题黑名单：这些不同刊不同期都叫同名、内容各异，绝不能按标题合并。
_GENERIC_TITLES = {"目录", "编者按", "卷首语", "本期导读", "前言", "摘要", "引言", "后记",
                   "编后记", "编者的话", "主编寄语", "编辑手记", "编者手记", "导读", "序", "序言"}


def _dedup_sig(p):
    """同一篇的判据。DOI 最可靠；法源/泛标题各留一份不并；否则 归一标题+首作者+年。"""
    doi = re.sub(r'^https?://(dx\.)?doi\.org/', '', (p.get("doi") or "").strip().lower())
    if doi:
        return ("doi", doi)
    if (p.get("itemtype") or "") in _DEDUP_LAW_TYPES:   # 法源同名不同版本(现行/已废止)必须各留→不并
        return ("key", p["key"])
    raw = (p.get("title") or "").strip()
    t = re.sub(r'[\s\W_]+', '', raw).lower()
    # ⚠ 阈值按字符数：中文标题字少义足（「司法信任研究」6 字就是真标题），故用 < 4 而非英文式的 < 8；
    #   泛标题(目录/编者按/卷首语等)再用黑名单兜住——它们同名不同内容，不能并。
    if len(t) < 4 or raw in _GENERIC_TITLES:
        return ("key", p["key"])
    au = re.sub(r'[\s,]+', '', (p.get("author") or "").split(";")[0]).lower()
    return ("tay", t, au, (p.get("year") or "").strip())


def _dedup_papers(papers, protect):
    """删纯题录重复、保留一切带深索产物的副本。返回 (去重后 papers, 删除数)。"""
    groups = {}
    for i, p in enumerate(papers):
        groups.setdefault(_dedup_sig(p), []).append(i)
    drop = set()
    for idxs in groups.values():
        if len(idxs) == 1:
            continue
        earliest = min((papers[i].get("ingested_at") or "9999") for i in idxs)
        prot_idx = [i for i in idxs if safe_name(papers[i]["key"]) in protect]
        if prot_idx:
            keep = set(prot_idx)   # 有深索产物的成员全保留（下游 purge 才不会删其 chunks/向量）
        else:
            keep = {sorted(idxs, key=lambda i: (not papers[i].get("has_fulltext"),
                                                papers[i].get("ingested_at") or "9999"))[0]}
        for i in keep:             # survivor 继承最早入库时间，别让去重把"首次入库"抹成 now
            if (papers[i].get("ingested_at") or "9999") > earliest:
                papers[i]["ingested_at"] = earliest
        drop |= (set(idxs) - keep)
    if not drop:
        return papers, 0
    return [p for i, p in enumerate(papers) if i not in drop], len(drop)


def main():
    t0 = time.time()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    papers, source = get_papers()
    # 单一开关：仅导入有可读取全文附件的条目。设置键 import_only_pdf 为兼容旧用户保留，
    # 产品语义已扩展为 PDF/EPUB/DOCX/Markdown/TXT，HTML 网页快照不算。
    try:
        import settings as _S
        if _S.source() == "zotero" and _S.load().get("import_only_pdf"):
            before = len(papers)
            papers = [p for p in papers if p.get("has_fulltext")]
            print(f"[light] 仅导入有全文附件：{before} → {len(papers)} 篇", flush=True)
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

    # 去重：Zotero 里同一篇被重复添加（各自不同 itemKey），管线本会当成互不相干的多篇。
    # 仅 zotero 模式（folder 靠路径自洽）。放在 enrich 之后：要用 ingested_at 选 survivor / 继承最早时间。
    # ⚠ 安全命根子：被删 key 的 LanceDB 行/产物会在下一步 index_semantic._purge_deleted 被清，
    #   所以 _protect_stems() 把一切带深索产物的 key 排除在可删集外——**已深索的重复一律保留、绝不删**，
    #   只删「纯题录」副本（无 chunk、无向量），不丢深索成果、不白烧 API 钱。老用户升级后首次 light 生效。
    try:
        import settings as _S2
        if _S2.source() == "zotero":
            papers, _nd = _dedup_papers(papers, _protect_stems())
            if _nd:
                print(f"[light] 去重：删 {_nd} 条纯题录重复（已深索的副本一律保留）", flush=True)
    except Exception as e:
        print(f"[light] 去重跳过（{type(e).__name__}: {e}）", flush=True)

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
    _man = {"source": source, "light_done": True, "light_at": now,
            "papers": len(papers), "with_fulltext": stats["coverage"]["with_fulltext"],
            "with_pdf": stats["coverage"]["with_pdf"],
            "by_fulltext_format": stats["coverage"]["by_fulltext_format"]}
    # backend 只由真正产出向量的阶段(index_semantic/embed_index)写入并作为一致性校验基准；
    # light 绝不覆写它——否则切换后端后一次轻量重建/自动增量就把「原引擎」证据抹掉，
    # 新旧两套向量混用无从检测。保留 manifest 里已有的 backend。
    try:
        _old = {}
        if C.INDEX_MANIFEST.exists():
            _old = json.loads(C.INDEX_MANIFEST.read_text(encoding="utf-8"))
            if _old.get("backend"):
                _man["backend"] = _old["backend"]
        # 轻量更新只证明 light 规则已重跑；deep/semantic 的旧指纹必须保留，不能假装也更新过。
        import upgrade_health as UH
        current_fp = UH.pipeline_fingerprints()
        old_fp = _old.get("pipeline_fingerprints") if isinstance(_old, dict) else None
        _man["pipeline_fingerprints"] = dict(old_fp) if isinstance(old_fp, dict) else current_fp
        _man["pipeline_fingerprints"]["light"] = current_fp["light"]
    except Exception:
        pass
    _atomic_write_text(C.INDEX_MANIFEST, json.dumps(_man, ensure_ascii=False, indent=2))
    print(f"[light] 完成 {len(papers)} 篇（源：{source}），用时 {time.time()-t0:.1f}s", flush=True)
    return stats

if __name__ == "__main__":
    s = main()
    print(json.dumps(s["coverage"], ensure_ascii=False))

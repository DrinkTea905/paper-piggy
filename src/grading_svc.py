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
try:
    import source_rules as SR      # 法源/报告规则 + 手动改档（优先级高于期刊分级）
except Exception as _e:
    SR = None
    print("[grading_svc] source_rules 未加载，法源/报告定档停用：", _e, flush=True)

# 旧内部档仍保留细分权重；面向法学增强的评价统一折叠为四档。
TIER_CN = {"T1": "权威", "T1b": "顶级", "T2": "核心", "T3": "核心",
           "T4": "普通", "T5": "普通", "待确认": "普通"}
TIER_RANK = {"T1": 0, "T1b": 1, "T2": 2, "T3": 3, "T4": 4, "T5": 5, "待确认": 6}
BAND_RANK = {"authority": 0, "top": 1, "core": 2, "normal": 3}
BAND_CN = {"authority": "权威", "top": "顶级", "core": "核心", "normal": "普通"}
FUN_BAND_CN = {"authority": "夯", "top": "顶级", "core": "人上人", "normal": "NPC"}
DISCIPLINE_ALIASES = {"law_personal_fun": "law_personal"}

_LOCK = threading.RLock()
_MEMO = {}            # {disc: {journal: {tier,weight,cn,rank,needs_review}}}
_MEMO_LOADED = False
_MEMO_DIRTY = False
_DIST = {}            # {disc: {"mtime": float, "by_tier": [...], "by_journal": [...]}}
_DIST_LOADED = False
_WARMING = set()      # 正在预热的 (disc, mtime)，防重复起线程

MEMO_FILE = C.DATA / "grading_memo.json"
DIST_FILE = C.DATA / "grading_dist.json"
MAPPING_FILE = C.DATA / "grading_mappings.json"
DIST_VER = 3   # v3=全类型统一评价 + 四档分布。旧缓存自动失效重算。

# 库总览可调整的目录/性质映射。默认值只是说明；实际自动评价仍由目录引擎决定，
# 只有用户显式写入 MAPPING_FILE 的项才覆盖当前学科，客观标签永远不变。
MAPPING_SPECS = [
    ("label:三大刊", "三大刊", "authority"), ("label:顶尖法评", "顶尖法评", "authority"),
    ("label:CLSCI", "CLSCI", "top"), ("label:TSSCI", "TSSCI", "top"),
    ("label:精选外文权威", "精选外文权威", "top"),
    ("label:SSCI Q1", "SSCI Q1", "core"), ("label:SSCI Q2", "SSCI Q2", "core"),
    ("label:SSCI Q3", "SSCI Q3", "normal"), ("label:SSCI Q4", "SSCI Q4", "normal"),
    ("label:CSSCI", "CSSCI", "core"), ("label:CSSCI扩展", "CSSCI扩展", "core"),
    ("label:北大核心", "北大核心", "core"), ("label:台湾法学", "台湾法学", "core"),
    ("label:SSCI", "SSCI（无分区）", "normal"), ("label:期刊论文", "其他期刊论文", "normal"),
    ("label:SJR Q1", "SJR Q1", "normal"), ("label:SJR Q2", "SJR Q2", "normal"),
    ("label:SJR Q3", "SJR Q3", "normal"), ("label:SJR Q4", "SJR Q4", "normal"),
    ("nature:book", "书籍", "authority"), ("nature:book_section", "书章", "authority"),
    ("nature:thesis", "学位论文", "top"), ("nature:legal_source", "法源", "top"),
    ("nature:case", "案例", "top"), ("nature:standard", "标准", "top"),
    ("nature:report", "报告与白皮书", "core"),
    ("nature:dataset_authority", "权威机构数据集", "core"),
    ("nature:dataset", "其他数据集", "normal"),
    ("nature:preprint", "预印本", "normal"),
    ("nature:conference_paper", "会议论文", "normal"), ("nature:web", "网页与其他", "normal"),
]


def canonical_discipline(discipline):
    """娱乐学科只作显示别名；规则、目录、权重和缓存全部复用 law_personal。"""
    return DISCIPLINE_ALIASES.get(discipline or "", discipline or "law")


def band_name(band, discipline=None):
    names = FUN_BAND_CN if discipline == "law_personal_fun" else BAND_CN
    return names.get(band, BAND_CN["normal"])


def _band_of_tier(tier):
    return SR.band_of_tier(tier) if SR is not None else {
        "T1": "authority", "T1b": "top", "T2": "core", "T3": "core",
        "T4": "normal", "T5": "normal", "待确认": "normal",
    }.get(tier, "normal")


def _disc():
    try:
        return canonical_discipline(S.discipline())
    except Exception:
        return "law"


def _requested_disc():
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


def _memo_key(journal, issn):
    return f"{journal}\u001f{issn or ''}"


def _localized(result, requested_disc):
    if result is None:
        return None
    out = dict(result)
    out["band_name"] = band_name(out.get("band", "normal"), requested_disc)
    out["cn"] = out["band_name"]       # 旧调用兼容：cn 现在就是当前显示四档
    return out


def _hit_map(hit_catalogs):
    return {str(x.get("catalog") or ""): str(x.get("level") or "")
            for x in (hit_catalogs or []) if isinstance(x, dict) and x.get("catalog")}


def _objective_label(hit_catalogs):
    """期刊唯一客观标签。先比较映射后的四档，再按同档目录优先级决胜。"""
    hits = _hit_map(hit_catalogs)
    has_tssci = "tssci_law" in hits
    candidates = []

    def add(catalog, band, priority, label):
        if catalog in hits and label:
            candidates.append((BAND_RANK[band], priority, label))

    if hits.get("clsci") == "权威":
        add("clsci", "authority", 0, "三大刊")
    elif "clsci" in hits:
        add("clsci", "top", 2, "CLSCI")
    add("law_review_top", "authority", 1, "顶尖法评")
    add("tssci_law", "top", 0, "TSSCI")
    add("ssci_law_authority", "top", 1, "精选外文权威")
    if "ssci" in hits:
        q = hits["ssci"]
        candidates.append((BAND_RANK["core" if q in ("Q1", "Q2") else "normal"], 3,
                           "SSCI " + q if q else "SSCI"))
    if "cssci" in hits:
        lvl = hits["cssci"]
        candidates.append((BAND_RANK["core"], 4 if lvl == "来源" else 5,
                           "CSSCI" if lvl == "来源" else "CSSCI扩展"))
    add("pku", "core", 6, "北大核心")
    if "sjr" in hits:
        q = hits["sjr"]
        candidates.append((BAND_RANK["normal"], 7, "SJR " + q if q else "SJR"))
    # 正式 TSSCI 命中时，个人目录只参与评价、不再抢普通页面标签。
    if "tw_law" in hits and not has_tssci:
        candidates.append((BAND_RANK["authority" if hits["tw_law"] == "核心" else "core"],
                           8, "台湾法学"))
    if not candidates:
        return "期刊论文"
    return min(candidates)[2]


def _signal_weight(raw, disc):
    """同为核心时允许目录有可配置的细分权重（CSSCI扩展必须高于北大核心）。"""
    try:
        data = JG.load_data()
        arch = data.archetype(data.discipline(disc)["archetype"])
        overrides = arch.get("signalWeights", {}) or {}
        winner = ((raw.get("explain") or {}).get("priorityWinner") or {})
        token = winner.get("token") or (
            f"{winner.get('catalog')}.{winner.get('level')}" if winner.get("catalog") else "")
        return float(overrides[token]) if token in overrides else None
    except Exception:
        return None


def _journal_result(journal, issn, compute, requested_disc):
    if JG is None or not journal:
        return None
    disc = canonical_discipline(requested_disc)
    ck = _memo_key(journal, issn)
    with _LOCK:
        _load_memo()
        dm = _MEMO.get(disc) or {}
        cached = dm.get(ck)
        # 旧 memo 以刊名为键且不含客观标签；不删除，允许 compute=True 时惰性补全。
        if cached and cached.get("source_type") and cached.get("objective_label"):
            return _localized(cached, requested_disc)
    if not compute:
        return None
    try:
        raw = JG.resolve_journal_weight({"journal": journal or "", "issn": issn or ""}, disc)
        internal = raw.get("tier") or "待确认"
        band = _band_of_tier(internal)
        # 未识别的“待确认”只保留在 explain 内部；普通接口一律以 normal/T5 呈现。
        if internal == "待确认":
            internal = "T5"
        weight = raw.get("weight")
        sw = _signal_weight(raw, disc)
        if sw is not None and band == "core":
            weight = sw
        out = {
            "source_type": "journal_article", "source_type_name": "期刊论文",
            "objective_label": _objective_label(raw.get("hitCatalogs")),
            "band": band, "standard_band_name": BAND_CN[band],
            "band_name": BAND_CN[band], "internal_tier": internal,
            "weight": weight, "rank": TIER_RANK.get(internal, 6),
            "band_rank": BAND_RANK[band], "hit_catalogs": raw.get("hitCatalogs") or [],
            "explain": raw.get("explain") or {}, "manual": False, "src": "journal",
            # 旧调用兼容字段
            "tier": internal, "cn": BAND_CN[band], "needs_review": False,
        }
    except Exception:
        out = None
    if out is not None:
        with _LOCK:
            global _MEMO_DIRTY
            _MEMO.setdefault(disc, {})[ck] = dict(out)
            _MEMO_DIRTY = True
    return _localized(out, requested_disc)


def grade(journal, issn="", compute=True):
    """兼容刊级入口；新代码优先用 evaluate_paper。"""
    return _journal_result(journal, issn, compute, _requested_disc())


def _base_result(source_type, source_type_name, label, internal, weight, src, explain=None):
    band = _band_of_tier(internal)
    return {
        "source_type": source_type, "source_type_name": source_type_name,
        "objective_label": label, "band": band, "standard_band_name": BAND_CN[band],
        "band_name": BAND_CN[band], "internal_tier": internal, "weight": weight,
        "rank": TIER_RANK.get(internal, 6), "band_rank": BAND_RANK[band],
        "hit_catalogs": [], "explain": explain or {}, "manual": False, "src": src,
        "tier": internal, "cn": BAND_CN[band], "needs_review": False,
    }


def _non_journal_result(p, source_type):
    name = SR.source_type_name(source_type) if SR is not None else source_type
    if source_type == "book":
        return _base_result(source_type, name, "书籍", "T1", 1.0, "rule")
    if source_type == "book_section":
        return _base_result(source_type, name, "书章", "T1", 1.0, "rule")
    if source_type == "thesis":
        tt = str(p.get("thesis_type") or p.get("type") or "")
        label = "博士论文" if any(x in tt.lower() for x in ("博士", "phd", "doctor")) else \
                ("硕士论文" if any(x in tt.lower() for x in ("硕士", "master")) else "学位论文")
        return _base_result(source_type, name, label, "T1b", 0.92, "rule")
    if source_type == "legal_source":
        return _base_result(source_type, name, "法源", "T1b", 0.92, "rule")
    if source_type == "case":
        return _base_result(source_type, name, "案例", "T1b", 0.92, "rule")
    if source_type == "standard":
        return _base_result(source_type, name, "标准", "T1b", 0.92, "rule")
    if source_type == "report":
        official = bool(SR and SR.is_authoritative_org(p))
        return _base_result(source_type, name, "官方报告" if official else "研究报告", "T2", 0.85, "rule")
    if source_type == "dataset":
        official = bool(SR and SR.is_authoritative_org(p))
        return _base_result(source_type, name, "权威数据" if official else "数据集",
                            "T2" if official else "T5", 0.85 if official else 0.25, "rule")
    labels = {"preprint": "预印本", "conference_paper": "会议论文", "web": "网页",
              "newspaper": "报纸", "other": "文件"}
    return _base_result(source_type, name, labels.get(source_type, "文件"), "T5", 0.25, "rule")


def _load_mapping_overrides():
    try:
        raw = json.loads(MAPPING_FILE.read_text(encoding="utf-8")) if MAPPING_FILE.exists() else {}
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def set_mapping_override(mapping_id, band, discipline=None):
    """保存当前学科的目录/性质四档覆盖；band 为空表示恢复自动。"""
    valid = {x[0] for x in MAPPING_SPECS}
    if mapping_id not in valid:
        raise ValueError(f"未知映射：{mapping_id}")
    if band and band not in BAND_RANK:
        raise ValueError("档位只支持 authority/top/core/normal")
    disc = canonical_discipline(discipline or _requested_disc())
    raw = _load_mapping_overrides()
    dm = raw.setdefault(disc, {})
    if band:
        dm[mapping_id] = band
    else:
        dm.pop(mapping_id, None)
    if not dm:
        raw.pop(disc, None)
    _atomic_write(MAPPING_FILE, raw)
    # 分布包含映射结果，写后立即失效；逐刊 memo 保留客观识别事实不受影响。
    with _LOCK:
        global _DIST
        _load_dist()
        _DIST.pop(disc, None)
        _atomic_write(DIST_FILE, _DIST)
    return {"mapping_id": mapping_id, "band": band, "discipline": discipline or _requested_disc()}


def _apply_mapping_override(out, requested):
    disc = canonical_discipline(requested)
    overrides = _load_mapping_overrides().get(disc) or {}
    source_type = out.get("source_type") or "other"
    if source_type in {"web", "newspaper", "other"}:
        source_type = "web"
    if out.get("source_type") == "journal_article":
        mapping_id = "label:" + str(out.get("objective_label") or "")
    elif source_type == "dataset" and out.get("objective_label") == "权威数据":
        mapping_id = "nature:dataset_authority"
    else:
        mapping_id = "nature:" + source_type
    band = overrides.get(mapping_id)
    if not band:
        return out
    result = dict(out)
    internal = SR.BAND_TO_TIER[band] if SR is not None else {
        "authority": "T1", "top": "T1b", "core": "T2", "normal": "T5"}[band]
    weight = (SR.TIER_W if SR is not None else {
        "T1": 1.0, "T1b": .92, "T2": .85, "T5": .25})[internal]
    result.update({
        "band": band, "standard_band_name": BAND_CN[band], "band_name": band_name(band, requested),
        "internal_tier": internal, "weight": weight, "rank": TIER_RANK[internal],
        "band_rank": BAND_RANK[band], "src": "mapping", "tier": internal,
        "cn": band_name(band, requested),
    })
    ex = dict(result.get("explain") or {})
    ex["mappingOverride"] = {"mappingId": mapping_id, "band": band}
    result["explain"] = ex
    return result


def evaluate_paper(p, compute=True, discipline=None):
    """全类型统一评价事实源。手动覆盖最后应用，且只改评价层。"""
    p = p if isinstance(p, dict) else {}
    requested = discipline or _requested_disc()
    source_type = SR.classify_source_type(p) if SR is not None else "journal_article"
    if source_type == "journal_article":
        out = _journal_result(p.get("journal", ""), p.get("issn", ""), compute, requested)
        if out is None:
            out = _base_result(source_type, "期刊论文", "期刊论文", "T5", 0.25, "journal_fallback",
                               {"provisional": True, "notes": ["期刊目录尚未计算，暂按普通显示。"]})
    else:
        out = _non_journal_result(p, source_type)
    out = _localized(out, requested)
    out = _apply_mapping_override(out, requested)

    raw_override = None
    try:
        raw_override = SR.get_override(p.get("key", "")) if SR is not None else None
    except Exception:
        raw_override = None
    if raw_override:
        out = dict(out)
        internal = SR.internal_tier_of_override(raw_override)
        band = SR.band_of_tier(raw_override)
        out.update({
            "band": band, "standard_band_name": BAND_CN[band],
            "band_name": band_name(band, requested), "internal_tier": internal,
            "weight": SR.TIER_W[internal], "rank": TIER_RANK[internal],
            "band_rank": BAND_RANK[band], "manual": True, "src": "manual",
            "tier": internal, "cn": band_name(band, requested), "needs_review": False,
        })
        ex = dict(out.get("explain") or {})
        ex["manualOverride"] = {"stored": raw_override, "band": band, "internalTier": internal}
        out["explain"] = ex
    return out


def grade_paper(p, compute=True):
    """兼容篇级入口，现直接返回统一评价契约。"""
    return evaluate_paper(p, compute=compute)


def _ov_mtime():
    """手动改档/映射文件的 mtime（分布缓存失效键）。"""
    try:
        one = SR.overrides_mtime() if SR is not None else 0.0
        two = MAPPING_FILE.stat().st_mtime if MAPPING_FILE.exists() else 0.0
        return max(one, two)
    except Exception:
        return 0.0


def weight_dist(papers):
    """返回当前学科的 (by_tier, by_journal) 分布；仅命中缓存时返回，否则 None（并异步预热）。
       papers: {key: paper_dict}。缓存键 = (学科, papers.jsonl mtime, 改档文件 mtime)。"""
    disc = _disc()
    mt = _papers_mtime(); ov = _ov_mtime()
    with _LOCK:
        _load_dist()
        d = _DIST.get(disc)
        if d and d.get("v") == DIST_VER \
             and abs(float(d.get("mtime", -1)) - mt) < 1e-6 \
             and abs(float(d.get("ov_mtime", 0.0)) - ov) < 1e-6:
            return d.get("by_tier", []), d.get("by_journal", [])
    warm_async(papers)          # 未命中 → 后台预热，本次先返回 None（调用方兜旧分布）
    return None


def overview(papers, compute=False):
    """库总览使用的四档、客观标签、真实性质和可调整映射明细。"""
    from collections import Counter
    requested = _requested_disc()
    band_counts = Counter()
    label_counts = Counter()
    type_counts = Counter()
    type_names = {}
    for p in (papers or {}).values():
        g = evaluate_paper(p, compute=compute, discipline=requested)
        band_counts[g["band"]] += 1
        label_counts[g["objective_label"]] += 1
        type_code = g["source_type"]
        if type_code in {"web", "newspaper", "other"}:
            type_code = "web"
        type_counts[type_code] += 1
        type_names[type_code] = "网页与其他" if type_code == "web" else g["source_type_name"]
    total = sum(band_counts.values())
    weights = {"authority": 1.0, "top": 0.92, "core": 0.85, "normal": 0.25}
    bands = [{
        "band": b, "name": band_name(b, requested), "standard_name": BAND_CN[b],
        "count": band_counts[b], "ratio": (band_counts[b] / total if total else 0.0),
        "weight": weights[b],
    } for b in BAND_RANK]
    disc = canonical_discipline(requested)
    overrides = _load_mapping_overrides().get(disc) or {}
    mappings = [{
        "mapping_id": mid, "label": label, "default_band": default,
        "band": overrides.get(mid, default), "band_name": band_name(overrides.get(mid, default), requested),
        "customized": mid in overrides, "editable": True, "update_url": "/grading/mapping",
    } for mid, label, default in MAPPING_SPECS]
    return {
        "discipline": requested, "canonical_discipline": disc,
        "discipline_name": ("法学（开发者增强：夯到拉）" if requested == "law_personal_fun"
                            else ((JG.load_data().disciplines().get(disc) or {}).get("name", requested)
                                  if JG is not None else requested)),
        "band_names": {b: band_name(b, requested) for b in BAND_RANK},
        "notice": ("仅供娱乐：功能与“法学（开发者增强）”完全相同，仅改变四档显示名。"
                   if requested == "law_personal_fun" else ""),
        "total": total, "bands": bands, "mappings": mappings,
        "labels": [{"label": k, "count": v} for k, v in label_counts.most_common()],
        "source_types": [{"source_type": k, "source_type_name": type_names.get(k, k), "count": v}
                         for k, v in type_counts.most_common()],
        "single_item_override": True,
    }


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
        g = grade_paper(p, compute=True)
        cn = g["standard_band_name"]
        rnk = g["band_rank"]
        tier_n[(cn, rnk)] += 1
        if j:
            jn[j] += 1
            # 期刊卡档位必须用**刊级** grade(j)：篇级结果（手动改档按 key、标题规则按篇）
            # last-wins 会把单篇改档写成整刊档位（对抗审查 #3/#4，major）。
            if j not in jtier:
                gj = grade(j, p.get("issn", ""), compute=True)
                jtier[j] = (gj["standard_band_name"], gj["band_rank"]) if gj else ("普通", 3)
    by_tier = sorted(({"tier": cn, "rank": r, "n": n} for (cn, r), n in tier_n.items()),
                     key=lambda x: x["rank"])
    by_journal = [{"journal": j, "tier": jtier[j][0], "rank": jtier[j][1], "n": n}
                  for j, n in jn.most_common(15)]
    return by_tier, by_journal


def warm(papers):
    """同步预热：算全库去重刊名 grade（填 memo）+ 算分布并落盘。设计跑在后台线程里。"""
    if JG is None:
        return None
    disc = _disc(); mt = _papers_mtime(); ov = _ov_mtime()
    by_tier, by_journal = _compute_dist(papers, disc)
    with _LOCK:
        _load_dist()
        _DIST[disc] = {"v": DIST_VER, "mtime": mt, "ov_mtime": ov,
                       "by_tier": by_tier, "by_journal": by_journal}
        _atomic_write(DIST_FILE, _DIST)
    flush()
    return by_tier, by_journal


def warm_async(papers):
    """若该 (学科, mtime) 尚未预热且无进行中的预热，则起后台线程 warm。"""
    if JG is None:
        return
    disc = _disc(); mt = _papers_mtime(); ov = _ov_mtime()
    tag = (disc, round(mt, 3), round(ov, 3))
    with _LOCK:
        _load_dist()
        d = _DIST.get(disc)
        if d and d.get("v") == DIST_VER \
             and abs(float(d.get("mtime", -1)) - mt) < 1e-6 \
             and abs(float(d.get("ov_mtime", 0.0)) - ov) < 1e-6:
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

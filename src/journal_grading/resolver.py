# -*- coding: utf-8 -*-
"""
解析器（journal_grading 第③层）：resolve_journal_weight(item, active_discipline)。
算法（方案 §六）：
  期刊识别 → 取信号(所有命中目录.级别) → priority-max 定主档位 → 查档位分
  → IF 微调(本期关) → 乘该学科 中/外 系数 → clamp[0,1] → 返回 {weight,tier,hitCatalogs,explain}
设计要点：
  - priority-max：按 resolution.priorityOrder 从高到低，第一个「能在本原型 map 里查到档位」的
    命中目录定档；只用这一个，不多目录累加（避免重复计分）。
  - token 语义：'cat.lvl' 真实信号；'cat.lvl@recognizedTop' 需同在 recognizedTopLists[学科]；
    'cssci.来源@fmsA' 需同时命中 fms.A；裸目录名(ft50/utd24)= 该目录收录；
    '普通'=识别到但无可映射排名信号的通用兜底(T5)；'其他核心'=识别到本土核心目录但未被更高档接住的次级兜底。
  - 缺分区/缺 IF：对应 token 匹配不上而已，照常出主档位，绝不报错。
  - 未识别：tier='待确认'、needs_review=True、explain.provisional=True，不静默按普通档发。
"""
import bisect
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import loader as _loader
from identify import identify, in_recognized_top, _item_name

_TIER_ORDER = ["T1", "T1b", "T2", "T3", "T4", "T5"]


def _if_percentile(sorted_vals, x):
    """x 在全库知网复合影响因子(升序)里的分位[0,1]；无数据/无 x → None。"""
    if not sorted_vals or x is None:
        return None
    return bisect.bisect_right(sorted_vals, x) / len(sorted_vals)
# 中/外刊判定：定档目录的性质
_DOMESTIC_CORE = {"cssci", "clsci", "pku", "ami", "fms"}
_FOREIGN = {"ssci", "ahci", "sjr", "abs", "ft50", "utd24", "erih"}
_CJK_RE = re.compile(r"[一-鿿]")


def _clamp01(x):
    return 0.0 if x < 0 else (1.0 if x > 1 else x)


def _has_cjk(s):
    return bool(_CJK_RE.search(s or ""))


def _parse_token(tok):
    """'cssci.来源@recognizedTop' -> ('cssci','来源','recognizedTop')；
       'ft50' -> ('ft50', None, None)；'abs.4*' -> ('abs','4*',None)。"""
    qual = None
    if "@" in tok:
        tok, qual = tok.split("@", 1)
    if "." in tok:
        cat, lvl = tok.split(".", 1)
    else:
        cat, lvl = tok, None
    return cat, lvl, qual


def _catalog_best_tier(cat, lvl, arch_map, ctx):
    """给定命中目录 cat 及其级别 lvl，在本原型 map 里找它能落到的最高内部档（T1→T5 先到先得）。
       ctx 提供限定判定：is_recognized_top / fms_level。返回 (tier, matched_token) 或 (None,None)。"""
    for tier in _TIER_ORDER:
        for tok in arch_map.get(tier, []):
            tcat, tlvl, tqual = _parse_token(tok)
            if tcat != cat:
                continue
            # 级别匹配：token 未写级别(裸目录名，如 ft50)则任意命中即算；写了则需相等
            if tlvl is not None and tlvl != lvl:
                continue
            # 限定符
            if tqual == "recognizedTop" and not ctx["is_recognized_top"]:
                continue
            if tqual == "fmsA" and ctx["fms_level"] != "A":
                continue
            return tier, tok
    return None, None


def resolve_journal_weight(item, active_discipline, data=None, if_enabled=None):
    """核心 API。
      item: dict，至少含 journal(刊名)，可含 issn/eissn、language/langid、if/if_cnki。
      active_discipline: 学科 id（如 'law'）。
    返回: { weight: float, tier: 'T1'|'T1b'|'T2'|'T3'|'T4'|'T5'|'待确认', hitCatalogs: [...], explain: {...} }
    """
    data = data or _loader.load()
    disc = data.discipline(active_discipline)          # KeyError 若学科名错（不静默）
    arch_name = disc["archetype"]
    arch = data.archetype(arch_name)
    arch_map = arch.get("map", {})
    tiers = data.tiers()
    reso = data.resolution()
    if if_enabled is None:
        if_enabled = bool(reso.get("ifEnabled", False))

    coeff = dict(arch.get("coeff", {}))
    coeff.update(disc.get("coeff", {}))                 # 学科系数覆盖原型默认
    if_strength = disc.get("ifStrength", arch.get("ifStrength", 0.0))

    # —— 识别 ——
    ident = identify(item, data)
    # F38-B 白名单裁剪：只保留"本学科可见"的目录信号（堵私有目录 tw_law/law_review_top 等泄漏）。
    # 语义边界：台湾刊在标准 law 下 identify 仍精确命中(ident.hit()=True)，只是 vis_cats 过滤后为空
    # → 走「普通」fallback（T5），不能当 unresolved/待确认（刊是认识的，只是该学科不承认这套私有信号）。
    allowed = data.visible_catalogs(active_discipline)
    vis_cats = {c: l for c, l in ident.catalogs.items() if c in allowed} if ident.hit() else {}
    is_top = in_recognized_top(item, ident, data, active_discipline) if ident.hit() else False
    fms_level = vis_cats.get("fms")
    ctx = {"is_recognized_top": is_top, "fms_level": fms_level}

    hit_catalogs = [{"catalog": c, "level": l, "version": ident.versions.get(c, "")}
                    for c, l in vis_cats.items()]
    notes = []

    # —— priority-max 定主档位（只在可见信号里选）——
    main_tier, winner_cat, winner_lvl, winner_token = None, None, None, None
    if vis_cats:
        for cat in reso.get("priorityOrder", []):
            if cat not in vis_cats:
                continue
            lvl = vis_cats[cat]
            tier, tok = _catalog_best_tier(cat, lvl, arch_map, ctx)
            if tier is not None:
                main_tier, winner_cat, winner_lvl, winner_token = tier, cat, lvl, tok
                break

    provisional = False
    if main_tier is None:
        if not ident.hit():
            # 未识别：待确认，给临时占位分但显式标记，绝不静默按普通档
            main_tier = reso.get("unknownDefault", "T5")
            provisional = True
            notes.append("待确认：未在目录数据中识别到该刊；weight 为临时占位(T5)，需人工确认，勿据此判为普通档。")
        else:
            # 识别到（可能可见信号被白名单裁空）但无可映射排名信号 → 本土核心走「其他核心」，否则「普通」。
            # 关键：此处走 fallback（普通/其他核心），绝不落 unresolved——刊是认识的。
            has_domestic_core = any(c in _DOMESTIC_CORE for c in vis_cats)
            fallback_token = "其他核心" if has_domestic_core else "普通"
            main_tier = _tier_of_pseudo(arch_map, fallback_token) or _tier_of_pseudo(arch_map, "普通") \
                or reso.get("unknownDefault", "T5")
            winner_token = fallback_token
            notes.append(f"无可映射的排名/名录信号，落兜底 token「{fallback_token}」→ {main_tier}。")

    base = float(tiers.get(main_tier, tiers.get("T5", 0.25)))

    # —— IF 微调（知网复合影响因子：base×(1+ifStrength×全库分位)；无数据自动跳过，不报错）——
    if_used = False
    if_val = None
    if_pct = None
    if if_enabled and if_strength and not provisional:
        if_val = _item_if(item)
        if if_val is None and ident.hit():
            if_val = ident.if_val
        if_pct = _if_percentile(data.if_values, if_val)
        if if_pct is not None:
            base = base * (1 + if_strength * if_pct)
            if_used = True
            notes.append(f"IF微调:复合IF={if_val},全库分位={round(if_pct,3)},×{round(1 + if_strength * if_pct, 4)}")

    # —— 中/外系数 ——
    # 优先用目录声明的 origin（配置即代码，新增私有目录无需改代码）；否则内置集合；最后 CJK 启发式。
    cat_origin = (data.config.get("catalogs", {}).get(winner_cat, {}) or {}).get("origin")
    if cat_origin in ("cn", "intl"):
        origin = cat_origin
    elif winner_cat in _DOMESTIC_CORE:
        origin = "cn"
    elif winner_cat in _FOREIGN:
        origin = "intl"
    else:  # warning / 兜底 / 待确认：按刊名 CJK 启发式
        origin = "cn" if _has_cjk(ident.name or _item_name(item)) else "intl"
    coeff_val = float(coeff.get(origin, 1.0))

    weight = _clamp01(base * coeff_val)

    explain = {
        "discipline": active_discipline,
        "disciplineName": disc.get("name", ""),
        "archetype": arch_name,
        "identify": {"status": ident.status, "matchedName": ident.name,
                     "fuzzyScore": ident.score, "needsReview": ident.needs_review},
        "signalSet": [f"{c}.{l}" for c, l in vis_cats.items()],
        "priorityWinner": {"catalog": winner_cat, "level": winner_lvl, "token": winner_token, "tier": main_tier},
        "base": round(base, 4),
        "coeff": {"origin": origin, "value": coeff_val},
        "ifUsed": if_used,
        "ifStrength": if_strength,
        "ifValue": if_val,
        "ifPercentile": round(if_pct, 4) if if_pct is not None else None,
        "provisional": provisional,
        "notes": notes,
    }

    return {
        "weight": round(weight, 4),
        "tier": "待确认" if provisional else main_tier,
        "needsReview": bool(ident.needs_review),
        "hitCatalogs": hit_catalogs,
        "explain": explain,
    }


def _tier_of_pseudo(arch_map, token):
    for tier in _TIER_ORDER:
        if token in arch_map.get(tier, []):
            return tier
    return None


def _item_if(item):
    if not isinstance(item, dict):
        return None
    for k in ("if", "if_cnki", "impact_factor", "IF"):
        v = item.get(k)
        if v not in (None, "", 0, "0"):
            try:
                return float(v)
            except Exception:
                return None
    return None


# 便捷：暴露给外部
def reload():
    return _loader.reload()

# -*- coding: utf-8 -*-
"""
期刊识别：把一条文献 item 定位到目录数据里的某本期刊。
优先级（方案 §matching / §六.1）：
  1. ISSN / eISSN 命中           → status="issn"
  2. 归一化刊名精确命中          → status="name"
  3. 归一化刊名模糊 ≥0.9 命中    → status="fuzzy"（自动采纳，explain 记下相似度与匹配到谁）
  4. 都不中                      → status="unresolved"（标"待确认"，绝不静默按普通档）
模糊用标准库 difflib，不引新依赖。
返回 IdentifyResult：
  status, key(命中的索引键), name(命中刊名), catalogs{cat:level}, versions{cat:ver},
  score(模糊时的相似度), needs_review(bool)
"""
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from normalize import normalize_name, normalize_issn

FUZZY_THRESHOLD = 0.9


class IdentifyResult:
    def __init__(self, status, key="", name="", catalogs=None, versions=None, score=None, if_val=None):
        self.status = status                    # issn | name | fuzzy | unresolved
        self.key = key
        self.name = name
        self.catalogs = catalogs or {}          # {catalog_id: level}
        self.versions = versions or {}          # {catalog_id: version}
        self.score = score                      # 模糊相似度（仅 status==fuzzy）
        self.if_val = if_val                    # 知网复合影响因子（若该刊有数据）
        self.needs_review = (status in ("fuzzy", "unresolved"))

    def hit(self):
        return self.status in ("issn", "name", "fuzzy")


def _item_issns(item):
    out = []
    for k in ("issn", "eissn", "ISSN", "EISSN"):
        v = item.get(k) if isinstance(item, dict) else None
        if v:
            ni = normalize_issn(v)
            if ni and len(ni) == 9:
                out.append(ni)
    return out


def _item_name(item):
    if not isinstance(item, dict):
        return str(item or "")
    for k in ("journal", "name", "publicationTitle", "container-title", "刊名"):
        v = item.get(k)
        if v:
            return str(v)
    return ""


def identify(item, data) -> IdentifyResult:
    """item: dict（至少含 journal 刊名，可含 issn/eissn）。data: loader.GradingData。

    ISSN 命中后**仍要并入归一名精确命中的目录信号**（2026-08-10 修）。
    历史 bug：本函数原本 ISSN 命中即 return，而 clsci / law_review_top /
    ssci_law_authority / tw_law 四个私有目录的条目 ISSN 字段是空的，只登记在
    by_name 桶里（`loader._register` 要求 ISSN 长度=9 才登记 by_issn）。于是
    **题录里带了 ISSN 的文献整条错过这些目录**——同一本刊会按题录有没有 ISSN
    分裂成两档（实测：《中国社会科学》30 篇里 28 篇只命中 CSSCI 被判顶级、
    2 篇走刊名命中 CLSCI 权威）。
    合并只取**精确**归一名，不取模糊命中；同名目录以 ISSN 侧为准，刊名侧只补空缺。
    """
    raw_name = _item_name(item)
    nn = normalize_name(raw_name)
    name_bucket = data.by_name.get(nn) if nn else None

    # 1) ISSN 优先（但目录信号与**同一本刊**的刊名桶取并集）
    for ni in _item_issns(item):
        b = data.by_issn.get(ni)
        if b:
            cats, vers = dict(b["catalogs"]), dict(b.get("versions", {}))
            # 合并前先确认两侧确实是同一本刊，否则（题录 ISSN 与刊名对不上、录错、子刊挂了母刊
            # ISSN…）会把别的刊的目录信号、影响因子和目录版本号安到本刊头上。
            #
            # 判据用**相似度**而不是严格相等：同一本刊在不同目录里拼写不一致是常态——
            # ssci/sjr 收 "YALE LAW JOURNAL"，而私有目录 law_review_top 收 "The Yale Law Journal"。
            # 严格相等会把这类条目判成两本刊、拒绝合并，本轮要修的"同刊分裂两档"就原样保留
            # （实测：Yale 带 ISSN 走 ssci.Q1→T2、不带 ISSN 走 law_review_top→T1）。
            # 实测阈值可分：真同刊 0.903—0.958（The X vs X），不同刊 0.000—0.600
            # （青少年犯罪问题 vs 中国社会科学 0.0、法学研究 vs 法学评论 0.5、中国法学 vs 中国社会科学 0.6），
            # 与第 3 步模糊匹配共用 FUZZY_THRESHOLD=0.9 正好分开。
            same_journal = False
            if name_bucket is not None:
                if name_bucket is b:
                    same_journal = True
                else:
                    bn = normalize_name(b.get("name", ""))
                    same_journal = bool(bn) and (
                        bn == nn or SequenceMatcher(None, bn, nn).ratio() >= FUZZY_THRESHOLD)
            if same_journal and name_bucket is not b:
                for cat, level in name_bucket["catalogs"].items():
                    cats.setdefault(cat, level)
                for cat, ver in (name_bucket.get("versions") or {}).items():
                    vers.setdefault(cat, ver)
            if_val = b.get("if")
            if if_val is None and same_journal:
                if_val = name_bucket.get("if")
            return IdentifyResult("issn", ni, b.get("name", "") or (name_bucket or {}).get("name", ""),
                                  cats, vers, if_val=if_val)

    if not nn:
        # 无刊名无 ISSN：无法识别
        return IdentifyResult("unresolved", "", raw_name)

    # 2) 归一名精确
    if name_bucket is not None:
        b = name_bucket
        return IdentifyResult("name", nn, b.get("name", ""), b["catalogs"], b.get("versions", {}), if_val=b.get("if"))

    # 3) 模糊 ≥0.9（在归一名索引里找最相似者）
    best_key, best_score = None, 0.0
    for cand in data.by_name.keys():
        # 快速剪枝：长度差过大直接跳过（相似度不可能到 0.9）
        if abs(len(cand) - len(nn)) > max(2, int(len(nn) * 0.4)):
            continue
        s = SequenceMatcher(None, nn, cand).ratio()
        if s > best_score:
            best_score, best_key = s, cand
    if best_key is not None and best_score >= FUZZY_THRESHOLD:
        b = data.by_name[best_key]
        return IdentifyResult("fuzzy", best_key, b.get("name", ""), b["catalogs"], b.get("versions", {}),
                              score=round(best_score, 4), if_val=b.get("if"))

    # 4) 待确认
    r = IdentifyResult("unresolved", "", raw_name)
    r.score = round(best_score, 4) if best_key is not None else None
    return r


def in_recognized_top(item, ident, data, discipline_id) -> bool:
    """判该刊是否在 recognizedTopLists[discipline]（ISSN 或归一名任一命中）。"""
    for ni in _item_issns(item):
        if ni in data.recognized_issn.get(discipline_id, set()):
            return True
    if ident.key and ident.status != "unresolved":
        # 用命中记录的规范刊名再归一，与顶刊名单比对
        if normalize_name(ident.name) in data.recognized_name.get(discipline_id, set()):
            return True
        if ident.status == "name" and ident.key in data.recognized_name.get(discipline_id, set()):
            return True
    nn = normalize_name(_item_name(item))
    return bool(nn) and nn in data.recognized_name.get(discipline_id, set())

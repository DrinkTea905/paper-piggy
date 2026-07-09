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
    """item: dict（至少含 journal 刊名，可含 issn/eissn）。data: loader.GradingData。"""
    # 1) ISSN 优先
    for ni in _item_issns(item):
        b = data.by_issn.get(ni)
        if b:
            return IdentifyResult("issn", ni, b.get("name", ""), b["catalogs"], b.get("versions", {}), if_val=b.get("if"))

    raw_name = _item_name(item)
    nn = normalize_name(raw_name)
    if not nn:
        # 无刊名无 ISSN：无法识别
        return IdentifyResult("unresolved", "", raw_name)

    # 2) 归一名精确
    b = data.by_name.get(nn)
    if b:
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

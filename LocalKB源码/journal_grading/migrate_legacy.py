# -*- coding: utf-8 -*-
"""
一次性迁移：把旧档 journal_tiers.json（312 条个人法学分级，2026-06-28 手工整理）
无损并入新系统的目录数据文件。幂等（按归一刊名 upsert 去重，可重复跑）。
  旧档档次            → 新目录.级别
  外文顶级法评        → law_review_top.收录   (T1，外刊不打折)
  台湾核心            → tw_law.核心           (T1)
  台湾一般            → tw_law.一般           (T2)
  CLSCI              → clsci.权威(三大权威)/来源(其余)
  外文权威            → ssci_law_authority.收录 (T2，外刊不打折)
  CSSCI              → cssci.来源
  CSSCI扩展          → cssci.扩展
  报纸                → newspaper.报纸        (T6)
  普刊/外文一般/境外/未知 → 跳过（默认档，无需登记）
旧档只有刊名+档次、无 ISSN，故并入条目 issn 留空，靠归一刊名匹配（可后续补 ISSN）。
合并策略：保留各目录种子文件已有条目（及其 ISSN），把旧档条目 upsert 进去（旧档为准定 level）。
"""
import argparse
import json
import os
import sys
from pathlib import Path

PKG = Path(__file__).parent
sys.path.insert(0, str(PKG))
from normalize import normalize_name

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _data_dir():
    """LocalKB 的数据目录。优先问 config（它认 LOCALKB_DATA 环境变量、分发版会改写路径），
    config 导不进来时（比如把本脚本单独拷出去跑）退回同一个环境变量、再退回源码根 data/。"""
    try:
        sys.path.insert(0, str(PKG.parent))
        import config as C
        return Path(C.DATA)
    except Exception:
        return Path(os.environ.get("LOCALKB_DATA", str(PKG.parent / "data")))


# 旧档候选位置，按序取第一个存在的；也可以用 --legacy <路径> 直接指定。
# 这里以前写死过开发机的绝对路径，开源前清掉——换台机器就是死路径，
# 只会让人白等一句「未找到旧档」。现在第一顺位跟着 config.DATA 走。
LEGACY_CANDIDATES = [
    _data_dir() / "journal_tiers.json",                   # 用户实际在用的数据目录
    PKG.parent / "journal_tiers.json",                    # 源码根种子
    PKG.parent / "app" / "journal_tiers.json",
]
CATALOGS_DIR = PKG / "catalogs"

THREE_AUTH = {normalize_name(x) for x in ("中国社会科学", "中国法学", "法学研究")}

DISPATCH = {
    "外文顶级法评": ("law_review_top", "收录"),
    "台湾核心":     ("tw_law", "核心"),
    "台湾一般":     ("tw_law", "一般"),
    "台湾":         ("tw_law", "一般"),      # 旧别名
    "CLSCI":       ("clsci", None),          # None → 三大权威=权威, 其余=来源
    "外文权威":     ("ssci_law_authority", "收录"),
    "CSSCI":       ("cssci", "来源"),
    "CSSCI扩展":    ("cssci", "扩展"),
    "报纸":         ("newspaper", "报纸"),
    # 跳过: 普刊 / 外文一般 / 境外 / 未知
}

# 新私有目录的 _meta（种子文件不存在时用它初始化）
NEW_META = {
    "law_review_top": {"catalog": "law_review_top", "version": "2026-06-28-legacy",
                       "source": "个人整理（顶尖外文法律评论）", "origin": "intl",
                       "note": "私有目录（law_personal 专用）：顶尖外文法评（耶鲁/哈佛/芝大等旗舰法评）。外刊不打折(intl=1.0)→T1。level=收录。"},
    "tw_law":         {"catalog": "tw_law", "version": "2026-06-28-legacy",
                       "source": "个人整理（台湾法学刊）", "origin": "cn",
                       "note": "私有目录（law_personal 专用）：台湾法学期刊。核心→T1、一般→T2。level ∈ {核心,一般}。"},
    "ssci_law_authority": {"catalog": "ssci_law_authority", "version": "2026-06-28-legacy",
                       "source": "个人整理（SSCI 法学/犯罪学/社科旗舰权威）", "origin": "intl",
                       "note": "私有目录（law_personal 专用）：非旗舰法评的 SSCI 权威社科刊。外刊不打折(intl=1.0)→T2。level=收录。"},
    "newspaper":      {"catalog": "newspaper", "version": "2026-06-28-legacy",
                       "source": "个人整理（法律类报纸/公报）", "origin": "cn",
                       "note": "私有目录（law_personal 专用）：报纸/公报，作辅证、压最低档 T6。level=报纸。"},
}


def load_legacy(explicit=None):
    cands = [Path(explicit).expanduser()] if explicit else LEGACY_CANDIDATES
    for p in cands:
        if p.exists():
            raw = json.loads(p.read_text(encoding="utf-8"))
            js = raw.get("journals", raw) if isinstance(raw, dict) else {}
            items = {k: v for k, v in js.items() if isinstance(k, str) and not k.startswith("_")}
            return p, items
    tried = "\n  ".join(str(p) for p in cands)
    raise SystemExit("未找到旧档 journal_tiers.json，可用 --legacy <路径> 指定。已尝试：\n  " + tried)


def load_existing(cat):
    """读现有目录文件（种子），返回 (_meta, {归一名: entry})。不存在则给新 _meta。"""
    p = CATALOGS_DIR / f"{cat}.json"
    if p.exists():
        raw = json.loads(p.read_text(encoding="utf-8"))
        meta = raw.get("_meta", {})
        idx = {}
        for j in raw.get("journals", []):
            if isinstance(j, dict) and j.get("name"):
                idx[normalize_name(j["name"])] = dict(j)
        return meta, idx
    return dict(NEW_META.get(cat, {"catalog": cat})), {}


def main():
    ap = argparse.ArgumentParser(description="把旧档 journal_tiers.json 并入 journal_grading 目录数据")
    ap.add_argument("--legacy", default=os.environ.get("LOCALKB_LEGACY_TIERS", ""),
                    help="旧档 journal_tiers.json 路径；不给则按候选位置自动找（data/ → 源码根 → app/）")
    args = ap.parse_args()

    src, legacy = load_legacy(args.legacy or None)
    print(f"[migrate] 旧档：{src}  条数={len(legacy)}", flush=True)

    # 预载所有目标目录现有内容（保留种子条目/ISSN）
    targets = {}   # cat -> (_meta, idx)
    for cat in ("law_review_top", "tw_law", "ssci_law_authority", "newspaper", "clsci", "cssci"):
        targets[cat] = load_existing(cat)

    added = {c: 0 for c in targets}
    skipped = 0
    for name, tier in legacy.items():
        disp = DISPATCH.get(tier)
        if not disp:
            skipped += 1
            continue
        cat, lvl = disp
        if cat == "clsci" and lvl is None:
            lvl = "权威" if normalize_name(name) in THREE_AUTH else "来源"
        meta, idx = targets[cat]
        key = normalize_name(name)
        if key in idx:
            idx[key]["level"] = lvl               # 旧档为准定级别
            idx[key].setdefault("issn", "")
            idx[key].setdefault("name", name)
        else:
            idx[key] = {"name": name, "issn": "", "level": lvl}
            added[cat] += 1

    # 写回
    for cat, (meta, idx) in targets.items():
        meta = dict(meta)
        note = meta.get("note", "")
        if "旧档" not in note:
            meta["note"] = (note + " ｜ 已并入 2026-06-28 个人法学旧档。").strip(" ｜")
        meta["migrated_from"] = str(src)
        out = {"_meta": meta,
               "journals": sorted(idx.values(), key=lambda j: (j.get("level", ""), j.get("name", "")))}
        (CATALOGS_DIR / f"{cat}.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  {cat:20s} 共 {len(idx):4d} 条（本次新增 {added[cat]}）", flush=True)

    print(f"[migrate] 跳过(普刊/外文一般/境外/未知) {skipped} 条。完成。", flush=True)


if __name__ == "__main__":
    main()

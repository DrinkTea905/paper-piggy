# -*- coding: utf-8 -*-
"""
期刊分级（单一数据源）。
体系：CLSCI > CSSCI > CSSCI扩展 > 普刊；另设 境外 / 台湾 / 报纸 / 未知。
- 数据文件：data/journal_tiers.json（{"_meta":{...}, "journals":{规范刊名: tier}}）。
- 用途：① 建库管线给每行写 journal_tier 字段；② daemon 检索时显示层级 + 按层级排序。
- 改动期刊层级：直接编辑 journal_tiers.json（以最新 CLSCI / CSSCI 名单为准），重启 daemon 即生效。
"""
import json, re, sys
from pathlib import Path
import config as C

def _resolve_tiers_file():
    """定位期刊分级数据文件。优先用户数据目录（可编辑、随索引走）；
    缺失则回退随源码分发的只读种子 APP/journal_tiers.json（打包时一起带上，新机不再缺档）。"""
    data_f = C.DATA / "journal_tiers.json"
    if data_f.exists():
        return data_f
    seed = C.APP / "journal_tiers.json"     # 随源码分发的种子副本
    if seed.exists():
        return seed
    return data_f   # 都无：返回 data 路径，_load() 会告警并退化默认档

TIERS_FILE = _resolve_tiers_file()

# 排序权重：数字越小越权威/越靠前（sort=tier 用）。允许并列（如 CLSCI 与 台湾核心 同为 0）。
TIER_RANK = {
    "CLSCI": 0, "台湾核心": 0,     # 台湾核心刊（台大法学论丛/政大法学评论/中正法学集刊）给到 CLSCI 高度（用户决策 2026-06-28）
    "外文顶级法评": 0,              # 顶尖法律评论（耶鲁/哈佛/芝大等旗舰法评）给到 CLSCI 高度（用户决策 2026-06-28）
    "外文权威": 1,                  # SSCI 法学/犯罪学/社科/旗舰顶刊（非旗舰法评）：介于 CLSCI 与 CSSCI 之间
    "CSSCI": 2, "台湾一般": 2,      # 其他台湾刊给 CSSCI 高度（用户决策）
    "CSSCI扩展": 3,
    "普刊": 4, "外文一般": 4, "境外": 4,   # 一般外文作辅证（≈普刊）；境外=外文一般的旧别名
    "台湾": 2,                      # 旧别名（迁移后不应再出现），按 CSSCI 兜底
    "法源": 1, "官方报告": 2,        # 法律法规/司法解释→准权威档、报告/白皮书→核心档（source_rules 建库期写入，用户决策 2026-07-12）
    "报纸": 5, "未知": 6,
}
DEFAULT_TIER = "普刊"
_CACHE = None
_CACHE_KEY = None    # (路径, mtime)：任一变化就重解析，实现进程内热重载（索引期后补词表即时生效）


def _norm(name: str) -> str:
    """规范化刊名：全角/半角括号统一、去空白，便于匹配。"""
    if not name:
        return ""
    s = str(name)
    for a, b in (("（", "("), ("）", ")"), ("〔", "("), ("〕", ")"), ("【", "("), ("】", ")")):
        s = s.replace(a, b)
    return re.sub(r"\s+", "", s).strip()


def _load() -> dict:
    global _CACHE, _CACHE_KEY
    # 每次现算路径（DATA 用户档优先，缺失退 APP 种子）：索引期后补 journal_tiers.json 无需重启
    # 即可被拾起；再按 (路径, mtime) 判断是否需重解析，文件没变则直接吃缓存。
    f = _resolve_tiers_file()
    try:
        key = (str(f), f.stat().st_mtime)
    except OSError:
        key = (str(f), None)
    if _CACHE is not None and _CACHE_KEY == key:
        return _CACHE
    m = {}
    if f.exists():
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
            data = raw.get("journals", raw)
            for k, v in data.items():
                if isinstance(k, str) and k.startswith("_"):
                    continue
                m[_norm(k)] = v
        except Exception as e:
            # 不静默：手工编辑 journal_tiers.json 引入语法错误时，若悄悄退化为空表，
            # 会把全部刊物打成默认档并被 03 持久写入 LanceDB（只 add 不改）。告警到 stderr。
            print(f"[journal_tiers] 解析 {f} 失败，期刊分级退化为默认档"
                  f"（中文→普刊/外文→外文一般）：{e}", file=sys.stderr, flush=True)
            m = {}
    else:
        # 数据文件缺失（新机/打包漏带最常见）：同样告警，否则全部刊物静默退化为默认档，
        # 且会被 index_light 持久化进 papers.jsonl/stats_cache，仪表盘只见 普刊/外文一般/未知。
        print(f"[journal_tiers] 未找到分级数据 {f}，期刊分级退化为默认档"
              f"（中文→普刊/外文→外文一般）。请将 journal_tiers.json 放到 {C.DATA} 或源码目录后重建索引。",
              file=sys.stderr, flush=True)
    _CACHE, _CACHE_KEY = m, key
    return _CACHE


def _has_cjk(s: str) -> bool:
    return bool(re.search(r"[一-鿿]", s or ""))


def tier_of(journal: str) -> str:
    """返回刊物层级。未登记：拉丁文刊名→境外，中文→普刊，空→未知。"""
    if not journal or not str(journal).strip():
        return "未知"
    m = _load()
    key = _norm(journal)
    if key in m:
        return m[key]
    # 未登记：外文刊名→外文一般（辅证级）；中文→普刊
    return "外文一般" if not _has_cjk(journal) else DEFAULT_TIER


def rank_of(tier: str) -> int:
    return TIER_RANK.get(tier, 9)


def reload():
    """清缓存 + 重新定位数据文件（编辑或后补 json 后热重载用；下次 _load 会重解析）。"""
    global _CACHE, _CACHE_KEY
    _CACHE = None
    _CACHE_KEY = None

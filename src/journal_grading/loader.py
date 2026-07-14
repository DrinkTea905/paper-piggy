# -*- coding: utf-8 -*-
"""
加载层：读 grading_config.json + catalogs/*.json，建立可查索引。
路径解析沿用本项目「DATA 优先(可编辑、随索引走) → APP 种子回退(打包分发)」约定：
  - 配置：  DATA/journal_grading/grading_config.json  →  <pkg>/config/grading_config.json
  - 目录数据：DATA/journals/<catalog>.json           →  <pkg>/catalogs/<catalog>.json
DATA 取值与 config.py 完全一致（环境变量 LOCALKB_DATA，否则 APP/data；APP=src根）；
但本模块不硬 import config，缺了也能独立跑（便于自检/复用）。

索引结构（GradingData）：
  config              解析后的完整配置 dict
  by_issn[issn]       -> { "catalogs": {cat: level}, "name": 首见刊名, "versions": {cat: ver} }
  by_name[normName]   -> 同上（归一刊名键）
  recognized_issn / recognized_name : {discipline: set(...)}  快速判 @recognizedTop
两个索引同源同值：一条期刊记录同时登记到 issn 与 归一名 两把键上。
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from normalize import normalize_name, normalize_issn

PKG = Path(__file__).parent
_CATALOG_IDS = ["cssci", "clsci", "pku", "ami", "ssci", "ahci", "sjr",
                "abs", "ft50", "utd24", "fms", "erih", "warning"]

_CACHE = None


def _data_root() -> Path:
    """与 config.py 同规则解析用户数据根（不 import config，避免耦合/循环）。"""
    env = os.environ.get("LOCALKB_DATA")
    if env:
        return Path(env)
    # APP = src根 = 本包父目录；config.py 的 DATA 默认即 APP/data
    return PKG.parent / "data"


def _resolve(rel_data: str, rel_seed: str) -> Path:
    """DATA 优先，回退包内种子。"""
    f = _data_root() / rel_data
    if f.exists():
        return f
    return PKG / rel_seed


def config_path() -> Path:
    return _resolve("journal_grading/grading_config.json", "config/grading_config.json")


def catalog_path(cat: str) -> Path:
    return _resolve(f"journals/{cat}.json", f"catalogs/{cat}.json")


class GradingData:
    def __init__(self, config, by_issn, by_name, recognized_issn, recognized_name, warnings, if_values=None):
        self.config = config
        self.by_issn = by_issn
        self.by_name = by_name
        self.recognized_issn = recognized_issn
        self.recognized_name = recognized_name
        self.load_warnings = warnings  # 加载期告警（缺档/坏档），非期刊预警
        self.if_values = if_values or []  # 全库知网复合影响因子(升序)，供计算分位

    # ——— 便捷访问 ———
    def disciplines(self):
        return self.config.get("disciplines", {})

    def discipline(self, did):
        d = self.disciplines().get(did)
        if not d:
            raise KeyError(f"未知学科 '{did}'，可选：{list(self.disciplines())}")
        return d

    def archetype(self, name):
        return self.config.get("archetypes", {}).get(name, {})

    def tiers(self):
        return self.config.get("tiers", {})

    def resolution(self):
        return self.config.get("resolution", {})

    def visible_catalogs(self, disc_id):
        """该学科可见目录集：所有非私有共享目录 ∪ 该学科追加目录(disciplines[*].catalogs)。
           把"哪些私有目录该学科能用"从"巧合式正确"变成"强制正确"（堵 tw_law 等信号泄漏）。"""
        cats = self.config.get("catalogs", {}) or {}
        shared = {c for c, m in cats.items()
                  if isinstance(m, dict) and m.get("levels") and not m.get("private")}
        extra = set((self.disciplines().get(disc_id, {}) or {}).get("catalogs", []) or [])
        return shared | extra


def _register(by_issn, by_name, cat, level, name, issn, version):
    """把一条期刊记录登记到 issn 与 归一名 两把键。"""
    def _bucket(container, key):
        b = container.get(key)
        if b is None:
            b = {"catalogs": {}, "versions": {}, "name": name}
            container[key] = b
        b["catalogs"][cat] = level
        if version:
            b["versions"][cat] = version
        if not b.get("name") and name:
            b["name"] = name
        return b

    ni = normalize_issn(issn)
    if ni and len(ni) == 9:  # xxxx-xxxx
        _bucket(by_issn, ni)
    nn = normalize_name(name)
    if nn:
        _bucket(by_name, nn)


def _attach_if(by_issn, by_name, name, issn, f):
    """把知网复合影响因子附到期刊记录（issn 与 归一名两把键）；记录不存在则建最小桶(可识别、无档位信号)。"""
    ni = normalize_issn(issn)
    nn = normalize_name(name)
    for container, key, isissn in ((by_issn, ni, True), (by_name, nn, False)):
        if not key or (isissn and len(key) != 9):
            continue
        b = container.get(key)
        if b is None:
            b = {"catalogs": {}, "versions": {}, "name": name}
            container[key] = b
        if b.get("if") is None or f > b["if"]:
            b["if"] = f
        if not b.get("name") and name:
            b["name"] = name


def load(force=False) -> GradingData:
    global _CACHE
    if _CACHE is not None and not force:
        return _CACHE

    warnings = []
    cfg_p = config_path()
    try:
        config = json.loads(cfg_p.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"[journal_grading] 无法读取配置 {cfg_p}：{e}")

    # 目录 id 从配置派生：凡在 catalogs 声明且 levels 非空者都加载（自动含 law_personal 私有目录；
    # 跳过 if_cnki 这种无 levels 的数值项）。config 未声明时回退到内置清单。
    cat_ids = [c for c, m in (config.get("catalogs") or {}).items()
               if isinstance(m, dict) and m.get("levels")] or _CATALOG_IDS

    by_issn, by_name = {}, {}
    for cat in cat_ids:
        p = catalog_path(cat)
        if not p.exists():
            warnings.append(f"缺目录数据：{cat}（{p}）—— 该目录信号将全部缺席，命中它的刊会降级；"
                            f"按《离线数据获取清单》补 {cat}.json 后 reload。")
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            warnings.append(f"坏目录数据：{cat}（{p}）解析失败：{e} —— 已跳过该目录。")
            continue
        ver = (raw.get("_meta") or {}).get("version", "")
        for j in raw.get("journals", []):
            if not isinstance(j, dict):
                continue
            _register(by_issn, by_name, cat,
                      j.get("level", ""), j.get("name", ""), j.get("issn", ""), ver)

    # 认可顶刊清单 → 便于 O(1) 判 @recognizedTop
    recognized_issn, recognized_name = {}, {}
    for did, lst in (config.get("recognizedTopLists") or {}).items():
        ri, rn = set(), set()
        for it in lst or []:
            ni = normalize_issn(it.get("issn", ""))
            if ni and len(ni) == 9:
                ri.add(ni)
            nn = normalize_name(it.get("name", ""))
            if nn:
                rn.add(nn)
        recognized_issn[did] = ri
        recognized_name[did] = rn

    # 知网复合影响因子（if_cnki）：附到期刊记录 + 收集全库分位所需的排序值
    if_values = []
    ifp = catalog_path("if_cnki")
    if ifp.exists():
        try:
            raw_if = json.loads(ifp.read_text(encoding="utf-8"))
            for j in raw_if.get("journals", []):
                try:
                    f = float(j.get("if"))
                except Exception:
                    continue
                if f <= 0:
                    continue
                if_values.append(f)
                _attach_if(by_issn, by_name, j.get("name", ""), j.get("issn", ""), f)
        except Exception as e:
            warnings.append(f"if_cnki 加载失败：{e}")
    if_values.sort()

    if warnings:
        for w in warnings:
            print("[journal_grading] " + w, file=sys.stderr, flush=True)

    _CACHE = GradingData(config, by_issn, by_name, recognized_issn, recognized_name, warnings, if_values)
    return _CACHE


def reload() -> GradingData:
    """编辑 config / 目录数据后热重载。"""
    return load(force=True)


if __name__ == "__main__":
    d = load()
    print("学科：", list(d.disciplines()))
    print("ISSN 索引条数：", len(d.by_issn), "  归一名索引条数：", len(d.by_name))
    print("law 认可顶刊(归一名)：", sorted(d.recognized_name.get("law", [])))
    if d.load_warnings:
        print("加载告警：", len(d.load_warnings), "条")

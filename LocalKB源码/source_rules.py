# -*- coding: utf-8 -*-
"""
文献性质定档（法源/官方报告）+ 单篇手动改档。
- 期刊分级引擎只认刊名，法规/报告/白皮书没有刊名 → 一律被压成"待确认"(0.175)，比普刊还低。
  本模块在期刊分级之前按【手动改档 > 条目类型 > 标题规则】三级定档（2026-07-12 用户拍板）：
  法源（法律法规/司法解释/中央决定/司法文件）→ T1b 准权威 0.92；报告/白皮书 → T2 核心 0.85。
- 标题规则只对 TITLE_SCOPE 内的"非学术条目类型"生效——论文标题里出现法名
  （如《…预防未成年人犯罪法》评析）绝不能被误升，所以 journalArticle/thesis 等永不进标题匹配。
- 手动改档存 data/tier_overrides.json（{paper_key: "T1".."T5"}），优先级最高，
  由 POST /paper/tier 写入；按文件 mtime 热重载，改完即时生效、不用重启/重建。
"""
import json, os, re, threading, time
from pathlib import Path
import config as C

# tier code → 权重分。与 journal_grading/config/grading_config.json 的 tiers 表保持一致；
# 法源/报告是本土文献，不乘中外系数。
TIER_W = {"T1": 1.00, "T1b": 0.92, "T2": 0.85, "T3": 0.65, "T4": 0.45, "T5": 0.25}
TIER_RANK = {"T1": 0, "T1b": 1, "T2": 2, "T3": 3, "T4": 4, "T5": 5}

# 条目类型 → 档位（Zotero itemtype；folder 模式 AI 抽的 report 同样命中）
ITEMTYPE_TIER = {"statute": "T1b", "case": "T1b", "standard": "T1b", "report": "T2"}

# 标题规则的适用类型：只限"可能装着法源/报告的非学术类型"
TITLE_SCOPE = {"webpage", "blogPost", "document", "book", "forumPost", "newspaperArticle"}

# 规则命中写进建库层（papers.jsonl/LanceDB 的 journal_tier）的旧离散档标签；
# 手动改档不写建库层（动态层已覆盖徽标/加权/过滤，建库层只在重建时才会变）。
OLD_TIER_LABEL = {"T1b": "法源", "T2": "官方报告"}

# 标题规则 v3——v2 按用户核对过的《待确认清单-法源与报告.md》定稿，v3 采纳对抗审查修正：
# ① 报告词先于法源词判定（防「…- 中华人民共和国最高人民法院公报」站名后缀把工作报告/司法统计误标法源）；
# ② 全名分支加 (?![院部学]) 负向断言（机构名「最高人民法院/司法部/法学…」不是法名）；
# ③ 去掉过宽的裸「量刑」（量刑指导意见由「指导意见」分支接住）；lookbehind 补「分」字（评分法/倍分法）；
# ④ 补国际文书（联合国公约/北京规则）、外文法典（Code of Criminal Procedure 等）、日文「◯◯法｜条文」；
# ⑤ 排除表加「评析/解读/观察/今起施行」等评论新闻词。排除表对整个标题匹配，防统计方法与教学材料误升。
RE_LAW = re.compile(
    r'(中华人民共和国.{1,20}法(?![院部学])'
    r'|(?<![办方倍分看想说做算写手])法(?:\s*[（(]|｜|$)'
    r'|条例|实施办法|管理办法|暂行办法|司法解释|立法规划'
    r'|(?:最高人民法院|最高人民检察院|两高|中共中央|国务院|中央办公厅|国务院办公厅|全国人民代表大会).{0,30}(?:意见|通知|决定|规定|解释|纪要|规则|办法)'
    r'|指导意见|服务规范|一般性意见|General Comment'
    r'|(?:联合国|儿童权利委员会).{0,30}(?:公约|规则|准则|意见)|国际公约|北京规则|儿童权利公约'
    r'|Rules of Criminal Procedure|Criminal Procedure Law|Code of Criminal Procedure|Criminal Code|Penal Code'
    r'|Guiding Opinions|Juvenile (?:Act|Law)|Convention on)')
RE_REPORT = re.compile(
    r'(白皮书|工作报告|发展报告|统计分析报告|调查报告|研究报告|评估报告|司法统计|统计年报|年鉴|蓝皮书'
    r'|犯罪白書|司法統計|Task Force Report|Annual\s+\w+\s+Report|Annual Report|Status Report|justice statistics'
    r'|Statistical (?:Report|Tables|Yearbook))', re.I)
RE_EXCLUDE = re.compile(
    r'(讲座|课程|奖学金|教程|教材|写作指南|作业|招聘|论坛预告|书评|访谈|方法|倍分法|Monte Carlo'
    r'|评析|述评|解读|观察|随笔|札记|今起施行|问题研究(?!报告)'
    r'|一图|图解|读懂|普法|漫画)', re.I)

OVERRIDE_FILE = C.DATA / "tier_overrides.json"
_LOCK = threading.Lock()
_OV = {"mtime": None, "data": {}}


def _load_overrides():
    """按 mtime 热重载 {key: tier_code}。文件缺失/损坏 → 空表，不抛错。"""
    with _LOCK:
        try:
            mt = OVERRIDE_FILE.stat().st_mtime
        except OSError:
            _OV["mtime"], _OV["data"] = None, {}
            return _OV["data"]
        if _OV["mtime"] != mt:
            try:
                raw = json.loads(OVERRIDE_FILE.read_text(encoding="utf-8"))
                _OV["data"] = {k: v for k, v in raw.items() if v in TIER_W}
                _OV["mtime"] = mt
            except Exception:
                # 瞬时读失败（OneDrive/杀软共享锁）：不缓存 mtime，下次调用重试——
                # 否则失败被按成功缓存，所有手动改档静默消失且不自愈（对抗审查 #1）。
                _OV["data"] = {}
                _OV["mtime"] = None
        return _OV["data"]


def overrides_mtime():
    """供分布缓存做失效键（改档后分布卡要重算）。"""
    try:
        return OVERRIDE_FILE.stat().st_mtime
    except OSError:
        return 0.0


def set_override(key, tier):
    """写/删一条手动改档。tier=None/"" → 恢复自动。写法与 grading_svc 一致：原子替换 + 重试。"""
    if not key:
        raise ValueError("key 不能为空")
    if tier and tier not in TIER_W:
        raise ValueError(f"非法档位 {tier}（可选：{'/'.join(TIER_W)}）")
    with _LOCK:
        data = {}
        try:
            if OVERRIDE_FILE.exists():
                data = json.loads(OVERRIDE_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if tier:
            data[key] = tier
        else:
            data.pop(key, None)
        OVERRIDE_FILE.parent.mkdir(parents=True, exist_ok=True)
        txt = json.dumps(data, ensure_ascii=False, indent=1)
        tmp = OVERRIDE_FILE.with_suffix(".json.tmp")
        tmp.write_text(txt, encoding="utf-8")
        for i in range(6):
            try:
                os.replace(tmp, OVERRIDE_FILE)
                break
            except PermissionError:
                time.sleep(0.15 * (i + 1))
        else:
            OVERRIDE_FILE.write_text(txt, encoding="utf-8")
        _OV["mtime"] = None      # 强制下次重载


def classify_title(title):
    """标题 → "T1b"（法源）/"T2"（报告）/None。调用方负责 itemtype 范围把关。
       报告词先判：工作报告/司法统计页常带「…- 中华人民共和国最高人民法院公报」类站名后缀，
       后者会撞上法源全名分支——报告优先才能落对档（对抗审查 #14）。"""
    if not title:
        return None
    if RE_EXCLUDE.search(title):
        return None
    if RE_REPORT.search(title):
        return "T2"
    if RE_LAW.search(title):
        return "T1b"
    return None


def rule_tier(itemtype, title):
    """纯规则定档（不含手动）：条目类型优先，其次标题规则（仅 TITLE_SCOPE 类型）。"""
    t = ITEMTYPE_TIER.get(itemtype or "")
    if t:
        return t
    if (itemtype or "") in TITLE_SCOPE:
        return classify_title(title or "")
    return None


def resolve(key, itemtype, title):
    """三级定档：手动 > 类型 > 标题。命中返回 {tier, weight, rank, src}，未命中 None（走期刊分级）。"""
    ov = _load_overrides().get(key or "")
    if ov:
        return {"tier": ov, "weight": TIER_W[ov], "rank": TIER_RANK[ov], "src": "manual"}
    t = rule_tier(itemtype, title)
    if t:
        return {"tier": t, "weight": TIER_W[t], "rank": TIER_RANK[t], "src": "rule"}
    return None

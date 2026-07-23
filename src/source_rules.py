# -*- coding: utf-8 -*-
"""
文献性质识别 + 单篇手动改档。
- 旧期刊引擎只认刊名，无法正确评价法规、报告、书籍等非期刊来源。
  本模块识别真实文献性质，并保留旧内部 T? 权重用于兼容；普通接口统一由 grading_svc
  显示为权威/顶级/核心/普通四档。
- 标题规则只对 TITLE_SCOPE 内的"非学术条目类型"生效——论文标题里出现法名
  （如《…预防未成年人犯罪法》评析）绝不能被误升，所以 journalArticle/thesis 等永不进标题匹配。
- 手动改档存 data/tier_overrides.json（{paper_key: "T1".."T5"}），优先级最高，
  由 POST /paper/tier 写入；按文件 mtime 热重载，改完即时生效、不用重启/重建。

统一评价层会先识别文献性质/客观标签，再在最后应用手动改档。因此手动改档只改变
评价，不会伪造或抹掉「三大刊 / CLSCI / 书籍 / 法源」等客观标签。本模块保留
``resolve`` 兼容旧调用方；新代码应优先调用 ``grading_svc.evaluate_paper``。
"""
import json, os, re, threading, time
from pathlib import Path
import config as C

# tier code → 权重分。与 journal_grading/config/grading_config.json 的 tiers 表保持一致；
# 法源/报告是本土文献，不乘中外系数。
TIER_W = {"T1": 1.00, "T1b": 0.92, "T2": 0.85, "T3": 0.65, "T4": 0.45, "T5": 0.25}
TIER_RANK = {"T1": 0, "T1b": 1, "T2": 2, "T3": 3, "T4": 4, "T5": 5}

# 面向新接口的四档稳定代码。旧 T? 记录只在读取时折叠，不强制重写用户文件。
BAND_TO_TIER = {"authority": "T1", "top": "T1b", "core": "T2", "normal": "T5"}
TIER_TO_BAND = {
    "T1": "authority", "T1b": "top", "T2": "core", "T3": "core",
    "T4": "normal", "T5": "normal", "待确认": "normal",
}
OVERRIDE_VALUES = set(TIER_W) | set(BAND_TO_TIER)

# 条目类型 → 档位（Zotero itemtype；folder 模式 AI 抽的 report 同样命中）
ITEMTYPE_TIER = {
    "book": "T1", "bookSection": "T1", "thesis": "T1b",
    "statute": "T1b", "case": "T1b", "standard": "T1b", "report": "T2",
}

# 标题规则的适用类型：只限"可能装着法源/报告的非学术类型"
TITLE_SCOPE = {"webpage", "blogPost", "document", "forumPost", "newspaperArticle"}

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
RE_DATASET = re.compile(
    r'(数据集|资料集|数据库|统计数据|微观数据|开放数据|dataset|data\s+set|microdata|open\s+data)', re.I)

SOURCE_TYPE_NAMES = {
    "journal_article": "期刊论文", "book": "书籍", "book_section": "书章",
    "thesis": "学位论文", "legal_source": "法源", "case": "案例",
    "standard": "标准", "report": "报告与白皮书", "dataset": "数据集",
    "preprint": "预印本", "conference_paper": "会议论文", "web": "网页与其他",
    "newspaper": "报纸", "other": "其他",
}

_CONTAINER_TYPES = {"webpage", "blogPost", "document", "forumPost", "newspaperArticle"}
_AUTHORITY_ORG_RE = re.compile(
    r'(全国人民代表大会|国务院|最高人民法院|最高人民检察院|国家统计局|司法部|公安部|'
    r'人民政府|人民法院|人民检察院|联合国|世界银行|国际货币基金组织|经合组织|OECD|WHO|'
    r'政府部门|国家级|官方)', re.I)

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
                _OV["data"] = {k: v for k, v in raw.items() if v in OVERRIDE_VALUES}
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
    if tier and tier not in OVERRIDE_VALUES:
        raise ValueError("非法档位 %s（可选：authority/top/core/normal，兼容 T1/T1b/T2/T3/T4/T5）" % tier)
    with _LOCK:
        data = {}
        try:
            if OVERRIDE_FILE.exists():
                data = json.loads(OVERRIDE_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            # 读旧表失败（OneDrive/杀软共享锁、文件损坏）时绝不能拿空表覆写——那会静默清空
            # 用户全部手动改档。直接抛错中止本次写入，由调用方 try/except 返回错误并提示重试。
            # 注意：_LOCK 不可重入且此刻已持有，不能再调 _load_overrides（会死锁）。
            raise RuntimeError(f"读取现有改档表失败，已中止写入以防丢失既有改档：{e}")
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


def get_override(key):
    """返回一篇的原始手动记录（四档稳定代码或历史 T?），没有则 None。"""
    return _load_overrides().get(key or "")


def band_of_tier(value):
    """四档稳定代码；同时接受新 band 与历史 T?。"""
    if value in BAND_TO_TIER:
        return value
    return TIER_TO_BAND.get(value, "normal")


def internal_tier_of_override(value):
    """把四档手动值映射到兼容内部档；历史 T? 原样保留其细分权重。"""
    return BAND_TO_TIER.get(value, value if value in TIER_W else "T5")


def is_authoritative_org(paper):
    """保守识别官方/权威机构。只用于报告、数据集的客观细分，不从普通作者名猜。"""
    if not isinstance(paper, dict):
        return False
    fields = ("institution", "reporting_institution", "publisher", "website_title", "author", "court")
    blob = " ".join(str(paper.get(k) or "") for k in fields)
    return bool(_AUTHORITY_ORG_RE.search(blob))


def classify_source_type(paper):
    """识别真实文献性质；容器型条目允许由可靠标题信号覆盖。"""
    paper = paper if isinstance(paper, dict) else {}
    it = str(paper.get("itemtype") or "").strip()
    title = str(paper.get("title") or "")

    fixed = {
        "journalArticle": "journal_article", "book": "book", "bookSection": "book_section",
        "thesis": "thesis", "statute": "legal_source", "case": "case",
        "standard": "standard", "report": "report", "dataset": "dataset",
        "preprint": "preprint", "conferencePaper": "conference_paper",
    }
    if it in fixed:
        return fixed[it]
    if it in _CONTAINER_TYPES:
        if RE_EXCLUDE.search(title):
            inferred = None
        elif RE_DATASET.search(title):
            inferred = "dataset"
        elif RE_REPORT.search(title):
            inferred = "report"
        elif RE_LAW.search(title):
            inferred = "legal_source"
        else:
            inferred = None
        if inferred:
            return inferred
        return "newspaper" if it == "newspaperArticle" else "web"
    # 兼容旧 papers.jsonl：itemtype 缺失但有刊名时，仍按期刊论文处理。
    if paper.get("journal"):
        return "journal_article"
    return "other"


def source_type_name(source_type):
    return SOURCE_TYPE_NAMES.get(source_type, SOURCE_TYPE_NAMES["other"])


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
        t = internal_tier_of_override(ov)
        return {"tier": t, "weight": TIER_W[t], "rank": TIER_RANK[t], "src": "manual",
                "band": band_of_tier(ov), "manual_code": ov}
    t = rule_tier(itemtype, title)
    if t:
        return {"tier": t, "weight": TIER_W[t], "rank": TIER_RANK[t], "src": "rule"}
    return None

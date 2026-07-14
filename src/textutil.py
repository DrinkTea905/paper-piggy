# -*- coding: utf-8 -*-
"""文本工具：jieba 分词（建库与查询必须用同一套，加载题录关键词生成的法律词典）
+ 通用清洗/键名工具（clean/safe_name/de_emoji），供数据源无关的建库管线共用。"""
import re, sys, hashlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C
import jieba

# ---- 通用文本/键名工具（原 bibutil，去 .bib 后迁到这里）----
ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
EMOJI   = re.compile(r'[☀-➿\U0001F000-\U0001FAFF←-⇿⬀-⯿]')

def safe_name(key):
    """把文献 key 变成安全文件名（过长则截断 + md5 尾）。"""
    s = ILLEGAL.sub('_', key)
    if len(s) > 200:
        s = s[:188] + '_' + hashlib.md5(key.encode('utf-8')).hexdigest()[:8]
    return s

def clean(s):
    """清洗字段：去换行、去残留花括号/反斜杠、压缩空白。"""
    if not s:
        return ""
    s = s.replace("\n", " ").replace("\r", " ")
    s = re.sub(r'[{}\\]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def de_emoji(s):
    return EMOJI.sub('', s or '').strip()

_inited = False
# 只放真正的功能虚词；保留 第/条/款/项/罪 等法律相关字以利精确匹配
STOP = set("的 了 和 与 及 在 是 也 对 为 等 中 我 你 他 它 这 那 之 其 或 而 并 被 把 着 已 将 "
           "就 都 很 更 最 由 从 以 于 向 也 又 不 没 有 个 们 ， 。 、 ； ： （ ） 《 》 ".split())

def init_jieba():
    global _inited
    if not _inited:
        if C.LEGAL_DICT.exists():
            jieba.load_userdict(str(C.LEGAL_DICT))
        jieba.initialize()
        _inited = True

def reload_userdict():
    """强制重载 jieba 用户词典。index_light 重建法律词典后，查询侧分词器仍是进程启动时的旧词典
       （_inited 一次性初始化会把旧词典钉死整个进程），新加的法律术语切不出来。本函数清掉初始化
       标记后重跑 init_jieba()：让 jieba 从主词典重建 FREQ 再叠加最新 LEGAL_DICT。首次懒加载行为不变。"""
    global _inited
    _inited = False
    try:
        jieba.dt.initialized = False   # 迫使下次 initialize() 从主词典重建，抹掉旧 userdict 词
    except Exception:
        pass                            # jieba 内部结构异变时不拦路，init_jieba 仍会补加最新词
    init_jieba()

def tokenize(text):
    init_jieba()
    out = []
    for t in jieba.lcut(text or ""):
        t = t.strip().lower()
        if not t or t in STOP:
            continue
        if re.fullmatch(r'[\W_]+', t):   # 纯标点/空白
            continue
        out.append(t)
    return out

# ═══ EN-L3：法学同义词/核心术语 loader（契约12）═══════════════════
# 出厂词表在 legal_lexicon.py（纯数据模块）；运行期若存在 data/legal_synonyms.txt
# （每行一组、顿号/逗号分隔，# 起注释）则**叠加**在出厂表之后——用户不用改代码就能补组。
# 模块级缓存：同义词按文件 mtime 失效（用户改完词表即时生效，不用重启）；核心词表纯静态缓存一次。
_LEX_CACHE = {"syn": None, "syn_mtime": "unset", "core": None}

def _syn_file():
    return getattr(C, "LEGAL_SYNONYMS_FILE", C.DATA / "legal_synonyms.txt")

def load_legal_synonyms():
    """返回 list[set[str]]（组员统一小写，与 tokenize 输出对齐）。任何一步失败都退空表/出厂表，
       绝不让词表问题炸掉检索主链路。"""
    p = _syn_file()
    try:
        mt = p.stat().st_mtime
    except OSError:
        mt = None                       # 文件不存在：只用出厂表
    if _LEX_CACHE["syn"] is not None and _LEX_CACHE["syn_mtime"] == mt:
        return _LEX_CACHE["syn"]
    groups = []
    try:
        import legal_lexicon as LX
        for g in LX.SYNONYMS:
            gs = {str(w).strip().lower() for w in g if str(w).strip()}
            if len(gs) >= 2:            # 单词成不了"组"，丢弃防手滑
                groups.append(gs)
    except Exception:
        pass
    if mt is not None:
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.split("#", 1)[0].strip()   # 支持行内注释
                if not line:
                    continue
                gs = {w.strip().lower() for w in re.split(r'[、，,;；\t]', line) if w.strip()}
                if len(gs) >= 2:
                    groups.append(gs)
        except Exception:
            pass                        # 用户文件损坏：出厂表照常工作
    _LEX_CACHE["syn"], _LEX_CACHE["syn_mtime"] = groups, mt
    return groups

def load_core_terms():
    """返回出厂核心术语 list[str]（原样大小写；进 jieba 词典用，不需 lower）。"""
    if _LEX_CACHE["core"] is None:
        try:
            import legal_lexicon as LX
            _LEX_CACHE["core"] = [str(t).strip() for t in LX.CORE_TERMS if str(t).strip()]
        except Exception:
            _LEX_CACHE["core"] = []
    return _LEX_CACHE["core"]

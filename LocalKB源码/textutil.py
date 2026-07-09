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

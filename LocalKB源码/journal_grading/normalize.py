# -*- coding: utf-8 -*-
"""
刊名归一化（journal_grading 第①层的匹配基础）。
按方案 §matching.normalize：trim → 去《》和空格 → 全半角归一 → 简繁归一 → 拉丁字母转小写。
纯函数、无副作用、无外部依赖（繁简用内置常用字映射；如库内已装 opencc/zhconv 会自动优先用之）。
"""
import re
import unicodedata

# 书名号/各类括号一并剥掉（刊名匹配不看这些装饰符）
_BRACKETS = "《》〈〉「」『』【】〔〕[]{}()（）"
_BRACKET_RE = re.compile("[" + re.escape(_BRACKETS) + "]")
_WS_RE = re.compile(r"\s+")

# 可选：若环境里有更全的繁简库则优先（不作硬依赖，缺了也能跑）
_ZH_CONVERT = None
try:  # zhconv 轻量、纯 Python
    import zhconv as _zhconv  # type: ignore
    _ZH_CONVERT = lambda s: _zhconv.convert(s, "zh-hans")
except Exception:
    try:  # opencc（若已随其它组件装了）
        from opencc import OpenCC as _OpenCC  # type: ignore
        _cc = _OpenCC("t2s")
        _ZH_CONVERT = _cc.convert
    except Exception:
        _ZH_CONVERT = None

# 兜底繁简表：覆盖期刊名里高频的繁体字（无 zhconv/opencc 时用）。可按需扩充。
_T2S = {
    "學": "学", "報": "报", "經": "经", "濟": "济", "會": "会", "計": "计", "評": "评",
    "論": "论", "與": "与", "現": "现", "實": "实", "國": "国", "際": "际", "傳": "传",
    "播": "播", "歷": "历", "史": "史", "語": "语", "研": "研", "究": "究", "數": "数",
    "量": "量", "術": "术", "財": "财", "貿": "贸", "當": "当", "代": "代", "馬": "马",
    "剋": "克", "義": "义", "產": "产", "業": "业", "廣": "广", "東": "东", "師": "师",
    "範": "范", "華": "华", "區": "区", "縣": "县", "臺": "台", "灣": "湾", "館": "馆",
    "體": "体", "戲": "戏", "劇": "剧", "藝": "艺", "術語": "术语", "圖": "图", "書": "书",
    "館學": "馆学", "檔": "档", "訊": "讯", "電": "电", "腦": "脑", "後": "后", "從": "从",
    "並": "并", "關": "关", "係": "系", "問": "问", "題": "题",
}


def _t2s(s: str) -> str:
    if _ZH_CONVERT is not None:
        try:
            return _ZH_CONVERT(s)
        except Exception:
            pass
    return "".join(_T2S.get(ch, ch) for ch in s)


def normalize_name(name) -> str:
    """归一化刊名，用于回退匹配。空/None → ''。"""
    if not name:
        return ""
    s = str(name)
    # 全角→半角、兼容分解（NFKC 把全角字母数字、全角括号等一起收敛）
    s = unicodedata.normalize("NFKC", s)
    s = s.strip()
    s = _t2s(s)                     # 简繁归一（NFKC 之后，避免顺序问题）
    s = _BRACKET_RE.sub("", s)      # 去书名号/括号
    s = _WS_RE.sub("", s)           # 去所有空白
    s = s.lower()                   # 拉丁字母转小写（中文无影响）
    return s


def normalize_issn(issn) -> str:
    """归一化 ISSN：仅留数字与 X，转大写，规整成 xxxx-xxxx。无效→''。"""
    if not issn:
        return ""
    s = re.sub(r"[^0-9Xx]", "", str(issn)).upper()
    if len(s) == 8:
        return s[:4] + "-" + s[4:]
    return s  # 长度异常原样返回（供上层判无效）


if __name__ == "__main__":
    # 快速自测
    for a, b in [
        ("《中国法学》", "中国法学"),
        ("经济学 (季刊)", "经济学季刊"),
        ("Ｊｏｕｒｎａｌ　ｏｆ　Ｆｉｎａｎｃｅ", "journaloffinance"),
        ("現代傳播（中國傳媒大學學報）", "现代传播中国传媒大学学报"),
    ]:
        got = normalize_name(a)
        print(("OK " if got == b else "!! ") + repr(a) + " -> " + repr(got) + (" 期望 " + repr(b) if got != b else ""))
    print("issn:", normalize_issn("1003 1707"), normalize_issn("1002-011x"))

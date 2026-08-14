# -*- coding: utf-8 -*-
"""按【单篇】重新校准阈值：合计值会把方差洗掉，判定却是单篇做的。
另修正一处测量错误：真刊 PDF 里半角 : ; ? 大量存在，只数全角会系统性低估。"""
import json, os, re, sys, statistics as st
sys.stdout.reconfigure(encoding="utf-8")

H = r"C:\Users\Lsj13\AppData\Local\Temp\claude\D--Onedrive-AI------\56aec02f-aa69-46b1-82fe-883f440b937e\scratchpad"
EX = r"D:\PaperPiggy\data\extracted"
rows = json.load(open(os.path.join(H, "corpus_index.json"), encoding="utf-8"))
CSS = {"822QP5P3","W76ZYVVH","VKVBQWSM","J5AQZNCK","3K7BCMHV","492SAXKB",
       "V5UXXRTT","4I7ZNSWZ","FMR4R73F","4RSHIGUL"}
TOP4 = {"中国社会科学","中国法学","法学研究","中外法学"}
CJK = r"\u4e00-\u9fff"
FN = re.compile(r"^\s*(〔\s*\d+\s*〕|\[\s*\d+\s*\]|［\d+］|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]|\(\s*\d+\s*\))\s*")
CITE = re.compile(r"(参见|载《|译，|出版社|第\s*\d+\s*页|\bpp?\.\s*\d+|University Press|ed\.|Vol\.)")
TRAD = set("為與這們對實現關學說當經濟權灣區證據並眾體處罰調機觀護會來發國")


def clean(pages):
    lines = []
    for txt in pages:
        for ln in txt.splitlines():
            s = ln.strip()
            if not s or len(s) <= 3 or re.fullmatch(r"[\-—·•\.\d\s]+", s):
                continue
            if FN.match(s) or (CITE.search(s) and len(s) < 160):
                continue
            if len(re.findall(r"[A-Za-z]", s)) > len(s) * 0.4:
                continue
            lines.append(s)
    t = "\n".join(lines)
    t = re.sub(r"(?<=[%s\u3000-\u303f\uff00-\uffef])[ \t]+(?=[%s\u3000-\u303f\uff00-\uffef])" % (CJK, CJK), "", t)
    return re.sub(r"\n+", "", t)


# 指标：半角/全角一并统计
M = [
    ("不是A而是B等",  r"不是[^。]{1,20}而是|而不是|不在于[^。]{1,20}而在于", "max"),
    ("这一＋抽象名词", r"这一[一-鿿]",                                   "max"),
    ("破折号",        r"——|--",                                       "max"),
    ("分号",          r"[；;]",                                        "max"),
    ("冒号",          r"[：:]",                                        "max"),
    ("凑三",          r"三[点项重组]|三个(层次|方面|坐标)|理由有三",        "max"),
    ("应当／不得",    r"应当|不得",                                     "max"),
    ("元评论总起",    r"[值需][得要][^。]{0,4}[说指注][明出意]",           "max"),
    ("平判断词",      r"难以成立|不能成立|难以自圆|并无规范依据",           "max"),
    ("本文",          r"本文",                                          "max"),
    ("例如等",        r"例如|比如|又如|再如|譬如",                        "min"),
    ("转折词",        r"但是|然而|虽然|尽管|而且",                        "min"),
    ("问号",          r"[？?]",                                        "min"),
    ("点名解释方法",  r"文义解释|体系解释|目的解释|历史解释|立法原意",       "min"),
    ("让步词",        r"的确|确实|不可否认|应当承认",                     "min"),
    ("实践中等",      r"实践中|调研中|实务上|实务中",                     "min"),
]

docs = []
for r in rows:
    if r["journal"].strip() not in TOP4 or r["key"] in CSS or r["nchar"] < 8000:
        continue
    p = os.path.join(EX, r["key"] + ".json")
    if not os.path.exists(p):
        continue
    d = json.load(open(p, encoding="utf-8"))
    t = clean([(pg.get("text") or "") for pg in d.get("pages") or []])
    han = re.findall(r"[一-鿿]", t)
    if len(han) < 6000:
        continue
    if sum(1 for c in han if c in TRAD) / len(han) > 0.012:
        continue
    docs.append((r, t, len(han)))

print("单篇样本 %d 篇（每篇 ≥6000 汉字）\n" % len(docs))
print("%-16s%8s%8s%8s%8s%8s   %s" % ("指标/万汉字", "p10", "中位", "p90", "p95", "最大", "建议阈值"))
print("-" * 86)
out = {}
for name, pat, kind in M:
    v = sorted(len(re.findall(pat, t)) / h * 10000 for _, t, h in docs)
    q = lambda p: v[min(len(v) - 1, int(len(v) * p))]
    if kind == "max":
        sug = round(q(0.95) * 1.15, 1)      # 95 分位再放 15% 余量
    else:
        sug = round(q(0.10) * 0.85, 2)      # 10 分位再降 15%
    out[name] = sug
    print("%-16s%8.2f%8.2f%8.2f%8.2f%8.2f   %s %s" %
          (name, q(0.10), st.median(v), q(0.90), q(0.95), v[-1],
           "≤" if kind == "max" else "≥", sug))

json.dump(out, open(os.path.join(H, "thresholds.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("\n已写出 thresholds.json")

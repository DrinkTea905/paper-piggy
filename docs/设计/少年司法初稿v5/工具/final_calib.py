# -*- coding: utf-8 -*-
"""终选：只保留在【单篇】层面真能把 AI 稿与真刊分开的指标。
判据：AI 三份稿全部落在真刊分布之外（上限类 > p95，下限类 < p5）才算通过。
并修掉复核发现的 OCR 假命中（所「有论述」性论文、「工序」、「样本」）。"""
import glob, json, os, re, statistics as st, sys
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


# 修掉假命中：有论述要求前面不是「所/具/含/占」；术语泄漏去掉「工序」（真刊 14 处均为「加工工序」）
M = [
    ("有论述（作主语）",   r"(?<![所具含占})])有论述|另有论述|已有论述", "max"),
    ("本节／本章",        r"本[节章]",                                "max"),
    ("归结起来组",        r"归结起来|如此看来|这足以说明|严格说来",       "max"),
    ("工作流术语泄漏",     r"落位|闸门|选边|交付名词|骨架卡|两栏",         "max"),
    ("「」引号",          r"「",                                     "max"),
    ("不是A而是B等",      r"不是[^。]{1,20}而是|而不是|不在于[^。]{1,20}而在于", "max"),
    ("这一＋抽象名词",     r"这一[一-鿿]",                             "max"),
    ("破折号",           r"——|--",                                  "max"),
    ("分号",             r"[；;]",                                   "max"),
    ("冒号",             r"[：:]",                                   "max"),
    ("凑三",             r"三[点项重组]|三个(层次|方面|坐标)|理由有三",     "max"),
    ("应当／不得",        r"应当|不得",                                "max"),
    ("元评论总起",        r"[值需][得要][^。]{0,4}[说指注][明出意]",        "max"),
    ("平判断词",          r"难以成立|不能成立|难以自圆|并无规范依据",        "max"),
    ("本文",             r"本文",                                     "max"),
    ("讲解腔合计",        r"先说|再说|接下来|第[一二三四]组|[须还]先?说明|有它的道理|问题就出在|最要紧|的好处|无非是", "max"),
    ("例如等",           r"例如|比如|又如|再如|譬如",                   "min"),
    ("转折词",           r"但是|然而|虽然|尽管|而且",                   "min"),
    ("问号",             r"[？?]",                                   "min"),
    ("笔者",             r"笔者",                                     "min"),
    ("让步词",           r"的确|确实|不可否认|应当承认",                 "min"),
    ("实践中等",         r"实践中|调研中|实务上|实务中",                 "min"),
]

real = []
for r in rows:
    if r["journal"].strip() not in TOP4 or r["key"] in CSS or r["nchar"] < 8000:
        continue
    p = os.path.join(EX, r["key"] + ".json")
    if not os.path.exists(p):
        continue
    d = json.load(open(p, encoding="utf-8"))
    t = clean([(pg.get("text") or "") for pg in d.get("pages") or []])
    han = re.findall(r"[一-鿿]", t)
    if len(han) < 6000 or sum(1 for c in han if c in TRAD) / len(han) > 0.012:
        continue
    real.append((t, len(han)))

def rd(p):
    t = open(p, encoding="utf-8", errors="ignore").read()
    t = re.sub(r"\[\^\d+\]", "", t); t = re.sub(r"(?m)^\[\^\d+\]:.*$", "", t)
    t = re.sub(r"(?m)^\s*[>#|].*$", "", t); t = re.sub(r"[*`_]", "", t)
    return t, len(re.findall(r"[一-鿿]", t))

ai = [("A稿", *rd(os.path.join(H, "draft_A.txt"))),
      ("B稿", *rd(os.path.join(H, "draft_B.txt")))]
par = sorted(glob.glob(os.path.join(r"D:\PaperPiggy\0_Agent交付物\核准追诉平行稿", "正文_第*.md")))
pt = "".join(open(f, encoding="utf-8").read() for f in par)
pt = re.sub(r"\[\^\d+\]", "", pt); pt = re.sub(r"(?m)^\s*[>#|].*$", "", pt); pt = re.sub(r"[*`_]", "", pt)
ai.append(("平行稿", pt, len(re.findall(r"[一-鿿]", pt))))

print("真刊单篇 %d 篇 ｜ AI 稿 %d 份\n" % (len(real), len(ai)))
print("%-14s%8s%8s%8s%8s | %7s%7s%7s | %s" %
      ("指标/万汉字", "p5", "中位", "p95", "最大", "A稿", "B稿", "平行", "终选判定"))
print("-" * 104)
keep = {}
for name, pat, kind in M:
    v = sorted(len(re.findall(pat, t)) / h * 10000 for t, h in real)
    q = lambda p: v[min(len(v) - 1, int(len(v) * p))]
    a = [len(re.findall(pat, t)) / h * 10000 for _, t, h in ai]
    if kind == "max":
        ok = all(x > q(0.95) for x in a)
        thr = round(q(0.95) * 1.1, 1)
    else:
        ok = all(x < q(0.05) for x in a)
        thr = round(q(0.05), 2)
    tag = ("✅ 保留  %s%s" % ("≤" if kind == "max" else "≥", thr)) if ok else "❌ 剔除（区分不开）"
    if ok:
        keep[name] = (kind, thr)
    print("%-14s%8.2f%8.2f%8.2f%8.2f | %7.2f%7.2f%7.2f | %s" %
          (name, q(0.05), st.median(v), q(0.95), v[-1], a[0], a[1], a[2], tag))

print("\n终选保留 %d 项：" % len(keep))
for k, (kind, thr) in keep.items():
    print("   %-14s %s%s" % (k, "≤" if kind == "max" else "≥", thr))
json.dump({k: {"kind": v[0], "thr": v[1]} for k, v in keep.items()},
          open(os.path.join(H, "final_thresholds.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

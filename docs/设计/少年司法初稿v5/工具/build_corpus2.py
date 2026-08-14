# -*- coding: utf-8 -*-
"""语料 v2：修掉 OCR 插空格、繁体混入、跨行断句三个污染源。

产出（均为「一篇一行」的纯正文，行内无换行）：
  R1_三大刊.txt            《中国社会科学》(法学篇)+《中国法学》+《法学研究》
  R1b_三大刊加中外法学.txt
  R1n_三大刊近十年.txt      上者中 year>=2014
  R2_少年司法真人.txt       库内少年司法主题真人论文（简体、非三大刊）
  A_AI稿.txt               本项目 AI 生成的 7 份稿
"""
import json, os, re, sys, glob
sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
EX = r"D:\PaperPiggy\data\extracted"
OUT = os.path.join(HERE, "corpus")
os.makedirs(OUT, exist_ok=True)
os.makedirs(os.path.join(OUT, "samples"), exist_ok=True)

rows = json.load(open(os.path.join(HERE, "corpus_index.json"), encoding="utf-8"))

CSS_NONLAW = {"822QP5P3", "W76ZYVVH", "VKVBQWSM", "J5AQZNCK", "3K7BCMHV",
              "492SAXKB", "V5UXXRTT", "4I7ZNSWZ", "FMR4R73F", "4RSHIGUL"}
TOP3 = {"中国社会科学", "中国法学", "法学研究"}
TOP4 = TOP3 | {"中外法学"}
JJ_PAT = re.compile(r"未成年|少年|低龄|罪错|附条件不起诉|专门矫治|收容教养|监护|儿童|校园")

# 只在繁体中出现的常用字（简体文本里几乎不会有）
TRAD = set("為與這個們對應該實現關於學說當時經濟權濟灣區證據個並與眾實體處罰調查機構觀護")
TRAD_ONLY = set("為與這們對實現關學說當經濟權灣區證據並眾體處罰調機觀護會來發國")

CJK = r"\u4e00-\u9fff"
FN_LINE = re.compile(r"^\s*(〔\s*\d+\s*〕|\[\s*\d+\s*\]|［\d+］|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]|\(\s*\d+\s*\))\s*")
CITE_HINT = re.compile(r"(参见|載《|载《|译，|譯，|出版社|第\s*\d+\s*页|第\s*\d+\s*頁|\bpp?\.\s*\d+|University Press|ed\.|Vol\.)")


def load_pages(key):
    p = os.path.join(EX, key + ".json")
    if not os.path.exists(p):
        return []
    d = json.load(open(p, encoding="utf-8"))
    return [(pg.get("text") or "") for pg in d.get("pages") or []]


def clean_doc(pages):
    lines = []
    for txt in pages:
        for ln in txt.splitlines():
            s = ln.strip()
            if not s or len(s) <= 3:
                continue
            if re.fullmatch(r"[\-—·•\.\d\s]+", s):
                continue
            if FN_LINE.match(s):
                continue
            if CITE_HINT.search(s) and len(s) < 160:
                continue
            latin = len(re.findall(r"[A-Za-z]", s))
            if latin > len(s) * 0.4:
                continue
            lines.append(s)
    t = "\n".join(lines)
    # ① 去掉 CJK/标点之间的空白（OCR 插空格），保留 ASCII 词间空格
    t = re.sub(r"(?<=[%s\u3000-\u303f\uff00-\uffef])[ \t]+(?=[%s\u3000-\u303f\uff00-\uffef])" % (CJK, CJK), "", t)
    # ② 跨行拼接：行尾不是句末标点就直接接上（PDF 的硬换行）
    t = re.sub(r"\n+", "", t)
    return t


def trad_ratio(t):
    if not t:
        return 1.0
    han = [c for c in t if "\u4e00" <= c <= "\u9fff"]
    if len(han) < 200:
        return 1.0
    return sum(1 for c in han if c in TRAD_ONLY) / len(han)


def build(name, keys, min_len=3000):
    kept, docs = [], []
    skipped_trad = 0
    for k in keys:
        t = clean_doc(load_pages(k))
        if len(t) < min_len:
            continue
        if trad_ratio(t) > 0.012:      # 繁体篇目剔除
            skipped_trad += 1
            continue
        docs.append(t)
        kept.append(k)
    open(os.path.join(OUT, name + ".txt"), "w", encoding="utf-8").write("\n".join(docs))
    print("%-22s %3d 篇 %9d 字 （剔繁体 %d 篇）" % (name, len(kept), sum(map(len, docs)), skipped_trad))
    return kept


r1 = [r["key"] for r in rows if r["journal"].strip() in TOP3 and r["key"] not in CSS_NONLAW and r["nchar"] > 5000]
r1b = [r["key"] for r in rows if r["journal"].strip() in TOP4 and r["key"] not in CSS_NONLAW and r["nchar"] > 5000]
r1n = [r["key"] for r in rows if r["journal"].strip() in TOP4 and r["key"] not in CSS_NONLAW
       and r["nchar"] > 5000 and (r["year"] or "0").isdigit() and int(r["year"] or "0") >= 2014]
r2 = [r["key"] for r in rows if JJ_PAT.search(r["title"]) and r["nchar"] > 5000 and r["key"] not in set(r1b)]

build("R1_三大刊", r1)
build("R1b_三大刊加中外法学", r1b)
build("R1n_三大刊近十年", r1n)
build("R2_少年司法真人", r2)

# ---- AI 稿 ----
ai_files = [os.path.join(HERE, "draft_A.txt"), os.path.join(HERE, "draft_B.txt")]
ai_files += sorted(glob.glob(os.path.join(r"D:\PaperPiggy\0_Agent交付物\核准追诉平行稿", "正文_第*.md")))
docs = []
for f in ai_files:
    t = open(f, encoding="utf-8", errors="ignore").read()
    t = re.sub(r"\[\^\d+\]", "", t)
    t = re.sub(r"(?m)^\[\^\d+\]:.*$", "", t)
    t = re.sub(r"(?m)^\s*[>#|].*$", "", t)
    t = re.sub(r"[*`_]", "", t)
    t = re.sub(r"\n+", "", t)
    docs.append(t)
open(os.path.join(OUT, "A_AI稿.txt"), "w", encoding="utf-8").write("\n".join(docs))
print("%-22s %3d 篇 %9d 字" % ("A_AI稿", len(docs), sum(map(len, docs))))

SAMPLES = {
    "GXIRWF87": "王颖-少年刑事司法基本原则之重构-中国法学2026",
    "LIC6NDQD": "姚建龙-少年司法社会调查程序-法学研究2026",
    "XDVR96NW": "姚建龙-不教而刑-中外法学2023",
    "YBBABVE5": "姚建龙-未成年人违警行为-中国法学2022",
    "52QG96AV": "姚莉-轻微犯罪记录封存-法学研究2025",
    "I94VN74H": "何挺-附条件不起诉实施状况-法学研究2019",
    "Y2PS87KX": "董坤-刑事诉讼办案期限-法学研究2024",
    "FDSEGY2J": "汪海燕-认罪认罚的撤回-法学研究2020",
    "CEZQR6FM": "郭烁-羁押人口率-中外法学2024",
    "3NT78PBE": "龙宗智-立法原意何处寻-中国法学2021",
    "SXMGK5FQ": "陈光中-逮捕与羁押制度改革-中国法学2023",
    "UBBSPRAD": "叶小琴-未成年人保护立法-中外法学2022",
}
for k, nm in SAMPLES.items():
    t = clean_doc(load_pages(k))
    if t:
        # 样本给人读，按句号断行更好读
        t = re.sub(r"([。？！])", r"\1\n", t)
        open(os.path.join(OUT, "samples", "%s_%s.txt" % (k, nm)), "w", encoding="utf-8").write(t)
print("样本 %d 篇已刷新" % len(SAMPLES))

# -*- coding: utf-8 -*-
"""逐稿拆分 + 口语词核查 + 段落形态。"""
import os, re, sys, glob, statistics as st
sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
C = os.path.join(HERE, "corpus")
R1 = open(os.path.join(C, "R1b_三大刊加中外法学.txt"), encoding="utf-8").read()
R2 = open(os.path.join(C, "R2_少年司法真人.txt"), encoding="utf-8").read()

def clean_md(t):
    t = re.sub(r"\[\^\d+\]", "", t)
    t = re.sub(r"(?m)^\[\^\d+\]:.*$", "", t)
    t = re.sub(r"(?m)^\s*[>#|].*$", "", t)
    return re.sub(r"[*`_]", "", t)

files = [("A稿(无手册)", open(os.path.join(HERE, "draft_A.txt"), encoding="utf-8").read()),
         ("B稿(用手册)", open(os.path.join(HERE, "draft_B.txt"), encoding="utf-8").read())]
par = r"D:\PaperPiggy\0_Agent交付物\核准追诉平行稿"
pf = sorted(glob.glob(os.path.join(par, "正文_第*.md")))
files.append(("平行稿(6章合)", clean_md("".join(open(f, encoding="utf-8").read() for f in pf))))
files += [("三大刊基准", R1), ("少司真人基准", R2)]

def r(t, pat):
    return len(re.findall(pat, t)) / len(t) * 10000

CHECKS = [
    ("口语词表(工作流§9禁用)", r"眼下|手上|怎么办|凭什么|算不上|用得很广|一回事|这件事|反过来|一大堆|干脆|只不过|说到底"),
    ("口语疑问式(有没有等)", r"有没有|够不够|存不存在|是不是|能不能|行不行"),
    ("动补否定(堵不上等)", r"[堵配靠说用抓管顶撑扛]得?不[上住清起来了通过]"),
    ("「家」作机关量词", r"[一两三四五几]家(机关|单位|法院|检察院|部门)"),
    ("术语泄漏(落位/闸门/选边)", r"落位|闸门|选边|工序|交付名词|骨架卡"),
    ("本节/本章/本文", r"本[节章文]"),
    ("有论述/有观点", r"有论述|有观点认为"),
    ("例如/比如/又如", r"例如|比如|又如|再如|譬如"),
    ("笔者/我们", r"笔者|我们"),
    ("但是/然而/虽然", r"但是|然而|虽然|尽管"),
    ("问号？", r"？"),
    ("破折号——", r"——"),
    ("分号；", r"；"),
    ("冒号：", r"："),
    ("「」非陆式引号", r"「"),
    ("空注码〔〕", r"〔\s*〕"),
    ("直接引语“…”", r"“[^”]{4,}”"),
    ("应当/不得(法条腔)", r"应当|不得"),
    ("这/该(指代)", r"[这该]一?[一-鿿]"),
    ("三项并列(A、B、C)", r"[一-鿿]{2,6}、[一-鿿]{2,6}、[一-鿿]{2,6}"),
]

print("%-26s" % "指标（每万字）" + "".join("%13s" % n for n, _ in files))
print("-" * (26 + 13 * len(files)))
for name, pat in CHECKS:
    print("%-26s" % name + "".join("%13.2f" % r(t, pat) for _, t in files))

print("\n" + "=" * 100)
print("句段形态")
print("=" * 100)
SENT = re.compile(r"[^。？！]+[。？！]")
print("%-14s %7s %7s %7s %7s %8s %8s" % ("语料", "句数", "均句长", "中位", "短句%", "长句%", "每句逗号"))
for n, t in files:
    ss = [s for s in SENT.findall(t) if len(s) >= 6]
    lens = [len(s) for s in ss]
    print("%-14s %7d %7.1f %7d %6.1f%% %7.1f%% %8.2f" %
          (n, len(ss), st.mean(lens), st.median(lens),
           100 * sum(1 for x in lens if x < 20) / len(lens),
           100 * sum(1 for x in lens if x > 80) / len(lens),
           st.mean([s.count("，") for s in ss])))

print("\n段落长度（只对有段落结构的文件有意义）")
for n, t in files[:3]:
    ps = [p for p in t.split("\n") if len(p) > 30]
    if ps:
        L = [len(p) for p in ps]
        print("%-14s 段数 %4d | 均 %5.0f 字 | 中位 %4d | 最长 %5d" % (n, len(ps), st.mean(L), st.median(L), max(L)))

print("\n" + "=" * 100)
print("AI 稿里「有论述…」的全部实例（看它变成了什么口头禅）")
print("=" * 100)
allai = "".join(t for _, t in files[:3])
seen = {}
for m in re.finditer(r"[^。；\n]{0,10}有论述[^。；\n]{0,26}", allai):
    s = m.group(0).strip()
    seen[s] = seen.get(s, 0) + 1
for s, k in sorted(seen.items(), key=lambda kv: -kv[1])[:40]:
    print("  %dx  %s" % (k, s))

# -*- coding: utf-8 -*-
"""从三大刊原文里抽标题树，检验现行工作流的结构硬指标在三大刊上成不成立。"""
import json, os, re, sys, collections, statistics as st
sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
EX = r"D:\PaperPiggy\data\extracted"
rows = json.load(open(os.path.join(HERE, "corpus_index.json"), encoding="utf-8"))
CSS_NONLAW = {"822QP5P3","W76ZYVVH","VKVBQWSM","J5AQZNCK","3K7BCMHV","492SAXKB",
              "V5UXXRTT","4I7ZNSWZ","FMR4R73F","4RSHIGUL"}
TOP4 = {"中国社会科学","中国法学","法学研究","中外法学"}

H1 = re.compile(r"^[（(]?([一二三四五六七八九十]{1,3})[）)]?[、\.．]\s*(.{2,34})$")
H2 = re.compile(r"^[（(]([一二三四五六七八九十]{1,3})[）)]\s*(.{2,34})$")
NOISE = re.compile(r"页|载《|参见|注释|摘要|关键词|Abstract|作者|收稿")

def headings(key):
    p = os.path.join(EX, key + ".json")
    if not os.path.exists(p):
        return [], []
    d = json.load(open(p, encoding="utf-8"))
    l1, l2 = [], []
    for pg in d.get("pages") or []:
        for ln in (pg.get("text") or "").splitlines():
            s = re.sub(r"\s+", "", ln.strip())
            if not s or len(s) > 40 or NOISE.search(s):
                continue
            m2 = H2.match(s)
            if m2:
                l2.append((m2.group(1), m2.group(2)))
                continue
            m1 = H1.match(s)
            if m1 and not s.startswith("("):
                l1.append((m1.group(1), m1.group(2)))
    return l1, l2

NUM = "一二三四五六七八九十"
def seq_ok(items):
    """只保留从「一」开始的递增序列，过滤掉正文里的『一、二、』枚举噪声。"""
    want, out = 0, []
    for n, t in items:
        if n == NUM[want] if want < 10 else False:
            out.append(t); want += 1
    return out

docs = [r for r in rows if r["journal"].strip() in TOP4 and r["key"] not in CSS_NONLAW and r["nchar"] > 8000]
stat = []
print("== 三大刊+中外法学 逐篇一级标题树（自动抽取，可能有漏） ==\n")
for r in sorted(docs, key=lambda x: (x["journal"], x["year"])):
    l1, l2 = headings(r["key"])
    t1 = seq_ok(l1)
    if len(t1) < 3:
        continue
    stat.append((r, t1, l2))
    print("【%s %s】%s（%s）" % (r["journal"], r["year"], r["title"][:34], r["author"][:8]))
    print("   " + " ／ ".join("%d.%s" % (i + 1, t) for i, t in enumerate(t1)))

print("\n\n" + "=" * 90)
print("结构统计（n=%d 篇，仅统计抽取成功者）" % len(stat))
print("=" * 90)
n1 = [len(t1) for _, t1, _ in stat]
print("一级单元数：均 %.1f  中位 %d  分布 %s" % (st.mean(n1), st.median(n1), dict(sorted(collections.Counter(n1).items()))))
print("4—8 个之内的占比：%.0f%%" % (100 * sum(1 for x in n1 if 4 <= x <= 8) / len(n1)))
titles = [t for _, t1, _ in stat for t in t1]
tl = [len(t) for t in titles]
print("一级标题字数：均 %.1f  中位 %d  最长 %d  ≥15字占比 %.0f%%" %
      (st.mean(tl), st.median(tl), max(tl), 100 * sum(1 for x in tl if x >= 15) / len(tl)))
print("首章叫「问题的提出/引言/导论」的占比：%.0f%%" %
      (100 * sum(1 for _, t1, _ in stat if re.search(r"问题的提出|引言|导论|绪论|问题与", t1[0])) / len(stat)))
末 = [t1[-1] for _, t1, _ in stat]
print("末章形态：", dict(collections.Counter(
    ["结论" if "结论" in t else "余论" if "余论" in t else "结语/代结语" if "结语" in t else
     "展望/前景" if re.search(r"展望|前景|走向", t) else "实质章(无结论章)" for t in 末]).most_common()))
print("\n一级标题的收尾词 top20：", collections.Counter([t[-2:] for t in titles]).most_common(20))
print("\n标题里含判断动词（应当/须/不/是/论/构建/重塑）的占比：%.0f%%" %
      (100 * sum(1 for t in titles if re.search(r"应当|必须|不[能得应]|是|重构|重塑|构建|走向|转向|反思|批判", t)) / len(titles)))

# -*- coding: utf-8 -*-
"""对照分析 v2：把统计放到真正承载文风的位置上——句首、句末、连接词、句法与标点指标。
   题材词不进句首/句末榜（句首多为话语标记），因此不必再做题材过滤。"""
import os, re, sys, collections, json
sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
C = os.path.join(HERE, "corpus")
load = lambda n: open(os.path.join(C, n), encoding="utf-8").read()

A  = load("A_AI稿.txt")
R1 = load("R1b_三大刊加中外法学.txt")
R2 = load("R2_少年司法真人.txt")
LA, L1, L2 = len(A), len(R1), len(R2)

SENT_SPLIT = re.compile(r"(?<=[。？！])")

def sentences(t):
    out = []
    for para in t.split("\n"):
        for s in SENT_SPLIT.split(para):
            s = s.strip()
            if len(s) >= 6:
                out.append(s)
    return out

SA, S1, S2 = sentences(A), sentences(R1), sentences(R2)
print("句数：AI %d / 三大刊 %d / 少司真人 %d" % (len(SA), len(S1), len(S2)))
print("字数：AI %d / 三大刊 %d / 少司真人 %d\n" % (LA, L1, L2))

def head_counter(sents, n):
    c = collections.Counter()
    for s in sents:
        h = re.sub(r"^[「『“\"（(《\[〔]+", "", s)[:n]
        if len(h) == n and re.fullmatch(r"[一-鿿]+", h):
            c[h] += 1
    return c

def tail_counter(sents, n):
    c = collections.Counter()
    for s in sents:
        t = re.sub(r"[。？！]+$", "", s)[-n:]
        if len(t) == n and re.fullmatch(r"[一-鿿]+", t):
            c[t] += 1
    return c

def show(title, counters, totals, minA=4, ratio=2.5, top=60, reverse=False):
    ca, c1, c2 = counters
    ta, t1, t2 = totals
    rows = []
    if not reverse:
        for g, k in ca.items():
            if k < minA:
                continue
            ra, r1, r2 = k / ta * 1000, c1.get(g, 0) / t1 * 1000, c2.get(g, 0) / t2 * 1000
            if ra >= max(r1, r2) * ratio:
                rows.append((g, k, ra, r1, r2, c1.get(g, 0), c2.get(g, 0)))
        rows.sort(key=lambda x: -x[2])
    else:
        for g, k in c1.items():
            r1 = k / t1 * 1000
            if r1 < 0.8:
                continue
            ra, r2 = ca.get(g, 0) / ta * 1000, c2.get(g, 0) / t2 * 1000
            if ra * ratio <= r1 and r2 >= r1 * 0.35:
                rows.append((g, ca.get(g, 0), ra, r1, r2, k, c2.get(g, 0)))
        rows.sort(key=lambda x: -x[3])
    print("=" * 96)
    print(title)
    print("=" * 96)
    print("%-14s %5s %8s %8s %8s   %s" % ("字串", "AI次", "AI‰", "三大刊‰", "真人‰", "真刊/真人原始次数"))
    for g, k, ra, r1, r2, n1, n2 in rows[:top]:
        print("%-14s %5d %8.2f %8.2f %8.2f   %d/%d" % (g, k, ra, r1, r2, n1, n2))
    print("（‰ = 每千句出现次数）共 %d 条\n" % len(rows))
    return rows

out = {}
for n in (2, 3, 4, 5):
    r = show("① 句首 %d 字：AI 明显偏爱（每千句频率 ≥ 真刊与真人的 2.5 倍）" % n,
             (head_counter(SA, n), head_counter(S1, n), head_counter(S2, n)),
             (len(SA), len(S1), len(S2)))
    out["head_over_%d" % n] = r

for n in (2, 3, 4):
    r = show("② 句首 %d 字：真刊常用而 AI 几乎不用" % n,
             (head_counter(SA, n), head_counter(S1, n), head_counter(S2, n)),
             (len(SA), len(S1), len(S2)), ratio=2.5, top=45, reverse=True)
    out["head_under_%d" % n] = r

for n in (3, 4):
    r = show("③ 句末 %d 字：AI 明显偏爱" % n,
             (tail_counter(SA, n), tail_counter(S1, n), tail_counter(S2, n)),
             (len(SA), len(S1), len(S2)))
    out["tail_over_%d" % n] = r
    r = show("④ 句末 %d 字：真刊常用而 AI 不用" % n,
             (tail_counter(SA, n), tail_counter(S1, n), tail_counter(S2, n)),
             (len(SA), len(S1), len(S2)), top=40, reverse=True)
    out["tail_under_%d" % n] = r

json.dump(out, open(os.path.join(HERE, "contrast2_result.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

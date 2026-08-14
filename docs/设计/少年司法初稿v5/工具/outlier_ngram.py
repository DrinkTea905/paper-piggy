# -*- coding: utf-8 -*-
"""表外扫描 · 自动找出「AI 稿超过全部真刊」的表达。

v4 的教训：固定且公开的禁区表会被优化到全绿，AI 味迁移到表外（丙稿五项统计型
禁区全过，而「恰恰」5.79/万字超过 106 篇真刊的全部，「……所做的，是」在真刊
106 篇里一次都没出现）。所以禁区表不能靠人想，必须每轮试跑后重新扫。

三层对照：
  L1 三大刊 106 篇（R1b）   —— 文体基准
  L2 少年司法真人 144 篇（R2）—— 题材基准，用来把「题材词」与「AI 味」分开
  AI 稿                     —— 被测对象

判据：AI 频率 > L1 每篇最大值，且 > L2 每篇最大值 → 强候选禁语（既不是三大刊
的写法，也不是本领域真人的写法）。只超 L1 不超 L2 的，多半是题材或刊物差异。

用法：
  python outlier_ngram.py <AI稿.txt> [更多稿.txt ...] [--n 2,3,4,5] [--min 3] [--top 60]
"""
import sys, io, os, re, json, argparse, collections

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
CORP = os.path.join(HERE, "corpus")
L1 = os.path.join(CORP, "R1b_三大刊加中外法学.txt")
L2 = os.path.join(CORP, "R2_少年司法真人.txt")

HAN = re.compile(r"[一-鿿]")
# 只在「纯汉字连续段」内取 n-gram，不跨标点、不跨数字与拉丁字母
SEG = re.compile(r"[一-鿿]{2,}")

# 题材字：含这些字的 gram 由题目决定，不是文风。B 组据此剔除。
# 少年司法用这一套；换领域时用 --topic 覆盖。
TOPIC_DEFAULT = ("核准追诉嫌疑侦查逮捕拘留羁押强制措施未成年少年儿童监护矫治教育专门学校"
                 "法院检察公安司法机关条款项第规定规则解释刑事诉讼程序犯罪责任年龄"
                 "被害人证据讯问询问审查起诉立案分流转处保护处分观护帮教附条件不起诉"
                 "日本德国美国台湾地区法典条约公约白皮书")
TOPIC = None  # 在 main() 里按 --topic 编译


def han_len(t):
    return len(HAN.findall(t))


def grams(t, n):
    out = collections.Counter()
    for s in SEG.findall(t):
        for i in range(len(s) - n + 1):
            out[s[i:i + n]] += 1
    return out


def load_docs(path):
    return [l for l in open(path, encoding="utf-8").read().split("\n") if han_len(l) > 3000]


def corpus_profile(docs, ns, cand):
    """只统计候选 gram（来自被测稿），避免在 500 万字语料上展开全部 n-gram。

    返回 {gram: (max_rate, median_rate, n_docs_hit)}，逐篇算率再取统计量。
    """
    hits = collections.defaultdict(list)   # gram -> [rate, ...]，只记出现过的篇
    for d in docs:
        L = han_len(d)
        if not L:
            continue
        cnt = collections.Counter()
        for s in SEG.findall(d):
            for n in ns:
                for i in range(len(s) - n + 1):
                    g = s[i:i + n]
                    if g in cand:
                        cnt[g] += 1
        for g, c in cnt.items():
            hits[g].append(c * 10000.0 / L)
    prof = {}
    for g, rates in hits.items():
        rates.sort()
        prof[g] = (rates[-1], rates[len(rates) // 2], len(rates))
    return prof


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("targets", nargs="+")
    ap.add_argument("--n", default="3,4,5")
    ap.add_argument("--min", type=int, default=3, help="AI 稿中至少出现几次")
    ap.add_argument("--top", type=int, default=60)
    ap.add_argument("--common", type=int, default=5, help="A 组门槛：该表达至少见于几篇真刊")
    ap.add_argument("--topic", default=TOPIC_DEFAULT, help="题材字集，含之者不进 B 组")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    ns = [int(x) for x in a.n.split(",")]
    global TOPIC
    TOPIC = re.compile("[%s]" % re.escape(a.topic))

    # ① 先从被测稿收集候选 gram（只有它们需要在语料上统计）
    texts = {}
    cand = set()
    for tgt in a.targets:
        t = open(tgt, encoding="utf-8").read()
        texts[tgt] = t
        for n in ns:
            for g, c in grams(t, n).items():
                if c >= a.min:
                    cand.add(g)
    print("候选表达 %d 条（来自 %d 份被测稿）" % (len(cand), len(a.targets)))

    print("载入语料…")
    d1, d2 = load_docs(L1), load_docs(L2)
    print("L1 三大刊 %d 篇 / L2 少年司法真人 %d 篇" % (len(d1), len(d2)))
    p1 = corpus_profile(d1, ns, cand)
    p2 = corpus_profile(d2, ns, cand)

    report = {}
    for tgt in a.targets:
        t = texts[tgt]
        L = han_len(t)
        rows = []
        for n in ns:
            for gram, c in grams(t, n).items():
                if c < a.min:
                    continue
                r = c * 10000.0 / L
                m1, med1, h1 = p1.get(gram, (0.0, 0.0, 0))
                m2, med2, h2 = p2.get(gram, (0.0, 0.0, 0))
                if r > m1 and r > m2:
                    rows.append({
                        "gram": gram, "n": n, "count": c, "rate": round(r, 2),
                        "L1_max": round(m1, 2), "L1_docs": h1,
                        "L2_max": round(m2, 2), "L2_docs": h2,
                        "excess": round(r / max(m1, m2, 0.01), 1),
                    })
        # 去掉被更长 gram 完全覆盖的短 gram（次数相同即视为同一现象）
        rows.sort(key=lambda x: -x["n"])
        keep = []
        for r in rows:
            if any(r["gram"] in k["gram"] and r["count"] <= k["count"] for k in keep):
                continue
            keep.append(r)
        keep.sort(key=lambda x: (-x["excess"], -x["count"]))
        # 分两组：A＝真刊常用而本稿超量（最可靠）；B＝真刊零出现的句式（须剔题材词）
        A = [r for r in keep if r["L1_docs"] >= a.common]
        B = [r for r in keep if r["L1_docs"] == 0 and r["L2_docs"] == 0
             and not TOPIC.search(r["gram"])]
        A.sort(key=lambda x: -x["rate"] / max(x["L1_max"], 0.01))
        B.sort(key=lambda x: -x["count"])
        report[os.path.basename(tgt)] = {"A": A, "B": B, "all": keep}
        print("\n" + "=" * 78)
        print("%s（%d 汉字）  超出全部真刊：共 %d 条 → A 组 %d 条 / B 组 %d 条"
              % (os.path.basename(tgt), L, len(keep), len(A), len(B)))
        for tag, rows2 in (("A｜真刊常用而本稿超量（见于≥%d 篇真刊）" % a.common, A),
                           ("B｜真刊零出现的句式（已剔题材词）", B)):
            print("\n  --- %s ---" % tag)
            print("  %-14s %4s %7s %8s %6s %8s %6s" % ("表达", "次数", "本稿/万", "三大刊max", "见篇", "少司max", "见篇"))
            for r in rows2[:a.top]:
                print("  %-14s %4d %7.2f %8.2f %6d %8.2f %6d"
                      % (r["gram"], r["count"], r["rate"], r["L1_max"], r["L1_docs"],
                         r["L2_max"], r["L2_docs"]))
    if a.json:
        json.dump(report, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n已写出", a.json)


if __name__ == "__main__":
    main()

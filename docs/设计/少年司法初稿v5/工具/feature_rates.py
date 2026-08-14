# -*- coding: utf-8 -*-
"""逐篇特征率对照：给定一组正则，报三大刊 106 篇与少年司法真人 143 篇的**逐篇**分布，
并把被测稿放进去比。

与 v4 的 contrast*.py 的区别：v4 有几次是在**合计语料**上算频率的，那会把方差洗掉
（106 篇真刊只有 1 篇能全过 v4 初版阈值，就是这么来的）。本脚本一律逐篇算率再取分位。

另附 --show 模式：把某个表达在真刊里的真实上下文抄出来给人看——
凡零结果或极低命中，必须人工看一眼原文再下结论（本轮踩过：查「回到原点」时正则漏了
「了」字，原文是"回到了原点"，差点据此判定 v4 全错）。

用法：
  python feature_rates.py 稿1.txt 稿2.txt                 # 用内置特征表
  python feature_rates.py --show "回到.{0,3}原点"          # 看真刊原文用例
"""
import re, io, sys, os, argparse, json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
CORP = os.path.join(HERE, "corpus")
L1P = os.path.join(CORP, "R1b_三大刊加中外法学.txt")
L2P = os.path.join(CORP, "R2_少年司法真人.txt")

# 内置特征表。分三类：
#   meta  元叙述（把研究过程写进正文）
#   anon  匿名指称（用户"正文不具名"规则的副作用）
#   move  论证动作与句式习惯
FEATURES = [
    ("meta", "行文至此", r"行文至此"),
    ("meta", "回到…原点", r"回到.{0,3}原点"),
    ("meta", "悬而未决", r"悬而未决"),
    ("meta", "应当/须/需要说明的是", r"应当说明的是|须说明的是|需要说明的是|有必要说明的是"),
    ("meta", "须作N点限缩", r"须作[一二三两]点|作[两三]点限|须作如下限"),
    ("meta", "本文并不主张", r"本文并不主张|并不需要主张|本文无意主张"),
    ("meta", "以下的讨论/本文以下", r"以下的讨论|以下讨论|本文以下"),
    ("meta", "综上", r"综上"),
    ("anon", "有意见/有论述/另有意见", r"有意见指出|另有意见|有论述|有一种意见认为|一类主张"),
    ("anon", "笔者", r"笔者"),
    ("anon", "本文", r"本文"),
    ("move", "恰恰", r"恰恰"),
    ("move", "值得注意/值得一提", r"值得注意|值得一提|尤其值得"),
    ("move", "换言之/也就是说", r"换言之|也就是说|易言之"),
    ("move", "所设的", r"所设的"),
    ("move", "所做的，是", r"所做的[，,]是"),
    ("move", "不是A而是B", r"不是[^。，、]{2,12}，而是|并非[^。，、]{2,12}，而是"),
    ("move", "问题在于/关键在于", r"问题在于|关键在于"),
    ("move", "例如/比如/又如", r"例如|比如|又如|再如"),
    ("move", "但是/然而/虽然/尽管/而且", r"但是|然而|虽然|尽管|而且"),
]


def han(t):
    return len(re.findall(r"[一-鿿]", t))


def docs(p):
    return [l for l in open(p, encoding="utf-8").read().split("\n") if han(l) > 3000]


def rate(t, p):
    return len(re.findall(p, t)) * 10000.0 / max(han(t), 1)


def q(vals, f):
    return vals[min(int(len(vals) * f), len(vals) - 1)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("targets", nargs="*")
    ap.add_argument("--show", default="", help="把某正则在三大刊里的真实用例抄出来")
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    L1, L2 = docs(L1P), docs(L2P)
    if a.show:
        hit = [d for d in L1 if re.search(a.show, d)]
        print("三大刊 %d/%d 篇命中 /%s/" % (len(hit), len(L1), a.show))
        for d in hit:
            for m in re.finditer(r".{35}" + a.show + r".{35}", d):
                print("   …", m.group(0))
        return

    names = [os.path.basename(t) for t in a.targets]
    texts = [open(t, encoding="utf-8").read() for t in a.targets]
    out = []
    print("%-5s %-24s %7s %7s %7s %7s %7s | %s"
          % ("类", "项", "L1中位", "L1p95", "L1max", "L2中位", "L2max", " ".join("%.8s" % n for n in names)))
    for cat, name, pat in FEATURES:
        a1 = sorted(rate(d, pat) for d in L1)
        a2 = sorted(rate(d, pat) for d in L2)
        rs = [rate(t, pat) for t in texts]
        ndoc1 = sum(1 for d in L1 if re.search(pat, d))
        out.append({"cat": cat, "name": name, "pat": pat,
                    "L1_median": q(a1, .5), "L1_p95": q(a1, .95), "L1_max": a1[-1],
                    "L1_docs_hit": ndoc1, "L1_docs_total": len(L1),
                    "L2_median": q(a2, .5), "L2_max": a2[-1],
                    "targets": dict(zip(names, rs))})
        print("%-5s %-24s %7.2f %7.2f %7.2f %7.2f %7.2f | %s"
              % (cat, name, q(a1, .5), q(a1, .95), a1[-1], q(a2, .5), a2[-1],
                 " ".join("%8.2f" % r for r in rs)))
    print("\nL1＝三大刊加中外法学 %d 篇；L2＝少年司法真人 %d 篇。频率单位：次/万汉字，逐篇计算后取分位。" % (len(L1), len(L2)))
    print("★ 判读纪律：某项在 L1 的命中篇数比例低于 10% 的，一律不得写成推荐动作，最多进「武器库·罕用」。")
    if a.json:
        json.dump(out, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("已写出", a.json)


if __name__ == "__main__":
    main()

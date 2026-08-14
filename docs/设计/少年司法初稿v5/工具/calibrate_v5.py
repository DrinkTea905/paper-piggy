# -*- coding: utf-8 -*-
"""阶段4 · 阈值校准（逐篇，不用合计口径）。

两条硬要求（用户定）：
  ① 凡定阈值，必须在【单篇】真刊上逐篇校准并报误伤率，>10% 即阈值错。
     合计语料会把方差洗掉——v4 初版按合计定阈值，逐篇回测时 106 篇真刊只有 1 篇能全过。
  ② 同时报「AI 四稿是否全部落在真刊分布之外」——只有两头都满足才算能区分。

阈值策略：一律取 **真刊单篇最大值**（误伤率天然为 0），再看 AI 四稿是否全部超过它。
  · 四稿全超 → 该项能区分，进禁区表
  · 部分超   → 只报数，不进禁区表（它测的是这一稿的习惯，不是 AI 与真人的分界）
  · 无一超   → 撤销，别再提

口径：正文汉字数为分母，每万字计率。AI 稿用 docx 提取正文（不含脚注），与真刊口径一致。
"""
import re, io, sys, os, json, argparse, statistics as st

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
CORP = os.path.join(HERE, "corpus")
L1P = os.path.join(CORP, "R1b_三大刊加中外法学.txt")
L2P = os.path.join(CORP, "R2_少年司法真人.txt")

# 候选项。来源三处：v4 遗留的五项统计禁区（须复核）／阶段0表外扫描的新发现／病症清单的可数条。
CANDIDATES = [
    # ── v4 遗留，本轮复核 ──
    ("v4", "有论述（作主语）", r"有论述(指出|认为|主张|提出|强调)"),
    ("v4", "本节／本章自指", r"本(节|章)(的|中|里)?(讨论|分析|论证|将|先|旨在)"),
    ("v4", "这一＋抽象名词", r"这一(点|处|层|问题|判断|命题|结论|定位|安排|设计|做法|区分|理解|意义|限度|要求)"),
    ("v4", "凑三", r"三(点|项|个层次|个方面|重困难)"),
    ("v4", "本文", r"本文"),
    # ── 阶段0 表外扫描的新发现（v4 一条都没管到）──
    ("新", "恰恰", r"恰恰"),
    ("新", "值得注意／值得一提", r"值得(注意|一提|单独一提)"),
    ("新", "「X所做的，是Y」分裂句", r"所(做|设|要寻找|选择)的[，,]是"),
    ("新", "所设的", r"所设的"),
    ("新", "本文并不主张／并不需要主张", r"本文并不(需要)?主张"),
    ("新", "须作N点限缩", r"须作.{0,3}[一二两三四五]点限缩|作.{0,3}[一二两三四五]处限缩"),
    # ── 病症清单里的可数条 ──
    ("症", "作业类元叙述", r"(未能取得|未能查得|经查[^，。]{0,12}(均无|没有)|这并非检索|空白不在检索|检索不周)"),
    ("症", "元话语连接件", r"(需要|应当|还应当|此处可补)(说明|指出|补充|承认|注意)的?是"),
    ("症", "待核标记进正文", r"〔待核|待核：|定稿前须复核|定稿前宜"),
    ("症", "引言自报贡献", r"本文(所增加的|的贡献|的创新)|与既有研究的关系"),
    ("症", "破折号说理", r"——"),
]


def han(t):
    return len(re.findall(r"[一-鿿]", t))


def docs(path):
    return [l for l in open(path, encoding="utf-8").read().split("\n") if han(l) > 3000]


def rate(t, pat, n=None):
    n = n or han(t)
    return len(re.findall(pat, t)) * 10000.0 / max(n, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("targets", nargs="+", help="AI 稿 txt")
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    L1, L2 = docs(L1P), docs(L2P)
    tgts = [(os.path.basename(p).split(".")[0], open(p, encoding="utf-8").read()) for p in a.targets]
    print("真刊 L1 %d 篇 / L2 %d 篇 ｜ 被测稿 %d 份：%s\n"
          % (len(L1), len(L2), len(tgts), "、".join(n for n, _ in tgts)))

    rows = []
    hdr = "%-22s %6s %6s %6s %6s │ %s │ %-8s %s"
    print(hdr % ("项", "中位", "p95", "max", "零篇%", "  ".join("%6.6s" % n for n, _ in tgts), "阈值", "判定"))
    print("─" * 118)
    for src, name, pat in CANDIDATES:
        vs = sorted(rate(d, pat) for d in L1)
        zero = sum(1 for v in vs if v == 0) * 100.0 / len(vs)
        mx = vs[-1]
        p95 = vs[int(len(vs) * 0.95)]
        med = st.median(vs)
        mine = [rate(t, pat) for _, t in tgts]
        n_over = sum(1 for m in mine if m > mx)
        v2 = sorted(rate(d, pat) for d in L2)
        mx2 = v2[-1]
        # ★ 判定分两类，不可混为一谈（首版脚本混了，导致误判）：
        #   ① 真刊 106 篇 + 少司真人 143 篇**双双零出现** → 零阈值禁令。
        #      它是体例外的东西，与「AI 稿是否都犯」无关——丙稿「有论述」为 0，
        #      正是因为 v4 已经禁了它且禁令有效，这是禁令奏效的证据，不是指标失效的证据。
        #   ② 真刊有分布 → 只有 AI 四稿**全部**超过真刊单篇最大值，才算测到了 AI 与真人的分界。
        if mx == 0 and mx2 == 0:
            verdict, thr = "★零阈值禁令（249 篇真人文献零出现）", "0 处"
        elif mx == 0:
            verdict, thr = "禁令降级（L1 零出现但 L2 有：max %.2f）" % mx2, "—"
        elif n_over == len(mine):
            verdict, thr = "★上限（四稿全超真刊上限）", "≤%.2f" % mx
        elif n_over > 0:
            verdict, thr = "只报数（%d/%d 超）" % (n_over, len(mine)), "—"
        else:
            verdict, thr = "撤销（无一超真刊上限）", "—"
        # 误伤率：阈值取真刊 max，故恒为 0；另报「若取 p95 会误伤多少篇」供对照
        misfire_p95 = sum(1 for v in vs if v > p95) * 100.0 / len(vs)
        print(hdr % (name, "%.2f" % med, "%.2f" % p95, "%.2f" % mx, "%.0f" % zero,
                     "  ".join("%6.2f" % m for m in mine), thr, verdict))
        rows.append({"src": src, "name": name, "pattern": pat,
                     "median": round(med, 2), "p95": round(p95, 2), "max": round(mx, 2),
                     "l2_max": round(mx2, 2),
                     "zero_pct": round(zero, 1), "ai": [round(m, 2) for m in mine],
                     "threshold": thr, "verdict": verdict,
                     "misfire_at_max": 0.0, "misfire_at_p95": round(misfire_p95, 1)})

    print("\n【误伤率】阈值一律取真刊单篇最大值 → 真刊误伤率 0.0%（定义使然）。")
    print("　　　　　作为对照：若照 v4 的做法把阈值取在 p95，真刊误伤率就是 %.1f%%（每项 5%% 左右），"
          % st.mean([r["misfire_at_p95"] for r in rows]))
    print("　　　　　16 项叠加后一篇真刊全过的概率仅 %.1f%%——这正是 v4 初版「106 篇只有 1 篇全过」的算法来源。"
          % (100 * (0.95 ** len(rows))))
    n_ok = sum(1 for r in rows if r["verdict"].startswith("★"))
    print("\n【分类】零阈值禁令 %d 项｜上限 %d 项｜只报数 %d 项｜撤销 %d 项"
          % (sum(1 for r in rows if "零阈值" in r["verdict"]),
             sum(1 for r in rows if r["verdict"].startswith("★上限")),
             sum(1 for r in rows if "只报数" in r["verdict"]),
             sum(1 for r in rows if "撤销" in r["verdict"] or "降级" in r["verdict"])))
    print("\n【结论】%d 项候选里，只有 %d 项四稿全部落在真刊分布之外，够格进禁区表；"
          % (len(rows), n_ok))
    print("　　　　其余 %d 项只报数或撤销——**它们测的是这一稿的习惯，不是 AI 与真人的分界**。" % (len(rows) - n_ok))
    if a.json:
        json.dump(rows, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n已写出", a.json)


if __name__ == "__main__":
    main()

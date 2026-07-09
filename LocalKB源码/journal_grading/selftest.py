# -*- coding: utf-8 -*-
"""
自检：覆盖方案 §六 的 4 个 worked example 与 §十 验收清单。
跑法（在 LocalKB源码 目录下）：  python journal_grading/selftest.py
全部通过 → 退出码 0；有失败 → 打印 FAIL 明细并退出码 1。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import loader
from resolver import resolve_journal_weight

DATA = loader.load()
_fail = []
_n = 0


def check(name, cond, detail=""):
    global _n
    _n += 1
    ok = bool(cond)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f"   << {detail}"))
    if not ok:
        _fail.append(name)


def approx(a, b, eps=1e-6):
    return a is not None and abs(a - b) <= eps


def R(item, disc, if_enabled=False):
    # 档位数值检查默认关 IF（确定性）；IF 单独测。生产由 config.ifEnabled 决定(现为开)。
    if isinstance(item, str):
        item = {"journal": item}
    return resolve_journal_weight(item, disc, data=DATA, if_enabled=if_enabled)


print("═══ §六 worked examples ═══")
r = R("中国法学", "law")
check("例1 《中国法学》→law→clsci.权威→T1→1.00",
      r["tier"] == "T1" and approx(r["weight"], 1.00), r)

r = R("Academy of Management Journal", "business")
check("例2 SSCI-Q1 管理刊→business→T1→1.00",
      r["tier"] == "T1" and approx(r["weight"], 1.00), r)

r = R("当代作家评论", "chinese_lit")
check("例3 CSSCI来源文学刊→chinese_lit→T2→0.85",
      r["tier"] == "T2" and approx(r["weight"], 0.85), r)

r = R("MATHEMATICAL PROBLEMS IN ENGINEERING", "law_personal")   # 真实中科院预警名单
check("例4改·预警刊不再压最低(已取消存疑档)→≥普通0.25、非最低",
      r["weight"] >= 0.25 and r["tier"] != "T6", r)

print("═══ §十 验收清单 ═══")

# 1) 期刊识别：ISSN 优先
r = R({"journal": "张冠李戴的错误刊名", "issn": "1003-1707"}, "law")
check("识别·ISSN优先（错名+对ISSN→中国法学 T1）",
      r["explain"]["identify"]["status"] == "issn" and r["tier"] == "T1", r)

# 归一化刊名回退（去《》）
r = R("《中国法学》", "law")
check("识别·刊名归一回退（去《》→精确命中）",
      r["explain"]["identify"]["status"] == "name" and r["tier"] == "T1", r)

# 模糊 ≥0.9 才自动采纳
r = R("Academy of Management Journals", "business")   # 尾字母多一个 → 相似度≈0.98
check("识别·模糊≥0.9自动采纳（needsReview 提示）",
      r["explain"]["identify"]["status"] == "fuzzy" and r["needsReview"] and approx(r["weight"], 1.00), r)

# 未命中标"待确认"而非静默给普通档
r = R("完全不存在的某期刊XYZ", "law")
check("识别·未命中→待确认（不静默给普通档）",
      r["tier"] == "待确认" and r["needsReview"] and r["explain"]["provisional"] is True, r)

# 2) priority-max：多目录命中不重复加分（当代作家评论 在 cssci.来源 且 pku.收录 → 只按 cssci 定档）
r = R("当代作家评论", "chinese_lit")
check("priority-max·cssci优先于pku，不重复计分",
      r["explain"]["priorityWinner"]["catalog"] == "cssci" and r["tier"] == "T2", r)

# 3) 纯人文档案不出现 SSCI/JIF 分区档位（配置层断言）
d_map = DATA.archetype("D_纯人文").get("map", {})
d_tokens = [t for toks in d_map.values() for t in toks]
check("纯人文·map 内无任何 ssci.* 分区档位",
      all(not t.startswith("ssci") for t in d_tokens), d_tokens)
# 纯人文一本外文人文刊 → 经 ahci/sjr 定档，绝不经 ssci（纯人文无 SSCI 分区档位）
# 注：规格 priorityOrder 里 sjr 在 ahci 之前，故同时命中两者时由 sjr 定档（spec 行为）。
r = R("Monumenta Serica", "chinese_lit")
check("纯人文·外文人文刊经ahci/sjr定档、绝不经ssci",
      r["explain"]["priorityWinner"]["catalog"] != "ssci"
      and r["explain"]["priorityWinner"]["catalog"] in ("ahci", "sjr", "erih")
      and r["tier"] in ("T3", "T4", "T5"), r)

# 4) 中/外系数按学科生效：法学外0.70、经管外1.00
r_law = R("Academy of Management Journal", "law")
r_bus = R("Academy of Management Journal", "business")
check("系数·法学外刊系数=0.70 生效",
      r_law["explain"]["coeff"]["origin"] == "intl" and approx(r_law["explain"]["coeff"]["value"], 0.70), r_law["explain"]["coeff"])
check("系数·经管外刊系数=1.00 生效",
      r_bus["explain"]["coeff"]["origin"] == "intl" and approx(r_bus["explain"]["coeff"]["value"], 1.00), r_bus["explain"]["coeff"])
check("系数·同一外刊 法学(0.315) ≠ 经管(1.00)，方向差异可见",
      approx(r_law["weight"], 0.315) and approx(r_bus["weight"], 1.00), (r_law["weight"], r_bus["weight"]))

# 5) 缺分区/缺 IF 时不报错、主档位照常
check("鲁棒·缺SJR分区/缺IF 不报错，ifUsed=False",
      r_bus["explain"]["ifUsed"] is False and r_bus["tier"] == "T1", r_bus["explain"]["ifUsed"])

# 6) 每个 journalWeight 可展开解释链
for r in (r_law, r_bus):
    e = r["explain"]
    check("解释链·含 signalSet/priorityWinner/coeff/notes",
          all(k in e for k in ("signalSet", "priorityWinner", "coeff", "notes", "base", "identify")), list(e))

# 7) 识别到但本原型无可映射信号 → 兜底（不报错）；外文人文刊在法学库落普通档
r = R("Monumenta Serica", "law")
check("兜底·法学库中的A&HCI刊(法学map无ahci)→普通T5，不报错",
      r["tier"] == "T5", r)

# 8) 认可顶刊(跨学科共同顶刊) → cssci.来源@recognizedTop → T1
r = R("中国社会科学", "chinese_lit")
check("顶刊·中国社会科学→chinese_lit→cssci.来源@recognizedTop→T1",
      r["tier"] == "T1" and approx(r["weight"], 1.00), r)

# 9) 配置完整性：10 学科齐、系数方向相反的证据
disc = DATA.disciplines()
_std10 = ["law", "marxism", "business", "economics", "education", "journalism",
          "publicadmin", "chinese_lit", "history", "foreign_lang"]
check("配置·10 标准学科齐全", all(d in disc for d in _std10), list(disc))
check("配置·law.intl=0.70 与 business.intl=1.00 方向相反",
      approx(disc["law"]["coeff"]["intl"], 0.70) and approx(disc["business"]["coeff"]["intl"], 1.00),
      (disc["law"]["coeff"], disc["business"]["coeff"]))

print("═══ law_personal（2026-06-28 旧档并入·个人法学增强）═══")
# 顶尖外文法评：外刊不打折→T1=最高
r = R("Harvard Law Review", "law_personal")
check("个人档·顶尖外文法评→law_review_top→T1→1.00（intl不打折）",
      r["tier"] == "T1" and approx(r["weight"], 1.00) and approx(r["explain"]["coeff"]["value"], 1.00), r)
# 外文权威→T1b（0.92，不打折、高于CSSCI，忠于旧档 外文权威>CSSCI）
r = R("American Political Science Review", "law_personal")
check("个人档·外文权威→ssci_law_authority→T1b→0.92（不打折、高于CSSCI）",
      r["tier"] == "T1b" and approx(r["weight"], 0.92) and r["explain"]["coeff"]["origin"] == "intl", r)
# 台湾核心→T1
r = R("台湾大学法学论丛", "law_personal")
check("个人档·台湾核心→tw_law.核心→T1→1.00",
      r["tier"] == "T1" and approx(r["weight"], 1.00), r)
# 台湾一般→T2
r = R("全国律师", "law_personal")
check("个人档·台湾一般→tw_law.一般→T2→0.85",
      r["tier"] == "T2" and approx(r["weight"], 0.85), r)
# CLSCI来源→T1b（0.92，法学顶刊高于CSSCI，忠于旧档 CLSCI>CSSCI）
r = R("中外法学", "law_personal")
check("个人档·CLSCI来源→clsci.来源→T1b→0.92（法学顶刊高于CSSCI）",
      r["tier"] == "T1b" and approx(r["weight"], 0.92), r)
# 排序链：三大权威(1.0) > CLSCI来源/外文权威(0.92) > CSSCI来源(0.85)
check("个人档·排序 三大权威1.0>CLSCI来源/外文权威0.92>CSSCI来源0.85",
      approx(R("中国法学", "law_personal")["weight"], 1.00)
      and approx(R("中外法学", "law_personal")["weight"], 0.92)
      and approx(R("American Political Science Review", "law_personal")["weight"], 0.92)
      and approx(R("历史研究", "law_personal")["weight"], 0.85), None)
# 报纸→T6
r = R("人民法院报", "law_personal")
check("个人档·报纸→newspaper→普通T5→0.25（原T6存疑档已取消）",
      r["tier"] == "T5" and approx(r["weight"], 0.25), r)

# 并存互不干扰：同一顶尖法评，标准law(产品)看不到私有目录→落普通；个人档→T1
r_std = R("Harvard Law Review", "law")
r_per = R("Harvard Law Review", "law_personal")
check("并存·哈佛法评 标准law≠T1（产品档不受个人判断影响）",
      r_std["tier"] != "T1", r_std)
check("并存·哈佛法评 个人档=T1 且权重远高于标准law",
      r_per["tier"] == "T1" and r_per["weight"] > r_std["weight"], (r_per["weight"], r_std["weight"]))
# 私有目录不泄漏到其他学科
r = R("Harvard Law Review", "chinese_lit")
check("隔离·哈佛法评在文学库≠T1（私有目录仅law_personal引用）",
      r["tier"] != "T1", r)
# 迁移后标准 law 顶刊仍正确
check("迁移后·中国法学 标准law 仍 T1=1.00", approx(R("中国法学", "law")["weight"], 1.00))
check("配置·11 学科（含 law_personal）", len(disc) == 11, list(disc))

print("═══ F38-B 白名单 & 统一治本 ═══")
# 1) 标准 law：台湾刊落普通 T5，且 signalSet/hitCatalogs 不泄漏 tw_law
r = R("台湾大学法学论丛", "law")
check("治本·标准law 台湾核心→普通T5(0.25)", r["tier"] == "T5" and approx(r["weight"], 0.25), r)
check("白名单·标准law 台湾刊 signalSet 无 tw_law",
      all("tw_law" not in s for s in r["explain"]["signalSet"]), r["explain"]["signalSet"])
check("白名单·标准law 台湾刊 hitCatalogs 无 tw_law",
      all(h["catalog"] != "tw_law" for h in r["hitCatalogs"]), r["hitCatalogs"])
# 2) 标准 law：外文法评落普通，signalSet 不泄漏 law_review_top
r = R("Harvard Law Review", "law")
check("治本·标准law 哈佛法评→普通T5", r["tier"] == "T5", r)
check("白名单·标准law 哈佛法评 signalSet 无 law_review_top",
      all("law_review_top" not in s for s in r["explain"]["signalSet"]), r["explain"]["signalSet"])
# 3) law_personal：台湾/外文法评仍入高档，且 signalSet 应含私有目录
rp = R("台湾大学法学论丛", "law_personal")
check("治本·个人档 台湾核心仍 T1", rp["tier"] == "T1" and approx(rp["weight"], 1.00), rp)
check("白名单·个人档 台湾刊 signalSet 含 tw_law",
      any("tw_law" in s for s in rp["explain"]["signalSet"]), rp["explain"]["signalSet"])
# 4) 分布随学科变：同一台湾刊 law=T5 / law_personal=T1
check("治本·分布随学科变 台湾刊 law(T5)≠law_personal(T1)",
      R("台湾大学法学论丛", "law")["tier"] != R("台湾大学法学论丛", "law_personal")["tier"], None)
# 5) visible_catalogs 白名单本身
check("白名单·law 不含私有目录",
      not ({"tw_law", "law_review_top", "ssci_law_authority", "newspaper"} & DATA.visible_catalogs("law")), None)
check("白名单·law_personal 含 4 私有目录",
      {"tw_law", "law_review_top", "ssci_law_authority", "newspaper"} <= DATA.visible_catalogs("law_personal"), None)

print("═══ 知网复合影响因子（IF 微调，已开启）═══")
check("IF·全库复合影响因子已加载(>3000)", len(DATA.if_values) > 3000, len(DATA.if_values))
# 开 IF：法学库里的中文核心刊按其复合IF全库分位小幅上浮（ifStrength=0.05→最多+5%）
_b = R("历史研究", "law_personal")["weight"]
_w = R("历史研究", "law_personal", if_enabled=True)
check("IF·历史研究 开IF后上浮且≤+5%、ifUsed=True",
      _w["weight"] > _b and _w["weight"] <= _b * 1.05 + 1e-9 and _w["explain"]["ifUsed"] is True, (_b, _w["weight"]))
# 纯人文 ifStrength=0 → 即便开 IF 也不动（规格：人文引用指标失效）
_a1 = R("文学评论", "chinese_lit")["weight"]; _a2 = R("文学评论", "chinese_lit", if_enabled=True)["weight"]
check("IF·纯人文(ifStrength=0)开IF也不变", approx(_a1, _a2), (_a1, _a2))
# T1 顶刊开 IF 仍封顶 1.0（clamp）
check("IF·中国法学 开IF仍封顶1.0", approx(R("中国法学", "law_personal", if_enabled=True)["weight"], 1.0))

print("\n─── 示例解释链（中国法学 / law）───")
import json
print(json.dumps(R("中国法学", "law"), ensure_ascii=False, indent=2))

print(f"\n═══ 结果：{_n - len(_fail)}/{_n} 通过 ═══")
if _fail:
    print("FAIL:", "; ".join(_fail))
    sys.exit(1)
print("ALL PASS ✓")
sys.exit(0)

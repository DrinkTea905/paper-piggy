# journal_grading —— 期刊引用权重分级引擎（v1）

把「期刊等级」转成**可解释、可调的引用权重 `journalWeight ∈ [0,1]`**，供检索排序 / 证据强度 / 过滤 / 加权。
权重是**来源先验**，不是单篇质量，只作多因子之一。落地自同目录规格 `期刊引用权重分级方案.md`（评级逻辑/系数/顶刊清单一字未改）。

**本期范围（v1）**：只做独立引擎——**不改动** `index_light` / `retriever` / 前端（接入检索是下一步，见文末）。
你 2026-06-28 的个人法学旧档（`journal_tiers.json`，312 条）已**并入**本引擎为 `law_personal` 学科（见「law_personal」一节）。

## 用法

```python
from journal_grading import resolve_journal_weight

r = resolve_journal_weight({"journal": "中国法学", "issn": "1003-1707"}, "law")
# {'weight': 1.0, 'tier': 'T1', 'needsReview': False,
#  'hitCatalogs': [{'catalog':'clsci','level':'权威','version':'...'}],
#  'explain': { ...命中目录/级别/版本、priority赢家、原型与中外系数、是否用IF、是否待确认... }}
```

- `item`：dict，至少含 `journal`(刊名)，可含 `issn`/`eissn`、`language`/`langid`、`if`/`if_cnki`。
- `active_discipline`：10 选 1 —— `law / marxism / business / economics / education / journalism / publicadmin / chinese_lit / history / foreign_lang`。本库默认锁 `law`。
- 返回 `tier` 为 `T1`–`T6`；未识别时为 `"待确认"`（`needsReview=True`、`explain.provisional=True`，绝不静默按普通档）。

改配置/目录数据后热重载：`from journal_grading import reload; reload()`。

## 自检

```
python journal_grading/selftest.py
```
覆盖规格 §六 4 个 worked example 与 §十 验收清单（当前 21/21 通过）。

## 目录结构（三层解耦）

```
journal_grading/
  config/grading_config.json   ① 配置即代码（CC0）——用户/AI 微调只碰这一份
  catalogs/*.json              ② 客观目录数据（每目录一文件，种子；用户副本放 <DATA>/journals/）
  normalize.py                 刊名/ISSN 归一
  loader.py                    加载配置+目录，建 ISSN/归一名索引（DATA优先→APP种子回退，带缓存/reload）
  identify.py                  期刊识别：ISSN优先→归一名→模糊≥0.9→否则待确认
  resolver.py                  ③ resolve_journal_weight（priority-max 定档→查分→IF微调(本期关)→乘中/外系数→clamp）
  selftest.py                  §六+§十 自检
  离线数据获取清单.md           各目录从哪个免费来源下、整理成什么字段
```

## 微调（只改 `grading_config.json`）

- 改本土倾向：`disciplines.law.coeff.intl` 0.70→0.50（法学更不看外刊）。
- 加学科：复制一段 `disciplines.*`，改 id/name，必要时换 `archetype`。
- 改档位分差：`tiers.T3` 0.65→0.72。
- 自定顶刊：往 `recognizedTopLists.<学科>` 加 `{name, issn}` → 该刊升 T1。
- 关影响因子：对应 `archetypes.*.ifStrength` 设 0（或保持 `resolution.ifEnabled=false`，本期即关）。

## 影响因子（本期关，留 v2）

`resolution.ifEnabled=false`；各原型 `ifStrength` 系数保留。v2 经浏览器抓知网复合影响因子填 `if_cnki`，
resolver 里 `_item_if` + `percentileInDiscipline` 的挂点已留好（无数据自动跳过、不报错）。

## law_personal（个人法学增强档，旧档已并入）

你的个人法学旧档（`journal_tiers.json` 312 条，2026-06-28 手工整理）已由 `migrate_legacy.py` 无损并入为一个**个人学科 `law_personal`** + 4 个私有目录，与面向用户的标准 `law` 并存、互不干扰：

| 私有目录 | 承载旧档 | 档位 | 中/外 |
|---|---|---|---|
| `law_review_top` | 外文顶级法评(11) | T1 | 外(不打折 1.0) |
| `tw_law` | 台湾核心(3)/一般(10) | T1/T2 | 中 |
| `ssci_law_authority` | 外文权威(46) | T2 | 外(不打折 1.0) |
| `newspaper` | 报纸(5) | T6 | 中 |

CLSCI(31)→`clsci`、CSSCI(122)+扩展(42)→`cssci`，普刊(42)跳过（默认档）。

- **外刊不打折**：`law_personal.coeff.intl = 1.0`——顶尖外文法评 = 最高(T1=1.00)、外文权威高位(T2=0.85)，忠于你旧档语义（与标准 `law` 的外 0.70 相反，这正是"个人专用"的意义）。
- **产品隔离**：私有目录只被 `A_法学个人` 原型引用；普通用户库锁标准 `law`，看不到台湾/法评这些你的个人判断。**产品 UI 的学科选择器应只列 10 个标准学科，`law_personal` 作隐藏/高级项，仅你自己锁。**
- **微调**：外文权威默认 T2，想让它高于 CSSCI 就在 `tiers` 加一档或改 `A_法学个人.map`；旧档的 English/拼音别名条目（如「China Legal Science [zhongguo faxue]」）与中文条目并存，补 ISSN 时可统一。
- 重跑迁移（旧档更新后）：`python journal_grading/migrate_legacy.py`（幂等）。

## 后续接入检索（下一步，本期未做）

计划把 `journalWeight` 接入：
1. 建库期 `index_light.enrich()` 给每篇加 `journal_weight` + `weight_tier` 字段（与现有 `journal_tier` 并存）；
2. 检索期 `retriever._apply_sort` 的 `blend` 用 `journal_weight` 替代/并入离散 `TIER_BONUS`，并支持按权重过滤；
3. 学科锁定存 `settings.json` 的 `journal_discipline`（默认 `law`），首启向导/库设置里选。

以上均为独立改动，需回归测试现有法学库排序，故本期不做。

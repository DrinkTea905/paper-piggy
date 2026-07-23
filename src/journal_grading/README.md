# journal_grading —— 全类型评价中的期刊目录引擎

把「期刊等级」转成**可解释、可调的引用权重 `journalWeight ∈ [0,1]`**，供检索排序 / 证据强度 / 过滤 / 加权。
权重是**来源先验**，不是单篇质量，只作多因子之一。落地自同目录规格 `期刊引用权重分级方案.md`（评级逻辑/系数/顶刊清单一字未改）。

本包只负责期刊目录识别与内部细分权重；`grading_svc.evaluate_paper()` 再把它与书籍、学位论文、
法源、报告等性质预设合并为统一评价，并接到检索、浏览、综述来源和 MCP。
个人法学旧档已并入 `law_personal`；`law_personal_fun` 只在服务层做显示别名，不复制配置。

## 用法

```python
from journal_grading import resolve_journal_weight

r = resolve_journal_weight({"journal": "中国法学", "issn": "1003-1707"}, "law")
# {'weight': 1.0, 'tier': 'T1', 'needsReview': False,
#  'hitCatalogs': [{'catalog':'clsci','level':'权威','version':'...'}],
#  'explain': { ...命中目录/级别/版本、priority赢家、原型与中外系数、是否用IF、是否待确认... }}
```

- `item`：dict，至少含 `journal`(刊名)，可含 `issn`/`eissn`、`language`/`langid`、`if`/`if_cnki`。
- `active_discipline`：配置中的 11 个学科之一（含 `law_personal`）；当前值以 `settings.DEFAULT` 为准。
- 返回的 `tier`/`needsReview` 是内部识别结果。普通接口由 `grading_svc` 折叠成权威、顶级、核心、普通四档；未识别只在解释链保留原因，不显示“待确认”档。

改配置/目录数据后热重载：`from journal_grading import reload; reload()`。

## 自检

```
python journal_grading/selftest.py
```
覆盖规格 worked examples、目录隔离、学科系数、个人法学增强和鲁棒性；以命令实际输出为准。

## 目录结构（三层解耦）

```
journal_grading/
  config/grading_config.json   ① 配置即代码（CC0）——用户/AI 微调只碰这一份
  catalogs/*.json              ② 客观目录数据（每目录一文件；用户副本放 <DATA>/journals/）
  catalog_registry.py          目录来源、上游版本/提交与开发维护日期
  update_catalogs.py           把已审查的 ShowJCR CSV 机械转换为 SSCI/JCR 目录
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

## 影响因子

中文刊可使用随包 `if_cnki.json` 做同档小幅微调；纯人文学科的 `ifStrength=0`。外文 SSCI/JCR
客观分区来自 ShowJCR JCR2025；缺失值自动跳过，不影响目录收录标签。

## law_personal（个人法学增强档，旧档已并入）

你的个人法学旧档（`journal_tiers.json` 312 条，2026-06-28 手工整理）已由 `migrate_legacy.py` 无损并入为一个**个人学科 `law_personal`** + 4 个私有目录，与面向用户的标准 `law` 并存、互不干扰：

| 私有目录 | 承载旧档 | 档位 | 中/外 |
|---|---|---|---|
| `law_review_top` | 外文顶级法评(11) | T1 | 外(不打折 1.0) |
| `tw_law` | 台湾重点(3)/其他(10) | T1/T2 | 中 |
| `tssci_law` | 正式 TSSCI 法律学门(7) | T1b（重点三刊仍 T1） | 中 |
| `ssci_law_authority` | 精选外文权威(46) | T1b | 外(不打折 1.0) |
| `newspaper` | 报纸(5) | T5 | 中 |

CLSCI(31)→`clsci`、CSSCI(122)+扩展(42)→`cssci`，普刊(42)跳过（默认档）。

- **外刊不打折**：`law_personal.coeff.intl = 1.0`——顶尖外文法评为权威，精选外文权威为顶级，保留既有高权重。
- **目录隔离**：个人目录只被 `law_personal` 使用；正式 TSSCI 独立成表，只有真实命中时才显示 TSSCI。
- **显示别名**：`law_personal_fun` canonical alias 到 `law_personal`，只把四档显示为“夯/顶级/人上人/NPC”。
- 重跑迁移（旧档更新后）：`python journal_grading/migrate_legacy.py`（幂等）。

## 已接入的消费点

`grading_svc.evaluate_paper()` 已统一接入 `retriever`、`server /papers`、单篇详情、wiki 来源、
MCP `list_sources` 与库总览。索引中的旧 `journal_tier` 仅作兼容；切学科、改映射或单篇改档均即时生效，无需重建索引。

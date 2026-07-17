# 维护手册 —— 「改了 X → 必须同步 Y」

> **改 UI / MCP 工具 / Agent 模板 / wiki 规约之前，先读这一份。**
> 这个应用有三套面向不同读者的指引，它们**不会自动跟着代码走**。历史上已经漂过一次
> （`MCP接入说明.md` 写「28 个工具」，代码里实际 32 个），所以别指望「我记得改」。

---

## 0. 为什么会漂

应用里的「指引」分三类，读者完全不同：

| 类别 | 给谁看 | 物理位置 |
|---|---|---|
| **新手指引** | 人类用户 | `web/index.html` 的静态 HTML + `web/app.js` 的渲染函数 |
| **agent 指引** | AI（应用内 Agent、外部 Claude Code） | `agent_ws.py` 的模板常量、`mcp_server.py` 的工具描述与 instructions、`wiki_store.py` 的 WIKI_MD_SEED |
| **开发者文档** | 你、下一个 agent | `MCP接入说明.md`、`docs/`、`CLAUDE.md` |

改一个功能，可能三类都要动。下面是映射表。

---

## 1. 映射表

图例：✅ = 有机器校验（`check_guides.py` 或 `--check` 能自动检出）；❌ = 只能靠人。

### 1.1 MCP 层

| 你改了 | 必须同步 | 校验 |
|---|---|---|
| `mcp_server.TOOLS`（:224）增删工具或改 description | 跑 `python gen_mcp_doc.py` 重新生成 `MCP接入说明.md` 的工具表 | ✅ `gen_mcp_doc.py --check`（过期时退出码 1） |
| `mcp_server.RESOURCES`（:610） | **手改** `MCP接入说明.md` 的 Resources 表 —— ⚠️ `gen_mcp_doc.py` **不管这张表**，`localkb://memory` 就是这么漏掉的 | ✅ check_guides ② |
| `mcp_server.PROMPTS`（:635） | 同上，手改 Prompts 表 | ✅ check_guides ② |
| `mcp_server._INSTRUCTIONS_HEAD`（:69）/ `_workspace_text()`（:130） | `index.html` `#ag-guide` 的「成果去哪」「权限与安全」两章；`agent_ws._README_RELY`（:58）、`_rules_summary_text()`（:315） | ❌ 人肉 |

### 1.2 Agent 工作区（`agent_ws.py`）

| 你改了 | 必须同步 | 校验 |
|---|---|---|
| `_WF_PAPER`(:197) / `_WF_WIKI`(:228) / `_WF_DIVERGENCE`(:253) —— 三个内置工作流 | ① 改完模板必须跑 `python agent_ws.py --print-hashes`，把新 hash 追加进 `_FACTORY_HASHES`（升级器已建成，见 §2.1）② `index.html` 第 3 章的工作流卡 ③ `_SKILLS_README`(:178) 里列出的工作流清单 | ✅ check_guides ③ |
| `_README_RELY`(:58) / `_README_OUTPUT`(:81) | 同样要追加 hash（否则老用户凭空多出 `.new.md`）；`#ag-guide` 对应章节 | ❌ 人肉（check_guides ③ 只覆盖三条工作流文件，不查这两份散文体 README） |
| 新增一条工作流 | `index.html` 里「三条开箱即用的工作流」的**硬编码列表**会静默变错 —— 这是**正确性问题**，不是文案洁癖 | ✅ check_guides ③ |

### 1.3 wiki 综合层（`wiki_store.py`）

| 你改了 | 必须同步 | 校验 |
|---|---|---|
| `WIKI_MD_SEED`(:33) —— wiki 页面规约种子 | ① **必须 bump `SCHEMA_VERSION`**(:31，现在是 `"v2"`) ② 把旧版的 normalized-sha1 加进 `_FACTORY_HASHES`(:172) ③ `MCP接入说明.md` 的「信任模型」段 | ✅ check_guides ④（只断言 seed 里的 `schema vN` == `SCHEMA_VERSION`；②③ 仍靠人） |

> ⚠️ **忘了 bump `SCHEMA_VERSION` 会静默让老库永远收到过期规约。** 这是本项目最阴的一个坑：
> 不报错、不告警，只是所有老用户的 wiki 规约永远停在旧版。

### 1.4 前端指引（`web/`）

| 你改了 | 必须同步 | 校验 |
|---|---|---|
| 新增/改动 UI 功能（页签、按钮、流程） | `index.html` `#home-guide`(:87) 八章 + `#ag-guide`(:350) 十章 + `app.js` `agentGuideCard()`(:1207) 四步图 | ❌ 人肉（用 §3 的 checklist） |
| 首启向导流程 | `index.html` `.wizard-steps`(:786) + `app.js` `renderStep1`(:3494) ~ `renderStep5`(:4025) + `src/README.md` 的「第一次使用」段 | ❌ 人肉 |
| 「🧹 清空并从头重建索引」(`#sec-rebuild` + `POST /index/reset`，破坏性、须 confirm) | 设置页就地说明是主文案；**动它必对齐 `backup.py` 的 CORE/INDEX「移哪些·保留哪些」口径**；破坏性操作要在指引里提示"先备份" | ❌ 人肉 |
| 顶栏自动更新徽标（`#up-badge`、设置页 `#up-autocheck`、`app.js renderUpdateBadge()`） | 两个 localStorage 键 `localkb.autoUpdateCheck`(默认开)/`localkb.updateDismissed`(按版本忽略)；文案要与「知识库自动更新」明确**区分**（同名不同物，见 CHANGELOG v1.0.1 提醒） | ❌ 人肉 |
| PDF 提取 / OCR 状态或文案 | 同步 `deep_extract_status.VALID_STATUSES`、`server.py` 的状态下发、`app.js` 的徽标/进度/重试文案，以及 `#home-guide` / `#ag-guide`。`ocr_pending` 必须能进入深索；`missing_pdf / invalid_pdf / ocr_failed` 才是阻塞终态 | ✅ OCR 单测覆盖核心；UI 文案仍需人肉 |

### 1.5 其它

| 你改了 | 必须同步 | 校验 |
|---|---|---|
| 期刊评级规则 | `journal_grading/` 配置 + `journal_grading/期刊引用权重分级方案.md`；跑 `journal_grading/selftest.py` | ✅ selftest |
| 依赖 | `requirements.txt` **和** `requirements.lock` 同时改；同步 `THIRD-PARTY-NOTICES.md` 并核许可证；分发包需要重建 `build/py312`。含新依赖的首版必须走完整安装器，应用内 app 增量包不会补 Python wheel。⚠️ 平台专属包用标记：Windows-only 加 `; sys_platform=="win32"`（如 `pythonnet`），macOS-only 加 `; sys_platform=="darwin"`（如 `pyobjc-*`）。`.lock` 是 Windows 实机冻结，**macOS 用 `.txt` 不用 `.lock`** | ❌ |
| 版本号 | **只改 `config.APP_VERSION`**(`config.py:19`) | ✅ check_guides ⑤（断言全源码没有第二处版本字面量） |
| **新增任何 `C.DATA / "xxx"` 落点** | **必须**在 `backup.py` 的四个清单里给它选一个座位：`CORE_IN_DATA`（备份）/ `INDEX_IN_DATA`（可选索引）/ `NEVER_IN_DATA`（永不）/ `SPECIAL_IN_DATA` | ✅ check_guides ⑥（未分类 → 直接中止打包） |
| **新增 `C.DATA.parent / "xxx"`（home 级）落点** | 同样要想清楚备份归类（如 `0_Agent*` 归 `backup.CORE_IN_HOME`）。⚠️ **check_guides ⑥ 只扫 `C.DATA / "xxx"`、不扫 home 级** —— 这一层纯靠人（否则重演 backup 第一版漏 `grading_memo` 的坑） | ❌ 人肉（护栏盲区） |
| HTTP 接口 | `/docs` 自动生成。但如果 agent 该知道这个接口 → 回到 §0.1（可能要动 MCP 工具或指引） | — |

> **为什么「新增数据落点」值得一条硬护栏**：备份清单漏了某个文件，**用户是不会知道的** ——
> 备份看起来成功了，直到他恢复之后才发现东西没了。而漏掉的往往正是最贵的那些。
> backup.py 的第一版清单就是凭印象列的，漏了 `grading_memo.json`（689 条 LLM 期刊分级，
> 花过真钱）、`summaries/`（SAC 检索摘要，花过 API 钱）、`tier_overrides.json`（用户一条条
> 手改的档位）—— 三样全是不可再生或再生要花钱的。是实机跑了一次备份、去数产物才发现的。
> 所以现在改成机器强制：不分类，就打不了包。

---

## 2. 必须补的机制

### 2.1 模板升级器（✅ 2026-07-14 已建成）

**曾经的病**：`agent_ws._write_if_absent()` **只在文件不存在时才写**。
后果：你改了 `_WF_PAPER` 的文本，**所有已经跑过一次的机器（包括开发机自己）永远收不到新版**——
「功能变更 → 指引同步」这条链上最后一环是断的。

**现在的实现**（`agent_ws.py:343-440`，`_FACTORY_HASHES` + `_template_specs()` + `_ensure_template()`）：
给每份出厂模板记一份「历史出厂版的 normalized-sha1（去掉所有空白后算）」清单，`ensure_scaffold()` 逐份比对磁盘文件：

- 文件不存在 → `created`
- 与**当前**模板一字不差 → `current`（一个字节都不写）
- 命中**历史**出厂版 → 用户没动过 → `upgraded`（静默换成新版）
- 谁都不像 → 用户改过 → `forked`：**保留用户的文件**，旁边写一份 `<名>.new.md` 供合并
- 「项目记忆.md / 变更日志.md」这类**用户数据种子**被写过 → `kept`（不塞 .new.md）

> ⚠️ **维护 SOP（改模板必做）**：改完任何模板文本后跑
> `build\py312\python.exe src\agent_ws.py --print-hashes`，
> 把标「★ 新版：请追加」的 hash 追加进 `_FACTORY_HASHES`（**旧 hash 一个都别删**）。
> 忘了追加 → 这一版的出厂原样文件在下下版会被误判成「用户改过」→ 用户机器上凭空多出一堆 `.new.md`。

### 2.2 把 UI 里写死的清单改成动态（低成本，先做这三条）

项目里**已经在用**这个模式了 —— `app.js:2444` 的工具数取的是后端下发的真实值：

```js
const n = (AG.cfg && AG.cfg.tool_count) || AG_TOOLS.length;   // 后端真值优先
```

后端 `server.py:418` 就是 `tool_count = len(MCP.TOOLS)`。照此办理：

- `AG_TOOLS`（`app.js:2417`，8 条硬编码）、`AG_PROMPTS`（:2427，4 条）→ 改由后端 `/agent/config` 下发。渲染只是一行 `.map()`，成本近乎零。
- `index.html` 里硬写的「三条开箱即用的工作流」+ 逐条列名 → 改成读 `0_Agent资料库/技能/` 的实际文件列表。**加第 4 条技能时这三处会静默变错。**
- `app.js` 的事件接线全按 id/class 挂（`wireHomeGuide()`:1248、`wireAgentPage()`:2533），所以模板生成器只要原样输出 id/class，`app.js` 一行都不用改。

> ⛔ **不要**把 `#home-guide` 八章 / `#ag-guide` 十章的中文正文（约 230 行）也做成代码生成 —— 收益低于成本。那部分用 §3 的 checklist 管。

### 2.3 `check_guides.py`（✅ 2026-07-14 已建成，只读校验器）

在 `src/check_guides.py`，已进 `build_bundle.py` 的 DEV_ONLY 名单（不进分发包）。
**`build_bundle.py` 开头会跑它（`verify_guides()`），退出码非 0 直接中止打包**（`--skip-checks` 可临时跳过，正式发版不许跳）。

现在断言这 7 条（编号即输出里的 ①~⑦）：

1. ① 调 `gen_mcp_doc.main(--check)`（工具表 ↔ `mcp_server.TOOLS`）
2. ② `RESOURCES` + `RESOURCE_TEMPLATES` ↔ `MCP接入说明.md` 的 Resources 表（双向集合比对）；`PROMPTS` ↔ Prompts 表
3. ③ `_WF_*` 数 == `ensure_scaffold` 落盘数 == `_SKILLS_README` 列出数 == `index.html` 第 3 章卡片数 == 正文里的中文数字，且逐个文件名比对
4. ④ `WIKI_MD_SEED` 里写的 `schema vN` == `SCHEMA_VERSION`
5. ⑤ 全源码（.py/.js/.html）只有一处版本字面量（`config.APP_VERSION`）
6. ⑥ 所有 `C.DATA / "xxx"` 落点都在 `backup.py` 的备份分类清单中
7. ⑦ 前端 JS 不得调用浏览器原生 `confirm()` / `alert()`，统一使用应用内对话框

**仍未机器化（靠人）**：`ensure_scaffold()` 写的其余文件名（项目记忆.md / 变更日志.md / 交付说明书模板.md……）
是否都在 `_README_RELY` / `_README_OUTPUT` 里被提到——那两份是散文体，正则误报率高，硬凑不如不做。
要补的话，先把 README 里的文件名用反引号写死，再机器比对。

理由很硬：`index.html:86` 和 `:349` 各有一条注释写着「⚠ 维护铁律：功能更新时这份指引也要同步更新」——
**项目自己都承认是靠人肉纪律，而事实证明它失败了**（工具表漂了 4 个，`localkb://memory` 漏了一整条 Resource）。
打包是发布的必经关口，卡在那里代价最小。

---

## 3. 新增功能时的人肉 checklist

代码写完之后，逐条过：

- [ ] 这个功能，**用户**需要知道吗？→ 改 `#home-guide`(:87) / `#ag-guide`(:350) / 向导
- [ ] 这个功能，**AI agent** 需要知道吗？→ 改 `mcp_server` 工具或 `agent_ws` 工作流模板
- [ ] 我改了 agent 模板吗？→ **老用户能收到新版吗？**（§2.1）
- [ ] 我改了 wiki 规约吗？→ **bump SCHEMA_VERSION 了吗？**（§1.3）
- [ ] 我改了 MCP 工具吗？→ 跑 `gen_mcp_doc.py` 了吗？
- [ ] UI 里有没有**硬编码的数量/清单**会因为这次改动而变错？（§2.2）
- [ ] 新增/升级依赖了吗？→ 同步 lock + 第三方声明，并明确首版是否必须完整安装器
- [ ] 改了 PDF/OCR 链路吗？→ 混合 PDF、附件缺失、坏 PDF、OCR 失败、旧状态迁移都测了吗？
- [ ] `CHANGELOG.md` 加一行了吗？

---

## 4. 隐私闸门（`.gitignore`）

改 `.gitignore` 之前必读。**被忽略的目录里有真实用户数据：**

- `src/data/settings.json` —— 跑过一次应用后会写入**真实 API key**
- `src/data/meta/papers.jsonl` —— 2110 条真实 Zotero 元数据，其中 1443 条含 `D:\` 本机绝对路径
- `src/data/wiki/.git` —— 嵌套仓库，不忽略会变成无效 gitlink

**发任何公开版本之前**，跑一遍：

```bash
git ls-files | grep -iE "settings\.json|papers\.jsonl|\.key|secret"   # 必须为空
```

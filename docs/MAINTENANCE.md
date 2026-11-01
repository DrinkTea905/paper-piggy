# 维护手册 —— 「改了 X → 必须同步 Y」

> **改 UI / MCP 工具 / Agent 模板 / wiki 规约之前，先读这一份。**
> 这个应用有三套面向不同读者的指引，它们**不会自动跟着代码走**。历史上已经漂过一次
> （`MCP接入说明.md` 曾写死旧工具数，代码继续变化后就漂移了），所以别指望「我记得改」。

> **面向用户的功能或 UI 更新还必须固定检查四处发布面**：① 产品内「新手指引」；
> ② 产品内「Agent 指引」；③ 主仓库 GitHub README；④ 独立教程仓库
> `DrinkTea905/paper-piggy-guide`。逐处判断是否需要同步；不改也要在完成报告中说明理由。

---

## 0. 为什么会漂

后台官方更新另有发布门禁：`backup_component_auth.py` 与固定公钥负责签名验证，`backup_component_update.py` 和 `backup_service.py` 负责受保护切代与状态，`installer/sign_backup_component.py` 仅在发布端使用。变更这些代码时须重建、签名并实测组件；出包需同时校验源码、组件 EXE、签名描述与内置公钥一致。发布私钥不得进仓库、安装器、用户数据或日志。新任务／队列使用既有 `backup_work` 暂存范围，不能混入知识库备份。

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
| `mcp_server._INSTRUCTIONS_HEAD` / `_workspace_text()` | `index.html` `#ag-guide` 的「成果去哪」「权限与安全」两章；`agent_ws._README_RELY`、`_ROOT_AGENTS`、`_ROOT_CLAUDE` 与 `_rules_summary_text()` | ❌ 人肉（闸门三句有 check_guides ③ 逐字断言） |
| **记忆的文件布局 / `read_project_memory` 的 scope / `append_project_memory` 的 target / 硬约束阈值** | 唯一事实源是 `agent_ws`：`memory_file/tools_file/tooldoms_dir/topics_dir/changelog_file` + `MEMORY_CORE_MAX_*`、`MEMORY_TOPIC_DUP_RATIO`、`TOPIC_STATUSES`、`MEMORY_TOOLDOM_FALLBACK` + `append_memory()`。**mcp_server 只组装视图与分段，绝不另拼路径**。要同步的 9 处：① `_INSTRUCTIONS_HEAD` 项目记忆闸门段 ② `_workspace_text()` 的 `mem_layout` ③ `_ROOT_AGENTS`（`_ROOT_CLAUDE` 自动派生）④ `_README_RELY` 的「记忆/」条目 ⑤ `_PROJECT_MEMORY` / `_TOOLS_NOTE` / `_CHANGELOG` 三份种子 ⑥ `MCP接入说明.md` 的手写闸门段（**不在 TOOLS:BEGIN/END 内，gen_mcp_doc 不管**）⑦ `index.html` `#home-guide` 第 1/3/6 章与 `#ag-guide` 第 4 章 ⑧ `app.js` 的 `agentGuideCard()` 与 `AG_TOOLS` ⑨ `_WF_WIKI` 的「记忆分区」步与 `maintenance._memory_todo()`（用户说「维护」就得把分区整理掉，两处口径必须一致）；另加 `README.md` 目录树与 `docs/ARCHITECTURE.md` | ✅ check_guides ③（闸门四/三/两句逐字）+ ④b（种子 hash）+ `test_memory_partition.py` |

### 1.2 Agent 工作区（`agent_ws.py`）

| 你改了 | 必须同步 | 校验 |
|---|---|---|
| `_WF_JJ_DRAFT` / `_WF_JJ_REVIEW` / `_WF_GENERAL_DRAFT` / `_WF_REVIEW` / `_WF_WIKI` / `_WF_DIVERGENCE` —— 六个内置工作流 | ① 改完模板必须跑 `python agent_ws.py --print-hashes`，把新 normalized hash 追加进 `_FACTORY_HASHES`，并把新 exact hash 追加进 `_WORKFLOW_FACTORY_EXACT_HASHES`（两表旧值都不删，见 §2.1）② 保留每份工作流的“触发条件 / 开工前检查 / 用户决策点 / 完成标准 / 最终报告”五段强制契约 ③ `index.html` 第 3 章的工作流卡 ④ `_SKILLS_README` 里列出的工作流清单 ⑤ 四条研究工作流保持“任务类型 → 研究领域”路由与**《检索发现日志》的四列格式**（查询词逐字／名次／处置／理由，外加漏召回一节；列名与顺序不得改动——这是检索校准的唯一数据源，格式一变历史日志就聚合不起来了）；两条综述与通用初稿保留“开工前问一次是否派子代理”的用户决策点，**少年司法初稿（v6，2026-08-20 上线）改为默认派**（批量精读／引证核验／定稿前独立校核派，起草正文各章不派）。少年司法初稿另须保留：**先读文献再搭骨架**（完整原文，检索片段与摘要不算读过）、唯一决策点“骨架树连同每章准备写什么贴给用户看一眼、无异议即续写”、9 条禁区与五条不能破、引注可靠性双口径（亲读／承核验分别标注并报双数字）、“用户已给详细大纲时结构指标降为参考”、自动附带《三大刊结构》台账，以及“最终成稿交 DOCX”要求。**选型前侦查、两张骨架卡、准入自检、七类结构原型、18 项可数硬失败随 v6 整体废止，不得回潮**（v4 教训：可数指标能被优化到全绿而质量并不改善）。通用初稿保留“尚未经过其他部门法训练验证”警告 | ✅ check_guides ③ + ④b |
| `_JJ_DRAFT_STRUCTURE_REF` / `_WORKFLOW_COMPANION_KEYS` —— 非顶层伴随材料 | **v6 起只自动附带《论文初稿（少年司法版）—三大刊结构.md》**。旧《…—成文技艺手册.md》不再保留正文常量或模板规格；升级迁移会在 `0_Agent资料库` 范围内一次性删除应用管理的主件、旧旁本、待合并件和历史备份，包括用户修改件，并以状态标记防止以后反复删除。仍在用的伴随材料物理落点固定在 `技能/参考手册/` 子目录，常量不得以 `_WF_` 命名，也不得列进 `## 现有工作流`；`read_workflow` 必须读取磁盘上的用户当前版本，不读代码常量或集中待合并版本。伴随材料和工作流一样登记 normalized 与 exact 历史指纹、保护 Markdown 空白与用户修改；同步 `_SKILLS_README` 的独立“自动附带”小节、产品内说明和读取契约测试 | ✅ check_guides ③ + ④b；`test_mcp_contract.py` |
| 新建、重训或从素材蒸馏专题工作流 | 先完整读取 `docs/设计/专题工作流训练方法.md`；区分“领域 × 任务类型”，先冻结语言、地区、类型、期刊等级和全文等素材准入条件，再以原文精读、子代理复核/对抗和规则裁决形成候选工作流。同一任务只有方法路线不同时优先做内部路线与用户选择卡，不滥增顶层工作流。训练记录留在设计文档，运行时模板只保留执行步骤、闸门和失败警告；默认不调用付费模型，也不把匿名 A/B 当作常规步骤 | ❌ 人工；候选工作流落地后仍须执行本表其余校验 |
| `_ROOT_AGENTS` / `_ROOT_CLAUDE` —— Agent 工作区根入口 | 两份内容都要保持“先完整读取项目记忆、私有记忆同步写回、再读取匹配工作流、维护即全量审查、完成后复核总结”的同一口径；同步追加模板 hash；新增 home 级文件要落入 `backup.CORE_IN_HOME` | ✅ check_guides ③/④b；备份归类仍需人读 |
| `_README_RELY` / `_README_OUTPUT` / `_MAINTENANCE_README` | 同样要追加 hash（否则老用户凭空多出待合并文件）；`#ag-guide` 对应章节。升级与备份目录结构变化还要同步旧布局迁移、完整备份包含规则和恢复测试 | ✅ check_guides ④b（散文正文仍需人读） |
| `src/kb.py` —— 远程会话直读桥（出厂铺到 `0_Agent资料库/工具/kb.py`，正文由 `_kb_bridge_source()` 直接读那份源码，**不另存副本**） | ① 改完必须跑 `--print-hashes`，把新 **exact** hash 追加进 `_WORKFLOW_FACTORY_EXACT_HASHES`（它是 Python 代码，缩进与空行有语义，去空白的 normalized 指纹管不住「只改缩进」的新版，会静默不升级）② `_REMOTE_BRIDGE_GUIDE` 工作流里的命令与能力口径 ③ `_ROOT_AGENTS` 顶部「远程会话闸门」里的判据与口径 ④ 保持**零安装**（远程侧不 pip 装任何包：jieba 借应用自带、BM25 用 numpy 直算 CSC、精排用标准库 urllib）与**只读**（唯一写操作是 `memory --append`）⑤ 排序链路改动要与 `retriever` 保持同源（限流 → 精排 → meta/chunk 顶替 → blend） | ✅ check_guides ④b |
| 新增一条工作流 | `index.html` 里「六条开箱即用的工作流」的**硬编码列表**会静默变错 —— 这是**正确性问题**，不是文案洁癖 | ✅ check_guides ③ |

当前六份内置文件名固定为：`论文初稿（少年司法版）.md`、`综述（少年司法版）.md`、
`论文初稿（通用暂用版）.md`、`综述.md`、`维护综述库.md`、`跨学科发散与补文献.md`。

### 1.3 wiki 综合层（`wiki_store.py`）

| 你改了 | 必须同步 | 校验 |
|---|---|---|
| `WIKI_MD_SEED` —— wiki 页面规约种子 | ① **必须 bump `SCHEMA_VERSION`**（当前值以 `wiki_store.py` 为准） ② 把当前版和所有旧版 normalized-sha1 留在 `_FACTORY_HASHES` ③ `MCP接入说明.md` 的「信任模型」段 | ✅ check_guides ④（schema + 当前 hash；③仍靠人） |

> ⚠️ **忘了 bump `SCHEMA_VERSION` 会静默让老库永远收到过期规约。** 这是本项目最阴的一个坑：
> 不报错、不告警，只是所有老用户的 wiki 规约永远停在旧版。

### 1.4 前端指引（`web/`）

| 你改了 | 必须同步 | 校验 |
|---|---|---|
| 新增/改动 UI 功能（页签、按钮、流程） | ① `index.html` `#home-guide`(:87) 八章；② `#ag-guide`(:350) 十章；③ `app.js` `agentGuideCard()`(:1207) 四步图；④ 主仓库 `README.md`；⑤ 独立教程仓库 `DrinkTea905/paper-piggy-guide` 的相关章节。前两项是产品内两份指引，连同两个 GitHub 发布面必须逐处检查并报告 | ❌ 人肉（用 §3 的 checklist） |
| 在长期指引中补充新功能说明 | 内容必须归入它所属的步骤或章节；**禁止在指引标题下顶置“新增功能 / 本次更新 / 特别提醒”式横幅**。版本变化写 `CHANGELOG.md`，长期指引只描述当前完整流程 | ✅ check_guides ⑧ |
| 首启向导流程 | `index.html` `.wizard-steps`(:786) + `app.js` `renderStep1`(:3494) ~ `renderStep5`(:4025) + `src/README.md` 的「第一次使用」段 | ❌ 人肉 |
| 「🧹 清空并从头重建索引」(`#sec-rebuild` + `POST /index/reset`，破坏性、须 confirm) | 设置页就地说明是主文案；**动它必对齐 `backup.py` 的 CORE/INDEX「移哪些·保留哪些」口径**；破坏性操作要在指引里提示"先备份" | ❌ 人肉 |
| 顶栏自动更新徽标（`#up-badge`、设置页 `#up-autocheck`、`app.js renderUpdateBadge()`） | 两个 localStorage 键 `localkb.autoUpdateCheck`(默认开)/`localkb.updateDismissed`(按版本忽略)；文案要与「知识库自动更新」明确**区分**（同名不同物，见 CHANGELOG v1.0.1 提醒） | ❌ 人肉 |
| 全文格式 / 提取 / OCR 状态或文案 | 格式清单与优先级只改 `document_formats.py`；同步来源扫描、`deep_extract_status.VALID_STATUSES`、`server.py` 状态下发、`app.js` 徽标/进度/重试文案，以及 `#home-guide` / `#ag-guide`。PDF 的 `ocr_pending` 必须能进入深索；各种 `missing_* / invalid_* / ocr_failed` 是阻塞终态 | ✅ 五格式与 OCR 单测覆盖核心；UI 文案仍需人肉 |

### 1.5 其它

专用浏览器的关联唯一源为 `dedicated_browser.CONFIG_FILE`，窗口打开、HTTP/MCP/CLI 发现和
`browser_devtools_mcp.py` 启动桥必须保持同源。修改时覆盖首次校验后保存、明确旧关联沿用、
端口/资料目录冲突、并发与窗口标签复用、开发隔离、后台不聚焦、设置重置保留，以及独立任务中的完整浏览器资料备份和缓存排除。
不得把用户的浏览器路径或调试端口写入出厂配置，也不得在升级安装时写用户关联。

备份任务、归档与恢复分别由 `backup_jobs.py`、`backup_archive.py`、`backup_restore.py` 管理；
`backup_sources.py` 约束来源，`backup_runner.py` 同步执行，`backup_api.py` 只做界面适配。
Windows 组件和同批多卷快照由 `backup_service.py` / `backup_vss.py` 实现。
修改这些链路时，必须验证热备 WAL、一份包独立恢复、失败保留成功版本、按 ledger 归属清理、
登录补跑、关闭应用后执行、进程退出保护及 `backup_jobs/restore-gate.json` 中断恢复门禁。
Zotero profile 和专用浏览器资料会包含敏感配置；不自动解密、同步或接管其他来源。
旧格式包只通过兼容转换进入新恢复队列，旧自动线程不得重新启用。

顶栏“备份”通过 `GET /backup2/summary` 读取本机两类任务的持久状态，只读摘要不得创建默认任务、
推进计划、探测源目录或遍历校验备份包，也不得触发组件更新。失败标记来自未解决的任务失败记录，
不能因打开页面、取消或重试清除，也不能用恢复成功冒充备份成功；成功附带清理警告与状态读取失败
分别显示提醒和未知。修改入口或状态归并时同步四处指引，并验证关闭设置时的提示、失败定位、
同会话提示去重、刷新失败保留已知错误及未保存表单保护。

新备份的物理分包与逻辑恢复点分开：`backup_parts.py` 管理普通 ZIP 套组及文件片段，
`backup_archive.py` 继续校验原文件和跨包数据库，`backup_restore.py` 只从已验证的完整套组还原。
每个最终 ZIP 实际大小上限为5,000,000,000字节；`backup_jobs.py` 按整套登记、保留及清理，
不得把分包数当作恢复点数，旧单 ZIP 格式也继续参与本任务的原有轮换。
修改此链路须验证真实文件字节上限、单个超大文件重组、任意包入口、缺包/混包/坏包拒绝、
全部分包成功前不删旧、整套清理中断恢复，以及格式1/2兼容；同步完整组件模块清单及签名载荷。

| 你改了 | 必须同步 | 校验 |
|---|---|---|
| 全类型文献评价、客观标签或四档映射 | `source_rules.py` + `grading_svc.py` + `journal_grading/`；同步检索/浏览/单篇详情/wiki 来源/MCP 契约与库总览，跑 `test_source_grading*.py` 和 `journal_grading/selftest.py`。新增目录还要登记 `catalog_registry.py` 的来源、上游版本、检查日期 | ✅ 单测 + selftest；UI 文案人肉 |
| **新增期刊目录（catalogs/*.json）** | 四处缺一不可，漏任一处都**静默失效**：① `grading_config.json > catalogs` 注册（`levels` 非空才会被 loader 加载）② 该学科 `disciplines.<学科>.catalogs`（私有目录不加就被 `visible_catalogs` 裁掉）③ `archetypes.<原型>.map` 里为**每个 level** 写一个 `目录.level` token（写了 level 却没 token = 该级别永不生效）④ `resolution.priorityOrder`（**不在这个列表里就永远选不上主档位**，这一条最容易漏）。另登记 `catalog_registry.py`。`field_focus` 是用户裁定目录，排 priorityOrder 首位、优先于含 CLSCI 在内的一切名录，level 认正式与娱乐两套档名 | ✅ `test_source_grading.py` 的 field_focus 三测；③④ 漏了不报错，靠人 |
| `PERSONAL_MAPPING_DEFAULTS`（`grading_svc.py`） | 它**无条件覆盖**引擎结果，值必须与 `grading_config.json > archetypes.A_法学个人.map` 对得上（注意 T1b→顶级、T2/T3→核心、T4/T5→普通的折叠）。改完跑一遍真实题录看四档分布有没有塌成一条带 | ✅ `test_personal_factory_mapping_matches_user_preset` 逐条写死 |
| 检索候选快照 `src/tests/retrieval_candidates_v*.jsonl` | `v1` 是 2026-07-19 冻结基线，`test_retrieval_goldset` 硬断言依赖它，**不要改**；期刊档位变动后另出 `v2/v3…`（同一批候选、只重算 `journal_weight`），用 `retrieval_calibrate.py --candidates` 对比。仓库里没有生成候选快照的脚本，要新采得先补 | ❌ 人肉 |
| 文献页查找与来源筛选 | `server.py /papers(query, source_type, objective_label)` + `mcp_server.py list_sources.source_type` + 文献页题录查找、十二类性质和动态客观标签；分类/状态/排序/分页组合必须一起测 | ✅ `test_source_grading_api.py`；UI 组合人肉 |
| Agent 工作流、伴随手册或出厂定时任务 | 修改 `agent_ws._WF_*`、伴随手册或任务模板后，同步 `_template_specs()`，运行 `agent_ws.py --print-hashes`；普通模板把新 normalized hash 追加到 `_FACTORY_HASHES`，工作流和伴随手册还须把 exact hash 追加到 `_WORKFLOW_FACTORY_EXACT_HASHES`（旧值不删） | ✅ 模板 hash 构建检查 |
| `upgrade_health._IMPLEMENTATION_GROUPS` 内任一索引实现文件 | 先判断题录、切块或向量产物语义是否变化。兼容改动：把新实现指纹和理由登记到当前 `_AUDITED_IMPLEMENTATIONS`；不兼容改动：提升 `CURRENT_INDEX_CONTRACTS` 对应 id，并补迁移、提示和重建/增量测试。禁止用文件哈希直接决定用户是否重建 | ✅ check_guides ⑨ + `test_maintenance.py` |
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

### 2.1 模板升级器与可见合并（✅ 2026-07-17 已闭环）

**曾经的病**：`agent_ws._write_if_absent()` **只在文件不存在时才写**。
后果：你改了旧 `_WF_PAPER` 的文本，**所有已经跑过一次的机器（包括开发机自己）永远收不到新版**——
「功能变更 → 指引同步」这条链上最后一环是断的。

**现在的实现**（`agent_ws.py` 的 `_FACTORY_HASHES` / `_WORKFLOW_FACTORY_EXACT_HASHES` + `_template_specs()` + `_ensure_template()`）：
普通 Agent 模板沿用历史 normalized-sha1；工作流及其自动附带手册另外保存仅统一换行符、保留缩进/行尾空格/空行的 exact-sha1。工作流和手册只有命中 exact 历史指纹才允许静默覆盖；normalized 只保留版本识别兼容性，不能单独授权删除或覆盖。`ensure_scaffold()` 逐份比对磁盘文件：

- 文件不存在 → `created`
- 与**当前**模板一字不差 → `current`（一个字节都不写）
- 命中**历史**出厂版 → 用户没动过 → `upgraded`（旧件先原子移入
  `0_Agent资料库/升级与备份/历史备份/自动升级/`，再独占创建新版）
- 谁都不像 → 用户改过 → `forked`：**保留用户的文件**，把新版集中写入
  `0_Agent资料库/升级与备份/待合并/`，并按原位置镜像层级
- 「项目记忆.md / 工具经验.md / 变更日志.md」这类**用户数据种子**被写过 → `kept`（不产生待合并文件）。
  ⚠ `工具经验.md` 在分区迁移后被换成**出厂原样的指路页**，于是重新落回「没被改过」一侧，后续照常跟着升级——这是有意的：真正的内容已经搬进 `工具经验/<子域>.md`，本体只剩指路。
  `主题档案/` 是**目录**、按需创建、不进 `_template_specs()`，因此既不登记 hash 也不产生待合并文件

旧 `写论文与综述.md` 拆分时还多一道迁移保护：命中从真实 git 历史正文复算的 exact hash 才能自动迁入新的 `综述.md` 并升级；
迁移前先将旧入口原子移入 `升级与备份/历史备份/工作流迁移/` 私有恢复目录，恢复件永久保留且不进入顶层工作流列表；
用户改过的旧文件再从恢复件生成 `升级与备份/待合并/技能/` 中的可见保留件，供人工并入 `综述.md` 或 `论文初稿（通用暂用版）.md`。
可见保留件使用硬链接优先、独占复制回退，POSIX 下也不得覆盖并发创建的同名目标；目标 `综述.md` 已存在时不得覆盖，
最终 exact 校验后的迟到保存不得再经 `unlink` 删除。
更早的 `工作流.md` 只留下 normalized 指纹、无法证明 Markdown 空白未改，因此现在一律改名保留，不再自动删除。

此前 `forked` 只有终端日志，用户看不到。现在 `upgrade_health.py` 把 Agent 模板与 `WIKI.md` 汇总到 Agent 页：
可查看统一差异、复制一份带路径与保护规则的合并任务给 Agent、对当前版本停止提醒，或在自动留
集中历史备份后直接采用新版。MCP 初始化也会把待合并项告诉外部 Agent，禁止它擅自覆盖。
任何已存在待合并文件——即使仍与历史出厂版相同——都只读不改；新版以 `.2.md` 递增，绝不覆盖合并笔记。
主文件和新待合并文件的首次落盘都使用独占创建；历史出厂主文件升级前先原子移入自动恢复备份，且该备份不自动删除，
以承接编辑器在改名后才完成的迟到保存。改名后的读取或创建若失败，升级器必须先以硬链接或独占创建恢复主路径，
不得只留下被列表过滤的备份。若用户或另一进程在检查期间并发创建或保存同名文件，用户文件优先，
出厂版改存受保护的待合并文件；回归测试必须同时覆盖“校验后保存主文件”和“历史待合并文件并发保存”两个窗口。

旧版散落的 `.new*.md`、`.user-backup-*`、`.agent-ws-migration-backup-*` 以及资料库根的内部状态 JSON，
由 `ensure_scaffold()` 在模板升级前逐类迁移：待合并内容进入集中待办区，备份和迁移恢复件进入按原因分类的历史区，
内部状态进入 `data/state/`。每项失败都保留原路径或集中恢复件，不以清洁目录为理由牺牲唯一副本。
完整数据备份把当前主资料、待合并内容和历史备份作为整个 `0_Agent资料库/` 一起保存；恢复时仍是完整工作区。

> ⚠️ **维护 SOP（改模板必做）**：改完任何模板文本后跑
> `build\py312\python.exe src\agent_ws.py --print-hashes`，
> 把标「★ 新版：请追加」的 normalized hash 追加进 `_FACTORY_HASHES`；工作流 exact 区的新版值追加进
> `_WORKFLOW_FACTORY_EXACT_HASHES`（**两张表的旧 hash 一个都别删**）。
> 忘了追加 → 这一版的出厂原样文件在下下版会被误判成「用户改过」→ 用户机器上凭空多出一堆待合并文件。

### 2.2 把 UI 里写死的清单改成动态（低成本，先做这三条）

项目里**已经在用**这个模式了 —— `app.js:2444` 的工具数取的是后端下发的真实值：

```js
const n = (AG.cfg && AG.cfg.tool_count) || AG_TOOLS.length;   // 后端真值优先
```

后端 `GET /agent/mcp-config` 以 `tool_count = len(MCP.TOOLS)` 返回运行时真值。照此办理：

- `AG_TOOLS`（`app.js`，8 条硬编码）、`AG_PROMPTS`（当前 6 条）→ 后续可改由后端 `/agent/config` 下发。渲染只是一行 `.map()`，成本近乎零。
- `index.html` 里硬写的「六条开箱即用的工作流」+ 逐条列名 → 改成读 `0_Agent资料库/技能/` 的实际文件列表。**增删技能时这些位置会静默变错。**
- `app.js` 的事件接线全按 id/class 挂（`wireHomeGuide()`:1248、`wireAgentPage()`:2533），所以模板生成器只要原样输出 id/class，`app.js` 一行都不用改。

> ⛔ **不要**把 `#home-guide` 八章 / `#ag-guide` 十章的中文正文（约 230 行）也做成代码生成 —— 收益低于成本。那部分用 §3 的 checklist 管。

### 2.3 `check_guides.py`（✅ 2026-07-14 已建成，只读校验器）

在 `src/check_guides.py`，已进 `build_bundle.py` 的 DEV_ONLY 名单（不进分发包）。
**`build_bundle.py` 开头会跑它（`verify_guides()`），退出码非 0 直接中止打包**（`--skip-checks` 可临时跳过，正式发版不许跳）。

现在断言这 10 项（编号即输出里的 ①~⑨，其中 ④b 单列）：

1. ① 调 `gen_mcp_doc.main(--check)`（工具表 ↔ `mcp_server.TOOLS`）
2. ② `RESOURCES` + `RESOURCE_TEMPLATES` ↔ `MCP接入说明.md` 的 Resources 表（双向集合比对）；`PROMPTS` ↔ Prompts 表
3. ③ `_WF_*` 数 == `ensure_scaffold` 落盘数 == `_SKILLS_README` 列出数 == `index.html` 第 3 章卡片数 == 正文里的中文数字，且逐个文件名比对；每份工作流都必须有五段强制契约，维护工作流还必须包含“全量审查 / 简单事项直接处理 / 复核 / 全面总结”及统一体检工具
4. ④ `WIKI_MD_SEED` 里写的 `schema vN` == `SCHEMA_VERSION`，且当前 seed hash 已登记
5. ④b `agent_ws._template_specs()` 每一份当前模板 hash 都已登记，六条工作流的 exact 指纹也已登记
6. ⑤ 全源码（.py/.js/.html）只有一处版本字面量（`config.APP_VERSION`）
7. ⑥ 所有 `C.DATA / "xxx"` 落点都在 `backup.py` 的备份分类清单中
8. ⑦ 前端 JS 不得调用浏览器原生 `confirm()` / `alert()`，统一使用应用内对话框
9. ⑧ `#home-guide` / `#ag-guide` 的标题下方、第一章之前不得出现独立 `ag-note`；功能说明必须归入对应章节
10. ⑨ `upgrade_health._IMPLEMENTATION_GROUPS` 的当前实现指纹必须登记在当前稳定产物契约下；未审计变化直接中止打包

**仍未机器化（靠人）**：`ensure_scaffold()` 写的其余文件名（项目记忆.md / 工具经验.md / 变更日志.md /
主题档案/ / 交付说明书模板.md……）是否都在 `_README_RELY` / `_README_OUTPUT` 里被提到——那两份是散文体，
正则误报率高，硬凑不如不做。
要补的话，先把 README 里的文件名用反引号写死，再机器比对。

理由很硬：`index.html:86` 和 `:349` 各有一条注释写着「⚠ 维护铁律：功能更新时这份指引也要同步更新」——
**项目自己都承认是靠人肉纪律，而事实证明它失败了**（工具表漂了 4 个，`localkb://memory` 漏了一整条 Resource）。
打包是发布的必经关口，卡在那里代价最小。

---

## 3. 新增功能时的人肉 checklist

代码写完之后，逐条过：

- [ ] 这个功能，**用户**需要知道吗？→ 改 `#home-guide`(:87) / `#ag-guide`(:350) / 向导
- [ ] 指引里的新增说明是否已归入对应步骤/章节，而不是作为更新横幅顶在标题下面？
- [ ] 这个功能，**AI agent** 需要知道吗？→ 改 `mcp_server` 工具或 `agent_ws` 工作流模板
- [ ] 我改了 agent 模板吗？→ **老用户能收到新版吗？**（§2.1）
- [ ] 我改了工作流吗？→ 五段强制契约还在吗？根入口、MCP 初始化指令和应用内 Agent 教程口径一致吗？
- [ ] 我改了维护链路吗？→ `maintenance.audit_all()`、MCP 工具、HTTP 接口、UI 提示和最终复核是否仍覆盖同一批项目？
- [ ] 我改了 wiki 规约吗？→ **bump SCHEMA_VERSION 了吗？**（§1.3）
- [ ] 我改了 MCP 工具吗？→ 跑 `gen_mcp_doc.py` 了吗？
- [ ] UI 里有没有**硬编码的数量/清单**会因为这次改动而变错？（§2.2）
- [ ] 新增/升级依赖了吗？→ 同步 lock + 第三方声明，并明确首版是否必须完整安装器
- [ ] 改了全文格式/PDF OCR 链路吗？→ 主附件优先级、五格式解析与定位、HTML 排除、混合 PDF、附件缺失、坏文件、OCR 失败、旧状态迁移都测了吗？
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

# PaperPiggy · 论文小猪 —— AI 开发 agent 总纲

> **你是接手这个项目的 AI agent。这份文件是你的唯一入口。**
> 读完它 + [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) + [docs/MAINTENANCE.md](docs/MAINTENANCE.md)，你就具备了动手的全部前提。
> 本文件与任何其他文档冲突时，**以本文件为准**。

---

## 0. 工作规则（最高优先级，先读这一节）

### 0.1 先调研，需要决策的问用户

**任何涉及以下情形的动作，先给「问题 + 2~4 个选项 + 你的推荐 + 一句话理由」，等用户拍板，绝不自作主张执行：**

- 删除数据、删除文件、删除目录
- 改动目录结构
- 花钱（批量 LLM 调用：嵌入 1400+ 篇、期刊分级、SAC 补生成，都是真金白银）
- 外部账号与发布（GitHub 建仓/推送/Release、密钥、云存储）
- 产品取舍（功能怎么做、UI 怎么改、默认值定成什么）

**动手之前先调研。** 不要看到一个文件名就开始改；先把相关链路读通，再动。
这条规则是用户明确要求写进项目指引的，不是建议，是要求。

### 0.2 花钱前先估算并报告

批量 LLM 调用之前，先估算「多少条 × 多少 token × 什么单价 ≈ 多少钱」，报给用户，等确认。
用户用的是**硅基流动**（SiliconFlow）。

### 0.3 `_archive/` 是历史，不是待办

`_archive/` 下的文档全部**已实施完毕**，每份开头都有「⛔ 归档件·请勿执行」戳。
它们是「当时为什么这么决定」的存档。**不要照着它们重跑改造。**
里面有几份写着「把这份文档整段发给一个新对话并从头到尾执行」——**那是给 2026 年 7 月的那次对话的，不是给你的。**

**全仓 grep / 搜索时排除 `_archive/`**，否则你会在过期副本里改错文件。

### 0.4 报告要诚实

跑失败了就说失败并贴输出；跳过了某步就说跳过；只有真正做完并验证过，才说做完了。
不要用「应该可以」「理论上」粉饰未经验证的改动。

---

## 1. 这是什么

**PaperPiggy（论文小猪）** —— Windows 本地知识库桌面应用，面向**法学 / 社科研究者**。

- 把 Zotero 文库（或任意文件夹）里的论文 / 法源 / 报告建成本地索引
- 检索：dense(LanceDB) + BM25 → RRF 融合 → reranker → 期刊权重加成
- **综合层 wiki**：把检索答案沉淀成可持久、可引用、会标记过期（stale）的知识页
- **Agent 层**：通过 MCP（32 个工具）让 Claude Code 等外部 agent 直接操作这个知识库，内置「写论文与综述」「维护综述库」「跨学科发散与补文献」等工作流

全本地运行（也支持 API 模式）。开源，**明文 .py 分发，不编译不混淆**。

**当前阶段：开发已完成，正在做 Windows 打包发布。** 见 [docs/RELEASE.md](docs/RELEASE.md)。

---

## 2. 目录地图

```
<仓库根>\                          ← git 根（本机：D:\Onedrive\AI\知识库应用）
├─ CLAUDE.md                      ← 你正在读的
├─ README.md                      开源门面
├─ LICENSE                        Apache-2.0
├─ CHANGELOG.md
├─ THIRD-PARTY-NOTICES.md         MinGit(GPLv2) / python-build-standalone / 微软 VC redist
├─ .gitignore                     ★ 隐私闸门，改它之前读 docs/MAINTENANCE.md
│
├─ src\                   ★★★ 唯一可以改代码的地方 ★★★
│   ├─ *.py                       约 40 个模块
│   ├─ web\                       前端（index.html + app.js，无构建步骤）
│   ├─ journal_grading\           期刊分级引擎 + catalogs/*.json（引擎必需，进 git）
│   ├─ requirements.txt           声明依赖
│   ├─ requirements.lock          ★ 唯一被实机验证过的依赖组合（pip freeze 产物）
│   ├─ data\                      运行时数据（.gitignore；含真实文献元数据 + API key）
│   ├─ logs\                      运行时日志（.gitignore）
│   └─ 0_Agent交付物\ 0_Agent资料库\   运行时由 agent_ws.ensure_scaffold() 生成（.gitignore）
│
├─ docs\                          活文档（长期维护）
│   ├─ ARCHITECTURE.md            五分钟架构（新 agent 必读）
│   ├─ MAINTENANCE.md             ★「改了 X → 必须同步 Y」映射表
│   ├─ RELEASE.md                 打包与自动更新
│   ├─ 开发\  设计\  assets\
│
├─ _archive\                      ⛔ 历史存档，不是待办，grep 时排除
│   ├─ 2026-07-UX大改造\  2026-07-Agent改造\  2026-07-期刊分级\  2026-07-调研\
│   ├─ 早期设计\
│   └─ 数据快照\                  grading_memo（689 条期刊分级 LLM 结果，花过 API 钱）
│
└─ build\                         ⛔ 不进 git（大二进制）
    ├─ py312\                     嵌入式 Python 3.12.13 + 全部依赖（约 800M）
    │                             ← 既是开发解释器，也是打包素材
    └─ assets\MinGit\             MinGit 2.55（随包分发，供 wiki 版本历史用）
```

### 外部硬依赖（换台机器就断，且 git 里没有）

| 东西 | 位置 | 说明 |
|---|---|---|
| **模型母版** | `D:\00Zotero知识库\rag\data\models\` | ★**唯一母本，勿删**（14G，含 fp32 完整版）。`bge-m3-onnx` + `bge-reranker-v2-m3-onnx`。重新量化要几小时。 |
| **Python 运行时** | `build\py312\` | 不在 git 里。重建法见 [docs/RELEASE.md](docs/RELEASE.md)。 |
| **MinGit** | `build\assets\MinGit\` | 不在 git 里。`fetch_mingit.py` 可重下。 |

---

## 3. 改代码铁律 v2

> **旧铁律（已作废）**：~~先备份 → 只改 src → sync_app.ps1 同步到 LocalKB\app → 在包里验证~~
> 作废原因：分发包 `LocalKB\` 已删除，不再有 `app\` 副本。而且「源码改了忘同步、验证的其实是旧代码」本身就是一类幽灵 bug 的来源。
> **历史教训**：曾经有人直接改 `app\` 里的代码，害惨用户（改动在下次同步时被覆盖）。现在从根上消灭了这个可能——**只有一份代码**。

### 三步

**① 改之前：git**（代替过去的手工备份目录）

```powershell
git switch -c fix/xxx      # 或在 main 上小步 commit
```

**② 只改 `src\`。源码即运行目标，没有副本需要同步。**

**③ 源码态直接跑起来验证**（在仓库根执行）

```powershell
# 模型母本目录（见 §2「外部硬依赖」；换机器就改这一行）
$env:LOCALKB_MODELS = 'D:\00Zotero知识库\rag\data\models'

# 后端 only（浏览器开 http://127.0.0.1:8770）
& .\build\py312\python.exe .\src\server.py

# 完整原生窗口
& .\build\py312\python.exe .\src\launcher.py
```

也可以用 `.claude\launch.json` 里配好的 `localkb-server` / `localkb-app`。

> ⚠️ **`LOCALKB_MODELS` 必须显式设**。`config.py` 的 `_resolve_models()` 已经不再兜底任何开发机的绝对路径了，不设就会指向一个不存在的 `src\models`，本地嵌入/重排静默失效。

**④ 只有真要出包时才构建**（不再有常驻的测试包）

```powershell
& build\py312\python.exe src\build_bundle.py    # 产物在 src\dist\
```

### 一个反直觉的坑

**不要把 python 运行时放到仓库根目录下的 `python\`。**
`config.py` 判断「是不是分发包」的依据是：`config.py` 的上上级目录里有没有 `run_localkb.py` / `python\python.exe` / `portable.txt`。
一旦仓库根出现 `python\python.exe`，开发态就会被误判成 bundle，`DATA` 改指 `%LOCALAPPDATA%\LocalKB`，**你现有的 `src\data` 开发索引就被抛弃了**。
放在 `build\py312\` 是安全的（同理，别往仓库根放 `run_localkb.py` 或 `portable.txt`）。

---

## 4. 功能变更 → 指引同步

**这是本项目最容易腐烂的地方。** 详细映射表在 **[docs/MAINTENANCE.md](docs/MAINTENANCE.md)**，动 UI / MCP 工具 / Agent 模板之前**必读**。

一句话版本：这个应用有**三套面向不同读者的指引**，改了功能三套都可能要跟着改：

1. **新手指引**（给人看）：`web/index.html` 的 `#home-guide`(:87) 八章 + `#ag-guide`(:350) 十章 + 首启向导(`:786` + `app.js` renderStep*)
2. **agent 指引**（给应用内/外部 AI 看）：`agent_ws.py` 的 `_WF_*` 工作流模板、`_README_*`；`mcp_server.py` 的工具描述；`wiki_store.py` 的 `WIKI_MD_SEED`
3. **开发者文档**（给你和下一个 agent 看）：`MCP接入说明.md`、`docs/`、本文件

**已经发生过的漂移**（引以为戒）：`MCP接入说明.md` 曾写「共 28 个工具」，而 `mcp_server.TOOLS` 实际有 **32** 个；
Resources 表还整条漏掉了 `localkb://memory`。`gen_mcp_doc.py --check` 本来能检出前者，但当时**没有任何地方调用它**。

**现在有护栏了**：`check_guides.py` 会断言这些一致性，且**已接进 `build_bundle.py` —— 校验不过直接中止打包**。
但它只覆盖机器可判定的部分（工具表 / Resources / Prompts / 工作流数量 / wiki schema / 版本字面量），
中文散文体的指引正文仍然靠人。改功能时请走 [docs/MAINTENANCE.md](docs/MAINTENANCE.md) 的 checklist。

---

## 5. 单一事实源（SSOT）

改这些东西时，**只改左边那一处**，其他地方应该是自动派生的：

| 事实 | 唯一源 |
|---|---|
| 版本号 | `config.APP_VERSION` |
| MCP 工具清单 | `mcp_server.TOOLS` → `gen_mcp_doc.py` 生成文档 |
| 依赖 | `requirements.txt` + `requirements.lock`（两个都要改） |
| 期刊评级规则 | `journal_grading/` + `journal_grading/期刊引用权重分级方案.md` |
| wiki 页面规约 | `wiki_store.WIKI_MD_SEED`（改了**必须** bump `SCHEMA_VERSION`，见 MAINTENANCE） |
| Agent 工作流 | `agent_ws._WF_*` 常量 |
| 应用图标 | `web/PaperPiggy.png`（`.ico` 由 launcher 运行时生成） |

---

## 6. 踩过的坑（真实事故，别再踩一遍）

- **硅基流动余额为 0 时，连免费的 bge-m3 都会 403（code 30001）**。`user/info` 返回的余额字段不含赠送额度，看着有钱其实没有。充 ¥1 即恢复。`.cn` 和 `.com` 账号不互通。
- **前端 `jpost()` 会吞掉真实错误**：非 2xx 时只读 `detail`/`error`，不读 `msg`，导致真实原因被吞成「/path 400」这种没用的提示。加接口时注意返回字段名。
- **`STATE["mode"]` 是在 `_load_wiki_index()` 之后才设的**，所以依赖 `STATE["mode"]` 的函数在 `_load_wiki_index` 内部会静默失效。改用 `"tbl" in M` 来判断。**日志打印「成功」不等于真的成功。**
- **wiki 的 stale 降权曾经是纸糊的**：reranker 分数尺度是 0~10+，而当时的 penalty 只有 0.05/0.5，等于没有。加惩罚项之前，先量一下真实分数的尺度。
- **OneDrive 目录下曾有过 Write 静默失败**（视为已解决）。写完关键文件后核对一下内容，别盲信写入成功。
- **出厂模板曾经推不动**：旧的 `_write_if_absent` 只在文件不存在时写 —— 改了工作流模板的文本，**所有已经跑过一次的机器（包括开发机自己）永远收不到新版**。已用 hash 比对的升级器替换（`agent_ws.py` 的 `_FACTORY_HASHES` / `_ensure_template`）：出厂原样→静默升级，用户改过→保留原文件并写一份 `.new.md`。
  ⚠️ **改完任何模板文本，必须跑 `python srcgent_ws.py --print-hashes` 把新 hash 追加进 `_FACTORY_HASHES`（旧的一个都别删）**，否则下下版会把这一版的出厂文件误判成「用户改过」，用户机器上凭空多出一堆 `.new.md`。

---

## 7. 已知待办

- **⏰ 权重校准（重要，新对话请主动提醒用户）**：综合排序里顶刊只多 0.375 分，形同虚设。需要用户攒够 20+ 条真实查询做金标集之后再校准。**不要凭感觉调这个权重。**
- **SAC 补生成（backfill）从未真跑过**：会消耗 API 额度且会改真实库，跑之前必须先问用户。
- **`import_only_pdf` 仍然会挡住没有 PDF 的法源条目**（法源常常只有网页/文本，没有 PDF）。
- **打包发布**：见 [docs/RELEASE.md](docs/RELEASE.md)。代码侧已就绪；**卡在需要用户本人做的两件事**：
  ① 建 GitHub 仓库 `DrinkTea905/paper-piggy`（公开）② 把模型传到 Release `models-v1` tag。
  在模型资产上传之前，`models_manifest.json` 里的 URL 全是 404，「首启下模型」实机测试必然失败。
- **国内镜像位是空的**（`models_manifest.json` 的 `mirror_base`）。GitHub 排第一、单次超时 60s，
  意味着国内用户首启要先干等一分钟才 fallback。上线镜像（如 Cloudflare R2）后记得调整顺序。

---

## 8. 改造轮次时间线

每一轮都已实施完毕，设计稿在 `_archive/`，代码在 git 历史里（`git log`，15 个「改造前」快照）。
**看到 `_archive/` 里的方案文档，不要重跑它们。**

| 时间 | 轮次 | 存档 |
|---|---|---|
| 07-08 ~ 07-09 | UX 用户友好化（68 条反馈） | `_archive/2026-07-UX大改造/改进方案.md` |
| 07-09 | UX 第 2 轮（27 点）、doc5 第 5 轮（13 项） | 同上目录 |
| 07-09 ~ 07-10 | 用户友好复审 v2（99 条） | `_archive/.../改进方案-v2-用户友好复审.md` |
| 07-10 | gist 对标改造（Query/Ingest/Lint 三环） | `_archive/2026-07-调研/gist对标审查-2026-07-10.md` |
| 07-12 | UX 第 3 轮、法源/报告权重分级 | `_archive/2026-07-UX大改造/详细设计/` |
| 07-12 ~ 07-13 | 全面调研（35 agent）→ 全量 bug 修复（71 项） | `_archive/2026-07-调研/` |
| 07-13 | 增强轮（wiki 三环扳机 / 法学检索 / MCP 工具） | `_archive/2026-07-调研/增强实施清单-2026-07-13.md` |
| 07-13 | 发布前修复（打包 blocker + 31 major） | git 历史 |
| 07-13 ~ 07-14 | Agent 七点改造（交付物/资料库/定时任务/技能自动装） | `_archive/2026-07-Agent改造/` |
| 07-14 | Agent+Wiki 审查（可信度分层）、SAC 可见化、跨学科发散技能 | git 历史 |
| 07-14 | **本次**：删分发包、归档、建 git、指引本地化 | git 历史 |

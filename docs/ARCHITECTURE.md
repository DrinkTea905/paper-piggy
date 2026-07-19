# PaperPiggy 架构

面向接手本项目的 AI 开发 agent。所有事实都带 `文件:行号` 证据（行号基于 2026-07-14 的源码）。
不确定的地方标了「待核」，请自行验证后再依赖。

**唯一可改的目录是 `src\`，而且 `src\` 就是运行目标——没有常驻的分发副本需要同步**（见 CLAUDE.md §3 铁律 v2）。
开发态直接 `build\py312\python.exe src\launcher.py` 跑验证；**只有真要出包时**才 `build_bundle.py` 把 `src\` 组装成一次性的 `dist\` bundle。
（历史：曾有个 `sync_app.ps1` / 常驻 `LocalKB\app\` 副本，"改了源码忘同步、验证的其实是旧代码"是一类幽灵 bug 之源，已整个删除。`build_bundle.py --sync-only` 参数仍在，但那只是"刷新某个既有 bundle 的 app\"的可选手段，**不是日常开发步骤**——日常只改 `src\` 并直接跑。）

---

## 0. 一句话定位

Python + FastAPI 后端 + pywebview 原生窗口 + 原生 JS 前端（无构建步骤）的本地文献 RAG 桌面应用，
面向法学/社科研究者：Zotero/文件夹 → 三档渐进索引 → 混合检索 → 之上叠一层持久 wiki（综合层）+ MCP Agent 工作流。

---

## 1. 进程模型

三个可执行入口，**互不为父子的常规关系**，但都会按需拉起同一个 `server.py`：

| 进程 | 文件 | 角色 |
|---|---|---|
| **server** | `server.py` | FastAPI + uvicorn，监听 `127.0.0.1:8770`。检索/索引/wiki/agent 全部业务逻辑。**唯一持有索引与模型的进程** |
| **launcher** | `launcher.py` | pywebview 原生窗口（标题 `PaperPiggy`）。子进程方式起 server，再开窗口指向 `http://127.0.0.1:8770` |
| **MCP server** | `mcp_server.py` | stdio JSON-RPC 2.0，由 Claude Code / Codex **独立拉起**。它自己也会在需要时 Popen 一个 server.py |

### 端口

`config.py:160-164`：`DAEMON_HOST=127.0.0.1`、`DAEMON_PORT=8770`、`DAEMON_URL` 由二者拼出。
8770 是刻意避开另一套知识库 daemon 的 8765（`config.py:9`）。

### 谁拉起谁

- `启动.bat` → `pythonw.exe` → `launcher.py`（分发版是 `run_localkb.py` → `launcher.main()`，见 `build_bundle.py:192-224`）。
- `launcher.main()`（`launcher.py:216`）：
  1. `server_running()` 探活（`launcher.py:60`，走 `/health` 且校验响应确实来自 LocalKB，`launcher.py:42-57`）；
  2. 若端口被**非** LocalKB 程序占用 → 弹 MessageBox 报错退出（`launcher.py:222-227`）；
  3. 否则 `subprocess.Popen([sys.executable, APP/server.py])`，`CREATE_NO_WINDOW`，stdout/stderr 重定向到 `LOGS/server.log`（`launcher.py:228-232`，日志轮转见 `:79-103`）；
  4. 轮询最多 60 次 × 1s 等 `/health`（`launcher.py:234-242`）；
  5. `webview.create_window("PaperPiggy", DAEMON_URL, …, js_api=_JsApi())` + `webview.start()` 阻塞（`launcher.py:267-271`）。
- `mcp_server.ensure_up()`（`mcp_server.py:204-222`）：`/health` 有应答就放行；否则 `Popen(server.py)` 并最多等 120s。
  注意注释里的教训：判活用「服务是否应答」而不是「索引 ready」，否则空库会重复起进程（`mcp_server.py:205-208`）。
- `localkb.py`（CLI）也有同款 `ensure_up`（`localkb.py:33`）。

### 关窗时怎么退

`launcher.py:271-276`：`webview.start()` 返回（= 窗口关闭）后，如果 server 是**本次 launcher 起的**（`proc` 非 None）就 `proc.terminate()`。
即「关窗 = 退出应用」。两个例外：

- 若 launcher 启动时 server 已在跑（比如是 MCP 先拉起的），`proc is None`，关窗**不会**杀 server —— server 继续常驻。
- pywebview 打不开（缺 WebView2 运行时）→ 回退系统浏览器，**刻意不 terminate**，server 常驻供浏览器访问（`launcher.py:282-297`）。

`server.py` 不会因空闲自行退出（MCP 父进程守护与 launcher 关窗负责进程生命周期）；但检索模型与 BM25
会按“设置 → 检索 → 检索内存”在空闲后释放，默认 10 分钟，server 与轻量库目录仍常驻。

### 前端 → 原生桥

`_JsApi.pick_folder`（`launcher.py:196-213`）暴露成 `window.pywebview.api.pick_folder()`。
**必须住在 launcher 进程**：server 是独立子进程，其 `webview.windows` 恒空，在 server 里调 `create_file_dialog` 永远失败（历史死按钮的根因，注释在 `launcher.py:197-199`）。

---

## 2. 数据落点三分支（最容易踩的坑）

全部逻辑在 `config.py` 顶部，**在任何模块 `import config` 时立刻执行**。

### 2.1 `_bootstrap_bundle_env()`（`config.py:24-51`）

它的存在理由：MCP / CLI 由 Claude Code 直接拉起时不经过 `run_localkb.py`，进程 env 里没有 `LOCALKB_DATA`，
数据会错落到 `app/data`（只读、且自动更新替换 `app/` 时被清掉），造成「MCP 看到空库 + 假 server 占住 8770」的数据脑裂（`config.py:18-23`）。

```
APP = Path(__file__).parent          # config.py:15  源码目录 或 bundle/app
root = APP.parent                    # config.py:27  分发版=bundle/ ；开发机=项目根

is_bundle =  (root/"run_localkb.py").exists()
          or (root/"python"/"python.exe").exists()
          or (root/"portable.txt").exists()          # config.py:28-30
```

| 分支 | 判定 | DATA |
|---|---|---|
| **① 源码态（开发）** | `is_bundle` 为假 → 直接 return | `src\data` |
| **② 分发包 + `portable.txt` + 目录可写** | 安装器版**默认走这条** | `<安装目录>\data` —— 数据与程序同目录，用户可整个装到 `D:\PaperPiggy` |
| **③ 分发包 + `portable.txt` 但目录不可写** | `_writable()` 探测失败（例如用户硬装进 `Program Files`） | 回退 `%LOCALAPPDATA%\PaperPiggy\data`，并打一行提示 —— **不崩** |
| **④ 分发包 无 `portable.txt`** | 用户自己删了那个开关文件 | `%LOCALAPPDATA%\PaperPiggy\data` |

`LOCALKB_HOME` 可覆盖 ③④ 的落点。②③④ 都会 `os.environ.setdefault("LOCALKB_DATA", …)`，
且**已有 `LOCALKB_DATA` 时直接 return，尊重外部设定**。

> **平台**：③④ 的"用户目录"由 `config._user_home()` 按平台决定——Windows=`%LOCALAPPDATA%\PaperPiggy`、
> macOS=`~/Library/Application Support/PaperPiggy`、Linux=`$XDG_DATA_HOME`（v1.0.10 起）。
> Windows 有打包安装器（走 ①②③④ 全链路）；**macOS/Linux 目前只支持从源码运行**（走分支①源码态），
> 见 [MAC-从源码运行.md](../MAC-从源码运行.md)。

> ★ 这段解析是**唯一实现**。`run_localkb.py` 曾经复刻过一份（两处各算各的 = 启动器和 MCP
> 认两个数据目录的漂移源），2026-07-14 已收成薄启动器：它只 `import config` 借道。**别再复制出去。**

DATA 之下的一切派生路径见 `config.py:56-98`：`extracted/ chunks/ lancedb/ bm25/ bm25_meta/ meta/ state/ logs/ wiki/ categories/ pagemap/ summaries/ folder/` 等。
**日志也跟随 DATA**（`config.py:64`，`LOGS = DATA/"logs"`）——放 `app/logs` 会在自动更新替换 `app/` 时丢失，且装到 Program Files 会因不可写而启动即崩。

### 2.2 MODELS 解析链（`config.py:103-122`）

`_resolve_models()` 顺序：

1. 环境变量 `LOCALKB_MODELS`（`config.py:104-106`）；
2. `APP/models`——存在 `bge-m3-onnx` 才算（`config.py:107-109`）；
3. `APP.parent/models`——分发版模型在 bundle 根（`config.py:110-112`）；
4. 环境变量 `LOCALKB_DEV_MODELS`（`config.py:115-119`）；
5. 都没有 → 返回 `APP/models`（可能根本不存在，待首启下载）（`config.py:120`）。

**关键：没有硬编码的开发机兜底路径了**（`config.py:113-114` 明写「不再裸写某台开发机的绝对路径」）。
实测源码树下既无 `src\models` 也无 `知识库应用\models`，所以**源码态跑本地模型必须显式设 `LOCALKB_MODELS`（或 `LOCALKB_DEV_MODELS`）**，否则 MODELS 指向一个不存在的目录，本地 ONNX 嵌入/重排会加载失败。
（绕开办法：设置里把 backend 切成 `api`，走 SiliconFlow 的 bge-m3 / reranker，不用本地模型。）

`config.py:125-130` 只 mkdir 自有目录，**绝不 mkdir MODELS**。
`config.py:133-136` 把 `HF_HOME` 指向 MODELS 并强制 `HF_HUB_OFFLINE=1`。

`build_bundle.py:214-218` 与 `config.py:46-49` 的模型选择规则一致：包内有 `models/bge-m3-onnx/model_quantized.onnx` 就用包内，否则用 `HOME/models`。

---

## 3. 三档渐进索引

编排入口 `build_all.py`（`--stage light|semantic|deep|all|folder|deep_prepare|deep_embed`，`build_all.py:20-51`）。
每档都断点续跑，反复跑 = 增量。

| 档 | 脚本 | 做什么 | 产出 | 代价 |
|---|---|---|---|---|
| **L 即时档** | `index_light.py` | 直读 `zotero.sqlite`（或 folder 的 `meta_cache`）→ 题录 → jieba 分词 → bm25 | `data/meta/papers.jsonl`（`index_light.py:202`）、`data/bm25_meta/` + `bm25_meta_ids.json`（`:210-211`）、`stats_cache.json`（`:216`）、`index_manifest.json`（`:229`）、`jieba_legal_dict.txt`（`:100`） | **秒级，0 嵌入 0 token**（`index_light.py:3`） |
| **S 语义档** | `index_semantic.py` | 对 `papers.jsonl` 中未嵌入的篇做 bge-m3 嵌入 → LanceDB 表 `row_type="meta"` 行 → 重建主 bm25 | LanceDB `chunks` 表的 meta 行（`index_semantic.py:28-45`）、`data/bm25/`（`:126-127`）、进度 `state/meta_embedded.txt` | **约 1-2 分钟**（`index_semantic.py:3`） |
| **F 深索档** | `extract.py` → `chunk.py` → `embed_index.py` → `page_map.py` | 逐页读 PDF；空白页本地 OCR → 父子块切分 → 嵌入入表（`row_type="chunk"`）+ 重建 bm25 → 印刷页码映射 | `data/extracted/*.json`、`data/chunks/*.json`、LanceDB chunk 行、`data/pagemap/*.json`、进度 `state/embedded_keys.txt`、提取状态 `state/deep_extract_status.json` | **数小时**；扫描页另耗本机 CPU，开了 SAC 还有每篇 1 次 LLM 调用 |

`build_all.py:39` 定义 `DEEP = [EXTRACT, CHUNK, EMBED, PAGEMAP]`；
`:43-44` 把深索拆成 `deep_prepare`（extract+chunk）与 `deep_embed`（embed+page_map），
好让「Agent 写摘要」插在两者之间（见 §8）。
`:52` 的 `SOFT = {"收藏夹树","AI 主题","印刷页码映射"}` 是非致命步骤，失败只跳过。

#### PDF 文字层与本地 OCR

`extract.py` 先用 PDFium 读取每一页原生文字；只有文字为空的页才在 300 DPI 下渲染并交给
RapidOCR。PDFium 的打开、取页、渲染与关闭都在全局锁内，OCR 使用锁内复制出的**单页**像素并在锁外
串行执行：不持有整本文档图像、不落临时图片、不联网，也不改写原 PDF。混合 PDF 会保留原生页和 OCR
成功页；仍未识别的页数及错误写进 `deep_extract_status.json`。

状态分为 `ocr_pending / ocr_failed / missing_pdf / invalid_pdf / ok_native / ok_ocr`。
`deep_no_text.txt` 只为旧前端兼容保留 `ocr_failed`；启动和深索前会由
`deep_extract_status.reconcile_legacy()` 幂等迁移旧名单，待 OCR 的篇会重新进入深索候选。
文件夹模式抽题录只调用 `extract._extract_pages(max_pages=2, ocr_mode="off")`，不会为了识别题录意外 OCR 整本。

### 触发与队列（server 侧）

- `POST /index/light`（`server.py:2478`）—— **同步**在 server 进程内 `importlib.reload(index_light)` 后跑，跑完 `R.load_all()`。
- `POST /index/semantic`（`server.py:2787`）、`POST /build`（`server.py:2601`，增量 all/folder）—— 走 `_run_build()`（`server.py:2522`）起 `build_all.py` 子进程。
- `POST /index/deep`（`server.py:2794`）：
  - `scope="keys:k1,k2"` → 进**持久队列** `enqueue_deep`（`server.py:1222`），批大小 `_DEEP_BATCH=50`、防抖 4s、失败重试 3 次（`server.py:1191-1193`），队列落盘 `state/deep_queue.json`，崩溃后 `_q_boot()` 回灌续跑（`server.py:1205`）。
  - `scope="all"` → 整库深索，忙时返回 `{ok:false,busy:true}`。
- 并发保护：`_BUILD_LOCK`（`server.py:30`）把「判 running + 置 True」做成原子，杜绝两个 build 子进程同时写坏 LanceDB。
- 取消：`POST /build/cancel`（`server.py:2623`）→ `_kill_tree`（`server.py:2609`）用 `taskkill /T /F` 杀整棵进程树（只 terminate 会留下继续烧 API 额度的孤儿 worker）。

### 自动更新

`_auto_update_loop`（`server.py:202`，startup 起的 daemon 线程，`server.py:364`）：
按 `settings.auto_update`（默认每天 07:00，`settings.py:30-43`）比对 `_source_signature()`（`server.py:148`，Zotero 走 sqlite+**-wal** 的 mtime，folder 走 PDF 数量+最新 mtime）。
**只跑轻量层+语义，深索永远手动**（`server.py:237`）。

---

## 4. 检索管线（`retriever.py`）

`retriever` 被 `server.py:12` **in-process import**，全局状态在 `STATE`/`M`（`retriever.py:29-30`）。

### 4.1 加载与两种模式（`load_all`，`retriever.py:36`）

- 无表且无 L 档 → `mode=None`，未建库（`:58-61`）。
- 只有 `bm25_meta` → `mode="light"`，启动只读题录目录；`bm25_meta` 首次检索再载入。
- 有 LanceDB 表 → `mode="full"`：启动只打开轻量 LanceDB 表句柄、读取题录/wiki 和行数；embedder、
  reranker、bm25 首次检索才载入。正文与向量始终留在 LanceDB，每次检索只按 `chunk_id` 读取融合后的
  候选详情，不维护全表 `M["records"]`。

`load_all` 顺带热重载 jieba 法律词典（按 mtime，`:41-49`）和 `journal_tiers`（`:50-53`），并载入 wiki index（`_load_wiki_index`，`:126`）。

检索组件生命周期由 `_begin_retrieval()` / `_end_retrieval()` 保护：界面检索、RAG 对话、Agent/MCP 检索及
“找相似”都走同一入口；活动计数大于 0 时绝不释放。`server._retrieval_idle_loop()` 每 5 秒按
`settings.DEFAULT["retrieval"]["idle_unload_min"]` 检查，默认最后一次检索完成 10 分钟后移除 ONNX/API
客户端与 BM25，保留表句柄、题录、wiki 和行数；下次检索自动重载。用户可在“设置 → 检索 → 检索内存”选择
5–120 分钟或 `0`（不自动释放），接口为 `GET/POST /setup/retrieval_memory`。

### 4.2 full 模式管线（`search_full`，`retriever.py:309`）

| 步骤 | 函数 | 行号 |
|---|---|---|
| ① dense 召回（LanceDB cosine，topk=50，白名单时 150） | `dense_search` | `retriever.py:158`（配置 `config.py:150-154`） |
| ② bm25 召回（jieba 分词 + **查询侧同义词扩展**） | `bm25_search` → `_expand_tokens` | `retriever.py:198` / `:167` |
| ③ RRF 融合（k=60） | `rrf` | `retriever.py:205`（`config.py:155`） |
| ④ 按融合 id 从 LanceDB 读取候选详情（明确排除 `vector`）→ 过滤白名单 + 截池（无 keys 时池 ≤64，有 keys 时 ≤128） | `fetch_records` + 内联 | `retriever.py` |
| ⑤ reranker 精排（cross-encoder） | `M["rerank"].scores(...)` | `retriever.py:328` |
| ⑥ 同 key 去重（chunk 顶替 meta；发现型检索 `MAX_PER_KEY=2`，不足时不拿重复段凑满） | 内联 | `retriever.py` |
| ⑦ 权重加成 + 排序 | `_weight_res` → `_apply_sort` | `retriever.py:227` / `:651` |
| ⑧ 组装输出（含 citation、statute_status、wiki 标记） | 内联 | `retriever.py:357-395` |

light 模式走 `search_light`（`retriever.py:547`）：只有 bm25_meta + `_apply_sort`，无 dense/rerank。
统一入口是 `search()`（`retriever.py:673`），负责 `min_weight` 过滤与「多取缓冲再截 topk」（`:684-687`，防被降权的 wiki 行白占名额）。
`max_per_key` 是显式的定向取证逃生口：普通 `search_localkb` / 分类检索保持每篇最多 2 段，
`verify_claim(keys=[...])` 已明确选定来源时可在该篇内取更多证据。这样 Agent 工作流是“先跨文献发现，再定向深读/核验”。

### 4.3 权重加成怎么算（`_weight_res`，`retriever.py:227`）

优先级：**手动改档 > 法源/报告规则 > 期刊分级引擎**。

1. `source_rules.resolve(key, itemtype, title)`（`source_rules.py:149`）——命中即返回；
2. `journal_grading.resolve_journal_weight({journal, issn}, discipline)`（`journal_grading/resolver.py:80`），带进程级 memo（`retriever.py:239-252`）；
3. 都算不出 → `None` → `_apply_sort` 回退旧的离散 `TIER_BONUS`（`config.py:169-177`）。

排序（`_apply_sort`，`retriever.py:651`）三种：`relevance` / `tier` / `blend`（默认 blend，`config.py:168`）。
`blend` 的加成 = `journal_weight × WEIGHT_BONUS_SCALE(0.5)`（`retriever.py:640-649`，`config.py:182`）。

排序分还会被两个**降权**改写（`_effective`，`retriever.py:636`）：
- `_wiki_effective`（`:584`）——综合页降权，见 §5.3；
- `_statute_eff`（`:621`）——已废止法条 ×0.5（`config.py:192`）。

两者都遵守同一条血泪教训：**reranker 分可为负，负分乘 factor 反而是提权**，所以正分乘、负分除（`retriever.py:601-605`、`:632-633`）。

---

## 5. 综合层 wiki（`wiki_store.py` / `wiki_vcs.py`）

定位：文献库之上的**附加缓存**，只写 `DATA/wiki/`，删掉不影响文献库/Zotero（`wiki_store.py:9`）。
`index.json` 是元数据的权威事实源；`.md` 是给人/Obsidian 读的渲染件，且 frontmatter 自足到能重建 index（`_rebuild_index_from_disk`，`wiki_store.py:221`）。

### 5.1 页种（`KIND_DIRS`，`wiki_store.py:119-128`）

| kind | 目录（`config.py:88-95`） | 含义 |
|---|---|---|
| `answer` | `wiki/answers/` | 一次 /chat 问答沉淀下来的综述 |
| `concept` | `wiki/concepts/` | 概念页（按需生成 + 缓存） |
| `topic` | `wiki/topics/` | 主题页（对应 AI 主题聚类的一簇） |
| `digest` | `wiki/digests/` | 资料汇编（带印刷页引注） |
| `outline` | `wiki/outlines/` | 选题框架 / 三级大纲 |
| `entity` | `wiki/entities/` | 实体页：作者/机构/案件/制度 |
| `overview` | `wiki/overviews/` | 总论页：随全库演进的 thesis |

**新增页种时只改 `KIND_DIRS`**，所有遍历都用 `KINDS`（`wiki_store.py:118` 明写这条纪律）。

### 5.2 SCHEMA_VERSION 与 `_FACTORY_HASHES`

- `SCHEMA_VERSION = "v2"`（`wiki_store.py:31`）。`WIKI.md`（= `WIKI_SCHEMA_MD`，`config.py:97`）会被 MCP `initialize` **整篇下发给 agent**（`mcp_server.py:90-107`、`:174-177`），所以它过期 = agent 照旧规约干活。
- `ensure_scaffold()`（`wiki_store.py:131`，server startup 调用于 `server.py:349`）：目录 mkdir + 若 `WIKI.md` 版本旧则升级。
- **`_FACTORY_HASHES`（`wiki_store.py:172-175`）= 各历史出厂 `WIKI.md` 的「去掉所有空白后」sha1 集合**。
  `_looks_untouched()`（`:182`）只有当现有 WIKI.md 与某个出厂版**一字不差**时才返回 True，此时才自动升级并把旧版留档为 `WIKI.v{n}.md`（`:149-162`）；
  一旦用户手改过（哪怕只在末尾追加一行），就**保留用户版本**，只打印提示（`:163-165`）。
  注释 `:170-171` 解释了为什么不能用「含有某几个特征串」来判断。
  **改动 `WIKI_MD_SEED` 时必须把旧 seed 的 normalized-sha1 追加进 `_FACTORY_HASHES`**，否则老用户的自动升级会断掉。
  同款机制在 agent 层也有一份：`agent_ws._LEGACY_WF_HASHES`（`agent_ws.py:353`）。

### 5.3 stale 标记与排序

- `set_stale(page_id, stale, reason)`（`wiki_store.py:791`）、`set_verified`（`:829`，人工核验章）。
- 检索期降权（`retriever._wiki_effective`，`:584`）：
  - stale → `× WIKI_STALE_FACTOR = 0.3`（`config.py:201`）；
  - `kind=="answer"` → `× WIKI_ANSWER_FACTOR = 0.45`（`config.py:205`）——answer 页标题≈用户原问题，reranker 拿 query 对 query 打分天然虚高（实测 7.99 vs 真论文 4.34），不压就是「幻觉复利引擎」；
  - `by_agent` 且 `verified_at` 为空 → 再 `× WIKI_UNVERIFIED_FACTOR = 0.6`（`config.py:210`），可与 answer 折减叠乘（0.45×0.6=0.27）；
  - 其余新鲜页 → 只减 `WIKI_BASE_PENALTY = 0.05`（`config.py:196`）。
- 降权在 **relevance / tier / blend 三种排序下都生效**（`retriever.py:651-670` 的注释：否则 agent 传 `sort=relevance` 就能绕开）。
- `WikiWriteDenied`（`wiki_store.py:21`）：agent 不得覆盖人工核验过的页；人可以覆盖 agent 的页，反之不行。

### 5.4 三环扳机（Query / Ingest / Lint）

| 环 | 触发点 | 代码 |
|---|---|---|
| **Query** | 人点「保存此答案」或 agent 调 `save_synthesis` → `POST /wiki/answer` → `W.save_answer` → 存盘 + 嵌入进表（`retriever.index_wiki_page`） | `server.py:1667-1670`、`wiki_store.py:584`、`retriever.py:486` |
| **Ingest** | 一批深索**成功后**自动算「这批新文献影响了哪些综述页」，结果落 `state/wiki_suggestions.json`（只建议不动手） | 队列批次：`server.py:1284-1288`（`_on_deep_done`）；整库深索：`server.py:2575-2580`（前后 `_deep_keys()` 差集）；Agent 深索：`server.py:2868`。算法 `_wiki_suggest_batch`（`server.py:1320`）→ `W.propose_updates`（`wiki_store.py:1026`） |
| **Lint** | 自动更新循环每轮顺带跑（TTL 24h），结果落 `state/wiki_lint.json` | `_wiki_lint_refresh`（`server.py:181`），调用点 `server.py:226`（**刻意放在 `enabled` 判断之前**——体检零成本，不该被自动更新开关关掉）。算法 `W.lint()`（`wiki_store.py:1095`） |

两个待办文件会被 MCP 挂到高频工具输出的尾部，逼 agent 看见（`mcp_server.py:33-64`）。

### 5.5 `wiki_vcs.py` 与 MinGit

- `_find_git()`（`wiki_vcs.py:49-76`）优先级：**包内 MinGit（`<bundle>/git/{cmd,bin,mingw64/bin}/git.exe`）> 环境变量 `LOCALKB_GIT` > 系统 PATH**。
- 有 git → 真 git 仓库（可 diff/log/回滚）；**没有 git → 自动退回 `.history/<page_id>/<时间戳>.md` 快照**，每页保留 `KEEP_SNAPSHOTS=20` 份（`wiki_vcs.py:22`、`:144-192`，目录 `config.py:98`）。
- 两种后端同一套接口：`snapshot / history / restore / read_at / commit`（`wiki_vcs.py:208/242/335/232`），上层不必关心。`backend()`（`:133`）返回 `"git"` 或 `"snapshot"`。
- 提交身份写死为 PaperPiggy 且关掉 gpgsign（`wiki_vcs.py:28-29`）——分发版机器多半没配 `user.email`，不这么做 `git commit` 直接失败。
- **只版本化 `.md`**，`index.json` 不入库（可由 frontmatter 重建，`wiki_vcs.py:14-15`、`:104`）。
- MinGit 由 `fetch_mingit.py` 在构建期下载塞进包；没有它也不会坏。

---

## 6. Agent 层

### 6.1 `agent_ws.py`——两个人类可读的文件夹

`ensure_scaffold()` 在 server 启动时幂等创建并升级出厂模板。未改过的历史出厂版静默升级；用户改过的文件原样保留并生成 `.new.md` 旁本；合并写回前会留 `user-backup`，不会无提示覆盖用户定制：

```
AGENTS.md / CLAUDE.md          # Agent 根入口：强制先读匹配工作流
0_Agent交付物/          # AGENT_OUTPUT_NAME，config.py:83
  README.md
  定时任务/
0_Agent资料库/          # AGENT_RELY_NAME，config.py:84
  README.md
  AI写综述遵守的规约.md      # 由 _rules_summary_text() 生成，agent_ws.py:315
  记忆/  项目记忆.md（当前真相）、变更日志.md（只增不改）
  技能/  说明.md、写论文与综述.md、维护综述库.md、跨学科发散与补文献.md
  参考格式/ 说明.md
  交付模板/ 交付说明书模板.md
  定时任务/ 说明.md
```

**落点（`base_dir()`）**：一律 `C.DATA.parent`（源码态 = `src\`；安装器版 = **安装目录本身**，如 `D:\PaperPiggy\`；回退时 = `%LOCALAPPDATA%\PaperPiggy\`）。与 folder/zotero 模式**无关**。
若 folder 模式的受管文件夹里已有非空的 `0_Agent资料库`，就跟着它走（避免老用户记忆孤儿化）。
历史坑：落点曾随 folder/zotero 模式漂移，表现为「记忆凭空清零」。

**内置工作流常量**：`_WF_PAPER`（写论文/综述）、`_WF_WIKI`（维护知识库与综述库）、`_WF_DIVERGENCE`（跨学科发散与补文献）。
一个工作流一个 `.md`，agent 中立（Claude Code / Codex 都是读文件夹）。
三份工作流都有“触发条件 / 开工前检查 / 用户决策点 / 完成标准 / 最终报告”强制契约；根入口与 MCP 初始化指令要求 Agent 在命中工作流时先读后做。“维护”会进入统一全量审查，不能把只列待办当作完成。
旧版单文件 `技能/工作流.md` 由 `_migrate_legacy_workflow()`（`:359`）拆分迁移。

**定时任务**：应用**不执行**任务（`_TASKS_README`，`agent_ws.py:152` 明说「本应用不执行任务」）——只登记/展示，定时触发由 agent 自己的调度器（如 Claude Code 的 scheduled-tasks）负责。
server 侧 `GET /agent/tasks`（`server.py:649`）解析 `任务.md` 的 frontmatter，`GET /agent/outputs`（`server.py:693`）列最近交付物。

### 6.2 `mcp_server.py`——给外部 AI 编码助手用

零第三方依赖（纯 stdlib + requests，不装 `mcp` 包，`mcp_server.py:4`）。stdio + newline-delimited JSON-RPC 2.0。

- **39 个 TOOLS**（以 `len(TOOLS)` 为准）。分派在 `do_tool()`，绝大多数是对 server HTTP 端点的薄封装；除原有检索、索引、Wiki、研究和记忆工具外，还包括：
  `list_workflows / read_workflow`（强制工作流入口）、`maintenance_audit`（统一全量体检）、
  `get_template_upgrade_diff / merge_template_upgrade`（安全合并模板）、`submit_agent_summaries`（Agent 摘要质量检查与重嵌入）、`resolve_wiki_suggestion`（建议处理留痕）。
  原有主要工具包括：
  检索类 `search_localkb / list_kb_categories / similar_sources / whats_new / list_sources / get_source_meta / read_source`；
  索引类 `localkb_status / deep_status / deep_index / localkb_build / add_source`；
  wiki 类 `save_synthesis / list_wiki / get_wiki_page / update_wiki_page / mark_stale / set_wiki_links / get_backlinks / lint_wiki / propose_wiki_updates / pending_wiki_updates`；
  研究类 `build_digest / research_outline / suggest_new_sources / export_disclosure / resolve_page / format_citation / locate_quote / verify_claim`；
  记忆类 `read_project_memory / append_project_memory`。
- **4 个 RESOURCES**（`mcp_server.py:610`）：`localkb://schema`（WIKI.md 全文）、`localkb://index`、`localkb://lint`、`localkb://memory`。
  外加 1 个 **RESOURCE_TEMPLATE** `localkb://page/{id}`（`:628`）。
- **3 个 PROMPTS**（`mcp_server.py:635`）= gist 三环的斜杠命令：`ingest-source` / `lint-wiki` / `query-and-file`。
- `initialize` 时下发 `instructions()`（`:174`）= 固定头 + **WIKI.md 全文** + 工作区说明（`_workspace_text`，`:130`，含项目记忆内联）。
- server 版本号取 `config.APP_VERSION`（**全项目唯一版本字面量**，`config.py:19`；`mcp_server.py:1454` 只是引用它），协议版本 `2024-11-05`（`:23`）。
- 前端 Agent 页的接入命令由 `GET /agent/mcp-config`（`server.py:396`）动态吐出（`claude mcp add localkb -- <python> <mcp_server.py>` / mcp.json / codex.toml），**工具数是运行时 `len(MCP.TOOLS)` 读出来的，不写死**（`server.py:416-420`）。
- 文档 `MCP接入说明.md` 的工具表由 `gen_mcp_doc.py` 从 `TOOLS` 生成——**改了 TOOLS 要跑一次**（`gen_mcp_doc.py --check` 可在提交前校验）。

工具清单与数量不得在散文里另建事实源；面向用户的完整表由 `gen_mcp_doc.py` 从 `TOOLS` 自动生成。

### 6.3 技能包 `skills/localkb-paper/`（**已删除**）

早期随源码分发一个 Claude Code 技能包，要用户自己复制到 `.claude/skills/`。已于 2026-07-14 删除：
它与 `agent_ws.py` 的内置工作流（`_WF_PAPER` 等，应用自动写进「0_Agent资料库/技能/」）是**同一条流水线的两份事实源**，只会打架。
现状：技能**不**自动装进 `<cwd>/.claude/skills`（`mcp_server.py:1447-1449`），「0_Agent资料库/技能/」是唯一落点；
`GET /agent/mcp-config` 的 `skill_src_dir` 字段同批删除，前端只用 `/agent/open_folder?which=skills`。

---

## 7. 全类型文献评价

**唯一运行时事实源是 `grading_svc.evaluate_paper()`**。检索、浏览、单篇详情、综述来源和 MCP
都消费同一结果：真实性质 `source_type`、唯一客观标签 `objective_label`、稳定四档
`band=authority|top|core|normal`、显示名、内部细分档/权重、目录命中解释和手动状态。

1. **`source_rules.py`** 识别真实性质。
   - 期刊、书籍、书章、学位论文、法源、案例、标准、报告、数据集、预印本、会议论文、网页等都有稳定代码。
   - 网页、报纸和普通文件允许由可靠内容信号改判为法源、报告或数据集；学术论文标题里出现法名不会被误判。
   - 单篇改档存 `data/tier_overrides.json`；旧 T1～T5 继续读取，新写入使用四档代码。它在评价链最后应用，只改四档与权重，客观标签不变。
2. **`journal_grading/`** 负责期刊客观目录与内部细分权重。
   - `catalogs/*.json` 包含中文目录、ShowJCR JCR2025 的 SSCI/Q1～Q4、SJR、正式 TSSCI 法律学门，
     以及项目内的三大刊、顶尖法评、精选外文权威和台湾个人偏好。
   - `catalog_registry.py` 保留来源 URL、上游提交/版本和检查日期，供开发维护与目录溯源；不在普通用户界面展示维护任务。
   - 引擎内部仍可保留 T1～T5 和未识别解释；对普通接口一律折叠成权威、顶级、核心、普通，不展示第五档或“待确认”档。
3. **`grading_svc.py`** 组合非期刊预设、期刊目录和覆盖项。
   - 书籍/书章=权威；学位论文、法源、案例、标准=顶级；标准法学预设中的报告=核心；权威机构数据=核心；其他来源按任务书预设。
   - `law_personal` 是用户私人定制的法学增强预设，四档映射以 `PERSONAL_MAPPING_DEFAULTS` 为出厂值；`law_personal_fun` 只 canonical alias 到它，完全复用规则、目录、缓存、权重与排序，只改变四档显示名。
   - 目录/性质映射覆盖存 `data/grading_mappings.json`，按学科隔离；只有偏离出厂值的项目才存为自定义。`overview()` 同时返回当前值、出厂值和自定义状态，库总览可逐项恢复；`POST /grading/mapping/reset` 只恢复当前学科的全部映射，不动单篇改档。
   - 映射是运行时评价规则：修改后评价分布、浏览和检索排序立即使用新值，只清分布缓存，不改索引。期刊 memo 与全库分布仍分别存 `grading_memo.json`、`grading_dist.json`；学科切换或单篇改档同样只触发相应缓存重算，不要求重建索引。

**`journal_tiers.py` 与索引里的 `journal_tier` 只作旧库兼容**，不再是普通页面的主标签源。

---

## 8. SAC（深索摘要，`sac.py`）

**是什么**：用 LLM 给每篇文献生成 ~150 字中文摘要，作为**嵌入前缀**提升检索召回（`sac.py:3-7`）。
存 `data/summaries/summaries.json`，键是 `safe_name(stem)`。

**跟 `embed_index` 的关系**（这是它唯一的消费点）：

- `embed_index.load_summaries()` 经 `sac.load_valid()` 读取：只有通过质量闸门的摘要才会成为嵌入前缀；
- 若 `sac.enabled()` 为真，**先给本轮待嵌入且缺摘要的篇生成摘要**（`embed_index.py:104-124` → `sac.ensure_for`，`sac.py:142`）；
- 嵌入时拼前缀：`embed_texts = [f"{summ}\n\n{c['text']}" for c in chunks]`（`embed_index.py:145-150`）。
  **摘要只进「嵌入文本」，存表的 `text` 仍是原文**（展示/重排/BM25 用的都是原文）。

**三种 generator**（`settings.sac_conf()`，`settings.py:177-185`，字段 `generator ∈ server|agent|off`，老配置按 `enabled` 迁移）：

- `server` —— 服务端用 API key 自动生成（`sac.enabled()` 仅在此档为真，`sac.py:63-68`）；
- `agent` —— **服务端不生成**，摘要由 Agent 经 `POST /index/deep_agent`（`server.py:2896`）写进 `summaries.json`（`sac.write_summaries`，`sac.py:71`）。流程：`deep_prepare`（切块）→ 返回正文节选给 Agent → Agent 写摘要 → `deep_embed`（带摘要嵌入）。**这一档下应用内点普通深索是不产摘要的**；
- `off` —— 不生成，退化为纯文本嵌入。

**质量闸门**：`sac.validate_summary()` 拦截空/过短、超长失控、问号乱码、连续重复词和“无正文”的过期占位摘要。
异常项不计入 `sac_done`，`/index/status`、`/index/queue`、`/papers` 与 MCP 都会单列异常原因；
Agent 批量提交采用整批原子校验，一篇异常则整批不写。质量检查本身只读，**不会后台擅自重写已有摘要**。
已经写进旧向量的异常前缀也不会被静默改动；只有用户主动修复 / 补生成并完成重嵌入后，旧向量才会被替换。

**修复 / 补生成**：`POST /index/sac_backfill` 给「已深索但摘要缺失或异常」的篇生成摘要并**重新嵌入**（`_sac_backfill_worker` 会先 `_unmark_deep` 再重跑）。
`sac.gen_missing()`（`sac.py:107`）**不受 generator 门控**——用户显式点补生成时无论哪一档都生成。

---

## 9. 前端（`web/`）

`web/index.html`（52KB）+ `web/app.js`（265KB，单文件 IIFE，原生 JS 零依赖）+ `web/style.css`。**无构建步骤**，`server.py:3162` 直接 `app.mount("/static", StaticFiles(web/))`。

六个顶层 tab（`web/index.html:13-20`，对应 `<main id="panel-*">`）：

| tab | panel | app.js 分区 | 说明 |
|---|---|---|---|
| 📊 库总览 | `panel-dashboard`（`:80`） | `app.js:1022` | 仪表盘，全部手绘 SVG/CSS |
| 📚 浏览 | `panel-browse`（`:170`） | `app.js:1401` | 左树（收藏夹/AI 主题）+ 右列表 + 选择性深索 |
| 🤖 Agent | `panel-agent`（`:296`） | `app.js:2414` | MCP 接入引导（本机真实命令 + 工具表 + 交付物/资料库入口 + 定时任务） |
| 📖 综述库 | `panel-wiki`（`:241`） | `app.js:1715`（综合层生成）/ `app.js:2049`（综合页书架） | wiki 页列表、新建综述、打开/重生/删除 |
| 🔍 检索 | `panel-search`（`:52`） | `app.js:608` | 检索 + 结果卡 |
| 💬 对话 | `panel-chat`（`:505`） | `app.js:2845` | SSE 流式 RAG 对话 |

其余分区：设置弹窗（`app.js:2977`，`#settings-modal` @ `index.html:544`）、手动更新（`app.js:3429`）、**首启向导**（`app.js:3479`，`#wizard` @ `index.html:777`）、文件夹模式拖入/选择 PDF 入库（`app.js:4162`）。
另有常驻索引进度条 `#idx-progress` 与深索详情面板 `#deep-panel`（`index.html:27-49`）。

---

## 10. 关键模块速查表

### 运行时必需（server / MCP / 检索链路）

| 文件 | 一行说明 |
|---|---|
| `config.py` | 中央路径与参数。**所有路径改动只改这里**；import 即执行分发包环境引导 |
| `server.py` | FastAPI 全部业务端点（160KB，96 个路由）+ 构建编排 + 深索队列 + 自动更新循环 |
| `launcher.py` | pywebview 原生窗口 + server 子进程生命周期 + 原生目录选择器桥 |
| `mcp_server.py` | stdio MCP server：32 TOOLS / 4 RESOURCES / 1 template / 3 PROMPTS |
| `localkb.py` | CLI（`--status` / `--build` / 查询三件事）；完整能力走 MCP |
| `retriever.py` | 检索核心：dense+bm25 → RRF → rerank → 权重/降权排序。被 server in-process import |
| `embedder.py` | bge-m3 dense 嵌入器（本地 ONNX-INT8） |
| `siliconflow_embedder.py` | OpenAI 兼容 `/embeddings` 的 drop-in 嵌入器（API 模式） |
| `reranker.py` | bge-reranker-v2-m3 重排（**只走 ONNX-INT8，不 import torch**） |
| `settings.py` | `data/settings.json` 读写；backend(local/api)、source(zotero/folder)、sac、auto_update、discipline 的单一事实源 |
| `llm.py` | OpenAI 兼容 chat（流式），内置服务商预设 |
| `textutil.py` | jieba 分词（建库与查询必须同一套）+ clean/safe_name/de_emoji |
| `legal_lexicon.py` | 法学同义词/核心术语出厂词表（纯数据，零依赖、不 import config） |
| `dbutil.py` | LanceDB 谓词工具：由 `safe_name(stem)` 反查真实原始 key（否则删除失效/重复入库） |
| `journal_tiers.py` | 旧离散期刊档（CLSCI/CSSCI/…），兜底用 |
| `journal_grading/` | 期刊权重引擎（loader/identify/normalize/resolver + `catalogs/*.json` + `config/grading_config.json`） |
| `grading_svc.py` | 分级服务层：memo 缓存、全库权重分布、启动预热 |
| `source_rules.py` | 法源/报告规则定档 + 手动改档（优先级高于期刊分级） |
| `wiki_store.py` | 综合层存储：页种、frontmatter、index.json、stale/verified、lint、propose_updates、synthesize |
| `wiki_vcs.py` | wiki 版本历史：有 git 用 git（含包内 MinGit），无 git 退回 `.history` 快照 |
| `agent_ws.py` | 0_Agent交付物 / 0_Agent资料库 脚手架、内置工作流、项目记忆、定时任务约定 |
| `sac.py` | 深索摘要（嵌入前缀）生成/合并/补生成 |
| `research_assistant.py` | 研究助手编排：digest（带页级引注的资料汇编）/ scope（选题+大纲）/ suggest_sources |
| `verify_claim.py` | 论断核验器，三态（supported / not_in_lib / mismatch）。**库内无 ≠ 论断为假** |
| `textloc.py` | 引文定位：一段引文 → 哪篇、PDF 第几页、印刷页第几页 |
| `cite_format.py` | 引注格式引擎（《法学引注手册》子集）——**规则做格式，绝不交 LLM** |
| `page_map.py` | PDF 顺序页 → 期刊印刷页码映射 sidecar（引注的地基） |
| `zotero_source.py` | 直读 `zotero.sqlite`（Zotero 开着时读只读副本） |
| `folder_source.py` | 文件夹模式数据源：扫 PDF + 读 meta_cache（不调 LLM）。**排除 `0_Agent*` 前缀目录** |
| `folder_meta.py` | 文件夹模式：LLM 从 PDF 首 1-2 页抽题录（严格 JSON + 兜底） |
| `folder_ingest.py` | 文件夹模式 build 步骤：并发抽题录写 meta_cache（必须先于 index_light） |
| `models_bootstrap.py` | 首启从云端下载两个 INT8 ONNX 模型（~1.2GB），校验 sha256 + 解压 |
| `updater.py` | **应用自更新**（不同于知识库自动更新）：`server /update/*` 下载增量包 → `launcher.apply_update()` 拉起独立进程换 `app\` 并重启；暂存验证+改名交换+回滚。**只碰 `app\`，从不引用 DATA/models/0_Agent**。顶栏 `#up-badge` 提示新版 |
| `backup.py` | 备份/恢复（zip）+ 清空重建的落点分类源（`CORE/INDEX/NEVER/SPECIAL_IN_DATA`，`check_guides ⑥` 据此断言） |

### 建库管线（子进程，由 `build_all.py` 编排）

| 文件 | 一行说明 |
|---|---|
| `build_all.py` | 建库编排器：`--stage light/semantic/deep/all/folder/deep_prepare/deep_embed` |
| `index_light.py` | L 档：题录 → papers.jsonl + bm25_meta + stats_cache + manifest（秒级，0 嵌入） |
| `index_semantic.py` | S 档：题录嵌入 → LanceDB meta 行 + 重建主 bm25（1-2 分钟） |
| `extract.py` | F 档 ①：有 PDF 的篇 → 原生文字 + 空页本地 OCR → `data/extracted/`（ThreadPool，断点续跑） |
| `deep_extract_status.py` | PDF 提取状态 sidecar、旧 `deep_no_text` 迁移与分类计数 |
| `chunk.py` | F 档 ②：逐页文本 → 父子块 `data/chunks/`（child ~500字 / parent = 整页） |
| `embed_index.py` | F 档 ③：块嵌入（可拼 SAC 前缀）→ LanceDB chunk 行 + 重建 bm25 |
| `build_categories.py` | 读 zotero.sqlite 收藏夹树 → `data/categories/zotero_collections.json`（零嵌入，秒级） |
| `build_ai_topics.py` | 全库向量 KMeans 聚类 + jieba 高频词命名 → `data/categories/ai_topics.json`（零 LLM） |

### 开发 / 构建专用（**不进运行时**）

| 文件 | 一行说明 |
|---|---|
| `build_bundle.py` | 组装可分发 bundle（`dist/LocalKB/`：python/ app/ models/ data/ + run_localkb.py/启动.bat/PaperPiggy.vbs）。`--sync-only` 只刷 app/（非日常步骤，见 §11） |
| `pack_models.py` | 打包「瘦模型」资产（`.tar.gz` + `models_manifest.json`，含 sha256），供首启下载 |
| `fetch_mingit.py` | 下载 MinGit 塞进 `<bundle>/git/`，让分发版用户也有真 git（无它则退回快照） |
| `gen_mcp_doc.py` | 从 `mcp_server.TOOLS` 生成 `MCP接入说明.md` 的工具表；`--check` 可校验过期（改 TOOLS 后必跑） |
| `check_guides.py` | 指引 ↔ 代码一致性校验（工具表/Resources/Prompts/工作流数/schema 版本/版本字面量）。`build_bundle.py` 开头会跑它，红了就中止打包 |
| `setup_reranker_onnx.py` | 开发机一次性：导出 reranker → ONNX → INT8 量化 + 验证排序一致性 |
| `import_fulltext.py` | 一次性迁移：把旧 rag 知识库的全文块导进本库（复用向量，省数小时）。旧库路径由 `--rag-lancedb` / `LOCALKB_RAG_LANCEDB` 显式指定 |
| `journal_grading/migrate_legacy.py` / `selftest.py` | 分级引擎的迁移与自检脚本 |
| `启动.bat` | 源码态/分发态的双击入口（找 pythonw → 跑 launcher / run_localkb） |

> 已删除（2026-07-14，开源前清理）：`fix_schema.py`（一次性 LanceDB schema 迁移，无人 import）、
> `sync_app.ps1`（同步进常驻分发目录 `app/`——那个目录已不存在）、`skills/`（见 §6.3）。

### 依赖

`requirements.txt`：fastapi / uvicorn / lancedb>=0.33 / onnxruntime(<2) / transformers(仅 tokenizer) / tokenizers / bm25s / jieba / scikit-learn / pypdfium2 / rapidocr / python-docx / requests / pywebview / pythonnet（按平台分流）。
**无 torch**（reranker 只走 onnxruntime，打包省 526MB）。`requirements.lock` 是冻结版。

---

## 11. 接手前必读的几条铁律

1. **只改 `src\`，源码即运行目标**——开发态直接 `build\py312\python.exe src\launcher.py` 验证，**只有真要出包时**才 `build_bundle.py`。没有常驻分发副本需要同步（`--sync-only` 只是刷新既有 bundle 的可选手段，非日常步骤）。详见 CLAUDE.md §3。
2. **源码态要用本地模型必须显式设 `LOCALKB_MODELS`**（§2.2），否则 MODELS 指向不存在的 `src\models`。
3. **建索引与查询必须用同一个 backend**（local ONNX-INT8 vs API 全精度，向量不一致会掉点）——`settings.py:5-6` 把它列为铁律，backend 写进 `index_manifest.json`。
4. **改 `WIKI_MD_SEED` 要同步追加旧版 sha1 到 `_FACTORY_HASHES`**（§5.2），否则老用户的 WIKI.md 自动升级会静默断掉。
5. **改 `mcp_server.TOOLS` 要跑 `python gen_mcp_doc.py`**，否则 `MCP接入说明.md` 的工具表过期。
6. **给排序加惩罚项前先量真实分数尺度**：reranker 分是 0~10+ 且**可为负**。减法常数（0.05/0.5）在这个尺度下形同虚设；乘法在负分域会反向提权。正确写法看 `retriever._wiki_effective`（`:601-619`）。
7. **wiki 是附加缓存，不是事实源**：`.md` 的 frontmatter 才是权威（能重建 index.json），删 `data/wiki/` 不影响文献库。
8. **agent 写回的页默认不可信**：`by_agent=True` 且未 `verified_at` 的页在检索里被叠乘降权到 0.27。别为了「让 agent 写的页排前面」把这个拆了——那是幻觉复利。

---

## 附：待核清单

- `web/app.js` 265KB 只按分区注释归类，未逐函数核对；表中的分区行号是分区标题所在行。
- `journal_grading/config/grading_config.json` 的档位表/优先级/学科定义未逐字读，只读了 `loader.py`/`resolver.py` 的消费方式。
- `research_assistant.py` / `verify_claim.py` / `textloc.py` / `page_map.py` 只读了模块 docstring 与 server 侧端点签名，内部算法未逐行核对。
- `server.py` 的 96 个路由只清点了签名与关键几处实现，未全部读完。
- `localkb.py:7-8` docstring 写「28 个工具」，与实测 `len(TOOLS)==32` 不符——是文档过期而非代码问题（可顺手修）。

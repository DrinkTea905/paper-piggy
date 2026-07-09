# 本地知识库 LocalKB

把你的 Zotero 文献库变成一个**独立的本地知识库应用**。两种用法：

1. **内置对话** —— 填自己的 LLM API Key（DeepSeek / 硅基流动 / Kimi / 智谱 / OpenAI），基于本地文献问答，回答带页码级引用。
2. **检索 API** —— 任何 agent（Claude Code / Codex / 自写脚本）都能 `POST /search` 直接拿到混合检索结果，**不需要 LLM key**。这就是把知识库变成"任何 agent 都能调的本地检索服务"。

检索质量与你的知识库同款：bge-m3 稠密 + jieba/BM25 词法 → RRF 融合 → bge-reranker 重排 → 期刊分级（CLSCI/CSSCI…）加权、页码级引用。

---

## ⚠️ 与知识库的关系（完全独立、不污染）

- 本项目所有**写入**只落在 `D:\LocalKB\data` 与 `logs`，**绝不写入** `D:\00Zotero知识库\rag`。
- 模型**只读复用**知识库已下好的 ONNX/reranker（省去重下 12GB）。
- 数据源直接读 Zotero 的 `zotero.sqlite`（不依赖 Better BibTeX 导出）。
- 服务端口 **8770**（知识库 daemon 是 8765），两者可同时运行、互不干扰。
- 想彻底独立分发给别人时，把 `config.py` 里 `MODELS` 改成随包路径即可（或用环境变量 `LOCALKB_MODELS`）。

---

## 启动

双击 **`启动.bat`**，浏览器自动打开 `http://127.0.0.1:8770`。
（或命令行：`.venv的python server.py`）

首次加载模型约需 30–80 秒；状态栏显示"就绪 · N 块"即可用。

---

## 用法一：对话

右上「⚙ 设置」填一个 LLM 服务商的 API Key → 切到「💬 对话」提问。
回答**只依据本地文献**，每个论点带 `[编号]` 引用，下方可展开来源原文。
🔒 Key 只存本机（浏览器 localStorage + 本地服务内存），不上传任何第三方（除你选的模型服务商）。

## 用法二：检索 API（给 agent）

```
POST http://127.0.0.1:8770/search
Content-Type: application/json
{"query": "认罪认罚从宽对司法信任的影响", "topk": 8, "sort": "blend"}
```
返回 `results[]`，每条含 `citation / title / author / year / page / journal_tier / tier_rank / score / text(命中段) / context(整页)`。

让 Claude Code / Codex 等 agent 调它检索你的文献库，就像它们现在调 `retrieve.py` 一样，只是变成了标准 HTTP 接口。

`sort`：`blend`(相关+权威，默认) | `relevance`(纯相关) | `tier`(先期刊层级)。

---

## 建库 / 更新（增量）

- 首次：`python build_all.py`（全量；2000 篇约数小时，与知识库同量级）。
- 加了新文献后：UI 点「⟳ 更新知识库」，或命令行 `python build_all.py`。
  直接读 Zotero 的 `zotero.sqlite`（你在 Zotero 里加文献即可）→ **只处理新增**，已入库的跳过（断点续跑）。
- 冒烟测试：`python build_all.py --limit 20`（只处理前 20 篇）。

---

## 文件

| 文件 | 作用 |
|---|---|
| `config.py` | 所有路径/参数（改这一处即可迁移） |
| `extract.py` / `chunk.py` / `embed_index.py` | 建库三步：提取→切块→嵌入+索引 |
| `build_all.py` | 建库编排（增量、断点续跑） |
| `embedder.py` / `reranker.py` / `textutil.py` / `journal_tiers.py` / `dbutil.py` | 引擎（从知识库复制，只读复用模型） |
| `retriever.py` | 检索核心（混合检索+重排） |
| `llm.py` | 对话（OpenAI 兼容，多服务商） |
| `server.py` | web 服务（检索 + 对话 + 建库，一个进程） |
| `web/` | 界面 |
| `data/` | 独立数据（向量库/索引/进度，随便删不影响知识库） |

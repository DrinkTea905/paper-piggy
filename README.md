# 知识库应用（LocalKB）—— 开发项目

把 Zotero 文库变成一个**本地知识库桌面/网页应用**：秒级检索、语义+词法混合、重排、页级引用、
可视化浏览、可对话、可被 agent 接入（MCP/API）。全程本地、隐私不出本机（或选 API 模式省空间）。

> 这是**纯开发项目**（只有源码 + 文档）。**不含**任何知识库数据/模型/索引——那些留在原机器。
> 已建的深索如何复用见 [开发文档/数据格式与深索迁移.md](开发文档/数据格式与深索迁移.md)。

## 目录结构
```
知识库应用\
  LocalKB源码\        应用全部源码（29 个 .py + web/ + docs/ + 构建脚本 + requirements.txt）
  开发文档\
    模型与职责.md          bge / reranker / LLM / 深索 各管什么（含 SiliconFlow 免费模型可行性）
    数据格式与深索迁移.md   表 schema + 向量兼容红线 + 迁移步骤（复用已建深索不重跑）
  调研\
    agent版本调研.md       把知识库改造成 deep-research agent 的可行性调研
```

## 在新机器上继续开发
1. 装 **Python 3.12**。
2. `cd LocalKB源码 && pip install -r requirements.txt`（无 torch，约几百 MB）。
3. **模型二选一**：
   - **最省事（开发推荐）= API 模式**：不用下模型。起应用后在向导选"API 模式"填 SiliconFlow 免费 key。
   - **本地模式**：需 bge-m3 + reranker 的 ONNX-INT8 模型。用 `setup_reranker_onnx.py` 之类导出，或首启向导从云端下载（分发时用）。
4. 跑：`python launcher.py`（原生窗口）或 `python server.py`（只起后端，浏览器开 http://127.0.0.1:8770）。

## 关键脚本
| 脚本 | 作用 |
|---|---|
| `server.py` | FastAPI 后端（检索/对话/浏览/设置/首启向导 API），端口 8770 |
| `launcher.py` | pywebview 原生窗口启动器（关窗即退） |
| `retriever.py` | 检索核心（dense+bm25 混合→RRF→rerank）；`embedder.py`/`reranker.py` 可本地或 API |
| `settings.py` | 后端选择（local/API）持久化 |
| `index_light/semantic.py` `extract/chunk/embed_index.py` `build_all.py` | 三档渐进建库（L 词法即时 / S 语义 / F 全文深索） |
| `zotero_source.py` | 直读 zotero.sqlite（绕过 Better BibTeX，自动探测数据目录） |
| `mcp_server.py` `localkb.py` | agent 接入（零依赖手写 MCP + CLI） |
| `build_bundle.py` `pack_models.py` `models_bootstrap.py` | 打包分发（内嵌 Python + 首启下模型） |

## 打包分发（Windows 独立应用）
`build_bundle.py` 组装 `dist/LocalKB/`（内嵌 CPython + 瘦依赖 + 源码 + 启动器）。
需先放好 `python-build-standalone` 到 `dist/LocalKB/python/` 并 pip 装依赖。
模型走首启云端下载（`pack_models.py` 打成 GitHub Release 资产 + `models_bootstrap.py` 下载）。
数据/模型落 `%LOCALAPPDATA%\LocalKB`（可写、自动更新不丢；放 `portable.txt` 则用包内）。

## 当前状态（最新）
- ✅ 三档索引 / 混合检索 / 重排 / 期刊分级 / 浏览探索 / AI 主题聚类 / 对话 / MCP 接入 全部可用
- ✅ 嵌入+重排砍掉 torch（裸 onnxruntime），可打包
- ✅ 检索引擎可插拔：本地 ONNX ↔ SiliconFlow 免费 API（首启向导二选一）
- ✅ **自动 SAC（`sac.py`）**：深索时用 LLM（默认 SiliconFlow 免费 DeepSeek，key 空则复用 API 后端 key）
  自动给每篇生成 ~150 字摘要当嵌入前缀，全自动无人值守；设置面板有开关（默认关）。
- ✅ 打包链路验证通过（内嵌 Python 原生窗口在真实桌面跑通）
- ✅ **用打包后独立 Python 从零实测**（读 zotero.sqlite→建 L→深索 PDF→检索页级正文全通），
  并修掉 3 个从零才暴露的 bug：S→F 建表 page 列 Null-type、全文模式篇数显示 0、确认内嵌 Python 可重定位。
- 📦 **测试包** `../LocalKB-测试包.zip`（1.3GB，自包含含模型）：拷到新电脑解压→双击 `启动.bat` 从零试。
- ⏳ 待办：模型传 GitHub + 安装器 + 自动更新 + 产品下载页；深索迁移（复用已建索引）；agent 循环（参考 DeerFlow，别搬 LobeChat）。

详细进度见 [开发文档](开发文档/) 与源码里 `docs/`。

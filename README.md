<div align="center">

# 🐷 论文猪 · PaperPiggy

**把你的文献库变成一个真正会用的本地知识库**

秒级混合检索 · 页级引用 · 可持久的综合层 Wiki · 可被 AI Agent 直接操作

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)]()
[![Python](https://img.shields.io/badge/python-3.12-green.svg)]()

</div>

---

## 这是什么

论文猪（代号 LocalKB）是一个 **Windows 桌面应用**，面向**法学 / 社科研究者**。

它把你的 Zotero 文库（或任意一个装满 PDF 的文件夹）建成本地知识库，然后：

- 🔍 **检索得准** —— 向量检索 + BM25 词法检索 → RRF 融合 → 重排序 → 期刊权重加成。中文法学文献专门做过分词与法源识别优化。
- 📖 **引得回去** —— 检索结果精确到**期刊印刷页码**，不是"第 37 个 chunk"，可以直接写进脚注。
- 🧠 **会沉淀** —— 独有的**综合层 Wiki**：把问答结果存成知识页，新文献入库时自动标记"可能过期"，而不是让答案烂在聊天记录里。
- 🤖 **AI 能直接用** —— 通过 MCP 暴露 32 个工具，Claude Code 这类 agent 可以直接读你的文库、写综述、核验引注。内置「写论文与综述」「维护综述库」「跨学科发散补文献」三条工作流。
- 🔒 **全本地** —— 文献和索引不出本机。也支持 API 模式（省磁盘、省内存）。

---

## 安装

> 📦 **发布准备中。** Release 上线后这里会有安装器和便携 zip 的下载链接。

两种包：

| | 说明 |
|---|---|
| **安装器**（推荐） | 体积小。首次启动时按需下载嵌入模型（约 1.2G），API 模式则无需下载。 |
| **便携 zip** | 解压即用，自带模型，适合无网络 / U 盘场景。 |

系统要求：Windows 10/11 64 位。无需预装 Python，无需 VC++ 运行库（都在包里）。

---

## 从源码运行（开发者）

```powershell
git clone https://github.com/DrinkTea905/paper-piggy.git
cd paper-piggy

# 装依赖（建议用 requirements.lock，这是唯一被实机验证过的组合）
python -m venv .venv
.\.venv\Scripts\pip install -r LocalKB源码\requirements.lock

# 本地模式需要 ONNX 模型；API 模式可跳过这一步
$env:LOCALKB_MODELS = 'X:\path\to\models'   # 内含 bge-m3-onnx / bge-reranker-v2-m3-onnx

# 起原生窗口
.\.venv\Scripts\python LocalKB源码\launcher.py

# 或者只起后端，用浏览器开 http://127.0.0.1:8770
.\.venv\Scripts\python LocalKB源码\server.py
```

**AI agent 接手这个项目？** 先读 [CLAUDE.md](CLAUDE.md)，那是给你的入口。

---

## 文档

| | |
|---|---|
| [CLAUDE.md](CLAUDE.md) | **AI 开发 agent 总纲**（工作规则、改代码铁律、踩过的坑） |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 架构：进程模型、三档索引、检索管线、wiki 综合层、Agent 层 |
| [docs/MAINTENANCE.md](docs/MAINTENANCE.md) | 维护：「改了 X → 必须同步 Y」映射表 |
| [docs/RELEASE.md](docs/RELEASE.md) | 打包与自动更新 |
| [LocalKB源码/MCP接入说明.md](LocalKB源码/MCP接入说明.md) | MCP 接入（32 个工具，由代码自动生成） |

---

## 技术栈

Python 3.12 · FastAPI · pywebview（WebView2）· LanceDB · bm25s · ONNX Runtime
· BAAI/bge-m3（嵌入）· BAAI/bge-reranker-v2-m3（重排）· jieba（中文法学词典）· pypdfium2

前端是**无构建步骤**的原生 JS（一个 `index.html` + 一个 `app.js`）。
后端是**明文 `.py`，不编译不混淆** —— 你可以直接改包里的代码。

---

## 许可

[Apache License 2.0](LICENSE)。第三方组件声明见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。

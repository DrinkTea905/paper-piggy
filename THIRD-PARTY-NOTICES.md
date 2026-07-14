# 第三方组件声明

LocalKB（论文猪）本体以 **Apache License 2.0** 发布（见 [LICENSE](LICENSE)）。
发行版（安装器 / 便携 zip）中还包含下列第三方组件，其版权与许可归各自作者所有。

> 维护提示：**新增依赖时必须回来更新这份文件**，并跑一遍许可证扫描
> （见 [docs/MAINTENANCE.md](docs/MAINTENANCE.md)）。
> ⛔ **红线**：不得引入 AGPL 或 Polyform Noncommercial 等与 Apache-2.0 冲突 / 非 OSI 开源的依赖。
> 本项目曾经因为 `pymupdf4llm` 间接拖进 `pymupdf_layout`(Polyform Noncommercial) 而
> **无法合法开源发布**，2026-07 已改用 `pypdfium2` 解决。

---

## 1. 随包分发的二进制

### Git for Windows（MinGit）—— GPLv2

- 用途：为知识库 wiki 提供逐字版本历史（`wiki_vcs.py`）。**可选组件**；缺失时应用自动退回 `.history` 快照机制，功能不受影响。
- 许可：**GNU General Public License v2**
- 源码：<https://github.com/git-for-windows/git>
- 说明：MinGit 以**独立可执行文件**的形式随包分发，通过进程调用（而非链接）使用，属 GPL 所称的「聚合分发」(mere aggregation)，不影响本项目自身的 Apache-2.0 授权。
- **依 GPLv2 §3 的义务**：随发行版附带 GPLv2 全文，并按上述地址提供完整源码获取途径。

### CPython 运行时（python-build-standalone）—— PSF License

- 用途：嵌入式 Python 3.12 解释器（`python/` 目录）。
- 许可：**Python Software Foundation License 2.0**
- 来源：<https://github.com/astral-sh/python-build-standalone>

### 微软 Visual C++ 运行库 —— Microsoft Redistributable

- 文件：`msvcp140.dll`、`msvcp140_1.dll`、`vcruntime140.dll`、`vcruntime140_1.dll`
- 用途：`onnxruntime` 的硬依赖（缺失则本地嵌入/重排在干净机器上直接 `WinError 1114`）。
- 许可：Microsoft Visual C++ Redistributable，依微软可再分发条款随应用分发。

### Microsoft Edge WebView2 —— Microsoft Redistributable

- 用途：桌面窗口渲染（`pywebview` 后端）。安装器在检测到系统缺失时静默安装 Evergreen Runtime。
- 许可：依微软 WebView2 Runtime 分发条款。

---

## 2. 模型（不随安装器分发，首次启动时从 GitHub Release 下载）

| 模型 | 用途 | 许可 |
|---|---|---|
| **BAAI/bge-m3**（ONNX 量化版） | 文本嵌入（dense 检索） | MIT |
| **BAAI/bge-reranker-v2-m3**（ONNX 量化版） | 重排序 | Apache-2.0 |

来源：<https://huggingface.co/BAAI>。API 模式下无需下载这两个模型。

---

## 3. Python 依赖

按许可证归类（完整精确清单见 [`LocalKB源码/requirements.lock`](LocalKB源码/requirements.lock)）：

**Apache-2.0**
`lancedb` · `transformers` · `tokenizers` · `huggingface_hub` · `safetensors` · `requests` · `flatbuffers` · `hf-xet` · `deprecation` · `lance-namespace`

**MIT**
`onnxruntime` · `jieba` · `bm25s` · `python-docx` · `PyYAML` · `rich` · `h11` · `filelock` · `charset-normalizer` · `six` · `proxy_tools` · `bottle` · `annotated-types`

**BSD（2/3-Clause）**
`pypdfium2`（同时含 Apache-2.0；底层 **PDFium** 为 Google 的 BSD-3 项目）· `pywebview` · `lxml` · `httpx` · `httpcore` · `protobuf` · `colorama` · `scikit-learn` · `scipy` · `numpy` · `pandas` 系

**MPL-2.0**（弱 copyleft，文件级；随包分发无需开放本项目源码）
`certifi` · `tqdm`（MPL-2.0 AND MIT）

**双许可，本项目按 BSD 使用**
`bibtexparser`（LGPLv3 **or** BSD）

**其它宽松许可**
`fastapi` · `starlette` · `uvicorn` · `pydantic` · `typer` · `click` · `pyarrow` · `networkx` · `pythonnet` · `clr_loader` 等（MIT/BSD/Apache 系）

---

## 4. 许可证合规检查（发布前必跑）

```bash
# 扫描所有依赖，确认没有 AGPL / Polyform / SSPL / 纯 GPL
build/py312/python.exe -m pip list --format=json | \
  build/py312/python.exe -c "..."   # 见 docs/MAINTENANCE.md
```

红灯条件：出现 `AGPL`、`Polyform`、`Noncommercial`、`SSPL`，或任何非 OSI 认可的许可证。

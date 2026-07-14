# 更新日志

本文件记录用户可见的变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

> 维护约定：**每次发版必须更新本文件**（见 [docs/MAINTENANCE.md](docs/MAINTENANCE.md) 的 checklist）。
> 版本号的唯一事实源是 `config.APP_VERSION`。

---

## [1.0.0] — 2026-07-14

首个公开版本。[下载](https://github.com/DrinkTea905/paper-piggy/releases/tag/v1.0.0)

### 新增
- 项目指引本地化：`CLAUDE.md`（AI agent 总纲）、`docs/ARCHITECTURE.md`、`docs/MAINTENANCE.md`
- 开源三件套：`LICENSE`（Apache-2.0）、`THIRD-PARTY-NOTICES.md`、`CHANGELOG.md`
- git 版本控制（此前靠 15 个手工备份目录，已回放为 git 历史）

### 变更
- **PDF 提取引擎改用 `pypdfium2`**（原 `pymupdf4llm`）。原方案会间接引入 `pymupdf_layout`
  （Polyform Noncommercial：禁止商业使用、非 OSI 开源）与 AGPL 的 PyMuPDF，
  与本项目的 Apache-2.0 冲突，**导致此前根本无法合法开源发布**。
  实测替换后：中文提取字符数逐篇完全相同（无内容损失），文本质量更好
  （原方案会把中文标点错序），单篇提取快 20~100 倍。
- `requirements.txt` 的 `transformers` / `tokenizers` 版本上限修正为与实装一致
  （此前写 `<5` / `<0.22`，而实装是 5.13.0 / 0.22.2，照着重装会得到一个从未验证过的组合）。

### 修复
- **Word 导出静默降级**：`python-docx` 声明在 requirements 却从未装进分发包，
  导致综述导出恒降级为 `.md` 且不报错。
- **干净机上本地模式必崩**：分发包的 Python 运行时缺 `msvcp140.dll` / `msvcp140_1.dll`
  （`onnxruntime` 的硬依赖），在未装 VC++ 2015-2022 的机器上 import 即 `WinError 1114`。
- MCP 工具表漂移：文档写「28 个工具」，代码里实际 32 个（`gen_mcp_doc.py --check`
  本可检出，但从未被调用）。
- `.claude/launch.json` 与 MCP 配置指向已不存在的目录。

### 移除
- 分发包目录 `LocalKB/`（2.1G 测试数据与构建产物）。构建改为从源码 + `build/` 资产生成。

### 打包
- Inno Setup 安装器（177 MB）+ 便携 zip（282 MB）+ updater 用的增量包
- 模型（约 900 MB）走 GitHub Release 按需下载，支持断点续传与 sha256 校验；API 模式无需下载
- 缺 WebView2 时安装器静默安装 Evergreen Runtime
- 自动更新：只替换 `app/`，不碰 Python 运行时与用户数据；用 sha256 清单识别用户改过的文件，不静默覆盖

---

## 历史

2026 年 6~7 月的开发轮次（UX 改造、期刊分级、gist 对标、Agent 层、wiki 综合层等）
未按版本号发布，其设计文档存档于 [`_archive/`](_archive/)，代码演进见 `git log`。

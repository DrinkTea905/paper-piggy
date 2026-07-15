# 更新日志

本文件记录用户可见的变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

> 维护约定：**每次发版必须更新本文件**（见 [docs/MAINTENANCE.md](docs/MAINTENANCE.md) 的 checklist）。
> 版本号的唯一事实源是 `config.APP_VERSION`。

---

## [1.0.1] — 2026-07-15

修复首个版本的两个启动问题，并重定发布形态。[下载](https://github.com/DrinkTea905/paper-piggy/releases/tag/v1.0.1)

### 修复
- **桌面快捷方式点了没反应**：启动器 `PaperPiggy.vbs` 用 `SW_HIDE` 启动子进程，
  被 pywebview 继承成「隐藏窗口」——应用其实每次都启动成功，只是窗口看不见，
  用户每点一次就多一个后台幽灵进程。快捷方式改为直接指向 `pythonw.exe`，绕开 VBS。
- **重复启动**：应用没有单实例保护，双击 N 次会起 N 个进程抢同一端口。
  现在再次启动会把已有窗口拉到前台、自己退出。
- **Agent 模板旁本 `.new.md` 被静默覆盖**：提示用户「对照合并」，用户在旁本里写的
  合并笔记会被下次启动的出厂原文盖掉。现在旁本被改过就另存为 `.new.2.md`，绝不覆盖。

### 变更
- **只发 Inno 安装器，不再出便携 zip**。数据与程序同目录后，便携版「删旧文件夹、
  解压新版」的常规升级姿势会把索引、综述、写好的论文一次性删光；安装器升级不会
  （只覆盖程序本体）。
- **改为用户级安装**（`%LOCALAPPDATA%\Programs\PaperPiggy`，可在向导里改到任意盘），
  安装不再需要管理员权限、不弹 UAC。
- **数据与程序放同一个文件夹**：索引、模型、综述、Agent 交付物全在安装目录内，
  整个文件夹拷走即可换机。安装目录不可写时自动回退到 `%LOCALAPPDATA%\PaperPiggy`。
- 用户数据目录由 `%LOCALAPPDATA%\LocalKB` 改名为 `PaperPiggy`。

### 新增
- **备份与恢复**（设置 → 💾 备份与恢复）：把综述、Agent 写的论文与记忆、收藏夹、
  期刊分级等「丢了就再也没有的东西」打成一个 zip。备份位置可指到 OneDrive／坚果云
  的文件夹实现云备份（同步静态 zip 是安全的，而云盘直接同步向量索引会把它搞坏）。
  可选连向量索引一起打包（换机免重建）；可设自动备份。API 密钥默认不进备份包。

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

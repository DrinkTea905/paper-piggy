# 在 macOS 上从源码运行 PaperPiggy

> 面向**技术型个人使用者**（比如帮朋友装一台）。PaperPiggy 目前只正式发布 Windows 安装器；
> macOS 没有打包好的 `.app`，但整套是纯 Python + 本地网页，**从源码直接跑得起来**。
> 本指南不涉及签名/公证/App Store —— 你在自己机器上跑自己的代码，Gatekeeper 不拦。
>
> ⚠️ 这条路是 v1.0.10 起才在代码里做了跨平台适配（数据落点、Zotero 探测、依赖平台标记、
> 进程组等）。用**这一版或更新**的源码。跑不起来的地方请把报错发回来，我们再补 —— 开发者
> 手上没有 Mac，这份是「照着 Windows 版推导 + 代码已适配」的结果，需要你在真机上验一遍。

## 1. 装前提

- **Python 3.12**（`python3 --version` 看一下；没有就用 [python.org](https://www.python.org/downloads/) 或 `brew install python@3.12`）
- **git**（`git --version`；没有会提示装 Xcode Command Line Tools，装上即可 —— 顺带 wiki 版本历史也靠它）
- **Zotero**（可选）：想让它读你的 Zotero 文库就装；只想丢一个装满 PDF、EPUB、DOCX、Markdown 或 TXT 的文件夹进去则不需要

## 2. 拉代码 + 装依赖

```bash
git clone https://github.com/DrinkTea905/paper-piggy.git
cd paper-piggy

python3 -m venv .venv
source .venv/bin/activate
pip install -r src/requirements.txt
```

> `requirements.txt` 已按平台分流：macOS 会自动装 `pyobjc`（桌面窗口要用），
> **跳过** `pythonnet`（那是 Windows 的 WebView2 桥，Mac 上装不了也用不着）。
> 别用 `requirements.lock` —— 那份是 Windows 实机冻结的、含 Windows-only 的包。

## 3. 选引擎（二选一）

首次启动向导里会让你选：

- **SiliconFlow 云端检索**（省事，推荐先用这个）：填写一个 [SiliconFlow（硅基流动）](https://siliconflow.cn) Key，
  嵌入和重排都走已适配的云端模型，**不用下载本地模型**。检索同时依赖这两类专用接口，
  所以不能直接填写 DeepSeek、Kimi、OpenAI 等普通对话 Key；这些厂商可以用于检索摘要或对话。
  ⚠️ 已适配的检索模型当前免费，但账户余额为 0 时免费模型也可能报 403，建议充 ¥1；以后价格以服务商页面为准。
- **本地模式**：模型跑在你机器上，首启按需下载约 900MB 的 ONNX 模型（`onnxruntime` 有 Mac wheel，
  Apple Silicon / Intel 都支持）。想省下载可以自己把模型放好，然后 `export LOCALKB_MODELS=/路径/models`。

## 4. 跑起来

```bash
# 原生窗口（推荐）
python src/launcher.py

# 或只起后端，浏览器开 http://127.0.0.1:8770
python src/server.py
```

## 5. 你的数据放在哪

macOS 上落在：

```
~/Library/Application Support/PaperPiggy/
├─ data/            索引、综述 wiki、收藏夹、期刊分级、检索摘要…
├─ models/          本地检索使用的模型（云端检索时为空）
├─ 0_Agent交付物/    AI 写的成品
└─ 0_Agent资料库/    AI 的记忆/技能/定时任务
```

想搬到别处：`export LOCALKB_HOME=/你想放的目录`（在跑之前设）。

## 6. 已知会降级/待验证的点（Mac 特有）

这些**不影响核心功能**，但和 Windows 版有差异，请留意并反馈：

- **单实例唤起**：Windows 上重复双击会把已开的窗口拉到前台；Mac 上暂时没做，重复 `python launcher.py`
  会起第二个实例（正常用不太会碰到）。
- **应用内「一键升级」**：那套是给 Windows 安装器版的（下载增量包换 `app\`）。Mac 源码版**请用
  `git pull` 更新**，别点应用内升级。顶栏的「有新版」提示仍会亮（提醒你去 `git pull`）。
- **系统文件管理器**：「打开备份/交付物文件夹」在 Mac 上走 `open`，应该正常。
- **取消整库建索引**：已改成按进程组 `killpg` 杀，能连嵌入子进程一起停 —— 这条**特别帮我验一下**
  （点「停止」后用「活动监视器」看还有没有残留的 python 进程在跑）。
- **窗口打不开 / 报 pyobjc 相关错**：多半是 `pyobjc` 没装全，试 `pip install pyobjc`（完整元包）再跑。

## 7. 接入 AI agent（可选）

应用「🤖 Agent」页会吐出本机真实可用的 `claude mcp add …` 命令（自动用当前 venv 的
python + `src/mcp_server.py`），复制到 Claude Code 等即可接入 32 个工具。

---

**跑通了或卡住了都告诉开发者** —— 尤其第 6 节那几条 Mac 特有行为，是没有 Mac 真机验证过的。

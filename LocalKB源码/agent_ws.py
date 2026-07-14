# -*- coding: utf-8 -*-
"""
Agent 专属工作区：两个人类可读、留在知识库本地的文件夹——
  0_Agent交付物 (Output)：agent 的成品（论文/资料汇编/周报），每主题一子文件夹。
  0_Agent资料库 (Rely)  ：agent 干活要用的东西（记忆/技能/参考格式/交付模板/定时任务）。
落点：
  folder 模式——建在受管文件夹内部（folder_source.scan 已排除 0_Agent* 前缀目录，不入库）。
  zotero 模式——无受管文件夹，落应用数据目录同级 %LOCALAPPDATA%\\LocalKB\\（=C.DATA.parent）。
所有子目录/模板【幂等】创建，**绝不覆盖**用户或 agent 已写过的文件。
理念：换任何 agent（Claude Code / Codex / …），新 agent 读这两个文件夹 + MCP 接入时下发的指令即可无缝接上。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C


def base_dir():
    """两个 0_Agent* 文件夹的落点。folder 模式=受管文件夹内；zotero/未配置=DATA 同级(HOME)。"""
    try:
        import settings as S
        if S.source() == "folder":
            fd = S.folder_dir()
            if fd and Path(fd).exists():
                return Path(fd)
    except Exception:
        pass
    return C.DATA.parent    # zotero/未配置：%LOCALAPPDATA%\LocalKB\ （默认，用户已确认）


def output_dir():    return base_dir() / C.AGENT_OUTPUT_NAME
def rely_dir():      return base_dir() / C.AGENT_RELY_NAME
def memory_dir():    return rely_dir() / "记忆"
def skills_dir():    return rely_dir() / "技能"
def formats_dir():   return rely_dir() / "参考格式"
def templates_dir(): return rely_dir() / "交付模板"
def tasks_dir():     return rely_dir() / "定时任务"


def resolve(which):
    """which=output|rely|skills → 目录 Path（供「打开文件夹」端点）。未知值回资料库根。"""
    return {"output": output_dir, "rely": rely_dir, "skills": skills_dir}.get(which, rely_dir)()


_README_RELY = """# 0_Agent资料库 —— 你的 AI 助手的专属资料库

> 这里放的是「AI 助手干活要用的东西」，不是你的文献本身。它**留在你本机**、**人类可读**；
> 换任何 AI 助手（Claude Code / Codex / …），新助手读这个文件夹就能无缝接上你之前的工作。
> AI 助手会自己维护这里、保持整洁——你随时能进来看它都放了什么。

## 里面有什么

- **记忆/** —— 项目记忆。`项目记忆.md` 记「当前定了什么」（决策 / 偏好 / 进度，保持简短），
  `变更日志.md` 记历史流水账（只增不改）。二者刻意分开，别让记忆膨胀成流水账。
- **技能/** —— AI 助手的技能 / 工作流。（Claude Code 接入时会自动把它装进 `.claude/skills/`；这里是「源」。）
- **参考格式/** —— 论文 / 文书的排版范本。把范本 .docx 放进来，AI 助手就能照着帮你改格式。
- **交付模板/** —— 交付形态模板。改这里 = 改 AI 助手给你产出的样子。
- **定时任务/** —— 定时任务的定义（搜什么、多久一次、成果放哪）。换助手也能照着重建。
- **AI写综述遵守的规约.md** —— AI 往综述库写回时必须遵守的规则摘要（只读+另存、标未核验、不覆盖你核验的、可删、全本地）。完整规约由应用维护，见文件内指路。

## 约定

- 这个文件夹**不会被当成文献索引**（名字以 `0_Agent` 开头的目录已被检索排除）。
- 成品（论文、资料汇编、周报）不放这里，放隔壁 `0_Agent交付物/`。
"""

_README_OUTPUT = """# 0_Agent交付物 —— AI 助手的成品都在这

> AI 助手替你查库、综述、写作的产出都放这里；**留在你本机**。
> 每个主题一个子文件夹，一条流水线：检索原始材料 → 写作方案 → 初稿 → 资料汇编 → 补全清单。

## 约定

- **每个主题一个子文件夹**（不同主题各放各的，别混在根目录）。
- 每个主题夹放一个 `README.md`（用途 / 引注规范 / 与其他材料的关系，模板见资料库/交付模板）。
- `定时任务/` 下放定期任务的成果（如少年司法周报：`周报_日期.md` + `重点摘录.md` 累积台账）。
- 引注纪律：论点带页码、优先引权威期刊、库里没有的标「【待核】」、每个方向列 gaps（缺口）。

## 和你的 AI 助手商量清楚

产出前，和你的助手明确你要的**交付形态**：篇幅、引注风格、要不要 .docx、分成几个文件。
模板在隔壁 `0_Agent资料库/交付模板/`，改模板即改产出样子。
"""

_PROJECT_MEMORY = """# 项目记忆 —— 当前真相

> 这里只记「现在定了什么」：决策、偏好、进度、关键事实。**保持简短。**
> 历史变更请写到同目录 `变更日志.md`，别让这个文件膨胀成流水账。
> AI 助手每次接入会先读这份文件，并可直接更新它（你也可以随手改）。

## 关于我 / 我的偏好
<!-- 例：我是法学研究者，主攻少年司法；引注偏好脚注、CLSCI 优先…… -->

## 当前在做
<!-- 例：正在写「涉罪未成年人分流转处」，产出放 0_Agent交付物/分流转处/ -->

## 已定决策
<!-- 把定下来、不想反复讨论的事记在这，AI 助手就不会每次重新问 -->

## 交付形态约定
<!-- 篇幅 / 引注风格 / 要不要 docx …… -->
"""

_CHANGELOG = """# 变更日志 —— 历史流水账（只增不改，最新在最上）

> AI 助手把每次重要动作 / 结论追加到这里，供回溯。当前真相请看同目录 `项目记忆.md`。

<!-- 示例：
## 2026-07-14
- 建立「分流转处」主题，完成 12 维度检索原始材料。
-->
"""

_DELIVERY_TEMPLATE = """# 交付说明书模板

> AI 助手在每个交付主题夹 `0_Agent交付物/<主题>/README.md` 里照此填写三段。

## 用途
<!-- 这份材料是什么、给谁看、要解决什么问题 -->

## 引注规范
<!-- 引注风格：脚注 / 尾注；页码是否已按官方页核对；未核实项标【待核】 -->

## 与其他材料的关系
<!-- 本主题夹里各文件的关系：检索原始材料 → 写作方案 → 初稿 → 资料汇编 → 补全清单 -->
"""

_FORMATS_README = """# 参考格式

> 把你要的排版范本（.docx）和格式规范放这里，AI 助手就能照着帮你改论文 / 文书格式。
> 例：`论文范本.docx`、`脚注格式说明.md`。
> 注意：AI 助手改 docx 格式时会**保护 Zotero 引注域、不重建文档**，只在样式层做手术。
"""

_TASKS_README = """# 定时任务

> 每个任务一个子文件夹，里面放一个 `任务.md`（人类可读、换 AI 助手也能读、照着在自己日程里重建）。
> **本应用不执行任务**（它不联网、不含大模型）——定时触发由你的 AI 助手负责；应用只登记、展示、把成果入库。

## 任务.md 格式

    ---
    名称: 少年司法周报
    频率: 每周一 08:30
    启用: true
    ---
    搜什么：过去 7 天内中国少年司法领域的新动态（立法政策 / 司法解释与典型案例 / 学术新论文 / 其他）
    输出：写 0_Agent交付物/定时任务/少年司法周报/周报_<日期>.md；要点 append 进同目录 重点摘录.md（最新在最上）
    收尾：把周报并入知识库；依检索到的时效内容更新相关综述(wiki)页；淡季如实标「本周无新增」，不硬凑

## 怎么建

对你的 AI 助手说「帮我建一个每周一早上的少年司法周报定时任务」，它会在这里写好一个 `任务.md`，
并在它自己的日程系统（如 Claude Code 的 scheduled-tasks）里排期。
换了 AI 助手，让新助手读这个文件夹即可照着重建——任务不会因为换助手而丢。
"""


def _rules_summary_text():
    """「AI 写综述遵守的规约」通俗摘要——放资料库供人类一眼看懂 AI 被约束成什么样；
       完整权威规约仍是引擎维护的 data/wiki/WIKI.md（每个接入的 agent 接入时自动收到全文）。"""
    try:
        wiki_md = str(C.WIKI_SCHEMA_MD)
    except Exception:
        wiki_md = "（应用数据目录）/wiki/WIKI.md"
    return f"""# AI 写综述遵守的规约（摘要）

> 这是 AI 助手往你的「综述库(wiki)」写回内容时**必须遵守的规则**的通俗摘要。
> 完整、权威的规约由应用维护，真身在：`{wiki_md}`
> （每个接入的 AI 助手在连接时都会自动收到这份完整规约，无论用 Claude Code 还是别的助手。）

## 五条核心（你的安全底线）

1. **只读检索 + 另存综合**：AI 只读你的文献做检索，把综合结论**另存**成综述页；**绝不改动**你的原始文献 / 索引 / Zotero。
2. **标「🤖 未核验」**：AI 写回的综述页都带未核验标记，检索时会被降权——你一眼看得出哪些是 AI 写的。
3. **不覆盖你核验过的**：你亲自保存 / 核验过的页，AI 不能覆盖（会被拒绝）；发现旧结论被新文献推翻，它只能标「过时」并写明理由，不能抹掉你的结论。
4. **可一键删**：任何 AI 写的综述页，你都能在「📖 综述库」页一键删除。
5. **全程本地**：文献与综述都在你本机，不上传任何服务器。

## 想看完整规约？
打开上面那个路径的 `WIKI.md`；或在「🤖 Agent」页的教程第 8 章「权限与安全」里一键复制它的完整路径。
"""


def _write_if_absent(path, text):
    """只在文件不存在时写（幂等；绝不覆盖用户/agent 已改内容）。"""
    try:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    except Exception:
        pass


def ensure_scaffold():
    """幂等创建两个文件夹的骨架 + README + 记忆/模板。可反复调用；异常吞掉不阻断主流程。"""
    try:
        for d in (output_dir(), output_dir() / "定时任务", rely_dir(),
                  memory_dir(), skills_dir(), formats_dir(), templates_dir(), tasks_dir()):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
        _write_if_absent(output_dir() / "README.md", _README_OUTPUT)
        _write_if_absent(rely_dir() / "README.md", _README_RELY)
        _write_if_absent(memory_dir() / "项目记忆.md", _PROJECT_MEMORY)
        _write_if_absent(memory_dir() / "变更日志.md", _CHANGELOG)
        _write_if_absent(templates_dir() / "交付说明书模板.md", _DELIVERY_TEMPLATE)
        _write_if_absent(formats_dir() / "说明.md", _FORMATS_README)
        _write_if_absent(tasks_dir() / "说明.md", _TASKS_README)
        _write_if_absent(rely_dir() / "AI写综述遵守的规约.md", _rules_summary_text())
    except Exception:
        pass


def paths_info():
    """给前端/agent 用的路径清单（Agent 页展示 + MCP 指令下发）。"""
    return {
        "output_dir": str(output_dir()),
        "rely_dir": str(rely_dir()),
        "memory_file": str(memory_dir() / "项目记忆.md"),
        "skills_dir": str(skills_dir()),
        "formats_dir": str(formats_dir()),
        "templates_dir": str(templates_dir()),
        "tasks_dir": str(tasks_dir()),
    }

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
import sys, hashlib, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C


def base_dir():
    """两个 0_Agent* 文件夹的**稳定**落点。
    历史坑：落点随 folder/zotero 模式漂移——切模式或换受管文件夹，已累积的记忆/技能/定时任务
    会悄悄留在旧位置、新会话读不到，表现为「记忆凭空清零」。修法：
      ① 若任一候选位置已经有内容（资料库目录非空），就跟着它走——绝不让已存的记忆凭空消失、不迁移文件；
      ② 全新安装（无内容）时，一律落到与数据源模式无关的稳定位置 C.DATA.parent
         （%LOCALAPPDATA%\\LocalKB\\），不再默认钻进受管文件夹，从源头消除漂移。"""
    stable = C.DATA.parent
    candidates = [stable]
    try:
        import settings as S
        fd = S.folder_dir()      # 持久化的受管文件夹路径（即便当前非 folder 模式也可能有值=历史落点）
        if fd and Path(fd).exists():
            candidates.append(Path(fd))
    except Exception:
        pass
    for c in candidates:
        try:
            rely = c / C.AGENT_RELY_NAME
            if rely.exists() and any(rely.iterdir()):
                return c         # 已有累积内容：跟着它，避免孤儿化（老 folder 用户的既有内容照旧可用）
        except Exception:
            pass
    return stable                # 全新安装：稳定落点，与 folder/zotero 模式无关


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
- **技能/** —— AI 助手的工作流，**一个工作流一个文件**（详见里面的 `说明.md`）：`写论文与综述.md`、`维护综述库.md`……
  **任何 AI 助手**（Claude Code / Codex / …）读这个文件夹即可照着干。要加新工作流就**新建一个 .md**，别往已有文件里塞。
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
    调度器: claude-code          # 可选：实际排期落在哪（如 Claude Code 的 scheduled-tasks / cron）。换助手照此重建
    上次执行: 2026-07-14         # 可选：AI 每次跑完回写这行——应用据此显示「上次何时跑」，避免「显示启用≠真在跑」
    ---
    搜什么：过去 7 天内中国少年司法领域的新动态（立法政策 / 司法解释与典型案例 / 学术新论文 / 其他）
    输出：写 0_Agent交付物/定时任务/少年司法周报/周报_<日期>.md；要点 append 进同目录 重点摘录.md（最新在最上）
    收尾：把周报要点用 save_synthesis / update_wiki_page 写成/更新综合页(wiki)——时效结论进综合层才能被之后检索复用（这是 wiki 的活水）；
         md 周报本身仅作人类可读台账（放交付物夹即可，不必也无法作为文献进 RAG）；淡季如实标「本周无新增」，不硬凑

## 怎么建

对你的 AI 助手说「帮我建一个每周一早上的少年司法周报定时任务」，它会在这里写好一个 `任务.md`，
并在它自己的日程系统（如 Claude Code 的 scheduled-tasks）里排期。
换了 AI 助手，让新助手读这个文件夹即可照着重建——任务不会因为换助手而丢。
"""


# agent 中立的工作流——**一个工作流一个文件**放进「技能/」。让任何 AI 助手（不止 Claude）读文件夹即得工作流。
# 拆成两条独立工作流 + 一份说明，避免一个文件塞太多、也方便用户/agent 增删单条工作流。
_SKILLS_README = """# 技能 / 工作流

> 这个文件夹放你和 AI 助手约定的**工作流**。**一个工作流一个文件**（一个 .md）——别把多条工作流塞进同一个文件。

## 现有工作流
- `写论文与综述.md` —— 基于文献库写论文 / 综述 / 研究报告的完整流水线（检索→提纲→起草→核验→引注→沉淀）。
- `维护综述库.md` —— 维护综述库(wiki)：更新受影响页 / 建新主题页 / 定期体检。

## 想加一条新工作流？
**新建一个 .md 文件**放这里（如「每周判例梳理.md」「读书笔记整理.md」），写清三件事：
① 什么时候用；② 分几步、每步用哪个工具做什么；③ 注意事项 / 铁律。
之后对任何接入的 AI 助手说「照『XXX.md』做」即可——它会读这个文件夹。**别往已有文件里塞新流程，各自一个文件。**

## 都能改
这里每个文件都是**默认版**，你可以和 AI 助手一起改成自己的习惯；换任何助手，读到的都是你这份定制版。
（Claude Code / Codex / 任意 MCP 客户端都直接读这些 .md，无需安装。）
"""

_WF_PAPER = """# 工作流：基于本地文献库写论文 / 综述 / 研究报告

> **这是默认工作流，你可以改**——和 AI 助手商量着直接编辑本文件（加步骤 / 改引注风格 / 定制交付形态）。
> 一个工作流一个文件：这是「写作」流程；「维护综述库」另见同目录 `维护综述库.md`。
> 所有工具来自 `localkb` MCP。没连上（工具列表看不到 search_localkb 等）先让用户在应用「🤖 Agent」页复制接入命令、
> 新开会话再继续——**没连上时别凭记忆假装做完任何一步**。

## 三条铁律（每步都遵守）
1. **无来源不落笔**：正文每个实质论断都要绑定库内来源（key + 页码）并经 `verify_claim` 核验。
   核不出（not_in_lib）的要么删、要么明确标「作者观点/库外知识」——not_in_lib 不等于论断为假，但绝不许伪装成有出处。
2. **只写综合层、只加不删**：你能写回 wiki（save_synthesis / update_wiki_page）、能收 PDF（add_source），
   但绝不改动文献库 / 索引 / Zotero，绝不覆盖用户人工核验过的页（被拒时用 mark_stale 标脏 + 写理由）。删除只由用户在应用里做。
3. **人工确认闸**：提纲必须经用户确认才动笔；「页码推算」的引注、AI 抽取的题录，都要提醒用户人工核对。

## 流水线
① **意图澄清（最多 3 问）**：研究问题 / 篇幅 / 受众。用户已说清的别重复问，问完即进入检索。
② **迭代检索**：同一问题换 3~5 组措辞各检一轮（概念名/制度名/争点/英文术语）；可先 list_kb_categories 聚焦分类；
   对核心文献用 similar_sources 找同题；对候选精读篇用 get_source_meta 取全貌；whats_new 看新入库；覆盖不足用 suggest_new_sources。
③ **grounded 提纲（人工确认闸）**：research_outline 生成拆解+三级大纲（会写回 wiki、标🤖未核验）；结合②调整、每节标可依托的 key；
   **呈给用户确认，没点头不进④**。
④ **分节起草**：每节先 read_source 精读依托文献（逐页带印刷页码，长文按 next_page 续读），别只凭 220 字片段；
   每个论断后立刻绑定来源〔KEY p.X〕；直接引语必须来自 read_source 的原文。
⑤ **逐条核验**：每个实质论断跑 verify_claim(claim, keys=[本节来源])——supported 留、mismatch 重读改写、not_in_lib 删或标库外；
   每处直接引语跑 locate_quote 确认在原文哪一页。
⑥ **引注排版**：每个引用点 format_citation(key, pdf_page)，页码用⑤核实过的；missing_fields/page_estimated 警告汇总给用户人工核对；
   引领词（参见/见/转引自）由作者自定，工具不代劳。
⑦ **沉淀回 wiki + 落交付物**：成稿/成节后 save_synthesis 或 update_wiki_page 存综合页（sources 填全部依托 key）、set_wiki_links 接进图；
   收尾调 pending_wiki_updates 拉「本轮新读文献影响了哪些既有页」逐页处理；**成品写进 0_Agent交付物/<主题>/**（每主题一子夹+README）。
⑧ **披露提醒**：如需披露 AI 参与，可用 export_disclosure（或 HTTP POST /research/disclosure {page_ids:[...]}）生成《生成式 AI 使用声明》。
"""

_WF_WIKI = """# 工作流：维护综述库（wiki）

> **这是默认工作流，你可以改**——和 AI 助手商量着直接编辑本文件。
> 一个工作流一个文件：这是「维护综述库」流程；「写论文/综述」另见同目录 `写论文与综述.md`。
> 综述库(wiki)是文献之上的综合层：把对文献的理解持久化成带引用、可累积、互链的页面。你是它的维护者。

## 什么时候做
- 深索一批文献后、或接入本库后，工具输出尾部出现「⚠ wiki 维护待办」时。
- 读完一篇新文献后。
- 想给综述库做定期体检时。

## 步骤
1. **拿待办**：`pending_wiki_updates` 拉清单——新文献影响了哪些既有页、哪些是新主题该新建页。
2. **逐页处理**：受影响页 `get_wiki_page` 看结论是否仍成立——被推翻→`mark_stale` 标脏写清理由 + `update_wiki_page` 重写；仍成立→跳过。
3. **建新页**：新主题（作者/机构/案件/制度/学说/概念）先 `read_source` 读原文，再 `update_wiki_page` 建 entity / concept 页。
4. **接图**：`set_wiki_links` 把新页接进已有的图，别留孤儿页。
5. **单篇追查**：读完一篇新文献可 `propose_wiki_updates(key)` 看它触及哪些页（一篇常触及 10-15 页，只改一页多半漏了）。
6. **定期体检**：`lint_wiki` 查孤儿页/过时页/断链/无来源页/缺概念页，照清单修。

## 铁律
- 只写综合层、绝不改文献库/索引/Zotero；不覆盖用户人工核验过的页（被拒用 mark_stale）。删除只由用户在应用里做。
- 每个论断带 [n] 引用、sources 填论文 key；下判断前先 read_source 读原文，别只凭检索片段。
- 矛盾 / 争议只作「未核实」的只读提示，不落成 wiki 断言。
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


# 各历史出厂 技能/工作流.md 的 normalized（去所有空白）sha1。仅当旧文件与某出厂版一字不差时才自动删
# （内容已拆进 写论文与综述.md + 维护综述库.md）；被用户改过则保留、改名提示并入，绝不丢用户的改动。
_LEGACY_WF_HASHES = {
    "58418033f3f47a30830ed73c3c96ac9768b34ada",   # v1（无「可自定义」头）
    "1e99b26c49277780ecd8f6aa4b6584a49abdc027",   # v2（有「可自定义」头）
}


def _migrate_legacy_workflow():
    """旧版把「写论文」和「维护 wiki」两条流程塞在一个 技能/工作流.md 里。现拆成一文件一工作流。
       出厂原样 → 删（内容已拆入新文件）；用户改过 → 改名保留，请他并进新文件。永不抛异常。"""
    try:
        old = skills_dir() / "工作流.md"
        if not old.exists():
            return
        txt = old.read_text(encoding="utf-8")
        h = hashlib.sha1(re.sub(r"\s+", "", txt).encode("utf-8")).hexdigest()
        if h in _LEGACY_WF_HASHES:
            old.unlink()
            print("[agent_ws] 旧 技能/工作流.md 已拆分为 写论文与综述.md + 维护综述库.md（内容不变）",
                  file=sys.stderr, flush=True)
        else:
            keep = skills_dir() / "工作流(你改过的·请并入新文件).md"
            if not keep.exists():
                old.rename(keep)
                print("[agent_ws] 你改过的 技能/工作流.md 已改名保留，请把你的修改并进 写论文与综述.md / 维护综述库.md",
                      file=sys.stderr, flush=True)
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
        _migrate_legacy_workflow()   # 旧单文件 技能/工作流.md → 拆成下面三个文件（一工作流一文件）
        _write_if_absent(skills_dir() / "说明.md", _SKILLS_README)
        _write_if_absent(skills_dir() / "写论文与综述.md", _WF_PAPER)
        _write_if_absent(skills_dir() / "维护综述库.md", _WF_WIKI)
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

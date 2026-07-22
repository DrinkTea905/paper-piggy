# -*- coding: utf-8 -*-
"""
Agent 专属工作区：两个人类可读、留在知识库本地的文件夹——
  0_Agent交付物 (Output)：agent 的成品（论文/资料汇编/周报），每主题一子文件夹。
  0_Agent资料库 (Rely)  ：agent 干活要用的东西（记忆/技能/参考格式/交付模板/定时任务）。
落点（唯一权威是 base_dir()，这段只是它的说明）：
  一律 = C.DATA.parent —— 与 folder / zotero 模式**无关**。
    · 安装器版（数据与程序同目录）→ 安装目录本身，比如 D:\\PaperPiggy\\
    · 安装目录不可写而回退时     → %LOCALAPPDATA%\\PaperPiggy\\
    · 源码开发态                  → 源码目录 src\\
  唯一例外：老用户的受管文件夹里已经有非空的 0_Agent资料库 → 跟着它走（不迁移、不孤儿化）。
  ⚠️ 旧版这里写的是「folder 模式建在受管文件夹内部」—— **那个行为已废止**，
     别照着旧描述推理（folder_source.scan 仍排除 0_Agent* 前缀目录，这一条没变）。
所有子目录/模板【幂等】创建。出厂模板（README/技能/工作流/规约摘要…）会**随版本升级**：
  与某个历史出厂版一字不差（= 用户没改过）→ 静默换成新版；被用户改过 → 原样保留，新版另存 <名>.new.md。
  见下方「出厂模板升级器」注释——**绝不覆盖**用户或 agent 写过一个字的文件。
理念：换任何 agent（Claude Code / Codex / …），新 agent 读这两个文件夹 + MCP 接入时下发的指令即可无缝接上。
"""
import sys, hashlib, re, json, difflib, time, os, shutil
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
- **技能/** —— AI 助手的工作流，**一个工作流一个文件**（详见里面的 `说明.md`）：`写论文与综述.md`、`维护综述库.md`、`跨学科发散与补文献.md`……
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
- `维护综述库.md` —— 只要提到维护就全量审查：模板 / 索引 / 深索 / 摘要 / wiki，简单事项直接处理，最后全面总结。
- `跨学科发散与补文献.md` —— 写作前打开理论视野：把窄题接到法学近学科 / 政治学·心理学等远学科，理出该补的（尤其外文）文献。

## 想加一条新工作流？
**新建一个 .md 文件**放这里（如「每周判例梳理.md」「读书笔记整理.md」），写清三件事：
① 什么时候用；② 分几步、每步用哪个工具做什么；③ 注意事项 / 铁律。
之后对任何接入的 AI 助手说「照『XXX.md』做」即可——它会读这个文件夹。**别往已有文件里塞新流程，各自一个文件。**

## 都能改
这里每个文件都是**默认版**，你可以和 AI 助手一起改成自己的习惯；换任何助手，读到的都是你这份定制版。
（Claude Code / Codex / 任意 MCP 客户端都直接读这些 .md，无需安装。）
"""

_ROOT_AGENTS = """# PaperPiggy Agent 工作入口

## 工作流闸门（最高优先级）

- 用户请求命中 `0_Agent资料库/技能/` 中的现有工作流时，必须先读取并明确声明采用哪一份工作流；未读取不得开始执行或宣布完成。
- 用户只要提到“维护”，一律读取 `维护综述库.md` 并执行全量审查：简单、可逆、无需取舍的事项直接处理；需要付费、删除/重建、外部修复或真实内容取舍时再询问用户。
- 工作结束必须按工作流的“完成标准”复核，并给出全面总结；看到待办却只解释原因，不算完成。
- PaperPiggy 的实时数据与写入一律走 `localkb` MCP；不要凭文件名或模型记忆猜测状态。
"""

_ROOT_CLAUDE = _ROOT_AGENTS.replace("# PaperPiggy Agent 工作入口", "# PaperPiggy Claude 工作入口")

_WF_PAPER = """# 工作流：基于本地文献库写论文 / 综述 / 研究报告

> **这是默认工作流，你可以改**——和 AI 助手商量着直接编辑本文件（加步骤 / 改引注风格 / 定制交付形态）。
> 一个工作流一个文件：这是「写作」流程；「维护综述库」另见同目录 `维护综述库.md`。
> 所有工具来自 `localkb` MCP。没连上（工具列表看不到 search_localkb 等）先让用户在应用「🤖 Agent」页复制接入命令、
> 新开会话再继续——**没连上时别凭记忆假装做完任何一步**。

## 触发条件
- 用户要求写论文、文献综述、研究报告、章节草稿、研究提纲或系统整理某一研究问题时使用。

## 开工前检查
- 先读项目记忆，确认研究主题、用户偏好与既有成果；再确认 localkb 已连接、核心文献是否已深索。
- 开工时明确告诉用户：本次采用《写论文与综述》工作流。

## 三条铁律（每步都遵守）
1. **无来源不落笔**：正文每个实质论断都要绑定库内来源（key + 原文位置）并经 `verify_claim` 核验。
   核不出（not_in_lib）的要么删、要么明确标「作者观点/库外知识」——not_in_lib 不等于论断为假，但绝不许伪装成有出处。
2. **只写综合层、只加不删**：你能写回 wiki（save_synthesis / update_wiki_page）、能收 PDF / EPUB / DOCX / Markdown / TXT（add_source），
   但绝不改动文献库 / 索引 / Zotero，绝不覆盖用户人工核验过的页（被拒时用 mark_stale 标脏 + 写理由）。删除只由用户在应用里做。
3. **人工确认闸**：提纲必须经用户确认才动笔；「页码推算」的引注、AI 抽取的题录，都要提醒用户人工核对。

## 流水线
① **意图澄清（最多 3 问）**：研究问题 / 篇幅 / 受众。用户已说清的别重复问，问完即进入检索。
② **迭代检索（先广后深）**：同一问题换 3~5 组措辞各检一轮（概念名/制度名/争点/英文术语）；可先 list_kb_categories 聚焦分类。
   `search_localkb` 是发现阶段：默认同一篇最多给 2 段，而且不会用重复弱段凑数——条数少不等于失败，先看覆盖了多少篇不同文献。
   对核心文献用 similar_sources 找同题；对候选精读篇用 get_source_meta / read_source 深读；到论断核验阶段再用 verify_claim(keys=[...]) 在选定文献内多段取证。whats_new 看新入库；覆盖不足用 suggest_new_sources。
   **先反向追踪库内参考文献**：确定 3~10 篇核心文献后，先读取其参考文献表并提取作者、题名、年份、期刊/出版社和 DOI；
   合并多篇核心文献重复引用的条目，优先核对这些条目是否已在库内，再用 search_localkb / get_source_meta 深读。
   每条候选必须保留“由哪篇库内核心文献、哪一页或参考文献序号追到”的出处；只有这条链覆盖不足时，才转向库外补文献。
   同等相关时优先保留外文与台湾来源，但不能因此降低相关性、真实性和可核验要求。
③ **grounded 提纲（人工确认闸）**：research_outline 生成拆解+三级大纲（会写回 wiki、标🤖未核验）；结合②调整、每节标可依托的 key；
   **呈给用户确认，没点头不进④**。
④ **分节起草**：每节先 read_source 精读依托文献（PDF 按页并尽量带印刷页码；其他格式按章节/段落/行号，长文按 next_page 续读），别只凭 220 字片段；
   每个论断后立刻绑定来源〔KEY p.X〕；直接引语必须来自 read_source 的原文。
⑤ **逐条核验**：每个实质论断跑 verify_claim(claim, keys=[本节来源])——supported 留、mismatch 重读改写、not_in_lib 删或标库外；
    每处直接引语跑 locate_quote 确认在原文什么位置。
⑥ **引注排版**：每个引用点调用 format_citation；PDF 传核实过的 pdf_page，其他格式传 position + locator；missing_fields/page_estimated 警告汇总给用户人工核对；
   引领词（参见/见/转引自）由作者自定，工具不代劳。
⑦ **沉淀回 wiki + 落交付物**：成稿/成节后 save_synthesis 或 update_wiki_page 存综合页（sources 填全部依托 key）、set_wiki_links 接进图；
   收尾调 pending_wiki_updates 拉「本轮新读文献影响了哪些既有页」逐页处理；**成品写进 0_Agent交付物/<主题>/**（每主题一子夹+README）。
   成品必须增加独立的「文献使用与缺口」一节：逐项说明核心文献实际用于哪个论证、由哪些库内参考文献链追到、
   外文与台湾来源覆盖到什么程度，以及仍缺哪些文献类型或观点。不得把“没检到”写成“学界没有”。
⑧ **披露提醒**：如需披露 AI 参与，可用 export_disclosure（或 HTTP POST /research/disclosure {page_ids:[...]}）生成《生成式 AI 使用声明》。

## 用户决策点
- 研究问题、篇幅或受众不明确时最多问 3 个问题；grounded 提纲必须经用户确认后再起草正文。
- 删除内容、采用库外来源或改变既定引注规则时单独询问。

## 完成标准
- 约定的交付物已落盘；每个实质论断有可追溯来源并完成核验；直接引语已定位；待核项已集中列出；本轮新读文献触发的 wiki 待办已处理。

## 最终报告
- 总结交付物路径、使用来源、核验结果、仍待用户核对的引注/元数据，以及沉淀到 wiki 的内容。
"""

_WF_WIKI = """# 工作流：维护知识库与综述库（wiki）

> **这是默认工作流，你可以改**——和 AI 助手商量着直接编辑本文件。
> 用户说“维护综述库”或“维护 wiki”时仍执行全量审查；范围较窄只影响自动处理优先级，不得跳过健康检查。

## 触发条件
- 用户只要提到“维护”，无论是否说“全面”，都立即采用本工作流。
- 深索一批文献后、工具提示 wiki 待办时，也采用本工作流。

## 开工前检查
1. 明确告诉用户：本次采用《维护知识库与综述库》工作流。
2. 调 `maintenance_audit`，一次盘点模板升级、索引、全文附件深索/PDF OCR、检索摘要、wiki 更新建议和 wiki 体检。
3. 把清单分为：可自动处理、需用户决策、外部阻塞。盘点不是完成，不能只解释待办。

## 执行步骤
1. **模板与工作流**：待合并项先 `get_template_upgrade_diff`。保留用户定制并合入新版，用 `merge_template_upgrade` 写回；只有规则互斥、无法同时保留时才问用户。
2. **索引与深索**：简单事项直接处理，安全重试和增量任务直接继续；清空重建索引、删除数据等破坏性动作必须先问。缺附件、坏文件、PDF OCR 终态列为外部阻塞，不冒充已修复。
3. **检索摘要**：generator=agent 时，异常/缺失摘要由你 `read_source` 读正文、写约 150 字中文检索摘要，再调 `submit_agent_summaries` 质量检查并重嵌入；不得改走服务端摘要 API。generator=server 时先估算数量与费用并询问；generator=off 时尊重设置。
4. **wiki 更新建议**：用 `pending_wiki_updates` 分页拉完全部清单。逐条读来源：更新已有页、把多篇并入有价值的新主题页，或用 `resolve_wiki_suggestion` 记录“无需写入”及理由。数量多、主题杂不是停止理由。
5. **wiki 体检**：`lint_wiki` 查孤儿、过时、断链、无来源、降级页与缺概念页。无来源只代表待补来源/待核验，**不等于过时**；只有新证据明确推翻页内结论时才能 `mark_stale`。
6. **接图与记忆**：新页用 `set_wiki_links` 接入真实相关页面；更新项目记忆与变更日志。每个论断带 [n]，sources 填实际读过的论文 key。
7. **复核**：再次调 `maintenance_audit`。可自动处理项未清零就继续循环，不得提前收工。

## 用户决策点
- 只在付费调用、删除/清空重建、外部文件修复、用户规则之间真实冲突、或内容取舍会显著改变知识结构时询问。
- 能从现有设置和资料判断的，不把选择重新甩给用户；多个决策尽量集中一次问完。

## 完成标准
- 全量审查的每一类都已复核；可自动处理项为 0；所有需决策项已执行用户选择；剩余项全部是明确的外部阻塞并写明下一步。
- 摘要选择“交给 Agent”时，可读取正文的异常/缺失项已由 Agent 生成、通过质量检查并完成重嵌入。
- wiki 建议不再有未说明的“待处理”项；模板不再有能自动合并却未处理的提示。

## 最终报告
- 给出全面总结：检查范围、维护前后数字、实际修改、未修改及原因、外部阻塞与用户后续动作；报告写入 `0_Agent交付物/维护报告_日期.md`。

## 铁律
- 不删除文献、索引、wiki 页或用户文件；不覆盖人工核验页。需要删除只由用户在应用里确认。
- 下判断前先读原文，别只凭检索片段；矛盾/争议未核实前不落成 wiki 断言。
"""

_WF_DIVERGENCE = """# 工作流：跨学科发散与补文献（写作前，打开理论视野）

> **这是默认工作流，你可以改**——和 AI 助手商量着直接编辑本文件。
> 一个工作流一个文件：这是「向外发散」的前置流程；写作在 `写论文与综述.md`、wiki 维护在 `维护综述库.md`。
> 库内工具都来自 `localkb` MCP。没连上（工具列表看不到 search_localkb 等）先让用户在应用「🤖 Agent」页复制接入命令、新开会话再继续——**没连上时别凭记忆假装做完任何一步**。

## 触发条件
- 用户要求拓宽理论视野、跨学科发散、补外文/邻接学科文献，或写作选题陷入原学科循环时使用。

## 开工前检查
- 先读项目记忆和当前写作主题，明确本土问题；开工时声明采用《跨学科发散与补文献》工作流。

## 什么时候用
- 选题初期、或写不动想拓宽视野时；精准索引让你只在原学科打转、agent 也只盯着那个精准的点时。
- **定位**：这是 `写论文与综述.md` 的**上游**——先用它把窄题接到邻近学科、理出该补的（尤其外文）文献，产物（跨学科视角矩阵）再喂给写作流水线。
- 它**不主张研究空白、不主张新颖**（那是另一回事，别把发散联想误当「研究空白」去下结论）。它只主张一件事：**「你的阅读面偏窄——这些邻接学科 / 理论 / 外文经典该纳入视野」**。

## 三条铁律（每步都遵守）
1. **无检索不荐文献 · 库内库外物理隔离**：任何具体文献都必须来自一次**真实检索动作**（库内 `search_localkb`/`similar_sources`，或联网 `WebSearch` + OpenAlex/Crossref 核实）——**先查到、再写出**，绝不凭记忆先列书单再补证。库外 / 外文条目一律进独立的「库外·未核验」区，**绝不对它 `verify_claim`/`format_citation`、绝不落成有出处的 grounded 断言**。
2. **未核实不给精确元数据**：没有逐条联网核实过的文献，**一律不给 DOI / 卷 / 期 / 页 / 确切年份**（这些正是模型错得最狠的字段）；书籍类只给「书名 + 作者 + 大致年代」。「搜到有人提到它」≠「它真实存在」——核实必须命中**权威书目记录本身**（OpenAlex/Crossref 里有这条 work、DOI 能解析），别把泛化网页命中当核实通过。另外：「这篇真存在」与「它真支持你的论点」是两回事，后者必须读原文自证，AI 不代劳。
3. **人工确认闸 · 只加不删**：视角矩阵、外文清单都只是**启发**，哪些进正文由用户定；写回 wiki 只加不删、不覆盖用户人工核验过的页（被拒用 `mark_stale` 标脏写理由）。删除只由用户在应用里做。

## 流水线

① **锚定窄题 + 挑战隐含假设（最多 3 问）**
弄清研究问题，以及它绑定的**本土问题**——外文文献必须服务于一个明确的本土问题（隐性比较法：域外理论是解决本国具体问题的「论据」，不是泛列一份英文书单）。再点出本题现有研究的 2~3 个**隐含假设**：被本学科视为理所当然的前提，常在另一学科里被重构或证伪——这正是跨学科连接的天然抓手（problematization，比单纯「找空白」更出理论深度）。用户已说清的别重复问。

② **库内视野盘点（划出你现有的视野边界）**
`list_kb_categories` 看库里各学科厚薄；对核心文献 `similar_sources` 找同题近邻；`search_localkb` 用**他部门法 + 政治学 / 心理学 / 社会学等学科术语**各换一轮词探底。它在发现阶段默认同一篇最多返回 2 段，也不会用重复弱段凑满 topk；所以要看“命中了多少篇不同文献 / 哪些学科”，不要把返回条数当覆盖度。目的只是圈出「库里已有什么、集中在哪个学科、缺哪个学科」。
（`suggest_new_sources` 从库内脚注挖被引最多但库里缺的**中文**文献，可作「库内驱动的发散源」之一；但它**荐不了外文**——外文靠第 ⑥ 步。）
先选出 3~10 篇与窄题最相关的库内核心文献，读取并解析其参考文献表；抽取作者、题名、年份、期刊/出版社和 DOI，
按 DOI 或规范化题名去重，记录每条候选来自哪篇核心文献以及原文位置或参考文献序号。先用库内检索确认候选是否已经入库，
并优先深读被多篇核心文献共同引用的条目。只有库内反向追踪仍有明确缺口时，才进入库外发现。
同等相关时优先纳入外文与台湾来源；“偏好”不能替代真实性核验，也不能把低相关条目硬塞进清单。

③ **强制过一遍「跨学科视角菜单」（防止只在原学科打转）**
逐项勾选，别只在本学科里绕：
- **法学近学科**：法社会学 / 法经济学 / 法人类学 / 法政治学 / 法律心理学 / 法史 / 法哲学。
- **远学科**：政治学 / 心理学 / 教育学 / 社会学 / 哲学 / 传播学 / 数据科学……**远学科门槛更高**——要么很强要么不进（远距离跨学科盲目追未必更好）。
每个选中的视角，用三招生成「**这个视角会追问什么**」的一组问题：
  - **正当性根基反推**——这项制度靠什么理论才站得住？（把规范论证接到实证学科上）
  - **制度生命周期横切**——从它为何产生、如何运行到为何变迁，各学科分别怎么看？
  - **张力对作引擎**——找出本题最强的一对张力（如少年司法「福利模式 ↔ 正当程序」），两极常各属不同进路，顺着张力挖出对立学科。

④ **成效筛选（把「避免为发散而发散」写成硬门槛）**
每个候选跨学科连接过一张清单再决定去留：
- **结构相似而非表面相似**、有高阶因果 / 系统性关联（Gentner 系统性原则）；
- 与你的**论点直接相关**；
- **无关键异质**（Hesse：本质差异会毁掉类比，如把成年人模型硬套未成年人）。
低分标「牵强」**默认丢弃**（用户可在确认闸手动救回，决定权始终在你）。遵循「非典型组合植根于常规」：**每篇只主推 1~2 个大胆连接、其余保持本学科稳健**，别全面猎奇稀释论证力（Uzzi 的经验规律）。每个保留的连接产出一个**边界概念**（如「未成年人利益最大化」可作法学-心理学-教育学的共用概念），写清「本学科怎么看 / 邻学科怎么看」，确保是真对接、不是贴标签。

⑤ **库内能落地的，走正规 grounded 通道**
对每个视角问题回 `search_localkb` 做跨文献发现；选定来源后再用 `read_source` 深读、`verify_claim(keys=[...])` 在指定文献内多段取证。凡能对上库内来源的论断，supported 才留，再用 `format_citation` 排脚注——与 `写论文与综述.md` 同一套核验 / 引注范式。这部分是「已 grounded」的可靠产物。

⑥ **库外·外文补充（能力自检 → A/B 双轨，防幻觉的关键环）**
先自检当前 agent 有没有联网 / 学术检索工具（WebSearch/WebFetch），**把判定结果显式告诉用户**，再择路：
- **A · 有网（如 Claude Code）**：先检索、后落笔（retrieve-then-generate）。逐条以「标题 + 作者」用 **OpenAlex**（免 key、2.5 亿+ 记录，首选）或 **Crossref** 反查权威书目记录，**命中且元数据吻合才保留**，并附可点达链接（优先 DOI / 开放获取）。核不出的就说「未核到，仅供检索方向」，**绝不用编造填空**。
- **B · 无网（通用 MCP 客户端）**：降级为**只给检索线索**四件套——「作者 / 流派名（外文原名）+ 双语关键词与布尔检索式 + 建议检索库（Google Scholar / HeinOnline / 知网…）+ 一句『为什么与本土问题 X 相关』」，**绝不给具体年 / 卷 / 期 / 页 / DOI**，并强提示逐条人工核。
- **外文护栏**：作者名始终保留**拉丁原名**（防回译出错），中译标「试译」，绝不把某个中译当规范译名直接输出；优先名称固定、易核验、理论价值高的**锚点**（国际公约、奠基判例、经典专著，如 CRC / 北京规则 / In re Gault 之类）。

⑦ **发散追问器（逼出你没想到的）**
回看 ② 的检索结果与 ③ 的菜单，专门追问两类遗漏：「库里被检到、却没进视角矩阵的边角线索」和「菜单里完全没出现、但本题其实相关的邻近学科」。逼出 unknown unknowns，再补一轮 ③④。

⑧ **交付物 + 写回 wiki（两区物理隔离）**
- 标准交付物 = 一张**跨学科视角矩阵**：`视角/学科 × 该视角的追问 × 命中的库内来源(带 key) × 库外待补外文(未核验) × 成效评分 × 边界概念`。**成品落 `0_Agent交付物/<主题>/` + 一个 README**（注明：外文清单由 AI 依世界知识 / 联网线索给出、**未经本库核验**；取得 PDF、EPUB、DOCX、Markdown 或 TXT 后可 `add_source` 收进库、再走 `写论文与综述.md` 的正规核验流程）。
- 写回 wiki 用 `save_synthesis`（kind=`topic`）：页内**强制分两区**——「**库内已覆盖**（带 key、grounded）」与「**库外待补**（外文、未核验、不作断言、不排脚注）」，两区不得混写；`set_wiki_links` 把它接进已有的图。**别用 answer/overview**（会被 `lint_wiki` 当无来源页报警）。
- 成品末尾增加「**文献使用与缺口**」：逐项说明核心文献实际用于哪个视角或论证、参考文献反向追踪链、
  外文与台湾来源覆盖情况、仍缺的学科/地区/文献类型，以及下一轮应如何补证。不得把暂未命中夸大成研究空白。

## 用户决策点
- 视角矩阵和库外补文献清单形成后，由用户决定哪些方向进入正式写作；库外来源未取得并核验原文前不作实质断言。

## 完成标准
- 视角菜单已完整走查并按成效门槛收敛；库内来源已核验；库外条目与库内证据物理分区；交付物已落盘并写明核验边界。

## 最终报告
- 汇总保留/舍弃的视角、库内证据覆盖、库外待补清单、无法联网核实项及交付物路径。

## 一句话
先广撒（③ 菜单尽量全）、再按判据收敛（④ 硬门槛克制），库内能核实就走 grounded、库外外文能联网核实就核实、不能核实只给检索线索——**打开视野，但绝不用编造的文献填空**。
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


# ═══════════════ 出厂模板升级器 ═══════════════════════════════════════════════
# 历史坑（这条链最后一环长期是断的）：老 _write_if_absent 只在**文件不存在**时写。于是我们每改进一次
# 出厂模板（工作流 / README / 规约摘要），**所有已经跑过一次的机器——包括开发机自己——永远收不到新版**：
# 「改功能 → 同步指引给 agent」在最后一步默默失效，用户手里还是老指引。
# 修法（照抄本项目已验证的范式：wiki_store._FACTORY_HASHES + 下面的 _LEGACY_WF_HASHES）：
# 为每个出厂模板记住**历次出厂版**的 normalized-sha1（去掉所有空白后算），然后
#   · 文件不存在                                   → 直接写（等同旧 _write_if_absent）；
#   · 文件 hash ∈ 名单（= 某个历史出厂版，用户一个字没改）→ **静默升级**到新版；
#   · 文件 hash 不在名单（= 用户/agent 改过）        → **保留用户的文件**，把新版另存为 <名>.new.md 并提示合并。
# 不能靠「含有某几个特征串」判断出厂原样——用户在文件末尾追加自己的规矩后特征串依然都在，那样会覆盖掉他写的东西。
#
# 【维护方式：改完模板文本后必做】跑一次
#     build\py312\python.exe src\agent_ws.py --print-hashes
# 把打出来的新 hash 追加进下面 _FACTORY_HASHES 对应条目（**旧 hash 一个都别删**——删了老用户的出厂原样
# 文件就会被误判成「用户改过」，白白多出一堆 .new.md）。当前这一版的 hash 已经在名单里，所以下次改模板时
# 它自动就成了「历史出厂版」，老机器照样能静默升级。

# 机器相关的可变片段掩码：规约摘要里内嵌了 WIKI.md 的**绝对路径**（因机而异），不抹掉就没法把 hash 写死。
# 只匹配反引号包起来、以 WIKI.md 结尾的片段——生成端与读入端一致地抹成占位符，比较才成立。
_MASK_WIKI_PATH = re.compile(r"`[^`\n]*WIKI\.md`")


def _norm_hash(text, mask=None):
    """normalized-sha1：（可选先抹掉机器相关片段，再）去掉**所有空白**算 sha1。
       算法必须与 wiki_store._norm_hash 保持一致——改算法 = 名单里所有历史 hash 当场作废。"""
    t = text or ""
    if mask is not None:
        t = mask.sub("«VAR»", t)
    return hashlib.sha1(re.sub(r"\s+", "", t).encode("utf-8")).hexdigest()


# 各出厂模板的**历史** normalized-sha1 名单（新版往对应集合里 add，旧的永不删）。
_FACTORY_HASHES = {
    "home/AGENTS.md":                     {"4484e95570ece0386e0734797a7ccd487fc464cc"},
    "home/CLAUDE.md":                     {"4e7fc7f0d4ba2a199e11897b8c7fdc5c286cab04"},
    "output/README.md":                 {"0e59bde65651fba0a5bd3a54f45484ea1864ed5e"},   # v1 2026-07-14
    "rely/README.md":                   {"8a596c19896e457c57437faa0d759049a319f16c"},   # v1 2026-07-14
    "rely/记忆/项目记忆.md":            {"3c8387860ad95913a602b87b9447bc80bf5a7403"},   # v1 2026-07-14
    "rely/记忆/变更日志.md":            {"4d9a267ca46f94ff7d257c8c7b9ac486ec15f1fc"},   # v1 2026-07-14
    "rely/交付模板/交付说明书模板.md":  {"ad22abdf9bfa208e19f761c42e4ddcceb93031a2"},   # v1 2026-07-14
    "rely/参考格式/说明.md":            {"6613758c89cf04441a5dd11b75f23912e5092fd0"},   # v1 2026-07-14
    "rely/定时任务/说明.md":            {"3a3e8897533e902d48eaa21c20bd1d9143dcd2e8"},   # v1 2026-07-14
    "rely/技能/说明.md":                {"de19ded63470c2c7975a19eb0f012f14214b62a7",
                                             "33f52df5011ac84f49139577c2f67eaa162513c0"},   # v2 强工作流入口
    "rely/技能/写论文与综述.md":        {"5027f5d8e6c6837907e5ddbc294de7b2f10d5de3",
                                             "ee9f25dc732f19acc62a95094b9159669ef74326",
                                             "04951705502b6cced56dca9cca145ec64e01a876",
                                             "6f1c82b020fad509e00b8bf956f88d34d5e81429",
                                             "35f90edf1c4dfbf899f82c9036efb6acca685e2b"},   # v5 五格式原文定位
    "rely/技能/维护综述库.md":          {"ada886d3bf34277de35f6530a02874efcffaac92",
                                             "25646a2433e3a913d32803e3bf895f062424bba2",
                                             "8fd036b38a135c0a7e4e930e3a6b3a3cc74c22b8",
                                             "e32747b30fa5bb155b1756f6280407cc503a1a43"},   # v3 五格式提取状态
    "rely/技能/跨学科发散与补文献.md":  {"5dc99c7cd7cac23354e6915eee406e6b47e42a8e",
                                             "5aa2171c342492cae321f3ae68764baa3402c867",
                                             "f0cc893989e6a04f3983c12a35a55974a9883b42",
                                             "9d11e6c36bbf7fb7d87a780648024f77834eda2e",
                                             "65eebc4e0bac45cb51c968d6e00540281fdc23e5"},   # v5 五格式 add_source
    "rely/AI写综述遵守的规约.md":       {"0e792f4d4c0b698d187d59623f835b76d6056d7d"},   # v1 2026-07-14（已掩码绝对路径）
}


def _template_specs():
    """全部出厂模板的清单：(名单键, 落点, 当前文本, 掩码, 是否「用户数据种子」)。
       ensure_scaffold 与 --print-hashes 共用这一份，防止两边漂移（漏算 hash = 老用户平白多出 .new.md）。
       seed=True 的两份是**给用户/agent 写满的空表**（项目记忆 / 变更日志），不是给他读的指引：
       出厂原样时照样升级（没有用户内容可丢），但一旦被写过就**安静保留**、不再塞 .new.md 骚扰——
       模板头改几个字就往人家记忆旁边扔个新文件，纯属噪音。"""
    return [
        ("home/AGENTS.md",                    base_dir() / "AGENTS.md",                    _ROOT_AGENTS,           None, False),
        ("home/CLAUDE.md",                    base_dir() / "CLAUDE.md",                    _ROOT_CLAUDE,           None, False),
        ("output/README.md",                output_dir() / "README.md",              _README_OUTPUT,        None, False),
        ("rely/README.md",                  rely_dir() / "README.md",                _README_RELY,          None, False),
        ("rely/记忆/项目记忆.md",           memory_dir() / "项目记忆.md",            _PROJECT_MEMORY,       None, True),
        ("rely/记忆/变更日志.md",           memory_dir() / "变更日志.md",            _CHANGELOG,            None, True),
        ("rely/交付模板/交付说明书模板.md", templates_dir() / "交付说明书模板.md",   _DELIVERY_TEMPLATE,    None, False),
        ("rely/参考格式/说明.md",           formats_dir() / "说明.md",               _FORMATS_README,       None, False),
        ("rely/定时任务/说明.md",           tasks_dir() / "说明.md",                 _TASKS_README,         None, False),
        ("rely/技能/说明.md",               skills_dir() / "说明.md",                _SKILLS_README,        None, False),
        ("rely/技能/写论文与综述.md",       skills_dir() / "写论文与综述.md",        _WF_PAPER,             None, False),
        ("rely/技能/维护综述库.md",         skills_dir() / "维护综述库.md",          _WF_WIKI,              None, False),
        ("rely/技能/跨学科发散与补文献.md", skills_dir() / "跨学科发散与补文献.md",  _WF_DIVERGENCE,        None, False),
        ("rely/AI写综述遵守的规约.md",      rely_dir() / "AI写综述遵守的规约.md",    _rules_summary_text(), _MASK_WIKI_PATH, False),
    ]


_UPDATE_STATE = ".paperpiggy-template-updates.json"


def _state_path():
    return rely_dir() / _UPDATE_STATE


def _load_update_state():
    try:
        d = json.loads(_state_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_update_state(d):
    """升级提醒状态属于应用元数据；原子写，且跟随 0_Agent资料库进入备份。"""
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def _current_sidecar(path, current_text, mask=None):
    """找出内容等于当前出厂版的 .new 旁本；用户改过的旁本不会被误认。"""
    wanted = _norm_hash(current_text, mask)
    for p in [path.with_name(path.stem + ".new" + path.suffix)] + [
            path.with_name(f"{path.stem}.new.{i}{path.suffix}") for i in range(2, 100)]:
        try:
            if p.exists() and _norm_hash(p.read_text(encoding="utf-8"), mask) == wanted:
                return p
        except Exception:
            continue
    return None


def upgrade_status(include_ignored=False):
    """返回用户可见的出厂模板升级状态；只把可合并的指引/工作流列为待办。"""
    ensure_scaffold()
    state = _load_update_state()
    items = []
    for key, path, text, mask, seed in _template_specs():
        if seed:
            continue
        cur_h = _norm_hash(text, mask)
        try:
            main_h = _norm_hash(path.read_text(encoding="utf-8"), mask)
        except Exception:
            main_h = ""
        if main_h == cur_h:
            status, sidecar = "current", None
        else:
            sidecar = _current_sidecar(path, text, mask)
            status = "pending" if sidecar else "customized"
            if state.get(key) == cur_h:
                status = "ignored"
        if status == "current" or (status == "ignored" and not include_ignored):
            continue
        label = path.name
        if key.startswith("rely/技能/"):
            label = "工作流 · " + path.stem
        elif key == "rely/README.md":
            label = "专属资料库说明"
        elif key == "output/README.md":
            label = "交付物说明"
        items.append({
            "kind": "agent", "key": key, "label": label, "status": status,
            "main_path": str(path), "new_path": str(sidecar or ""),
            "current_hash": cur_h, "main_hash": main_h,
        })
    return {"pending_count": sum(x["status"] == "pending" for x in items), "items": items}


def template_diff(key):
    spec = next((x for x in _template_specs() if x[0] == key and not x[4]), None)
    if not spec:
        raise KeyError("不支持的模板")
    _key, path, text, _mask, _seed = spec
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    return "".join(difflib.unified_diff(
        old.splitlines(True), text.splitlines(True),
        fromfile=f"你的版本/{path.name}", tofile=f"新版出厂/{path.name}", n=3)) or "（只有空白差异）"


def acknowledge_update(key, current_hash):
    spec = next((x for x in _template_specs() if x[0] == key and not x[4]), None)
    if not spec or _norm_hash(spec[2], spec[3]) != current_hash:
        raise ValueError("这条升级提醒已经过期，请刷新后再试")
    d = _load_update_state()
    d[key] = current_hash
    _save_update_state(d)


def replace_with_factory(key, current_hash):
    """采用新版前先在原目录留用户备份；不删除 .new 旁本，操作可人工恢复。"""
    spec = next((x for x in _template_specs() if x[0] == key and not x[4]), None)
    if not spec:
        raise KeyError("不支持的模板")
    _key, path, text, mask, _seed = spec
    if _norm_hash(text, mask) != current_hash:
        raise ValueError("新版已变化，请刷新后再试")
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.stem}.user-backup-{stamp}{path.suffix}")
        i = 2
        while backup.exists():
            backup = path.with_name(f"{path.stem}.user-backup-{stamp}-{i}{path.suffix}")
            i += 1
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    d = _load_update_state()
    d.pop(key, None)
    _save_update_state(d)
    return str(backup or "")


def merge_template(key, current_hash, main_hash, merged_text):
    """写入 Agent 合并后的模板；写前校验主文件没被并发改动，并始终留用户备份。

    合并后记录“这一版已处理”，保留 .new 旁本不删；下一版 factory hash 变化时会重新提醒。
    """
    spec = next((x for x in _template_specs() if x[0] == key and not x[4]), None)
    if not spec:
        raise KeyError("不支持的模板")
    _key, path, text, mask, _seed = spec
    if _norm_hash(text, mask) != current_hash:
        raise ValueError("新版已变化，请刷新差异后再合并")
    if not isinstance(merged_text, str) or not merged_text.strip():
        raise ValueError("合并后的内容不能为空")
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if _norm_hash(old, mask) != main_hash:
        raise ValueError("你的文件在读取差异后又发生了变化，请重新读取再合并")
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.stem}.user-backup-{stamp}{path.suffix}")
    i = 2
    while backup.exists():
        backup = path.with_name(f"{path.stem}.user-backup-{stamp}-{i}{path.suffix}")
        i += 1
    backup.write_text(old, encoding="utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(merged_text, encoding="utf-8")
    os.replace(tmp, path)
    d = _load_update_state()
    d[key] = current_hash
    _save_update_state(d)
    return str(backup)


def _ensure_template(path, current_text, factory_hashes, mask=None, seed=False):
    """出厂模板的幂等落盘 + 升级。返回 created|current|upgraded|kept|forked|error（仅供测试/日志，调用方可忽略）。

    绝不丢用户内容 —— **覆盖一个已存在的文件，当且仅当它与某个历史出厂版一字不差**（= 用户没碰过）。
    这条规矩对**主文件和 .new.md 旁本一视同仁**：旁本被改过，就另起名字（.new.2.md…），不覆盖。
    （历史 bug：旁本曾经是无条件覆盖的 —— 提示语请用户「对照合并」，用户就在旁本里写笔记，
      然后下一次启动/下一次 agent 连 MCP，笔记就被出厂原文静默盖掉。而那时这行 docstring
      还宣称「唯一会覆盖的分支是一字不差」，把读代码的人也一起骗了。）"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(current_text, encoding="utf-8")
            return "created"
        old = path.read_text(encoding="utf-8")
        cur_h, old_h = _norm_hash(current_text, mask), _norm_hash(old, mask)
        if old_h == cur_h:
            return "current"                                   # 已是最新（纯空白差异也算最新）：一个字节都别写
        if old_h in factory_hashes:
            path.write_text(current_text, encoding="utf-8")    # 历史出厂原样、用户没改过 → 静默升级
            return "upgraded"
        if seed:
            return "kept"                                      # 用户数据种子：写过了就是他的东西，安静走开

        # 用户改过主文件 → 保留主文件，新版出厂模板另存为旁本。
        # ⚠️ 旁本**也是用户的东西**：提示语让他「对照合并」，他就很可能直接在旁本里做合并笔记。
        #    老代码在这里无条件 write_text，等于每次启动都把他的笔记盖回出厂原文（静默、无备份）。
        #    现在：旁本一旦被改过，就换个不冲突的名字放新版，绝不覆盖。
        newp = path.with_name(path.stem + ".new" + path.suffix)   # 写论文与综述.md → 写论文与综述.new.md
        if newp.exists():
            new_h = _norm_hash(newp.read_text(encoding="utf-8"), mask)
            if new_h == cur_h:
                return "kept"                                  # 旁本已是这一版：别重复写、更别每次启动刷屏
            if new_h not in factory_hashes:                     # 旁本被用户改过 → 另起一个名字
                for i in range(2, 100):
                    alt = path.with_name(f"{path.stem}.new.{i}{path.suffix}")
                    if not alt.exists():
                        newp = alt
                        break
                    if _norm_hash(alt.read_text(encoding="utf-8"), mask) == cur_h:
                        return "kept"                          # 这一版的旁本已经躺在那儿了
                else:
                    return "kept"                              # 攒了 98 个没合并的旁本？别再刷了。
            # else：旁本还是某个历史出厂版（用户没碰过它）→ 直接换成新版，安全。
        newp.write_text(current_text, encoding="utf-8")
        print(f"[agent_ws] 「{path.name}」你改过，已原样保留；新版出厂模板另存为「{newp.name}」，"
              f"可对照合并（用不上就直接删掉 {newp.name}）", file=sys.stderr, flush=True)
        return "forked"
    except Exception:
        return "error"                                         # 落模板绝不阻断主流程


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
        h = _norm_hash(txt)              # 与上面的升级器同一套 normalized-sha1（算法别分叉）
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


def _remove_obsolete_catalog_check_task():
    """删除曾误放进用户资料库的开发维护任务。

    该任务从未接入调度器，也不属于用户产品。用户已明确要求删除全部副本，因此不再按模板
    hash 区分是否编辑；删除范围固定为这一个精确命名的任务目录，不触碰其他定时任务。
    """
    try:
        obsolete = tasks_dir() / "文献目录半年检查"
        if obsolete.is_dir():
            shutil.rmtree(obsolete)
            print("[agent_ws] 已移除不应面向用户的「文献目录半年检查」开发任务", file=sys.stderr, flush=True)
        elif obsolete.exists():
            obsolete.unlink()
    except Exception:
        pass


def ensure_scaffold():
    """幂等创建两个文件夹的骨架 + README + 记忆/模板/工作流。可反复调用（server/mcp 每次启动、多处端点都调）；
       异常吞掉不阻断主流程。出厂模板走 _ensure_template：没改过的**跟着版本升级**、改过的原样保留（见上面注释）。
       返回 {名单键: 动作} 供测试/排查；正常调用方忽略即可。"""
    acts = {}
    try:
        for d in (output_dir(), output_dir() / "定时任务", rely_dir(),
                  memory_dir(), skills_dir(), formats_dir(), templates_dir(), tasks_dir()):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
        # 顺序要紧：先把旧单文件 技能/工作流.md 拆掉（删/改名），再铺新模板——反过来会先生成新文件、
        # 让迁移提示显得莫名其妙。
        _migrate_legacy_workflow()
        _remove_obsolete_catalog_check_task()
        for key, path, text, mask, seed in _template_specs():
            acts[key] = _ensure_template(path, text, _FACTORY_HASHES.get(key, set()), mask, seed)
    except Exception:
        pass
    return acts


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
        "agents_file": str(base_dir() / "AGENTS.md"),
        "claude_file": str(base_dir() / "CLAUDE.md"),
    }


if __name__ == "__main__":
    # 维护用：改完任一出厂模板文本后跑
    #     build\py312\python.exe src\agent_ws.py --print-hashes
    # 把变了的那几行 hash **追加**进 _FACTORY_HASHES 对应集合（旧的别删）。
    # 只读不写：不落任何文件、不碰 0_Agent* 工作区。
    if "--print-hashes" in sys.argv:
        print("# 当前出厂模板的 normalized-sha1（追加进 _FACTORY_HASHES，旧 hash 一个都别删）")
        for key, _path, text, mask, _seed in _template_specs():
            h = _norm_hash(text, mask)
            hit = h in _FACTORY_HASHES.get(key, set())
            print(f'    "{key}":{" " * max(1, 36 - len(key.encode("utf-8")))}{{"{h}"}},'
                  f'   # {"名单里已有" if hit else "★ 新版：请追加"}')
    else:
        print("用法: python agent_ws.py --print-hashes", file=sys.stderr)

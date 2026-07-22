# PaperPiggy MCP 接入说明

> ⚠️ **下面的命令是占位示例，请勿照抄！** 里面的 `<你的PaperPiggy目录>` 只是占位符，直接复制会得到错误配置。
> ✅ **正确做法**：打开 PaperPiggy 应用 →「🤖 Agent」页，那里有**本机真实路径已填好**的一键接入命令（Claude Code / Codex / 通用 mcp.json 各一份），直接复制即可。本文档只作原理与工具参考。

让 Claude Code / Codex 等 agent **原生调用**本地知识库——不用记命令，直接说「查库里关于 XX 的文献」，agent 自动调用检索工具。

零依赖（纯 stdlib + requests），不需要装 `mcp` 包。

## 提供的工具

<!-- TOOLS:BEGIN 由 gen_mcp_doc.py 生成，勿手改 -->
共 **39 个工具**（26 读 / 13 写）。工具清单与读写分类由 `gen_mcp_doc.py` 从代码生成。

| 工具 | 类型 | 作用 |
|---|---|---|
| `search_localkb(query, topk=8, sort=blend, category?)` | 读 | 检索本地文献知识库（用户自己的 Zotero 库或导入的全文文件夹，支持 PDF、EPUB、DOCX、Markdown、TXT）。返回带期刊等级、原文定位、可回溯引用的结果，用于查找某主题的相关文献、论点或原文段落。发现型检索默认同一篇最多返回2段，不用重复弱段凑满条数，适合先广泛找文献；定向深读请再用 read_source / verify_claim。可先用 localkb_status 了解库内篇数与学科。 |
| `list_kb_categories()` | 读 | 列出本地知识库的自建「知识库分类」及 AI 主题，返回可用于 search_localkb 的 category id。先列分类、再带 category 检索，可把检索聚焦到某一组文献。 |
| `resolve_page(key, pdf_page)` | 读 | 把某篇文献的『PDF 顺序页号』解析成『期刊印刷页码』（读者翻期刊看到的那一页）。写带页级引注时用它把检索命中的 page 换成正确印刷页；标『页码推算』者为连续性推算、请核对。 |
| `build_digest(query, topk=14)` | 写 | 半自动研究助手·能力二：给一个子题，返回并写回一节『带期刊印刷页引注的资料汇编综述』（含覆盖评级 ◎○△▲▽ 与诚实的资料缺口提示）。写回为 digest 页、标 🤖 未核验、可被检索命中。 |
| `research_outline(topic)` | 写 | 半自动研究助手·能力一：给研究主题，返回并写回『选题拆解 + 标题参考 + 三级大纲(★核心/☆辅助)』的框架页。论证主线须由学者自定，本工具只做启发。写回为 outline 页、标 🤖 未核验。 |
| `suggest_new_sources(topic)` | 读 | 半自动研究助手·能力三：给主题，返回『建议新增文献（脚注引文挖掘库内缺失、按被引频次）+ 库内错配（有全文附件未深索）+ 覆盖评估』。只读、不写库。 |
| `export_disclosure(page_ids)` | 读 | 半自动研究助手·G4：按所选综合页(digest/outline 等的 id)生成《生成式 AI 使用声明》文本（规则拼装、零 LLM），用于论文投稿的 AIGC 合规披露。传入相关 wiki 页 id 列表即可。 |
| `localkb_status()` | 读 | 查看本地知识库索引状态（词法/语义/全文各档就绪情况、已索引篇数）。查【深索】进度请用 deep_status。 |
| `list_workflows()` | 读 | 列出用户本机现有工作流及路径。请求命中工作流时必须先调用，再用 read_workflow 读取全文。 |
| `read_workflow(name)` | 读 | 读取指定工作流全文。开始写作、维护或跨学科发散前必须先读匹配工作流并照完成标准执行。 |
| `maintenance_audit()` | 读 | 全量维护统一入口：一次盘点模板、索引、全文附件深索/PDF OCR、检索摘要、wiki 待办和体检，并区分自动处理/需决策/外部阻塞。用户只要提到维护就先调用。 |
| `get_template_upgrade_diff(key)` | 读 | 读取某条 Agent 模板/工作流升级的差异与并发校验 hash，供 Agent 保留用户定制后完成语义合并。 |
| `merge_template_upgrade(key, current_hash, main_hash, merged_text)` | 写 | 提交 Agent 合并后的模板正文；写前校验文件未变化并自动留 user-backup。只有真实语义冲突才应先问用户。 |
| `submit_agent_summaries(summaries)` | 写 | 在设置选择“交给 Agent 生成”时，提交你根据 read_source 原文写好的检索摘要；整批质量检查后只重嵌入指定文献。 |
| `resolve_wiki_suggestion(key, status, reason?, related_page_ids?)` | 写 | 记录一条 wiki 建议的真实处理结果。更新/建页后标 updated/created；无需写入或阻塞时必须写理由。 |
| `deep_status()` | 读 | 查看本地库【深索】进度：已深索篇数 / 有全文附件总数 / 队列真实状态 / 摘要有效、异常与缺失数 / 预计剩余时间（ETA）/ 当前在深索或队首的篇。深索前后可随时查，了解深到哪了。 |
| `deep_index(summaries?, batch=15)` | 写 | 深索用户的本地文献库——切块→你自己写检索摘要→带摘要嵌入，一趟完成，不用「先深索再单独补摘要」。用法（循环）：第一次【不带 summaries】调用我 → 我返回 to_summarize（若干篇的 key、标题、正文节选 excerpt）；你为每篇写一段【约150字的中文检索摘要】（概括核心主题/研究方法/主要结论，供语义检索用）；再【带 summaries=[{key, summary}]】调用我 → 我把上一批带着你的摘要嵌入入库、并返回下一批待写摘要；摘要会先过质量检查：过短、乱码、无限重复或失控长文会让整批拒绝写入，并返回具体 key 与原因；修正后重交。如此循环，直到我返回 finished=true 表示全部深索完成。每批默认 15 篇（可用 batch 调整）。若返回 busy=true 说明有其它构建在跑，稍后再调。 |
| `localkb_build(stage=light)` | 写 | 触发本地知识库建库/更新。stage: light(即时词法,秒级) / semantic(语义,分钟级) / deep(全文深索)。加了新文献后用来增量更新。注意：deep 深索大库很慢，且服务端摘要需 API Key——推荐改用 deep_index 让你（Agent）自己写检索摘要，一趟把深索+摘要都做完（无需 API Key、质量可控）。 |
| `save_synthesis(title?, content, sources?)` | 写 | 把一段综合结论回填本地知识库的「综合层」。用 search_localkb 检索后，可把你综合出的结论/文献综述存成一张带引用、可累积、之后能被检索到的综合页（answer 页）——同类问题下次可直接命中该缓存综合，探索开始累积。每个论断请带 [n] 引用，sources 填所依据论文的 key。 |
| `list_wiki(offset=0, limit=100)` | 读 | 列出本地知识库综合层里已存的 wiki 综合页（answer/concept/topic）。动手写综合前先查有没有现成的，避免重复造轮子（先读 index、后写回）。页数多时用 offset 翻页（返回里会注明总页数与当前 offset）。 |
| `get_wiki_page(id)` | 读 | 取某个 wiki 综合页的正文（markdown）+ 其来源的论文级页码引用。配合 list_wiki：先列后取，复用已有综合而非从零重写。 |
| `read_source(key, from_page=1, to_page=0, max_chars=20000)` | 读 | 读某篇论文的**原文正文**（PDF 按页并附期刊印刷页码；其他格式按章节、段落或行号定位）。检索结果只给 220 字片段；要真正读懂一篇文献、写综述、或核对引注，必须用这个先读原文。key 来自 search_localkb 结果里的 «key:…» 或 list_sources。未深索 / 只有题录 / 扫描件时会明确告知原因与补救办法，不会静默返回空。 |
| `list_sources(deep=all, category?, source_type?, limit=50, offset=0)` | 读 | 列出知识库里的文献题录。可用 deep='no' 筛出**尚未深索**的篇目——那些是还没被读过、值得 ingest 的源。用于驱动「逐篇读入并维护 wiki」的循环。 |
| `mark_stale(page_id, stale=True, reason?)` | 写 | 把某综合页标记为「已过时」（或清除标记）。当新文献推翻了旧综合、或页内断言不再成立时用。标记后该页在检索里显著降权、界面显示 ⚠ 徽标。这是健康检查(lint)的核心动作：**不要**直接覆盖别人的结论页，而应标脏并写清理由。 |
| `get_backlinks(key?, page_id?)` | 读 | 反查关联。给 key（论文）→ 哪些综合页引用了这篇（新增或更新这篇后，据此判断哪些页要标脏/重生）；给 page_id（综合页）→ 它引用了哪些论文、与哪些页互链、是不是孤儿页。这是 ingest 后「一篇源触及多个 wiki 页」和 lint 的起点。 |
| `update_wiki_page(page_id, kind?, title?, content, sources?, mode=replace, links?)` | 写 | 建立或修改一个 wiki 综合页。这是维护 wiki 的主要动作。 kind 可选：answer(问答沉淀) / concept(概念) / topic(主题) / digest(资料汇编) / outline(选题框架) / **entity(实体页：作者、机构、案件、制度)** / **overview(总论页：随全库演进的核心论点)**。 mode='append' 把新内容与来源并入既有页；'replace' 整体重写，显式传 sources 时会替换旧来源，可用于修正失效 key；replace 不传 sources 则保留旧来源。 护栏：不能覆盖用户人工核验过的页（会被拒绝）。每个论断带 [n] 引用，sources 填论文 key。 |
| `set_wiki_links(page_id, links, mode=replace)` | 写 | 维护某页的交叉链接（wiki 页之间的边）。**这是把一堆孤立页面变成一张知识图的唯一途径**——没有 links，每一页都是孤儿，lint 会一直报警。只接受已存在的页 id，自动拒绝自链与断链。 |
| `lint_wiki(min_mentions=2)` | 读 | 综合层健康体检（gist 三大操作之一）。查：孤儿页、已过时页、断链、无来源论文的页、未配 AI 模型时生成的降级页、被反复提及却没有独立页的概念、无效来源 key、重复标题/研究问题。返回问题清单 + 建议动作。定期跑一次，wiki 才不会烂掉。纯读，不改任何东西。 |
| `propose_wiki_updates(key, topk=12)` | 读 | **读完一篇文献后必调**。给论文 key，返回这篇影响了哪些既有 wiki 页、每页该怎么改。 两条线索：① 直接引用它的页（结论可能被推翻）；② 讲同一主题却没引用它的页（该更新却没人知道）。 gist 的经验：一篇源常常触及 10-15 个页。拿到清单后逐页执行 update_wiki_page / mark_stale / set_wiki_links，别只改一页就收工。 |
| `format_citation(key, pdf_page?, position?, locator?, style=footnote)` | 读 | 把一篇文献排成规范引注（脚注格式）。写论文脚注时用：key 来自 search_localkb / list_sources。PDF 用 pdf_page（会换算期刊印刷页码）；其他格式传 position 和 locator（来自检索或 locate_quote）。返回里若有 missing_fields（题录缺字段）或 page_estimated（页码为推算）请提醒用户人工核对。注意：引领词（参见/见/转引自）由作者按引用性质自定，本工具不加。排注前建议先用 locate_quote 核对引文确实在那一页。 |
| `get_source_meta(key)` | 读 | 取**单篇**文献的完整题录与状态：作者/年份、真实文献性质、唯一客观标签、四档评价、有无全文附件、主全文格式、是否深索、题录摘要（bibliographic_abstract）与 SAC 检索摘要（retrieval_summary，二者明确分开）、法条时效（statute_status）、以及哪些 wiki 综合页引用了它（cited_by_wiki）。替代『list_sources 翻找 + get_backlinks 反查』两跳——精读一篇前先调它一次拿全貌。 |
| `similar_sources(key, topk=8)` | 读 | 给一篇 key，返回**向量近邻**的相似文献（cosine，非关键词匹配）。精读完一篇后用它扩展检索面——换角度找到 search_localkb 用词召不回的同题文献。需要语义索引（full 模式）且该篇已入向量表；不满足时会明确告知回退办法。 |
| `whats_new(days=7, limit=20)` | 读 | 列出最近 N 天新入库的文献（按入库时间倒序）。回访一个久未碰的库时先调它，了解「上次之后进了什么新东西」。返回的 affected_pages 恒为空数组——逐篇分析太贵，请对关心的新篇配合 propose_wiki_updates / get_wiki_page 深入。 |
| `locate_quote(quote, key?, fuzzy=True)` | 读 | **引注核对地基**：给一句引文，核对它是否真的在原文里以及原文位置（PDF 页号 + 期刊印刷页码，或 EPUB/DOCX/Markdown/TXT 的章节、段落、行号）。写脚注前、以及核查既有文稿的引注时逐条过一遍。默认模糊匹配（容忍 OCR/标点差异），exact=false 的命中请人工比对 context。给 key 则只在该篇内找，不给则全库找。 |
| `verify_claim(claim, keys?, topk=8)` | 读 | 核验一个**实质论断**是否有库内文献支撑。返回三态：supported=有证据支持 / mismatch=库内证据与论断相左（可能记错或过度概括）/not_in_lib=库里找不到依据。注意 not_in_lib **不等于论断为假**——只说明本库无证据，该论断要么删、要么明确标注「作者观点/库外知识」。写完每一节后逐条过实质论断。 |
| `add_source(path, note?)` | 写 | 把本机一个全文文件收进知识库（支持 PDF、EPUB、DOCX、Markdown、TXT；只加不删，不支持 HTML）。用户在对话里给了本地文件路径、想让它进库时用。题录由 AI 自动抽取、**待人工核对**（应用里会标「题录待核对」）。收录后建库在后台跑，稍后可用 localkb_status / deep_status 查进度。仅 folder（文件夹）模式可用：Zotero 模式会拒绝并提示把全文文件附到 Zotero 条目上。 |
| `pending_wiki_updates(offset=0, limit=30)` | 读 | 拉取服务器已算好的「待处理综合页更新」清单——最近深索/新增的文献可能影响哪些既有 wiki 页。深索一批文献后、或想主动维护 wiki 时**先调它**，直接拿到受影响页清单（无需自己对每篇跑 propose_wiki_updates），再逐页处理；有 next_offset 时必须继续翻页，直到全部清零。 |
| `read_project_memory()` | 读 | 读用户的**项目记忆**（当前真相：用户是谁/偏好/已定决策/当前在做）。这是换任何 AI 助手都共享的本地文件——开工前先读它接上之前的工作。initialize 已内联一份，但内容可能已被更新，动手前可再读一次拿最新。 |
| `append_project_memory(text)` | 写 | 把一条**已定决策/偏好/进度**追加进项目记忆（保持它是「当前真相」，供之后任何 AI 助手接上）。只写实质结论、保持简短；历史流水账不要写这里。默认追加到文件末尾；不覆盖已有内容。 |
<!-- TOOLS:END -->

> **信任模型（读—综合—写回闭环）**：agent 能**写**（建页、改页、建互链、标过时），**不能删**。
> 三道护栏：
> 1. **不能覆盖人工页**——`page_id` 由标题哈希而来，同标题即同页。若目标页是你人工保存/核验过的（`by_agent=false`），agent 写回会被拒（HTTP 409），只能换标题或先 `get_wiki_page` 读了再说。agent 可以覆盖 agent 自己的页；你可以覆盖 agent 的页。
> 2. **强制 provenance**——每页带来源 bibkey + 页码 + 模型 + 时间，可一跳回溯原文核对。
> 3. **检索降权**——新鲜综合页同分让位于原始文献；被 `mark_stale` 标脏的页乘性重罚（×0.3）真正沉到真论文之下，三种 `sort` 下一致生效。未配 AI 模型时生成的「证据清单」根本不入检索表。
>
> 4. **版本历史**——每次写入自动记一版（装了 git 就用 git，没装则用 `.history/` 快照）。
>    在综述库里可以查看任意一页的修改历史并一键回滚。所以放手让 agent 改，改错了能退回来。
>
> 删除只由人在网页端一键「🗑 不保存」（`DELETE /wiki/page/{id}`，**故意不做成 MCP 工具**）。回滚同理。
> 发现旧页被新文献推翻，正确做法是 `mark_stale` 标脏 + 写清理由，而不是抹掉别人的结论。
>
> **规约自动下发**：agent 连上时 MCP `initialize` 会把 `WIKI.md`（综合层结构约定）连同写回纪律一起下发到 agent 的系统提示里——不需要你手动粘贴，也不必让 agent 自己去读文件。

## Resources（agent 可直接读的资源）
| uri | 内容 |
|---|---|
| `localkb://schema` | `WIKI.md` 全文——综合层的结构约定与写回纪律 |
| `localkb://index` | 所有 wiki 页的清单 |
| `localkb://lint` | 当前的体检报告（孤儿页/过时页/断链/缺失概念页） |
| `localkb://memory` | 项目记忆（当前真相：用户是谁 / 偏好 / 已定决策 / 当前在做）——换任何 AI 助手都先读这份接上之前的工作 |
| `localkb://page/<id>` | 某一页的 markdown 正文 |

## Prompts（斜杠命令，把 gist 三大操作变成一句话）
| 命令 | 做什么 |
|---|---|
| `/ingest-source key=<论文key>` | 读原文 → 看它影响哪些页 → 逐页更新 → 建互链 → 更新总论页（gist 的 **Ingest**） |
| `/lint-wiki` | 体检并修复：孤儿页补互链、过时页重写、断链清理（gist 的 **Lint**） |
| `/query-and-file question=<问题>` | 回答问题，并把好答案沉淀回 wiki、接进知识图（gist 的 **Query**） |

在 Claude Code 里输入 `/` 即可看到这三个命令。

> 补充：`localkb://page/{id}` 现以 **resource template** 声明（MCP `resources/templates/list`），
> 支持该方法的客户端可以直接发现并按 id 读任意综合页，不必先调 list_wiki。

## 工作流 / 技能：**无需手动安装**

应用会自己把工作流写到「**0_Agent资料库 › 技能**」文件夹里（Agent 页点「打开技能文件夹」即到），
**一个工作流一个 .md、人类可读**，目前出厂三条：

| 文件 | 做什么 |
|---|---|
| `写论文与综述.md` | 意图澄清 → 迭代检索 → grounded 提纲（经你确认）→ 分节起草 → `verify_claim` 逐条核验 → `format_citation` 排脚注 → 沉淀回 wiki → 披露提醒。每个论断强制绑定库内来源 key + 页码 |
| `维护综述库.md` | 用户提到维护就全量审查模板、索引、深索、摘要和 Wiki；简单事项直接处理，真实决策再询问，复核后全面总结 |
| `跨学科发散与补文献.md` | 打开理论视野、推荐库外该补的外文文献 |

MCP 一接上，agent 在 `initialize` 时就会收到这个技能目录的路径，自己去读——**你不用复制任何文件夹，
也不用往 `.claude/skills/` 里装东西**。（早期版本发过一个 `skills/localkb-paper` 技能包，已废弃：
它和应用内置的工作流是同一条流水线的两份事实源，只会打架。）

想改成自己的习惯，或新增一条属于你的工作流？直接编辑 / 新建那个 `.md` 就行——让 AI 助手帮你改也可以。
这些工作流都依赖 localkb MCP server，请先按下文完成 MCP 接入。

---

## Claude Code 接入

> 下面路径中的 `<你的PaperPiggy目录>` 是占位符（勿照抄，用 Agent 页的真实命令）。

**方式 A（命令行，推荐）**——在任意 Claude Code 会话里运行：
```
claude mcp add localkb -- "<你的PaperPiggy目录>\python\python.exe" "<你的PaperPiggy目录>\app\mcp_server.py"
```
加 `--scope user` 可让所有项目都能用；不加则只在当前项目。

**方式 B（项目级 `.mcp.json`）**——在工作区根目录建 `.mcp.json`：
```json
{
  "mcpServers": {
    "localkb": {
      "command": "<你的PaperPiggy目录>\\python\\python.exe",
      "args": ["<你的PaperPiggy目录>\\app\\mcp_server.py"]
    }
  }
}
```

加好后：**新开一个 Claude Code 会话** → 输入 `/mcp` 应能看到 `localkb`（工具数见上表）。
之后可以直接对话「帮我查库里关于社会观护的权威文献」，Claude 会自动调用 `search_localkb`。
更进一步，试试 `/ingest-source`：它会读完一篇原文、找出受影响的综述页、逐页更新并建好互链——
这正是 gist 说的「LLM 做掉所有 bookkeeping」。

---

## Codex 接入

编辑 `~/.codex/config.toml`，加（路径同样是占位符，勿照抄，用 Agent 页真实命令）：
```toml
[mcp_servers.localkb]
command = "<你的PaperPiggy目录>\\python\\python.exe"
args = ["<你的PaperPiggy目录>\\app\\mcp_server.py"]
```

---

## 说明
- MCP server 是**瘦客户端**：它调用 LocalKB 的 HTTP 服务（127.0.0.1:8770），服务没起会自动拉起（首次加载模型约 30-60s）。
- 中文查询、结果全部 UTF-8，日志走 stderr 不干扰协议。
- 当前正在运行的 Claude Code 会话**无法热加载**新 MCP——配好后要新开会话才生效。
- **非 MCP 生态**（自写脚本、LangChain、别家 agent 框架）可直接调 HTTP：OpenAPI 交互文档在
  <http://127.0.0.1:8770/docs>（服务运行时可开）。
- **CLI 定位**：`localkb.py` 仅覆盖检索/建库/状态三件事，完整能力（读原文、wiki 维护、引注核验等）走 MCP。
- 部分工具（search_localkb / read_source / list_sources / list_wiki / get_source_meta / verify_claim / locate_quote / whats_new）
  在返回里附带 `structuredContent`（MCP 2025-06 规范）；老客户端自动忽略，文本仍是主载体。

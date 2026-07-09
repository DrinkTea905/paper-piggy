# LocalKB MCP 接入说明

> ⚠️ **下面的命令是占位示例，请勿照抄！** 里面的 `<你的PaperPiggy目录>` 只是占位符，直接复制会得到错误配置。
> ✅ **正确做法**：打开 PaperPiggy 应用 →「🤖 Agent」页，那里有**本机真实路径已填好**的一键接入命令（Claude Code / Codex / 通用 mcp.json 各一份），直接复制即可。本文档只作原理与工具参考。

让 Claude Code / Codex 等 agent **原生调用**本地知识库——不用记命令，直接说「查库里关于 XX 的文献」，agent 自动调用检索工具。

零依赖（纯 stdlib + requests），不需要装 `mcp` 包。

## 提供的工具
| 工具 | 类型 | 作用 |
|---|---|---|
| `search_localkb(query, topk=8, sort=blend)` | 读 | 检索知识库，返回带期刊分级+官方页码+引用的结果；wiki 综合页会以「📝已存综合／🤖未核验」标注一并召回 |
| `localkb_status()` | 读 | 索引状态（各档就绪、篇数）+ 综合层已存页数 + `WIKI.md`（写回规约）路径 |
| `list_wiki()` | 读 | 列已存的 wiki 综合页（answer/concept/topic）——**动手综合前先查有没有现成的**，避免重复造轮子 |
| `get_wiki_page(id)` | 读 | 取某页正文（markdown）+ 其来源的论文级页码引用 |
| `save_synthesis(title, content, sources)` | 写 | 把综合结论**回填**成带引用的 wiki 页（跨会话/跨 agent、与网页 `/chat` 共享同一份综合；默认采纳、立即可检索） |
| `localkb_build(stage=light)` | 写 | 触发建库/更新（light/semantic/deep），加了新文献后增量更新 |
| `list_kb_categories()` | 读 | 列「知识库分类」+ AI 主题，返回可传给 `search_localkb(category=…)` 的 id，把检索聚焦到某组文献 |
| `resolve_page(key, pdf_page)` | 读 | 把检索命中 chunk 的 PDF 顺序页 → **期刊印刷页码**（读者翻期刊看到那页）；标「页码推算」者为连续性推算，请核对 |
| `build_digest(query, topk=14)` | 写 | 研究助手·能力二：给子题产出并写回一节**带印刷页引注的资料汇编综述**（含覆盖评级◎○△▲▽ + 诚实缺口提示） |
| `research_outline(topic)` | 写 | 研究助手·能力一：给主题产出并写回**选题拆解 + 标题参考 + 三级大纲(★/☆)**（论证主线须学者自定，只做启发） |
| `suggest_new_sources(topic)` | 读 | 研究助手·能力三：给主题返回**建议新增文献（脚注引文挖掘库内缺失，按被引频次）+ 库内错配 + 覆盖评估** |

> **信任模型（读—综合—写回闭环）**：agent 只能**写**（`save_synthesis`），**不能删**。写回默认采纳、立即可检索；质量兜底靠每页强制 provenance（来源 bibkey + 页码 + 模型 + 时间）可一跳核对、检索降权不盖过真论文，以及**人在网页端一键「🗑 不保存此答案」**剔除（`DELETE /wiki/page/{id}`，故意不做成 MCP 工具）。**写前先 `list_wiki` / `get_wiki_page` 查有没有现成综合**，并读 `localkb_status()` 返回里的 `WIKI.md` 路径了解写回规约。

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

加好后：**新开一个 Claude Code 会话** → 输入 `/mcp` 应能看到 `localkb`（6 个工具）。之后直接对话「帮我查库里关于社会观护的权威文献」，Claude 会自动调用 `search_localkb`；综合完还能让它 `save_synthesis` 把结论回填、下次直接复用。

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

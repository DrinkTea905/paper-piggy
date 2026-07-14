# 把知识库改造成 Agent 版本 —— 可行性调研

> ---
> ⛔ **归档件 · 请勿执行**
> 本文档记录的是 2026 年 7 月某一轮改造的**方案/指令/调研**，相关内容**均已全量实施完毕**。
> 它保留在此仅为「当时为什么这么决定」的决策存档。
> **不要把它当作待办清单，不要照此重跑改造。**
> 项目当前的事实、规则与待办，一律以项目根 `CLAUDE.md` 为准。
> ---


## 结论先行：有搞头，而且起点很低
你已经做对了最关键的一步——**LocalKB 已经暴露了 MCP 服务 + 检索 API**。
这意味着"agent 版本"**不需要重写知识库**，而是把一个现成的 deep-research agent 接到你的库上：
agent 负责"规划→多轮检索→精读→综合→写作"，你的 2000+ 篇法学库当它的**受控检索工具**，
用你自己的 LLM key（SiliconFlow 免费即可）做推理。

## 什么是"agent 版本"（相对现在）
- **现在**：搜索 + 单轮对话（问一句，检索 top-8，LLM 综合一段带引用的回答）。
- **agent 版本**：给一个大任务（如"就'涉罪未成年人分流转处'写一篇文献综述"），agent 自主
  **拆解子问题 → 多角度反复查库 → 逐篇精读 → 交叉比对 → 产出结构化长文（带页级引用）**。
  从"问答"升级为"自动研究员"。

## 开源格局（2026）
| 框架 | 特点 | 与你的契合点 |
|---|---|---|
| **DeerFlow**（字节, MIT） | 长时程 SuperAgent，多智能体(Coordinator/Planner/Researcher/Coder/Reporter)，**模型无关(OpenAI兼容)**，**原生支持 MCP 工具**，本地记忆 | 最契合：SiliconFlow key 直接用；把 LocalKB 的 MCP 挂上就是"研究员"的检索工具；专为文献综述设计 |
| **R2R** | 带 Deep Research API，agentic 多步推理，混合本地库+外部 | 现成的 deep-research 后端 |
| **RAGFlow** | agentic RAG 引擎，深度文档理解(表格/版式) | 若想连"重新解析 PDF"一起换 |
| **Haystack**（deepset） | 生产级 RAG/agent 编排框架 | 想自己搭 agent 管线时的积木 |
| **Qwen-DeepResearch / Camel-workforce** | 面向离线闭环、多模态交叉阅读 | 纯离线深研的参考 |

## 两条落地路径
### 路径 A：接现成 agent（低成本，先验证价值）★推荐先做
- 保持 LocalKB 当**检索后端**不动，把它的 MCP 服务作为工具接入 **DeerFlow**（或 R2R）。
- agent 的"大脑"用你的 SiliconFlow key；"资料来源"= 你精选的法学库（带期刊分级）。
- 几乎零改造就能试出"自动文献综述"的效果，值不值一试便知。

### 路径 B：把 agent 循环做进 LocalKB（高成本，验证后再上）
- 在应用里加一个"深度研究"模式：内置 plan→retrieve→read→synthesize 循环 + 子任务并行。
- 更可控、体验一体化，但工作量大（且要处理长文引用一致性、防幻觉、进度可视化）。

## 为什么你的场景特别适合
- **grounded 优势**：通用 deep-research agent 满互联网抓料、质量参差；你的 agent 只在
  **你亲自筛过的 2000+ 篇 + 期刊分级** 里检索 → 引用可靠、可回溯页码，天然适合严肃法学写作。
- **已有基建**：MCP/API/期刊分级/页级引用/选择性深索都现成，agent 直接享用。
- **成本可控**：SiliconFlow 免费模型可做嵌入+重排+对话；agent 多步推理建议用稍强的模型
  （免费 DeepSeek-V3 可起步，复杂综述可换更强的）。

## 风险 / 注意
- **多步推理吃 LLM 能力**：免费模型能跑通，但综述质量与规划能力和模型强弱正相关。
- **幻觉控制**：必须强约束"每个论断都挂检索到的原文+页码"，否则 agent 长文容易编。
- **延迟/调用量**：一次深研可能几十次检索+多次 LLM 调用，分钟级；免费档注意限流。
- **别推倒重来**：不要为了 agent 化而改嵌入管线（见迁移文档红线），agent 是加在检索**之上**的一层。

## 建议下一步（等你定）
1. 先按**路径 A** 起个 DeerFlow，把 LocalKB 的 MCP 挂上，拿"写一篇某主题文献综述"实测一把。
2. 效果好，再考虑把最有用的部分（如"深度研究"按钮）**原生化**进 LocalKB（路径 B）。

## 参考来源
- [15 Best Open-Source RAG Frameworks in 2026](https://www.firecrawl.dev/blog/best-open-source-rag-frameworks)
- [Best open source frameworks for building AI agents in 2026](https://www.firecrawl.dev/blog/best-open-source-agent-frameworks)
- [ByteDance DeerFlow (GitHub)](https://github.com/bytedance/deer-flow)
- [Build a local deep research agent with DeerFlow](https://docs.olares.com/1.12.5/use-cases/deerflow.html)
- [ByteDance Open-Sources DeerFlow (MarkTechPost)](https://www.marktechpost.com/2025/05/09/bytedance-open-sources-deerflow-a-modular-multi-agent-framework-for-deep-research-automation/)
- [Top Agentic Frameworks 2026 (JetBrains)](https://blog.jetbrains.com/pycharm/2026/06/top-agentic-frameworks-for-building-applications-2026/)

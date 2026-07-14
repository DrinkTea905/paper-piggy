I now have everything I need. Below is the complete implementation-level design document for my scope (导航重构 + wiki页 + Agent页 + 对话页).

---

# LocalKB 详细设计 · 导航重构 + wiki页 / Agent页 / 对话页

> ---
> ⛔ **归档件 · 请勿执行**
> 本文档记录的是 2026 年 7 月某一轮改造的**方案/指令/调研**，相关内容**均已全量实施完毕**。
> 它保留在此仅为「当时为什么这么决定」的决策存档。
> **不要把它当作待办清单，不要照此重跑改造。**
> 项目当前的事实、规则与待办，一律以项目根 `CLAUDE.md` 为准。
> ---


> 负责范围：6 标签导航骨架、`panel-wiki`、`panel-agent`、`panel-chat` 改造，以及一个新后端端点 `GET /agent/mcp-config`。全部面向用户文案为成稿。
> 已核实的源码基线：`web/index.html`、`web/app.js`、`server.py`、`wiki_store.py`、`mcp_server.py`、`config.py`、`MCP接入说明.md`。凡带「需核实」的地方我都写清了核实方法。

---

## 1. 目标与范围

### 1.1 要解决什么
1. **导航从 4 标签扩成 6 标签**：📊库总览 / 📚浏览 / 📖wiki页 / 🔍检索 / 🤖Agent / 💬对话，默认落地「库总览」。当前默认停在检索（`index.html:13` 的 `active`）。
2. **wiki 页从"孤儿弹窗"变成一等公民页面**：后端综合层 5 个端点全就绪，但 `GET /wiki/list` 与 `POST /wiki/concept` **前端从未调用过**（已核实：`app.js` 全文无 `/wiki/list`、无 `/wiki/concept`）。用户在对话里"保存此答案"沉淀的综合页、agent 经 MCP 写回的页，**目前只能靠检索碰运气命中**，没有列表入口。本设计给它一个可浏览、可筛选、可打开/重生/删除的主页面，并与检索双向连通。
3. **Agent 页从零新建**：当前应用内无任何 MCP 入口，只有一份 `MCP接入说明.md`，且里面路径是占位符 `D:\LocalKB\...`、`D:\00Zotero知识库\rag\.venv\...`（真实机器路径不同，用户照抄会失败）。本设计让应用**自动吐出该机器真实可用的接入命令**，并把"能干什么、怎么接、怎么用"讲清楚。
4. **对话页降级为辅助**：产品要弱化"对话驱动"、引导用户走 Agent。改造 `.chat-hint` 为劝退+引导文案；把设置弹窗里的「LLM 服务商」整块内联进对话页成一个默认收起的折叠区（onChange 即存，去掉保存按钮）；"保存此答案"成功后给一个"→已进 wiki 页"的跳转。

### 1.2 不含什么
- 不动检索核心（`retriever.py`）、不动综合层生成逻辑（`wiki_store.py` 的 `synthesize_*`）、不动索引/建库流程。
- 不改 `cfg()/saveCfg()` 的存储格式（`app.js:46-47`），只改「谁来触发保存」。
- 不新增综合层生成能力，只把已有的 `/wiki/concept`（按需综述）接到前端。
- 库总览、浏览两页本设计不改（属其它负责人）；但我会用到它们已有的 `switchTab()`、`switchToSearch()`、`doSearch()`。

---

## 2. 现状（关键 `文件:行号`）

**导航**
- 硬编码 4 按钮：`index.html:12-17`，`data-tab=search` 带 `active`；`data-tab` ∈ {search, browse, dashboard, chat}。
- 4 个 panel：`panel-search`（`index.html:31`）、`panel-dashboard`（:56）、`panel-browse`（:63）、`panel-chat`（:102）。除 search 外都带 `hidden`。
- 切换逻辑两处，**必须同步改**：
  - tab 点击监听 `app.js:117-124`：清 active → 加 active → 按 `data-panel` 显隐 → dashboard/browse 懒加载。
  - `switchTab(tab)` `app.js:126-131`：同逻辑的函数版（供代码内部调用，如库总览深索卡跳浏览）。
  - `switchToSearch(q)` `app.js:133-136`：切到检索并执行一次 `doSearch()`。
- 懒加载哨兵：`let dashLoaded = false, browseLoaded = false;`（`app.js:116`）。

**wiki（现状＝只有弹窗）**
- DOM：`#wiki-modal`（`index.html:216-230`），含 `#wiki-title / #wiki-meta / #wiki-body / #wiki-sources` 与 `#wiki-regen / #wiki-discard / #wiki-close`。
- JS：`renderWiki(p)`（`app.js:557`）、`openWikiPage(id)`（:568）、`discardWiki(id,onDone,btn)`（:573）、`genTopic(topicId,name,btn)`（:585）、`wireWikiModal()`（:596-613）。`stripFm/llmBody/needKey`（:544-556）是综合层公用工具。
- 后端全就绪：`GET /wiki/list`（`server.py:384`）→ `W.list_pages()`；`GET /wiki/page/{id}`（:388）；`DELETE /wiki/page/{id}`（:395，仅人用）；`POST /wiki/concept`（:427）；`POST /wiki/topic`（:435）；`POST /wiki/regenerate/{id}`（:443）。
- `list_pages()` 返回字段（`wiki_store.py:347-354`）：`id / kind / title / generated_at / generated_by / stale / by_agent / n_sources`。
- **检索行 ↔ wiki 页 id 一致性（已核实）**：检索结果里 wiki 行的 `r.key` 就是 `list_pages` 的 `id`——`retriever.py:163` 用 `r.get("key")` 作为 `wid` 去 `M["wiki"]`（即 `wiki_store.index_map()`，以页 id 为键）取元数据。故三处删除 id 来源天然一致：检索卡 `discardWiki(r.key,…)`（`app.js:192`）、弹窗 `discardWiki($("#wiki-regen").dataset.id,…)`（`app.js:599`，值来自 `renderWiki` 里 `p.id`）、新 wiki 列表卡（本设计用 `list_pages().id`）——三者都是同一个 page id。

**Agent（现状＝无入口）**
- 应用内零入口。`mcp_server.py:54-116` 定义 6 工具：`search_localkb / localkb_status / localkb_build / save_synthesis / list_wiki / get_wiki_page`。
- 端口 8770（`config.py:96`，`DAEMON_URL`=`http://127.0.0.1:8770`，`config.py:98`）。
- 接入命令路径在 `MCP接入说明.md:25/34-37/50-51` 全是占位符。
- "当前会话不热加载、要新开会话"：`MCP接入说明.md:59`。
- `mcp_server.py` 用 `sys.executable` 拉起 server（:42），自身路径＝`C.APP / "mcp_server.py"`（`config.py:15` APP=文件所在目录）。

**对话（现状）**
- DOM：`panel-chat`（`index.html:102-113`）含 `.chat-hint`（:104-107，提示去设置填 key）、`#chat-log`、`#chat-q`、`#chat-go`。
- JS：`doChat()`（`app.js:784-821`）、`addSaveBtn()`（:757-783）、`addBubble/addSources`（:743-755）。
- 设置里「LLM 服务商」块：`index.html:118-127`（`#set-provider / #set-base / #set-key / #set-model / #set-keyurl` + 两条 hint）。
- 相关 JS：`PROVIDERS/PROVIDER_NAMES`（`app.js:20-28`）、`applyProvider()`（:833-837）、`provSel` 填充（:831-832）、`openSettings()` 回填（:839-850）、`#set-save` 保存（:875-878）、`cfg()/saveCfg()`（:46-47）、`needKey()`（:552-556）。

---

## 3. 数据模型 / 存储

**本设计不新增任何持久化 schema。** 三页全部消费既有端点与既有 sidecar：

- wiki 页：读 `data/wiki/index.json` 经 `list_pages()/get_page()` 暴露（结构见 `config.py:40-46`、`wiki_store.py:347-371`）。`kind ∈ {answer, concept, topic}`；`by_agent` 布尔（agent 经 MCP 写回为 true）；`stale` 布尔（被新论文影响、待重生）。
- 对话设置：仍存 `localStorage["localkb.cfg"]`（`app.js:46-47`），字段 `{provider, base, api_key, model}`，格式**不变**——只把"写入时机"从「点保存」改成「onChange 即存」。
- Agent 页：新端点 `GET /agent/mcp-config` 是**纯计算、无持久化**（实时读 `sys.executable` 与 `config.py` 常量拼字符串）。

> 唯一新增的前端状态：`app.js` 顶部懒加载哨兵新增 `wikiLoaded / agentLoaded`（见 §5.1）。

---

## 4. 后端接口

只新增 **1 个端点**，其余全部复用。

### 4.1 新增：`GET /agent/mcp-config`

**目的**：把"这台机器上真实可用"的 MCP 接入命令算好返回给前端，取代 `MCP接入说明.md` 里的占位路径。

**入参**：无。

**出参 JSON 形状**：
```json
{
  "python": "D:\\Onedrive\\AI\\知识库应用\\LocalKB\\python\\python.exe",
  "mcp_server": "D:\\Onedrive\\AI\\知识库应用\\LocalKB\\app\\mcp_server.py",
  "daemon_url": "http://127.0.0.1:8770",
  "server_running": true,
  "wiki_schema_md": "D:\\...\\data\\wiki\\WIKI.md",
  "claude_cmd": "claude mcp add localkb -- \"D:\\...\\python.exe\" \"D:\\...\\mcp_server.py\"",
  "claude_cmd_user": "claude mcp add localkb --scope user -- \"D:\\...\\python.exe\" \"D:\\...\\mcp_server.py\"",
  "mcp_json": "{\n  \"mcpServers\": {\n    \"localkb\": {\n      \"command\": \"D:\\\\...\\\\python.exe\",\n      \"args\": [\"D:\\\\...\\\\mcp_server.py\"]\n    }\n  }\n}",
  "codex_toml": "[mcp_servers.localkb]\ncommand = \"D:\\\\...\\\\python.exe\"\nargs = [\"D:\\\\...\\\\mcp_server.py\"]"
}
```

**关键说明**
- `python`＝`sys.executable`：这是**当前正在跑 server.py 的解释器**，也正是 `mcp_server.py:42` 拉起 server 用的同一个，一定带齐 `requests` 等依赖。分发版下它＝`LocalKB/python/python.exe`，开发版＝你的 venv/python——都天然正确，无需硬编码。
- `mcp_server`＝`str(C.APP / "mcp_server.py")`（`config.py:15`）。
- `server_running`：本进程正在响应即为 true，直接给常量 `True`（能返回这个 JSON 说明服务在跑）。前端用它渲染"服务已在运行"绿点。
- `mcp_json` / `codex_toml`：直接给拼好的字符串，前端一键复制即用，无需前端再拼转义。JSON 里 Windows 反斜杠需 `\\` 转义——**在后端用 `json.dumps` 生成**，避免前端手工转义出错。
- `wiki_schema_md`＝`str(C.WIKI_SCHEMA_MD)`（`config.py:46`），对齐 `localkb_status` 里返回的同一路径（`mcp_server.py:139`），给"进阶：写回规约"板块指路。

**落点**：`server.py`，紧跟 `/health` 之后（`server.py:97` 后），与其它 setup/状态类端点同区。伪代码（贴近现有风格，`import` 就近，返回 dict 即 JSON）：

```python
# ── Agent / MCP 接入信息（给应用内 Agent 页，吐出本机真实可用的接入命令）──
@app.get("/agent/mcp-config")
def agent_mcp_config():
    py  = sys.executable                       # 正在跑本服务的解释器＝mcp_server 拉起 server 用的同一个
    mcp = str(C.APP / "mcp_server.py")
    def q(s): return '"' + s + '"'             # 路径带空格/中文，命令行统一加引号
    add_core = f'claude mcp add localkb -- {q(py)} {q(mcp)}'
    mcp_json = json.dumps(
        {"mcpServers": {"localkb": {"command": py, "args": [mcp]}}},
        ensure_ascii=False, indent=2)
    codex_toml = (f'[mcp_servers.localkb]\n'
                  f'command = {json.dumps(py)}\n'
                  f'args = [{json.dumps(mcp)}]')
    return {
        "python": py, "mcp_server": mcp,
        "daemon_url": C.DAEMON_URL, "server_running": True,
        "wiki_schema_md": str(C.WIKI_SCHEMA_MD),
        "claude_cmd": add_core,
        "claude_cmd_user": add_core.replace("claude mcp add localkb ",
                                            "claude mcp add localkb --scope user "),
        "mcp_json": mcp_json, "codex_toml": codex_toml,
    }
```
> `sys` / `json` / `C` 均已在 `server.py:8-11` import，无需新增依赖。

**需核实（1 处）**：`sys.executable` 在分发版里指向 `LocalKB/python/python.exe` 且该 python 能跑 `mcp_server.py`（它只依赖 stdlib+requests）。核实方法：分发机上启动应用后 `curl http://127.0.0.1:8770/agent/mcp-config`，把返回的 `claude_cmd` 原样贴进一个新 Claude Code 会话跑一次 `/mcp` 看能否列出 localkb。若分发版 server 由某 launcher 用非 python.exe 的宿主启动，则 `sys.executable` 可能是 launcher 自身——那时改为显式拼 `str(C.APP.parent / "python" / "python.exe")` 并加 `Path.exists()` 兜底。已核实存在 `LocalKB/python/`（见目录列举），此路径可作兜底。

### 4.2 复用端点（无改动，仅列前端新调用点）

| 端点 | 方法 | 前端新调用处 | 出参要点 |
|---|---|---|---|
| `/wiki/list` | GET | `loadWikiList()`（新） | `{pages:[{id,kind,title,generated_at,generated_by,stale,by_agent,n_sources}]}` |
| `/wiki/page/{id}` | GET | `openWikiPage()`（已存，:568） | 见 `get_page` :368-371 |
| `/wiki/concept` | POST | wiki 页"新建综述"（新） | body `{concept, provider, base_url, api_key, model, topk}`；返回 `{ok,id,title,kind,cached,indexed,n_sources}` |
| `/wiki/regenerate/{id}` | POST | 复用 `wireWikiModal` 重生（已存，:602） | 同上 |
| `/wiki/page/{id}` | DELETE | wiki 卡"删除"+复用 `discardWiki`（:573） | `{ok,id,...}` |

---

## 5. 前端交互与状态

### 5.1 导航骨架（index.html + app.js）

**index.html:12-17 → 替换为 6 按钮**（默认 active 迁到库总览）：

```html
<nav class="tabs">
  <button class="tab active" data-tab="dashboard">📊 库总览</button>
  <button class="tab" data-tab="browse">📚 浏览</button>
  <button class="tab" data-tab="wiki">📖 wiki页</button>
  <button class="tab" data-tab="search">🔍 检索</button>
  <button class="tab" data-tab="agent">🤖 Agent</button>
  <button class="tab" data-tab="chat">💬 对话</button>
</nav>
```

配套 panel 改动：
- `panel-search`（`index.html:31`）加 `hidden`（不再是默认页）。
- `panel-dashboard`（:56）去掉 `hidden`（成为默认页）。
- 新增 `panel-wiki`、`panel-agent`（DOM 见 §5.2、§5.3），放在 `panel-chat` 之前，均带 `hidden`。

> 图标不撞车说明：📚浏览＝一摞书（文献列表），📖wiki＝翻开的书（综合读物），两者字形明显不同；🤖Agent 与 💬对话区分清楚。

**app.js 懒加载哨兵**（:116）扩展：
```js
let dashLoaded = false, browseLoaded = false, wikiLoaded = false, agentLoaded = false;
```

**app.js:117-124 tab 点击监听**——在末尾加两分支：
```js
if (tab === "wiki")  { loadWikiList(wikiLoaded ? "silent" : "loud"); wikiLoaded = true; }
if (tab === "agent" && !agentLoaded) { agentLoaded = true; loadAgentConfig(); }
```
> wiki 用 `silent/loud` 参数（同 dashboard 语义）：首次进 loud（显"加载中"），再次进 silent（后台刷新、不闪空）。这样删除/重生/新建后回列表也能静默刷新。

**app.js:126-131 `switchTab(tab)`**——加同样两分支（保持与点击监听一致）：
```js
if (tab === "wiki")  { loadWikiList(wikiLoaded ? "silent" : "loud"); wikiLoaded = true; }
if (tab === "agent" && !agentLoaded) { agentLoaded = true; loadAgentConfig(); }
```

**默认落地库总览**：改动仅需上面的 DOM（active 在 dashboard、dashboard 去 hidden）。但注意 `dashLoaded` 初始 `false`，而页面首帧 dashboard 已可见却**不会自动触发 `loadDashboard`**（懒加载只在点击/switchTab 时跑）。故在 `maybeWizard()` 之后、`IIFE` 收尾处补一次冷启动加载：
```js
// 冷启动：默认页＝库总览，主动加载一次（此前只有点 tab 才加载）
if (!dashLoaded) { loadDashboard("loud"); dashLoaded = true; }
```
放在 `app.js:1458 maybeWizard();` 调用之后。若首启向导会弹出遮住 dashboard，可放到 `maybeWizard` 的 `else`（已索引）分支里；简单起见放全局末尾即可，向导关掉后 dashboard 已就绪。**需核实**：向导显示时提前 `loadDashboard` 是否有副作用——`loadDashboard` 只 `GET /stats`，无副作用，安全。

### 5.2 wiki 页 `panel-wiki`

**DOM 草案**（插在 `index.html` 的 `panel-chat` 之前）：
```html
<main id="panel-wiki" class="panel" data-panel="wiki" hidden>
  <div class="wiki-page">
    <!-- 顶部：新建综述 + 筛选 -->
    <div class="wk-bar">
      <div class="wk-new">
        <input id="wk-concept" type="text"
          placeholder="输入一个概念/主题，生成一页综述（如：认罪认罚从宽的证据法争议）" />
        <button id="wk-gen" class="primary-btn">＋ 生成综述</button>
      </div>
      <div class="wk-tools">
        <select id="wk-kind" title="按类型筛选">
          <option value="">全部类型</option>
          <option value="answer">📝 对话沉淀</option>
          <option value="concept">🧩 概念综述</option>
          <option value="topic">🗂 主题综述</option>
        </select>
        <label class="wk-agent-only"><input type="checkbox" id="wk-agent" /> 只看 🤖 未核验</label>
      </div>
    </div>
    <div id="wk-msg" class="msg"></div>
    <div id="wk-list" class="wk-list"></div>
  </div>
</main>
```

**每张卡片结构**（由 `loadWikiList` 渲染，字段全来自 `list_pages`）：
```
[类型徽标] 标题
基于 N 篇 · 生成于 YYYY-MM-DD · 模型 xxx  [⚠ 可能已过时]  [🤖 未核验 / 📝 我保存的]
[📖 打开] [🔍 按标题检索] [↻ 重新生成] [🗑 删除]
```

**`loadWikiList(mode)` 函数设计**（新增，放综合层区块 `app.js:541` 附近）：
```js
let WK = { kind: "", agentOnly: false, pages: [] };
async function loadWikiList(mode) {
  const box = $("#wk-list");
  if (mode !== "silent") box.innerHTML = `<div class="wk-loading">加载综合页中…</div>`;
  try {
    const d = await jget("/wiki/list");
    WK.pages = d.pages || [];
    renderWikiList();
  } catch (e) {
    box.innerHTML = `<div class="wk-loading">加载失败：${esc(e.message)}</div>`;
  }
}
function renderWikiList() {
  const box = $("#wk-list");
  let list = WK.pages;
  if (WK.kind) list = list.filter((p) => (p.kind || "answer") === WK.kind);
  if (WK.agentOnly) list = list.filter((p) => p.by_agent);
  if (!WK.pages.length) {
    box.innerHTML = `<div class="wk-empty">
      <div class="wk-empty-ic">📖</div>
      <div class="wk-empty-h">还没有综合页</div>
      <div class="wk-empty-s">在上面输入一个概念点「生成综述」，或在「💬 对话」里问答后「保存此答案」，
        或让「🤖 Agent」调 save_synthesis 写回——综合会在这里累积。</div></div>`;
    return;
  }
  if (!list.length) { box.innerHTML = `<div class="wk-empty">当前筛选下没有综合页。</div>`; return; }
  box.innerHTML = "";
  list.forEach((p) => box.appendChild(wikiCard(p)));
}
const WK_KIND = { answer: "📝 对话沉淀", concept: "🧩 概念综述", topic: "🗂 主题综述" };
function wikiCard(p) {
  const div = document.createElement("div");
  div.className = "wk-card" + (p.stale ? " stale" : "");
  const kind = WK_KIND[p.kind || "answer"] || (p.kind || "");
  const prov = p.by_agent
    ? `<span class="wk-flag agent" title="agent 写回、未经人工核验">🤖 未核验</span>`
    : `<span class="wk-flag" title="你保存/生成的综合页">📝 我保存的</span>`;
  const stale = p.stale ? `<span class="wk-flag stale" title="有新论文可能影响此综合，建议重生">⚠ 可能已过时</span>` : "";
  div.innerHTML =
    `<div class="wk-card-head"><span class="wk-badge k-${esc(p.kind || "answer")}">${kind}</span>` +
      `<span class="wk-title">${esc(p.title || "(无标题)")}</span></div>` +
    `<div class="wk-card-meta">基于 ${num(p.n_sources)} 篇 · ${esc((p.generated_at || "").slice(0, 10) || "未知日期")}` +
      ` · 模型 ${esc(p.generated_by || "未知")} ${prov} ${stale}</div>` +
    `<div class="wk-card-btns">` +
      `<button class="ghost2 wk-open">📖 打开</button>` +
      `<button class="ghost2 wk-tosearch">🔍 按标题检索</button>` +
      `<button class="ghost2 wk-regen">↻ 重新生成</button>` +
      `<button class="ghost2 danger wk-del">🗑 删除</button>` +
    `</div>`;
  div.querySelector(".wk-open").addEventListener("click", () => openWikiPage(p.id));
  div.querySelector(".wk-tosearch").addEventListener("click", () => switchToSearch(p.title || ""));
  div.querySelector(".wk-del").addEventListener("click", (e) =>
    discardWiki(p.id, () => loadWikiList("silent"), e.currentTarget));   // 删完静默刷新列表
  const rb = div.querySelector(".wk-regen");
  rb.addEventListener("click", async () => {
    if (needKey()) return;
    if (p.kind === "answer") { alert("「对话沉淀」页由对话生成，请回「💬 对话」重新提问后再保存。"); return; }
    const old = rb.textContent; rb.disabled = true; rb.textContent = "重新生成中…";
    try {
      const r = await jpost("/wiki/regenerate/" + encodeURIComponent(p.id), llmBody({}));
      if (!r.ok) throw new Error(r.detail || "失败");
      await openWikiPage(r.id);          // 打开新版本
      loadWikiList("silent");            // 后台刷新列表（日期/stale 更新）
    } catch (e) { alert("重新生成失败：" + (e.message || e)); }
    finally { rb.disabled = false; rb.textContent = old; }
  });
  return div;
}
```
> `answer` 页不可重生（`wiki_store.py:340-341` 会抛「answer 页由对话生成」），前端提前拦截给友好提示，避免 400。

**"生成综述"事件**（→ `/wiki/concept`）：
```js
$("#wk-gen").addEventListener("click", genConcept);
$("#wk-concept").addEventListener("keydown", (e) => { if (e.key === "Enter") genConcept(); });
async function genConcept() {
  const c = $("#wk-concept").value.trim();
  if (!c) { $("#wk-msg").textContent = "请先输入一个概念或主题。"; return; }
  if (needKey()) return;                                  // 复用综合层的 key 校验（app.js:552）
  const btn = $("#wk-gen"); btn.disabled = true; btn.textContent = "生成中…"; $("#wk-msg").textContent = "";
  try {
    const r = await jpost("/wiki/concept", llmBody({ concept: c }));  // llmBody 复用 app.js:548
    if (!r.ok) throw new Error(r.detail || "生成失败");
    $("#wk-concept").value = "";
    await openWikiPage(r.id);      // 立刻打开（复用现有弹窗）
    loadWikiList("silent");        // 后台把新页并进列表
    $("#wk-msg").textContent = r.cached ? "已命中已有综合（未重复生成）。" : "已生成新综述并加入列表。";
  } catch (e) { $("#wk-msg").textContent = "生成失败：" + (e.message || e); }
  finally { btn.disabled = false; btn.textContent = "＋ 生成综述"; }
}
```

**筛选事件**：
```js
$("#wk-kind").addEventListener("change", () => { WK.kind = $("#wk-kind").value; renderWikiList(); });
$("#wk-agent").addEventListener("change", () => { WK.agentOnly = $("#wk-agent").checked; renderWikiList(); });
```

**复用 `renderWiki` 弹窗**：wiki 页的「📖 打开」直接调既有 `openWikiPage(id)`（`app.js:568`）——它 `GET /wiki/page/{id}` 后调 `renderWiki` 弹出 `#wiki-modal`。**弹窗里删除/重生后需回列表刷新**：`wireWikiModal()`（:596-613）现有的 discard 回调是 `if ($("#q").value.trim()) doSearch()`（刷新检索）。改成同时刷 wiki 列表：
```js
// app.js:599 discard 回调改为：
if (disc) disc.addEventListener("click", () => discardWiki($("#wiki-regen").dataset.id,
  () => {
    $("#wiki-modal").hidden = true;
    if (wikiLoaded) loadWikiList("silent");     // 从 wiki 页删的，刷新列表
    if ($("#q").value.trim()) doSearch();        // 从检索删的，刷新检索（保留原行为）
  }, disc));
```
弹窗里的 `#wiki-regen` 成功后也补一句 `if (wikiLoaded) loadWikiList("silent");`（`app.js:609` `await openWikiPage(r.id);` 之后）。

**检索 ↔ wiki 双向连通**
- **检索行 →「📖在wiki页打开」**：在 `resultCard`（`app.js:176-194`）里，`is_wiki` 行追加一个按钮（现有已有 `wiki-discard` 于 :181）。在 `discard` 变量旁加：
  ```js
  const gotoWiki = r.is_wiki ? `<button class="ghost2 wiki-goto" title="在 wiki 页查看这条综合">📖 在wiki页打开</button>` : "";
  ```
  加进 `div.innerHTML` 的按钮行（`app.js:187` 那行 `+ discard` 后接 `+ gotoWiki`），并绑事件：
  ```js
  const gbtn = div.querySelector(".wiki-goto");
  if (gbtn) gbtn.addEventListener("click", () => { switchTab("wiki"); openWikiPage(r.key); });
  ```
  > `r.key`＝wiki 页 id（§2 已核实），可直接 `openWikiPage`。切到 wiki tab 让用户回得到列表，再弹出该页。
- **wiki 卡 →「🔍按标题检索」**：已在 `wikiCard` 里用 `switchToSearch(p.title)`（切检索 tab 并执行检索，复用 `app.js:133`）。

### 5.3 Agent 页 `panel-agent`

**6 个板块**：① 价值锚点 ② 一键接入（真实命令，从 `/agent/mcp-config`）③ 工具清单大白话表 ④ prompt 示例 ⑤ 按分类的现状说明 ⑥ SAC/写回规约指路。

**DOM 骨架**（`index.html`，`panel-chat` 之前）：
```html
<main id="panel-agent" class="panel" data-panel="agent" hidden>
  <div class="agent-page">

    <!-- ① 价值锚点 -->
    <section class="ag-hero">
      <div class="ag-hero-ic">🤖</div>
      <div>
        <h2>把你的文库接进 Claude Code / Codex</h2>
        <p>让 AI 助手直接「查你的库、读页码、写回综合」——不用复制粘贴、不用切窗口。
           你的文献和综合始终留在本机，只有你选的模型服务商会看到检索请求。</p>
      </div>
      <div class="ag-run" id="ag-run"><span class="ag-dot"></span><span id="ag-run-txt">检查服务状态…</span></div>
    </section>

    <!-- ② 一键接入 -->
    <section class="ag-card">
      <h3>① 一键接入（本机真实路径已填好）</h3>
      <div class="ag-tabs">
        <button class="ag-t active" data-agtab="claude">Claude Code</button>
        <button class="ag-t" data-agtab="codex">Codex</button>
        <button class="ag-t" data-agtab="json">通用 mcp.json</button>
      </div>

      <div class="ag-pane" data-agpane="claude">
        <p class="ag-step">在任意 Claude Code 会话里粘贴运行这一行：</p>
        <div class="ag-code"><code id="ag-claude"></code><button class="ag-copy" data-copy="ag-claude">复制</button></div>
        <label class="ag-scope"><input type="checkbox" id="ag-scope-user" /> 让所有项目都能用（加 <code>--scope user</code>）</label>
      </div>

      <div class="ag-pane" data-agpane="codex" hidden>
        <p class="ag-step">编辑 <code>~/.codex/config.toml</code>，粘贴：</p>
        <div class="ag-code"><code id="ag-codex"></code><button class="ag-copy" data-copy="ag-codex">复制</button></div>
      </div>

      <div class="ag-pane" data-agpane="json" hidden>
        <p class="ag-step">项目根目录建 <code>.mcp.json</code>（或填进你的客户端 MCP 配置），粘贴：</p>
        <div class="ag-code"><code id="ag-json"></code><button class="ag-copy" data-copy="ag-json">复制</button></div>
      </div>

      <div class="ag-warn">⚠ <b>配好后要新开一个会话才生效</b>——当前正在运行的 Claude Code / Codex 会话不会热加载新 MCP。新会话里输入 <code>/mcp</code> 应能看到 <code>localkb</code>（6 个工具）。</div>
    </section>

    <!-- ③ 工具清单大白话表 -->
    <section class="ag-card">
      <h3>② 接好后 AI 能做这 6 件事</h3>
      <table class="ag-tools"><thead><tr><th>你可以说</th><th>它会调用</th><th>做什么</th></tr></thead>
      <tbody id="ag-tools"></tbody></table>
    </section>

    <!-- ④ prompt 示例 -->
    <section class="ag-card">
      <h3>③ 直接这样对它说</h3>
      <ul class="ag-prompts" id="ag-prompts"></ul>
    </section>

    <!-- ⑤ 按分类现状说明 -->
    <section class="ag-card">
      <h3>④ 关于「深索」——AI 能读到多深，取决于这个</h3>
      <p class="ag-note">AI 检索时，<b>已深索</b>的文献能返回精确到页码的正文段落；<b>未深索</b>的只能返回题录+摘要。
        想让某个方向的问答更扎实，先去「📚 浏览」把该收藏夹/主题里有 PDF 的文献深索了。</p>
      <div id="ag-deep" class="ag-deep">读取深索进度…</div>
    </section>

    <!-- ⑥ 写回规约指路 -->
    <section class="ag-card">
      <h3>⑤ 进阶：让 AI 写回的综合更可信</h3>
      <p class="ag-note">AI 用 <code>save_synthesis</code> 写回的综合页会标 🤖「未核验」，默认可被检索命中、但检索排序里不会盖过原始文献；你可在「📖 wiki页」一键删除不满意的。
        AI 写回时应遵守本机的写回规约（引用格式/来源纪律），规约文件在：</p>
      <div class="ag-code"><code id="ag-schema"></code><button class="ag-copy" data-copy="ag-schema">复制路径</button></div>
    </section>

  </div>
</main>
```

**`loadAgentConfig()`**（新增）：
```js
async function loadAgentConfig() {
  // 服务状态点
  try {
    const d = await jget("/agent/mcp-config");
    AG.cfg = d;
    $("#ag-run").classList.toggle("ok", !!d.server_running);
    $("#ag-run-txt").textContent = d.server_running
      ? "本地服务已在运行 · 127.0.0.1:8770" : "本地服务未就绪";
    renderAgentCmds();
    $("#ag-schema").textContent = d.wiki_schema_md || "";
  } catch (e) {
    $("#ag-run-txt").textContent = "读取接入信息失败：" + e.message;
  }
  renderAgentTools();     // 静态表，无需等网络
  renderAgentPrompts();
  loadAgentDeep();        // 复用 /index/status
}
let AG = { cfg: null };
function renderAgentCmds() {
  const d = AG.cfg; if (!d) return;
  $("#ag-claude").textContent = $("#ag-scope-user").checked ? d.claude_cmd_user : d.claude_cmd;
  $("#ag-codex").textContent = d.codex_toml;
  $("#ag-json").textContent = d.mcp_json;
}
$("#ag-scope-user")?.addEventListener("change", renderAgentCmds);
// tab 切换
$$(".ag-t").forEach((t) => t.addEventListener("click", () => {
  $$(".ag-t").forEach((x) => x.classList.toggle("active", x === t));
  const k = t.dataset.agtab;
  $$(".ag-pane").forEach((p) => { p.hidden = p.dataset.agpane !== k; });
}));
// 复制按钮（复用错误日志里 navigator.clipboard 的兜底思路，app.js:998-1008）
$$(".ag-copy").forEach((b) => b.addEventListener("click", async () => {
  const txt = ($("#" + b.dataset.copy) || {}).textContent || "";
  try { await navigator.clipboard.writeText(txt); b.textContent = "已复制 ✓"; }
  catch (_) { b.textContent = "复制失败，请手动选中"; }
  setTimeout(() => (b.textContent = b.dataset.copy === "ag-schema" ? "复制路径" : "复制"), 1500);
}));
```

**工具表 / prompt（静态，成稿见 §6）**：
```js
const AG_TOOLS = [
  ["“查库里关于 XX 的文献”", "search_localkb", "在你的文库里做混合检索，返回带期刊分级、页码、可回溯引用的结果"],
  ["“库里现在有多少、索引到哪了”", "localkb_status", "看索引各档进度、篇数，以及已存了多少综合页"],
  ["“把库更新一下 / 深索一下”", "localkb_build", "触发建库或深索（加了新文献后增量更新）"],
  ["“把这个综述存进库”", "save_synthesis", "把 AI 综合出的结论写回成一页带引用的 wiki，下次能被检索命中"],
  ["“库里有没有现成的综述”", "list_wiki", "列已存的综合页，避免重复造轮子"],
  ["“打开那页综述给我看”", "get_wiki_page", "取某页综合的正文 + 来源页码引用"],
];
function renderAgentTools() {
  $("#ag-tools").innerHTML = AG_TOOLS.map(
    ([say, tool, desc]) => `<tr><td>${esc(say)}</td><td><code>${esc(tool)}</code></td><td>${esc(desc)}</td></tr>`).join("");
}
const AG_PROMPTS = [
  "帮我查库里关于「认罪认罚从宽对司法信任的影响」的权威文献，按期刊层级排。",
  "先 list_wiki 看有没有现成综述；没有的话检索后综合一版，再 save_synthesis 存回来。",
  "把库里关于「社会观护」的核心论点综述一下，每个论断带页码引用。",
  "库里最近加的文献深索了吗？没有的话帮我 localkb_build 深索一下。",
];
function renderAgentPrompts() {
  $("#ag-prompts").innerHTML = AG_PROMPTS.map((p) => `<li>${esc(p)}</li>`).join("");
}
async function loadAgentDeep() {
  try {
    const st = await jget("/index/status");
    const withPdf = st.with_pdf || 0, deep = st.deep_done || 0;
    const pct = withPdf ? Math.round((deep / withPdf) * 100) : 0;
    $("#ag-deep").innerHTML = withPdf
      ? `已深索 <b>${num(deep)}</b> / 有 PDF ${num(withPdf)} 篇（${pct}%）。` +
        (deep < withPdf ? ` <a class="ag-link" id="ag-godeep">去「浏览」深索更多 →</a>` : ` 已全部深索完成 ✓`)
      : `暂无可深索文献（库里没有带 PDF 的文献，或尚未建库）。`;
    const g = $("#ag-godeep");
    if (g) g.addEventListener("click", () => switchTab("browse"));
  } catch (e) { $("#ag-deep").textContent = "读取深索进度失败：" + e.message; }
}
```
> `st.mode/with_pdf/deep_done` 字段与库总览 `deepProgressCard`（`app.js:342-368`）消费的是同一个 `/index/status`，字段可信。

### 5.4 对话页改造

**(a) `.chat-hint` 换成劝退+引导**（`index.html:104-107` 整块替换）：
```html
<div class="chat-hint">
  <div class="ch-h">💬 对话适合「随手问一句」</div>
  <p>它基于你本地文库回答、带页码引用，但一次只看少量来源、答案不会自动沉淀。</p>
  <p><b>要做认真的检索、综述、跨会话累积？更推荐用「🤖 Agent」</b>——让 Claude Code / Codex 直接调你的库，能查、能读页码、能把综合写回、下次复用。</p>
  <button id="chat-to-agent" class="ghost2">→ 去 Agent 页看怎么接入</button>
  <p class="ch-mini">继续用对话也行：下面「模型设置」里填一个 LLM 的 API Key（DeepSeek / 硅基流动 / Kimi / 智谱 / OpenAI 皆可）即可开问。硅基流动可复用检索引擎已配好的 key。</p>
</div>
```
事件：`$("#chat-to-agent")?.addEventListener("click", () => switchTab("agent"));`
> 注意 `doChat`（`app.js:789`）里 `const hint = $(".chat-hint"); if (hint) hint.remove();`——首次发消息会删掉整个 hint（含新按钮），符合预期（开聊后不再劝退）。

**(b) 把「LLM 服务商」块内联进对话页成折叠区**（默认收起、onChange 即存）。

DOM：在 `.chat-input`（`index.html:109`）**上方**插入折叠区，把 `index.html:118-127` 的服务商块搬进来（`id` 全部保留，避免改 `applyProvider`/`openSettings` 的选择器）：
```html
<details id="chat-model" class="chat-model">
  <summary>⚙ 模型设置（对话用；检索不需要 key）</summary>
  <div class="cm-body">
    <label>服务商 <select id="set-provider"></select></label>
    <label>Base URL <input id="set-base" placeholder="https://…/v1" /></label>
    <label>API Key <input id="set-key" type="password" placeholder="sk-…" /></label>
    <label>模型 <input id="set-model" placeholder="deepseek-chat" /></label>
    <p class="hint" id="set-keyurl"></p>
    <p class="hint">🔒 Key 只存本机浏览器（localStorage）与本地服务内存，不上传第三方（除你选的模型服务商）。</p>
    <p class="hint">💡 硅基流动有免费模型：对话推荐 <code>Qwen/Qwen3-8B</code>。选「硅基流动」且此处留空时，自动复用「设置→检索引擎」里的 SiliconFlow key。</p>
    <p class="hint cm-saved" id="cm-saved">改动即时保存 ✓</p>
  </div>
</details>
```
从设置弹窗 `index.html:116-127` 里**删掉**原 `<h3>设置 · LLM 服务商…</h3>` + 4 个 label + `#set-keyurl` + 两条 hint（即 :118-127）。设置弹窗保留检索引擎/深索/SAC/学科/日志各块。

**DOM 搬迁与事件重绑清单**
1. `#set-provider / #set-base / #set-key / #set-model / #set-keyurl` 五个 id **原样保留**——`provSel` 填充（`app.js:831-832`）、`applyProvider`（:833-837）、`openSettings` 里对这些 id 的回填（:841-845）都靠 querySelector，DOM 搬家后选择器仍命中，**无需改这些函数**。
2. **`openSettings()`（`app.js:839-850`）**：它现在会在打开设置弹窗时回填 provider 字段。由于这些字段已搬出弹窗，回填仍应保留（首帧要填对话页的模型设置）。保留 `openSettings` 里 `provSel.value=… / applyProvider / set-base/model/key` 那几行（:841-845），删不删无害——但为确保对话页 `#chat-model` 一进页面就显示已存配置，新增一个初始化调用：
   ```js
   // 冷启动即回填对话页模型设置（原逻辑只在打开设置弹窗时回填）
   function initChatModel() {
     const c = cfg();
     provSel.value = c.provider || "siliconflow";
     applyProvider(provSel.value, true);
     $("#set-base").value  = c.base  || PROVIDERS[provSel.value].base;
     $("#set-model").value = c.model || PROVIDERS[provSel.value].model;
     $("#set-key").value   = c.api_key || "";
   }
   ```
   在 IIFE 末尾（`provSel` 填充之后）调一次 `initChatModel();`。
3. **onChange 即存**（替代保存按钮）——`saveCfg` 不变（`app.js:47`），新增即时保存：
   ```js
   function saveChatModel() {
     saveCfg({ provider: provSel.value, base: $("#set-base").value.trim(),
               api_key: $("#set-key").value.trim(), model: $("#set-model").value.trim() });
     const s = $("#cm-saved"); if (s) { s.textContent = "已保存 ✓"; s.classList.add("flash");
       setTimeout(() => s.classList.remove("flash"), 800); }
   }
   ["#set-base", "#set-key", "#set-model"].forEach((sel) =>
     $(sel).addEventListener("input", saveChatModel));   // 输入即存
   provSel.addEventListener("change", () => {            // 换服务商：先套预设默认，再存
     applyProvider(provSel.value, false); saveChatModel();
   });
   ```
   > 现有 `provSel.addEventListener("change", () => applyProvider(provSel.value, false));`（`app.js:838`）**替换**为上面这个（合并「套默认+即存」）。`applyProvider(k,false)` 会把 base/model 重置成该服务商默认值（:835），随后 `saveChatModel` 落盘，符合"换商即换默认并保存"。
4. **`#set-save`（`app.js:875-878`）与 `#set-close`**：`#set-save` 现在只负责 LLM 块的保存，块搬走后它不再需要保存 provider——但设置弹窗里还有检索引擎/SAC 等**各自有独立保存按钮**，`#set-save` 原本只 `saveCfg(provider…)` 那一行可整段删掉，`#set-close` 保留（关闭弹窗）。**需核实**：`#set-save` 是否被其它逻辑依赖——已核实全文仅 `app.js:875` 绑定一次，删其 body 安全；建议把 `#set-save` 按钮本身从设置弹窗 footer 去掉，只留「关闭」，避免用户以为不点保存就丢设置。
5. **`needKey()`（:552）与 `doChat` 的 key 校验（:788）** 不变——它们读 `cfg()`，onChange 已即时写入 `cfg`，逻辑自洽。

**(c) "保存此答案"成功后加"→已进wiki页"跳转**（改 `addSaveBtn`，`app.js:767-782`）：
```js
// 成功分支（app.js:776-777）改为：
btn.textContent = "✓ 已沉淀为综合页";
msg.innerHTML = (r.indexed ? "（已入库，检索/wiki页可见）" : "（已存盘，重建索引后可检索）") +
  ` <a class="save-goto" href="#">→ 去 wiki 页查看</a>`;
const go = msg.querySelector(".save-goto");
if (go) go.addEventListener("click", (e) => {
  e.preventDefault();
  switchTab("wiki");
  if (r.id) openWikiPage(r.id);      // 直接弹出刚存的这页
});
```
> **需核实**：`/wiki/answer` 返回是否含 `id`——已核实 `server.py:378` 返回 `"id": meta["id"]`，故 `r.id` 可用，能精确打开刚沉淀的页。

---

## 6. 文案（成稿）

> 以下均可直接使用。§5 的 DOM/JS 里已内联大部分；这里汇总，便于校对语气一致。

### 导航
`📊 库总览` `📚 浏览` `📖 wiki页` `🔍 检索` `🤖 Agent` `💬 对话`

### wiki 页
- 新建输入占位：`输入一个概念/主题，生成一页综述（如：认罪认罚从宽的证据法争议）`
- 按钮：`＋ 生成综述`；类型筛选：`全部类型 / 📝 对话沉淀 / 🧩 概念综述 / 🗂 主题综述`；`只看 🤖 未核验`
- 卡片徽标：`📝 对话沉淀` `🧩 概念综述` `🗂 主题综述`；provenance：`🤖 未核验`（tip：`agent 写回、未经人工核验`）/ `📝 我保存的`；`⚠ 可能已过时`（tip：`有新论文可能影响此综合，建议重生`）
- 卡片元信息：`基于 N 篇 · YYYY-MM-DD · 模型 xxx`
- 卡片按钮：`📖 打开` `🔍 按标题检索` `↻ 重新生成` `🗑 删除`
- 空态（全空）：标题 `还没有综合页`；副：`在上面输入一个概念点「生成综述」，或在「💬 对话」里问答后「保存此答案」，或让「🤖 Agent」调 save_synthesis 写回——综合会在这里累积。`
- 空态（筛选无）：`当前筛选下没有综合页。`
- 生成中提示：`生成中…`；成功：`已生成新综述并加入列表。` / 命中缓存：`已命中已有综合（未重复生成）。`
- answer 页点重生：`「对话沉淀」页由对话生成，请回「💬 对话」重新提问后再保存。`
- 删除确认（复用 `discardWiki`，`app.js:575`）：`删除这条本地综合页？会同时删文件、索引与检索行——不影响文献库，可日后重新生成。`
- 检索行新按钮：`📖 在wiki页打开`（tip：`在 wiki 页查看这条综合`）

### Agent 页
- 标题：`把你的文库接进 Claude Code / Codex`
- 价值锚点：`让 AI 助手直接「查你的库、读页码、写回综合」——不用复制粘贴、不用切窗口。你的文献和综合始终留在本机，只有你选的模型服务商会看到检索请求。`
- 服务状态：`本地服务已在运行 · 127.0.0.1:8770` / `本地服务未就绪` / `检查服务状态…`
- 板块①标题：`① 一键接入（本机真实路径已填好）`；tab：`Claude Code / Codex / 通用 mcp.json`
- Claude 步骤：`在任意 Claude Code 会话里粘贴运行这一行：`；勾选：`让所有项目都能用（加 --scope user）`
- Codex 步骤：`编辑 ~/.codex/config.toml，粘贴：`
- json 步骤：`项目根目录建 .mcp.json（或填进你的客户端 MCP 配置），粘贴：`
- **醒目提示（成稿，红/黄底）**：`⚠ 配好后要新开一个会话才生效——当前正在运行的 Claude Code / Codex 会话不会热加载新 MCP。新会话里输入 /mcp 应能看到 localkb（6 个工具）。`
- 板块②标题：`② 接好后 AI 能做这 6 件事`；表头：`你可以说 / 它会调用 / 做什么`（6 行内容见 §5.3 `AG_TOOLS`，全大白话）
- 板块③标题：`③ 直接这样对它说`（4 条 prompt 见 §5.3 `AG_PROMPTS`）
- 板块④标题：`④ 关于「深索」——AI 能读到多深，取决于这个`；说明：`AI 检索时，已深索的文献能返回精确到页码的正文段落；未深索的只能返回题录+摘要。想让某个方向的问答更扎实，先去「📚 浏览」把该收藏夹/主题里有 PDF 的文献深索了。`；进度句：`已深索 D / 有 PDF N 篇（P%）。` + `去「浏览」深索更多 →` / `已全部深索完成 ✓`
- 板块⑤标题：`⑤ 进阶：让 AI 写回的综合更可信`；说明：`AI 用 save_synthesis 写回的综合页会标 🤖「未核验」，默认可被检索命中、但检索排序里不会盖过原始文献；你可在「📖 wiki页」一键删除不满意的。AI 写回时应遵守本机的写回规约（引用格式/来源纪律），规约文件在：`
- 复制按钮：`复制` / `复制路径` / `已复制 ✓` / `复制失败，请手动选中`

### 对话页
- 劝退卡标题：`💬 对话适合「随手问一句」`
- 正文：`它基于你本地文库回答、带页码引用，但一次只看少量来源、答案不会自动沉淀。`
- 引导：`要做认真的检索、综述、跨会话累积？更推荐用「🤖 Agent」——让 Claude Code / Codex 直接调你的库，能查、能读页码、能把综合写回、下次复用。`
- 按钮：`→ 去 Agent 页看怎么接入`
- 小字：`继续用对话也行：下面「模型设置」里填一个 LLM 的 API Key（DeepSeek / 硅基流动 / Kimi / 智谱 / OpenAI 皆可）即可开问。硅基流动可复用检索引擎已配好的 key。`
- 折叠区标题：`⚙ 模型设置（对话用；检索不需要 key）`；即存提示：`改动即时保存 ✓` / `已保存 ✓`
- 保存答案成功：`✓ 已沉淀为综合页` + `（已入库，检索/wiki页可见）`/`（已存盘，重建索引后可检索）` + `→ 去 wiki 页查看`

---

## 7. 迁移 / 兼容 / 回归

- **无数据迁移、无索引重建**：本设计只加 UI + 1 个无副作用 GET 端点，不碰 LanceDB/sidecar/建库流程。存量用户升级后：库总览成为默认页、原有综合页立即在 wiki 页可见（`/wiki/list` 一直返回它们，只是过去没界面）。
- **`localStorage["localkb.cfg"]` 兼容**：格式不变，老用户已存的 provider/key 直接被对话页折叠区读出（`initChatModel` 用 `cfg()`）。
- **灰度**：可无灰度直接全量（纯前端 + 只读端点）。若要保守，可先只上 `/agent/mcp-config` 端点与 Agent 页（读端点，零风险），wiki/对话改造随后。
- **回归清单**：
  1. 六个 tab 互相切换，各 panel 正确显隐；默认进库总览且自动加载。
  2. wiki 页：`/wiki/list` 渲染、kind 筛选、`只看未核验` 筛选、生成综述（命中缓存/新生成两条路径）、打开弹窗、重生（concept/topic 成功、answer 被拦）、删除后列表刷新。
  3. 检索行「📖在wiki页打开」跳转正确；wiki 卡「🔍按标题检索」跳转正确；确认三处删除 id 一致（删同一页后检索行与 wiki 卡都消失）。
  4. Agent 页命令含**真实路径**（非占位符）；复制可用；三 tab 切换；`--scope user` 勾选后命令变化；服务状态点。
  5. 对话页：劝退卡显示→发首条消息后消失；`→去Agent` 跳转；模型设置折叠区回填正确、改任一字段即存（刷新页面后保留）、换服务商套默认并保存；对话仍能正常流式；保存答案成功后「→去wiki页」精确打开该页。
  6. 设置弹窗删掉 LLM 块后，检索引擎/SAC/学科/日志各块仍正常（各自独立保存按钮不受影响）。
- **需核实（回归项）**：删掉设置弹窗 LLM 块后，`openSettings()`（`app.js:839-850`）里对 `#set-*` 的赋值会因元素已移到对话页而写到对话页字段——无害但要确认打开设置弹窗时不会意外清空对话页已填的 key。核实方法：填好 key→打开设置弹窗→关闭→回对话页看 key 还在。若被覆盖，把 `openSettings` 里 :841-845 五行删除（回填交给 `initChatModel`）。

---

## 8. 分步实现清单

> 规模：S≈半天内、M≈1天、L≈2天+。标注前后依赖。

1. **[S] 后端 `GET /agent/mcp-config`**：`server.py:97` 后加端点（§4.1）。无依赖。可先独测 `curl`。
2. **[S] 导航 DOM**：`index.html:12-17` 换 6 按钮 + active 迁 dashboard；`panel-search` 加 hidden、`panel-dashboard` 去 hidden。依赖：无。
3. **[S] 导航懒加载分支**：`app.js:116` 加 `wikiLoaded/agentLoaded`；:117-124 与 :126-131 各加 wiki/agent 分支；IIFE 末尾加库总览冷启动加载。依赖 #2。
4. **[M] wiki 页 DOM + `panel-wiki`**：`index.html` 加骨架（§5.2）。依赖 #2。
5. **[M] wiki 页 JS**：`loadWikiList/renderWikiList/wikiCard/genConcept` + 筛选事件（§5.2）；改 `wireWikiModal` 的 discard/regen 回调刷新列表。依赖 #4、#3。
6. **[S] 检索↔wiki 连通**：`resultCard`（`app.js:176-194`）加「📖在wiki页打开」；wiki 卡「🔍按标题检索」已在 #5。依赖 #5。
7. **[M] Agent 页 DOM + `panel-agent`**：`index.html` 加骨架（§5.3）。依赖 #2。
8. **[M] Agent 页 JS**：`loadAgentConfig` + cmds/tools/prompts/deep 渲染 + tab/复制/scope 事件（§5.3）。依赖 #7、#1。
9. **[M] 对话页 hint 改造**：换劝退卡 + `→去Agent` 事件。依赖 #3。
10. **[M] 对话页模型设置内联**：搬 `index.html:118-127` → `#chat-model` 折叠区；删设置弹窗对应块与 `#set-save` body；加 `initChatModel/saveChatModel` + onChange 绑定 + 换商即存（§5.4）。依赖 #9。
11. **[S] 保存答案跳转**：改 `addSaveBtn`（`app.js:776`）加「→去wiki页」。依赖 #5、#10。
12. **[S] 样式**：`style.css` 补 `.wk-* / .ag-* / .chat-model / .chat-hint` 新类（本设计未展开 CSS，按现有 `.dcard/.bcard/.set-section` 风格套色变量 `--accent` 等）。贯穿 #4-#11。
13. **[S] 文档同步**：`MCP接入说明.md` 顶部加一句「应用内『🤖 Agent』页有本机自动填好的接入命令，优先用那个」，避免占位路径误导。依赖 #8。
14. **[M] 全量回归**：按 §7 清单走一遍。依赖全部。

---

## 9. 风险与未决点

1. **`sys.executable` 在分发版是否指向可跑 mcp_server 的 python**（§4.1 已详述核实法）。这是 Agent 页"命令真实可用"的命门。**兜底**：端点里加 `Path(py).exists()` 校验，失败时改拼 `C.APP.parent / "python" / "python.exe"`（已确认该目录存在）。
2. **`openSettings()` 与内联模型设置的字段争用**（§7 需核实项）。低风险但要实测，必要时删 `openSettings` 的 LLM 回填 5 行。
3. **`--scope user` 命令拼接**：我用 `str.replace` 插入 `--scope user`（§4.1）。若未来 `claude mcp add` 语法变，替换会失配。**更稳**：后端直接返回两条完整命令（我已返回 `claude_cmd` 与 `claude_cmd_user` 两个独立字段），前端只做二选一——已规避，`replace` 仅在后端一处、易维护。
4. **对话页折叠区里 `#set-provider` 等 id 与设置弹窗曾经共存**：搬迁后全局仅一份这些 id，`provSel`（`app.js:831`）等选择器唯一命中。但若 CSS/其它脚本按"在弹窗内"定位过这些元素，需一并核实（已核实 app.js 内无 `.modal #set-*` 之类的层级选择器）。
5. **wiki 列表规模**：`list_pages` 无分页（`wiki_store.py:347`）。当前综合页量级小（个位到几十），一次性渲染无压力；若未来上千页需加前端分页/虚拟列表——**暂不做，标记为未决**。触发阈值：`WK.pages.length > 500` 时考虑。
6. **`/wiki/concept` 生成耗时**：走 LLM，几秒到十几秒。前端已置 `生成中…` + 禁用按钮，但无进度条（后端非流式）。可接受；若体验差，未来可让 `/wiki/concept` 改 SSE——**本期不做**。
7. **Agent 页服务状态点**：`server_running` 恒 true（能返回 JSON 即在跑）。它反映的是"web 服务"在跑，**不等于** MCP 已在某个 agent 里注册成功——文案已用"本地服务已在运行"而非"MCP 已接入"，避免误导。

—— 以上为完整实现级设计。相关源码锚点均已核实到 `文件:行号`；带「需核实」的三处（§4.1 sys.executable、§5.4/§7 openSettings 字段争用、§5.1 向导期 loadDashboard）均给出了核实方法与兜底方案。
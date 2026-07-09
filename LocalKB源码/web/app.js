// 本地知识库 · 前端逻辑（原生 JS，零依赖，离线）
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => document.querySelectorAll(s);

  // 前端错误自动上报到后端 logs/errors.log（方便测试时反馈问题）
  const reportErr = (msg, ctx) => {
    try {
      fetch("/log", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ level: "error", msg: String(msg || ""), ctx: ctx || "" }) });
    } catch (_) {}
  };
  window.addEventListener("error", (e) =>
    reportErr((e.message || "") + " @ " + (e.filename || "") + ":" + (e.lineno || ""), "window.onerror"));
  window.addEventListener("unhandledrejection", (e) =>
    reportErr((e.reason && (e.reason.stack || e.reason.message)) || e.reason, "unhandledrejection"));

  // 与后端 llm.py 对齐的服务商预设
  const PROVIDERS = {
    deepseek:    { base: "https://api.deepseek.com/v1",          model: "deepseek-chat",   keyurl: "https://platform.deepseek.com/api_keys" },
    siliconflow: { base: "https://api.siliconflow.cn/v1",        model: "Qwen/Qwen3-8B",   keyurl: "https://cloud.siliconflow.cn/account/ak" },
    kimi:        { base: "https://api.moonshot.cn/v1",           model: "moonshot-v1-32k",         keyurl: "https://platform.moonshot.cn/console/api-keys" },
    zhipu:       { base: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4-plus",              keyurl: "https://bigmodel.cn/usercenter/proj-mgmt/apikeys" },
    openai:      { base: "https://api.openai.com/v1",            model: "gpt-4o",                  keyurl: "https://platform.openai.com/api-keys" },
    custom:      { base: "",                                     model: "",                        keyurl: "" },
  };
  const PROVIDER_NAMES = { deepseek: "DeepSeek", siliconflow: "硅基流动", kimi: "Kimi(月之暗面)", zhipu: "智谱AI", openai: "OpenAI", custom: "自定义(OpenAI兼容)" };
  // rank → 颜色（数字越小越权威）
  const TIER_COLOR = { 0: "#c0392b", 1: "#e67e22", 2: "#2563eb", 3: "#16a085", 4: "#8395a7", 5: "#b2bec3", 6: "#b2bec3" };
  // 甜甜圈/柱状用的中性调色板
  const PALETTE = ["#2563eb", "#16a085", "#e67e22", "#8b5cf6", "#c0392b", "#0ea5e9", "#f59e0b", "#64748b", "#ec4899", "#14b8a6"];

  const esc = (s) => (s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const num = (n) => (n == null ? "0" : Number(n).toLocaleString("zh-CN"));

  async function jget(url) { const r = await fetch(url); if (!r.ok) throw new Error(url + " " + r.status); return r.json(); }
  async function jpost(url, body) {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    let j = null; try { j = await r.json(); } catch (e) {}
    if (!r.ok) throw new Error((j && (j.detail || j.error)) || (url + " " + r.status));
    return j;
  }

  // ── 设置存取（localStorage）──
  const cfg = () => JSON.parse(localStorage.getItem("localkb.cfg") || "{}");
  const saveCfg = (c) => localStorage.setItem("localkb.cfg", JSON.stringify(c));

  // 内联输入弹层（替代浏览器 prompt() 的终端风格弹框）。返回 Promise<string|null>。
  function askText(title, defaultVal, placeholder) {
    return new Promise((resolve) => {
      const ov = document.createElement("div");
      ov.className = "ask-overlay";
      ov.innerHTML =
        `<div class="ask-box"><div class="ask-title">${esc(title || "请输入")}</div>` +
        `<input class="ask-input" type="text" value="${esc(defaultVal || "")}" placeholder="${esc(placeholder || "")}" />` +
        `<div class="ask-btns"><button class="ghost ask-cancel">取消</button><button class="primary-btn ask-ok">确定</button></div></div>`;
      document.body.appendChild(ov);
      const inp = ov.querySelector(".ask-input");
      inp.focus(); inp.select();
      const done = (v) => { ov.remove(); resolve(v); };
      ov.querySelector(".ask-ok").addEventListener("click", () => done(inp.value.trim() || null));
      ov.querySelector(".ask-cancel").addEventListener("click", () => done(null));
      ov.addEventListener("click", (e) => { if (e.target === ov) done(null); });
      inp.addEventListener("keydown", (e) => { if (e.key === "Enter") done(inp.value.trim() || null); if (e.key === "Escape") done(null); });
    });
  }

  // ── 数据源（zotero | folder）全局态：未加载前保守当 zotero，避免误隐藏/误挂拖拽 ──
  const APP = { source: "zotero", folderDir: "", metaReady: false, srcLoaded: false };
  async function ensureSource() {
    if (APP.srcLoaded) return APP.source;
    try {
      const d = await jget("/setup/detect");
      APP.source = d.source === "folder" ? "folder" : "zotero";
      APP.folderDir = d.folder_dir || ""; APP.metaReady = !!d.meta_ready;
      APP.importOnlyPdf = !!d.import_only_pdf;
    } catch (e) { /* 保守留 zotero */ }
    APP.srcLoaded = true;
    applySourceCopy();
    return APP.source;
  }
  // 文件夹模式下把 Zotero 字样替换成文件夹措辞（纯文案 patch）
  function applySourceCopy() {
    if (APP.source !== "folder") return;
    const sub = document.querySelector(".wizard-sub");
    if (sub) sub.textContent = "把一个文件夹里的 PDF 变成能秒级检索、可视化、可对话的本地知识库。全程离线，隐私不出本机。";
    const bh = $("#build-modal .modal-box .hint");
    if (bh) bh.textContent = "重新扫描你的知识库文件夹，只处理新加入的 PDF，已入库的会跳过。（也可以直接把 PDF 拖进窗口即时入库。）";
    const bt = $("#btn-build");
    if (bt) bt.title = "加了新 PDF 后点这里增量更新（或直接把 PDF 拖进窗口）";
  }

  // ── 顶栏状态 pill（读 /health：mode=null|light|full）+ 常驻进度条（读 /index/status）──
  let lastIdxStatus = null;
  let wasDeepBusy = false;
  async function poll() {
    const s = $("#status");
    let h = null, st = null;
    try { h = await jget("/health"); } catch (e) {}
    try { st = await jget("/index/status"); } catch (e) {}
    if (!h) { s.textContent = "服务未连接"; s.className = "status err"; hideProgress(); return; }
    // 顶栏状态统一用「已深索 x/y 篇」口径（F47）：y=有PDF可深索的篇数，x=已深索数。
    // 全应用只保留「已深索/未深索」一套说法，去掉「词法就绪/全文就绪」等黑话。
    const papers = h.papers != null ? h.papers : h.n;
    const stage = st ? (st.stage || "") : "";
    if (h.building) {
      s.textContent = (stage === "deep") ? "深度索引中…" : (stage === "folder") ? "读取题录中…" : "索引中…";
      s.className = "status warn";
    }
    else if (!h.mode) { s.textContent = "未建库"; s.className = "status warn"; }
    else {
      const withPdf = st ? (st.with_pdf || 0) : 0;
      const deep = st ? (st.deep_done || 0) : 0;
      if (withPdf > 0) {
        s.textContent = `已深索 ${num(deep)}/${num(withPdf)} 篇`;
        s.title = `共 ${num(papers)} 篇文献；${num(withPdf)} 篇有 PDF 可深索，已深索 ${num(deep)} 篇（可精读、页级引用）`;
      } else {
        s.textContent = `已索引 ${num(papers)} 篇`;
        s.title = `${num(papers)} 篇题录即时可搜（暂无 PDF 可深索）`;
      }
      s.className = "status ok";
    }
    // 进度条：语义嵌入未完 / 深索进行中时常驻，完成自动消失
    if (st) {
      lastIdxStatus = st;
      updateProgress(st);
      // 深索从「进行中」跳到「结束」的那一刻，静默刷新一次库总览，让进度卡反映最新结果
      const deepBusyNow = st.building && st.stage === "deep";
      if (wasDeepBusy && !deepBusyNow && dashLoaded) loadDashboard("silent");
      wasDeepBusy = deepBusyNow;
    } else { hideProgress(); }
  }
  poll(); setInterval(poll, 4000);

  // ── S 档语义 / F 档深索 进度条 ──
  function hideProgress() { $("#idx-progress").hidden = true; }
  function updateProgress(st) {
    const bar = $("#idx-progress");
    const papers = st.papers || 0, meta = st.meta_done || 0;
    const withPdf = st.with_pdf || 0, deep = st.deep_done || 0;
    const stage = st.stage || "";
    // 深索进行中：building 且当前 stage 是 deep，或深索尚有缺口且正在建
    const deepBusy = st.building && stage === "deep";
    const semanticPending = papers > 0 && meta < papers;
    const deepPending = withPdf > 0 && deep < withPdf;
    const qPending = st.queue_pending || 0;   // F10：自动深索队列积压（加入分类触发）
    let done, total, txt;
    if (deepBusy || (st.building && stage !== "semantic" && deepPending && !semanticPending)) {
      // 全文深索
      done = deep; total = withPdf || 1;
      txt = `全文深索中… ${num(deep)}/${num(withPdf)}` + (qPending ? ` · 另 ${num(qPending)} 篇排队` : "");
    } else if (semanticPending || (st.building && stage === "semantic")) {
      // 语义层提质
      done = meta; total = papers || 1;
      txt = `正在提升检索质量… ${num(meta)}/${num(papers)}`;
    } else if (qPending > 0) {
      // 有文献排队等待深索、但深索尚未开跑（防抖窗口 / 等其它构建让出锁）
      $("#idx-progress-fill").style.width = "100%";
      $("#idx-progress-txt").textContent = `${num(qPending)} 篇排队等待深索…`;
      bar.hidden = false; return;
    } else {
      hideProgress(); return;
    }
    const pct = Math.max(0, Math.min(100, Math.round((done / total) * 100)));
    $("#idx-progress-fill").style.width = pct + "%";
    $("#idx-progress-txt").textContent = `${txt} · ${pct}%`;
    bar.hidden = false;
  }

  // ── tab 切换（泛化：按 data-panel 显隐）──
  // loaded 标志只在「加载成功」后由 loadDashboard/loadBrowse 自己置位；
  // 加载失败则保持 false，切走再切回会自动重试（旧版加载前就置 true，失败后只能刷新整页）。
  let dashLoaded = false, browseLoaded = false, wikiLoaded = false, agentLoaded = false;
  $$(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));
  // 通用 tab 切换（顶栏 tab 与代码内跳转共用）
  function switchTab(tab) {
    $$(".tab").forEach((x) => x.classList.toggle("active", x.dataset.tab === tab));
    $$(".panel").forEach((p) => { p.hidden = p.dataset.panel !== tab; });
    if (tab === "dashboard") loadDashboard(dashLoaded ? "silent" : "loud");
    if (tab === "browse" && !browseLoaded) loadBrowse();
    if (tab === "wiki") loadWikiList(wikiLoaded ? "silent" : "loud");
    if (tab === "agent" && !agentLoaded) loadAgentConfig();
    if (tab === "chat") loadChatCats();
  }
  // 「找相似」本地实词提取（F58，止血版）：不再把整句标题灌进检索，
  // 而是在中文虚词/标点处切开、去停用词、取前几个实词短语作为查询。向量近邻版放后续波次。
  const STOP_WORDS = new Set("研究 分析 探讨 问题 影响 视角 视域 语境 背景 我国 关于 及其 兼论 试论 浅析 述评 反思 论纲 综述 制度 机制 路径 模式 体系 完善 构建 反思".split(/\s+/));
  function extractKeywords(title) {
    if (!title) return "";
    const cleaned = title.replace(/[《》「」“”‘’（）()\[\]【】{}·—\-,，.。;；:：!！?？、\/\\|"']/g, " ");
    // 在常见中文虚词处切开（无分词器时的近似），再按空白切
    const parts = cleaned
      .split(/[的了和与及或在是对中于以为把被让向从到并兼之其如何什么怎样一种一个]+|\s+/)
      .map((s) => s.trim())
      .filter(Boolean);
    const terms = parts.filter((s) => s.length >= 2 && !STOP_WORDS.has(s));
    const pick = (terms.length ? terms : parts).slice(0, 3);
    return pick.join(" ") || title;
  }
  // 供浏览 tab 里点标题「找相似」用：默认用 AI 从标题抽核心检索词（更准），无 key 退本地实词
  async function switchToSearch(q) {
    switchTab("search");
    $("#q").value = "正在提取关键词…"; $("#go").disabled = true;
    let kw = "";
    try {
      const c = cfg();
      const r = await jpost("/similar/keywords", { title: q, provider: c.provider || "siliconflow",
        base_url: c.base || "", api_key: c.api_key || "", model: c.model || "" });
      kw = (r && r.keywords) || "";
    } catch (e) {}
    if (!kw) kw = extractKeywords(q);        // 退化本地抽词
    $("#go").disabled = false;
    $("#q").value = kw; doSearch();
  }

  // ══════════════════════════════════════════
  //  检索
  // ══════════════════════════════════════════
  // F38-B：主徽标口径统一到学科感知中文档名（权威/核心/普通…）；WT_COLOR/WT_RANK 以中文档名为键
  const WT_COLOR = { "权威":"#c0392b","准权威":"#a93226","核心":"#e67e22","次核心":"#d4a017",
                     "一般":"#27ae60","普通":"#7f8c8d","待确认":"#bdc3c7","未知":"#8395a7" };
  const WT_RANK  = { "权威":0,"准权威":1,"核心":2,"次核心":3,"一般":4,"普通":5,"待确认":6,"未知":6 };
  // 徽标颜色：优先新中文档名；回退旧扁平刊名色（grading 预热中/引擎 down 时 weight_tier 空、兜 journal_tier）
  function badgeColor(name) {
    if (WT_COLOR[name] != null) return WT_COLOR[name];
    return tierNameColor(name);
  }
  function tierBadge(r) {
    const cn = r.weight_tier || r.journal_tier || "未知";
    const rev = r.weight_needs_review;
    const tip = rev ? ' title="待确认：未精确识别到该刊，档位为临时值，请核对原刊"'
                    : ' title="期刊权威度（按当前锁定学科评定）"';
    return `<span class="badge" style="background:${badgeColor(cn)}"${tip}>${esc(cn)}</span>`;
  }
  // 次徽标：只显示 0–1 数值权重（档位已由主徽标表达，去掉重复的 tier 前缀）
  function weightBadge(r) {
    if (r.is_wiki || r.journal_weight == null) return "";   // 综合页无期刊，不显权重徽标
    const rev = r.weight_needs_review;
    const tip = rev ? ' title="待确认：未精确识别到该刊，权重为临时值"'
                    : ' title="期刊引用权重（来源先验，0–1，按当前学科）"';
    return `<span class="badge wbadge" style="background:${badgeColor(r.weight_tier)}"${tip}>权重 ${Number(r.journal_weight).toFixed(2)}</span>`;
  }
  function depthTag(r) {
    if (r.is_wiki) return "";                       // wiki 行用专属徽标（wikiBadge），不显示深度标
    if (r.depth === "full") return `<span class="tag full">📄 已深索</span>`;
    return `<span class="tag abstract" title="仅题录可搜；深索该篇后可精读到页码">📋 未深索</span>`;
  }
  // 综合层徽标：命中的是"已存综合"页（可能已过时；来源可展开回溯到论文页码）。
  // agent 经 MCP 写回、未核验的页标 🤖，方便一眼锁定该复看/剔除的对象；人自己保存的标 📝。
  function wikiBadge(r) {
    if (!r.is_wiki) return "";
    return r.by_agent
      ? `<span class="tag wiki agent" title="agent 自动写回、未经人工核验；可点「🗑 不保存」剔除">🤖 AI综合·未核验</span>`
      : `<span class="tag wiki" title="本地综合页，可能已过时，请核对来源原文">📝 已存综合</span>`;
  }
  // 共用的一行元信息（检索卡与浏览卡合用；作者深色、期刊斜体、页码强调色由 CSS 承担）
  function metaRow(o) {
    const bits = [];
    const who = (o.author || "").split(";")[0].trim();
    if (who) bits.push(`<span class="m-author">${esc(who)}</span>`);
    if (o.year) bits.push(`<span class="m-year">${esc(String(o.year))}</span>`);
    if (o.journal) bits.push(`<span class="m-journal">${esc(o.journal)}</span>`);
    if (o.official_pages) bits.push(`<span class="pg">第 ${esc(o.official_pages)} 页</span>`);
    return bits.length ? `<div class="card-meta">${bits.join('<span class="dot-sep">·</span>')}</div>` : "";
  }
  // 复制「引文 + 片段」（研究者更需要的是可直接引用的文本，非工程 score）
  async function copyResult(r, btn) {
    const txt = (r.citation || r.title || "") + "\n\n" + (r.text || "").trim();
    const old = btn.textContent;
    try {
      await navigator.clipboard.writeText(txt);
      btn.textContent = "✓ 已复制";
    } catch (e) {
      const ta = document.createElement("textarea");
      ta.value = txt; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.focus(); ta.select();
      try { document.execCommand("copy"); btn.textContent = "✓ 已复制"; }
      catch (_) { btn.textContent = "复制失败"; }
      document.body.removeChild(ta);
    }
    setTimeout(() => { btn.textContent = old; }, 1500);
  }
  function resultCard(r, i) {
    const div = document.createElement("div");
    div.className = "card";
    // 主行只留标题（F32）；作者/年份/期刊/页码统一进 metaRow；score 黑话不再显示
    const discard = r.is_wiki ? `<button class="ghost2 wiki-discard" title="删除这条本地综合页（文件+索引+检索行；不影响文献库）">🗑 不保存</button>` : "";
    const gotoWiki = r.is_wiki ? `<button class="ghost2 wiki-goto" title="在 wiki 页查看这条综合">📖 在wiki页打开</button>` : "";
    const rawCtx = (r.context || "").trim();
    const hasCtx = rawCtx && rawCtx !== (r.text || "").trim();
    const ctxTitleBits = [];
    if (r.official_pages) ctxTitleBits.push("第 " + esc(r.official_pages) + " 页");
    if (r.heading) ctxTitleBits.push(esc(r.heading));
    const ctxHead = "📖 该片段所在整段原文" + (ctxTitleBits.length ? " · " + ctxTitleBits.join(" · ") : "");
    div.innerHTML =
      `<div class="card-head"><span class="idx">#${i}</span>${wikiBadge(r)}${tierBadge(r)}${weightBadge(r)}${depthTag(r)}</div>` +
      `<div class="cite">${esc(r.title || r.citation || "")}</div>` +
      metaRow(r) +
      `<div class="snippet">${esc((r.text || "").trim())}</div>` +
      // 复制按钮已去掉（副本#35：文字本身已可选中复制）；wiki 行仍保留其专属按钮
      `<div class="card-btns">` +
        (hasCtx ? `<button class="ghost2 ctx-toggle">查看原文上下文</button>` : "") + gotoWiki + discard +
      `</div>` +
      (hasCtx ? `<div class="ctx hidden"><div class="ctx-h">${ctxHead}</div><div class="ctx-body">${esc(rawCtx)}</div></div>` : "");
    const btn = div.querySelector(".ctx-toggle"), ctx = div.querySelector(".ctx");
    if (btn && ctx) btn.addEventListener("click", () => { const h = ctx.classList.toggle("hidden"); btn.textContent = h ? "查看原文上下文" : "收起原文"; });
    const gbtn = div.querySelector(".wiki-goto");
    if (gbtn) gbtn.addEventListener("click", () => { switchTab("wiki"); openWikiPage(r.key); });
    const dbtn = div.querySelector(".wiki-discard");
    if (dbtn) dbtn.addEventListener("click", () => discardWiki(r.key, () => div.remove(), dbtn));
    return div;
  }
  // 无限滚动：一次取较大批(重排只跑一次、顺序最稳)，先渲染 10 条，滚到底再追加（原#30/副本#24）
  const SR = { all: [], shown: 0, observer: null, page: 10 };
  function _renderMoreResults() {
    const end = Math.min(SR.shown + SR.page, SR.all.length);
    for (let i = SR.shown; i < end; i++) $("#results").appendChild(resultCard(SR.all[i], i + 1));
    SR.shown = end;
    const sentinel = $("#s-sentinel");
    if (sentinel) sentinel.hidden = SR.shown >= SR.all.length;
  }
  async function doSearch() {
    const q = $("#q").value.trim(); if (!q) return;
    $("#go").disabled = true; $("#results").innerHTML = ""; $("#s-msg").textContent = "检索中…";
    if (SR.observer) { SR.observer.disconnect(); SR.observer = null; }
    SR.all = []; SR.shown = 0;
    try {
      const res = await jpost("/search", { query: q, topk: 20, sort: $("#sort").value, min_weight: parseFloat($("#minw") && $("#minw").value) || 0 });
      if (res.error) { $("#s-msg").textContent = res.error; return; }
      SR.all = res.results || [];
      const modeTip = res.mode === "light" ? " · 词法模式（深索后可精读到页码）" : "";
      $("#s-msg").textContent = `命中 ${SR.all.length} 条 · ${res.took_ms != null ? res.took_ms : "?"}ms${modeTip}`;
      if (!SR.all.length) { $("#s-msg").textContent += "（无结果，换个关键词试试）"; return; }
      _renderMoreResults();
      // 底部哨兵 + IntersectionObserver：滚到底自动补充
      const sentinel = document.createElement("div");
      sentinel.id = "s-sentinel"; sentinel.className = "s-sentinel";
      sentinel.hidden = SR.shown >= SR.all.length;
      $("#results").after(sentinel);
      SR.observer = new IntersectionObserver((es) => {
        if (es[0].isIntersecting && SR.shown < SR.all.length) _renderMoreResults();
      }, { rootMargin: "200px" });
      SR.observer.observe(sentinel);
    } catch (e) { $("#s-msg").textContent = "检索失败：" + e.message; }
    finally { $("#go").disabled = false; }
  }
  $("#go").addEventListener("click", doSearch);
  // Enter 触发检索，但按钮禁用（检索进行中）时不重复提交，避免结果闪烁
  $("#q").addEventListener("keydown", (e) => { if (e.key === "Enter" && !$("#go").disabled) doSearch(); });

  // ══════════════════════════════════════════
  //  库总览（仪表盘）—— 全部手绘 SVG/CSS
  // ══════════════════════════════════════════
  function donutSVG(items, size) {
    // items: [{label, n, color}]
    const total = items.reduce((a, b) => a + b.n, 0) || 1;
    const R = 16, C = 2 * Math.PI * R, cx = 21, cy = 21;
    let off = 0, segs = "";
    items.forEach((it) => {
      const frac = it.n / total, len = frac * C;
      segs += `<circle cx="${cx}" cy="${cy}" r="${R}" fill="none" stroke="${it.color}" stroke-width="7"
        stroke-dasharray="${len.toFixed(2)} ${(C - len).toFixed(2)}" stroke-dashoffset="${(-off).toFixed(2)}"
        transform="rotate(-90 ${cx} ${cy})"><title>${esc(it.label)}: ${it.n}</title></circle>`;
      off += len;
    });
    return `<svg viewBox="0 0 42 42" width="${size}" height="${size}">${segs}
      <text x="21" y="20" text-anchor="middle" font-size="6.5" font-weight="700" fill="#1f2937">${num(total)}</text>
      <text x="21" y="26" text-anchor="middle" font-size="3.2" fill="#6b7280">篇</text></svg>`;
  }
  function donutCard(title, sub, items) {
    const legend = items.map((it) =>
      `<div class="row"><i style="background:${it.color}"></i><span class="lbl">${esc(it.label)}</span><span class="val">${num(it.n)}</span></div>`
    ).join("");
    return `<div class="dcard"><h4>${esc(title)}</h4>${sub ? `<p class="dcard-sub">${esc(sub)}</p>` : ""}
      <div class="donut-wrap">${donutSVG(items, 120)}<div class="donut-legend">${legend}</div></div></div>`;
  }
  function hbar(label, n, max, color, showDot) {
    const w = Math.max(2, Math.round((n / (max || 1)) * 100));
    const dot = showDot ? `<span class="dot" style="background:${color}"></span>` : "";
    return `<div class="hbar">${dot}<span class="lbl" title="${esc(label)}">${esc(label)}</span>
      <span class="track"><span class="fill" style="width:${w}%;background:${color}"></span></span>
      <span class="val">${num(n)}</span></div>`;
  }

  // 年份柱状图（SVG rect）
  function yearBarsSVG(by_year) {
    if (!by_year || !by_year.length) return "<p class='dcard-sub'>暂无数据</p>";
    // 「未标注」放最后；其余按年份升序（后端已升序，但保险起见）
    const labeled = by_year.filter((d) => d.year !== "未标注").slice().sort((a, b) => String(a.year).localeCompare(String(b.year)));
    const unlabeled = by_year.filter((d) => d.year === "未标注");
    const data = labeled.concat(unlabeled);
    const W = 640, H = 200, padB = 26, padL = 4, padT = 8, padR = 4;
    const iw = W - padL - padR, ih = H - padB - padT;
    const max = Math.max.apply(null, data.map((d) => d.n)) || 1;
    const bw = iw / data.length;
    let bars = "", labels = "";
    data.forEach((d, i) => {
      const bh = Math.max(1, (d.n / max) * ih);
      const x = padL + i * bw, y = padT + ih - bh;
      const isUn = d.year === "未标注";
      const col = isUn ? "#cbd5e1" : "#2563eb";
      bars += `<rect x="${(x + bw * 0.14).toFixed(1)}" y="${y.toFixed(1)}" width="${(bw * 0.72).toFixed(1)}" height="${bh.toFixed(1)}" rx="1.5" fill="${col}"><title>${esc(String(d.year))}：${d.n} 篇</title></rect>`;
      // 稀疏标注 x 轴（每约 5 个 + 未标注）
      const showLbl = isUn || i % 5 === 0 || i === data.length - 1;
      if (showLbl) {
        const lx = x + bw / 2;
        labels += `<text x="${lx.toFixed(1)}" y="${H - 8}" text-anchor="middle" font-size="9" fill="#6b7280"${isUn ? ' font-weight="600"' : ""}>${esc(String(d.year))}</text>`;
      }
    });
    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:200px">
      <line x1="${padL}" y1="${padT + ih}" x2="${W - padR}" y2="${padT + ih}" stroke="#e5e7eb" stroke-width="1"/>
      ${bars}${labels}</svg>`;
  }

  // 覆盖：堆叠横条（有PDF / 无PDF）+ 无摘要标注
  function coverageCard(cov) {
    const total = cov.total || 0;
    const withPdf = cov.with_pdf || 0, noPdf = cov.no_pdf || 0, noAbs = cov.no_abstract || 0, metaIdx = cov.meta_indexed || 0;
    const pw = total ? (withPdf / total) * 100 : 0;
    const stacked = `<svg viewBox="0 0 100 8" preserveAspectRatio="none" style="width:100%;height:16px;border-radius:8px">
      <rect x="0" y="0" width="${pw.toFixed(2)}" height="8" fill="#16a085"><title>有PDF：${withPdf} 篇</title></rect>
      <rect x="${pw.toFixed(2)}" y="0" width="${(100 - pw).toFixed(2)}" height="8" fill="#cbd5e1"><title>无PDF：${noPdf} 篇</title></rect></svg>`;
    return `<div class="dcard span2"><h4>入库覆盖</h4>
      <p class="dcard-sub">题录已索引 ${num(metaIdx)} 篇；有 PDF 的可深索到页码级</p>
      <div class="cov-nums">
        <div class="n"><b>${num(total)}</b><span>总篇数</span></div>
        <div class="n"><b style="color:#16a085">${num(withPdf)}</b><span>有 PDF</span></div>
        <div class="n"><b style="color:#94a3b8">${num(noPdf)}</b><span>无 PDF</span></div>
        <div class="n"><b style="color:#b45309">${num(noAbs)}</b><span>无摘要</span></div>
      </div>
      ${stacked}
      <div class="cov-legend">
        <span><i style="background:#16a085"></i>有 PDF ${total ? Math.round(withPdf / total * 100) : 0}%</span>
        <span><i style="background:#cbd5e1"></i>无 PDF ${total ? Math.round(noPdf / total * 100) : 0}%</span>
        <span><i style="background:#f59e0b"></i>无摘要 ${num(noAbs)} 篇（仅题录）</span>
      </div></div>`;
  }

  function tierCard(by_tier) {
    if (!by_tier || !by_tier.length) return "";
    const max = Math.max.apply(null, by_tier.map((d) => d.n)) || 1;
    const rows = by_tier.map((d) => hbar(d.tier, d.n, max, badgeColor(d.tier), true)).join("");
    return `<div class="dcard"><h4>期刊分级分布</h4>
      <p class="dcard-sub">按当前锁定学科评定 · 颜色越暖越权威（红＝权威 / 橙＝核心 / 绿＝一般 / 灰＝普通·待确认）</p>${rows}</div>`;
  }

  function journalCard(by_journal) {
    if (!by_journal || !by_journal.length) return "";
    const max = Math.max.apply(null, by_journal.map((d) => d.n)) || 1;
    const rows = by_journal.map((d) => hbar(d.journal, d.n, max, badgeColor(d.tier), true)).join("");
    return `<div class="dcard span2 jlist"><h4>高频期刊 Top ${by_journal.length}</h4>
      <p class="dcard-sub">带分级色点（按当前学科）</p>${rows}</div>`;
  }
  // 旧扁平刊名→颜色（保留作 grading 预热中/引擎 down 的兜底；新口径优先走 WT_COLOR）
  const TIER_RANK_BY_NAME = {
    "CLSCI": 0, "台湾核心": 0, "外文顶级法评": 0,
    "外文权威": 1,
    "CSSCI": 2, "台湾一般": 2,
    "CSSCI扩展": 3,
    "外文一般": 4, "普刊": 4,
    "报纸": 5, "未知": 6,
  };
  function tierNameColor(name) {
    const r = TIER_RANK_BY_NAME[name];
    return TIER_COLOR[r != null ? r : 6];
  }

  // 最近入库：默认 4 条、可展开；每条带三态深索按钮（可深索/已深索/无PDF）
  function recentCard(recent) {
    if (!recent || !recent.length) return `<div class="dcard"><h4>最近入库</h4><p class="dcard-sub">暂无</p></div>`;
    const row = (r) => {
      const t = (r.title || "").slice(0, 52) + ((r.title || "").length > 52 ? "…" : "");
      let btn;
      if (r.deep) btn = `<span class="rc-tag done">已深索</span>`;
      else if (r.has_pdf) btn = `<button class="rc-deep" data-key="${esc(r.key || "")}">深索</button>`;
      else btn = `<span class="rc-tag nopdf" title="无 PDF，无法深索">无PDF</span>`;
      return `<li><div class="rc-main"><div class="rt">${esc(t)}</div><div class="rd">${esc(r.ingested_at || "")}</div></div>${btn}</li>`;
    };
    const head = recent.slice(0, 4).map(row).join("");
    const rest = recent.slice(4).map(row).join("");
    return `<div class="dcard"><h4>最近入库</h4>
      <ul class="recent-list">${head}</ul>
      ${rest ? `<ul class="recent-list recent-more" hidden>${rest}</ul>
                <button class="rc-expand" id="rc-expand">展开更多 (${recent.length - 4}) ▾</button>` : ""}</div>`;
  }

  // 概览卡：期刊分级分布 + 一行"文献构成"速览（副本#12：除分级分布再放点啥）
  function overviewCard(d) {
    const cov = d.coverage || {};
    const by = d.by_tier || [];
    const max = Math.max.apply(null, by.map((x) => x.n).concat([1]));
    const rows = by.map((x) => hbar(x.tier, x.n, max, badgeColor(x.tier), true)).join("");
    const zh = (d.by_lang || []).find((x) => /中/.test(x.lang)) || {};
    const wai = (d.by_lang || []).find((x) => /外/.test(x.lang)) || {};
    const compose = `<div class="ov-compose">
      <span><b>${num(cov.meta_indexed)}</b>总篇</span>
      <span><b>${num(cov.with_pdf)}</b>有PDF</span>
      ${zh.n ? `<span><b>${num(zh.n)}</b>中文</span>` : ""}
      ${wai.n ? `<span><b>${num(wai.n)}</b>外文</span>` : ""}
    </div>`;
    return `<div class="dcard"><h4>概览</h4>
      ${compose}
      <p class="dcard-sub" style="margin-top:10px">期刊分级分布（按当前锁定学科评定 · 红＝权威/橙＝核心/绿＝一般/灰＝普通）</p>${rows}</div>`;
  }

  // 底部：为什么用 Agent 驱动（用户友好 + 可跳转）
  function agentGuideCard() {
    return `<div class="dcard span2 dash-guide">
      <div class="dg-ic">🤖</div>
      <div class="dg-body">
        <div class="dg-h">让 AI 助手替你驱动整个知识库</div>
        <p>对话只适合随手问一句。要系统地检索、写带页码引用的综述、跨主题梳理、把结论沉淀复用——更推荐把 PaperPiggy 接进
           <b>Claude Code / Codex</b>：它能查你的库、读到期刊印刷页码、把综合写回，下次一键复用。</p>
        <button class="primary-btn" id="dg-go">去「🤖 Agent」看怎么接入 →</button>
      </div></div>`;
  }

  // 全文深索进度卡（读 /index/status；点击跳到浏览 tab 的「仅题录」筛选）
  function deepProgressCard(st) {
    if (!st) return "";
    const withPdf = st.with_pdf || 0, deep = st.deep_done || 0;
    if (withPdf <= 0) {
      return `<div class="dcard span2 deep-prog">
        <h4>全文深索进度</h4>
        <p class="dcard-sub">有 PDF 的文献全文切块+向量化后，回答可精确到页码</p>
        <div class="hbar deep-prog-bar"><span class="track"><span class="fill" style="width:0%;background:#16a085"></span></span><span class="val">0%</span></div>
        <p class="deep-prog-txt">暂无可深索文献</p></div>`;
    }
    const rawPct = (deep / withPdf) * 100;
    const barPct = deep > 0 ? Math.max(3, Math.round(rawPct)) : 0;   // 有成果就留一条可见的进度条，避免 2/1443 显示 0%
    const pctLabel = (deep > 0 && rawPct < 1) ? "<1%" : Math.round(rawPct) + "%";
    const remain = Math.max(0, withPdf - deep);
    const clickable = remain > 0;
    const bar = `<span class="track"><span class="fill" style="width:${barPct}%;background:#16a085"></span></span>`;
    const txt = remain > 0
      ? `已深索 <b>${num(deep)}</b> / 有PDF ${num(withPdf)} 篇，还有 ${num(remain)} 篇可深索`
      : `已深索 <b>${num(deep)}</b> / 有PDF ${num(withPdf)} 篇，已全部深索完成 ✓`;
    // 查看「已深索了哪些」（跳到浏览的「已深索」筛选）。整卡可点→浏览挑未深索去深索。
    const seeLink = deep > 0 ? `<span class="deep-prog-see" id="deep-prog-see">查看已深索 ${num(deep)} 篇 →</span>` : "";
    return `<div class="dcard span2 deep-prog${clickable ? " clickable" : ""}"${clickable ? ' id="deep-prog-card"' : ""}>
      <h4>全文深索进度</h4>
      <p class="dcard-sub">只有<b>深索过</b>的文献才能被精读、页级引用、跨篇综合；未深索的仅题录可搜。</p>
      <div class="hbar deep-prog-bar">${bar}<span class="val">${pctLabel}</span></div>
      <p class="deep-prog-txt">${txt}${seeLink}</p></div>`;
  }

  function _goDeepBrowse(filter) {
    BR.deepFilter = filter; BR.sort = "recommend";
    const df = $("#bl-deep-filter"); if (df) df.value = filter;
    const bs = $("#bl-sort"); if (bs) bs.value = "recommend";
    const firstBrowse = !browseLoaded;
    switchTab("browse");
    if (!firstBrowse) selectCollection(null, "全部", null, true);
  }
  function renderDashboard(d, status) {
    const cov = d.coverage || {};
    const health = d.health || {};
    const st = status || {};
    const withPdf = st.with_pdf || 0, deep = st.deep_done || 0;
    const remain = Math.max(0, withPdf - deep);
    const rawPct = withPdf ? (deep / withPdf) * 100 : 0;
    const barPct = deep > 0 ? Math.max(3, Math.round(rawPct)) : 0;
    const pctLabel = withPdf === 0 ? "—" : (deep > 0 && rawPct < 1 ? "<1%" : Math.round(rawPct) + "%");
    // 概览条（深色）：一句话 + 数字 + 全文深索进度（副本#11 深索进度并入顶部黑框）
    const header = `<div class="dash-hero">
      <div class="dh-left">
        <div class="dh-title">${esc(health.one_liner || "知识库总览")}</div>
        <div class="dh-sub">题录 ${num(cov.meta_indexed)} 篇 · 有 PDF ${num(cov.with_pdf)} 篇 · 已深索 ${num(cov.deep_indexed)} 篇</div>
      </div>
      <div class="dh-deep">
        <div class="dh-deep-h"><b>全文深索进度</b><span>${pctLabel}</span></div>
        <div class="hbar dh-bar"><span class="track"><span class="fill" style="width:${barPct}%;background:#7ee0b8"></span></span></div>
        <div class="dh-deep-txt">${withPdf === 0 ? "暂无可深索文献。" : (remain > 0
            ? `只有深索过的文献才能被精读、页级引用、跨篇综合。已深索 <b>${num(deep)}</b>/${num(withPdf)} 篇，还有 ${num(remain)} 篇。`
            : `已全部深索完成 ✓`)}
          ${deep > 0 ? `<a class="dh-link" id="dash-see-deep">查看已深索 ${num(deep)} 篇 →</a>` : ""}
          ${remain > 0 ? `<a class="dh-link" id="dash-go-deep">去深索这 ${num(remain)} 篇 →</a>` : ""}</div>
      </div></div>`;
    $("#dash").innerHTML = header
      + `<div class="dash-grid dash-2col">${overviewCard(d)}${recentCard(d.recent)}</div>`
      + agentGuideCard();
    // 事件
    const seeD = $("#dash-see-deep"); if (seeD) seeD.addEventListener("click", () => _goDeepBrowse("yes"));
    const goD = $("#dash-go-deep"); if (goD) goD.addEventListener("click", () => _goDeepBrowse("no"));
    const exp = $("#rc-expand"); if (exp) exp.addEventListener("click", () => {
      const more = $(".recent-more"); if (more) { more.hidden = false; exp.hidden = true; }
    });
    $$(".rc-deep").forEach((b) => b.addEventListener("click", () => {
      b.disabled = true; b.textContent = "…"; deepIndexKeys([b.dataset.key], b);
    }));
    const dgGo = $("#dg-go"); if (dgGo) dgGo.addEventListener("click", () => switchTab("agent"));
  }

  async function loadDashboard(mode) {
    if (mode !== "silent") $("#dash").innerHTML = `<div class="dash-loading">加载库统计中…</div>`;
    try {
      // 并发拉统计 + 深索状态（status 失败不阻断仪表盘）
      const [d, status] = await Promise.all([
        jget("/stats"),
        jget("/index/status").catch(() => null),
      ]);
      renderDashboard(d, status);
      dashLoaded = true;   // 仅加载成功才置位；失败保持 false，切回自动重试
    } catch (e) {
      $("#dash").innerHTML = `<div class="dash-loading">统计加载失败：${esc(e.message)}<br>（需先完成即时索引）</div>`;
    }
  }

  // ══════════════════════════════════════════
  //  浏览（左树 + 右列表 + 选择性深索）
  // ══════════════════════════════════════════
  // scope 统一左树选择态（type: all|zotero|topic|kbcat），取代旧的 collection/topic 两两互清。
  const BR = { scope: { type: "all", id: null, name: "全部" },
               deepFilter: "", sort: "recommend", papers: [], selected: new Set(),
               cats: [] };   // cats：缓存 /kb/categories，供右键菜单/加成员用

  // 左侧收藏夹树（递归渲染，默认折叠，只展开有子节点的第一层由用户点开）
  function treeNodeEl(node, depth) {
    const wrap = document.createElement("div");
    wrap.className = "bt-node";
    const hasKids = node.children && node.children.length;
    const row = document.createElement("div");
    row.className = "bt-row";
    row.style.paddingLeft = (8 + depth * 14) + "px";
    row.dataset.path = node.path;
    // 双数字「已深索/总数」：总数用 count_deep（该夹+子孙的题录合计），已深索用后端实时算的 count_indexed。
    // 旧版只显示已深索数 → 新库首次满屏 0 像空库，是最劝退的首因。
    const tot = node.count_deep || 0, ind = node.count_indexed || 0;
    row.innerHTML =
      `<span class="bt-caret ${hasKids ? "" : "leaf"}">${hasKids ? "▸" : ""}</span>` +
      `<span class="bt-nm" title="${esc(node.name)}">${esc(node.name)}</span>` +
      `<span class="bt-cnt" title="已深索 ${num(ind)} / 共 ${num(tot)} 篇">${num(ind)}/${num(tot)}</span>`;
    wrap.appendChild(row);
    let kidsBox = null;
    if (hasKids) {
      kidsBox = document.createElement("div");
      kidsBox.className = "bt-kids"; kidsBox.hidden = true;
      node.children.forEach((c) => kidsBox.appendChild(treeNodeEl(c, depth + 1)));
      wrap.appendChild(kidsBox);
    }
    const caret = row.querySelector(".bt-caret");
    // 点箭头：仅展开/折叠；点名字：选中该收藏夹加载文献
    caret.addEventListener("click", (e) => {
      e.stopPropagation();
      if (!hasKids) return;
      kidsBox.hidden = !kidsBox.hidden;
      caret.textContent = kidsBox.hidden ? "▸" : "▾";
      caret.classList.toggle("open", !kidsBox.hidden);
    });
    row.addEventListener("click", () => selectCollection(node.path, node.name, row));
    return wrap;
  }

  // 换收藏夹/主题时默认把「深索状态」筛选清回「全部」，否则上次的「已深索」会把列表掐成只剩深索篇，
  // 让人误以为该收藏夹/主题只有深索的那几篇。库总览的「查看已深索/去深索」入口用 keepFilter 保留筛选。
  function resetDeepFilter() {
    BR.deepFilter = "";
    const df = $("#bl-deep-filter"); if (df) df.value = "";
  }

  // 统一切换左树范围：设 scope、清筛选（除非 keep）、统一高亮、加载文献。
  function applyScope(type, id, name, rowEl, keep) {
    BR.scope = { type, id, name: name || "全部" };
    if (!keep) resetDeepFilter();
    $("#bt-all").classList.toggle("active", type === "all");
    $$("#bt-tree .bt-row").forEach((r) => r.classList.remove("active"));
    $$("#bt-topics .bt-topic").forEach((c) => c.classList.remove("active"));
    $$("#bt-kbcats .kbcat").forEach((c) => c.classList.remove("active"));
    if (rowEl && type !== "all") rowEl.classList.add("active");
    loadPapers();
  }
  // 薄封装（保留原签名，兼容既有调用点）：收藏夹 / AI 主题 / 知识库分类
  function selectCollection(path, name, rowEl, keepFilter) {
    applyScope(path ? "zotero" : "all", path, name || "全部", rowEl, keepFilter);
  }
  function selectTopic(id, name, chipEl) { applyScope("topic", id, name, chipEl); }
  function selectKbCat(id, name, el) { applyScope("kbcat", id, name, el); }

  async function loadTopics() {
    const box = $("#bt-topics");
    try {
      const d = await jget("/topics");
      const topics = d.topics || [];
      box.innerHTML = "";
      if (!topics.length) {
        // 空态区分：无索引→引导去检索引擎；已索引→引导点「生成主题」
        const st = lastIdxStatus || {};
        const hasVec = st.mode === "full";
        box.innerHTML = hasVec
          ? `<div class="bt-hint">还没有 AI 主题。点上方「🔄 生成主题」，让 AI 把已深索的文献自动归类。</div>`
          : `<div class="bt-hint">AI 主题按向量把已深索文献自动归类，需先建语义/深索索引。
             <a class="ag-link" id="bt-topics-goengine">去「设置 → 检索引擎」</a></div>`;
        const g = $("#bt-topics-goengine"); if (g) g.addEventListener("click", openSettings);
        return;
      }
      topics.forEach((t) => {
        const chip = document.createElement("span");
        chip.className = "bt-topic";
        chip.title = t.name;
        chip.innerHTML = `<span class="tp-nm">${esc(t.name)}</span><span class="tp-cnt">${num(t.size)}</span><button class="tp-gen" title="生成/查看本主题的综述页">🧩</button>`;
        chip.querySelector(".tp-gen").addEventListener("click", (e) => { e.stopPropagation(); genTopic(t.id, t.name, e.currentTarget); });
        chip.addEventListener("click", () => {
          // 再点已选中的主题 → 取消，回到全库
          if (BR.scope.type === "topic" && BR.scope.id === t.id) { selectCollection(null, "全部", null); return; }
          selectTopic(t.id, t.name, chip);
        });
        box.appendChild(chip);
      });
    } catch (e) {
      box.innerHTML = `<div class="bt-loading">主题加载失败：${esc(e.message)}</div>`;
    }
  }

  async function loadBrowse() {
    const src = await ensureSource();
    loadTopics(); // AI/知识库分类：两模式都要
    loadKbCats(); // 知识库分类（用户自建）
    const zsec = $("#bt-zotero-sec");
    if (src === "folder") {
      // 文件夹模式：整块隐藏 Zotero 分类区，不请求 /categories
      if (zsec) zsec.hidden = true;
      browseLoaded = true;
      loadPapers();
      return;
    }
    if (zsec) zsec.hidden = false;
    try {
      const d = await jget("/categories");
      $("#bt-all-cnt").textContent = d.n_collections != null ? (num(d.n_collections) + " 夹") : "";
      const box = $("#bt-tree"); box.innerHTML = "";
      (d.tree || []).forEach((n) => box.appendChild(treeNodeEl(n, 0)));
      if (!(d.tree || []).length) box.innerHTML = `<div class="bt-loading">（无收藏夹）</div>`;
      // 仅导入有 PDF 时提示：分类篇数为 Zotero 原始数量，无 PDF 的未进库
      if (APP.importOnlyPdf && !$("#bt-onlypdf-note")) {
        const note = document.createElement("div");
        note.id = "bt-onlypdf-note"; note.className = "bt-hint";
        note.textContent = "已设为「只导入有 PDF」：下面分类的篇数是 Zotero 原始数量，没有 PDF 的条目未进库。";
        box.parentNode.insertBefore(note, box);
      }
      browseLoaded = true;   // 仅收藏夹加载成功才置位；失败保持 false，切回自动重试
    } catch (e) {
      $("#bt-tree").innerHTML = `<div class="bt-loading">收藏夹加载失败：${esc(e.message)}</div>`;
    }
    loadPapers(); // 默认加载全库推荐
  }

  // ── F10：知识库分类（自建）左树区渲染 + CRUD + 右键加入 ──
  function toast(msg) { const el = $("#bl-msg"); if (el) el.textContent = msg; }
  async function loadKbCats() {
    try {
      const d = await jget("/kb/categories");
      BR.cats = d.categories || [];
      const box = $("#bt-kbcats"); if (!box) return;
      box.innerHTML = "";
      BR.cats.forEach((c) => {
        const el = document.createElement("div");
        el.className = "kbcat";
        if (BR.scope.type === "kbcat" && BR.scope.id === c.id) el.classList.add("active");
        el.innerHTML =
          `<span class="kbcat-nm" title="${esc(c.name)}">${esc(c.name)}</span>` +
          `<span class="kbcat-cnt" title="已深索 ${num(c.deep_count)} / 共 ${num(c.count)}${c.pending ? "；" + num(c.pending) + " 篇排队深索中" : ""}">` +
            `${num(c.deep_count)}/${num(c.count)}${c.pending ? " ⏳" + num(c.pending) : ""}</span>` +
          `<button class="kbcat-menu" title="重命名 / 删除">⋯</button>`;
        el.addEventListener("click", (e) => {
          if (e.target.closest(".kbcat-menu")) return;
          selectKbCat(c.id, c.name, el);
        });
        el.querySelector(".kbcat-menu").addEventListener("click", (e) => {
          e.stopPropagation(); openKbCatMenu(c, e.currentTarget);
        });
        // 拖拽落点：把文献卡拖到此分类上即加入
        el.addEventListener("dragover", (e) => {
          if ((e.dataTransfer.types || []).includes("application/x-kb-keys")) { e.preventDefault(); el.classList.add("drop-hi"); }
        });
        el.addEventListener("dragleave", () => el.classList.remove("drop-hi"));
        el.addEventListener("drop", (e) => {
          e.preventDefault(); el.classList.remove("drop-hi");
          try {
            const keys = JSON.parse(e.dataTransfer.getData("application/x-kb-keys") || "[]");
            if (keys.length) addKeysToCat(c.id, keys);
          } catch (_) {}
        });
        box.appendChild(el);
      });
    } catch (e) { const b = $("#bt-kbcats"); if (b) b.innerHTML = `<div class="bt-loading">分类加载失败：${esc(e.message)}</div>`; }
  }
  // 分类的「重命名 / 删除」小菜单（复用 #ctx-menu 浮层）
  function openKbCatMenu(c, anchor) {
    const m = $("#ctx-menu"); if (!m) return;
    const r = anchor.getBoundingClientRect();
    m.innerHTML =
      `<div class="ctx-h">${esc(c.name)}</div>` +
      `<div class="ctx-item ctx-agent">🤖 复制「让 Agent 处理本分类」的话</div>` +
      `<div class="ctx-item ctx-rename">✎ 重命名</div>` +
      `<div class="ctx-item danger ctx-del">🗑 删除分类</div>`;
    m.style.left = Math.min(r.left, window.innerWidth - 220) + "px"; m.style.top = (r.bottom + 2) + "px"; m.hidden = false;
    m.querySelector(".ctx-agent").addEventListener("click", async () => {
      m.hidden = true;
      // 生成一段可直接发给 Claude Code / Codex 的话，让 agent 限定在本分类工作
      const prompt2 = `请用 localkb 的 search_localkb 工具、把 category 设为「${c.id}」，检索并综述我知识库里「${c.name}」这个分类下的文献（这组文献我已在 PaperPiggy 里挑好、且已深索）；每个论点带页码引用，最后用 build_digest 存成一份带印刷页引注的资料汇编。`;
      try { await navigator.clipboard.writeText(prompt2); toast(`已复制「让 Agent 处理『${c.name}』」的话，去 Claude Code / Codex 粘贴即可。`); }
      catch (e) {
        const ta = document.createElement("textarea"); ta.value = prompt2; ta.style.position = "fixed"; ta.style.opacity = "0";
        document.body.appendChild(ta); ta.select(); try { document.execCommand("copy"); toast("已复制发给 Agent 的话。"); } catch (_) { toast("复制失败，请手动选中"); } document.body.removeChild(ta);
      }
    });
    m.querySelector(".ctx-rename").addEventListener("click", async () => {
      m.hidden = true;
      const name = await askText("重命名分类", c.name); if (!name) return;
      try { await fetch("/kb/categories/" + encodeURIComponent(c.id), { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) }); loadKbCats(); }
      catch (e) { toast("重命名失败：" + e.message); }
    });
    m.querySelector(".ctx-del").addEventListener("click", async () => {
      m.hidden = true;
      if (!confirm(`删除分类「${c.name}」？只删这个分类清单，不会删除文献，也不会撤销已建好的深索。`)) return;
      try {
        await fetch("/kb/categories/" + encodeURIComponent(c.id), { method: "DELETE" });
        if (BR.scope.type === "kbcat" && BR.scope.id === c.id) selectCollection(null, "全部", null);
        loadKbCats();
      } catch (e) { toast("删除失败：" + e.message); }
    });
  }
  // 右键「加入分类▸」菜单
  function openAddToCatMenu(keys, x, y) {
    const m = $("#ctx-menu"); if (!m) return;
    m.innerHTML =
      `<div class="ctx-h">加入分类（${keys.length} 篇）</div>` +
      BR.cats.map((c) => `<div class="ctx-item" data-id="${esc(c.id)}">🗂 ${esc(c.name)}</div>`).join("") +
      `<div class="ctx-item ctx-new">＋ 新建分类…</div>`;
    m.style.left = Math.min(x, window.innerWidth - 200) + "px"; m.style.top = Math.min(y, window.innerHeight - 60) + "px"; m.hidden = false;
    m.querySelectorAll(".ctx-item[data-id]").forEach((it) =>
      it.addEventListener("click", () => addKeysToCat(it.dataset.id, keys)));
    m.querySelector(".ctx-new").addEventListener("click", async () => {
      const name = await askText("新分类名称", "", "如：认罪认罚从宽"); if (!name) return;
      try {
        const r = await jpost("/kb/categories", { name });
        if (r.ok) { await loadKbCats(); addKeysToCat(r.id, keys); }
      } catch (e) { toast("新建分类失败：" + e.message); }
    });
  }
  async function addKeysToCat(cid, keys) {
    $("#ctx-menu").hidden = true;
    try {
      const r = await jpost(`/kb/categories/${encodeURIComponent(cid)}/members`, { keys });
      const bits = [`已加入 ${num((r.added || []).length)} 篇`];
      if (r.queued) bits.push(`其中 ${num(r.queued)} 篇有全文，已排队深索`);
      if ((r.no_pdf || []).length) bits.push(`${num(r.no_pdf.length)} 篇无 PDF，仅题录、不可深读`);
      if ((r.already_deep || []).length) bits.push(`${num(r.already_deep.length)} 篇已深索`);
      if ((r.already || []).length) bits.push(`${num(r.already.length)} 篇此前已在分类`);
      toast(bits.join("；") + "。");
      loadKbCats();
      if (r.queued) poll();   // 让顶栏进度接管
    } catch (e) { toast("加入分类失败：" + e.message); }
  }
  // ── 左树交互：新建分类 / 收起两区 / 点标题看全部已深索 / 生成AI主题 ──
  (function wireBrowseChrome() {
    const b = $("#bt-kbcat-new");
    if (b) b.addEventListener("click", async () => {
      const name = await askText("新分类名称", "", "如：认罪认罚从宽"); if (!name) return;
      try { const r = await jpost("/kb/categories", { name }); if (r.ok) loadKbCats(); }
      catch (e) { toast("新建分类失败：" + e.message); }
    });
    // 收起/展开两区（状态存 localStorage）
    const applyCollapse = () => {
      const st = JSON.parse(localStorage.getItem("localkb.btCollapse") || "{}");
      const kbcBody = $("#bt-kbc-body"), zotBody = $("#bt-zot-body");
      if (kbcBody) kbcBody.hidden = !!st.kbc;
      if (zotBody) zotBody.hidden = !!st.zot;
      $$(".bt-caret2").forEach((c) => { c.textContent = st[c.dataset.sec] ? "▸" : "▾"; });
    };
    $$(".bt-caret2").forEach((c) => c.addEventListener("click", (e) => {
      e.stopPropagation();
      const st = JSON.parse(localStorage.getItem("localkb.btCollapse") || "{}");
      st[c.dataset.sec] = !st[c.dataset.sec];
      localStorage.setItem("localkb.btCollapse", JSON.stringify(st));
      applyCollapse();
    }));
    applyCollapse();
    // 点「知识库分类（已深索）」标题 → 显示全部已深索文献
    const kbcTitle = $("#bt-kbc-title");
    if (kbcTitle) kbcTitle.addEventListener("click", () => {
      BR.deepFilter = "yes";
      const df = $("#bl-deep-filter"); if (df) df.value = "yes";
      applyScope("all", null, "全部已深索", null, true);
    });
    // 「🔄 生成主题」→ 后台 AI 聚类归类已深索文献
    const gen = $("#bt-topics-gen");
    if (gen) gen.addEventListener("click", async () => {
      gen.disabled = true; const old = gen.textContent; gen.textContent = "归类中…";
      try {
        const r = await jpost("/topics/rebuild", {});
        if (!r.ok) { toast(r.msg || "无法生成主题"); gen.disabled = false; gen.textContent = old; return; }
        // 轮询完成
        const iv = setInterval(async () => {
          try {
            const s = await jget("/topics/status");
            if (!s.running) { clearInterval(iv); gen.disabled = false; gen.textContent = old; loadTopics(); toast("AI 主题已更新。"); }
          } catch (e) { clearInterval(iv); gen.disabled = false; gen.textContent = old; }
        }, 2500);
      } catch (e) { toast("生成主题失败：" + e.message); gen.disabled = false; gen.textContent = old; }
    });
    // 全局点击关闭浮层菜单
    document.addEventListener("click", (e) => {
      const m = $("#ctx-menu");
      if (m && !m.hidden && !m.contains(e.target)) m.hidden = true;
    });
  })();

  // ══════════════════════════════════════════
  //  综合层（Phase 1）：按需专题综述生成 + 综合页查看
  // ══════════════════════════════════════════
  function stripFm(md) {
    const m = (md || "").match(/^---[\s\S]*?\n---\n?/);   // 去掉 YAML frontmatter，只留正文
    return m ? md.slice(m[0].length).trim() : (md || "");
  }
  function llmBody(extra) {
    const c = cfg();
    return Object.assign({ provider: c.provider || "siliconflow", base_url: c.base || "", api_key: c.api_key || "", model: c.model || "" }, extra || {});
  }
  function needKey() {   // 非硅基流动且没填 key → 先去设置（硅基流动可复用检索引擎 key，服务端兜底）
    const c = cfg();
    if (!c.api_key && (c.provider || "siliconflow") !== "siliconflow") { openSettings(); return true; }
    return false;
  }
  function renderWiki(p) {
    $("#wiki-title").textContent = p.title || "综合页";
    const stale = p.stale ? " · ⚠ 可能已过时" : "";
    $("#wiki-meta").textContent = `本地综合 · 基于 ${(p.sources || []).length} 篇 · 生成于 ${(p.generated_at || "").slice(0, 10)} · 模型 ${p.generated_by || "未知"}${stale}`;
    $("#wiki-body").textContent = stripFm(p.markdown);
    $("#wiki-sources").innerHTML = (p.sources || []).length
      ? `<div class="ws-h">参考来源（可回溯到论文页码）</div>` + p.sources.map((s, i) => `<div class="ws-item">[${i + 1}] ${esc(s.citation || s.key)}</div>`).join("")
      : "";
    $("#wiki-regen").dataset.id = p.id;
    $("#wiki-modal").hidden = false;
  }
  async function openWikiPage(id) {
    try { renderWiki(await jget("/wiki/page/" + encodeURIComponent(id))); }
    catch (e) { alert("打开综合页失败：" + (e.message || e)); }
  }
  // 「🗑 不保存此答案」——与「💾 保存此答案」互为反操作（删文件+索引+检索行）。仅人用。
  async function discardWiki(id, onDone, btn) {
    if (!id) return;
    if (!confirm("删除这条本地综合页？会同时删文件、索引与检索行——不影响文献库，可日后重新生成。")) return;
    const old = btn ? btn.textContent : null;
    if (btn) { btn.disabled = true; btn.textContent = "删除中…"; }
    try {
      const resp = await fetch("/wiki/page/" + encodeURIComponent(id), { method: "DELETE" });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      if (onDone) onDone();
    } catch (e) { alert("删除失败：" + (e.message || e)); }
    finally { if (btn) { btn.disabled = false; if (old != null) btn.textContent = old; } }
  }
  async function genTopic(topicId, name, btn) {
    if (needKey()) return;
    const old = btn ? btn.textContent : null;
    if (btn) { btn.textContent = "⏳"; btn.disabled = true; }
    try {
      const r = await jpost("/wiki/topic", llmBody({ topic_id: topicId }));
      if (!r.ok) throw new Error(r.detail || "生成失败");
      await openWikiPage(r.id);
    } catch (e) { alert("生成「" + name + "」综述失败：" + (e.message || e)); }
    finally { if (btn) { btn.textContent = old; btn.disabled = false; } }
  }
  (function wireWikiModal() {
    const close = $("#wiki-close"); if (close) close.addEventListener("click", () => ($("#wiki-modal").hidden = true));
    const disc = $("#wiki-discard");
    if (disc) disc.addEventListener("click", () => discardWiki($("#wiki-regen").dataset.id,
      () => {
        $("#wiki-modal").hidden = true;
        if (wikiLoaded) loadWikiList("silent");        // 从 wiki 页删的，刷新列表
        if ($("#q").value.trim()) doSearch();          // 从检索删的，刷新检索（保留原行为）
      }, disc));
    const regen = $("#wiki-regen");
    if (regen) regen.addEventListener("click", async () => {
      if (needKey()) return;
      const id = regen.dataset.id; if (!id) return;
      regen.textContent = "重新生成中…"; regen.disabled = true;
      try {
        const r = await jpost("/wiki/regenerate/" + encodeURIComponent(id), llmBody({}));
        if (!r.ok) throw new Error(r.detail || "失败");
        await openWikiPage(r.id);
        if (wikiLoaded) loadWikiList("silent");
      } catch (e) { alert("重新生成失败：" + (e.message || e)); }
      finally { regen.textContent = "↻ 重新生成"; regen.disabled = false; }
    });
  })();

  // ══════════════════════════════════════════
  //  wiki 页（综合页书架）：列出 /wiki/list + 新建综述 + 打开/重生/删除
  // ══════════════════════════════════════════
  let WK = { kind: "", agentOnly: false, pages: [] };
  async function loadWikiList(mode) {
    const box = $("#wk-list");
    if (mode !== "silent") box.innerHTML = `<div class="wk-loading">加载综合页中…</div>`;
    try {
      const d = await jget("/wiki/list");
      WK.pages = d.pages || [];
      renderWikiList();
      wikiLoaded = true;   // 成功才置位，失败保持 false 便于切回重试
    } catch (e) {
      box.innerHTML = `<div class="wk-loading">加载失败：${esc(e.message)}</div>`;
    }
  }
  const WK_KIND = { answer: "📝 对话沉淀", concept: "🧩 概念综述", topic: "🗂 主题综述",
                    digest: "📚 资料汇编", outline: "🧭 选题框架" };
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
  function wikiCard(p) {
    const div = document.createElement("div");
    div.className = "wk-card" + (p.stale ? " stale" : "");
    const kind = WK_KIND[p.kind || "answer"] || (p.kind || "");
    const prov = p.by_agent
      ? `<span class="wk-flag agent" title="agent 写回、未经人工核验">🤖 未核验</span>`
      : `<span class="wk-flag" title="你保存/生成的综合页">📝 我保存的</span>`;
    const stale = p.stale ? `<span class="wk-flag stale" title="有新论文可能影响此综合，建议重生">⚠ 可能已过时</span>` : "";
    div.innerHTML =
      `<div class="wk-card-head"><span class="wk-badge k-${esc(p.kind || "answer")}">${esc(kind)}</span>` +
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
      discardWiki(p.id, () => loadWikiList("silent"), e.currentTarget));
    const rb = div.querySelector(".wk-regen");
    rb.addEventListener("click", async () => {
      if (needKey()) return;
      if ((p.kind || "answer") === "answer") { alert("「对话沉淀」页由对话生成，请回「💬 对话」重新提问后再保存。"); return; }
      const old = rb.textContent; rb.disabled = true; rb.textContent = "重新生成中…";
      try {
        const r = await jpost("/wiki/regenerate/" + encodeURIComponent(p.id), llmBody({}));
        if (!r.ok) throw new Error(r.detail || "失败");
        await openWikiPage(r.id);
        loadWikiList("silent");
      } catch (e) { alert("重新生成失败：" + (e.message || e)); }
      finally { rb.disabled = false; rb.textContent = old; }
    });
    return div;
  }
  async function genConcept() {
    const c = $("#wk-concept").value.trim();
    if (!c) { $("#wk-msg").textContent = "请先输入一个概念或主题。"; return; }
    if (needKey()) return;
    const btn = $("#wk-gen"); btn.disabled = true; btn.textContent = "生成中…"; $("#wk-msg").textContent = "";
    try {
      const r = await jpost("/wiki/concept", llmBody({ concept: c }));
      if (!r.ok) throw new Error(r.detail || "生成失败");
      $("#wk-concept").value = "";
      await openWikiPage(r.id);
      loadWikiList("silent");
      $("#wk-msg").textContent = r.cached ? "已命中已有综合（未重复生成）。" : "已生成新综述并加入列表。";
    } catch (e) { $("#wk-msg").textContent = "生成失败：" + (e.message || e); }
    finally { btn.disabled = false; btn.textContent = "＋ 生成综述"; }
  }
  (function wireWikiPage() {
    const gen = $("#wk-gen"); if (gen) gen.addEventListener("click", genConcept);
    const inp = $("#wk-concept"); if (inp) inp.addEventListener("keydown", (e) => { if (e.key === "Enter") genConcept(); });
    const kind = $("#wk-kind"); if (kind) kind.addEventListener("change", () => { WK.kind = kind.value; renderWikiList(); });
    const ag = $("#wk-agent"); if (ag) ag.addEventListener("change", () => { WK.agentOnly = ag.checked; renderWikiList(); });
  })();

  // ══════════════════════════════════════════
  //  Agent 页：MCP 接入引导（本机真实命令 + 工具表 + prompt 示例 + 深索现状）
  // ══════════════════════════════════════════
  let AG = { cfg: null };
  const AG_TOOLS = [
    ["“查库里关于 XX 的文献”", "search_localkb", "在你的文库里做混合检索，返回带期刊分级、页码、可回溯引用的结果"],
    ["“库里现在有多少、索引到哪了”", "localkb_status", "看索引各档进度、篇数，以及已存了多少综合页"],
    ["“把库更新一下 / 深索一下”", "localkb_build", "触发建库或深索（加了新文献后增量更新）"],
    ["“把这个综述存进库”", "save_synthesis", "把 AI 综合出的结论写回成一页带引用的 wiki，下次能被检索命中"],
    ["“库里有没有现成的综述”", "list_wiki", "列已存的综合页，避免重复造轮子"],
    ["“打开那页综述给我看”", "get_wiki_page", "取某页综合的正文 + 来源页码引用"],
  ];
  const AG_PROMPTS = [
    "帮我查库里关于「认罪认罚从宽对司法信任的影响」的权威文献，按期刊层级排。",
    "先 list_wiki 看有没有现成综述；没有的话检索后综合一版，再 save_synthesis 存回来。",
    "把库里关于「社会观护」的核心论点综述一下，每个论断带页码引用。",
    "库里最近加的文献深索了吗？没有的话帮我 localkb_build 深索一下。",
  ];
  function renderAgentCmds() {
    const d = AG.cfg; if (!d) return;
    const su = $("#ag-scope-user");
    $("#ag-claude").textContent = (su && su.checked) ? d.claude_cmd_user : d.claude_cmd;
    $("#ag-codex").textContent = d.codex_toml;
    $("#ag-json").textContent = d.mcp_json;
  }
  function renderAgentTools() {
    $("#ag-tools").innerHTML = AG_TOOLS.map(
      ([say, tool, desc]) => `<tr><td>${esc(say)}</td><td>${esc(desc)}</td></tr>`).join("");
  }
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
  async function loadAgentConfig() {
    try {
      const d = await jget("/agent/mcp-config");
      AG.cfg = d;
      $("#ag-run").classList.toggle("ok", !!d.server_running);
      $("#ag-run-txt").textContent = d.server_running
        ? "本地服务已在运行 · 127.0.0.1:8770" : "本地服务未就绪";
      renderAgentCmds();
      $("#ag-schema").textContent = d.wiki_schema_md || "";
      agentLoaded = true;   // 成功才置位
    } catch (e) {
      $("#ag-run-txt").textContent = "读取接入信息失败：" + e.message;
    }
    renderAgentTools();     // 静态表，无需等网络
    renderAgentPrompts();
    loadAgentDeep();        // 复用 /index/status
  }
  (function wireAgentPage() {
    const su = $("#ag-scope-user"); if (su) su.addEventListener("change", renderAgentCmds);
    $$(".ag-t").forEach((t) => t.addEventListener("click", () => {
      $$(".ag-t").forEach((x) => x.classList.toggle("active", x === t));
      const k = t.dataset.agtab;
      $$(".ag-pane").forEach((p) => { p.hidden = p.dataset.agpane !== k; });
    }));
    $$(".ag-copy").forEach((b) => b.addEventListener("click", async () => {
      const el = $("#" + b.dataset.copy);
      const txt = (el && el.textContent) || "";
      const revert = b.dataset.copy === "ag-schema" ? "复制路径" : "复制";
      try { await navigator.clipboard.writeText(txt); b.textContent = "已复制 ✓"; }
      catch (_) {
        const ta = document.createElement("textarea");
        ta.value = txt; ta.style.position = "fixed"; ta.style.opacity = "0";
        document.body.appendChild(ta); ta.focus(); ta.select();
        try { document.execCommand("copy"); b.textContent = "已复制 ✓"; }
        catch (__) { b.textContent = "复制失败，请手动选中"; }
        document.body.removeChild(ta);
      }
      setTimeout(() => (b.textContent = revert), 1500);
    }));
  })();

  // 只保留友好的「建议深读」标签，隐藏 ⭐15.7 这种裸数字（假精度；口径与设置「学科」无关，见 F17）
  function scoreBadge(p) {
    const s = p.score;
    if (s == null || s < 12) return "";
    return `<span class="brec" title="按期刊层级/年份/有无全文综合推荐，值得优先深读">建议深读</span>`;
  }
  function deepBadge(p) {
    if (p.deep) return `<span class="tag full">📄 已深索</span>`;
    if (p.has_pdf) return `<span class="tag abstract" title="有 PDF，可深索后精读到页码">📋 未深索</span>`;
    return `<span class="tag nopdf" title="无 PDF，无法深索">📋 未深索</span>`;
  }
  // 文件夹模式：AI 抽的题录待人工核对
  function reviewBadge(p) {
    return p.needs_review ? `<span class="tag review" title="这条题录是 AI 从正文读出来的，可能有出入；重要引用前建议核对题名/年份/期刊">📝 待核对</span>` : "";
  }
  function paperCard(p) {
    const div = document.createElement("div");
    div.className = "bcard";
    const selectable = p.has_pdf && !p.deep;
    const checked = BR.selected.has(p.key) ? "checked" : "";
    const cbDisabled = selectable ? "" : "disabled";
    div.innerHTML =
      `<label class="bcard-cb"><input type="checkbox" ${checked} ${cbDisabled} data-key="${esc(p.key)}"/></label>` +
      `<div class="bcard-body">` +
        `<div class="bcard-head">${scoreBadge(p)}${tierBadge(p)}${deepBadge(p)}${reviewBadge(p)}</div>` +
        `<div class="bcard-title" title="点标题：用其中的关键词找相似文献">${esc(p.title || "(无标题)")}</div>` +
        metaRow(p) +
      `</div>`;
    const cb = div.querySelector("input[type=checkbox]");
    if (selectable) cb.addEventListener("change", () => {
      if (cb.checked) BR.selected.add(p.key); else BR.selected.delete(p.key);
      refreshSelUI();
    });
    div.querySelector(".bcard-title").addEventListener("click", () => switchToSearch(p.title || ""));
    // 右键「加入分类▸」：若右击的卡片在多选集里则操作整个多选集，否则只操作这一篇
    div.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      const keys = (BR.selected.has(p.key) && BR.selected.size) ? [...BR.selected] : [p.key];
      openAddToCatMenu(keys, e.clientX, e.clientY);
    });
    // 拖拽入分类（副本#21）：拖动这张卡（或整个多选集）到左树某分类上即加入
    div.draggable = true;
    div.addEventListener("dragstart", (e) => {
      const keys = (BR.selected.has(p.key) && BR.selected.size) ? [...BR.selected] : [p.key];
      e.dataTransfer.setData("application/x-kb-keys", JSON.stringify(keys));
      e.dataTransfer.effectAllowed = "copy";
      div.classList.add("dragging");
    });
    div.addEventListener("dragend", () => div.classList.remove("dragging"));
    return div;
  }

  function refreshSelUI() {
    const n = BR.selected.size;
    $("#bl-sel-n").textContent = num(n);
    $("#bl-deep-sel").disabled = n === 0;
    // 全选框状态：以「当前列表里可深索的」为分母
    const selectable = BR.papers.filter((p) => p.has_pdf && !p.deep);
    const allSel = selectable.length > 0 && selectable.every((p) => BR.selected.has(p.key));
    const box = $("#bl-selall");
    box.checked = allSel;
    box.indeterminate = n > 0 && !allSel;
  }

  async function loadPapers() {
    $("#bl-name").textContent = BR.scope.name;
    $("#bl-list").innerHTML = ""; $("#bl-msg").textContent = "加载中…";
    BR.selected.clear(); refreshSelUI();
    const isRec = BR.sort === "recommend";
    $("#bl-tip").innerHTML = isRec
      ? `⭐ 已按「值得先读」排序：优先高档期刊（权威/核心）、近年、有全文可深读的
         <span class="tip-i" title="推荐分 = 期刊来源门槛（按当前学科评定，0–1）×10 ＋ 近年加成 ＋ 有全文可深读；「建议深读」= 推荐分较高的篇。因此它和「设置 → 期刊分级学科」相关：换学科，推荐分与排序会跟着变。">ⓘ 怎么算的</span>`
      : "";
    $("#bl-tip").style.display = isRec ? "" : "none";
    try {
      const params = new URLSearchParams({ sort: BR.sort, limit: "300" });
      const s = BR.scope;
      if (s.type === "topic") params.set("topic", s.id);
      else if (s.type === "zotero") params.set("collection", s.id);
      else if (s.type === "kbcat") params.set("category", s.id);
      // 深索状态筛选（与范围叠加）
      if (BR.deepFilter) params.set("deep", BR.deepFilter);
      const d = await jget("/papers?" + params.toString());
      BR.papers = d.papers || [];
      $("#bl-count").textContent = `· 共 ${num(d.total != null ? d.total : BR.papers.length)} 篇` +
        (d.total > BR.papers.length ? `（显示前 ${num(BR.papers.length)}）` : "");
      $("#bl-msg").textContent = BR.papers.length ? "" : (s.type === "topic" ? "（该主题暂无文献）" : s.type === "kbcat" ? "（该分类暂无文献，去右键把文献加进来）" : "（该分类暂无文献）");
      const frag = document.createDocumentFragment();
      BR.papers.forEach((p) => frag.appendChild(paperCard(p)));
      $("#bl-list").appendChild(frag);
      refreshSelUI();
    } catch (e) {
      $("#bl-msg").textContent = "加载失败：" + e.message;
    }
  }

  // 触发对一批 key 的深索
  async function deepIndexKeys(keys, btn) {
    if (!keys.length) { $("#bl-msg").textContent = "没有可深索的文献（需有 PDF 且尚未深索）。"; return; }
    if (btn) btn.disabled = true;
    try {
      await jpost("/index/deep", { scope: "keys:" + keys.join(",") });
      localStorage.removeItem("localkb.deepDismissed");
      $("#bl-msg").textContent = `已开始后台深索 ${num(keys.length)} 篇，进度见顶部。`;
      poll(); // 让顶栏进度条接管
    } catch (e) {
      $("#bl-msg").textContent = "启动深索失败：" + e.message;
    } finally {
      if (btn) btn.disabled = false;
      refreshSelUI();
    }
  }

  // 全选 / 取消（只作用于当前列表里可深索的）
  $("#bl-selall").addEventListener("change", () => {
    const on = $("#bl-selall").checked;
    const selectable = BR.papers.filter((p) => p.has_pdf && !p.deep);
    if (on) selectable.forEach((p) => BR.selected.add(p.key));
    else selectable.forEach((p) => BR.selected.delete(p.key));
    // 同步 DOM 上的勾选框
    $$("#bl-list input[type=checkbox]:not([disabled])").forEach((cb) => { cb.checked = on; });
    refreshSelUI();
  });
  $("#bl-sort").addEventListener("change", () => { BR.sort = $("#bl-sort").value; loadPapers(); });
  $("#bl-deep-filter").addEventListener("change", () => { BR.deepFilter = $("#bl-deep-filter").value; loadPapers(); });
  $("#bl-deep-sel").addEventListener("click", () => {
    const keys = BR.papers.filter((p) => BR.selected.has(p.key) && p.has_pdf && !p.deep).map((p) => p.key);
    deepIndexKeys(keys, $("#bl-deep-sel"));
  });
  // 「深索推荐 Top 10」按钮已移除（F19：年份排序时名不副实，且与选中深索重复）
  $("#bt-all").addEventListener("click", () => selectCollection(null, "全部", null));

  // ══════════════════════════════════════════
  //  对话（SSE 流式）—— 保留原逻辑
  // ══════════════════════════════════════════
  const history = [];
  function addBubble(cls, text) {
    const d = document.createElement("div");
    d.className = "bubble " + cls; d.textContent = text;
    $("#chat-log").appendChild(d); $("#chat-log").scrollTop = 1e9; return d;
  }
  function addSources(hits) {
    if (!hits || !hits.length) return;
    const det = document.createElement("details");
    det.className = "sources";
    det.innerHTML = `<summary>📎 ${hits.length} 条来源（点击展开）</summary>` +
      hits.map((h, i) => `<div class="src-item"><div class="cite">[${i + 1}] ${esc(h.citation || h.title || "")}</div><div class="snippet">${esc((h.text || "").trim())}</div></div>`).join("");
    $("#chat-log").appendChild(det); $("#chat-log").scrollTop = 1e9;
  }
  // 「保存此答案」——把这次问答沉淀成带引用的综合页（Phase 0，opt-in）
  function addSaveBtn(botEl, query, answer, hits) {
    const bar = document.createElement("div");
    bar.className = "chat-actions";
    const btn = document.createElement("button");
    btn.className = "ghost2 save-answer";
    btn.textContent = "💾 保存此答案";
    const msg = document.createElement("span");
    msg.className = "save-msg";
    bar.appendChild(btn); bar.appendChild(msg);
    botEl.after(bar);
    btn.addEventListener("click", async () => {
      btn.disabled = true; btn.textContent = "保存中…"; msg.textContent = "";
      try {
        const c = cfg();
        const r = await jpost("/wiki/answer", {
          query, answer,
          sources: (hits || []).map((h) => ({ key: h.key || "", citation: h.citation || "" })).filter((s) => s.key),
          model: c.model || "",
        });
        btn.textContent = "✓ 已沉淀为综合页";
        msg.innerHTML = (r.indexed ? "（已入库，检索/wiki页可见）" : "（已存盘，重建索引后可检索）") +
          ` <a class="save-goto" href="#">→ 去 wiki 页查看</a>`;
        const go = msg.querySelector(".save-goto");
        if (go) go.addEventListener("click", (e) => {
          e.preventDefault();
          switchTab("wiki");
          if (r.id) openWikiPage(r.id);      // 直接弹出刚存的这页
        });
      } catch (e) {
        btn.disabled = false; btn.textContent = "💾 保存此答案";
        msg.textContent = "保存失败：" + (e.message || e);
      }
    });
  }
  async function doChat() {
    const q = $("#chat-q").value.trim(); if (!q) return;
    const c = cfg();
    // 硅基流动可复用检索引擎的 key（服务端兜底），故无 key 也放行；其它服务商仍要求先填 key
    if (!c.api_key && (c.provider || "siliconflow") !== "siliconflow") { openSettings(); return; }
    const hint = $(".chat-hint"); if (hint) hint.remove();
    $("#chat-q").value = ""; $("#chat-q").style.height = "auto";
    addBubble("user", q);
    const bot = addBubble("bot", "");
    $("#chat-go").disabled = true;
    try {
      const resp = await fetch("/chat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: q, history: history.slice(-6),
          provider: c.provider || "siliconflow", base_url: c.base || "", api_key: c.api_key || "", model: c.model || "",
          topk: 6, sort: "blend", category: ($("#chat-cat") && $("#chat-cat").value) || null }),
      });
      const reader = resp.body.getReader(); const dec = new TextDecoder(); let buf = "", answer = "", errored = false, srcHits = [];
      while (true) {
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const evt = buf.slice(0, idx); buf = buf.slice(idx + 2);
          const line = evt.split("\n").find((l) => l.startsWith("data:")); if (!line) continue;
          const data = line.slice(5).trim();
          if (data === "[DONE]") continue;
          let j; try { j = JSON.parse(data); } catch (e) { continue; }
          if (j.sources) { srcHits = j.sources || []; addSources(j.sources); }
          else if (j.delta) { answer += j.delta; bot.textContent = answer; $("#chat-log").scrollTop = 1e9; }
          else if (j.error) { errored = true; bot.textContent = "⚠ " + j.error; }
        }
      }
      history.push({ role: "user", content: q }, { role: "assistant", content: answer });
      if (answer && !errored) addSaveBtn(bot, q, answer, srcHits);
      // 流正常结束却一个字都没吐、也没报错：给一句兜底，避免留个空白气泡让人以为卡死
      else if (!answer && !errored) bot.textContent = "⚠ 模型没有返回内容，请重试，或在设置里检查对话模型/Key。";
    } catch (e) { bot.textContent = "⚠ 请求失败：" + e; }
    finally { $("#chat-go").disabled = false; }
  }
  $("#chat-go").addEventListener("click", doChat);
  $("#chat-q").addEventListener("keydown", (e) => {
    // Enter 发送，但发送中（按钮禁用）不重复提交，避免并发污染 history
    if (e.key === "Enter" && !e.shiftKey && !$("#chat-go").disabled) { e.preventDefault(); doChat(); }
  });
  $("#chat-q").addEventListener("input", (e) => { e.target.style.height = "auto"; e.target.style.height = Math.min(e.target.scrollHeight, 140) + "px"; });

  // ══════════════════════════════════════════
  //  设置 —— 保留原逻辑
  // ══════════════════════════════════════════
  const provSel = $("#set-provider");
  Object.keys(PROVIDERS).forEach((k) => { const o = document.createElement("option"); o.value = k; o.textContent = PROVIDER_NAMES[k]; provSel.appendChild(o); });
  function applyProvider(k, keepUserFields) {
    const p = PROVIDERS[k];
    if (!keepUserFields) { $("#set-base").value = p.base; $("#set-model").value = p.model; }
    $("#set-keyurl").innerHTML = p.keyurl ? `获取 API Key：<a href="${p.keyurl}" target="_blank">${p.keyurl}</a>` : "";
  }
  // 换服务商：套该商的预设默认（base/model），再即时落盘（对话模型设置已内联到对话页 #chat-model）
  provSel.addEventListener("change", () => { applyProvider(provSel.value, false); saveChatModel(); });
  function openSettings() {
    // LLM 服务商字段已搬到对话页折叠区，设置弹窗不再回填它们（避免覆盖对话页已填的 key）
    $("#settings-modal").hidden = false;
    loadSac();    // 进入设置面板即拉取 SAC 当前状态并回填
    loadEngine(); // 同时回填检索引擎当前后端/是否已设 key
    loadDiscipline(); // 回填期刊分级学科下拉
    loadAutoUpdate(); // 回填自动更新开关/间隔
  }

  // ── 自动更新（即改即存）──
  async function loadAutoUpdate() {
    const en = $("#au-enabled"), iv = $("#au-interval"); if (!en) return;
    try {
      const s = await jget("/setup/auto_update");
      en.checked = !!s.enabled;
      if (iv) iv.value = String(s.interval_min || 30);
    } catch (e) {}
  }
  async function saveAutoUpdate() {
    const en = $("#au-enabled"), iv = $("#au-interval"), msg = $("#au-msg");
    try {
      await jpost("/setup/auto_update", { enabled: en.checked, interval_min: parseInt(iv.value, 10) || 30 });
      if (msg) msg.textContent = en.checked ? `已开启：约每 ${iv.value} 分钟检查一次新增文献并自动更新。` : "已关闭：只能用顶栏「⟳ 更新知识库」手动更新。";
    } catch (e) { if (msg) msg.textContent = "保存失败：" + e.message; }
  }
  (function wireAutoUpdate() {
    const en = $("#au-enabled"), iv = $("#au-interval");
    if (en) en.addEventListener("change", saveAutoUpdate);
    if (iv) iv.addEventListener("change", saveAutoUpdate);
  })();

  // ── 期刊分级学科：设置里选整库锁定的学科，保存后下次检索即生效 ──
  async function loadDiscipline() {
    const sel = $("#disc-select"); if (!sel) return;
    try {
      const s = await jget("/setup/discipline");
      // 名称直接用后端 name（个人档已含「（开发者增强，欢迎试用）」）——不再前端追加，避免「（个人增强）（个人增强）」双重（F64）
      sel.innerHTML = (s.disciplines || []).map(d =>
        `<option value="${esc(d.id)}">${esc(d.name)}</option>`).join("");
      if (s.current) sel.value = s.current;
      $("#disc-msg").textContent = "";
    } catch (e) {
      $("#disc-msg").textContent = "加载失败：" + e.message;
    }
  }
  // 切学科后刷新库总览/浏览的分级口径。分级分布是后台预热的（首次冷算 20+s），
  // 故先立即刷一次（可能仍 pending），再轮询 grading_pending 就绪后自动补刷。
  function refreshAfterDiscipline() {
    if (dashLoaded) loadDashboard("silent");
    if (browseLoaded) loadPapers();
    let tries = 0;
    const iv = setInterval(async () => {
      tries++;
      try {
        const s = await jget("/stats");
        if (!s.grading_pending || tries > 20) {
          clearInterval(iv);
          if (dashLoaded) loadDashboard("silent");
          if (browseLoaded) loadPapers();
        }
      } catch (e) { clearInterval(iv); }
    }, 2000);
  }
  // 学科下拉即改即存（F65 去掉「保存学科」按钮）：onChange 直接 POST，下次检索生效
  const _dsel = $("#disc-select");
  if (_dsel) _dsel.addEventListener("change", async () => {
    const sel = $("#disc-select"), msg = $("#disc-msg");
    try {
      const r = await jpost("/setup/discipline", { discipline: sel.value });
      msg.textContent = "已切换：" + (sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].text : r.current) + "（检索即时生效；分级分布后台重算后自动刷新）";
      refreshAfterDiscipline();
    } catch (e) { msg.textContent = "保存失败：" + e.message; }
  });
  $("#btn-settings").addEventListener("click", openSettings);
  $("#set-close").addEventListener("click", () => $("#settings-modal").hidden = true);

  // ── 对话页「模型设置」折叠区（原设置弹窗的 LLM 服务商块内联到此，onChange 即存）──
  // 冷启动即回填对话页模型设置（原逻辑只在打开设置弹窗时回填）
  function initChatModel() {
    const c = cfg();
    provSel.value = c.provider || "siliconflow";
    applyProvider(provSel.value, true);
    $("#set-base").value = c.base || PROVIDERS[provSel.value].base;
    $("#set-model").value = c.model || PROVIDERS[provSel.value].model;
    $("#set-key").value = c.api_key || "";
  }
  function saveChatModel() {
    saveCfg({ provider: provSel.value, base: $("#set-base").value.trim(),
              api_key: $("#set-key").value.trim(), model: $("#set-model").value.trim() });
    const s = $("#cm-saved");
    if (s) { s.textContent = "已保存 ✓"; s.classList.add("flash"); setTimeout(() => s.classList.remove("flash"), 800); }
  }
  ["#set-base", "#set-key", "#set-model"].forEach((sel) => {
    const el = $(sel); if (el) el.addEventListener("input", saveChatModel);
  });
  initChatModel();
  const _c2a = $("#chat-to-agent"); if (_c2a) _c2a.addEventListener("click", () => switchTab("agent"));

  // 对话「限定分类」下拉：只列知识库分类（用户分类 + AI 主题；Zotero 收藏夹多未深索，不列）
  async function loadChatCats() {
    const sel = $("#chat-cat"); if (!sel) return;
    const cur = sel.value;
    const [cats, tops] = await Promise.all([
      jget("/kb/categories").then((d) => d.categories || []).catch(() => []),
      jget("/topics").then((d) => d.topics || []).catch(() => []),
    ]);
    sel.innerHTML = `<option value="">全部文献</option>`
      + cats.map((c) => `<option value="${esc(c.id)}">🗂 ${esc(c.name)}（${num(c.deep_count)}/${num(c.count)}）</option>`).join("")
      + tops.map((t) => `<option value="topic:${t.id}">🧠 ${esc(t.name)}（${num(t.size)}）</option>`).join("");
    if (cur) sel.value = cur;   // 保留已选
  }

  // 恢复默认设置：清后端 settings + 本机对话 key，回填面板
  const _reset = $("#set-reset");
  if (_reset) _reset.addEventListener("click", async () => {
    if (!confirm("恢复默认设置？会清空：检索引擎回本地、期刊学科回标准法学、API/摘要 key、以及本机保存的对话模型 Key。文献索引不受影响。")) return;
    try {
      await jpost("/setup/reset", {});
      localStorage.removeItem("localkb.cfg");   // 清对话 LLM 配置（存 localStorage）
      initChatModel();                          // 对话页模型设置回默认
      loadEngine(); loadSac(); loadDiscipline();
      poll();
      alert("已恢复默认设置。");
    } catch (e) { alert("恢复默认失败：" + (e.message || e)); }
  });

  // ── 检索引擎（嵌入/重排）：设置里随时切换本地/API、改 key，不必重跑首启向导 ──
  function engApiVisible(be) { const box = $("#eng-api"); if (box) box.hidden = (be !== "api"); }
  async function loadEngine() {
    $("#eng-msg").textContent = "";
    // /setup/detect 只回 backend + 是否已设 key；base/模型名用标准默认（改过高级项的可自行重填）
    $("#eng-base").value = "https://api.siliconflow.cn/v1";
    $("#eng-embed").value = ""; $("#eng-rerank").value = ""; $("#eng-key").value = "";
    let be = "local", keySet = false;
    try { const d = await jget("/setup/detect"); be = d.backend === "api" ? "api" : "local"; keySet = !!d.api_key_set; } catch (e) {}
    const r = document.querySelector(`input[name=eng-backend][value=${be}]`); if (r) r.checked = true;
    $("#eng-key").placeholder = keySet ? "已设置（留空＝不改）" : "去 cloud.siliconflow.cn 领免费 key";
    engApiVisible(be);
  }
  $$("input[name=eng-backend]").forEach((r) => r.addEventListener("change", () =>
    engApiVisible((document.querySelector("input[name=eng-backend]:checked") || {}).value || "local")));
  function engBody() {
    const be = (document.querySelector("input[name=eng-backend]:checked") || {}).value || "local";
    if (be !== "api") return { backend: "local" };
    const body = { backend: "api", base: $("#eng-base").value.trim() || "https://api.siliconflow.cn/v1" };
    const key = $("#eng-key").value.trim(); if (key) body.key = key;   // 留空＝不改，避免把已存的 key 清空
    const em = $("#eng-embed").value.trim(); if (em) body.embed_model = em;
    const rm = $("#eng-rerank").value.trim(); if (rm) body.rerank_model = rm;
    return body;
  }
  $("#eng-test").addEventListener("click", async () => {
    const msg = $("#eng-msg"), b = engBody();
    if (b.backend !== "api") { msg.className = "hint"; msg.textContent = "本地模式无需测试连接。"; return; }
    const btn = $("#eng-test"); btn.disabled = true; msg.className = "hint"; msg.textContent = "测试中…";
    try {
      const r = await jpost("/setup/test_api", { base: b.base, key: b.key, embed_model: b.embed_model, rerank_model: b.rerank_model });
      if (r && r.ok) { msg.className = "hint ok"; msg.textContent = `✓ 连接成功，向量维度 ${num(r.dim)}，延迟 ${num(r.latency_ms)}ms`; }
      else { msg.className = "hint warn"; msg.textContent = "连接失败：" + esc((r && r.msg) || "未知错误"); }
    } catch (e) { msg.className = "hint warn"; msg.textContent = "连接失败：" + esc(e.message); }
    finally { btn.disabled = false; }
  });
  $("#eng-save").addEventListener("click", async () => {
    const msg = $("#eng-msg"), btn = $("#eng-save"); btn.disabled = true;
    msg.className = "hint"; msg.textContent = "保存中…";
    try {
      const r = await jpost("/setup/backend", engBody());
      msg.className = "hint ok";
      msg.textContent = "✓ 已保存检索引擎（" + (r.backend === "api" ? "API" : "本地") + "）。" + (r.warn ? " ⚠ " + r.warn : "");
      poll();
    } catch (e) { msg.className = "hint warn"; msg.textContent = "保存失败：" + esc(e.message); }
    finally { btn.disabled = false; }
  });

  // ── 深索摘要（SAC）：GET 回填 / 开关即存 / 高级单独存 ──
  // 依据 effective_ready 显示三态状态行
  function renderSacStatus(enabled, effectiveReady) {
    const el = $("#sac-status");
    if (!enabled) {
      el.className = "sac-status hint"; el.textContent = "已关闭：深索时不生成摘要。";
    } else if (effectiveReady) {
      el.className = "sac-status hint ok"; el.textContent = "✓ 已就绪：深索时会自动生成摘要前缀。";
    } else {
      el.className = "sac-status hint warn"; el.textContent = "⚠ 需先在检索引擎里配 API key，或在下方“高级”单独填 key。";
    }
  }
  async function loadSac() {
    // 先给个中性态，避免上次残留
    $("#sac-status").className = "sac-status hint";
    $("#sac-status").textContent = "";
    try {
      const s = await jget("/setup/sac");
      $("#sac-enabled").checked = !!s.enabled;
      $("#sac-base").value = s.base || "";
      $("#sac-model").value = s.model || "";
      // key 是密码，后端只回 key_set 布尔；已设则用占位提示，不回填明文
      $("#sac-key").value = "";
      $("#sac-key").placeholder = s.key_set ? "已设置（留空＝不改；复用检索引擎的 key）" : "留空＝复用检索引擎的 key";
      renderSacStatus(!!s.enabled, !!s.effective_ready);
    } catch (e) {
      $("#sac-status").className = "sac-status hint warn";
      $("#sac-status").textContent = "状态加载失败：" + e.message;
    }
  }
  // 开关切换：立即 POST 保存并用返回的 effective_ready 刷新状态行
  $("#sac-enabled").addEventListener("change", async () => {
    const enabled = $("#sac-enabled").checked;
    try {
      const r = await jpost("/setup/sac", { enabled });
      renderSacStatus(enabled, !!(r && r.effective_ready));
    } catch (e) {
      // 保存失败则回滚勾选态并提示
      $("#sac-enabled").checked = !enabled;
      $("#sac-status").className = "sac-status hint warn";
      $("#sac-status").textContent = "保存失败：" + e.message;
    }
  });
  // 高级：单独保存 base / key / model（只传填了的字段）
  $("#sac-adv-save").addEventListener("click", async () => {
    const msg = $("#sac-adv-msg");
    const body = { enabled: $("#sac-enabled").checked };
    const base = $("#sac-base").value.trim(), model = $("#sac-model").value.trim(), key = $("#sac-key").value.trim();
    body.base = base; body.model = model;
    if (key) body.key = key; // 留空则不改，后端复用检索引擎的 key
    msg.textContent = "保存中…";
    try {
      const r = await jpost("/setup/sac", body);
      renderSacStatus($("#sac-enabled").checked, !!(r && r.effective_ready));
      $("#sac-key").value = ""; // 存完清空明文输入
      if (key) $("#sac-key").placeholder = "已设置（留空＝不改；复用检索引擎的 key）";
      msg.textContent = "已保存 ✓";
    } catch (e) { msg.textContent = "保存失败：" + e.message; }
  });

  // ── 错误日志（查看 / 复制 / 清空）──
  const errMsg = (t) => { $("#err-msg").textContent = t || ""; };
  $("#err-view").addEventListener("click", async () => {
    $("#err-panel").hidden = false;
    $("#err-text").value = "加载中…"; errMsg("");
    try {
      const r = await jget("/errors?n=200");
      $("#err-text").value = r.errors || "（暂无错误记录）";
      errMsg(r.lines ? `共 ${num(r.lines)} 行` : "");
    } catch (e) { $("#err-text").value = "加载失败：" + e.message; }
  });
  $("#err-copy").addEventListener("click", async () => {
    const txt = $("#err-text").value || "";
    try {
      await navigator.clipboard.writeText(txt);
      errMsg("已复制到剪贴板 ✓");
    } catch (e) {
      // 剪贴板 API 不可用时退化为选中，方便手动 Ctrl+C
      $("#err-text").focus(); $("#err-text").select();
      errMsg("已全选，请按 Ctrl+C 复制");
    }
  });
  $("#err-clear").addEventListener("click", async () => {
    try {
      await jpost("/errors/clear", {});
      $("#err-text").value = "（已清空）"; errMsg("已清空 ✓");
    } catch (e) { errMsg("清空失败：" + e.message); }
  });

  // 设置里的「深度索引」小节已删除（F67）：深索入口改由库总览深索卡 + 检索区深索邀请卡 + 浏览页选中深索承担。

  // ══════════════════════════════════════════
  //  建库（增量更新）—— 保留原逻辑
  // ══════════════════════════════════════════
  let buildTimer = null;
  $("#btn-build").addEventListener("click", () => { $("#build-modal").hidden = false; refreshBuild(); });
  $("#build-close").addEventListener("click", () => { $("#build-modal").hidden = true; clearInterval(buildTimer); });
  $("#build-start").addEventListener("click", async () => {
    $("#build-start").disabled = true;
    await fetch("/build", { method: "POST" });
    buildTimer = setInterval(refreshBuild, 1500);
  });
  async function refreshBuild() {
    try {
      const s = await (await fetch("/build/status")).json();
      $("#build-log").textContent = (s.log || []).join("\n") || "（无输出）";
      $("#build-log").scrollTop = 1e9;
      if (!s.running) { $("#build-start").disabled = false; clearInterval(buildTimer); }
    } catch (e) {}
  }

  // ══════════════════════════════════════════
  //  首启向导（Onboarding）
  // ══════════════════════════════════════════
  // backend: 引擎选择的临时态（local 默认；api 模式记录表单与「是否测通」）
  const WZ = { detect: null, step: 1, backend: "local", api: { base: "https://api.siliconflow.cn/v1", key: "", embed_model: "BAAI/bge-m3", rerank_model: "BAAI/bge-reranker-v2-m3" }, apiTested: false };
  function setStep(n) {
    WZ.step = n;
    $$(".wizard-steps li").forEach((li) => {
      const s = parseInt(li.dataset.step, 10);
      li.classList.toggle("active", s === n);
      li.classList.toggle("done", s < n);
    });
  }
  function closeWizard() { $("#wizard").hidden = true; }

  function renderStep1() {
    setStep(1);
    const d = WZ.detect || {};
    const row = (icOk, icMiss, k, val, okTxt, missTxt) => {
      const ok = !!val;
      return `<li><span class="ic">${ok ? icOk : icMiss}</span><span class="k">${k}</span>
        <span class="v ${ok ? "ok" : "miss"}">${ok ? esc(String(val)) : (missTxt || "未检测到")}</span></li>`;
    };
    // 模型状态行随后端：API 模式无需本地模型；本地模式看模型文件是否就绪
    const isApi = d.backend === "api";
    let semanticRow, rerankerRow;
    if (isApi) {
      semanticRow = `<li><span class="ic">☁️</span><span class="k">语义模型</span><span class="v ok">API 模式（无需本地模型）</span></li>`;
      rerankerRow = `<li><span class="ic">☁️</span><span class="k">重排模型</span><span class="v ok">API 模式（无需本地模型）</span></li>`;
    } else {
      semanticRow = row("🧠", "⏬", "语义模型", d.models_local ? "已就绪（bge-m3）" : "", null, "尚未下载（可在第 4 步准备）");
      rerankerRow = row("🎯", "⏬", "重排模型", d.reranker_local ? "已就绪" : "", null, "尚未下载（可在第 4 步准备）");
    }
    const noZoteroHint = !d.zotero_dir
      ? `<div class="wz-note">未检测到 Zotero —— 没关系，第 3 步可选「文件夹模式」，直接放 PDF 建库。</div>` : "";
    $("#wizard-body").innerHTML =
      `<ul class="wz-check">
        ${row("📁", "⚠️", "Zotero 目录", d.zotero_dir, null, "未探测到（可用文件夹模式）")}
        ${semanticRow}
        ${rerankerRow}
      </ul>
      ${noZoteroHint}
      <div class="wz-actions">
        <button class="primary" id="wz1-next">下一步 →</button>
      </div>`;
    $("#wz1-next").addEventListener("click", renderStepKey);
  }

  // 第 2 步（新）：领一个免费 SiliconFlow key —— 很多功能（对话/AI摘要/抽题录/找相似）用它的免费模型就够
  function renderStepKey() {
    setStep(2);
    const cur = (WZ.api && WZ.api.key) || "";
    $("#wizard-body").innerHTML =
      `<div class="wz-note">强烈建议先领一个 <b>免费的 SiliconFlow（硅基流动）Key</b>。填这一个，PaperPiggy 的很多小功能——
        对话、深索时自动生成 AI 摘要、文件夹模式自动读题录、点标题「找相似」——都能用它的<b>免费模型</b>跑起来。
        <br>（认真做研究更推荐把库接进 Agent，见后面「🤖 Agent」页；这个 Key 是给应用内小功能兜底用的。）</div>
      <div class="wz-field">
        <label>SiliconFlow API Key（可选，但强烈建议）</label>
        <input id="wzk-key" type="password" value="${esc(cur)}" placeholder="去 https://account.siliconflow.cn/zh/login 登录领免费额度" />
      </div>
      <p class="wz-mini">👉 <a href="https://account.siliconflow.cn/zh/login" target="_blank" rel="noopener">点此打开 SiliconFlow 登录页领 Key</a>。也可以先跳过，之后在「⚙ 设置」里补。</p>
      <div id="wzk-msg"></div>
      <div class="wz-actions">
        <button class="ghost2c wz-back" id="wzk-back">← 上一步</button>
        <button class="primary" id="wzk-next">下一步：选择检索引擎 →</button>
      </div>`;
    $("#wzk-back").addEventListener("click", renderStep1);
    $("#wzk-next").addEventListener("click", async () => {
      const k = $("#wzk-key").value.trim();
      if (k) {
        try {
          await jpost("/setup/backend", { backend: WZ.backend, key: k });  // 只存 key，不改后端
          WZ.api.key = k; if (WZ.detect) WZ.detect.meta_ready = true; APP.metaReady = true;
          // 对话页也用这个 key（siliconflow）
          const c = cfg(); c.provider = c.provider || "siliconflow"; c.api_key = c.api_key || k; saveCfg(c);
        } catch (e) { $("#wzk-msg").innerHTML = `<div class="wz-err">保存失败：${esc(e.message)}（可跳过，之后在设置里补）</div>`; }
      }
      renderStep2();
    });
  }

  // ── 第 2 步：选择检索引擎（本地 / API 二选一）──
  function renderStep2() {
    setStep(3);
    const a = WZ.api;
    const localSel = WZ.backend === "local";
    $("#wizard-body").innerHTML =
      `<div class="wz-engines">
        <label class="wz-engine ${localSel ? "sel" : ""}" data-be="local">
          <input type="radio" name="wz-backend" value="local" ${localSel ? "checked" : ""} />
          <div class="wz-engine-body">
            <div class="wz-engine-h">🔒 本地模式 <span class="wz-badge-rec">推荐</span></div>
            <div class="wz-engine-d">离线 · 隐私不出本机 · 需下载约 1.2GB 模型</div>
          </div>
        </label>
        <label class="wz-engine ${localSel ? "" : "sel"}" data-be="api">
          <input type="radio" name="wz-backend" value="api" ${localSel ? "" : "checked"} />
          <div class="wz-engine-body">
            <div class="wz-engine-h">☁️ API 模式 <span class="wz-badge-save">省空间</span></div>
            <div class="wz-engine-d">接入 SiliconFlow 等 OpenAI 兼容 API 做嵌入+重排，免费、免下载；检索时联网，文本会发给该服务商</div>
          </div>
        </label>
      </div>
      <div id="wz-api-form" class="wz-api-form" ${localSel ? "hidden" : ""}>
        <div class="wz-field">
          <label>服务商 Base URL</label>
          <input id="wz-api-base" value="${esc(a.base)}" placeholder="https://api.siliconflow.cn/v1" />
        </div>
        <div class="wz-field">
          <label>API Key</label>
          <input id="wz-api-key" type="password" value="${esc(a.key)}" placeholder="去 https://cloud.siliconflow.cn/account/ak 领免费 key" />
        </div>
        <details class="wz-adv">
          <summary>高级：模型名（一般不用改）</summary>
          <div class="wz-field">
            <label>嵌入模型</label>
            <input id="wz-api-embed" value="${esc(a.embed_model)}" placeholder="BAAI/bge-m3" />
          </div>
          <div class="wz-field">
            <label>重排模型</label>
            <input id="wz-api-rerank" value="${esc(a.rerank_model)}" placeholder="BAAI/bge-reranker-v2-m3" />
          </div>
        </details>
        <div class="wz-actions-inline">
          <button class="ghost2c" id="wz-api-test">测试连接</button>
          <span id="wz-api-test-msg" class="wz-test-msg"></span>
        </div>
        <p class="wz-mini">若你在「对话」里也用 SiliconFlow，同一个 key 即可。</p>
      </div>
      <div id="wz2-msg"></div>
      <div class="wz-actions">
        <button class="ghost2c wz-back" id="wz2-back">← 上一步</button>
        <button class="primary" id="wz2-next">下一步 →</button>
        
      </div>`;
    { const _sk=$("#wz-skip"); if (_sk) _sk.addEventListener("click", closeWizard); }
    $("#wz2-back").addEventListener("click", renderStepKey);

    // 单选切换：更新选中态 + 展开/收起 API 表单
    const syncBackend = () => {
      const be = $$("input[name=wz-backend]").length
        ? (document.querySelector("input[name=wz-backend]:checked") || {}).value || "local"
        : "local";
      WZ.backend = be;
      $$(".wz-engine").forEach((el) => el.classList.toggle("sel", el.dataset.be === be));
      $("#wz-api-form").hidden = be !== "api";
    };
    $$("input[name=wz-backend]").forEach((r) => r.addEventListener("change", syncBackend));

    // 表单输入回写 WZ.api；改动后视为「未测通」
    const readApiForm = () => {
      WZ.api.base = $("#wz-api-base").value.trim();
      WZ.api.key = $("#wz-api-key").value.trim();
      WZ.api.embed_model = $("#wz-api-embed").value.trim() || "BAAI/bge-m3";
      WZ.api.rerank_model = $("#wz-api-rerank").value.trim() || "BAAI/bge-reranker-v2-m3";
    };
    ["#wz-api-base", "#wz-api-key", "#wz-api-embed", "#wz-api-rerank"].forEach((sel) => {
      const el = $(sel); if (el) el.addEventListener("input", () => { readApiForm(); WZ.apiTested = false; });
    });

    // 测试连接
    $("#wz-api-test").addEventListener("click", async () => {
      readApiForm();
      const btn = $("#wz-api-test"), msg = $("#wz-api-test-msg");
      if (!WZ.api.key) { msg.className = "wz-test-msg err"; msg.textContent = "请先填 API Key"; return; }
      btn.disabled = true; msg.className = "wz-test-msg"; msg.textContent = "测试中…";
      try {
        const r = await jpost("/setup/test_api", {
          base: WZ.api.base, key: WZ.api.key,
          embed_model: WZ.api.embed_model, rerank_model: WZ.api.rerank_model,
        });
        if (r && r.ok) {
          WZ.apiTested = true;
          msg.className = "wz-test-msg ok";
          msg.textContent = `✓ 连接成功，向量维度 ${num(r.dim)}${r.rerank_ok === false ? "（重排未通过）" : ""}，延迟 ${num(r.latency_ms)}ms`;
        } else {
          WZ.apiTested = false;
          msg.className = "wz-test-msg err";
          msg.textContent = "连接失败：" + esc((r && r.msg) || "未知错误");
        }
      } catch (e) {
        WZ.apiTested = false;
        msg.className = "wz-test-msg err";
        msg.textContent = "连接失败：" + esc(e.message);
      } finally { btn.disabled = false; }
    });

    // 下一步：提交后端选择；API 模式需已填并测通
    $("#wz2-next").addEventListener("click", async () => {
      syncBackend();
      $("#wz2-msg").innerHTML = "";
      if (WZ.backend === "api") {
        readApiForm();
        if (!WZ.api.key) { $("#wz2-msg").innerHTML = `<div class="wz-err">请先填写 API Key，或选择本地模式。</div>`; return; }
        if (!WZ.apiTested) { $("#wz2-msg").innerHTML = `<div class="wz-err">请先点「测试连接」确认 key 可用，再继续。</div>`; return; }
      }
      const btn = $("#wz2-next"); btn.disabled = true;
      const body = WZ.backend === "api"
        ? { backend: "api", base: WZ.api.base, key: WZ.api.key, embed_model: WZ.api.embed_model, rerank_model: WZ.api.rerank_model }
        : { backend: "local" };
      try {
        const r = await jpost("/setup/backend", body);
        // 同步本地 detect 缓存，供第 1/4 步与后续判断
        if (WZ.detect) { WZ.detect.backend = (r && r.backend) || WZ.backend; WZ.detect.api_key_set = !!(r && r.api_key_set); }
        renderStep3();
      } catch (e) {
        $("#wz2-msg").innerHTML = `<div class="wz-err">保存失败：${esc(e.message)}</div>`;
        btn.disabled = false;
      }
    });
  }

  // ── 第 3 步：连接文库 ──
  // 第 3 步：选择文库来源（连接 Zotero / 文件夹模式）
  function renderStep3() {
    setStep(4);
    const d = WZ.detect || {};
    if (!WZ.srcChoice) WZ.srcChoice = d.zotero_detected ? "zotero" : "folder";
    const zSel = WZ.srcChoice === "zotero";
    $("#wizard-body").innerHTML =
      `<div class="wz-engines">
        <label class="wz-engine ${zSel ? "sel" : ""}" data-src="zotero">
          <input type="radio" name="wz-src" value="zotero" ${zSel ? "checked" : ""} ${d.zotero_detected ? "" : "disabled"} />
          <div class="wz-engine-body">
            <div class="wz-engine-h">🔗 连接 Zotero ${d.zotero_detected ? '<span class="wz-badge-rec">已检测到</span>' : '<span class="wz-badge-save">未检测到</span>'}</div>
            <div class="wz-engine-d">直接读取 Zotero 里每一条文献（含题录和收藏夹分类），不会改动你的 Zotero 数据。</div>
          </div>
        </label>
        <label class="wz-engine ${zSel ? "" : "sel"}" data-src="folder">
          <input type="radio" name="wz-src" value="folder" ${zSel ? "" : "checked"} />
          <div class="wz-engine-body">
            <div class="wz-engine-h">📁 文件夹模式 <span class="wz-badge-save">无需 Zotero</span></div>
            <div class="wz-engine-d">指定一个文件夹放 PDF，系统用 AI 自动读出题名、作者、年份、期刊等信息。适合没装 Zotero、手上就是一堆 PDF 的你。</div>
          </div>
        </label>
      </div>
      <div id="wz-src-body"></div>`;
    const sync = () => {
      WZ.srcChoice = (document.querySelector("input[name=wz-src]:checked") || {}).value || "folder";
      $$(".wz-engine").forEach((el) => el.classList.toggle("sel", el.dataset.src === WZ.srcChoice));
      WZ.srcChoice === "zotero" ? renderStep3Zotero() : renderStep3Folder();
    };
    $$("input[name=wz-src]").forEach((r) => r.addEventListener("change", sync));
    sync();
  }
  function renderStep3Zotero() {
    const d = WZ.detect || {};
    $("#wz-src-body").innerHTML =
      `<div class="wz-field">
        <label>Zotero 数据目录（含 zotero.sqlite，留空自动探测）</label>
        <input id="wz-zdir" value="${esc(d.zotero_dir || "")}" placeholder="如 D:\\Zotero（留空则自动探测）" />
      </div>
      <div class="wz-note">连接会直接读取 Zotero 的 zotero.sqlite 里每一条文献（含没有 PDF 的纯题录），<b>不修改</b>你的 Zotero 数据。</div>
      <label class="sac-toggle" style="margin:6px 0"><input type="checkbox" id="wz-onlypdf" ${(WZ.detect && WZ.detect.import_only_pdf) ? "checked" : ""} />
        <span>只导入有 PDF 的文献（没有 PDF 的纯题录不进库；之后可在设置里改）</span></label>
      <div id="wz3c-msg"></div>
      <div class="wz-actions">
        <button class="ghost2c wz-back" id="wz3-back">← 上一步</button>
        <button class="primary" id="wz3-connect">连接文库</button>
      </div>`;
    $("#wz3-back").addEventListener("click", renderStep2);
    const _op = $("#wz-onlypdf");
    if (_op) _op.addEventListener("change", () => {
      jpost("/setup/import_only_pdf", { only_pdf: _op.checked }).catch(() => {});
      if (WZ.detect) WZ.detect.import_only_pdf = _op.checked;
    });
    $("#wz3-connect").addEventListener("click", async () => {
      const btn = $("#wz3-connect"); btn.disabled = true;
      $("#wz3c-msg").innerHTML = "";
      const zdir = $("#wz-zdir").value.trim();
      try {
        const r = await jpost("/setup/connect", zdir ? { zotero_dir: zdir } : {});
        WZ.connected = r; WZ.srcChoice = "zotero"; APP.source = "zotero";
        $("#wz3c-msg").innerHTML = `<div class="wz-result">✅ 已连接，共 ${num(r.entries)} 条文献</div>`;
        setTimeout(renderStep4, 600);
      } catch (e) {
        $("#wz3c-msg").innerHTML = `<div class="wz-err">连接失败：${esc(e.message)}</div>`;
        btn.disabled = false;
      }
    });
  }
  async function renderStep3Folder() {
    const nativePick = (typeof window !== "undefined" && window.pywebview !== undefined);
    // 建议默认目录：应用自己的数据目录旁 <HOME>/papers
    let def = WZ.folderDir || "";
    if (!def) { try { def = (await jget("/setup/folder_default")).default_dir || ""; } catch (e) {} }
    $("#wz-src-body").innerHTML =
      `<div class="wz-field">
        <label>知识库文件夹（PaperPiggy 会用它来放你的 PDF）</label>
        <div class="wz-folder-pick">
          <input id="wz-folder-dir" value="${esc(def)}" placeholder="如 D:\\我的论文库" />
          ${nativePick ? `<button class="ghost2c" id="wz-folder-browse">浏览…</button>` : ""}
        </div>
        <p class="wz-mini">默认就用应用自己的文件夹（上面这个路径）。点下面「📂 打开文件夹放 PDF」会创建并直接打开它，把论文拖进去即可。</p>
        <div class="wz-actions-inline">
          <button class="ghost2c" id="wz-folder-open">📂 打开文件夹放 PDF</button>
          <span id="wz-folder-cnt" class="wz-test-msg"></span>
        </div>
      </div>
      <div id="wz-meta-dep"></div>
      <div id="wz3f-msg"></div>
      <div class="wz-actions">
        <button class="ghost2c wz-back" id="wz3-back">← 上一步</button>
        <button class="primary" id="wz3f-connect">建立文件夹库</button>
      </div>`;
    $("#wz3-back").addEventListener("click", renderStep2);
    const browse = $("#wz-folder-browse");
    if (browse) browse.addEventListener("click", async () => {
      try {
        const r = await jpost("/setup/pick_folder", {});
        if (r && r.ok && r.dir) $("#wz-folder-dir").value = r.dir;
      } catch (e) {}
    });
    // 打开文件夹：先把这个目录设为受管文件夹（创建）+ 在系统里打开 + 显示 PDF 数
    $("#wz-folder-open").addEventListener("click", async () => {
      const dir = $("#wz-folder-dir").value.trim();
      try {
        if (dir) await jpost("/setup/folder", { folder_dir: dir });
        const r = await jpost("/setup/open_folder", {});
        const cnt = $("#wz-folder-cnt");
        if (cnt) cnt.textContent = "已打开文件夹，把 PDF 拖进去后回来点「建立文件夹库」。";
      } catch (e) { const cnt = $("#wz-folder-cnt"); if (cnt) cnt.textContent = "打开失败：" + e.message; }
    });
    $("#wz3f-connect").addEventListener("click", async () => {
      const dir = $("#wz-folder-dir").value.trim();
      if (!dir) { $("#wz3f-msg").innerHTML = `<div class="wz-err">请先选择或填写一个文件夹。</div>`; return; }
      const btn = $("#wz3f-connect"); btn.disabled = true;
      try {
        const r = await jpost("/setup/connect", { source: "folder", folder_dir: dir });
        WZ.srcChoice = "folder"; WZ.folderDir = dir; WZ.folderConnected = r;
        APP.source = "folder"; APP.folderDir = dir; APP.srcLoaded = true; applySourceCopy();
        const n = num(r.entries || 0);
        $("#wz3f-msg").innerHTML = `<div class="wz-result">✅ 已建立文件夹库，发现 ${n} 个 PDF${r.entries ? "" : "（空文件夹也没关系，稍后把 PDF 拖进窗口就会自动入库）"}</div>`;
        setTimeout(renderStep4, 700);
      } catch (e) {
        $("#wz3f-msg").innerHTML = `<div class="wz-err">建立失败：${esc(e.message)}</div>`;
        btn.disabled = false;
      }
    });
    renderMetaDep();
  }
  // 抽题录需 LLM Key 的三态引导
  function renderMetaDep() {
    const box = $("#wz-meta-dep"); if (!box) return;
    const hasKey = (WZ.backend === "api" && WZ.api.key) || (WZ.detect && WZ.detect.meta_ready) || APP.metaReady;
    if (hasKey) {
      box.innerHTML = `<div class="wz-note wz-note-ok">🤖 <b>题录抽取已就绪</b>：入库时会用你配置的 API Key，自动从 PDF 正文读出题名 / 作者 / 年份 / 期刊 / 摘要。</div>`;
      return;
    }
    box.innerHTML = `
      <div class="wz-note wz-note-warn">⚠️ <b>还差一步：配一个 AI 的 API Key</b><br>
        文件夹里的 PDF 没有题名、作者等信息，需要 AI 从正文里读出来。推荐用 <b>SiliconFlow（硅基流动）</b>，
        有免费模型、几分钟就能配好。不配也能入库，但文献只会显示文件名，检索和分类会大打折扣。</div>
      <div class="wz-field"><label>API Key（SiliconFlow）</label>
        <input id="wz-meta-key" type="password" placeholder="去 https://cloud.siliconflow.cn/account/ak 领免费 key" /></div>
      <div class="wz-actions-inline">
        <button class="ghost2c" id="wz-meta-save">保存 Key</button>
        <span id="wz-meta-msg" class="wz-test-msg"></span>
      </div>
      <p class="wz-mini">也可以先跳过、之后在「⚙ 设置」里补，或让 agent 代为补全题录。</p>`;
    $("#wz-meta-save").addEventListener("click", async () => {
      const k = $("#wz-meta-key").value.trim(), msg = $("#wz-meta-msg");
      if (!k) { msg.className = "wz-test-msg err"; msg.textContent = "请先填 Key"; return; }
      try {
        await jpost("/setup/backend", { backend: WZ.backend, key: k });  // 只存 key，不改检索后端
        WZ.api.key = k; if (WZ.detect) WZ.detect.meta_ready = true; APP.metaReady = true;
        renderMetaDep();
      } catch (e) { msg.className = "wz-test-msg err"; msg.textContent = "保存失败：" + esc(e.message); }
    });
  }

  // ── 第 4 步：准备检索引擎（仅本地模式需下载模型；API 模式直接就绪）──
  function renderStep4() {
    setStep(5);
    const readyHtml =
      `<div class="wz-note">检索引擎已就绪 ✓${WZ.backend === "api" ? "（API 模式，无需本地模型）" : "（本地模型已在）"}。可直接进入下一步。</div>
      <div class="wz-actions">
        <button class="ghost2c wz-back" id="wz4-back">← 上一步</button>
        <button class="primary" id="wz4-next">下一步：即时索引 →</button>
        
      </div>`;
    $("#wizard-body").innerHTML = `<div id="wz4-inner"><div class="wz-note">正在检查检索引擎…</div></div>`;

    const bindReady = () => {
      $("#wz4-inner").innerHTML = readyHtml;
      { const _sk=$("#wz-skip"); if (_sk) _sk.addEventListener("click", closeWizard); }
      $("#wz4-back").addEventListener("click", renderStep3);
      $("#wz4-next").addEventListener("click", renderStep5);
    };

    // API 模式无需本地模型，直接就绪
    if (WZ.backend === "api") { bindReady(); return; }

    // 本地模式：查模型是否就位
    (async () => {
      let st = null;
      try { st = await jget("/setup/models_status"); } catch (e) { st = null; }
      if (st && st.present) { bindReady(); return; }
      const missing = (st && st.missing) || [];
      $("#wz4-inner").innerHTML =
        `<div class="wz-note">本地模式需下载约 <b>1.2GB</b> 模型（仅此一次），下载后全程离线、隐私不出本机。
          ${missing.length ? `<br><span class="wz-subtle">待下载：${esc(missing.join("、"))}</span>` : ""}</div>
        <div id="wz4-prog" class="wz-dl" hidden>
          <div class="wz-dl-head"><span id="wz4-dl-name">准备中…</span><span id="wz4-dl-pct">0%</span></div>
          <div class="hbar wz-dl-bar"><span class="track"><span id="wz4-dl-fill" class="fill" style="width:0%;background:var(--accent)"></span></span></div>
          <div id="wz4-dl-sub" class="wz-dl-sub"></div>
        </div>
        <div id="wz4-msg"></div>
        <div class="wz-actions">
          <button class="ghost2c wz-back" id="wz4-back">← 上一步</button>
          <button class="primary green" id="wz4-dl">开始下载</button>
          
        </div>`;
      { const _sk=$("#wz-skip"); if (_sk) _sk.addEventListener("click", closeWizard); }
      $("#wz4-back").addEventListener("click", renderStep3);
      $("#wz4-dl").addEventListener("click", () => downloadModels());
    })();
  }

  // 本地模型下载：POST + SSE（fetch 流式读取 text/event-stream）
  const PHASE_TXT = { download: "下载", verify: "校验", extract: "解压" };
  async function downloadModels() {
    const btn = $("#wz4-dl"); if (btn) { btn.disabled = true; btn.textContent = "下载中…"; }
    const prog = $("#wz4-prog"); if (prog) prog.hidden = false;
    $("#wz4-msg").innerHTML = "";
    const nameEl = $("#wz4-dl-name"), pctEl = $("#wz4-dl-pct"), fillEl = $("#wz4-dl-fill"), subEl = $("#wz4-dl-sub");
    const mb = (b) => (Number(b || 0) / (1024 * 1024)).toFixed(1);

    const applyProgress = (j) => {
      const total = Number(j.total) || 0, dl = Number(j.done) || 0;
      const pct = total > 0 ? Math.max(0, Math.min(100, Math.round((dl / total) * 100))) : 0;
      const phase = PHASE_TXT[j.phase] || j.phase || "下载";
      if (nameEl) nameEl.textContent = `${phase}：${esc(j.name || "")}`;
      if (pctEl) pctEl.textContent = pct + "%";
      if (fillEl) fillEl.style.width = pct + "%";
      if (subEl) subEl.textContent = total > 0 ? `${mb(dl)} / ${mb(total)} MB` : `${mb(dl)} MB`;
    };

    const done = (ok, msg) => {
      if (ok) {
        $("#wz4-msg").innerHTML = `<div class="wz-result">下载完成 ✓</div>`;
        // 就绪：把「开始下载」换成「下一步」
        const actions = btn ? btn.parentNode : null;
        if (actions) actions.innerHTML = `<button class="primary" id="wz4-next">下一步：即时索引 →</button>`;
        $("#wz4-next").addEventListener("click", renderStep5);
        { const _sk=$("#wz-skip"); if (_sk) _sk.addEventListener("click", closeWizard); }
        if (WZ.detect) { WZ.detect.models_local = true; WZ.detect.reranker_local = true; WZ.detect.models_ready = true; }
      } else {
        $("#wz4-msg").innerHTML = `<div class="wz-err">下载失败：${esc(msg || "未知错误")}</div>`;
        if (btn) { btn.disabled = false; btn.textContent = "重试下载"; }
      }
    };

    try {
      const resp = await fetch("/setup/download_models", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      if (!resp.ok || !resp.body) { done(false, "HTTP " + resp.status); return; }
      const reader = resp.body.getReader(); const dec = new TextDecoder();
      let buf = "", finished = false;
      while (true) {
        const { value, done: rdDone } = await reader.read();
        if (rdDone) break;
        buf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const frame = buf.slice(0, idx); buf = buf.slice(idx + 2);
          // 取该帧内以 data: 开头的行；忽略 : keepalive 心跳与其它字段
          const dataLine = frame.split("\n").find((l) => l.startsWith("data:"));
          if (!dataLine) continue;
          const data = dataLine.slice(5).trim();
          if (data === "[DONE]") { finished = true; continue; }
          let j; try { j = JSON.parse(data); } catch (e) { continue; }
          if (j.final) { finished = true; done(!!j.ok, j.msg); }
          else applyProgress(j);
        }
      }
      // 流结束但没收到 final：视为异常（除非已明确 [DONE] 且此前 final 已处理）
      if (!finished) done(false, "连接中断，请重试");
    } catch (e) {
      done(false, e.message);
    }
  }

  // ── 第 5 步：即时索引（词法层）──
  function renderStep5() {
    setStep(6);
    const isFolder = WZ.srcChoice === "folder";
    const entries = WZ.connected ? WZ.connected.entries : (WZ.folderConnected ? WZ.folderConnected.entries : null);
    const emptyFolder = isFolder && (entries === 0);
    const note = isFolder
      ? (emptyFolder
          ? `<div class="wz-note">文件夹还是空的——没关系，进去后把 PDF 拖进窗口就会自动入库。也可以先放好 PDF 再点下面。</div>`
          : `<div class="wz-note">下一步会<b>逐篇读出每个 PDF 的题录</b>（题名/作者/年份/期刊），再建索引。
              比 Zotero 慢一些（每篇要让 AI 读一次），${entries != null ? `约 <b>${num(entries)}</b> 篇，` : ""}可以放着，完成后自动可搜。</div>`)
      : `<div class="wz-note">下一步做<b>即时索引</b>（词法层）：把每篇的「标题+摘要+关键词」建成可搜索索引。
          <b>0 等待、秒级完成</b>，完成后检索框和库总览立刻可用。${entries != null ? ` 待索引约 <b>${num(entries)}</b> 篇。` : ""}</div>`;
    const goLabel = emptyFolder ? "进入知识库 →" : (isFolder ? "开始建库（读取题录）" : "开始即时索引");
    $("#wizard-body").innerHTML =
      note +
      `<div id="wz5-msg"></div>
      <div class="wz-actions">
        <button class="ghost2c wz-back" id="wz5-back">← 上一步</button>
        <button class="primary green" id="wz5-go">${goLabel}</button>
        
      </div>`;
    { const _sk=$("#wz-skip"); if (_sk) _sk.addEventListener("click", closeWizard); }
    $("#wz5-back").addEventListener("click", renderStep4);
    $("#wz5-go").addEventListener("click", async () => {
      const btn = $("#wz5-go");
      if (emptyFolder) { closeWizard(); poll(); maybeShowDropzone(); switchTab("browse"); return; }
      btn.disabled = true; btn.innerHTML = `<span class="wz-spin"></span>正在建库…`;
      $("#wz5-msg").innerHTML = "";
      try {
        if (isFolder) {
          // 文件夹模式：后台建库（含逐篇 LLM 抽题录），轮询进度；无 key 时用 _nokey 端点退文件名
          const ep = APP.metaReady ? "/index/folder_build" : "/index/folder_build_nokey";
          const r = await jpost(ep, {});
          if (r && r.need_key && !APP.metaReady) {
            // 无 key：也可建（退文件名）——直接走 nokey
            await jpost("/index/folder_build_nokey", {});
          }
          $("#wz5-msg").innerHTML = `<div class="wz-result">🚀 已开始建库，正在逐篇读取题录（进度见顶部）。可直接进入，完成后自动可搜。</div>`;
          btn.outerHTML = `<button class="primary" id="wz5-enter">进入知识库 →</button>`;
          $("#wz5-enter").addEventListener("click", () => { closeWizard(); poll(); });
          poll();
          return;
        }
        const r = await jpost("/index/light", {});
        const papers = r.meta_indexed != null ? r.meta_indexed : (r.total || 0);
        const wp = r.with_pdf || 0;
        jpost("/index/semantic", {}).catch((e) => reportErr(e && e.message, "wizard auto-semantic"));
        $("#wz5-msg").innerHTML = `<div class="wz-result">🎉 已索引 ${num(papers)} 篇（其中 ${num(wp)} 篇有 PDF 可深索）<br><span class="wz-subtle">语义层已在后台自动提质，可直接进入使用。</span></div>`;
        btn.outerHTML = `<button class="primary" id="wz5-enter">进入知识库 →</button>`;
        $("#wz5-enter").addEventListener("click", () => { closeWizard(); poll(); maybeDeepInvite(); });
      } catch (e) {
        $("#wz5-msg").innerHTML = `<div class="wz-err">建库失败：${esc(e.message)}</div>`;
        btn.disabled = false; btn.textContent = "重试";
      }
    });
  }

  // 主界面里的深索邀请卡（检索结果区顶部）
  // 触发前提：语义层已就绪（mode=full）且仍有未深索的 PDF。可用 force 绕过「以后再说」（供设置里再触发）。
  async function maybeDeepInvite(force) {
    if ($("#deep-invite")) return;
    if (!force && localStorage.getItem("localkb.deepDismissed") === "1") return;
    // 取最新状态（poll 可能还没跑过一轮）
    let st = lastIdxStatus;
    if (!st) { try { st = await jget("/index/status"); lastIdxStatus = st; } catch (e) { return; } }
    if (st.mode !== "full") return;                       // 语义层未就绪不弹
    const withPdf = st.with_pdf || 0, deep = st.deep_done || 0;
    if (!(withPdf > 0 && deep < withPdf)) return;          // 已全部深索完，无需弹
    renderDeepInvite(deep, withPdf, st.building && st.stage === "deep");
  }
  function renderDeepInvite(deep, withPdf, alreadyBusy) {
    if ($("#deep-invite")) return;
    const card = document.createElement("div");
    card.id = "deep-invite";
    card.className = "deep-invite";
    card.innerHTML =
      `<div class="di-ic">📄</div>
      <div class="di-txt"><b>深度索引让回答精确到页码</b>
        <span>已深索 <b>${num(deep)}</b>/<b>${num(withPdf)}</b> 篇有 PDF 的文献。把剩余文献全文切块并向量化，可后台进行。</span></div>
      <div class="di-btns"><button class="go">深索全库</button><button class="later">以后再说</button></div>`;
    $("#results").parentNode.insertBefore(card, $("#results"));
    card.querySelector(".later").addEventListener("click", () => {
      localStorage.setItem("localkb.deepDismissed", "1"); card.remove();
    });
    const startDeep = async () => {
      const b = card.querySelector(".go"); b.disabled = true;
      const later = card.querySelector(".later"); if (later) later.style.display = "none";
      try {
        await jpost("/index/deep", { scope: "all" });
        b.textContent = "后台深索中…（进度见顶部）";
        poll();  // 立刻拉一次，让顶部进度条接管显示
      } catch (e) { b.textContent = "启动失败：" + e.message; b.disabled = false; if (later) later.style.display = ""; }
    };
    card.querySelector(".go").addEventListener("click", startDeep);
    if (alreadyBusy) {  // 已在深索：直接显示后台态
      const b = card.querySelector(".go"); b.disabled = true; b.textContent = "后台深索中…（进度见顶部）";
      const later = card.querySelector(".later"); if (later) later.style.display = "none";
    }
  }
  // 兼容旧调用名
  function showDeepInvite() { maybeDeepInvite(); }

  async function maybeWizard() {
    try {
      const d = await jget("/setup/detect");
      WZ.detect = d;
      // 顺手写入数据源全局态（ensureSource 命中缓存），并初始化向导来源默认
      APP.source = d.source === "folder" ? "folder" : "zotero";
      APP.folderDir = d.folder_dir || ""; APP.metaReady = !!d.meta_ready; APP.srcLoaded = true;
      WZ.srcChoice = d.zotero_detected ? "zotero" : "folder";
      applySourceCopy();
      // 用后端当前后端选择初始化向导态（默认 local）
      if (d.backend === "api" || d.backend === "local") WZ.backend = d.backend;
      if (!d.indexed) { $("#wizard").hidden = false; renderStep1(); }
      else { maybeDeepInvite(); maybeShowDropzone(); }  // 老用户回访：深索提示 + 文件夹空库 dropzone
    } catch (e) { /* 后端不可达时不弹向导，让状态 pill 提示 */ }
  }
  // ══════════════════════════════════════════
  //  文件夹模式：拖入 / 选择 PDF 入库
  // ══════════════════════════════════════════
  function openMetaKeyHelp() {
    openSettings();
    setTimeout(() => { const e = $("#settings-modal .set-section"); if (e) e.scrollIntoView({ behavior: "smooth" }); }, 100);
  }
  function fileToB64(file) {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(String(r.result || "").split(",").pop());
      r.onerror = reject;
      r.readAsDataURL(file);
    });
  }
  function showIngestPanel(n) {
    $("#ingest-modal").hidden = false;
    $("#ingest-sub").textContent = APP.metaReady
      ? `正在入库 ${num(n)} 篇（复制 + 用 AI 抽取题录中…）`
      : `⚠️ 还没配 AI Key，这次只能按文件名入库。配好之后新入库的文献会自动补全题名/作者/年份。`;
    $("#ingest-fill").style.width = "8%";
    $("#ingest-detail").textContent = "";
    $("#ingest-result").innerHTML = "";
    $("#ingest-close").hidden = true;
  }
  function finishIngest(r) {
    $("#ingest-fill").style.width = "100%";
    const failed = r.failed || [];
    let html = `<div class="wz-result">✅ 新入库 ${num(r.added || 0)} 篇${r.skipped ? `（跳过重复 ${num(r.skipped)} 篇）` : ""}</div>`;
    if (failed.length) html += `<div class="wz-err">⚠️ ${num(failed.length)} 篇未入库：` + failed.map((f) => `${esc(f.name)}（${esc(f.reason)}）`).join("、") + `</div>`;
    if (r.need_key) html += `<div class="ed-nokey">题录用文件名占位——<a class="ag-link" id="ing-gokey">去配 AI Key</a> 后「更新知识库」可自动补全。</div>`;
    $("#ingest-result").innerHTML = html;
    $("#ingest-detail").textContent = r.building ? "已在后台建索引/抽题录，完成后自动可搜（进度见顶部）。" : "";
    $("#ingest-close").hidden = false;
    const g = $("#ing-gokey"); if (g) g.addEventListener("click", openMetaKeyHelp);
    // 收尾刷新
    browseLoaded = false; if (!$("#panel-browse").hidden) loadBrowse();
    if (dashLoaded) loadDashboard("silent");
    poll();
  }
  async function ingestFiles(fileList) {
    const files = Array.from(fileList || []).filter((f) => /\.pdf$/i.test(f.name));
    if (!files.length) { toast("只支持拖入 PDF 文件"); return; }
    showIngestPanel(files.length);
    try {
      const payload = { files: [] };
      for (let i = 0; i < files.length; i++) {
        payload.files.push({ name: files[i].name, content_b64: await fileToB64(files[i]) });
        $("#ingest-fill").style.width = Math.round(8 + (i + 1) / files.length * 40) + "%";
        $("#ingest-detail").textContent = `读取文件 ${i + 1}/${files.length}…`;
      }
      $("#ingest-detail").textContent = "上传并处理中…";
      const r = await jpost("/ingest/files", payload);
      finishIngest(r);
    } catch (e) {
      $("#ingest-result").innerHTML = `<div class="wz-err">入库失败：${esc(e.message)}</div>`;
      $("#ingest-close").hidden = false;
    }
  }
  function initDragIngest() {
    let dragDepth = 0;
    const overlay = $("#drop-overlay");
    const isFileDrag = (e) => Array.from((e.dataTransfer && e.dataTransfer.types) || []).includes("Files");
    window.addEventListener("dragenter", (e) => {
      if (APP.source !== "folder" || !isFileDrag(e)) return;
      e.preventDefault(); dragDepth++; if (overlay) overlay.hidden = false;
    });
    window.addEventListener("dragover", (e) => {
      if (APP.source !== "folder" || !isFileDrag(e)) return;
      e.preventDefault(); e.dataTransfer.dropEffect = "copy";   // 必须 preventDefault，否则浏览器会打开文件
    });
    window.addEventListener("dragleave", (e) => {
      if (APP.source !== "folder") return;
      if (--dragDepth <= 0) { dragDepth = 0; if (overlay) overlay.hidden = true; }
    });
    window.addEventListener("drop", (e) => {
      if (APP.source !== "folder") return;
      e.preventDefault(); dragDepth = 0; if (overlay) overlay.hidden = true;
      ingestFiles((e.dataTransfer && e.dataTransfer.files) || []);
    });
    // 文件选择器兜底（拖拽不生效时 100% 可用）
    const inp = $("#ingest-file-input");
    if (inp) inp.addEventListener("change", (e) => { ingestFiles(e.target.files); e.target.value = ""; });
    const edPick = $("#ed-pick"); if (edPick) edPick.addEventListener("click", () => inp && inp.click());
    const edKey = $("#ed-gokey"); if (edKey) edKey.addEventListener("click", openMetaKeyHelp);
    const ic = $("#ingest-close"); if (ic) ic.addEventListener("click", () => ($("#ingest-modal").hidden = true));
  }
  // 文件夹模式且空库：显示大号 dropzone（浏览列表顶部）
  async function maybeShowDropzone() {
    const dz = $("#empty-dropzone"); if (!dz) return;
    if (APP.source !== "folder") { dz.hidden = true; return; }
    let papers = 0;
    try { const h = await jget("/health"); papers = h.papers != null ? h.papers : (h.n || 0); } catch (e) {}
    dz.hidden = papers > 0;
    const nk = $("#ed-nokey"); if (nk) nk.hidden = !!APP.metaReady;
  }

  ensureSource().then(() => { initDragIngest(); maybeShowDropzone(); });
  maybeWizard();
  // 冷启动：默认页＝库总览，主动加载一次（懒加载此前只在点 tab 时触发；loadDashboard 只 GET /stats，无副作用）
  if (!dashLoaded) loadDashboard("loud");
})();

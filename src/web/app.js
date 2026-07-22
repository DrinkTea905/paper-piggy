// 本地知识库 · 前端逻辑（原生 JS，零依赖，离线）
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => document.querySelectorAll(s);

  // 应用内对话框：替代浏览器原生确认/提示框（原生框会突兀地显示本地服务地址）。
  // 多处异步任务可能同时报错，所以统一排队，避免后一条覆盖前一条；队列清空后恢复原焦点。
  const _uiDialogQueue = [];
  let _uiDialogActive = false, _uiDialogRestore = null;

  function _drainUiDialogs() {
    if (_uiDialogActive || !_uiDialogQueue.length) return;
    _uiDialogActive = true;
    const task = _uiDialogQueue.shift();
    const { message, opts, notice, resolve } = task;
    const m = $("#confirm-modal"), ok = $("#confirm-ok"), cancel = $("#confirm-cancel");
    if (!m || !ok || !cancel) {
      _uiDialogActive = false;
      resolve(notice);
      queueMicrotask(_drainUiDialogs);
      return;
    }
    $("#confirm-title").textContent = opts.title || (notice ? "提示" : "请确认");
    $("#confirm-msg").textContent = message || "";
    ok.textContent = opts.okText || "确定";
    cancel.textContent = opts.cancelText || "取消";
    ok.className = opts.danger ? "danger-btn" : "";
    cancel.hidden = notice;
    m.setAttribute("role", "dialog");
    m.setAttribute("aria-modal", "true");
    m.hidden = false;
    let finished = false;
    const done = (v) => {
      if (finished) return;
      finished = true;
      m.hidden = true;
      cancel.hidden = false;
      ok.className = "";
      ok.removeEventListener("click", onOk);
      cancel.removeEventListener("click", onCancel);
      m.removeEventListener("mousedown", onBackdrop);
      document.removeEventListener("keydown", onKey, true);
      _uiDialogActive = false;
      resolve(v);
      if (_uiDialogQueue.length) {
        queueMicrotask(_drainUiDialogs);
      } else {
        const target = _uiDialogRestore;
        _uiDialogRestore = null;
        if (target && target.isConnected && !target.disabled) setTimeout(() => target.focus(), 0);
      }
    };
    const onOk = () => done(true);
    const onCancel = () => done(false);
    const onBackdrop = (e) => { if (e.target === m) done(notice); };
    const onKey = (e) => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done(notice); }
      else if (e.key === "Enter") {
        e.preventDefault(); e.stopPropagation();
        if (notice) done(true);
        else {
          const ae = document.activeElement;
          done(ae === cancel ? false : ae === ok ? true : !opts.danger);
        }
      } else if (e.key === "Tab") {
        const focusable = notice ? [ok] : [cancel, ok];
        const i = focusable.indexOf(document.activeElement);
        e.preventDefault();
        focusable[(i + (e.shiftKey ? -1 : 1) + focusable.length) % focusable.length].focus();
      }
    };
    ok.addEventListener("click", onOk);
    cancel.addEventListener("click", onCancel);
    m.addEventListener("mousedown", onBackdrop);
    document.addEventListener("keydown", onKey, true);
    // 危险操作默认聚焦「取消」，防止手快连敲 Enter 误删；普通提示只有确定按钮。
    setTimeout(() => (notice ? ok : opts.danger ? cancel : ok).focus(), 0);
  }

  function _uiDialog(message, opts, notice) {
    return new Promise((resolve) => {
      if (!_uiDialogActive && !_uiDialogQueue.length) _uiDialogRestore = document.activeElement;
      _uiDialogQueue.push({ message, opts: opts || {}, notice, resolve });
      _drainUiDialogs();
    });
  }

  function uiConfirm(message, opts = {}) { return _uiDialog(message, opts, false); }
  function uiNotice(message, opts = {}) { return _uiDialog(message, opts, true); }

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
  const hasFulltext = (x) => !!(x && (x.has_fulltext != null ? x.has_fulltext : x.has_pdf));
  const withFulltext = (x) => Number((x && (x.with_fulltext != null ? x.with_fulltext : x.with_pdf)) || 0);
  const noFulltext = (x) => Number((x && (x.no_fulltext != null ? x.no_fulltext : x.no_pdf)) || 0);
  const fulltextLabel = (x) => ({ pdf: "PDF", epub: "EPUB", docx: "DOCX", markdown: "Markdown", txt: "TXT" }[
    String((x && x.fulltext_format) || "").toLowerCase()
  ] || "全文");

  // 全局浮层提示（右下角，自动消失）——用于跨页通知（如深索完成），不依赖当前所在 tab
  let _toastTimer = null;
  function flashToast(msg) {
    let el = $("#ui-toast");
    if (!el) { el = document.createElement("div"); el.id = "ui-toast"; el.className = "ui-toast"; document.body.appendChild(el); }
    el.textContent = msg; el.classList.add("show");
    clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => el.classList.remove("show"), 4000);
  }

  // BF11：jget 也要读后端人话（detail/error/msg），之前只修了 jpost，jget 仍吞成「/path 500」
  async function jget(url) {
    const r = await fetch(url);
    if (!r.ok) {
      let j = null; try { j = await r.json(); } catch (e) {}
      throw new Error((j && (j.detail || j.error || j.msg)) || (url + " " + r.status));
    }
    return r.json();
  }
  async function jpost(url, body) {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    let j = null; try { j = await r.json(); } catch (e) {}
    // A7：后端把「人话」错误常放在 msg（如「请先完全退出 Zotero」）——一并读取，别再吞成「/path 400」
    if (!r.ok) throw new Error((j && (j.detail || j.error || j.msg)) || (url + " " + r.status));
    return j;
  }
  // BF12：PATCH/DELETE 等任意方法的统一封装——错误解析同 jpost，别再用裸 fetch 静默吞掉 4xx 原因
  async function jsend(url, method, body) {
    const r = await fetch(url, { method, headers: { "Content-Type": "application/json" },
      body: body != null ? JSON.stringify(body) : undefined });
    let j = null; try { j = await r.json(); } catch (e) {}
    if (!r.ok) throw new Error((j && (j.detail || j.error || j.msg)) || (url + " " + r.status));
    return j;
  }

  // R14：localStorage 可能被写坏，JSON.parse 无兜底会连锁抛错（连对话都发不出）。统一走 safeParse。
  const safeParse = (s, fb) => { try { const v = JSON.parse(s); return v == null ? fb : v; } catch (e) { return fb; } };

  // ── 设置存取（localStorage）──
  const cfg = () => safeParse(localStorage.getItem("localkb.cfg"), {});
  const saveCfg = (c) => localStorage.setItem("localkb.cfg", JSON.stringify(c));
  // K3（副本#4/#5）：已填 key 用掩码「••••••1234」显示，让用户一眼知道填过了；后端只回末4位，绝不回明文
  const maskKey = (last4) => (last4 ? "••••••" + String(last4) : "");

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
    if (sub) sub.textContent = "把一个文件夹里的全文文件变成能秒级检索、可视化、可对话的本地知识库。支持 PDF、EPUB、DOCX、Markdown、TXT，全程离线。";
    const bt = $("#btn-build");
    if (bt) bt.title = "加了新全文文件后点一次即在后台增量更新（也可直接拖进窗口即时入库）。想定时自动更新？到 ⚙ 设置里开。";
  }

  // ── 顶栏状态 pill（读 /health：mode=null|light|full）+ 常驻进度条（读 /index/status）──
  let lastIdxStatus = null;
  let wasDeepBusy = false;
  let wasBackfilling = false;   // 追踪「生成检索摘要」后台任务，结束时刷新列表/徽标
  let lastDeepDone = -1, lastExtractSig = "";   // 深索/提取状态变化 → 非破坏式刷新浏览徽标
  let lastRev = null;   // 库修订号（/health.rev，分域 lib/wiki/agent）：某域变了=该域有改动，静默刷当前可见页（连点都不用）
  async function poll() {
    const s = $("#status");
    let h = null, st = null;
    try { h = await jget("/health"); } catch (e) {}
    try { st = await jget("/index/status"); } catch (e) {}
    // BF14：/health 失败早退时清掉 wasDeepBusy，防止后端重启后误弹一次「深索完成」
    if (!h) { s.textContent = "服务未连接"; s.className = "status err"; hideProgress(); wasDeepBusy = false; return; }
    // 库修订感知（连点都不用）：库/综述/交付物任一变化 → 静默刷「真变了的那一域对应的可见页」，
    // 捕捉「外部 MCP agent 改库/写综述、纯新增入库」这类 /index/status 无信号、原本要手点🔄或重开才见的变化。
    // 分域比对：只刷真变了的那一域的可见页，避免跨域误刷（如浏览页看列表时 agent 写综述把列表刷跳）。
    const rev = h.rev;
    if (rev) {
      if (lastRev) {
        if (rev.lib !== lastRev.lib) {          // 入库 / 去重 → 库总览、浏览、Agent 深索卡
          if (dashLoaded && !$("#panel-dashboard").hidden) loadDashboard("silent");
          if (browseLoaded && !$("#panel-browse").hidden) refreshBrowse();
          if (agentLoaded && !$("#panel-agent").hidden) loadAgentDeep();
        }
        if (rev.wiki !== lastRev.wiki) {        // 综述写回（agent/用户）→ 综述库、库总览体检角标
          if (wikiLoaded && !$("#panel-wiki").hidden) loadWikiList("silent");
          if (dashLoaded && !$("#panel-dashboard").hidden) loadDashboard("silent");
        }
        if (rev.agent !== lastRev.agent) {      // 交付物 / 定时任务新增 → Agent 页两张卡
          if (agentLoaded && !$("#panel-agent").hidden) { loadAgentTasks(); loadAgentOutputs(); }
        }
      }
      lastRev = rev;
    }
    // 顶栏状态统一用「已深索 x/y 篇」口径（F47）：y=有PDF可深索的篇数，x=已深索数。
    // 全应用只保留「已深索/未深索」一套说法，去掉「词法就绪/全文就绪」等黑话。
    const papers = h.papers != null ? h.papers : h.n;
    const stage = st ? (st.stage || "") : "";
    if (h.building) {
      s.textContent = (stage === "deep") ? "深索中…" : (stage === "folder") ? "读取题录中…" : "索引中…";
      s.className = "status warn";
    }
    else if (!h.mode) { s.textContent = "未建库"; s.className = "status warn"; }
    else {
      const withPdf = withFulltext(st);
      const deep = st ? (st.deep_done || 0) : 0;
      if (withPdf > 0) {
        s.textContent = `已深索 ${num(deep)}/${num(withPdf)} 篇`;
        s.title = `共 ${num(papers)} 篇文献；${num(withPdf)} 篇有全文附件可深索，已深索 ${num(deep)} 篇（可精读并回溯原文位置）` + sacText(st);
      } else {
        s.textContent = `已索引 ${num(papers)} 篇`;
        s.title = `${num(papers)} 篇题录即时可搜（暂无全文附件可深索）`;
      }
      s.className = "status ok";
    }
    // 进度条：语义嵌入未完 / 深索进行中时常驻，完成自动消失
    if (st) {
      lastIdxStatus = st;
      updateProgress(st);
      // 深索从「进行中」跳到「结束」的那一刻，静默刷新一次库总览，让进度卡反映最新结果
      const deepBusyNow = st.building && st.stage === "deep";
      if (wasDeepBusy && !deepBusyNow) {
        if (dashLoaded) loadDashboard("silent");
        // B4：若正在「浏览」页且已加载，静默刷新列表与分类，否则卡片仍显示「未深索」误导重复触发
        if (browseLoaded && !$("#panel-browse").hidden) { loadPapers(); loadKbCats(); }
        if (agentLoaded && !$("#panel-agent").hidden) loadAgentDeep();   // Agent 页深索卡同步（与 dashboard/browse 对称）
        // BF14+：结束≠成功。**只有拿到 rc===0 的阳性证据才报完成**；rc 非0/未知(null)一律按「中断」。
        // （深索失败后 rc 会被紧接着的其它构建重置成 null/0——后端已加排空守卫防覆写，这里再兜一层：
        //   宁可提示用户去核验，也绝不谎报「已入库」。）
        if (st.cancelled) flashToast("已停止深索，已完成部分已保存。");
        else if (st.rc === 0) flashToast("✓ 深索完成，相关文献已可精读到页码。");
        else flashToast("⚠ 深索中断（原因见深索详情面板），已完成部分已保存。");
      }
      wasDeepBusy = deepBusyNow;
      // 深索或 PDF 提取分类变化时，非破坏式刷新浏览徽标。
      const _dn = st.deep_done || 0;
      const _extractSig = JSON.stringify(st.extract_status_counts || { legacy: st.deep_no_text || 0 });
      if (_dn !== lastDeepDone || _extractSig !== lastExtractSig) {
        if (browseLoaded && !$("#panel-browse").hidden) refreshBrowseDeepState();
        if (agentLoaded && !$("#panel-agent").hidden) loadAgentDeep();   // Agent 页深索/摘要计数同步
      }
      lastDeepDone = _dn; lastExtractSig = _extractSig;
      // 「生成检索摘要」（第②步）后台任务结束→刷新库总览与浏览列表，让 sac 数字/徽标更新
      const bfNow = !!(st.sac_backfill && st.sac_backfill.running);
      if (wasBackfilling && !bfNow) {
        if (dashLoaded) loadDashboard("silent");
        if (browseLoaded && !$("#panel-browse").hidden) { loadPapers(); loadKbCats(); }
        if (agentLoaded && !$("#panel-agent").hidden) loadAgentDeep();   // Agent 页检索摘要计数同步
        const bmsg = st.sac_backfill && st.sac_backfill.msg;
        if (bmsg) flashToast(bmsg);
      }
      wasBackfilling = bfNow;
    } else { hideProgress(); wasDeepBusy = false; wasBackfilling = false; }   // BF14：status 拉不到时清标记，恢复后不凭旧标记补发迟到 toast
  }
  poll(); setInterval(poll, 4000);

  // 启动遮罩看门狗：后端读取库目录与轻量句柄期间盖住尚不可用的主界面。
  // 盖住 #boot 直到 /health.loading 变 false（后端加载完 / 或本就空库快速返回），再淡出。每 1s 查一次（比 4s 主轮询灵敏）。
  function bootWatch() {
    const el = document.getElementById("boot"); if (!el) return;
    const t0 = Date.now();
    let serverSeen = false;   // /health 至少应答过一次 = 已连上后端
    // 秒数计时独立走客户端 setInterval；即使模型初始化期间 /health 暂时未应答，计时也不会停住。
    const timer = setInterval(() => {
      const sec = Math.round((Date.now() - t0) / 1000);
      const msg = document.getElementById("boot-msg");
      if (msg) msg.textContent = serverSeen
        ? `正在读取知识库目录… 已 ${sec}s`
        : `正在连接本地服务… 已 ${sec}s`;
    }, 1000);
    const done = () => { clearInterval(timer); el.classList.add("gone"); setTimeout(() => { el.hidden = true; }, 500); };
    const tick = async () => {   // 只负责探测「加载完没」，与上面的计时解耦
      let h = null;
      try { h = await jget("/health"); } catch (e) {}
      if (h) serverSeen = true;
      if (h && !h.loading) { done(); return; }   // 加载完（loading=false，含未建库/空库快速返回）→ 撤遮罩
      setTimeout(tick, 1000);
    };
    tick();
  }
  bootWatch();

  // 启动时静默检查新版本（默认开，可在 设置→应用→应用更新 关闭）。延后 2.5s 让首屏先加载完；
  // 服务端 10 分钟缓存 + updater 自带重试；失败静默（不弹错），只在有新版时点亮右上角徽标。
  if (localStorage.getItem("localkb.autoUpdateCheck") !== "0") {
    setTimeout(() => { try { checkUpdate(true); } catch (e) {} }, 2500);
  }

  // ── 深索摘要（SAC）覆盖：在每个「深索进度」旁并显「其中 M 篇有检索摘要」+ 补生成入口 ──
  // st 需含 deep_done / sac_done / sac_invalid / sac_missing / sac_backfill。
  // 异常摘要不计完成；只在用户点修复/补生成时才重写并重嵌入。
  function sacFrag(st) {
    if (!st) return "";
    const deep = st.deep_done || 0, sac = st.sac_done || 0;
    const invalid = st.sac_invalid || 0, missing = st.sac_missing == null ? Math.max(0, deep - sac - invalid) : st.sac_missing;
    if (deep <= 0) return "";
    const bf = st.sac_backfill || {};
    const gap = Math.max(0, deep - sac);
    let s = ` · <b>② 检索摘要</b>：已配 <b>${num(sac)}</b>/${num(deep)} 篇`;
    if (bf.running) {
      s += ` <span class="sac-bf-run">🧬 ${esc(bf.phase || "生成中")} ${num(bf.done || 0)}/${num(bf.total || 0)}…</span>`;
    } else if (gap > 0) {
      const detail = [invalid ? `${num(invalid)} 篇摘要异常` : "", missing ? `${num(missing)} 篇缺失` : ""].filter(Boolean).join("，");
      s += ` <span class="sac-gap">（${detail}，不计完成）</span>`
        + ` <a class="sac-bf-btn" data-act="sac-backfill" role="button" tabindex="0" title="第②步：修复异常摘要、补生成缺失摘要并重嵌入。需 API key，只处理这些篇，可后台跑。">🧬 修复 / 补生成摘要</a>`;
    } else {
      s += ` ✓`;
    }
    return s;
  }
  // 纯文本版（供顶栏 tooltip 等不能放 HTML 的地方）
  function sacText(st) {
    if (!st) return "";
    const deep = st.deep_done || 0, sac = st.sac_done || 0, invalid = st.sac_invalid || 0;
    if (deep <= 0) return "";
    const gap = Math.max(0, deep - sac);
    return `；② 检索摘要有效 ${num(sac)}/${num(deep)} 篇`
      + (invalid > 0 ? `，${num(invalid)} 篇异常` : "")
      + (gap - invalid > 0 ? `，${num(gap - invalid)} 篇缺失` : "");
  }
  let _sacBfBusy = false;
  async function startSacBackfill() {
    if (_sacBfBusy) return;
    const ok = await uiConfirm(
      "将修复质量检查未通过的摘要，并为缺失摘要的已深索文献补生成摘要，再重新嵌入这些篇（摘要只有重嵌入后才对检索生效）。用你配的 API key 生成；只处理这些篇，其它文献不受影响；可放后台跑。",
      { title: "修复 / 补生成检索摘要？", okText: "开始处理" });
    if (!ok) return;
    _sacBfBusy = true;
    try {
      const r = await jpost("/index/sac_backfill", {});
      if (r && r.ok === false) {
        flashToast(r.msg || (r.need_key ? "需要先配 API key。" : r.busy ? "已有任务在跑，稍后再试。" : "无法开始补生成。"));
      } else if (r && r.started === false) {
        flashToast(r.msg || "无需补生成。");
      } else {
        flashToast(`已开始为 ${num((r && r.total) || 0)} 篇生成检索摘要（第②步），进度见「检索摘要」。`);
        poll();
      }
    } catch (e) { flashToast("启动生成失败：" + (e.message || e)); }
    finally { _sacBfBusy = false; }
  }
  // 事件委托：任意「🧬 生成检索摘要」入口（多处渲染，用委托免去逐处重复绑定）
  document.addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest('[data-act="sac-backfill"]');
    if (b) { e.preventDefault(); startSacBackfill(); }
  });
  // 🔄 手动刷新（委托）：库总览 / 综述库 / 浏览页的整体刷新按钮 —— 不用重开应用即可看到后台或外部 agent 的改动。
  //   （深索/SAC 进度已由 4s 轮询自动走字；这里给「轮询看不见」的变化——纯新增入库、agent 写综述——一个手动出口。）
  document.addEventListener("click", async (e) => {
    const b = e.target.closest && e.target.closest('[data-act^="refresh-"]');
    if (!b) return;
    e.preventDefault();
    if (b.dataset.busy) return;
    const fn = { "refresh-dash": () => loadDashboard("silent"),
                 "refresh-wiki": () => loadWikiList("silent"),
                 "refresh-browse": () => refreshBrowse() }[b.dataset.act];
    if (!fn) return;
    b.dataset.busy = "1"; const lbl = b.textContent; b.textContent = "刷新中…";
    try { await fn(); b.textContent = "已刷新 ✓"; }
    catch (_) { b.textContent = "刷新失败"; }
    setTimeout(() => { b.textContent = lbl; delete b.dataset.busy; }, 1200);
  });

  // 只读查看某篇的检索摘要（点卡上「🧬 摘要有效」）
  async function openSummaryView(key, title) {
    const m = $("#summary-modal"); if (!m) return;
    $("#summary-title").textContent = title || "检索摘要";
    $("#summary-body").textContent = "读取中…";
    m.hidden = false;
    try {
      const r = await jget("/summary?key=" + encodeURIComponent(key));
      $("#summary-body").textContent = (r && r.has_summary) ? r.summary
        : (r && r.summary_invalid ? `（这篇摘要未通过质量检查：${r.summary_error || "内容异常"}。请在卡上点「🟠 摘要异常」修复。）`
          : "（这篇还没有检索摘要——可在卡上点「⚪ 无摘要」为它生成）");
    } catch (e) { $("#summary-body").textContent = "读取失败：" + (e.message || e); }
  }
  { // 摘要弹窗关闭：按钮 / 点遮罩 / Esc
    const m = $("#summary-modal");
    const close = () => { if (m) m.hidden = true; };
    const cb = $("#summary-close"); if (cb) cb.addEventListener("click", close);
    if (m) m.addEventListener("mousedown", (e) => { if (e.target === m) close(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && m && !m.hidden) close(); });
  }
  // 为某一篇生成检索摘要（点卡上「⚪ 无摘要」）——第②步的单篇版
  async function genSummaryOne(key, badge) {
    const repairing = !!(badge && badge.classList.contains("invalid"));
    const ok = await uiConfirm(
      `${repairing ? "重新生成" : "生成"}这篇的 AI 检索摘要（知识库建设第②步）：用你的 API key 生成 ~150 字摘要并重嵌入这一篇，让语义检索更容易命中它。可放后台跑。`,
      { title: repairing ? "修复这篇的异常摘要？" : "为这篇生成检索摘要？", okText: repairing ? "修复" : "生成" });
    if (!ok) return;
    const original = badge ? badge.textContent : "⚪ 无摘要";
    const restore = () => { if (badge) { badge.textContent = original; badge.style.pointerEvents = ""; } };
    if (badge) { badge.textContent = "⏳ 生成中…"; badge.style.pointerEvents = "none"; }
    try {
      const r = await jpost("/index/sac_backfill", { keys: [key] });
      if (r && r.ok === false) {
        flashToast(r.msg || (r.need_key ? "需要先配 API key。" : "已有任务在跑，稍后再试。")); restore();
      } else if (r && r.started === false) {
        flashToast(r.msg || "这篇已有摘要。"); restore();
      } else {
        flashToast("已开始为这篇生成检索摘要，完成后自动刷新。"); poll();
      }
    } catch (e) { flashToast("启动失败：" + (e.message || e)); restore(); }
  }

  // ── S 档语义 / F 档深索 进度条 ──
  function hideProgress() { $("#idx-progress").hidden = true; }
  // F7：用近一段进度速率估算剩余时间（约剩 N 分）。key 用于区分深索/语义两条进度，切换时重置。
  let _eta = { key: "", done: 0, t: 0 };
  function estimateEta(key, done, total) {
    const now = Date.now();
    if (_eta.key !== key) { _eta = { key, done, t: now }; return ""; }
    const dDone = done - _eta.done, dt = (now - _eta.t) / 1000;
    if (dt < 8) return "";                       // 采样窗口太短，先不显示，避免抖动
    const rate = dDone / dt;                      // 篇/秒
    _eta = { key, done, t: now };
    if (rate <= 0) return "";
    const remain = Math.max(0, total - done);
    const sec = remain / rate;
    if (sec < 45) return "约剩不到 1 分钟";
    const min = Math.round(sec / 60);
    return min > 90 ? "约剩 " + Math.round(min / 60) + " 小时" : "约剩 " + min + " 分钟";
  }
  function extractCounts(st) {
    const c = (st && st.extract_status_counts) || {};
    const hasStructured = Object.keys(c).length > 0;
    const missing = (c.missing_pdf || 0) + (c.missing_file || 0);
    const invalid = (c.invalid_pdf || 0) + (c.invalid_file || 0);
    const failed = c.ocr_failed || 0, pending = c.ocr_pending || 0;
    return { missing, invalid, failed, pending,
      blocked: hasStructured ? missing + invalid + failed : ((st && st.deep_no_text) || 0) };
  }
  function extractCountsText(st) {
    const x = extractCounts(st), bits = [];
    if (x.pending) bits.push(`${num(x.pending)} 篇待/正在本地 OCR`);
    if (x.missing) bits.push(`${num(x.missing)} 篇附件缺失`);
    if (x.invalid) bits.push(`${num(x.invalid)} 篇全文附件无法读取`);
    if (x.failed) bits.push(`${num(x.failed)} 篇 OCR 未识别`);
    return bits.join("，");
  }
  function extractState(r) { return ((r && r.extract_status) || {}).status || ""; }
  function extractBlocked(r) {
    const s = extractState(r);
    return r && (r.no_text || s === "missing_pdf" || s === "invalid_pdf" || s === "missing_file" || s === "invalid_file" || s === "ocr_failed");
  }
  function updateProgress(st) {
    const bar = $("#idx-progress");
    const papers = st.papers || 0, meta = st.meta_done || 0;
    const withPdf = withFulltext(st), deep = st.deep_done || 0;
    const stage = st.stage || "";
    // F4：仅 building 且 stage==="deep" 才算「深索中」（增量更新 all / 队列空时不误报）
    const deepBusy = st.building && stage === "deep";
    const semanticPending = papers > 0 && meta < papers;
    const qPending = st.queue_pending || 0;   // F10：自动深索队列积压（加入分类触发）
    let done, total, txt;
    // F4：只在 stage==="deep" 时才叫「深索中」——增量更新(stage=all)/队列空闲时不再误报「深索中 0%」
    if (deepBusy) {
      // 深索（把有全文附件的文献拆成可检索的小段）
      const blocked = extractCounts(st).blocked;
      if (withPdf > 0 && (deep + blocked) >= withPdf) {
        // 可处理正文已嵌完，其余是明确的附件/OCR失败终态；整库深索还有最后一步：
        // 重建 bm25 检索索引 + 印刷页码映射（20 多万块，分词+建索引要好几分钟）。
        // 这期间深索计数已到顶，若仍显示「深索中 1388/1398 · 99%」会让人误以为卡死——
        // 明确告诉用户在收尾（用户 2026-07-15 反馈「卡在 99% 不动」，实为此阶段）。
        done = 1; total = 1;
        txt = `深索已完成，正在重建检索索引（收尾中，请稍候）`;
      } else {
        done = deep; total = withPdf || 1;
        const eta = estimateEta("deep", deep, withPdf || 0);
        txt = `深索中… ${num(deep)}/${num(withPdf)}` + (qPending ? ` · 另 ${num(qPending)} 篇排队` : "") + (eta ? ` · ${eta}` : "");
      }
    } else if (semanticPending && st.building) {
      // 语义层提质（**正在跑**才画动进度条）
      done = meta; total = papers || 1;
      txt = `正在提升检索质量… ${num(meta)}/${num(papers)}`;
    } else if (semanticPending) {
      // meta<papers 但没有构建在跑 = 上次语义嵌入中途崩了/被中断，进度冻结在此。
      // 不再画会动的假进度条（否则永远卡着谎报「正在提升」）；给静态诚实提示 + 续跑入口。
      const pct = Math.max(0, Math.min(100, Math.round((meta / (papers || 1)) * 100)));
      $("#idx-progress-fill").style.width = pct + "%";
      $("#idx-progress-txt").textContent =
        `检索质量待建完 ${num(meta)}/${num(papers)}（上次未完成）· 点顶栏「⟳ 手动更新知识库」继续`;
      bar.hidden = false; return;
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

  // ── K1（副本#9）：深索详情面板——轮询 /index/queue，暂停/继续 /index/deep/pause ──
  const DP = { open: false, iv: null, paused: false };
  function _etaText(sec) {
    if (sec == null || sec <= 0) return "";
    if (sec < 45) return "约剩不到 1 分钟";
    const min = Math.round(sec / 60);
    return min > 90 ? "约剩 " + Math.round(min / 60) + " 小时" : "约剩 " + min + " 分钟";
  }
  async function deepPanelPoll() {
    if (!DP.open) return;
    try {
      const q = await jget("/index/queue");
      DP.paused = !!q.paused;
      const deep = q.deep_done || 0, withPdf = withFulltext(q);
      const pending = q.pending || 0, inflight = q.in_flight || 0;
      const building = !!q.building, stage = q.stage || "";
      const xc = extractCounts(q);
      const undeep = Math.max(0, withPdf - deep - xc.blocked);
      // BF31：整库深索以后端 /index/queue 的 bulk 字段为准（队列批次也可能瞬时 inflight=0，旧推断会误报「整库」）；
      // 旧后端没有 bulk 字段（undefined）时回退旧推断，保持兼容
      const bulkDeep = building && stage === "deep" &&
        (q.bulk === true || (q.bulk === undefined && inflight === 0 && pending === 0));
      const queueActive = inflight > 0 || pending > 0;
      // F4：分「整库深索中 / 队列进行中 / 空闲」三态，空闲不再假显示「深索中」，无可暂停对象时藏起暂停按钮
      let stat, eta = _etaText(q.eta_seconds), listEmpty;
      if (bulkDeep) {
        if (withPdf > 0 && (deep + xc.blocked) >= withPdf) {
          // 可处理正文已嵌完——整库深索进入最后的「重建检索索引」阶段。
          // 别再显示冻住的 1388/1398，明说在收尾（同 updateProgress 的处理）。
          stat = `深索已完成，正在重建检索索引（bm25 + 页码映射，20 多万块，需几分钟）…请稍候`;
          listEmpty = "正在重建检索索引，本步骤完成后整库深索即结束";
        } else {
          stat = `整库深索进行中… 已深索 <b>${num(deep)}</b> / ${num(withPdf)} 篇`;
          listEmpty = "正在整库深索（本批完成后自动继续下一批）";
        }
      } else if (queueActive) {
        // BF31：点明队列态只深索用户点选/排队的那些，别让人误以为在整库深索
        stat = `已深索 <b>${num(deep)}</b> / ${num(withPdf)} 篇 · 队列剩余 <b>${num(pending)}</b> 篇`
          + (inflight ? ` · 正在处理 ${num(inflight)} 篇` : "")
          + `（仅深索你勾选/排队的文献）`;
        listEmpty = DP.paused ? "已暂停，暂无正在深索的文献" : "暂无正在深索的文献";
      } else {
        stat = `深索队列已空 · ` + (undeep > 0
          ? `待深索 <b>${num(undeep)}</b> 篇（可在「浏览」页勾选后深索）`
          : `全部全文附件已深索 ✓`);
        const issueText = extractCountsText(q);
        if (issueText) stat += ` · ${issueText}`;
        if (xc.blocked > 0) stat += ` · <a href="#" id="dp-retry-nt" title="修好全文附件后，清除失败状态与旧产物再重试">🔁 重试 ${num(xc.blocked)} 篇提取失败文献</a>`;
        listEmpty = "暂无正在深索的文献"; eta = "";
      }
      $("#dp-stat").innerHTML = stat + sacFrag(q);
      const rnt = $("#dp-retry-nt");
      if (rnt) rnt.addEventListener("click", async (ev) => {
        ev.preventDefault();
        if (!(await uiConfirm("将清除全文附件缺失、无法读取或 PDF OCR 未识别文献的失败状态与旧提取产物。请先确认附件已修好；之后可在「浏览」页重新深索。不会改动原文件。", { title: "重试全文提取失败文献？", okText: "清除并可重试" }))) return;
        try { const r = await jpost("/index/retry_no_text", {}); flashToast(r.msg || `已清除 ${num(r.cleared || 0)} 篇。`); deepPanelPoll(); }
        catch (e) { flashToast("重试失败：" + (e.message || e)); }
      });
      // F4b：队列专属 UI（暂停/继续、「正在深索」列表、⏸已暂停文案）只在真·队列态出现。
      // 整库深索(bulkDeep)走 subprocess、不是队列，即使 /index/queue 瞬时 inflight>0 也不显示队列件，
      // 否则会与「整库深索进行中」并排冒出「暂停 + 队列items/暂无」→ 三态混显（用户 2026-07-15 反馈）。
      const queueUI = queueActive && !bulkDeep;
      $("#dp-eta").textContent = (queueUI && DP.paused)
        ? "⏸ 已暂停（正在跑的那批会跑完，队列保留）"
        : (eta || "");
      const itemsH = $(".dp-items-h"), itemsUl = $("#dp-items");
      if (queueUI) {
        if (itemsH) itemsH.hidden = false;
        if (itemsUl) {
          const items = q.items || [];
          itemsUl.hidden = false;
          itemsUl.innerHTML = items.length
            ? items.map((it) => `<li title="${esc(it.title || it.key || "")}">${esc(it.title || it.key || "（未命名）")}</li>`).join("")
            : `<li class="dp-empty">${listEmpty}</li>`;
        }
      } else {
        // 整库深索 / 空闲：队列列表不适用，连「正在深索」标题一起藏，面板只留 stat(+eta)(+停止整库)
        if (itemsH) itemsH.hidden = true;
        if (itemsUl) { itemsUl.hidden = true; itemsUl.innerHTML = ""; }
      }
      const btn = $("#dp-pause");
      // 仅真·队列态显示暂停/继续；整库与空闲都藏（整库用下面的「停止整库深索」#dp-cancel）
      if (btn) { btn.hidden = !queueUI; btn.textContent = DP.paused ? "继续" : "暂停"; btn.classList.toggle("primary-btn", DP.paused); btn.classList.toggle("ghost", !DP.paused); }
      // 整库深索走 subprocess、不进队列，「暂停」对它无效——给它一个真正的「停止」（终止子进程）
      const cb = $("#dp-cancel");
      if (cb) cb.hidden = !bulkDeep;
    } catch (e) {
      $("#dp-stat").textContent = "读取失败：" + e.message;
    }
  }
  function openDeepPanel() {
    const p = $("#deep-panel"); if (!p) return;
    DP.open = true; p.hidden = false;
    $("#dp-msg").textContent = "";
    deepPanelPoll();
    if (DP.iv) clearInterval(DP.iv);
    DP.iv = setInterval(deepPanelPoll, 3000);
  }
  function closeDeepPanel() {
    const p = $("#deep-panel"); if (p) p.hidden = true;
    DP.open = false;
    if (DP.iv) { clearInterval(DP.iv); DP.iv = null; }
  }
  function toggleDeepPanel() { DP.open ? closeDeepPanel() : openDeepPanel(); }
  { // 顶栏进度条点击 → 展开/收起深索详情
    const idxp = $("#idx-progress");
    if (idxp) idxp.addEventListener("click", toggleDeepPanel);
    const cl = $("#deep-panel-close"); if (cl) cl.addEventListener("click", closeDeepPanel);
    const pb = $("#dp-pause");
    if (pb) pb.addEventListener("click", async () => {
      pb.disabled = true; $("#dp-msg").textContent = "";
      try {
        const r = await jpost("/index/deep/pause", { paused: !DP.paused });
        DP.paused = !!(r && r.paused);
        $("#dp-msg").textContent = DP.paused ? "已暂停。" : "已继续。";
        deepPanelPoll();
      } catch (e) { $("#dp-msg").textContent = "操作失败：" + e.message; }
      finally { pb.disabled = false; }
    });
    // 停止整库深索：终止子进程。深索是增量的，已完成的篇不会白跑。
    const cb = $("#dp-cancel");
    if (cb) cb.addEventListener("click", async () => {
      if (!(await uiConfirm("已经深索完成的文献都已保存，不会白跑。之后可以随时再点「深索」继续未完成的部分。",
            { title: "停止整库深索？", okText: "停止", danger: true }))) return;
      cb.disabled = true; $("#dp-msg").textContent = "正在停止…";
      try {
        await jpost("/build/cancel", {});
        $("#dp-msg").textContent = "已停止。已完成的部分已保存，可随时继续。";
        deepPanelPoll();
      } catch (e) { $("#dp-msg").textContent = "停止失败：" + e.message; }
      finally { cb.disabled = false; }
    });
    // 点面板外部关闭（不含进度条本身，避免点进度条时刚开又被关）
    document.addEventListener("click", (e) => {
      if (!DP.open) return;
      const p = $("#deep-panel"), idx = $("#idx-progress");
      if (p && !p.contains(e.target) && idx && !idx.contains(e.target) && !e.target.closest(".dash-deep-detail")) closeDeepPanel();
    });
  }

  // ── tab 切换（泛化：按 data-panel 显隐）──
  // loaded 标志只在「加载成功」后由 loadDashboard/loadBrowse 自己置位；
  // 加载失败则保持 false，切走再切回会自动重试（旧版加载前就置 true，失败后只能刷新整页）。
  let dashLoaded = false, browseLoaded = false, wikiLoaded = false, agentLoaded = false;
  $$(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));
  // 顶栏页签可拖动排序（用户自定义顺序，存 localStorage，跨会话保留）
  (function wireTabReorder() {
    const nav = document.querySelector("nav.tabs"); if (!nav) return;
    const KEY = "localkb.taborder";
    const tabsOf = () => Array.from(nav.querySelectorAll(".tab"));
    // 恢复已保存的顺序（未在名单里的新页签保持在末尾、原相对顺序）
    try {
      const saved = JSON.parse(localStorage.getItem(KEY) || "null");
      if (Array.isArray(saved) && saved.length) {
        const by = {}; tabsOf().forEach((t) => (by[t.dataset.tab] = t));
        saved.forEach((id) => { if (by[id]) { nav.appendChild(by[id]); delete by[id]; } });
      }
    } catch (e) {}
    const save = () => { try { localStorage.setItem(KEY, JSON.stringify(tabsOf().map((t) => t.dataset.tab))); } catch (e) {} };
    tabsOf().forEach((t) => t.setAttribute("draggable", "true"));
    let drag = null;
    nav.addEventListener("dragstart", (e) => {
      const t = e.target.closest(".tab"); if (!t) return;
      drag = t; t.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      try { e.dataTransfer.setData("text/plain", t.dataset.tab); } catch (_) {}
    });
    nav.addEventListener("dragover", (e) => {
      if (!drag) return; e.preventDefault(); e.dataTransfer.dropEffect = "move";
      const after = (() => {                       // 找光标右侧最近的页签，插到它前面
        let best = { off: -Infinity, el: null };
        tabsOf().filter((t) => t !== drag).forEach((el) => {
          const box = el.getBoundingClientRect();
          const off = e.clientX - box.left - box.width / 2;
          if (off < 0 && off > best.off) best = { off, el };
        });
        return best.el;
      })();
      if (after == null) nav.appendChild(drag);
      else if (after !== drag) nav.insertBefore(drag, after);
    });
    nav.addEventListener("drop", (e) => e.preventDefault());
    nav.addEventListener("dragend", () => { if (drag) { drag.classList.remove("dragging"); drag = null; save(); } });
  })();
  // 通用 tab 切换（顶栏 tab 与代码内跳转共用）
  function switchTab(tab) {
    $$(".tab").forEach((x) => x.classList.toggle("active", x.dataset.tab === tab));
    $$(".panel").forEach((p) => { p.hidden = p.dataset.panel !== tab; });
    syncHomeGuideTrigger(tab);
    // R12：离开浏览页时清掉「生成主题」的轮询，避免后台卡住时无限轮询
    if (tab !== "browse" && BR && BR.topicIv) { clearInterval(BR.topicIv); BR.topicIv = null; }
    if (tab === "dashboard") loadDashboard(dashLoaded ? "silent" : "loud");
    if (tab === "browse" && !browseLoaded) loadBrowse();
    if (tab === "wiki") loadWikiList(wikiLoaded ? "silent" : "loud");
    if (tab === "agent" && !agentLoaded) loadAgentConfig();
    if (tab === "chat") loadChatCats();
  }
  // 浏览与全文检索共用「文献」页。题录查找走 /papers，本地即时；全文模式复用 /search。
  let LIBRARY_MODE = "metadata";
  function browseScopeCategory() {
    if (!BR || !BR.scope || BR.scope.type === "all") return null;
    if (BR.scope.type === "topic") return `topic:${BR.scope.id}`;
    if (BR.scope.type === "zotero") return `zotero:${BR.scope.id}`;
    return BR.scope.id || null;
  }
  function syncMetadataSort(queryChanged) {
    const q = (BR.query || "").trim(), sel = $("#bl-sort");
    const match = sel && sel.querySelector('option[value="match"]');
    if (match) match.hidden = !q;
    if (q && queryChanged && BR.sort === "ingested") BR.sort = "match";
    if (!q && BR.sort === "match") BR.sort = "ingested";
    if (sel) sel.value = BR.sort;
  }
  function runMetadataSearch() {
    const q = $("#q").value.trim(), changed = q !== BR.query;
    BR.query = q;
    syncMetadataSort(changed);
    if (browseLoaded) loadPapers();
  }
  function setLibrarySearchMode(mode, run) {
    LIBRARY_MODE = mode === "semantic" ? "semantic" : "metadata";
    const semantic = LIBRARY_MODE === "semantic";
    const modeSel = $("#lib-search-mode"); if (modeSel) modeSel.value = LIBRARY_MODE;
    const controls = $("#lib-semantic-controls"); if (controls) controls.hidden = !semantic;
    const browseView = $("#browse-view"); if (browseView) browseView.hidden = semantic;
    const semanticView = $("#semantic-view"); if (semanticView) semanticView.hidden = !semantic;
    const q = $("#q"), go = $("#go");
    if (q) q.placeholder = semantic
      ? "输入研究问题，检索相关文献与原文片段"
      : "按题名、作者、期刊、年份、DOI 或 ISBN 查找";
    if (go) go.textContent = semantic ? "检索全文" : "查找";
    if (run === false) return;
    if (semantic) {
      if (q && q.value.trim()) doSearch();
      else if ($("#s-msg")) $("#s-msg").textContent = "输入研究问题，检索相关文献与原文片段。";
    } else runMetadataSearch();
  }
  function runLibrarySearch() {
    if (LIBRARY_MODE === "semantic") doSearch();
    else runMetadataSearch();
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
    switchTab("browse");
    setLibrarySearchMode("semantic", false);
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
  // C4/F6：真·向量找相似。先试 GET /similar/{key}（full 模式下用已存向量取近邻，结果结构同 /search，复用 resultCard）；
  // ok:false（light 模式 / 取不到向量）时无损回退到既有抽词法 switchToSearch。
  async function findSimilar(key, title) {
    if (!key) { switchToSearch(title || ""); return; }
    switchTab("browse");
    setLibrarySearchMode("semantic", false);
    // UX7：不再把「与「XX」相似」这种伪查询塞进 #q（用户会误当成自己的检索词）——#q 保持原输入，
    // 相似态改由 #s-msg 的提示条表达，点「清除」即回到普通检索
    const tShort = (title || "").slice(0, 24) + ((title || "").length > 24 ? "…" : "");
    // BF13：找相似占用检索序号——既作废在途的旧 doSearch，自己的响应也要判序
    // （连点两篇文献标题会并发两个 /similar，后返回者不能覆盖最后点击的那篇）
    const myseq = ++SR.reqSeq;
    $("#go").disabled = true; $("#results").innerHTML = ""; $("#s-msg").textContent = "查找相似文献中…";
    _clearSentinel();
    SR.raw = []; SR.all = []; SR.shown = 0; SR.facet = { deep: false, tier: "", year: "" };
    const fb = $("#s-facets"); if (fb) { fb.hidden = true; fb.innerHTML = ""; }
    try {
      const r = await jget("/similar/" + encodeURIComponent(key) + "?topk=8");
      if (myseq !== SR.reqSeq) return;   // 已有更新的检索/找相似，丢弃本响应
      if (r && r.ok) {
        SR.raw = r.results || []; SR.all = SR.raw.slice();
        $("#go").disabled = false;
        if (!SR.raw.length) { $("#s-msg").textContent = "没有找到相似文献。"; return; }
        // UX11：黑话「向量近邻」→「按内容相似」
        $("#s-msg").innerHTML = `正在显示与《${esc(tShort)}》相似的 ${SR.raw.length} 篇文献（按内容相似） · <a class="ag-link" id="s-sim-clear">清除</a>`;
        const clr = $("#s-sim-clear");
        if (clr) clr.addEventListener("click", () => {
          _clearSentinel(); SR.raw = []; SR.all = []; SR.shown = 0;
          $("#results").innerHTML = ""; $("#s-msg").textContent = "";
          if ($("#q").value.trim()) doSearch();   // 原输入还在就直接恢复用户自己的检索
        });
        _mountResults(); renderFacets();
        return;
      }
    } catch (e) { /* 落到抽词回退 */ }
    if (myseq !== SR.reqSeq) return;   // BF13：过期请求不触发回退，也不动 #go
    $("#go").disabled = false;
    switchToSearch(title || "");   // 回退：无向量则用抽词法（保证无回退风险）
  }

  // ══════════════════════════════════════════
  //  检索
  // ══════════════════════════════════════════
  // 普通页面只显示一个客观标签；四档评价留给排序、tooltip、库总览和改档菜单。
  const BAND_ORDER = ["authority", "top", "core", "normal"];
  const BAND_STANDARD = { authority: "权威", top: "顶级", core: "核心", normal: "普通" };
  const BAND_COLOR = { authority: "#c0392b", top: "#a93226", core: "#e67e22", normal: "#7f8c8d" };
  const LEGACY_BAND = { "权威":"authority", "准权威":"top", "顶级":"top", "核心":"core",
                        "次核心":"core", "一般":"normal", "普通":"normal", "待确认":"normal", "未知":"normal" };
  const DISC = { id: "", name: "", bandNames: Object.assign({}, BAND_STANDARD), notice: "" };
  function absorbBandNames(data) {
    if (!data) return;
    let src = data.band_names || data.tier_names || data.bandNames || null;
    if (!src && Array.isArray(data.bands)) {
      src = {}; data.bands.forEach((x) => { if (x && x.band) src[x.band] = x.band_name || x.name || x.standard_band_name; });
    }
    if (src) BAND_ORDER.forEach((b) => { if (src[b]) DISC.bandNames[b] = String(src[b]); });
    const minw = $("#minw");
    if (minw) Array.from(minw.options).forEach((o) => {
      const b = o.dataset.band;
      if (b) o.textContent = b === "authority" ? `只看${DISC.bandNames[b]}` : `${DISC.bandNames[b]}及以上`;
    });
  }
  function bandOf(r) {
    if (r && BAND_ORDER.includes(r.band)) return r.band;
    const code = (r && (r.weight_tier_code || r.internal_tier || r.tier)) || "";
    if (code === "T1") return "authority";
    if (code === "T1b") return "top";
    if (code === "T2" || code === "T3") return "core";
    return LEGACY_BAND[(r && (r.band_name || r.weight_tier || r.journal_tier)) || ""] || "normal";
  }
  function bandDisplay(r) { return (r && (r.band_name || r.standard_band_name || r.weight_tier)) || DISC.bandNames[bandOf(r)] || BAND_STANDARD[bandOf(r)]; }
  function objectiveLabel(r) { return (r && (r.objective_label || r.source_label || r.journal_tier || r.source_type_name)) || "来源"; }
  function bandAlias(shown, standard) {
    return shown && standard && shown !== standard ? `${shown}（${standard}）` : (shown || standard || "普通");
  }
  function badgeColor(nameOrBand) {
    const b = BAND_ORDER.includes(nameOrBand) ? nameOrBand : (LEGACY_BAND[nameOrBand] || "normal");
    return BAND_COLOR[b];
  }
  function gradingTip(r) {
    const shown = bandDisplay(r), standard = (r && r.standard_band_name) || BAND_STANDARD[bandOf(r)];
    return `${objectiveLabel(r)} · 当前评价：${bandAlias(shown, standard)} · ${r && (r.manual || r.weight_src === "manual") ? "手动改档" : "自动评定"}`;
  }
  // 客观标签可点击打开四档菜单；手动改档只增加轻量 ✎，不改标签文字。
  function tierBadge(r) {
    if (r.is_wiki) return "";
    const label = objectiveLabel(r);
    const manual = !!r.manual || r.weight_src === "manual";
    const clickable = !!r.key && !r.is_wiki;
    const tip = gradingTip(r) + (clickable ? "。点击可手动改档；客观标签不会被改写" : "");
    const currentBand = bandOf(r), currentName = bandDisplay(r), currentStandard = r.standard_band_name || BAND_STANDARD[currentBand];
    const autoBand = r.auto_band || currentBand, autoName = r.auto_band_name || DISC.bandNames[autoBand] || BAND_STANDARD[autoBand];
    const autoStandard = r.auto_standard_band_name || BAND_STANDARD[autoBand];
    const dk = clickable ? ` data-key="${esc(r.key)}"` : "";
    const gradingData = clickable ? ` data-band="${esc(currentBand)}" data-band-name="${esc(currentName)}" data-standard-band-name="${esc(currentStandard)}" data-auto-band="${esc(autoBand)}" data-auto-band-name="${esc(autoName)}" data-auto-standard-band-name="${esc(autoStandard)}" data-manual="${manual ? "1" : "0"}"` : "";
    return `<span class="badge tier-badge objective-badge${clickable ? " tb-click" : ""}"${dk}${gradingData} title="${esc(tip)}">${esc(label)}${manual ? " ✎" : ""}</span>`;
  }
  function closeTierMenu() { const old = document.getElementById("tier-menu"); if (old) old.remove(); }
  function openTierMenu(badge) {
    closeTierMenu();
    const key = badge.dataset.key;
    if (!key) return;
    const label = badge.textContent.replace(/\s*✎\s*$/, "");
    const currentBand = badge.dataset.band || "normal";
    const currentName = badge.dataset.bandName || DISC.bandNames[currentBand] || BAND_STANDARD[currentBand];
    const currentStandard = badge.dataset.standardBandName || BAND_STANDARD[currentBand];
    const autoBand = badge.dataset.autoBand || currentBand;
    const autoName = badge.dataset.autoBandName || DISC.bandNames[autoBand] || BAND_STANDARD[autoBand];
    const autoStandard = badge.dataset.autoStandardBandName || BAND_STANDARD[autoBand];
    const manual = badge.dataset.manual === "1";
    const m = document.createElement("div");
    m.id = "tier-menu";
    const bandButtons = BAND_ORDER.map((b) => {
      const shown = DISC.bandNames[b] || BAND_STANDARD[b];
      const selected = b === currentBand;
      return `<button data-tier="${b}" class="${selected ? "selected" : ""}" aria-pressed="${selected ? "true" : "false"}"><span>${selected ? "✓" : ""}</span>${esc(bandAlias(shown, BAND_STANDARD[b]))}</button>`;
    }).join("");
    const restore = manual ? `<button data-tier="" class="tm-restore">↺ 恢复自动：${esc(bandAlias(autoName, autoStandard))}</button>` : "";
    m.innerHTML = `<div class="tm-head"><b>${esc(label)}</b><span>当前评价：${esc(bandAlias(currentName, currentStandard))} · ${manual ? "手动改档" : "自动评定"}</span>${manual ? `<small>自动规则：${esc(bandAlias(autoName, autoStandard))}</small>` : ""}</div>${bandButtons}${restore}`;
    document.body.appendChild(m);
    const rc = badge.getBoundingClientRect();
    m.style.left = Math.max(8, Math.min(rc.left, window.innerWidth - m.offsetWidth - 8)) + "px";
    m.style.top = Math.max(8, Math.min(rc.bottom + 4, window.innerHeight - m.offsetHeight - 8)) + "px";
    m.addEventListener("click", async (e) => {
      const b = e.target.closest("button[data-tier]");
      if (!b) return;
      e.stopPropagation();
      try {
        const res = await jpost("/paper/tier", { key, tier: b.dataset.tier || null });
        const g = res.effective || {};
        const isManual = g.manual != null ? !!g.manual : !!res.override;
        // 同键徽标全部更新（同文献可能有多张卡），并回写内存行数据——
        // 否则 facet 收窄/重渲染会用旧数据把徽标打回原档（对抗审查 #9/#12/#13）。
        document.querySelectorAll(`.tier-badge[data-key="${CSS.escape(key)}"]`).forEach((el) => {
          el.textContent = objectiveLabel(g) + (isManual ? " ✎" : "");
          el.title = gradingTip(Object.assign({}, g, { manual: isManual })) + "。点击可手动改档；客观标签不会被改写";
          const band = g.band || "normal", auto = g.auto_band || band;
          el.dataset.band = band;
          el.dataset.bandName = g.band_name || DISC.bandNames[band] || BAND_STANDARD[band];
          el.dataset.standardBandName = g.standard_band_name || BAND_STANDARD[band];
          el.dataset.autoBand = auto;
          el.dataset.autoBandName = g.auto_band_name || DISC.bandNames[auto] || BAND_STANDARD[auto];
          el.dataset.autoStandardBandName = g.auto_standard_band_name || BAND_STANDARD[auto];
          el.dataset.manual = isManual ? "1" : "0";
        });
        const patch = (row) => {
          if (!row || row.key !== key) return;
          ["objective_label", "band", "band_name", "standard_band_name", "auto_band", "auto_band_name", "auto_standard_band_name", "source_type", "source_type_name"].forEach((k) => {
            if (g[k] != null) row[k] = g[k];
          });
          row.weight_tier = g.band_name || g.cn || row.weight_tier || "";
          row.weight_tier_code = g.internal_tier || g.tier || null;
          row.weight = (g.weight != null) ? g.weight : row.weight;
          row.journal_weight = (g.weight != null) ? g.weight : null;
          row.weight_needs_review = false;
          row.manual = isManual;
          row.weight_src = isManual ? "manual" : (g.src || null);
        };
        (SR.raw || []).forEach(patch); (SR.all || []).forEach(patch);
        ((typeof BR !== "undefined" && BR.papers) || []).forEach(patch);
      } catch (err) { flashToast("改档失败：" + err.message); }   // UX10：应用内浮层替代浏览器灰框 alert
      closeTierMenu();
    });
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest(".tier-badge.tb-click");
    if (b) { e.stopPropagation(); e.preventDefault(); openTierMenu(b); return; }
    if (!e.target.closest("#tier-menu")) closeTierMenu();
  });
  // 「⚡深索/＋分类」等控件 stopPropagation 会拦掉上面的冒泡关闭——捕获阶段兜底；
  // 滚动/Escape 也关闭，防 fixed 定位的菜单脱锚残留（对抗审查 #10/#11）。
  document.addEventListener("click", (e) => {
    if (!document.getElementById("tier-menu")) return;
    if (e.target.closest("#tier-menu") || e.target.closest(".tier-badge.tb-click")) return;
    closeTierMenu();
  }, true);
  document.addEventListener("scroll", closeTierMenu, true);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeTierMenu(); });
  // 次徽标已废弃：权威度数值已收进 tierBadge 的 tooltip（redundant-tier-weight-badge）。保留空实现兼容旧调用点。
  function weightBadge(r) { return ""; }
  function extractFailureBadge(r) {
    const s = extractState(r), rec = (r && r.extract_status) || {};
    if (s === "missing_pdf" || s === "missing_file") return `<span class="tag nopdf" title="记录里的全文附件已不在磁盘上；请先在 Zotero 中修复路径">📎 附件缺失</span>`;
    if (s === "invalid_pdf" || s === "invalid_file") return `<span class="tag nopdf" title="全文附件无法读取，可能损坏、未同步完整、格式异常或被占用">⚠ 附件无法读取</span>`;
    if (s === "ocr_failed") return `<span class="tag nopdf" title="本地 OCR 已运行，但没有识别出有效文字；可检查 PDF 后重试">⚠ OCR未识别</span>`;
    if (!s && r && r.no_text) return `<span class="tag nopdf" title="旧版提取失败记录；更新状态后会显示具体原因">⚠ 正文提取失败</span>`;
    if (rec.empty_pages > 0 && (s === "ok_native" || s === "ok_ocr"))
      return `<span class="tag review" title="正文已入索引，但仍有 ${num(rec.empty_pages)} 页未识别">⚠ ${num(rec.empty_pages)}页未识别</span>`;
    return "";
  }
  function depthTag(r) {
    if (r.is_wiki) return "";                       // wiki 行用专属徽标（wikiBadge），不显示深度标
    const failure = extractFailureBadge(r);
    if (extractBlocked(r)) return failure;
    if (r.depth === "full") return `<span class="tag full">📄 ${esc(fulltextLabel(r))} · 已深索</span>` + failure + (r.has_summary ? `<span class="tag sac" title="已生成并通过质量检查，检索更容易命中这篇">🧬 摘要有效</span>` : "");
    // F12：有 PDF 的未深索命中→可点击深索；无 PDF 的纯题录不给深索入口（避免点了无效）
    if (hasFulltext(r)) {
      const pending = extractState(r) === "ocr_pending";
      return `<span class="tag abstract deep-one" data-key="${esc(r.key || "")}" role="button" tabindex="0" title="${pending ? "点此继续深索并自动运行本地 OCR；不会上传或改写原 PDF" : "点此深索该篇：全文拆成可检索的小段后可回溯原文位置"}"><span class="lbl-idle">${pending ? "🔎 待本地OCR" : `📋 ${esc(fulltextLabel(r))} · 未深索`}</span><span class="lbl-hover">⚡ ${pending ? "开始OCR" : "深索该篇"}</span></span>`;
    }
    return `<span class="tag nopdf" title="无受支持全文附件，只有题录（题名·作者·年份·期刊）">🚫 无全文 / 仅题录</span>`;
  }
  // 综合层徽标：命中的是"已存综合"页（可能已过时；来源可展开回溯到论文页码）。
  // agent 经 MCP 写回、未核验的页标 🤖，方便一眼锁定该复看/剔除的对象；人自己保存的标 📝。
  function wikiBadge(r) {
    if (!r.is_wiki) return "";
    return r.by_agent
      ? `<span class="tag wiki agent" title="agent 自动写回、未经人工核验；可点「🗑 不保存」剔除">🤖 综述页·未核验</span>`
      : `<span class="tag wiki" title="本地综述页，可能已过时，请核对来源原文">📝 综述页</span>`;
  }
  // EN-F5：法条时效徽标——retriever 输出侧按 papers.jsonl 现算的 statute_status（""|已修订|已废止）。
  // 引已废止/已修订的法条是硬伤，红/橙醒目提示；样式对齐既有 tier 徽标（.badge）
  function statuteBadge(r) {
    const s = r.statute_status || "";
    if (s !== "已修订" && s !== "已废止") return "";
    const cls = s === "已废止" ? "revoked" : "revised";
    return `<span class="badge statute-badge ${cls}" title="该法条${s}，注意核对现行有效版本">${s}</span>`;
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
  function resultCard(r, i) {
    const div = document.createElement("div");
    div.className = "card";
    // 主行只留标题（F32）；作者/年份/期刊/页码统一进 metaRow；score 黑话不再显示
    const discard = r.is_wiki ? `<button class="ghost2 wiki-discard" title="删除这条本地综合页（文件+索引+检索行；不影响文献库）">🗑 不保存</button>` : "";
    const gotoWiki = r.is_wiki ? `<button class="ghost2 wiki-goto" title="在综述库查看这条综述">📖 在综述库打开</button>` : "";
    const rawCtx = (r.context || "").trim();
    const hasCtx = rawCtx && rawCtx !== (r.text || "").trim();
    const ctxTitleBits = [];
    if (r.official_pages) ctxTitleBits.push("第 " + esc(r.official_pages) + " 页");
    if (r.heading) ctxTitleBits.push(esc(r.heading));
    const ctxHead = "📖 该片段所在整段原文" + (ctxTitleBits.length ? " · " + ctxTitleBits.join(" · ") : "");
    // C2：深索结果（有 key、非 wiki）可一键打开原文 PDF 核对页码
    const openPdf = (!r.is_wiki && r.key && r.depth === "full") ? `<button class="ghost2 open-pdf" title="用系统默认阅读器打开这篇的原文文件">📄 打开原文</button>` : "";
    div.innerHTML =
      `<div class="card-head"><span class="idx">#${i}</span>${wikiBadge(r)}${tierBadge(r)}${statuteBadge(r)}${weightBadge(r)}${depthTag(r)}</div>` +   // EN-F5：检索卡带法条时效徽标
      `<div class="cite">${esc(r.title || r.citation || "")}</div>` +
      metaRow(r) +
      `<div class="snippet">${esc((r.text || "").trim())}</div>` +
      `<div class="card-btns">` +
        (hasCtx ? `<button class="ghost2 ctx-toggle">查看原文上下文</button>` : "") + openPdf + gotoWiki + discard +
      `</div>` +
      (hasCtx ? `<div class="ctx hidden"><div class="ctx-h">${ctxHead}</div><div class="ctx-body">${esc(rawCtx)}</div></div>` : "");
    const btn = div.querySelector(".ctx-toggle"), ctx = div.querySelector(".ctx");
    if (btn && ctx) btn.addEventListener("click", () => { const h = ctx.classList.toggle("hidden"); btn.textContent = h ? "查看原文上下文" : "收起原文"; });
    const gbtn = div.querySelector(".wiki-goto");
    if (gbtn) gbtn.addEventListener("click", () => { switchTab("wiki"); openWikiPage(r.key); });
    const dbtn = div.querySelector(".wiki-discard");
    if (dbtn) dbtn.addEventListener("click", () => discardWiki(r.key, () => {
      div.remove();
      // BF15：同时从 SR.raw 与 SR.all 剔除该行（参照改档 patch 同步两数组的先例）——
      // 否则 facet 收窄/重渲染会用内存旧数据把已删的综述卡复活
      const drop = (arr) => (arr || []).filter((x) => !(x.is_wiki && x.key === r.key));
      SR.raw = drop(SR.raw); SR.all = drop(SR.all);
    }, dbtn));
    const obtn = div.querySelector(".open-pdf");
    if (obtn) obtn.addEventListener("click", () => openPdfByKey(r.key, obtn));
    // F12：检索结果里的「未深索」徽标可点击单篇深索
    const rdb = div.querySelector(".deep-one");
    if (rdb) {
      const fire = (e) => { e.stopPropagation(); deepOneFromBadge(r.key, rdb); };
      rdb.addEventListener("click", fire);
      rdb.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fire(e); } });
    }
    return div;
  }
  // ── 打开原文 PDF（C2）/ 引文与 BibTeX（D3/F1）辅助 ──
  async function openPdfByKey(key, btn) {
    if (!key) return;
    const old = btn ? btn.textContent : null;
    if (btn) { btn.disabled = true; btn.textContent = "打开中…"; }
    try {
      const r = await jpost("/open_source", { key });
      if (!r || !r.ok) flashToast("打不开原文：" + ((r && r.msg) || "未找到全文附件"));
    } catch (e) { flashToast("打不开原文：" + (e.message || e)); }
    finally { if (btn) { btn.disabled = false; if (old != null) btn.textContent = old; } }
  }
  // 通用复制（clipboard + execCommand 兜底），复制成功时闪一下按钮文字
  async function copyText(txt, btn) {
    const old = btn ? btn.textContent : null;
    const ok = () => { if (btn) { btn.textContent = "✓ 已复制"; setTimeout(() => { if (old != null) btn.textContent = old; }, 1500); } };
    try { await navigator.clipboard.writeText(txt); ok(); return; } catch (e) {}
    const ta = document.createElement("textarea"); ta.value = txt; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.focus(); ta.select();
    try { document.execCommand("copy"); ok(); } catch (_) { if (btn) btn.textContent = "复制失败"; }
    document.body.removeChild(ta);
  }
  // 无限滚动：一次取较大批(重排只跑一次、顺序最稳)，先渲染 10 条，滚到底再追加（原#30/副本#24）
  // raw=后端原始命中；all=facet 过滤后当前展示集（F5）。facet=当前选中的二次筛选。
  // reqSeq：BF13 检索请求序号守卫（同浏览页 BR.reqSeq 的 B5），防快速连发时旧响应覆盖新检索
  const SR = { raw: [], all: [], shown: 0, observer: null, page: 10, facet: { deep: false, band: "", year: "" }, reqSeq: 0 };
  function _renderMoreResults() {
    const end = Math.min(SR.shown + SR.page, SR.all.length);
    for (let i = SR.shown; i < end; i++) $("#results").appendChild(resultCard(SR.all[i], i + 1));
    SR.shown = end;
    const sentinel = $("#s-sentinel");
    if (sentinel) sentinel.hidden = SR.shown >= SR.all.length;
  }
  // R9：每次重渲染先清掉旧哨兵与旧 observer，避免泄漏出多个 #s-sentinel（ID 重复、分页操作到错误的哨兵）
  function _clearSentinel() {
    if (SR.observer) { SR.observer.disconnect(); SR.observer = null; }
    const old = $("#s-sentinel"); if (old) old.remove();
  }
  function _mountResults() {
    $("#results").innerHTML = ""; SR.shown = 0;
    _clearSentinel();
    if (!SR.all.length) return;
    _renderMoreResults();
    const sentinel = document.createElement("div");
    sentinel.id = "s-sentinel"; sentinel.className = "s-sentinel";
    sentinel.hidden = SR.shown >= SR.all.length;
    $("#results").after(sentinel);
    SR.observer = new IntersectionObserver((es) => {
      if (es[0].isIntersecting && SR.shown < SR.all.length) _renderMoreResults();
    }, { rootMargin: "200px" });
    SR.observer.observe(sentinel);
  }
  // F5：基于 SR.raw 前端二次筛选（仅已深索 / 来源评价 / 年份），即时收窄，无需重查后端
  function applyFacets() {
    const f = SR.facet;
    SR.all = SR.raw.filter((r) => {
      if (f.deep && r.depth !== "full") return false;
      if (f.band && bandOf(r) !== f.band) return false;
      if (f.year && String(r.year || "") !== f.year) return false;
      return true;
    });
    _mountResults();
  }
  function renderFacets() {
    const box = $("#s-facets"); if (!box) return;
    if (!SR.raw.length) { box.hidden = true; box.innerHTML = ""; return; }
    const bands = {}, years = {}; let deepN = 0;
    SR.raw.forEach((r) => {
      if (r.depth === "full") deepN++;
      const b = bandOf(r); bands[b] = (bands[b] || 0) + 1;
      const y = String(r.year || ""); if (y) years[y] = (years[y] || 0) + 1;
    });
    const f = SR.facet;
    let html = "";
    if (deepN && deepN < SR.raw.length) html += `<span class="facet-chip${f.deep ? " on" : ""}" data-fk="deep">📄 仅已深索 (${deepN})</span>`;
    BAND_ORDER.forEach((b) => {
      if (bands[b] && Object.keys(bands).length > 1) html += `<span class="facet-chip${f.band === b ? " on" : ""}" data-fk="band" data-fv="${b}">${esc(DISC.bandNames[b] || BAND_STANDARD[b])} (${bands[b]})</span>`;
    });
    Object.keys(years).sort((a, b) => b.localeCompare(a)).slice(0, 6).forEach((y) => {
      html += `<span class="facet-chip${f.year === y ? " on" : ""}" data-fk="year" data-fv="${esc(y)}">${esc(y)} (${years[y]})</span>`;
    });
    box.innerHTML = html ? (`<span class="facet-lbl">收窄：</span>` + html) : "";
    box.hidden = !html;
    box.querySelectorAll(".facet-chip").forEach((c) => c.addEventListener("click", () => {
      const k = c.dataset.fk, v = c.dataset.fv || "";
      if (k === "deep") f.deep = !f.deep;
      else f[k] = (f[k] === v) ? "" : v;   // 再点同一个＝取消
      renderFacets(); applyFacets();
    }));
  }
  async function doSearch() {
    const q = $("#q").value.trim(); if (!q) return;
    const myseq = ++SR.reqSeq;   // BF13：请求序号守卫，过期响应不写结果/历史、不提前解禁按钮
    $("#go").disabled = true; $("#results").innerHTML = ""; $("#s-msg").textContent = "检索中…";
    _clearSentinel();
    SR.raw = []; SR.all = []; SR.shown = 0; SR.facet = { deep: false, band: "", year: "" };
    const box = $("#s-facets"); if (box) { box.hidden = true; box.innerHTML = ""; }
    try {
      const cat = browseScopeCategory() || "";
      const TOPK = 50;   // search-hard-cap：由 20 提到 50
      const res = await jpost("/search", { query: q, topk: TOPK, sort: $("#sort").value,
        min_weight: parseFloat($("#minw") && $("#minw").value) || 0, category: cat || null });
      if (myseq !== SR.reqSeq) return;   // BF13：已有更新的检索发出，丢弃这次陈旧响应
      if (res.error) { $("#s-msg").textContent = res.error; return; }
      SR.raw = res.results || []; SR.all = SR.raw.slice();
      // T4：不再泄漏「词法模式」黑话；深索后可精读到页码
      const modeTip = res.mode === "light" ? " · 快速检索（深索后可精读到页码）" : "";
      const capTip = SR.raw.length >= TOPK ? `（已达上限 ${TOPK} 条，可加关键词或缩小范围）` : "";
      $("#s-msg").textContent = `命中 ${SR.raw.length} 条 · ${res.took_ms != null ? res.took_ms : "?"}ms${modeTip}${capTip}`;
      if (!SR.raw.length) {
        // UX14：限定了分类且 0 命中——点明「只搜了这个分类」，给一键切回全库重搜的出口
        if (cat) {
          const catName = (BR.scope && BR.scope.name) || "当前分类";
          $("#s-msg").innerHTML = `当前仅在『${esc(catName)}』内检索，无结果 · <a class="ag-link" id="s-cat-clear">切回全部文献</a>`;
          const cc = $("#s-cat-clear");
          if (cc) cc.addEventListener("click", () => applyScope("all", null, "全部", null, true));
        } else { $("#s-msg").textContent += "（无结果，换个关键词试试）"; }
        return;
      }
      _mountResults();
      renderFacets();
      pushSearchHistory(q);   // F4
    } catch (e) {
      if (myseq !== SR.reqSeq) return;   // BF13：陈旧请求的失败同样不写界面
      $("#s-msg").textContent = "检索失败：" + e.message;
    }
    finally { if (myseq === SR.reqSeq) $("#go").disabled = false; }   // BF13：只有最新请求才解禁按钮
  }
  $("#go").addEventListener("click", runLibrarySearch);
  // Enter 触发当前模式；全文检索进行中时不重复提交。
  $("#q").addEventListener("keydown", (e) => { if (e.key === "Enter" && !$("#go").disabled) runLibrarySearch(); });
  $("#lib-search-mode").addEventListener("change", (e) => setLibrarySearchMode(e.target.value));

  // ── F4：检索历史（localStorage 最近 15 条，可点 chip）──
  function pushSearchHistory(q) {
    if (!q) return;
    let h = safeParse(localStorage.getItem("localkb.searchHist"), []);
    if (!Array.isArray(h)) h = [];
    h = h.filter((x) => x !== q); h.unshift(q); h = h.slice(0, 15);
    localStorage.setItem("localkb.searchHist", JSON.stringify(h));
    renderSearchHistory();
  }
  function renderSearchHistory() {
    const box = $("#s-history"); if (!box) return;
    const h = safeParse(localStorage.getItem("localkb.searchHist"), []);
    if (!Array.isArray(h) || !h.length) { box.hidden = true; box.innerHTML = ""; return; }
    box.innerHTML = `<span class="s-chips-lbl">🕘 最近检索：</span>` +
      h.map((q) => `<span class="s-chip" data-q="${esc(q)}">${esc(q)}</span>`).join("") +
      `<span class="s-chip s-chip-clear">清空</span>`;
    box.hidden = false;
    // BF13：检索进行中（按钮禁用）时 chip 不抢跑，与 #q 的 Enter 行为一致
    box.querySelectorAll(".s-chip[data-q]").forEach((c) => c.addEventListener("click", () => { if ($("#go").disabled) return; $("#q").value = c.dataset.q; doSearch(); }));
    const clr = box.querySelector(".s-chip-clear");
    if (clr) clr.addEventListener("click", () => { localStorage.removeItem("localkb.searchHist"); renderSearchHistory(); });
  }
  // ── F9：新手示例检索词（可点 chip）──
  const EXAMPLE_QUERIES = ["认罪认罚从宽对司法信任的影响", "社会观护制度", "程序正义与结果正义", "数据合规的法律责任"];
  let EXAMPLES_DYN = null;   // UX13：由 /stats 最近入库标题动态生成的示例词（拿不到退静态）
  function renderExamples() {
    const box = $("#s-examples"); if (!box) return;
    const qs = (EXAMPLES_DYN && EXAMPLES_DYN.length) ? EXAMPLES_DYN : EXAMPLE_QUERIES;
    box.innerHTML = `<span class="s-chips-lbl">试试：</span>` +
      qs.map((q) => `<span class="s-chip" data-q="${esc(q)}">${esc(q)}</span>`).join("");
    box.hidden = false;
    // BF13：检索进行中不抢跑
    box.querySelectorAll(".s-chip").forEach((c) => c.addEventListener("click", () => { if ($("#go").disabled) return; $("#q").value = c.dataset.q; doSearch(); }));
  }
  // UX13：示例词优先贴合本库——复用 loadDashboard 已拉到的 /stats（不多打接口），
  // 取最近入库前 4 条标题的前 8–12 字作为示例 chip
  function updateExamplesFromStats(d) {
    const rec = (d && d.recent) || [];
    const qs = rec.slice(0, 4)
      .map((r) => String(r.title || "").replace(/[《》「」“”"']/g, "").trim().slice(0, 12))
      .filter((s) => s.length >= 4);
    if (qs.length) { EXAMPLES_DYN = qs; renderExamples(); }
  }
  renderExamples(); renderSearchHistory();

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
      <text x="21" y="20" text-anchor="middle" font-size="6.5" font-weight="700" fill="currentColor" class="donut-total">${num(total)}</text>
      <text x="21" y="26" text-anchor="middle" font-size="3.2" fill="currentColor" class="donut-unit">篇</text></svg>`;
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

  // 覆盖：堆叠横条（有全文附件 / 无全文附件）+ 无摘要标注
  function coverageCard(cov) {
    const total = cov.total || 0;
    const withPdf = withFulltext(cov), noPdf = noFulltext(cov), noAbs = cov.no_abstract || 0, metaIdx = cov.meta_indexed || 0;
    const pw = total ? (withPdf / total) * 100 : 0;
    const stacked = `<svg viewBox="0 0 100 8" preserveAspectRatio="none" style="width:100%;height:16px;border-radius:8px">
      <rect x="0" y="0" width="${pw.toFixed(2)}" height="8" fill="#16a085"><title>有全文附件：${withPdf} 篇</title></rect>
      <rect x="${pw.toFixed(2)}" y="0" width="${(100 - pw).toFixed(2)}" height="8" fill="#cbd5e1"><title>无全文附件：${noPdf} 篇</title></rect></svg>`;
    return `<div class="dcard span2"><h4>入库覆盖</h4>
      <p class="dcard-sub">题录已索引 ${num(metaIdx)} 篇；有全文附件的可深索并回溯原文位置</p>
      <div class="cov-nums">
        <div class="n"><b>${num(total)}</b><span>总篇数</span></div>
        <div class="n"><b style="color:#16a085">${num(withPdf)}</b><span>有全文附件</span></div>
        <div class="n"><b style="color:#94a3b8">${num(noPdf)}</b><span>无全文附件</span></div>
        <div class="n"><b style="color:#b45309">${num(noAbs)}</b><span>无摘要</span></div>
      </div>
      ${stacked}
      <div class="cov-legend">
        <span><i style="background:#16a085"></i>有全文 ${total ? Math.round(withPdf / total * 100) : 0}%</span>
        <span><i style="background:#cbd5e1"></i>无全文 ${total ? Math.round(noPdf / total * 100) : 0}%</span>
        <span><i style="background:#f59e0b"></i>无摘要 ${num(noAbs)} 篇（仅题录）</span>
      </div></div>`;
  }

  // 旧缓存可能仍是六档；展示层无损折叠为四档。
  function mergeProvisionalTier(by) {
    const counts = { authority: 0, top: 0, core: 0, normal: 0 };
    (by || []).forEach((x) => { counts[x.band || LEGACY_BAND[x.tier] || "normal"] += Number(x.n || x.count || 0); });
    return BAND_ORDER.map((band) => ({ band, tier: DISC.bandNames[band] || BAND_STANDARD[band], n: counts[band] }));
  }
  // 「最近入库」的「展开更多」→ 跳「浏览」并按入库时间排序
  function _goBrowseRecent() {
    BR.sort = "ingested"; BR.query = ""; BR.deepFilter = ""; BR.sourceType = ""; BR.objectiveLabel = "";
    setLibrarySearchMode("metadata", false);
    const q = $("#q"); if (q) q.value = "";
    const bs = $("#bl-sort"); if (bs) bs.value = "ingested";
    const df = $("#bl-deep-filter"); if (df) df.value = "";
    const tf = $("#bl-type-filter"); if (tf) tf.value = "";
    const firstBrowse = !browseLoaded;
    switchTab("browse");
    if (!firstBrowse) selectCollection(null, "全部", null);
  }
  function tierCard(by_tier) {
    by_tier = mergeProvisionalTier(by_tier);
    if (!by_tier || !by_tier.length) return "";
    const max = Math.max.apply(null, by_tier.map((d) => d.n)) || 1;
    const rows = by_tier.map((d) => hbar(d.tier, d.n, max, badgeColor(d.band), true)).join("");
    return `<div class="dcard"><h4>四档来源评价</h4>
      <p class="dcard-sub">按当前锁定学科评定；普通页面仍只显示客观标签</p>${rows}</div>`;
  }

  function journalCard(by_journal) {
    if (!by_journal || !by_journal.length) return "";
    const max = Math.max.apply(null, by_journal.map((d) => d.n)) || 1;
    const rows = by_journal.map((d) => hbar(d.journal, d.n, max, badgeColor(d.tier), true)).join("");
    return `<div class="dcard span2 jlist"><h4>高频期刊 Top ${by_journal.length}</h4>
      <p class="dcard-sub">带来源评价色点（按当前学科）</p>${rows}</div>`;
  }
  // 旧扁平刊名→颜色（保留作 grading 预热中/引擎 down 的兜底；新口径优先走 WT_COLOR）
  const TIER_RANK_BY_NAME = {
    "CLSCI": 0, "台湾核心": 0, "外文顶级法评": 0,
    "外文权威": 1,
    "CSSCI": 2, "台湾一般": 2,
    "CSSCI扩展": 3,
    "外文一般": 4, "普刊": 4,
    "法源": 1, "官方报告": 2,   // source_rules 建库期标签的旧版颜色兜底
    "报纸": 5, "未知": 6,
  };
  function tierNameColor(name) {
    const r = TIER_RANK_BY_NAME[name];
    return TIER_COLOR[r != null ? r : 6];
  }

  // 最近入库与评价分布共用一张「文库概况」卡；每条带三态深索按钮。
  function recentSection(recent) {
    if (!recent || !recent.length) return `<section class="ov-recent"><div class="ov-panel-head"><h5>最近入库</h5></div><p class="hint">暂无</p></section>`;
    const row = (r) => {
      const t = (r.title || "").slice(0, 52) + ((r.title || "").length > 52 ? "…" : "");
      let btn;
      if (r.deep) btn = `<span class="rc-tag done">已深索</span>`;
      else if (hasFulltext(r)) btn = `<button class="rc-deep" data-key="${esc(r.key || "")}">深索</button>`;
      else btn = `<span class="rc-tag nopdf" title="无全文附件，无法深索">无全文</span>`;
      return `<li><div class="rc-main"><div class="rt">${esc(t)}</div><div class="rd">${esc(r.ingested_at || "")}</div></div>${btn}</li>`;
    };
    const head = recent.slice(0, 3).map(row).join("");
    return `<section class="ov-recent"><div class="ov-panel-head"><h5>最近入库</h5><button class="rc-expand" id="rc-expand">查看全部</button></div>
      <ul class="recent-list">${head}</ul></section>`;
  }

  function rowsOf(value, keyName) {
    if (Array.isArray(value)) return value;
    if (value && typeof value === "object") return Object.keys(value).map((k) => {
      const v = value[k]; return (v && typeof v === "object") ? Object.assign({ [keyName]: k }, v) : { [keyName]: k, n: v };
    });
    return [];
  }
  function gradingBandRows(d, go) {
    absorbBandNames(go);
    let raw = rowsOf(go.bands || go.by_band || go.band_counts, "band");
    if (!raw.length) raw = mergeProvisionalTier(d.by_tier || []);
    const map = {};
    raw.forEach((x) => {
      const band = x.band || LEGACY_BAND[x.tier] || "normal";
      map[band] = { band, n: Number(x.n != null ? x.n : (x.count || 0)), ratio: x.ratio,
                    weight: x.weight, band_name: x.band_name || x.name || x.tier };
      if (map[band].band_name) DISC.bandNames[band] = map[band].band_name;
    });
    absorbBandNames({ band_names: DISC.bandNames });
    const total = Number(go.total || Object.values(map).reduce((s, x) => s + x.n, 0) || ((d.coverage || {}).meta_indexed) || 0);
    return BAND_ORDER.map((band) => Object.assign({ band, n: 0, ratio: 0, weight: null }, map[band] || {}, {
      band_name: (map[band] && map[band].band_name) || DISC.bandNames[band] || BAND_STANDARD[band], total,
    }));
  }
  function gradingMappings(go) { return rowsOf(go.mappings || go.mapping_rows || go.mapping, "mapping_id"); }
  function gradingBreakdown(go, kind) {
    if (kind === "type") return rowsOf(go.source_types || go.by_source_type || go.type_counts, "source_type");
    return rowsOf(go.objective_labels || go.by_objective_label || go.label_counts || go.labels, "objective_label");
  }
  function gradingOverviewCard(d) {
    const go = d.grading_overview || {};
    const bands = gradingBandRows(d, go);
    const bandRows = bands.map((x) => {
      const pct = x.ratio != null ? Math.round(Number(x.ratio) * (Number(x.ratio) <= 1 ? 100 : 1)) : (x.total ? Math.round(x.n / x.total * 100) : 0);
      return `<div class="ov-band-row"><span class="ov-band-dot" style="background:${BAND_COLOR[x.band]}"></span>` +
        `<span class="ov-band-name">${esc(x.band_name)}</span><span class="ov-band-track"><i style="width:${Math.max(0, Math.min(100, pct))}%;background:${BAND_COLOR[x.band]}"></i></span>` +
        `<b>${num(x.n)}</b><small>${pct}%</small></div>`;
    }).join("");
    const typeRows = gradingBreakdown(go, "type");
    const labelRows = gradingBreakdown(go, "label");
    const labelLimit = 12;
    const breakdown = (typeRows.length || labelRows.length) ? `<div class="dcard span2 ov-breakdown">
      <div class="ov-break-head"><div class="ov-break-title"><h4>文献构成</h4><div class="ov-break-tabs" role="tablist"><button class="active" data-break-kind="type" role="tab" aria-selected="true">按文献性质</button><button data-break-kind="label" role="tab" aria-selected="false">按客观标签</button></div></div><small>点击项目查看文献</small></div>
      <div class="ov-break-panel active" data-break-panel="type"><div class="ov-chips">${typeRows.map((x) => {
        const type = x.source_type || x.id || x.key || "", name = x.source_type_name || x.name || x.label || type || "其他";
        const body = `${esc(name)} <b>${num(x.n != null ? x.n : x.count || 0)}</b>`;
        return type ? `<button class="ov-chip ov-type-chip" data-source-type="${esc(type)}" data-source-name="${esc(name)}">${body}</button>`
          : `<span class="ov-chip">${body}</span>`;
      }).join("") || `<span class="hint">暂无统计</span>`}</div></div>
      <div class="ov-break-panel" data-break-panel="label"><div class="ov-chips">${labelRows.map((x, i) => {
        const label = x.objective_label || x.label || x.name || "来源";
        return `<button class="ov-chip ov-label-chip${i >= labelLimit ? " ov-chip-extra" : ""}" data-objective-label="${esc(label)}">${esc(label)} <b>${num(x.n != null ? x.n : x.count || 0)}</b></button>`;
      }).join("") || `<span class="hint">暂无统计</span>`}${labelRows.length > labelLimit ? `<button class="ov-break-more" data-extra-count="${labelRows.length - labelLimit}">展开其余 ${num(labelRows.length - labelLimit)} 项</button>` : ""}</div></div></div>` : "";
    const mappings = gradingMappings(go);
    const customized = mappings.filter((x) => x.customized);
    const customCount = customized.length;
    const mappingRows = (rows) => rows.map((x) => {
        const id = x.mapping_id || x.id || x.key || "", band = x.band || LEGACY_BAND[x.band_name || x.tier] || "normal";
        const defaultBand = x.default_band || band;
        const defaultName = x.default_band_name || DISC.bandNames[defaultBand] || BAND_STANDARD[defaultBand];
        const title = x.objective_label || x.source_type_name || x.label || x.name || id;
        const detail = x.description || x.rule || x.source || "";
        const select = x.editable ? `<select class="ov-map-select" data-mapping-id="${esc(id)}" data-default-band="${esc(defaultBand)}" data-saved-value="${esc(band)}" data-update-url="${esc(x.update_url || "/grading/mapping")}">` +
          BAND_ORDER.map((b) => `<option value="${b}"${b === band ? " selected" : ""}>${esc(DISC.bandNames[b] || BAND_STANDARD[b])}</option>`).join("") + `</select>`
          : `<span class="ov-map-band" style="color:${BAND_COLOR[band]}">${esc(x.band_name || DISC.bandNames[band] || BAND_STANDARD[band])}</span>`;
        return `<div class="ov-map-row${x.customized ? " customized" : ""}"><div><b>${esc(title)}</b>${x.customized ? `<small>默认：${esc(defaultName)}</small>` : (detail ? `<small>${esc(detail)}</small>` : "")}</div>${select}</div>`;
      }).join("");
    const customRows = customized.map((x) => {
      const id = x.mapping_id || x.id || x.key || "", band = x.band || "normal", defaultBand = x.default_band || "normal";
      const title = x.objective_label || x.source_type_name || x.label || x.name || id;
      const currentName = x.band_name || DISC.bandNames[band] || BAND_STANDARD[band];
      const defaultName = x.default_band_name || DISC.bandNames[defaultBand] || BAND_STANDARD[defaultBand];
      return `<div class="ov-custom-row"><div><b>${esc(title)}</b><small>当前：${esc(currentName)} · 默认：${esc(defaultName)}</small></div><button class="ov-map-reset" data-mapping-id="${esc(id)}">恢复此项</button></div>`;
    }).join("");
    const catalogMappings = mappings.filter((x) => String(x.mapping_id || x.id || "").startsWith("label:"));
    const natureMappings = mappings.filter((x) => !String(x.mapping_id || x.id || "").startsWith("label:"));
    const mappingCard = mappings.length ? `<details class="dcard span2 ov-mapping"><summary><span><b>评价规则</b><small>当前：${esc(DISC.name || "当前学科")} · ${customCount ? `<mark>${num(customCount)} 项已修改</mark>` : "使用当前预设"}</small></span><em>展开调整</em></summary>
      <div class="ov-mapping-body"><div class="ov-map-intro"><span>调整后评价、总览和检索排序会即时更新；无需重建索引，客观标签不变。</span>${customCount ? `<button id="ov-map-reset-all">恢复全部默认</button>` : ""}</div>
      ${customCount ? `<h5 class="ov-custom-title">已修改（${num(customCount)}）</h5><div class="ov-map-custom">${customRows}</div>` : ""}
      <h5>期刊与目录</h5><div class="ov-map-table">${mappingRows(catalogMappings)}</div>
      <h5>其他文献性质</h5><div class="ov-map-table">${mappingRows(natureMappings)}</div>
      <div class="ov-map-msg hint" aria-live="polite"></div></div></details>` : "";
    return { bandRows, breakdown, mappingCard };
  }

  // 概览卡：四档来源评价 + 全类型/目录明细。
  function overviewCard(d) {
    const cov = d.coverage || {};
    const zh = (d.by_lang || []).find((x) => /中/.test(x.lang)) || {};
    const wai = (d.by_lang || []).find((x) => /外/.test(x.lang)) || {};
    const compose = `<div class="ov-compose">
      <span><b>${num(cov.meta_indexed)}</b>总篇</span>
      <span><b>${num(withFulltext(cov))}</b>有全文附件</span>
      ${zh.n ? `<span><b>${num(zh.n)}</b>中文</span>` : ""}
      ${wai.n ? `<span><b>${num(wai.n)}</b>外文</span>` : ""}
    </div>`;
    // F3：显示当前锁定学科（中文名）+ 修改入口
    const go = d.grading_overview || {};
    DISC.id = go.discipline || d.grading_discipline || DISC.id;
    DISC.name = go.discipline_name || d.grading_discipline_name || d.grading_discipline || DISC.name || "法学";
    DISC.notice = go.notice || go.discipline_notice || d.grading_notice || DISC.notice || "";
    const discName = DISC.name;
    const discLine = `<div class="ov-disc"><span>当前学科：<b>${esc(discName)}</b> · <a id="ov-change-disc" class="dh-link">修改</a></span>${DISC.notice ? `<small>${esc(DISC.notice)}</small>` : ""}</div>`;
    const detail = gradingOverviewCard(d);
    let distBody = detail.bandRows;
    if (d.grading_pending) {
      distBody = `<p class="ov-dist-pending">分级分布计算中，请稍候…</p>`;
    }
    return `<div class="dcard span2 ov-library"><div class="ov-library-head"><div><h4>文库概况</h4>${compose}</div>${discLine}</div>
      <div class="ov-library-grid"><section class="ov-rating"><div class="ov-panel-head"><h5>评价分布</h5><small>按当前学科计算</small></div>${distBody}</section>${recentSection(d.recent)}</div></div>` +
      detail.breakdown + detail.mappingCard;
  }

  // 底部：横板四步示意图（替代原密排功能点文字；一页显示、自适应宽度）
  function agentGuideCard() {
    const step = (cls, badge, emoji, title, desc) =>
      `<div class="step ${cls}"><span class="ht-badge">${badge}</span><div class="step-top"></div>
        <div class="st-emoji">${emoji}</div><div class="st-title">${title}</div>
        <div class="st-desc">${desc}</div></div>`;
    const arrow = `<div class="arrow"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h13M13 6l6 6-6 6"/></svg></div>`;
    return `<div class="howto">
      <div class="ht-head">
        <img class="ht-mascot" src="/static/PaperPiggy.png" alt="PaperPiggy">
        <div class="ht-head-txt">
          <div class="ht-h">四步，把小猪养成你的<span class="hl">专属知识库</span></div>
          <div class="ht-sub">越喂越聪明 —— 喂文献进去，它替你精读、检索、写综述</div>
        </div>
        <button class="primary-btn ht-cta" id="dg-go">怎么接入 AI 助手 →</button>
      </div>
      <div class="flow">
        ${step("s1", "1", "📥", "喂饱小猪 · 深索＋摘要", "先把全文附件<b>深索</b>成可读小段，再配<b>检索摘要</b>帮助准确命中文献；都能按需筛选、放后台慢慢完成。")}
        ${arrow}
          ${step("s2", "2", "🔍", "秒级检索", "抛一个研究问题，全库<b>秒找</b>相关文献与原文段落，按来源评价权重排序、回溯到页码/章节/段落。")}
        ${arrow}
        ${step("s3", "3", "🤖", "Agent 驱动", "把库接进 <b>Claude Code / Codex</b>，让 AI 替你查库、读原文位置 —— 尤其半自动研究助手，边问边写。")}
        ${arrow}
        ${step("s4", "4", "📚", "定时养库", "把问答与综述沉淀成 <b>wiki</b>、定期维护综述库 —— 知识越用越厚，复用越来越省。")}
      </div>
      <div class="ht-foot"><span class="pig">🐷</span> 喂得越勤，小猪越懂你 —— 让 PaperPiggy 成为你的专属知识库 ✨</div>
    </div>`;
  }

  // 库总览·新手指引：只在库总览页启用左上角小猪；首次显示一次锚定提示。
  function openHomeGuide() {
    const g = $("#home-guide"); if (!g) return;
    const vis = $("#hg-visual");
    if (vis && !vis.dataset.filled) {           // 首次打开时填四步可视化并接线其 CTA
      vis.innerHTML = agentGuideCard();
      vis.dataset.filled = "1";
      const go = $("#dg-go"); if (go) go.addEventListener("click", () => { closeHomeGuide(); switchTab("agent"); });
    }
    g.hidden = false;
    const body = g.querySelector(".ag-guide-body"); if (body) body.scrollTop = 0;
  }
  function closeHomeGuide() { const g = $("#home-guide"); if (g) g.hidden = true; }
  function syncHomeGuideTrigger(tab) {
    const opener = $("#brand-guide"), tip = $("#brand-guide-tip");
    if (!opener) return;
    const active = tab === "dashboard";
    opener.classList.toggle("is-inactive", !active);
    opener.setAttribute("aria-disabled", active ? "false" : "true");
    opener.setAttribute("aria-label", active ? "打开新手指引" : "PaperPiggy");
    opener.title = active ? "打开新手指引" : "";
    if (!active && tip) tip.hidden = true;
  }
  (function wireHomeGuide() {
    const opener = $("#brand-guide"), tip = $("#brand-guide-tip"), tipClose = $("#brand-guide-tip-close");
    const tipKey = "localkb.brandGuideTipSeen";
    const dismissTip = () => {
      if (tip) tip.hidden = true;
      try { localStorage.setItem(tipKey, "1"); } catch (_) {}
    };
    if (opener) opener.addEventListener("click", () => {
      if (opener.getAttribute("aria-disabled") === "true") return;
      dismissTip(); openHomeGuide();
    });
    if (tipClose) tipClose.addEventListener("click", dismissTip);
    syncHomeGuideTrigger($(".tab.active")?.dataset.tab || "dashboard");
    try {
      if (tip && opener?.getAttribute("aria-disabled") !== "true" && localStorage.getItem(tipKey) !== "1") tip.hidden = false;
    } catch (_) { if (tip && opener?.getAttribute("aria-disabled") !== "true") tip.hidden = false; }
    const c = $("#home-guide-close"); if (c) c.addEventListener("click", closeHomeGuide);
    const t2a = $("#hg-to-agent"); if (t2a) t2a.addEventListener("click", () => { closeHomeGuide(); switchTab("agent"); });
    document.addEventListener("keydown", (e) => { const g = $("#home-guide"); if (e.key === "Escape" && g && !g.hidden) closeHomeGuide(); });
  })();

  // 全文深索进度卡（读 /index/status；点击跳到浏览 tab 的「仅题录」筛选）
  function deepProgressCard(st) {
    if (!st) return "";
    const withPdf = withFulltext(st), deep = st.deep_done || 0;
    if (withPdf <= 0) {
      return `<div class="dcard span2 deep-prog">
        <h4>深索进度</h4>
        <p class="dcard-sub">有全文附件的文献拆成可检索的小段后，回答可回溯到页码、章节、段落或行号</p>
        <div class="hbar deep-prog-bar"><span class="track"><span class="fill" style="width:0%;background:#16a085"></span></span><span class="val">0%</span></div>
        <p class="deep-prog-txt">暂无可深索文献</p></div>`;
    }
    const xc = extractCounts(st), blocked = xc.blocked;
    const processed = Math.min(withPdf, deep + blocked);
    const rawPct = (processed / withPdf) * 100;
    const barPct = processed > 0 ? Math.max(3, Math.round(rawPct)) : 0;   // 有成果就留一条可见的进度条，避免 2/1443 显示 0%
    const pctLabel = (processed > 0 && rawPct < 1) ? "<1%" : Math.round(rawPct) + "%";
    const remain = Math.max(0, withPdf - deep - blocked);
    const clickable = remain > 0;
    const bar = `<span class="track"><span class="fill" style="width:${barPct}%;background:#16a085"></span></span>`;
    const txt = (remain > 0
      ? `已深索 <b>${num(deep)}</b> / 有全文附件 ${num(withPdf)} 篇，还有 ${num(remain)} 篇可深索`
      : (blocked > 0
          ? `已深索 <b>${num(deep)}</b> / 有全文附件 ${num(withPdf)} 篇；可处理正文已完成（${extractCountsText(st)}）`
          : `已深索 <b>${num(deep)}</b> / 有全文附件 ${num(withPdf)} 篇，已全部深索完成 ✓`)) + sacFrag(st);
    // 查看「已深索了哪些」（跳到浏览的「已深索」筛选）。整卡可点→浏览挑未深索去深索。
    const seeLink = deep > 0 ? `<span class="deep-prog-see" id="deep-prog-see">查看已深索 ${num(deep)} 篇 →</span>` : "";
    return `<div class="dcard span2 deep-prog${clickable ? " clickable" : ""}"${clickable ? ' id="deep-prog-card"' : ""}>
      <h4>深索进度</h4>
      <p class="dcard-sub">只有<b>深索过</b>的文献才能被精读、定位引用、跨篇综合；未深索的仅题录可搜。</p>
      <div class="hbar deep-prog-bar">${bar}<span class="val">${pctLabel}</span></div>
      <p class="deep-prog-txt">${txt}${seeLink}</p></div>`;
  }

  function _goDeepBrowse(filter) {
    BR.deepFilter = filter; BR.sourceType = ""; BR.objectiveLabel = ""; BR.query = ""; BR.sort = "recommend";
    setLibrarySearchMode("metadata", false);
    const q = $("#q"); if (q) q.value = "";
    const df = $("#bl-deep-filter"); if (df) df.value = filter;
    const tf = $("#bl-type-filter"); if (tf) tf.value = "";
    const bs = $("#bl-sort"); if (bs) bs.value = "recommend";
    const firstBrowse = !browseLoaded;
    switchTab("browse");
    if (!firstBrowse) selectCollection(null, "全部", null, true);
  }
  function renderDashboard(d, status) {
    const cov = d.coverage || {};
    const health = d.health || {};
    const st = status || {};
    const withPdf = withFulltext(st), deep = st.deep_done || 0;
    const xc = extractCounts(st), blocked = xc.blocked;
    const remain = Math.max(0, withPdf - deep - blocked);
    const processed = Math.min(withPdf, deep + blocked);
    const rawPct = withPdf ? (processed / withPdf) * 100 : 0;
    const barPct = processed > 0 ? Math.max(3, Math.round(rawPct)) : 0;
    const pctLabel = withPdf === 0 ? "—" : (processed > 0 && rawPct < 1 ? "<1%" : Math.round(rawPct) + "%");
    // 第②步 检索摘要 的进度（分母＝已深索数，摘要只对已深索的篇有意义）
    const sac = st.sac_done || 0;
    const sacInvalid = st.sac_invalid || 0;
    const sacMissing = st.sac_missing == null ? Math.max(0, deep - sac - sacInvalid) : st.sac_missing;
    const sacRawPct = deep > 0 ? (sac / deep) * 100 : 0;
    const sacBarPct = sac > 0 ? Math.max(3, Math.round(sacRawPct)) : 0;
    const sacPctLabel = deep === 0 ? "—" : (sac > 0 && sacRawPct < 1 ? "<1%" : Math.round(sacRawPct) + "%");
    const sacGap = Math.max(0, deep - sac);
    const bf = st.sac_backfill || {};
    // 概览条（深色）：一句话 + 数字 + 知识库建设两步（① 深索 / ② 检索摘要），各一条独立进度条
    const header = `<div class="dash-hero">
      <div class="dh-left">
        <div class="dh-title">${esc(health.one_liner || "知识库总览")}</div>
        <div class="dh-sub">题录 ${num(cov.meta_indexed)} 篇 · 有全文附件 ${num(withFulltext(cov))} 篇 · 已深索 ${num(cov.deep_indexed)} 篇</div>
        <button class="dash-refresh" data-act="refresh-dash" title="重新扫描进度与最近入库（后台深索/生成摘要在跑、或外部 AI 助手改了库，点这里就能看到最新，不用重开应用）">🔄 刷新</button>
      </div>
      <div class="dh-deep">
        <div class="dh-step">
          <div class="dh-deep-h"><b>① 深索</b><span>${pctLabel}</span></div>
          <div class="hbar dh-bar"><span class="track"><span class="fill" style="width:${barPct}%;background:#7ee0b8"></span></span></div>
          <div class="dh-deep-txt">${withPdf === 0 ? "暂无可深索文献。" : (remain > 0
              ? `把全文附件拆成可检索的小段，回答才能回溯到原文位置。已深索 <b>${num(deep)}</b>/${num(withPdf)} 篇，还有 ${num(remain)} 篇。`
               : (blocked > 0
                   ? `可处理正文已完成 ✓ —— ${extractCountsText(st)}。修好全文附件后可在「深索详情」重试；PDF OCR 全程本地运行。`
                  : `已全部深索完成 ✓`))}
            ${deep > 0 ? `<a class="dh-link" id="dash-see-deep">查看已深索 ${num(deep)} 篇 →</a>` : ""}
            ${remain > 0 ? `<a class="dh-link" id="dash-go-deep">深索全部未深索文献 →</a>` : ""}
            ${withPdf > 0 ? `<a class="dh-link dash-deep-detail" id="dash-deep-detail">深索详情 / 暂停 →</a>` : ""}</div>
        </div>
        <div class="dh-step">
          <div class="dh-deep-h"><b>② 检索摘要</b><span>${sacPctLabel}</span></div>
          <div class="hbar dh-bar"><span class="track"><span class="fill" style="width:${sacBarPct}%;background:#7ec8e0"></span></span></div>
          <div class="dh-deep-txt">${deep === 0
              ? "先完成 ① 深索，才能给文献配检索摘要。"
              : (bf.running
                  ? `<span class="sac-bf-run">🧬 ${esc(bf.phase || "生成中")} ${num(bf.done || 0)}/${num(bf.total || 0)}…</span>`
                  : (sacGap > 0
                       ? `有效摘要 <b>${num(sac)}</b>/${num(deep)} 篇；${[sacInvalid ? `<b>${num(sacInvalid)}</b> 篇异常` : "", sacMissing ? `<b>${num(sacMissing)}</b> 篇缺失` : ""].filter(Boolean).join("，")}。异常摘要不计完成；修复并重嵌入后才会替换旧前缀。 <a class="sac-bf-btn" data-act="sac-backfill" role="button" tabindex="0" title="修复异常摘要、补生成缺失摘要并重嵌入，需 API key，只处理这些篇，可后台跑。">🧬 修复 / 补生成摘要</a>`
                       : `全部摘要已通过质量检查 ✓`))}</div>
        </div>
        ${(remain > 0 || sacGap > 0) ? `<div class="dh-deep-note">① 深索＝把全文附件拆成可检索小段（能精读、定位原文、跨篇综合）；② 检索摘要＝再给每篇配段 AI 摘要当检索前缀（更易被命中）。都可放后台慢慢跑，不影响使用。</div>` : ""}
      </div></div>`;
    // EN-F1：综述库体检角标——/stats 带 wiki_lint（后端读 data/state/wiki_lint.json；没有该键=没体检过，不显示）。
    // gist 点名 drift（综述与库脱节）是头号失败模式——有待理顺项时在首页给一条显眼入口，点击直达体检面板
    const wl = d.wiki_lint || null;
    const lintLine = (wl && wl.issues > 0)
      ? `<div class="dash-lint" id="dash-lint" role="button" tabindex="0" title="上次体检：${esc(wl.checked_at || "未知时间")}。点击去综述库查看体检详情">🩺 综述库有 <b>${num(wl.issues)}</b> 处待理顺（孤立页 / 过时页 / 断链等）→</div>`
      : "";
    $("#dash").innerHTML = header + lintLine
      + `<div class="dash-grid dash-2col">${overviewCard(d)}</div>`;
    // 事件
    const seeD = $("#dash-see-deep"); if (seeD) seeD.addEventListener("click", () => _goDeepBrowse("yes"));
    const goD = $("#dash-go-deep"); if (goD) goD.addEventListener("click", async () => {
      goD.textContent = "正在开始全量深索…";
      try { const r = await jpost("/index/deep", { scope: "all" });
        if (r && r.ok === false) { goD.textContent = "已有任务在跑，稍后再试"; }
        else { poll(); goD.textContent = "已开始后台深索，进度见顶部 ✓"; }
      } catch (e) { goD.textContent = "启动失败：" + (e.message || e); }
    });
    const ddD = $("#dash-deep-detail"); if (ddD) ddD.addEventListener("click", openDeepPanel);   // K1：库总览深索卡 → 深索详情面板
    // EN-F1：点体检角标 → 切到综述库并自动展开体检面板（runLint 是开关式：仅在收起时调，避免把已开的面板反手关掉）
    const dlint = $("#dash-lint");
    if (dlint) {
      const goLint = () => {
        switchTab("wiki");
        const lp = $("#wk-lint-panel");
        if (lp && lp.hidden) runLint();
      };
      dlint.addEventListener("click", goLint);
      dlint.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); goLint(); } });
    }
    const chD = $("#ov-change-disc"); if (chD) chD.addEventListener("click", (e) => { e.preventDefault(); openSettings("sec-discipline"); });  // F3：修改学科
    $$(".ov-break-tabs button").forEach((tab) => tab.addEventListener("click", () => {
      const kind = tab.dataset.breakKind;
      $$(".ov-break-tabs button").forEach((x) => { const on = x === tab; x.classList.toggle("active", on); x.setAttribute("aria-selected", on ? "true" : "false"); });
      $$(".ov-break-panel").forEach((x) => x.classList.toggle("active", x.dataset.breakPanel === kind));
    }));
    $$(".ov-break-more").forEach((b) => b.addEventListener("click", () => {
      const chips = b.closest(".ov-chips"), expanded = chips.classList.toggle("expanded");
      b.textContent = expanded ? "收起" : `展开其余 ${b.dataset.extraCount || ""} 项`;
    }));
    $$(".ov-type-chip").forEach((b) => b.addEventListener("click", () => {
      BR.sourceType = b.dataset.sourceType || ""; BR.objectiveLabel = ""; BR.query = "";
      setLibrarySearchMode("metadata", false);
      const q = $("#q"); if (q) q.value = "";
      const ts = $("#bl-type-filter"); if (ts) ts.value = BR.sourceType;
      BR.scope = { type: "all", id: null, name: b.dataset.sourceName || "全部" };
      switchTab("browse");
      if (browseLoaded) applyScope("all", null, b.dataset.sourceName || "全部", null, true);
    }));
    $$(".ov-label-chip").forEach((b) => b.addEventListener("click", () => {
      BR.objectiveLabel = b.dataset.objectiveLabel || ""; BR.sourceType = ""; BR.query = "";
      setLibrarySearchMode("metadata", false);
      const q = $("#q"); if (q) q.value = "";
      const ts = $("#bl-type-filter"); if (ts) ts.value = "";
      BR.scope = { type: "all", id: null, name: "全部" };
      switchTab("browse");
      if (browseLoaded) applyScope("all", null, "全部", null, true);
    }));
    $$(".ov-map-select").forEach((sel) => sel.addEventListener("change", async () => {
      const old = sel.dataset.savedValue || "";
      const msg = sel.closest(".ov-mapping")?.querySelector(".ov-map-msg");
      sel.disabled = true; if (msg) msg.textContent = "正在保存映射…";
      try {
        await jpost(sel.dataset.updateUrl || "/grading/mapping", { mapping_id: sel.dataset.mappingId, band: sel.value });
        sel.dataset.savedValue = sel.value;
        if (msg) msg.textContent = "评价与检索排序已更新，无需重建索引；客观标签保持不变。";
        if (browseLoaded) loadPapers();
        setTimeout(() => loadDashboard("silent"), 300);
      } catch (e) {
        if (old) sel.value = old;
        if (msg) msg.textContent = "保存失败：" + (e.message || e);
      } finally { sel.disabled = false; }
    }));
    $$(".ov-map-reset").forEach((b) => b.addEventListener("click", async () => {
      const msg = b.closest(".ov-mapping")?.querySelector(".ov-map-msg");
      b.disabled = true; if (msg) msg.textContent = "正在恢复默认…";
      try {
        await jpost("/grading/mapping", { mapping_id: b.dataset.mappingId, band: null });
        if (browseLoaded) loadPapers();
        await loadDashboard("silent");
        flashToast("已恢复这一项的默认评价；无需重建索引。");
      } catch (e) {
        if (msg) msg.textContent = "恢复失败：" + (e.message || e);
        b.disabled = false;
      }
    }));
    const resetAll = $("#ov-map-reset-all");
    if (resetAll) resetAll.addEventListener("click", async () => {
      const ok = await uiConfirm("将恢复当前学科的全部出厂评价规则。单篇手动改档、客观标签和索引都不会改变。", {
        title: "恢复全部默认评价", okText: "恢复全部默认", danger: true,
      });
      if (!ok) return;
      resetAll.disabled = true;
      try {
        await jpost("/grading/mapping/reset", {});
        if (browseLoaded) loadPapers();
        await loadDashboard("silent");
        flashToast("当前学科已恢复全部默认评价；无需重建索引。");
      } catch (e) {
        resetAll.disabled = false;
        const msg = resetAll.closest(".ov-mapping")?.querySelector(".ov-map-msg");
        if (msg) msg.textContent = "恢复失败：" + (e.message || e);
      }
    });
    const exp = $("#rc-expand"); if (exp) exp.addEventListener("click", _goBrowseRecent);
    // R10：库总览「最近入库」的深索按钮自己 try/finally 恢复文字并用浮层反馈（不写进隐藏的浏览页 #bl-msg）
    $$(".rc-deep").forEach((b) => b.addEventListener("click", async () => {
      const old = b.textContent; b.disabled = true; b.textContent = "…";
      try {
        const r = await jpost("/index/deep", { scope: "keys:" + b.dataset.key });
        if (r && r.ok === false) { flashToast("已有任务在跑，稍后再试。"); }
        else { localStorage.removeItem("localkb.deepDismissed"); flashToast("已开始后台深索这篇，进度见顶部。"); poll(); }
      } catch (e) { flashToast("启动深索失败：" + (e.message || e)); }
      finally { b.disabled = false; b.textContent = old; }
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
      updateExamplesFromStats(d);   // UX13：顺手用最近入库标题刷新检索示例词（复用本次 /stats，不另打接口）
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
               query: "", deepFilter: "", sourceType: "", objectiveLabel: "", sort: "ingested", papers: [], selected: new Set(),
               cats: [], reqSeq: 0, topicIv: null, total: 0 };   // cats：缓存 /kb/categories；reqSeq：B5 守卫；topicIv：R12 主题轮询句柄；total：W1 分页总数

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

  // 换收藏夹/主题时默认把文献筛选清回「全部」，否则上次的 OCR/摘要等筛选会继续收窄新范围，
  // 让人误以为该收藏夹/主题只有那几篇。库总览的「查看已深索/去深索」入口用 keepFilter 保留筛选。
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
    if (LIBRARY_MODE === "semantic") {
      if ($("#q").value.trim()) doSearch();
      else $("#s-msg").textContent = `当前范围：${BR.scope.name}。输入研究问题开始检索。`;
    } else loadPapers();
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
          ? `<div class="bt-hint">还没有 AI 主题。点上方「🔄 生成主题」，让 AI 把所有文献自动归类。</div>`
          : `<div class="bt-hint">AI 主题按向量把所有文献自动归类，需先建语义/深索索引。
             <a class="ag-link" id="bt-topics-goengine">去「设置 → 检索 → 检索引擎」</a></div>`;
        const g = $("#bt-topics-goengine"); if (g) g.addEventListener("click", () => openSettings("sec-engine"));
        return;
      }
      topics.forEach((t) => {
        const chip = document.createElement("span");
        chip.className = "bt-topic";
        chip.title = t.name;
        chip.innerHTML = `<span class="tp-nm">${esc(t.name)}</span><span class="tp-cnt" title="已深索/总篇数">${num(t.deep || 0)}/${num(t.size)}</span>`;
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
    // 副本#8：「全部文献」后显示总篇数（原为收藏夹数「N 夹」），两种数据源模式都设
    jget("/health").then((h) => {
      const n = h && (h.papers != null ? h.papers : h.n);
      // F10：全部文献显示 已深索/总数（与图例「数字＝已深索/总篇数」一致）
      $("#bt-all-cnt").textContent = n != null ? (num(h.deep || 0) + "/" + num(n) + " 篇") : "";
    }).catch(() => {});
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
      const box = $("#bt-tree"); box.innerHTML = "";
      (d.tree || []).forEach((n) => box.appendChild(treeNodeEl(n, 0)));
      if (!(d.tree || []).length) box.innerHTML = `<div class="bt-loading">（无收藏夹）</div>`;
      // 仅导入有 PDF 时提示：分类篇数为 Zotero 原始数量，无 PDF 的未进库
      if (APP.importOnlyPdf && !$("#bt-onlypdf-note")) {
        const note = document.createElement("div");
        note.id = "bt-onlypdf-note"; note.className = "bt-hint";
        note.textContent = "已设为「只导入有全文附件」：下面分类的篇数是 Zotero 原始数量，没有受支持全文附件的条目未进库。";
        box.parentNode.insertBefore(note, box);
      }
      browseLoaded = true;   // 仅收藏夹加载成功才置位；失败保持 false，切回自动重试
    } catch (e) {
      $("#bt-tree").innerHTML = `<div class="bt-loading">收藏夹加载失败：${esc(e.message)}</div>`;
    }
    loadPapers(); // 默认加载全库推荐
  }

  // 🔄 文献页整体刷新：重取左树/总数/主题/知识库分类 + 按当前范围重拉列表。
  //   覆盖「纯新增入库 / 外部 agent 建库」这类后台轮询扳机（深索/SAC 计数）照不到、原本要切页或重开才刷的场景。
  //   刻意不置 browseLoaded=false（不整页重建，保留当前范围/选择），只重取会 stale 的那几块。
  async function refreshBrowse() {
    loadTopics();
    loadKbCats();
    jget("/health").then((h) => {
      const n = h && (h.papers != null ? h.papers : h.n);
      $("#bt-all-cnt").textContent = n != null ? (num(h.deep || 0) + "/" + num(n) + " 篇") : "";
    }).catch(() => {});
    try {
      const src = await ensureSource();
      if (src !== "folder") {
        const d = await jget("/categories");
        const box = $("#bt-tree");
        if (box) {
          box.innerHTML = "";
          (d.tree || []).forEach((n) => box.appendChild(treeNodeEl(n, 0)));
          if (!(d.tree || []).length) box.innerHTML = `<div class="bt-loading">（无收藏夹）</div>`;
        }
      }
    } catch (e) { /* 左树刷新失败不阻断列表刷新 */ }
    await loadPapers();   // 按当前 BR.scope 重拉列表
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
      // BF12：裸 fetch 对 4xx 不抛错，重名/非法名会被静默吞掉——改走 jsend 读后端人话，成功才刷新
      try { await jsend("/kb/categories/" + encodeURIComponent(c.id), "PATCH", { name }); loadKbCats(); }
      catch (e) { toast("重命名失败：" + e.message); }
    });
    m.querySelector(".ctx-del").addEventListener("click", async () => {
      m.hidden = true;
      if (!(await uiConfirm("只删这个分类清单，不会删除文献，也不会撤销已建好的深索。",
            { title: `删除分类「${c.name}」？`, okText: "删除", danger: true }))) return;
      try {
        // BF12：同重命名——jsend 抛出真实失败原因，成功才切范围/刷新列表
        await jsend("/kb/categories/" + encodeURIComponent(c.id), "DELETE");
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
      if ((r.no_pdf || []).length) bits.push(`${num(r.no_pdf.length)} 篇无全文附件，仅题录、不可深读`);
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
    // 手动分类默认展开；AI 分类与 Zotero 分类默认收起。用户调整后记住选择。
    const applyCollapse = () => {
      const st = safeParse(localStorage.getItem("localkb.btCollapse"), {});
      const defaults = { kbc: false, topics: true, zot: true };
      // 各分区约定：#bt-{sec}-body（topics / kbc / zot）——按 caret 的 data-sec 统一收放
      $$(".bt-caret2").forEach((c) => {
        const body = $("#bt-" + c.dataset.sec + "-body");
        const collapsed = st[c.dataset.sec] == null ? !!defaults[c.dataset.sec] : !!st[c.dataset.sec];
        if (body) body.hidden = collapsed;
        c.textContent = collapsed ? "▸" : "▾";
      });
    };
    $$(".bt-caret2").forEach((c) => c.addEventListener("click", (e) => {
      e.stopPropagation();
      const st = safeParse(localStorage.getItem("localkb.btCollapse"), {});
      const defaults = { kbc: false, topics: true, zot: true };
      const collapsed = st[c.dataset.sec] == null ? !!defaults[c.dataset.sec] : !!st[c.dataset.sec];
      st[c.dataset.sec] = !collapsed;
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
      if (BR.topicIv) { clearInterval(BR.topicIv); BR.topicIv = null; }   // R12：先清上一次泄漏的轮询
      gen.disabled = true; const old = gen.textContent; gen.textContent = "归类中…";
      const restore = () => { gen.disabled = false; gen.textContent = old; };
      try {
        const r = await jpost("/topics/rebuild", {});
        if (!r.ok) { toast(r.msg || "无法生成主题"); restore(); return; }
        // 轮询完成：加最大次数上限（约 5 分钟），iv 存模块级变量便于离开时清理
        let tries = 0;
        BR.topicIv = setInterval(async () => {
          if (++tries > 120) { clearInterval(BR.topicIv); BR.topicIv = null; restore(); toast("生成主题超时，请稍后重试。"); return; }
          try {
            const s = await jget("/topics/status");
            if (!s.running) { clearInterval(BR.topicIv); BR.topicIv = null; restore(); loadTopics(); toast("AI 主题已更新。"); }
          } catch (e) { clearInterval(BR.topicIv); BR.topicIv = null; restore(); }
        }, 2500);
      } catch (e) { toast("生成主题失败：" + e.message); restore(); }
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
  // UX3：缺 Key 时的引导落点分场景——只丢到设置弹窗第一屏（期刊学科）会让人找不到该填哪。
  // 对话场景：直接去对话页展开「模型设置」折叠区并聚焦 Key 框；其余场景：进设置并滚到「检索引擎」小节锚点。
  function gotoLlmKeySetup(scene) {
    // UX3：非硅基流动服务商的 Key 只在对话页「⚙ 模型设置」里有输入框——不论从哪个场景触发，
    // 都得落到对话页；只有硅基流动（可复用检索引擎 key）才去设置弹窗的检索引擎小节。
    const prov = (cfg().provider || "siliconflow");
    if (scene === "chat" || prov !== "siliconflow") {
      switchTab("chat");
      const det = $("#chat-model"); if (det) det.open = true;
      setTimeout(() => { const k = $("#set-key"); if (k) k.focus(); }, 0);
    } else {
      openSettings("sec-engine");
    }
  }
  function needKey() {   // 非硅基流动且没填 key → 先去配置（硅基流动可复用检索引擎 key，服务端兜底）
    const c = cfg();
    if (!c.api_key && (c.provider || "siliconflow") !== "siliconflow") { gotoLlmKeySetup(); return true; }
    return false;
  }
  // A6：放行对话/综述前，据 /setup/detect 的 api_key_set 判断「是否真有可用 key」。
  // 默认本地后端（离线免 key）用户即便选硅基流动、服务端也没 key 可复用，一发即失败——此处提前拦下、引导配置。
  async function ensureLlmKey(scene) {
    const c = cfg();
    if (c.api_key) return true;   // 本机已填 key
    if ((c.provider || "siliconflow") !== "siliconflow") { gotoLlmKeySetup(scene); return false; }
    try { const d = await jget("/setup/detect"); if (d && d.api_key_set) return true; } catch (e) {}
    gotoLlmKeySetup(scene);
    flashToast("对话/综述需要一个可用的 API Key——硅基流动有免费模型，填一个即可。");
    return false;
  }
  // 零依赖极简 Markdown 渲染：先整体 esc() 防 XSS，再在已转义文本上套基本块级/行内标签（wiki-body-raw-markdown）
  function mdToHtml(md) {
    const src = stripFm(md);
    // EN-F4：行内代码占位符——NUL 字符不可能出现在 esc 后的文本里；用 fromCharCode
    // 构造避免源码里出现裸 NUL 字节（编辑器/差异工具会被它坑）
    const CODE_PH = String.fromCharCode(0);
    const CODE_PH_RE = new RegExp(CODE_PH + "([0-9]+)" + CODE_PH, "g");
    const inline = (t) => {
      let s = esc(t);
      // EN-F4：行内代码先摘走成占位符——`[[x]]` 在 <code> 里必须原样展示不能变链接，
      // bold/italic 也不该动到代码内容（占位符用 NUL 字符，esc 后的文本里不会出现）
      const codes = [];
      s = s.replace(/`([^`]+)`/g, (mm, c) => { codes.push(c); return CODE_PH + (codes.length - 1) + CODE_PH; });
      // EN-F4：[[page-id]] / [[page-id|显示文字]] → 页内链接。整段文本已先 esc()，id/文字里的
      // 引号尖括号都已成实体，放进 data-wk 属性是安全的；mdToHtml 输出走 innerHTML、不能挂
      // 内联 onclick，改用 data-wk + #wiki-body 上的事件委托（见 wireWikiModal）。
      // 放在 bold/italic 之前：[[**x**]] 这类 id 不能把 <b> 标签带进属性值；
      // label 量词用 *（允许 [[x|]] 空文字，与后端 lint 正则口径一致，空则退回显示 id）
      s = s.replace(/\[\[([^\[\]|]+?)(?:\|([^\[\]]*?))?\]\]/g,
        (mm, id, label) => `<a class="wk-link" data-wk="${id.trim()}" title="打开综述页：${id.trim()}">${(label || id).trim()}</a>`);
      s = s.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
        .replace(/(^|[^*])\*([^*]+)\*/g, "$1<i>$2</i>");
      return s.replace(CODE_PH_RE, (mm, i) => `<code>${codes[+i]}</code>`);
    };
    const lines = src.split(/\r?\n/);
    let html = "", listType = "", inCode = false, code = "";
    const closeList = () => { if (listType) { html += "</" + listType + ">"; listType = ""; } };
    for (const raw of lines) {
      if (/^```/.test(raw)) {
        if (inCode) { html += `<pre class="wk-code">${esc(code)}</pre>`; code = ""; inCode = false; }
        else { closeList(); inCode = true; }
        continue;
      }
      if (inCode) { code += raw + "\n"; continue; }
      let m;
      if ((m = raw.match(/^(#{1,6})\s+(.*)$/))) { closeList(); const lv = m[1].length; html += `<h${lv}>${inline(m[2])}</h${lv}>`; }
      else if ((m = raw.match(/^\s*[-*+]\s+(.*)$/))) { if (listType !== "ul") { closeList(); html += "<ul>"; listType = "ul"; } html += `<li>${inline(m[1])}</li>`; }
      else if ((m = raw.match(/^\s*\d+\.\s+(.*)$/))) { if (listType !== "ol") { closeList(); html += "<ol>"; listType = "ol"; } html += `<li>${inline(m[1])}</li>`; }
      else if (/^\s*$/.test(raw)) { closeList(); }
      else { closeList(); html += `<p>${inline(raw)}</p>`; }
    }
    closeList();
    if (inCode) html += `<pre class="wk-code">${esc(code)}</pre>`;
    return html;
  }
  function renderWiki(p) {
    $("#wiki-title").textContent = p.title || "综述页";
    const stale = p.stale ? " · ⚠ 可能已过时" : "";
    // 降级页不显示「模型 fallback(no-key)」这种黑话——它对读者毫无意义，还让人以为是正常综述
    const themeMeta = p.theme ? ` · ${p.theme_source === "manual" ? "◆" : "◇"} ${p.theme}` : "";
    $("#wiki-meta").textContent = p.degraded
      ? `基于 ${(p.sources || []).length} 篇 · 生成于 ${(p.generated_at || "").slice(0, 10)}${themeMeta}${stale}`
      : `本地综述 · 基于 ${(p.sources || []).length} 篇 · 生成于 ${(p.generated_at || "").slice(0, 10)}${themeMeta} · 模型 ${p.generated_by || "未知"}${stale}`;
    // unverified-flag-missing-in-modal：模态里也显示 🤖 未核验 / 📝 我保存的；降级页优先警示
    // W3：agent 写回的页可人工核验——未核验给「标为已核验」按钮，核验后徽章转 ✅（frontmatter 落 verified_at）
    const flag = $("#wiki-flag");
    if (flag) {
      flag.innerHTML = p.degraded
        ? `<span class="wk-flag degraded" title="${esc(p.degraded_reason || "")}">⚠ 证据清单（非 AI 综述）</span>`
        : p.by_agent
        ? (p.verified_at
            ? `<span class="wk-flag ok" title="已于 ${esc(p.verified_at)} 人工核验"><span class="status-dot" aria-hidden="true"></span>已核验</span>`
            : `<span class="wk-flag agent" title="agent 写回、未经人工核验，请核对来源原文"><span class="status-dot" aria-hidden="true"></span>未核验</span>` +
              `<button id="wiki-verify" class="ghost2b wiki-verify" title="确认内容与来源无误后，把这一页标为已人工核验">标为已核验</button>`)
        : `<span class="wk-flag" title="你保存/生成的综述页"><span class="status-dot" aria-hidden="true"></span>我保存的</span>`;
      const vb = $("#wiki-verify");
      if (vb) vb.addEventListener("click", async () => {
        vb.disabled = true; vb.textContent = "标记中…";
        try {
          const r = await jpost("/wiki/verify", { page_id: p.id });
          const at = (r && r.verified_at) || "";
          flag.innerHTML = `<span class="wk-flag ok" title="已于 ${esc(at)} 人工核验"><span class="status-dot" aria-hidden="true"></span>已核验</span>`;
          // 同步列表内存态，让「只看未核验」过滤即刻正确，不用重拉
          (WK.pages || []).forEach((pg) => { if (pg.id === p.id) pg.verified_at = at; });
          if (wikiLoaded) renderWikiList();
        } catch (e) {
          vb.disabled = false; vb.textContent = "标为已核验";
          flashToast("标记核验失败：" + (e.message || e));
        }
      });
    }
    // C3：研究助手产出的资料汇编(digest)/大纲(outline) 可一键导出 Word（有 python-docx 出 .docx，否则降级 .md），
    // 产出可直接拿去写作。用 <a download> 直接触发下载（GET /research/export_docx/{id}）。此前该出口无任何入口=死机制。
    if (flag && !p.degraded && (p.kind === "digest" || p.kind === "outline")) {
      flag.innerHTML += `<a class="wk-flag" style="text-decoration:none;cursor:pointer" ` +
        `href="/research/export_docx/${encodeURIComponent(p.id)}" download ` +
        `title="导出为 Word 文档（含参考文献，可直接拿去写作）">⬇ 导出 Word</a>`;
    }
    // 降级页在正文顶部挂一条醒目横幅，说明它为什么不是综述、怎么交给 Agent 补救
    const banner = p.degraded
      ? `<div class="wk-degraded-banner">⚠ <b>这不是 AI 综述。</b>${esc(p.degraded_reason || "")}。` +
        `<br>请让 Agent 读取原始文献后重写这一页；本页在修复前不参与检索。</div>`
      : "";
    // 旧版存量降级页的斜体尾注里仍写着「模型 fallback(no-key)」这类黑话（新页已不再这样写）。
    // 顶部横幅已把话说清楚，这里只在显示层隐去那一行，不改盘上的 .md。
    const mdSrc = p.degraded
      ? p.markdown.replace(/^\*（[^\n]*(?:fallback\(|no-key|no-hits)[^\n]*）\*\s*$/gm, "")
      : p.markdown;
    $("#wiki-body").innerHTML = banner + mdToHtml(mdSrc);   // 极简 md 渲染（已转义防 XSS）
    renderWikiToc();
    // 参考来源：按 source key 由后端带回统一评价；前端不再从 citation 猜目录或类型。
    // 所以主操作是「📄 打开原文」（直接开 PDF），不是跳去「找相似」。
    const compositionText = (() => {
      const c = p.source_composition;
      if (typeof c === "string") return c;
      const rows = rowsOf(c, "objective_label");
      if (rows.length) return rows.map((x) => `${x.objective_label || x.label || x.name || "来源"} ${num(x.n != null ? x.n : x.count || 0)}`).join(" · ");
      const counts = {};
      (p.sources || []).forEach((s) => { const k = objectiveLabel(s); counts[k] = (counts[k] || 0) + 1; });
      return Object.keys(counts).map((k) => `${k} ${num(counts[k])}`).join(" · ");
    })();
    $("#wiki-sources").innerHTML = (p.sources || []).length
      ? `<div class="ws-h"><span>参考来源</span>${compositionText ? `<small>${esc(compositionText)}</small>` : ""}</div>` +
        p.sources.map((s, i) =>
          `<div class="ws-item" data-key="${esc(s.key || "")}" data-cite="${esc(s.citation || "")}">` +
            `<span class="ws-grade"><span class="badge objective-badge">${esc(objectiveLabel(s))}</span>` +
              `<span class="ws-band" title="${esc(gradingTip(s))}">${esc(bandDisplay(s))}${s.manual ? " · 手动" : ""}</span></span>` +
            `<span class="ws-txt">[${i + 1}] ${esc(s.citation || s.key)}</span>` +
            `<span class="ws-btns">` +
              (s.key ? `<button class="ghost2 ws-pdf" title="用系统阅读器打开这篇原文文件">📄 打开原文</button>` : "") +
              (s.key ? `<button class="ghost2 ws-sim" title="在库里找与这篇相似的文献">🔍 找相似</button>` : "") +
            `</span>` +
          `</div>`).join("")
      : "";
    $("#wiki-sources").querySelectorAll(".ws-item").forEach((el) => {
      const k = el.dataset.key, c = el.dataset.cite;
      const pdf = el.querySelector(".ws-pdf");
      if (pdf) pdf.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        pdf.disabled = true;
        try {
          const r = await jpost("/open_source", { key: k });
          // UX10：非阻断性失败用浮层提示，不再弹浏览器灰框 alert
          if (r && r.ok === false) flashToast(r.msg || "打开失败：这篇可能没有全文附件。");
        } catch (e) { flashToast("打开原文失败：" + (e.message || e)); }
        finally { pdf.disabled = false; }
      });
      const sim = el.querySelector(".ws-sim");
      if (sim) sim.addEventListener("click", (ev) => {
        ev.stopPropagation();
        $("#wiki-modal").hidden = true;
        if (k) findSimilar(k, c); else switchToSearch(c || "");
      });
    });
    renderWikiLinks(p);
    $("#wiki-hist").dataset.id = p.id;
    $("#wiki-discard").dataset.id = p.id;
    $("#wiki-history").hidden = true;
    // EN-F7：换页时清空上一页残留的核验输入与结果，避免张冠李戴
    const wvI = $("#wv-claim"); if (wvI) wvI.value = "";
    const wvR = $("#wv-result"); if (wvR) { wvR.hidden = true; wvR.innerHTML = ""; }
    $("#wiki-modal").hidden = false;
  }
  function renderWikiToc() {
    const body = $("#wiki-body"), toc = $("#wiki-toc"); if (!body || !toc) return;
    const headings = [...body.querySelectorAll("h2, h3")];
    if (headings.length < 2) { toc.hidden = true; toc.innerHTML = ""; return; }
    headings.forEach((h, i) => { h.id = `wiki-section-${i}`; });
    toc.innerHTML = `<div class="wiki-toc-h">本页目录</div>` + headings.map((h, i) =>
      `<button class="wiki-toc-link lv-${h.tagName.toLowerCase()}" data-target="wiki-section-${i}">${esc(h.textContent || "")}</button>`).join("");
    toc.hidden = false;
    toc.querySelectorAll(".wiki-toc-link").forEach((b) => b.addEventListener("click", () => {
      const h = body.querySelector("#" + b.dataset.target); if (h) h.scrollIntoView({ behavior: "smooth", block: "start" });
    }));
  }
  // 互链（本页链出）与反向链接（哪些页链到本页）——把孤立的页面走成一张图。
  // 这是 karpathy 用 Obsidian graph view 看的东西，这里长在应用自己的界面里。
  async function renderWikiLinks(p) {
    const box = $("#wiki-links");
    if (!box) return;
    box.innerHTML = "";
    let bl = null;
    try { bl = await jget("/wiki/backlinks?page_id=" + encodeURIComponent(p.id)); } catch (e) { /* 静默：互链是增强，不是主功能 */ }
    const out = (bl && bl.links_out) || [];
    const inn = (bl && bl.links_in) || [];
    if (!out.length && !inn.length) {
      box.innerHTML = `<div class="wl-empty">🔗 这一页还没有和其它综述页互链（孤儿页）。
        可以让 agent 调 <code>set_wiki_links</code> 把它接进知识图。</div>`;
      return;
    }
    const chip = (x) => `<button class="wl-chip" data-id="${esc(x.id)}" title="打开这一页">${esc(x.title || x.id)}</button>`;
    let html = "";
    if (out.length) html += `<div class="wl-row"><span class="wl-h">🔗 本页链向</span>${out.map(chip).join("")}</div>`;
    if (inn.length) html += `<div class="wl-row"><span class="wl-h">↩ 被这些页引用</span>${inn.map(chip).join("")}</div>`;
    box.innerHTML = html;
    box.querySelectorAll(".wl-chip").forEach((el) =>
      el.addEventListener("click", () => openWikiPage(el.dataset.id)));   // 顺着链走，像真的 wiki
  }

  // 版本历史 + 回滚。有 git 用 git，没 git 用快照——用户不必知道区别。
  async function toggleWikiHistory(id) {
    const box = $("#wiki-history");
    if (!box) return;
    if (!box.hidden) { box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = `<div class="wh-loading">读取修改历史…</div>`;
    try {
      const h = await jget("/wiki/history/" + encodeURIComponent(id));
      const vs = h.versions || [];
      if (!vs.length) { box.innerHTML = `<div class="wh-loading">还没有历史版本。</div>`; return; }
      const fmt = (ts) => new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false });
      box.innerHTML = `<div class="wh-h">修改历史（${vs.length} 版）</div>` +
        vs.map((v, i) =>
          `<div class="wh-item">
             <span class="wh-ts">${esc(fmt(v.ts))}</span>
             <span class="wh-msg">${esc(v.message || "（无说明）")}</span>
             ${i === 0 ? `<span class="wh-cur">当前</span>`
                       : `<button class="ghost2 wh-restore" data-rev="${esc(v.rev)}">回滚到这一版</button>`}
           </div>`).join("");
      box.querySelectorAll(".wh-restore").forEach((el) => el.addEventListener("click", async () => {
        if (!(await uiConfirm("当前内容会先存成一个新版本，之后还能再滚回来。",
              { title: "回滚到这一版？", okText: "回滚" }))) return;
        el.disabled = true; el.textContent = "回滚中…";
        try {
          await jpost("/wiki/restore/" + encodeURIComponent(id), { rev: el.dataset.rev });
          await openWikiPage(id);
          if (wikiLoaded) loadWikiList("silent");
        } catch (e) { flashToast("回滚失败：" + (e.message || e)); el.disabled = false; el.textContent = "回滚到这一版"; }   // UX10
      }));
    } catch (e) {
      box.innerHTML = `<div class="wh-loading">读取历史失败：${esc(e.message || e)}</div>`;
    }
  }

  async function openWikiPage(id) {
    try { renderWiki(await jget("/wiki/page/" + encodeURIComponent(id))); }
    catch (e) { flashToast("打开综合页失败：" + (e.message || e)); }   // UX10
  }
  // 「🗑 不保存此答案」——与「💾 保存此答案」互为反操作（删文件+索引+检索行）。仅人用。
  async function discardWiki(id, onDone, btn) {
    if (!id) return;
    if (!(await uiConfirm("不影响文献库。", { title: "删除这条本地综合页？", okText: "删除", danger: true }))) return;
    const old = btn ? btn.textContent : null;
    if (btn) { btn.disabled = true; btn.textContent = "删除中…"; }
    try {
      // BF11：删除失败时读后端 detail/error/msg 的人话原因，不再只报「HTTP 4xx」
      await jsend("/wiki/page/" + encodeURIComponent(id), "DELETE");
      if (onDone) onDone();
    } catch (e) { flashToast("删除失败：" + (e.message || e)); }   // UX10
    finally { if (btn) { btn.disabled = false; if (old != null) btn.textContent = old; } }
  }
  (function wireWikiModal() {
    const close = $("#wiki-close"); if (close) close.addEventListener("click", () => ($("#wiki-modal").hidden = true));
    // EN-F4：正文 wikilink 点击委托——mdToHtml 输出走 innerHTML，不能挂内联 onclick；
    // 委托挂在常驻容器 #wiki-body 上，正文每次重渲染也不用重挂
    const wb = $("#wiki-body");
    if (wb) wb.addEventListener("click", (e) => {
      const a = e.target.closest("a.wk-link[data-wk]");
      if (a) { e.preventDefault(); openWikiPage(a.dataset.wk); }
    });
    // EN-F7：核验一句话——把综述里的论断丢给核验器（/research/verify_claim），三态徽章 + 证据列表。
    // 这是核验器的「人用入口」：不接 Agent 也能随手查一句话有没有库内依据
    const WV_VERDICT = { supported: ["ok", "✅ 库内证据支持"], mismatch: ["warn", "⚠ 与库内证据不符"], not_in_lib: ["miss", "❓ 库里没找到依据"] };
    async function runVerifyClaim() {
      const inp = $("#wv-claim"), btn = $("#wv-go"), out = $("#wv-result");
      if (!inp || !btn || !out) return;
      const claim = (inp.value || "").trim();
      if (!claim) { out.hidden = false; out.innerHTML = `<span class="hint">先粘贴一句要核验的论断。</span>`; return; }
      if (btn.disabled) return;   // 核验中不重复提交
      btn.disabled = true; const old = btn.textContent; btn.textContent = "核验中…";
      out.hidden = false; out.innerHTML = `<span class="hint">正在库里找证据…（可能要几秒）</span>`;
      try {
        const r = await jpost("/research/verify_claim", { claim, keys: null, topk: 8 });
        const v = WV_VERDICT[r.verdict] || ["miss", esc(r.verdict || "结果未知")];
        const conf = (r.confidence != null) ? `（把握 ${Math.round(Number(r.confidence) * 100)}%）` : "";
        const evs = (r.evidence || []).map((ev) => {
          const pg = (ev.printed_page != null && ev.printed_page !== "") ? `第 ${esc(String(ev.printed_page))} 页`
                   : (ev.locator ? esc(String(ev.locator)) : ((ev.pdf_page != null && ev.pdf_page !== "") ? `PDF 第 ${esc(String(ev.pdf_page))} 页` : ""));
          return `<div class="wv-ev"><div class="wv-ev-t">${esc(ev.title || ev.citation || ev.key || "")}${pg ? ` · <span class="pg">${pg}</span>` : ""}</div>` +
            (ev.quote ? `<div class="wv-ev-q">「${esc(ev.quote)}」</div>` : "") + `</div>`;
        }).join("");
        out.innerHTML = `<div class="wv-verdict ${v[0]}">${v[1]}${esc(conf)}</div>` +
          (r.note ? `<div class="hint">${esc(r.note)}</div>` : "") + evs;
      } catch (e) {
        // jpost 已读出后端 detail/error/msg 的人话原因，原样亮出来
        out.innerHTML = `<div class="wv-verdict warn">核验失败：${esc(e.message || e)}</div>`;
      } finally { btn.disabled = false; btn.textContent = old; }
    }
    const wvGo = $("#wv-go"); if (wvGo) wvGo.addEventListener("click", runVerifyClaim);
    const wvIn = $("#wv-claim"); if (wvIn) wvIn.addEventListener("keydown", (e) => { if (e.key === "Enter") runVerifyClaim(); });
    const disc = $("#wiki-discard");
    if (disc) disc.addEventListener("click", () => discardWiki(disc.dataset.id,
      () => {
        $("#wiki-modal").hidden = true;
        if (wikiLoaded) loadWikiList("silent");        // 从 wiki 页删的，刷新列表
        if ($("#q").value.trim()) doSearch();          // 从检索删的，刷新检索（保留原行为）
      }, disc));
  })();

  // ══════════════════════════════════════════
  //  wiki 页（综合页书架）：列出 /wiki/list + 主题整理 + 打开/核验/删除；写作交给 Agent
  // ══════════════════════════════════════════
  let WK = { kind: "", agentOnly: false, pages: [], themes: [], theme: "", search: "", sort: "new" };
  async function loadWikiList(mode) {
    const box = $("#wk-list");
    loadWikiSuggestions();   // EN-F2：每次进综述库都刷新建议横幅（fire-and-forget，失败静默）
    if (mode !== "silent") box.innerHTML = `<div class="wk-loading">加载综合页中…</div>`;
    try {
      const [d, td] = await Promise.all([jget("/wiki/list"), jget("/wiki/themes")]);
      WK.pages = d.pages || [];
      WK.themes = td.themes || [];
      if (WK.theme && !WK.themes.some((x) => x.name === WK.theme)) WK.theme = "";
      renderWikiThemes();
      renderWikiList();
      if (GV.on) drawGraph();      // 关系图开着时（删页/回滚/新建后）同步重画
      wikiLoaded = true;   // 成功才置位，失败保持 false 便于切回重试
    } catch (e) {
      box.innerHTML = `<div class="wk-loading">加载失败：${esc(e.message)}</div>`;
    }
  }
  const WK_KIND = { answer: "📝 对话沉淀", concept: "🧩 概念综述", topic: "🗂 主题综述",
                    digest: "📚 资料汇编", outline: "🧭 选题框架",
                    entity: "👤 实体页", overview: "🧭 总论页" };
  function renderWikiThemes() {
    const box = $("#wk-themes"); if (!box) return;
    const rows = [{ name: "", label: "全部综述", count: WK.pages.length, source: "all" }, ...WK.themes.map((x) => ({ ...x, label: x.name }))];
    box.innerHTML = rows.map((t) =>
      `<button class="wk-theme${WK.theme === t.name ? " active" : ""}" data-theme="${esc(t.name)}">` +
        `<span class="wk-theme-ic">${t.source === "manual" ? "◆" : (t.source === "all" ? "▦" : "◇")}</span>` +
        `<span class="wk-theme-name">${esc(t.label)}</span><span class="wk-theme-count">${num(t.count)}</span></button>`).join("");
    box.querySelectorAll(".wk-theme").forEach((el) => el.addEventListener("click", () => {
      WK.theme = el.dataset.theme || ""; renderWikiThemes(); renderWikiList();
    }));
    const selected = WK.themes.find((x) => x.name === WK.theme);
    const actions = $("#wk-theme-actions");
    if (actions) {
      actions.innerHTML = selected && selected.custom
        ? `<button class="ghost2b" data-theme-act="rename">重命名</button><button class="ghost2b danger" data-theme-act="delete">删除主题</button>` : "";
      const rn = actions.querySelector('[data-theme-act="rename"]'); if (rn) rn.addEventListener("click", renameWikiTheme);
      const del = actions.querySelector('[data-theme-act="delete"]'); if (del) del.addEventListener("click", deleteWikiTheme);
    }
  }
  function renderWikiList() {
    const box = $("#wk-list");
    let list = [...WK.pages];
    if (WK.theme) list = list.filter((p) => (p.theme || "未分类") === WK.theme);
    if (WK.kind) list = list.filter((p) => (p.kind || "answer") === WK.kind);
    // W3：「只看未核验」＝agent 写回且尚未人工核验（已核验的不再算待办）
    if (WK.agentOnly) list = list.filter((p) => p.by_agent && !p.verified_at);
    if (WK.search) {
      const q = WK.search.toLocaleLowerCase("zh-CN");
      list = list.filter((p) => (p.title || "").toLocaleLowerCase("zh-CN").includes(q));
    }
    if (WK.sort === "title") list.sort((a, b) => (a.title || "").localeCompare(b.title || "", "zh-CN"));
    else if (WK.sort === "sources") list.sort((a, b) => Number(b.n_sources || 0) - Number(a.n_sources || 0));
    else list.sort((a, b) => String(b.generated_at || "").localeCompare(String(a.generated_at || "")));
    const heading = $("#wk-current-theme"); if (heading) heading.textContent = WK.theme || "全部综述";
    const count = $("#wk-count"); if (count) count.textContent = `共 ${list.length} 页`;
    if (!WK.pages.length) {
      box.innerHTML = `<div class="wk-empty">
        <div class="wk-empty-ic">📖</div>
        <div class="wk-empty-h">还没有综述页</div>
        <div class="wk-empty-s">请让「🤖 Agent」阅读文献并把带来源的综合写回；
          生成后的页面会自动出现在这里，供你分类、阅读和核验。</div></div>`;
      return;
    }
    if (!list.length) { box.innerHTML = `<div class="wk-empty">当前筛选下没有综述页。</div>`; return; }
    box.innerHTML = "";
    list.forEach((p) => box.appendChild(wikiCard(p)));
  }
  function wikiCard(p) {
    const div = document.createElement("div");
    div.className = "wk-card" + (p.stale ? " stale" : "") + (p.degraded ? " degraded" : "");
    const kind = WK_KIND[p.kind || "answer"] || (p.kind || "");
    const prov = p.degraded
      ? `<span class="wk-flag degraded" title="${esc(p.degraded_reason || "")}">⚠ 证据清单（非 AI 综述）</span>`
      : p.by_agent
      // W3：核验过的 agent 页在列表里也转 ✅，与详情弹窗口径一致
      ? (p.verified_at
          ? `<span class="wk-flag ok" title="已于 ${esc(p.verified_at)} 人工核验"><span class="status-dot" aria-hidden="true"></span>已核验</span>`
          : `<span class="wk-flag agent" title="agent 写回、未经人工核验"><span class="status-dot" aria-hidden="true"></span>未核验</span>`)
      : `<span class="wk-flag" title="你保存/生成的综述页"><span class="status-dot" aria-hidden="true"></span>我保存的</span>`;
    const stale = p.stale ? `<span class="wk-flag stale" title="有新论文可能影响此综述，建议让 Agent 读取新增文献后更新">⚠ 可能已过时</span>` : "";
    // 整卡可点即打开；整理主题与删除收进「…」，让列表优先服务阅读。
    div.className += " wk-card-click";
    const themeOptions = [`<option value="">自动归类</option>`, ...WK.themes.map((t) =>
      `<option value="${esc(t.name)}"${p.theme_source === "manual" && p.theme === t.name ? " selected" : ""}>${esc(t.name)}</option>`)].join("");
    div.innerHTML =
      `<div class="wk-card-main"><div class="wk-card-head"><span class="wk-badge k-${esc(p.kind || "answer")}">${esc(kind)}</span>` +
        `<span class="wk-title">${esc(p.title || "(无标题)")}</span></div>` +
      `<div class="wk-card-meta"><span>${p.theme_source === "manual" ? "◆" : "◇"} ${esc(p.theme || "未分类")}</span>` +
        `<span>基于 ${num(p.n_sources)} 篇</span><span>${esc((p.generated_at || "").slice(0, 10) || "未知日期")}</span></div></div>` +
      `<span class="wk-card-status">${prov}${stale}</span>` +
      `<button class="wk-card-more" title="整理或删除" aria-label="整理或删除">•••</button>` +
      `<div class="wk-card-pop" hidden><label>固定到主题<select class="wk-card-theme">${themeOptions}</select></label>` +
        `<button class="wk-card-del">删除这条综述</button></div>`;
    div.addEventListener("click", () => openWikiPage(p.id));
    div.setAttribute("role", "button");
    div.setAttribute("tabindex", "0");
    div.addEventListener("keydown", (e) => { if (e.key === "Enter") openWikiPage(p.id); });
    const more = div.querySelector(".wk-card-more"), pop = div.querySelector(".wk-card-pop");
    more.addEventListener("click", (e) => { e.stopPropagation(); pop.hidden = !pop.hidden; });
    pop.addEventListener("click", (e) => e.stopPropagation());
    div.querySelector(".wk-card-theme").addEventListener("change", async (e) => {
      e.stopPropagation();
      try { await jpost("/wiki/page/" + encodeURIComponent(p.id) + "/theme", { name: e.target.value }); await loadWikiList("silent"); }
      catch (err) { flashToast("整理主题失败：" + (err.message || err)); }
    });
    div.querySelector(".wk-card-del").addEventListener("click", (e) => {
      e.stopPropagation();
      discardWiki(p.id, () => loadWikiList("silent"), e.currentTarget);
    });
    return div;
  }
  async function createWikiTheme() {
    const name = await askText("新建研究主题", "", "例如：少年司法"); if (!name) return;
    try { await jpost("/wiki/themes", { name }); WK.theme = name; await loadWikiList("silent"); }
    catch (e) { flashToast("新建主题失败：" + (e.message || e)); }
  }
  async function renameWikiTheme() {
    if (!WK.theme) return;
    const name = await askText("重命名主题", WK.theme, "主题名称"); if (!name || name === WK.theme) return;
    try { await jpost("/wiki/themes/rename", { old_name: WK.theme, new_name: name }); WK.theme = name; await loadWikiList("silent"); }
    catch (e) { flashToast("重命名失败：" + (e.message || e)); }
  }
  async function deleteWikiTheme() {
    if (!WK.theme) return;
    if (!(await uiConfirm("主题里的综述不会被删除，它们会恢复自动归类。", { title: `删除“${WK.theme}”主题？`, okText: "删除主题", danger: true }))) return;
    try { await jsend("/wiki/themes/" + encodeURIComponent(WK.theme), "DELETE"); WK.theme = ""; await loadWikiList("silent"); }
    catch (e) { flashToast("删除主题失败：" + (e.message || e)); }
  }
  // ── EN-F2：建议横幅——新深索的文献可能影响已有综述页（Ingest 环：喂了新料，提醒回头更新综述）──
  const WSUG = { items: [], open: false };   // open：列表是否展开（跨刷新保持用户的展开选择）
  async function loadWikiSuggestions() {
    const box = $("#wk-suggest"); if (!box) return;
    // 建议是增强功能：拉不到（旧后端/接口未就绪）就当没有，绝不打断综述库主流程
    try { const d = await jget("/wiki/suggestions"); WSUG.items = d.items || []; }
    catch (e) { WSUG.items = []; }
    renderWikiSuggestions();
  }
  function renderWikiSuggestions() {
    const box = $("#wk-suggest"); if (!box) return;
    const items = WSUG.items;
    if (!items.length) { box.hidden = true; box.innerHTML = ""; return; }
    const row = (it) => {
      // 受影响页 chips：点击直接打开该综述页复核；「忽略」= dismiss 后端记录，之后不再提示这篇
      // new_page 只是新主题候选：维护 Agent 读原文后可建页、并入已有页或记录无需写入。
      let mid;
      if (it.kind === "new_page") {
        mid = `<span class="wsg-newpage" title="${esc(it.hint || "维护 Agent 会读原文后判断建页、并入或无需写入")}">🆕 新主题候选</span>`;
      } else {
        const chips = (it.pages || []).map((p) =>
          `<button class="wl-chip wsg-pg" data-id="${esc(p.id)}" title="打开这页综述复核">${esc(p.title || p.id)}</button>`).join("");
        mid = `<span class="wsg-pages">${chips}</span>`;
      }
      return `<div class="wsg-row">
        <span class="wsg-doc" title="${esc(it.title || it.key)}">📄 ${esc(it.title || it.key)}</span>
        ${mid}
        <button class="ghost2b wsg-dismiss" data-key="${esc(it.key)}" title="不再为这篇提示">忽略</button>
      </div>`;
    };
    box.innerHTML =
      `<div class="wsg-head" role="button" tabindex="0">📬 <b>${num(items.length)}</b> 篇新深索文献待审阅（可让 Agent 全量维护并逐条清零）` +
        `<span class="wsg-caret">${WSUG.open ? "收起 ▴" : "展开看看 ▾"}</span></div>` +
      `<div class="wsg-body"${WSUG.open ? "" : " hidden"}>${items.map(row).join("")}</div>`;
    box.hidden = false;
    const head = box.querySelector(".wsg-head");
    const toggle = () => { WSUG.open = !WSUG.open; renderWikiSuggestions(); };
    head.addEventListener("click", toggle);
    head.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });
    box.querySelectorAll(".wsg-pg").forEach((b) =>
      b.addEventListener("click", (e) => { e.stopPropagation(); openWikiPage(b.dataset.id); }));
    box.querySelectorAll(".wsg-dismiss").forEach((b) => b.addEventListener("click", async (e) => {
      e.stopPropagation(); b.disabled = true;
      try {
        await jpost("/wiki/suggestions/dismiss", { key: b.dataset.key });
        WSUG.items = WSUG.items.filter((x) => x.key !== b.dataset.key);   // 本地同步剔除，空了整条横幅消失
        renderWikiSuggestions();
      } catch (err) { b.disabled = false; flashToast("忽略失败：" + (err.message || err)); }
    }));
  }

  // ── EN-F3：时间线——综述库演化史（/wiki/timeline：git log 解析优先，退 .history 快照目录）──
  // 动作文案后端已是中文（「agent写入 concept 页」「人修订正文(append)」…），直接展示；
  // 仅剥掉 by_agent 判定用的「agent/人」前缀（🤖/👤 图标已承担这份信息，不重复念）
  async function openTimeline() {
    const m = $("#wk-tl-modal"), list = $("#wk-tl-list"), src = $("#wk-tl-src");
    if (!m || !list) return;
    m.hidden = false;
    src.textContent = "";
    list.innerHTML = `<div class="wh-loading">读取时间线…</div>`;
    try {
      const d = await jget("/wiki/timeline?limit=100");
      const evs = d.events || [];
      if (!evs.length) {
        list.innerHTML = `<div class="wh-loading">还没有记录——生成或修改综述页之后，这里会按时间列出每一次变动。</div>`;
        return;
      }
      src.textContent = (d.source === "git" ? "来自 git 版本历史" : "来自本地快照记录") + ` · 最近 ${num(evs.length)} 条`;
      list.innerHTML = evs.map((ev) => {
        // 🤖=Agent 写回 / 👤=你本人（应用内操作）——一眼锁定该复核的机器改动
        const who = ev.by_agent
          ? `<span class="tl-who" title="由 Agent 写回">🤖</span>`
          : `<span class="tl-who" title="你本人 / 应用内操作">👤</span>`;
        const act = (ev.action || "").replace(/^(agent|人)/, "");
        // 系统提交（git 初始化 / WIKI.md 升级）没有页 id——不渲染空链接 chip
        const pg = ev.page_id
          ? `<button class="wl-chip tl-pg" data-id="${esc(ev.page_id)}" title="打开这页综述">${esc(ev.page_id)}</button>`
          : "";
        return `<div class="tl-item">
          <span class="tl-time">${esc(ev.time || "")}</span>${who}
          <span class="tl-act">${esc(act)}</span>
          ${pg}
        </div>`;
      }).join("");
      // 点页链接：关时间线、开该综述页（已删除的页会由 openWikiPage 的失败浮层兜底提示）
      list.querySelectorAll(".tl-pg").forEach((b) => b.addEventListener("click", () => {
        m.hidden = true; openWikiPage(b.dataset.id);
      }));
    } catch (e) {
      list.innerHTML = `<div class="wh-loading">读取时间线失败：${esc(e.message || e)}</div>`;
    }
  }

  // ── 体检（lint）：孤儿 / 过时 / 断链 / 来源 / 降级 / 缺失概念 / 重复外壳 ──
  const LINT_LABEL = { orphan: "孤儿页（没有任何互链）", stale: "已标记过时", broken_link: "断链",
                       body_broken_link: "正文链接指向不存在的页",   // EN-F4：正文 [[wikilink]] 的断链
                       no_sources: "没有来源论文", degraded: "降级页（未配 AI 模型时生成）",
                       missing_concept: "被反复提及、却没有独立页的概念",
                       invalid_source: "来源指向不存在的文献",
                       duplicate_scaffold: "重复标题或研究问题" };
  // 体检建议的大白话版（给法学用户看；后端 suggestions 带工具名是给 agent 的，这里不用）
  const LINT_FIX = {
    orphan: (n) => `有 ${n} 页没跟其它综述连起来（成了"孤岛"）。可以让 AI 把它们和相关的页互相关联，以后顺着就能查到；或者确认它们本来就该单独放。`,
    stale: (n) => `有 ${n} 页被标了"可能过时"。找几篇新文献让 AI 重写更新，再把过时标记去掉就行。`,
    broken_link: (n) => `有 ${n} 处关联指向了已经删掉的页（点了会扑空）。让 AI 把这些失效的关联清理掉。`,
    // EN-F4：正文里 [[双方括号链接]] 的断链，与上面的元数据关联断链分开说
    body_broken_link: (n) => `有 ${n} 处正文链接指向不存在的页（点了会扑空）。让 AI 把这些链接改成正确的页，或补写目标页。`,
    no_sources: (n) => `有 ${n} 页没标是根据哪些文献写的。没有出处的综述不可靠——让 AI 补上来源，或者干脆删掉。`,
    degraded: (n) => `有 ${n} 页是没配 AI 模型时生成的，其实只是原文片段的清单、不是真正的综述。请让 Agent 读取原始文献后重写。`,
    missing_concept: (n, items) => `有些概念被好几页反复提到、却没有自己的独立页（比如${(items || []).slice(0, 3).map((x) => "「" + x.concept + "」").join("、")}）。可以让 AI 各写一页，查起来更方便。`,
    invalid_source: (n) => `有 ${n} 个来源编号在当前文献库里不存在，通常是复制时错了一位。让 AI 按候选编号回查原文后修正，不能凭猜测替换。`,
    duplicate_scaffold: (n) => `有 ${n} 页把标题或研究问题重复写了两遍。让 AI 保留一份正文外壳后重写即可。`,
  };
  async function runLint() {
    const box = $("#wk-lint-panel"), btn = $("#wk-lint");
    if (!box) return;
    if (!box.hidden) { box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = `<div class="wh-loading">正在体检…</div>`;
    if (btn) btn.disabled = true;
    try {
      const r = await jget("/wiki/lint");
      // 大白话：为什么要维护、维护了有什么用（法学用户看得懂）
      const why = `<div class="lint-why">📖 <b>综述库为什么要"维护"？</b>你和 AI 生成的每页综述，会随着你不断加新文献而<b>慢慢过时、或彼此脱节</b>。
        定期体检能揪出：没跟其它页连起来的<b>孤立页</b>、被新文献推翻的<b>过时页</b>、指向已删页的<b>断链</b>、没有或写错来源的<b>可疑页</b>，以及重复的标题/研究问题。
        把这些理顺，你的知识库才会<b>越用越准、越查越省事</b>，而不是越堆越乱。这些整理活儿都可以交给 AI 代劳。</div>`;
      if (r.healthy) {
        box.innerHTML = why + `<div class="lint-ok">✅ 综述库很健康：${r.n_pages} 页，没有孤立页、过时页、断链，来源齐全有效，标题也没有重复，暂时不用维护。</div>`;
        return;
      }
      let html = why + `<div class="lint-h">🩺 体检结果：共 ${r.n_pages} 页，发现 <b>${r.n_issues}</b> 处可以理顺的地方</div>`;
      for (const [k, items] of Object.entries(r.issues || {})) {
        if (!items.length) continue;
        html += `<div class="lint-grp"><div class="lint-grp-h">${esc(LINT_LABEL[k] || k)}（${items.length}）</div><div class="lint-items">`;
        html += items.slice(0, 10).map((x) => {
          // EN-F4：body_broken_link 结构同 broken_link（page_id/title/dangling），chip 点开出问题的那页
          if (k === "broken_link" || k === "body_broken_link") return `<span class="lint-chip" data-id="${esc(x.page_id)}">${esc(x.title || x.page_id)} → ${esc(x.dangling)}</span>`;
          if (k === "missing_concept") return `<span class="lint-chip plain">${esc(x.concept)}（被 ${x.mentioned_in} 页提及）</span>`;
          if (k === "invalid_source") return `<span class="lint-chip" data-id="${esc(x.id)}">${esc(x.title || x.id)} → ${esc(x.key)}${(x.suggestions || []).length ? `（可能是 ${esc(x.suggestions.join("、"))}）` : ""}</span>`;
          return `<span class="lint-chip" data-id="${esc(x.id)}">${esc(x.title || x.id)}</span>`;
        }).join("");
        if (items.length > 10) html += `<span class="lint-chip plain">…… 还有 ${items.length - 10} 个</span>`;
        html += `</div></div>`;
      }
      // 大白话建议（前端按问题类型生成，不用后端带工具名的版本）
      const fixes = [];
      for (const [k, items] of Object.entries(r.issues || {})) {
        if (items.length && LINT_FIX[k]) fixes.push(LINT_FIX[k](items.length, items));
      }
      html += `<div class="lint-sug"><b>怎么办：</b><ul>` +
        fixes.map((s) => `<li>${esc(s)}</li>`).join("") + `</ul>` +
        `<div class="lint-tip">💡 这些都不用你亲自动手：<b>接入 AI 助手（在「🤖 Agent」页照着做一次）</b>后，跟它说一句「<b>帮我整理一下综述库</b>」，它就会自动把上面这些补关联、重写、清理好。</div></div>`;
      box.innerHTML = html;
      box.querySelectorAll(".lint-chip[data-id]").forEach((el) =>
        el.addEventListener("click", () => openWikiPage(el.dataset.id)));
    } catch (e) {
      box.innerHTML = `<div class="wh-loading">体检失败：${esc(e.message || e)}</div>`;
    } finally { if (btn) btn.disabled = false; }
  }

  // ── 关系图：节点=综述页，边=互链。纯手写力导向，无外部依赖，替代 Obsidian graph view ──
  const GRAPH_COLOR = { answer: "#60a5fa", concept: "#34d399", topic: "#fbbf24",
                        digest: "#a78bfa", outline: "#f472b6", entity: "#fb923c", overview: "#f87171" };
  let GV = { on: false, raf: 0 };

  async function toggleGraph() {
    GV.on = !GV.on;
    $("#wk-list").hidden = GV.on;
    $("#wk-graph").hidden = !GV.on;
    $("#wk-view").textContent = GV.on ? "返回列表" : "关系图";
    if (GV.on) await drawGraph();
    else if (GV.raf) { cancelAnimationFrame(GV.raf); GV.raf = 0; }
  }

  async function drawGraph() {
    const svg = $("#wk-graph-svg"), empty = $("#wk-graph-empty"), stat = $("#wk-graph-stat");
    let g;
    try { g = await jget("/wiki/graph"); }
    catch (e) { stat.textContent = "读取关系图失败：" + (e.message || e); return; }

    const nodes = g.nodes || [], edges = g.edges || [];
    if (!nodes.length) {
      svg.innerHTML = ""; empty.hidden = false;
      empty.innerHTML = `<div class="wk-empty-ic">🕸</div><div class="wk-empty-h">还没有综述页</div>
        <div class="wk-empty-s">先生成几页综述，它们之间的互链会在这里连成一张知识图。</div>`;
      stat.textContent = ""; return;
    }
    empty.hidden = true;
    stat.innerHTML = `${nodes.length} 页 · ${edges.length} 条互链` +
      (g.n_orphan ? ` · <b class="gs-orphan">${g.n_orphan} 个孤儿页</b>（没有任何连线）` : " · 无孤儿页 ✓");

    const W = svg.clientWidth || 900, H = Math.max(380, Math.min(560, 120 + nodes.length * 26));
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.style.height = H + "px";

    // 初始位置：环形铺开（比随机更稳，避免每次打开图都长得不一样）
    const N = nodes.length, R = Math.min(W, H) * 0.34;
    nodes.forEach((n, i) => {
      const a = (i / N) * Math.PI * 2;
      n.x = W / 2 + R * Math.cos(a); n.y = H / 2 + R * Math.sin(a); n.vx = n.vy = 0;
    });
    const idx = Object.fromEntries(nodes.map((n, i) => [n.id, i]));
    const links = edges.map((e) => ({ s: idx[e.source], t: idx[e.target] })).filter((l) => l.s != null && l.t != null);

    // 力导向：斥力 + 弹簧 + 向心力。迭代若干帧后停下（不做成永动，省电）。
    let step = 0;
    const tick = () => {
      for (let iter = 0; iter < 2; iter++) {
        for (let i = 0; i < N; i++) {
          const a = nodes[i];
          for (let j = i + 1; j < N; j++) {
            const b = nodes[j];
            let dx = b.x - a.x, dy = b.y - a.y, d2 = dx * dx + dy * dy || 0.01;
            if (d2 > 90000) continue;
            const f = 1400 / d2, d = Math.sqrt(d2);
            const ux = dx / d * f, uy = dy / d * f;
            a.vx -= ux; a.vy -= uy; b.vx += ux; b.vy += uy;
          }
          a.vx += (W / 2 - a.x) * 0.0016; a.vy += (H / 2 - a.y) * 0.0016;
        }
        for (const l of links) {
          const a = nodes[l.s], b = nodes[l.t];
          const dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 0.01;
          const f = (d - 110) * 0.012, ux = dx / d * f, uy = dy / d * f;
          a.vx += ux; a.vy += uy; b.vx -= ux; b.vy -= uy;
        }
        for (const n of nodes) {
          n.vx *= 0.82; n.vy *= 0.82;
          n.x = Math.max(30, Math.min(W - 30, n.x + n.vx));
          n.y = Math.max(24, Math.min(H - 24, n.y + n.vy));
        }
      }
      paint();
      if (++step < 140) GV.raf = requestAnimationFrame(tick);
      else GV.raf = 0;
    };

    const paint = () => {
      const eHtml = links.map((l) =>
        `<line x1="${nodes[l.s].x.toFixed(1)}" y1="${nodes[l.s].y.toFixed(1)}" x2="${nodes[l.t].x.toFixed(1)}" y2="${nodes[l.t].y.toFixed(1)}" class="gv-edge"/>`).join("");
      const nHtml = nodes.map((n) => {
        const r = 6 + Math.min(7, n.degree * 1.6);
        const cls = "gv-node" + (n.orphan ? " orphan" : "") + (n.stale ? " stale" : "");
        const fill = n.orphan ? "#94a3b8" : (GRAPH_COLOR[n.kind] || "#94a3b8");
        const label = (n.title || n.id).slice(0, 14);
        return `<g class="${cls}" data-id="${esc(n.id)}" tabindex="0" role="button" aria-label="${esc(n.title || n.id)}">
            <title>${esc(n.title || n.id)}（${esc(WK_KIND[n.kind] || n.kind)}·${n.n_sources} 篇来源${n.orphan ? "·孤儿页" : ""}${n.stale ? "·已过时" : ""}）</title>
            <circle cx="${n.x.toFixed(1)}" cy="${n.y.toFixed(1)}" r="${r}" fill="${fill}"/>
            <text x="${n.x.toFixed(1)}" y="${(n.y + r + 11).toFixed(1)}" text-anchor="middle" class="gv-label">${esc(label)}</text>
          </g>`;
      }).join("");
      svg.innerHTML = eHtml + nHtml;
      svg.querySelectorAll(".gv-node").forEach((el) => {
        const open = () => openWikiPage(el.dataset.id);
        el.addEventListener("click", open);
        el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
      });
    };

    if (GV.raf) cancelAnimationFrame(GV.raf);
    step = 0; tick();
  }

  (function wireWikiPage() {
    const kind = $("#wk-kind"); if (kind) kind.addEventListener("change", () => { WK.kind = kind.value; renderWikiList(); });
    const search = $("#wk-search"); if (search) search.addEventListener("input", () => { WK.search = search.value.trim(); renderWikiList(); });
    const sort = $("#wk-sort"); if (sort) sort.addEventListener("change", () => { WK.sort = sort.value; renderWikiList(); });
    const ag = $("#wk-agent"); if (ag) ag.addEventListener("change", () => { WK.agentOnly = ag.checked; renderWikiList(); });
    const nt = $("#wk-theme-new"); if (nt) nt.addEventListener("click", createWikiTheme);
    const closeWikiMore = () => { const more = document.querySelector(".wk-more"); if (more) more.open = false; };
    const lint = $("#wk-lint"); if (lint) lint.addEventListener("click", () => { closeWikiMore(); runLint(); });
    // EN-F3：时间线按钮 + 弹层关闭（Esc/遮罩关闭统一走 W2 的通用弹窗处理，见「W2」段）
    const tl = $("#wk-timeline"); if (tl) tl.addEventListener("click", () => { closeWikiMore(); openTimeline(); });
    const tlc = $("#wk-tl-close"); if (tlc) tlc.addEventListener("click", () => ($("#wk-tl-modal").hidden = true));
    const view = $("#wk-view"); if (view) view.addEventListener("click", toggleGraph);
    const hist = $("#wiki-hist"); if (hist) hist.addEventListener("click", () => toggleWikiHistory(hist.dataset.id));
  })();

  // ══════════════════════════════════════════
  //  Agent 页：MCP 接入引导（本机真实命令 + 工具表 + prompt 示例 + 深索现状）
  // ══════════════════════════════════════════
  let AG = { cfg: null };
  const AG_TOOLS = [
        ["“查库里关于 XX 的文献”", "search_localkb", "在你的文库里做混合检索，返回带统一来源评价、页码、可回溯引用的结果"],
    ["“库里现在有多少、索引到哪了”", "localkb_status", "看索引各档进度、篇数，以及已存了多少综合页"],
    ["“把库更新一下 / 深索一下”", "localkb_build", "触发建库或深索（加了新文献后增量更新）"],
    ["“把这个综述存进库”", "save_synthesis", "把 AI 综合出的结论写回成一页带引用的 wiki，下次能被检索命中"],
    ["“库里有没有现成的综述”", "list_wiki", "列已存的综合页，避免重复造轮子"],
    ["“打开那页综述给我看”", "get_wiki_page", "取某页综合的正文 + 来源页码引用"],
    ["“记住我偏好脚注 / 这个项目定了 XX”", "append_project_memory", "把你的偏好、已定决策记进项目记忆，换任何助手接入都从这接着干"],
    ["“看看有哪些综述该更新了”", "pending_wiki_updates", "拉新文献影响了哪些既有综述页，逐页判断标脏 / 重写"],
  ];
  const AG_PROMPTS = [
    "帮我查库里关于「认罪认罚从宽对司法信任的影响」的文献，按来源评价排。",
    "先看看库里有没有现成的综述；没有的话检索后综合一版，再帮我存起来。",
    "把库里关于「社会观护」的核心论点综述一下，每个论断带页码引用。",
    "库里最近加的文献深索了吗？没有的话帮我全部深索一遍。",
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
    // 工具数用后端真实值（当前 32 个），别用下面这张示例表的行数（只列了几个代表性的）
    const n = (AG.cfg && AG.cfg.tool_count) || AG_TOOLS.length;
    const tc = $("#ag-tool-count"); if (tc) tc.textContent = `（${n} 个工具）`;
  }
  function renderAgentPrompts() {
    $("#ag-prompts").innerHTML = AG_PROMPTS.map((p) => `<li>${esc(p)}</li>`).join("");
  }
  async function loadAgentDeep() {
    try {
      const st = await jget("/index/status");
      const withPdf = withFulltext(st), deep = st.deep_done || 0, xc = extractCounts(st);
      const pct = withPdf ? Math.round((Math.min(withPdf, deep + xc.blocked) / withPdf) * 100) : 0;
      $("#ag-deep").innerHTML = withPdf
        ? `已深索 <b>${num(deep)}</b> / 有全文附件 ${num(withPdf)} 篇（${pct}%）。` +
          ((deep + xc.blocked) < withPdf ? ` <a class="ag-link" id="ag-godeep">去「浏览」深索更多 →</a>`
            : (xc.blocked > 0 ? ` 可处理正文已完成 ✓（${extractCountsText(st)}）` : ` 已全部深索完成 ✓`)) + sacFrag(st)
        : `暂无可深索文献（库里没有带受支持全文附件的文献，或尚未建库）。`;
      const g = $("#ag-godeep");
      if (g) g.addEventListener("click", () => switchTab("browse"));
    } catch (e) { $("#ag-deep").textContent = "读取深索进度失败：" + e.message; }
  }
  function renderUpgradeHealth(h) {
    const box = $("#ag-upgrade"), list = $("#ag-upgrade-items"), health = $("#ag-upgrade-health");
    if (!box || !list || !health) return;
    AG.upgrade = h || { template_items: [] };
    const items = AG.upgrade.template_items || [];
    const pendingItems = items.map((x, i) => ({ ...x, _upgradeIndex: i }))
      .filter((x) => x.status === "pending" || x.status === "customized");
    // 本地模型是否安装是用户可自行选择的运行方式，不作为应用更新后的待处理提醒。
    const system = [["索引", AG.upgrade.index], ["运行环境", AG.upgrade.runtime]];
    const warnings = system.filter(([, x]) => x && ["stale", "missing", "unknown"].includes(x.state));
    const lightIndexOnly = !pendingItems.length && warnings.length === 1 && warnings[0][0] === "索引"
      && Array.isArray((warnings[0][1] || {}).changed) && warnings[0][1].changed.join(",") === "light";
    box.hidden = AG.upgradeDismissed || (!pendingItems.length && !warnings.length);
    if (box.hidden) return;
    const n = pendingItems.length;
    $("#ag-upgrade-title").textContent = n ? `${n} 项用户内容有新版待合并`
      : (lightIndexOnly ? "题录分类规则需要更新" : "应用更新后有项目需要留意");
    $("#ag-upgrade-sub").textContent = n
      ? "你的版本没有被覆盖。先看差异，推荐复制给 Agent 合并；也可带备份直接采用新版。"
      : (lightIndexOnly ? (warnings[0][1].detail || "手动更新一次知识库即可，无需清空或重新深索。")
        : "应用已更新，但下面的配套内容仍需人工处理。");
    list.innerHTML = pendingItems.map((x) =>
      `<div class="ag-upgrade-item" data-upgrade-i="${x._upgradeIndex}">` +
      `<div class="agu-name">${esc(x.label)}${x.status === "customized" ? "（新版旁本写入失败）" : ""}</div>` +
      `<div class="agu-actions">` +
      `<button data-uact="diff">查看差异</button><button data-uact="agent">复制给 Agent 合并</button>` +
      `<button data-uact="ack">本版不再提醒</button><button class="agu-use" data-uact="replace">备份后采用新版</button>` +
      `</div><div class="agu-path">你的文件：${esc(x.main_path)}${x.new_path ? `<br>新版旁本：${esc(x.new_path)}` : ""}</div></div>`
    ).join("");
    // 正常状态不占警告卡；这里只显示确实需要处理的项目。
    health.innerHTML = warnings.map(([name, x]) => {
      const tip = x.action ? `；建议：${x.action}` : "";
      const action = name === "索引" && x.action === "手动更新知识库" && !x.full_rebuild
        ? `<button class="agu-health-action" data-uhealth="index">手动更新知识库</button>` : "";
      return `<div class="agu-health-line"><span class="agu-health warn" title="${esc((x.label || "") + tip)}">${esc(name)}：${esc(x.label || "未知")}</span>${action}</div>`;
    }).join("");
    const refreshIndex = health.querySelector('[data-uhealth="index"]');
    if (refreshIndex) refreshIndex.addEventListener("click", doManualUpdate);
  }
  async function refreshUpgradeHealth() {
    AG.upgradeDismissed = false;
    const h = await jget("/upgrade/health");
    renderUpgradeHealth(h);
    return h;
  }
  function mergePrompt(x) {
    return `请帮我安全合并 PaperPiggy 的一项出厂内容升级。\n\n` +
      `我的版本：${x.main_path}\n新版旁本：${x.new_path || "（请以应用当前出厂版为准）"}\n\n` +
      `要求：1. 先读取并比较两个文件；2. 保留我的个性化规则、措辞和项目习惯；` +
      `3. 把新版新增且不冲突的要求合并进我的版本；4. 不删除任何备份或旁本；` +
      `5. 遇到冲突先用大白话告诉我并让我决定；6. 完成后说明保留了什么、新增了什么。`;
  }
  async function handleUpgradeAction(btn) {
    const row = btn.closest("[data-upgrade-i]");
    const x = AG.upgrade && (AG.upgrade.template_items || [])[Number(row && row.dataset.upgradeI)];
    if (!x) return;
    const act = btn.dataset.uact;
    if (act === "diff") {
      const d = await jget(`/upgrade/diff?kind=${encodeURIComponent(x.kind)}&key=${encodeURIComponent(x.key)}`);
      $("#upgrade-diff-title").textContent = `${x.label} · 版本差异`;
      $("#upgrade-diff-body").textContent = d.diff || "没有文字差异";
      $("#upgrade-diff-modal").hidden = false;
      return;
    }
    if (act === "agent") { await copyText(mergePrompt(x), btn); return; }
    if (act === "ack") {
      if (!(await uiConfirm("这不会改动或删除任何文件；只对当前这个新版停止提醒。以后出厂内容再次更新，还会重新提醒。",
        { title: `本版不再提醒「${x.label}」？`, okText: "不再提醒" }))) return;
      await jpost("/upgrade/ack", { kind:x.kind, key:x.key, current_hash:x.current_hash });
      await refreshUpgradeHealth(); return;
    }
    if (act === "replace") {
      if (!(await uiConfirm("你的当前文件会先在同一目录保存为 user-backup 备份，然后主文件改成最新版。新版旁本和备份都不会删除。",
        { title: `采用新版「${x.label}」？`, okText: "备份后采用新版", danger:true }))) return;
      const r = await jpost("/upgrade/replace", { kind:x.kind, key:x.key, current_hash:x.current_hash, confirm:"replace_with_factory" });
      await uiNotice(r.backup ? `已采用新版。你的原版本备份在：\n${r.backup}` : "已采用新版。", { title:"处理完成" });
      await refreshUpgradeHealth();
    }
  }
  async function loadAgentConfig() {
    try {
      const d = await jget("/agent/mcp-config");
      AG.cfg = d;
      $("#ag-run").classList.toggle("ok", !!d.server_running);
      // agent-status-dot-always-green：绿点只代表本地库服务在线，不代表 MCP 已接好
      $("#ag-run-txt").textContent = d.server_running
        ? "本地库服务在线（不代表 MCP 已接好）· 127.0.0.1:8770" : "本地库服务未就绪";
      renderAgentCmds();
      $("#ag-schema").textContent = d.wiki_schema_md || "";
      // Agent 专属文件夹路径（📦 交付物 / 📚 资料库）——后端算好本机绝对路径直接展示
      const op = $("#ag-output-path"); if (op) op.textContent = d.agent_output_dir || "（首次接入后生成）";
      const rp = $("#ag-rely-path"); if (rp) rp.textContent = d.agent_rely_dir || "（首次接入后生成）";
      renderUpgradeHealth(d.upgrade_health || {});
      agentLoaded = true;   // 成功才置位
    } catch (e) {
      $("#ag-run-txt").textContent = "读取接入信息失败：" + e.message;
    }
    renderAgentTools();     // 静态表，无需等网络
    renderAgentPrompts();
    loadAgentDeep();        // 复用 /index/status
    loadAgentTasks();       // ⏰ 定时任务（读本地「资料库/定时任务」）
    loadAgentOutputs();     // 📦 最近交付物主题（读本地「交付物/*」）
  }
  // C4：交付物卡「最近做了哪些主题」——读 /agent/outputs（扫「交付物/*」子文件夹）。
  //     常显：空/失败也显示引导（配合标题的「🔄 刷新」，新增交付物不必重启即可看到）。
  async function openAgentFolder(which, btn) {
    const lbl = btn && btn.textContent;
    try {
      const r = await jpost("/agent/open_folder", { which });
      if (r && r.ok === false) throw new Error(r.msg || "打开失败");
      if (btn) { btn.textContent = "已在文件管理器打开 ✓"; setTimeout(() => (btn.textContent = lbl), 1600); }
    } catch (e) {
      if (btn) { btn.textContent = "打开失败：" + (e.message || e); setTimeout(() => (btn.textContent = lbl), 2200); }
      else flashToast("打开文件夹失败：" + (e.message || e));
    }
  }
  async function openAgentOutput(name, card) {
    if (!name || (card && card.getAttribute("aria-busy") === "true")) return;
    if (card) { card.setAttribute("aria-busy", "true"); card.classList.add("opening"); }
    try {
      const r = await jpost("/agent/open_output", { name });
      if (r && r.ok === false) throw new Error(r.msg || "打开失败");
    } catch (e) {
      flashToast("打开交付物失败：" + (e.message || e));
    } finally {
      if (card) { card.removeAttribute("aria-busy"); card.classList.remove("opening"); }
    }
  }
  async function loadAgentOutputs() {
    const box = $("#ag-outputs"); if (!box) return;
    const empty = (msg) => { box.innerHTML = `<div class="ag-outputs-empty">${msg}</div>`; };
    try {
      const d = await jget("/agent/outputs");
      const items = (d && d.outputs) || [];
      if (!items.length) {
        empty(`还没有交付物主题。让 AI 助手把成品（论文 / 资料汇编 / 周报）写进「交付物」后，点右上角 <b>🔄 刷新</b> 即可看到。`);
        return;
      }
      box.innerHTML = items.map((o) => {
        const fileCount = o.file_count != null ? o.file_count : (o.n_files || 0);
        const subdirCount = o.subdir_count || 0;
        const counts = o.name === "定时任务"
          ? [`${subdirCount} 个任务文件夹`, `${fileCount} 个成果文件（含子文件夹）`]
          : [`${fileCount} 个文件${subdirCount ? "（含子文件夹）" : ""}`,
             subdirCount ? `${subdirCount} 个子文件夹` : null];
        const stats = [...counts, o.has_readme ? "含说明" : null]
          .filter(Boolean).map((x) => `<span>${x}</span>`).join(`<i class="ag-sep">·</i>`);
        return `<div class="ag-output-item" role="button" tabindex="0" data-output="${esc(o.name)}" title="打开这个交付物主题">`
          + `<div class="ag-output-name"><span class="ag-output-ico">📁</span><span>${esc(o.name)}</span></div>`
          + `<div class="ag-output-meta"><span>${o.mtime ? esc(o.mtime) : "日期未知"}</span></div>`
          + `<div class="ag-output-stats">${stats}</div></div>`;
      }).join("");
      box.querySelectorAll(".ag-output-item").forEach((card) => {
        const open = () => openAgentOutput(card.dataset.output, card);
        card.addEventListener("click", open);
        card.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
        });
      });
    } catch (e) {
      empty(`暂时读不到交付物列表（可能是后端未就绪）。稍后点 <b>🔄 刷新</b> 再试。`);
    }
  }
  // ⏰ 定时任务：读后端 /agent/tasks（扫「资料库/定时任务/*/任务.md」）。端点缺失/空时优雅降级。
  async function loadAgentTasks() {
    const box = $("#ag-tasks"); if (!box) return;
    try {
      const d = await jget("/agent/tasks");
      const items = (d && d.tasks) || [];
      const unrecognized = (d && d.unrecognized) || [];
      const diagnostic = () => {
        if (!unrecognized.length) return "";
        const missing = unrecognized.filter((x) => x.reason === "missing_task_file").length;
        const failed = unrecognized.filter((x) => x.reason === "read_error").length;
        const names = unrecognized.map((x) => esc(x.name || "未命名目录")).join("、");
        const reasons = [missing ? `${missing} 个缺少「任务.md」` : "", failed ? `${failed} 个读取失败` : ""]
          .filter(Boolean).join("，");
        return `<div class="ag-task-diagnostic"><span>发现 ${unrecognized.length} 个未识别任务目录（${reasons}）：${names}</span>`
          + `<button class="ag-open-taskdir" type="button">打开定时任务文件夹</button></div>`;
      };
      const wireDiagnostic = () => {
        const b = box.querySelector(".ag-open-taskdir");
        if (b) b.addEventListener("click", () => openAgentFolder("tasks", b));
      };
      if (!items.length) {
        box.innerHTML = unrecognized.length
          ? diagnostic()
          : `<div class="ag-tasks-empty">还没有定时任务。想让 AI 助手定期帮你搜集/综述（如每周少年司法动态），`
            + `对它说「<b>帮我建一个每周一早上的少年司法周报定时任务</b>」，它会把任务定义写进 `
            + `<code>资料库/定时任务/</code>，并在它自己的日程里排期。</div>`;
        wireDiagnostic();
        return;
      }
      box.innerHTML = items.map((t) => {
        const isDraft = t.has_enabled === false;   // 没写「启用」字段=草稿，不显示成绿灯
        const off = (t.enabled === false || isDraft) ? " off" : "";
        const b = [];
        if (t.freq) b.push(`<span class="ag-task-freq">${esc(t.freq)}</span>`);
        if (isDraft) b.push(`<span class="ag-task-freq" style="color:#a16207;background:rgba(161,98,7,.14)">草稿·未确认启用</span>`);
        else if (t.enabled === false) b.push(`<span class="ag-task-freq" style="color:#94a3b8;background:rgba(148,163,184,.12)">已暂停</span>`);
        // C1：可观测——有「上次执行」就显示；启用但从无执行记录 → 中性提示，戳破「显示启用≠真在跑」
        if (t.last_run) b.push(`<span class="ag-task-freq" style="color:#0f766e;background:rgba(15,118,110,.10)" title="AI 上次跑这个任务的时间">上次执行 ${esc(t.last_run)}</span>`);
        else if (!isDraft && t.enabled !== false) b.push(`<span class="ag-task-freq" style="color:#b45309;background:rgba(180,83,9,.10)" title="任务显示启用，但还没有执行记录——确认你的 AI 助手真的在它自己的日程里排期了">尚无执行记录</span>`);
        return `<div class="ag-task-item${off}"><span class="ag-task-name">${esc(t.name || "未命名任务")}</span>`
          + b.join("")
          + `<span class="ag-task-desc">${esc(t.desc || "")}</span></div>`;
      }).join("") + diagnostic();
      wireDiagnostic();
    } catch (e) {
      // 端点尚未就绪（旧后端）或读失败：显示引导而非报错
      box.innerHTML = `<div class="ag-tasks-empty">还没有定时任务。对 AI 助手说「帮我建一个每周定时任务」即可，`
        + `任务定义会存进 <code>资料库/定时任务/</code>。</div>`;
    }
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
      // EN-F6：恢复文案改为快照按钮原始文字（首次点击时存 data-lbl）——新增「📋 复制路径」等
      // 不同文案的复制按钮不用再逐个写死；连点两次也不会把「已复制 ✓」误存成原文案
      if (!b.dataset.lbl) b.dataset.lbl = b.textContent;
      const revert = b.dataset.lbl;
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
    // 📂 打开文件夹（交付物/资料库/技能）——复用后端 /agent/open_folder
    $$(".ag-openbtn").forEach((b) => b.addEventListener("click", () => openAgentFolder(b.dataset.open, b)));
    const osk = $("#ag-open-skills");
    if (osk) osk.addEventListener("click", () => openAgentFolder("skills", null));
    // 🔄 刷新：定时任务 / 最近交付物 首次加载后被 agentLoaded 门控锁住，切页不再重拉；
    //   这两个按钮直接重新扫盘，让「新建的任务 / 新写的交付物」不必重启应用即可看到。
    function wireRefresh(id, loader) {
      const b = $("#" + id); if (!b) return;
      b.addEventListener("click", async () => {
        if (b.disabled) return;
        if (!b.dataset.lbl) b.dataset.lbl = b.textContent;
        b.disabled = true; b.textContent = "刷新中…";
        try { await loader(); b.textContent = "已刷新 ✓"; }
        catch (_) { b.textContent = "刷新失败"; }
        setTimeout(() => { b.textContent = b.dataset.lbl; b.disabled = false; }, 1200);
      });
    }
    wireRefresh("ag-tasks-refresh", loadAgentTasks);
    wireRefresh("ag-outputs-refresh", loadAgentOutputs);
    wireRefresh("ag-upgrade-refresh", refreshUpgradeHealth);
    const upbox = $("#ag-upgrade");
    const upDismiss = $("#ag-upgrade-dismiss");
    if (upDismiss) upDismiss.addEventListener("click", () => {
      AG.upgradeDismissed = true;
      if (upbox) upbox.hidden = true;
    });
    if (upbox) upbox.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-uact]");
      if (b) handleUpgradeAction(b).catch((err) => uiNotice("处理失败：" + (err.message || err), { title:"升级处理失败" }));
    });
    const diffm = $("#upgrade-diff-modal"), diffc = $("#upgrade-diff-close");
    const closeDiff = () => { if (diffm) diffm.hidden = true; };
    if (diffc) diffc.addEventListener("click", closeDiff);
    if (diffm) diffm.addEventListener("mousedown", (e) => { if (e.target === diffm) closeDiff(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && diffm && !diffm.hidden) closeDiff(); });
    // 🧭 全屏教程浮层：开 / 关（Esc 也可关）
    const guide = $("#ag-guide"), gopen = $("#ag-guide-open"), gclose = $("#ag-guide-close");
    const showGuide = (v) => { if (guide) { guide.hidden = !v; if (v) guide.querySelector(".ag-guide-body").scrollTop = 0; } };
    if (gopen) gopen.addEventListener("click", () => showGuide(true));
    if (gclose) gclose.addEventListener("click", () => showGuide(false));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && guide && !guide.hidden) showGuide(false); });
  })();

  // 已深索的卡：有效→🧬；异常→🟠；缺失→⚪。异常与缺失都由用户点后才生成。
  function sacBadge(p) {
    if (!p.deep) return "";
    if (p.summary_invalid) {
      return `<span class="tag sac none invalid" role="button" tabindex="0" title="摘要未通过质量检查：${esc(p.summary_error || "内容异常")}。点此修复并重嵌入这一篇">🟠 摘要异常</span>`;
    }
    return p.has_summary
      ? `<span class="tag sac has" role="button" tabindex="0" title="点开查看这篇的 AI 检索摘要">🧬 摘要有效</span>`
      : `<span class="tag sac none" role="button" tabindex="0" title="点此为这篇生成 AI 检索摘要（知识库建设第②步；让检索更容易命中，需 API key，会重嵌入这一篇，可后台跑）">⚪ 摘要缺失</span>`;
  }
  function deepBadge(p) {
    const failure = extractFailureBadge(p);
    if (extractBlocked(p)) return failure;
    if (p.deep) return `<span class="tag full">📄 ${esc(fulltextLabel(p))} · 已深索</span>` + failure + sacBadge(p);
    // F11：有 PDF 的未深索徽标可点击深索（hover 变「深索该篇」）
    if (hasFulltext(p)) {
      const pending = extractState(p) === "ocr_pending";
      return `<span class="tag abstract deep-one" data-key="${esc(p.key)}" role="button" tabindex="0" title="${pending ? "点此继续深索并自动运行本地 OCR；不会上传或改写原 PDF" : "点此深索该篇（后台排队，不影响你继续操作）"}"><span class="lbl-idle">${pending ? "🔎 待本地OCR" : `📋 ${esc(fulltextLabel(p))} · 未深索`}</span><span class="lbl-hover">⚡ ${pending ? "开始OCR" : "深索该篇"}</span></span>`;
    }
    return `<span class="tag nopdf" title="无受支持全文附件，只有题录（题名·作者·年份·期刊）">🚫 无全文 / 仅题录</span>`;  // T5：与「未深索」区分
  }
  // 文件夹模式：AI 抽的题录待人工核对
  function reviewBadge(p) {
    return p.needs_review ? `<span class="tag review" title="这条题录是 AI 从正文读出来的，可能有出入；重要引用前建议核对题名/年份/期刊">📝 待核对</span>` : "";
  }
  function paperCard(p) {
    const div = document.createElement("div");
    div.className = "bcard";
    div.dataset.key = p.key;   // 供深索推进时按 key 定位、非破坏式刷新徽标（refreshBrowseDeepState）
    // F13：勾选与「可深索」解耦——所有卡片都可勾选（供加分类/导引文/多选拖拽），深索时再各自过滤
    const checked = BR.selected.has(p.key) ? "checked" : "";
    div.innerHTML =
      `<label class="bcard-cb"><input type="checkbox" ${checked} data-key="${esc(p.key)}"/></label>` +
      `<div class="bcard-body">` +
        `<div class="bcard-head">${tierBadge(p)}${statuteBadge(p)}${deepBadge(p)}${reviewBadge(p)}` +   // EN-F5：浏览卡带法条时效徽标
          // UX4：直接开原文 PDF（复用检索卡的 /open_pdf 通道）；无 PDF 禁用。标题点击仍保持「找相似」不动
          `<button class="bcard-open" title="${hasFulltext(p) ? `用系统阅读器打开这篇的 ${fulltextLabel(p)} 原文` : "无全文附件，无法打开原文"}"${hasFulltext(p) ? "" : " disabled"}>📄 打开</button>` +
          `<button class="bcard-addcat" title="加入「手动分类」">＋分类</button></div>` +   // D1：可见入口
        `<div class="bcard-title" title="点标题：查找相似文献">${esc(p.title || "(无标题)")}</div>` +
        metaRow(p) +
      `</div>`;
    const cb = div.querySelector("input[type=checkbox]");
    cb.addEventListener("change", () => {
      if (cb.checked) BR.selected.add(p.key); else BR.selected.delete(p.key);
      refreshSelUI();
    });
    // F11：卡上「未深索」徽标可点击单篇深索（stopPropagation 防冒泡到标题/右键）
    const bdb = div.querySelector(".deep-one");
    if (bdb) {
      const fire = (e) => { e.stopPropagation(); deepOneFromBadge(p.key, bdb); };
      bdb.addEventListener("click", fire);
      bdb.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fire(e); } });
    }
    // 检索摘要徽标：🧬摘要有效→点开只读查看；⚪摘要缺失→点→为这篇生成（第②步）
    const sacHas = div.querySelector(".tag.sac.has");
    if (sacHas) {
      const fire = (e) => { e.stopPropagation(); openSummaryView(p.key, p.title || ""); };
      sacHas.addEventListener("click", fire);
      sacHas.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fire(e); } });
    }
    const sacNone = div.querySelector(".tag.sac.none");
    if (sacNone) {
      const fire = (e) => { e.stopPropagation(); genSummaryOne(p.key, sacNone); };
      sacNone.addEventListener("click", fire);
      sacNone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fire(e); } });
    }
    div.querySelector(".bcard-title").addEventListener("click", () => findSimilar(p.key, p.title || ""));
    // UX4：「📄 打开」——系统阅读器打开原文（stopPropagation 防触发标题/右键行为）
    const ob = div.querySelector(".bcard-open");
    if (ob && hasFulltext(p)) ob.addEventListener("click", (e) => { e.stopPropagation(); openPdfByKey(p.key, ob); });
    // D1：卡片上的「＋分类」按钮——右击的多选集优先，否则只这一篇
    const ac = div.querySelector(".bcard-addcat");
    if (ac) ac.addEventListener("click", (e) => {
      e.stopPropagation();
      const keys = (BR.selected.has(p.key) && BR.selected.size) ? [...BR.selected] : [p.key];
      const rect = ac.getBoundingClientRect();
      openAddToCatMenu(keys, rect.left, rect.bottom + 2);
    });
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
    // 待本地 OCR 可以进入深索；只排除附件缺失、坏 PDF、OCR 已失败的终态。
    const deepN = BR.papers.filter((p) => BR.selected.has(p.key) && hasFulltext(p) && !p.deep && !extractBlocked(p)).length;
    $("#bl-sel-n").textContent = num(deepN);
    $("#bl-deep-sel").disabled = deepN === 0;
    const _a = $("#bl-addcat"); if (_a) _a.disabled = n === 0;
    // 全选框状态：所有卡片均可勾选，故以当前列表全部为分母
    const allSel = BR.papers.length > 0 && BR.papers.every((p) => BR.selected.has(p.key));
    const box = $("#bl-selall");
    box.checked = allSel;
    box.indeterminate = n > 0 && !allSel;
  }

  function renderBrowseFilterCounts(counts) {
    const sel = $("#bl-deep-filter"); if (!sel || !counts) return;
    Array.from(sel.options).forEach((opt) => {
      const base = opt.dataset.label || opt.textContent.replace(/（[\d,]+）$/, "");
      const key = opt.value || "all";
      opt.dataset.label = base;
      opt.textContent = counts[key] == null ? base : `${base}（${num(counts[key])}）`;
    });
  }
  function renderBrowseTypeCounts(counts) {
    const sel = $("#bl-type-filter"); if (!sel || !counts) return;
    Array.from(sel.options).forEach((opt) => {
      const base = opt.dataset.label || opt.textContent.replace(/（[\d,]+）$/, "");
      const key = opt.value || "all";
      opt.dataset.label = base;
      opt.textContent = counts[key] == null ? base : `${base}（${num(counts[key])}）`;
    });
  }
  function renderBrowseLabelCounts(counts) {
    const sel = $("#bl-label-filter"); if (!sel) return;
    const current = BR.objectiveLabel || "";
    const rows = Object.entries(counts || {}).sort((a, b) => Number(b[1] || 0) - Number(a[1] || 0) || a[0].localeCompare(b[0], "zh-CN"));
    sel.innerHTML = `<option value="">客观标签</option>` + rows.map(([label, count]) =>
      `<option value="${esc(label)}">${esc(label)}（${num(count)}）</option>`).join("");
    if (current && !rows.some(([label]) => label === current)) {
      const opt = document.createElement("option"); opt.value = current; opt.textContent = `${current}（0）`; sel.appendChild(opt);
    }
    sel.value = current;
  }

  function renderBrowseActiveFilters() {
    const box = $("#bl-active-filters"); if (!box) return;
    const chips = [];
    if (BR.objectiveLabel) chips.push(`<span>客观标签：<b>${esc(BR.objectiveLabel)}</b><button type="button" data-clear-filter="objective" aria-label="清除客观标签筛选">×</button></span>`);
    box.innerHTML = chips.join("");
    box.hidden = !chips.length;
  }

  async function loadPapers() {
    const myseq = ++BR.reqSeq;   // B5：请求序号守卫，防止快速切换时旧响应覆盖新选择
    $("#bl-name").textContent = BR.scope.name;
    $("#bl-list").innerHTML = ""; $("#bl-msg").textContent = "加载中…";
    BR.selected.clear(); refreshSelUI();
    renderBrowseActiveFilters();
    const om = $("#bl-more"); if (om) om.remove();   // W1：换范围/重载时清掉旧「加载更多」按钮
    try {
      const params = new URLSearchParams({ sort: BR.sort, limit: "300", offset: "0" });
      const s = BR.scope;
      if (s.type === "topic") params.set("topic", s.id);
      else if (s.type === "zotero") params.set("collection", s.id);
      else if (s.type === "kbcat") params.set("category", s.id);
      // 文献状态筛选（深索 / OCR / 检索摘要，与左侧范围叠加）
      if (BR.deepFilter) params.set("deep", BR.deepFilter);
      if (BR.sourceType) params.set("source_type", BR.sourceType);
      if (BR.objectiveLabel) params.set("objective_label", BR.objectiveLabel);
      if (BR.query) params.set("query", BR.query);
      const d = await jget("/papers?" + params.toString());
      if (myseq !== BR.reqSeq) return;   // B5：已有更新的请求发出，丢弃这次陈旧响应
      renderBrowseFilterCounts(d.filter_counts || {});
      renderBrowseTypeCounts(d.source_type_counts || d.type_counts || {});
      renderBrowseLabelCounts(d.objective_label_counts || {});
      BR.papers = d.papers || [];
      // W1：「（显示前 300）」这种死数字标签取消——分页由底部「加载更多」承担
      BR.total = d.total != null ? d.total : BR.papers.length;
      $("#bl-count").textContent = `· 共 ${num(BR.total)} 篇`;
      if (BR.papers.length) { $("#bl-msg").innerHTML = ""; }
      else if (s.type === "topic") { $("#bl-msg").textContent = "（该主题暂无文献）"; }
      else if (s.type === "kbcat") {
        // empty-cat-hint-deadlock：给出可操作路径 + 跳转按钮，而非死胡同
        $("#bl-msg").innerHTML = `该分类暂无文献。去「⭐ 全部文献」勾选后点「＋ 加入分类」，或右键文献卡 / 拖到左侧分类上归类。 <a class="ag-link" id="bl-go-all">→ 去全部文献</a>`;
        const g = $("#bl-go-all"); if (g) g.addEventListener("click", () => selectCollection(null, "全部", null));
      } else if (BR.query || BR.deepFilter || BR.sourceType || BR.objectiveLabel) {
        const bits = [];
        if (BR.query) bits.push(`题录：${BR.query}`);
        if (BR.sourceType) bits.push($("#bl-type-filter").selectedOptions[0]?.dataset.label || "当前文献类型");
        if (BR.deepFilter) bits.push($("#bl-deep-filter").selectedOptions[0]?.dataset.label || "当前状态");
        if (BR.objectiveLabel) bits.push(`客观标签：${BR.objectiveLabel}`);
        $("#bl-msg").textContent = `（没有符合“${bits.join(" · ")}”的文献）`;
      } else { $("#bl-msg").textContent = "（该分类暂无文献）"; }
      const frag = document.createDocumentFragment();
      BR.papers.forEach((p) => frag.appendChild(paperCard(p)));
      $("#bl-list").appendChild(frag);
      refreshSelUI();
      renderLoadMore();   // W1：还有没显示的就给「加载更多」
    } catch (e) {
      if (myseq !== BR.reqSeq) return;
      $("#bl-msg").textContent = "加载失败：" + e.message;
    }
  }

  // 深索/提取状态推进期间，只更新已渲染卡片的状态徽标——不动勾选、滚动、分页。
  // 这个 staleness（用户 2026-07-15 反馈）。后端 /papers 是实时算的，这里只是让前端跟上。
  async function refreshBrowseDeepState() {
    if (!browseLoaded || $("#panel-browse").hidden || !BR.papers.length) return;
    if ($("#bl-list .bcard.dragging")) return;   // 有卡正在拖拽 → 本轮跳过（拖拽<1s，下轮进度变化会再刷）
    const myseq = BR.reqSeq;                       // 不自增：换范围/排序会 bump reqSeq，本次结果作废
    try {
      const params = new URLSearchParams({ sort: BR.sort, limit: String(BR.papers.length), offset: "0" });
      const s = BR.scope;
      if (s.type === "topic") params.set("topic", s.id);
      else if (s.type === "zotero") params.set("collection", s.id);
      else if (s.type === "kbcat") params.set("category", s.id);
      if (BR.deepFilter) params.set("deep", BR.deepFilter);
      if (BR.sourceType) params.set("source_type", BR.sourceType);
      if (BR.objectiveLabel) params.set("objective_label", BR.objectiveLabel);
      if (BR.query) params.set("query", BR.query);
      const d = await jget("/papers?" + params.toString());
      if (myseq !== BR.reqSeq) return;             // 期间用户切了范围/排序 → 丢弃
      const fresh = new Map((d.papers || []).map((x) => [x.key, x]));
      BR.papers.forEach((p) => {
        const f = fresh.get(p.key);
        if (!f) return;
        if (p.no_text === f.no_text && p.deep === f.deep && p.has_summary === f.has_summary
            && p.summary_invalid === f.summary_invalid && p.summary_error === f.summary_error
            && JSON.stringify(p.extract_status || {}) === JSON.stringify(f.extract_status || {})) return;
        p.no_text = f.no_text; p.deep = f.deep; p.has_summary = f.has_summary;
        p.summary_invalid = f.summary_invalid; p.summary_error = f.summary_error;
        p.extract_status = f.extract_status || {};
        const old = $(`#bl-list .bcard[data-key="${CSS.escape(p.key)}"]`);
        if (old) old.replaceWith(paperCard(p));    // 整卡重建：勾选态按 BR.selected 重算、事件重绑
      });
      refreshSelUI();                              // 深索候选随提取状态变化，需重算
    } catch (e) { /* 静默：后台刷新，不打扰用户 */ }
  }

  // ── W1：浏览页分页——「加载更多」追加下一页（不清勾选、不重置滚动位置）──
  function renderLoadMore() {
    const old = $("#bl-more"); if (old) old.remove();
    if (!(BR.papers.length < (BR.total || 0))) return;
    const btn = document.createElement("button");
    btn.id = "bl-more"; btn.className = "bl-more ghost2b";
    btn.textContent = `加载更多（已显示 ${num(BR.papers.length)} / 共 ${num(BR.total)} 篇）`;
    btn.addEventListener("click", () => loadMorePapers(btn));
    // 放在滚动列表内部末尾（.bl-list 是滚动容器），滚到底自然看到；loadPapers 清空列表时会一并清掉
    $("#bl-list").appendChild(btn);
  }
  async function loadMorePapers(btn) {
    const myseq = ++BR.reqSeq;   // 与 loadPapers 共用守卫：期间换了范围/排序就丢弃本次追加
    if (btn) { btn.disabled = true; btn.textContent = "加载中…"; }
    try {
      const params = new URLSearchParams({ sort: BR.sort, limit: "300", offset: String(BR.papers.length) });
      const s = BR.scope;
      if (s.type === "topic") params.set("topic", s.id);
      else if (s.type === "zotero") params.set("collection", s.id);
      else if (s.type === "kbcat") params.set("category", s.id);
      if (BR.deepFilter) params.set("deep", BR.deepFilter);
      if (BR.sourceType) params.set("source_type", BR.sourceType);
      if (BR.objectiveLabel) params.set("objective_label", BR.objectiveLabel);
      if (BR.query) params.set("query", BR.query);
      const d = await jget("/papers?" + params.toString());
      if (myseq !== BR.reqSeq) return;
      // W1：recommend/year 排序在两次请求间可能因深索完成/改档而重排——按 key 去重防重复卡片（漏条无法前端补救，可接受）
      const seen = new Set(BR.papers.map((p) => p.key));
      const more = (d.papers || []).filter((p) => !seen.has(p.key));
      if (d.total != null) BR.total = d.total;
      BR.papers = BR.papers.concat(more);
      const frag = document.createDocumentFragment();
      more.forEach((p) => frag.appendChild(paperCard(p)));
      $("#bl-list").appendChild(frag);
      $("#bl-count").textContent = `· 共 ${num(BR.total)} 篇`;
      refreshSelUI();
      renderLoadMore();
    } catch (e) {
      if (myseq !== BR.reqSeq) return;
      if (btn) { btn.disabled = false; btn.textContent = "加载失败，点此重试"; }
    }
  }

  // 触发对一批 key 的深索
  async function deepIndexKeys(keys, btn) {
    if (!keys.length) { $("#bl-msg").textContent = "没有可深索的文献（需有受支持全文附件且尚未深索）。"; return; }
    if (btn) btn.disabled = true;
    try {
      // C7：手动深索走持久队列，撞锁自动排队；后端返回 {ok:true,queued:n}
      const r = await jpost("/index/deep", { scope: "keys:" + keys.join(",") });
      if (r && r.ok === false) { $("#bl-msg").textContent = "已有任务在跑，稍后再试。"; return; }
      localStorage.removeItem("localkb.deepDismissed");
      const n = (r && r.queued != null) ? r.queued : keys.length;
      $("#bl-msg").textContent = `已开始后台深索 ${num(n)} 篇，进度见顶部。`;
      poll(); // 让顶栏进度条接管
    } catch (e) {
      $("#bl-msg").textContent = "启动深索失败：" + e.message;
    } finally {
      if (btn) btn.disabled = false;
      refreshSelUI();
    }
  }

  // F11/F12：点「未深索」徽标 → 单篇后台深索（走持久队列，撞锁/多篇自动排队接续，不卡住）
  async function deepOneFromBadge(key, badge) {
    if (!key || badge.classList.contains("deep-pending")) return;
    const idle = badge.querySelector(".lbl-idle"), hov = badge.querySelector(".lbl-hover");
    const setLbl = (t) => { if (idle) idle.textContent = t; if (hov) hov.textContent = t; };
    const reset = () => { badge.classList.remove("deep-pending"); badge.style.pointerEvents = ""; if (idle) idle.textContent = "📋 未深索"; if (hov) hov.textContent = "⚡ 深索该篇"; };
    badge.classList.add("deep-pending"); badge.style.pointerEvents = "none"; setLbl("⏳ 已入队");
    try {
      const r = await jpost("/index/deep", { scope: "keys:" + key });
      if (r && r.ok === false) { flashToast("已有任务在跑，稍后再试。"); reset(); return; }
      if (r && r.queued === 0) { flashToast("这篇可能已在深索，或全文附件当前不可读，未新增任务。"); reset(); }
      else { localStorage.removeItem("localkb.deepDismissed"); flashToast("已开始后台深索这篇，进度见顶部。"); poll(); }
    } catch (e) { flashToast("启动深索失败：" + (e.message || e)); reset(); }
  }

  // 全选 / 取消（所有卡片均可勾选：深索按钮只对未深索有 PDF 的生效，加分类/引文对全部选中生效）
  $("#bl-selall").addEventListener("change", () => {
    const on = $("#bl-selall").checked;
    if (on) BR.papers.forEach((p) => BR.selected.add(p.key));
    else BR.papers.forEach((p) => BR.selected.delete(p.key));
    // 同步 DOM 上的勾选框
    $$("#bl-list input[type=checkbox]").forEach((cb) => { cb.checked = on; });
    refreshSelUI();
  });
  $("#bl-sort").addEventListener("change", () => { BR.sort = $("#bl-sort").value; loadPapers(); });
  $("#bl-deep-filter").addEventListener("change", () => { BR.deepFilter = $("#bl-deep-filter").value; loadPapers(); });
  $("#bl-type-filter").addEventListener("change", () => { BR.sourceType = $("#bl-type-filter").value; loadPapers(); });
  $("#bl-label-filter").addEventListener("change", () => { BR.objectiveLabel = $("#bl-label-filter").value; loadPapers(); });
  $("#bl-active-filters").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-clear-filter]");
    if (!b) return;
    if (b.dataset.clearFilter === "objective") BR.objectiveLabel = "";
    loadPapers();
  });
  $("#bl-deep-sel").addEventListener("click", () => {
    const keys = BR.papers.filter((p) => BR.selected.has(p.key) && hasFulltext(p) && !p.deep && !extractBlocked(p)).map((p) => p.key);
    deepIndexKeys(keys, $("#bl-deep-sel"));
  });
  // D1：工具栏「＋ 加入分类」——对当前勾选集打开加入分类菜单
  const _blAdd = $("#bl-addcat");
  if (_blAdd) _blAdd.addEventListener("click", (e) => {
    if (!BR.selected.size) return;
    e.stopPropagation();   // 否则冒泡到 document 的关闭浮层处理器会立刻关掉刚打开的菜单
    const rect = _blAdd.getBoundingClientRect();
    openAddToCatMenu([...BR.selected], rect.left, rect.bottom + 2);
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
        msg.innerHTML = (r.indexed ? "（已入库，检索/综述库可见）" : "（已存盘，重建索引后可检索）") +
          ` <a class="save-goto" href="#">→ 去综述库查看</a>`;
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
    // A6：放行前据 /setup/detect 判断是否真有可用 key（默认本地后端 + 硅基流动无 key 会直接失败）
    if (!(await ensureLlmKey("chat"))) return;   // UX3：对话场景缺 Key 直接展开本页「模型设置」，不再跳设置弹窗
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
      // BF19：先验 HTTP 状态与流类型——后端 500/返回 JSON 错误时别硬当 SSE 读（否则空气泡+误导去查 Key），
      // 把响应体里的真实原因（detail/error/msg 或纯文本前 200 字）亮出来
      const ctype = resp.headers.get("Content-Type") || "";
      if (!resp.ok || ctype.indexOf("text/event-stream") < 0) {
        let detail = "";
        try {
          const raw = await resp.text();
          try { const j = JSON.parse(raw); detail = j.detail || j.error || j.msg || ""; } catch (_) {}
          if (!detail) detail = (raw || "").slice(0, 200);
        } catch (_) {}
        bot.textContent = "⚠ 服务返回错误：" + (detail || ("HTTP " + resp.status));
        return;
      }
      const reader = resp.body.getReader(); const dec = new TextDecoder("utf-8"); let buf = "", answer = "", srcHits = [], warnEl = null, errMsg = "";
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
          else if (j.error) {
            // C6：检索后端失败是【前置警告】——server 随后仍会照常流式作答。渲染成独立警示条（不占正文，
            // delta 不会把它冲掉），且不阻止把回答留进 history / 出「保存此答案」按钮。仅「全程无回答」才算终止性错误。
            errMsg = j.error;
            if (!warnEl) {
              warnEl = document.createElement("div");
              warnEl.style.cssText = "color:#b45309;background:rgba(245,158,11,.12);border:1px solid rgba(245,158,11,.4);border-radius:8px;padding:6px 10px;margin-bottom:6px;font-size:13px";
              if (bot.parentNode) bot.parentNode.insertBefore(warnEl, bot);
            }
            warnEl.textContent = "⚠ " + j.error;
          }
        }
      }
      // 有回答就写进 history（前置警告不影响回答有效性）；出「保存此答案」按钮
      if (answer) { history.push({ role: "user", content: q }, { role: "assistant", content: answer }); addSaveBtn(bot, q, answer, srcHits); }
      // 全程无回答：若有 error 则为终止性错误（warnEl 已显示，bot 兜底也补一句）；否则给空回复兜底
      else if (errMsg) { if (!warnEl) bot.textContent = "⚠ " + errMsg; }
      else bot.textContent = "⚠ 模型没有返回内容，请重试，或在设置里检查对话模型/Key。";
    } catch (e) { bot.textContent = "⚠ 请求失败：" + e; }
    finally { $("#chat-go").disabled = false; }
  }
  $("#chat-go").addEventListener("click", doChat);
  $("#chat-q").addEventListener("keydown", (e) => {
    // Enter 发送，但发送中（按钮禁用）不重复提交，避免并发污染 history
    if (e.key === "Enter" && !e.shiftKey && !$("#chat-go").disabled) { e.preventDefault(); doChat(); }
  });
  $("#chat-q").addEventListener("input", (e) => { e.target.style.height = "auto"; e.target.style.height = Math.min(e.target.scrollHeight, 140) + "px"; });
  // UX5：「🧹 新对话」——清上下文与聊天记录、恢复欢迎语（欢迎语在启动时快照；重建 DOM 后要重挂里面的按钮）
  const CHAT_HINT_HTML = $("#chat-log") ? $("#chat-log").innerHTML : "";
  const _chatNew = $("#chat-new");
  if (_chatNew) _chatNew.addEventListener("click", () => {
    if ($("#chat-go").disabled) { flashToast("正在回答中，请稍候再开新对话。"); return; }
    history.length = 0;
    $("#chat-log").innerHTML = CHAT_HINT_HTML;
    const b = $("#chat-to-agent"); if (b) b.addEventListener("click", () => switchTab("agent"));
  });

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
  provSel.addEventListener("change", () => {
    applyProvider(provSel.value, false);
    // BF20：各家 key 不通用——换服务商必须清空输入框与掩码占位，并把 api_key 存成空串（不得沿用旧商的 key）
    $("#set-key").value = "";
    $("#set-key").placeholder = "该服务商尚未配置 Key";
    saveChatModel({ resetKey: true });
  });
  const SETTINGS_PANE_KEY = "localkb.settingsPane";
  function showSettingsPane(name, targetId) {
    const panes = $$(".settings-pane"), buttons = $$(".settings-nav-btn");
    if (!Array.from(panes).some((p) => p.dataset.settingsPane === name)) name = "search";
    panes.forEach((p) => (p.hidden = p.dataset.settingsPane !== name));
    buttons.forEach((b) => {
      const active = b.dataset.settingsPane === name;
      b.classList.toggle("active", active);
      b.setAttribute("aria-current", active ? "page" : "false");
    });
    try { localStorage.setItem(SETTINGS_PANE_KEY, name); } catch (_) {}
    const wrap = $(".settings-pane-wrap"); if (wrap) wrap.scrollTop = 0;
    if (targetId) requestAnimationFrame(() => {
      const target = $("#" + targetId);
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }
  function settingsPaneForTarget(targetId) {
    const target = targetId && $("#" + targetId);
    const pane = target && target.closest(".settings-pane");
    return pane ? pane.dataset.settingsPane : "";
  }
  $$(".settings-nav-btn").forEach((b) => b.addEventListener("click", () => showSettingsPane(b.dataset.settingsPane)));

  function openSettings(targetId) {
    if (typeof targetId !== "string") targetId = "";  // 允许直接作为 click 监听器使用
    // LLM 服务商字段已搬到对话页折叠区，设置弹窗不再回填它们（避免覆盖对话页已填的 key）
    $("#settings-modal").hidden = false;
    let pane = settingsPaneForTarget(targetId);
    if (!pane) { try { pane = localStorage.getItem(SETTINGS_PANE_KEY) || "search"; } catch (_) { pane = "search"; } }
    showSettingsPane(pane, targetId);
    loadSac();    // 进入设置面板即拉取 SAC 当前状态并回填
    loadEngine(); // 同时回填检索引擎当前后端/是否已设 key
      loadDiscipline(); // 回填来源评价学科下拉
    loadAutoUpdate(); // 回填自动更新开关/间隔
    loadRetrievalMemory(); // 回填检索组件的空闲释放时间与当前状态
    loadOnlyPdf();    // 回填「只导入有全文附件」开关（旧函数名保留兼容）
    loadBackup();     // 回填备份位置/自动备份，并列出已有备份包
    checkUpdate(true); // 静默查一次新版（用缓存、失败不吭声），有新版才显示升级面板
    loadMirror();     // 回填国内镜像地址
  }

  // ── 自动更新（按天 + 指定时刻 + 补跑；即改即存）──
  function _auDaysLabel(d) { return d === 1 ? "每天" : `每 ${d} 天`; }
  async function loadAutoUpdate() {
    const en = $("#au-enabled"); if (!en) return;
    const dd = $("#au-days"), tm = $("#au-time"), cu = $("#au-catchup");
    try {
      const s = await jget("/setup/auto_update");
      en.checked = !!s.enabled;
      if (dd) dd.value = String(Math.min(30, Math.max(1, s.interval_days || 1)));
      if (tm) tm.value = s.at_time || "07:00";
      if (cu) cu.checked = s.catch_up_on_launch !== false;
      // 删除同步：folder 模式给一个默认关的开关（防误删临时挪走的 PDF）；zotero 模式露出手动「清理已删除」按钮
      const note = $("#au-del-note"), pb = $("#au-purge"), dsRow = $("#au-delsync-row"), ds = $("#au-delsync");
      if (s.source === "folder") {
        if (dsRow) dsRow.hidden = false;
        if (ds) ds.checked = !!s.delete_sync;
        if (note) note.textContent = s.delete_sync
          ? "🗑 已开启同步删除：文件夹里删掉的全文文件下次更新会从库中清出。"
          : "🗑 未开启同步删除：文件夹里删掉的全文文件仍留在库中（防误删）。要清出请勾选上面。";
        if (pb) pb.hidden = true;
      } else {
        if (dsRow) dsRow.hidden = true;
        if (note) note.textContent = "🗑 删除同步：Zotero 里删掉的文献不会自动清出，可手动清理 →";
        if (pb) pb.hidden = false;
      }
    } catch (e) {}
  }
  async function saveAutoUpdate() {
    const en = $("#au-enabled"), dd = $("#au-days"), tm = $("#au-time"), cu = $("#au-catchup"), ds = $("#au-delsync"), msg = $("#au-msg");
    let days = parseInt(dd && dd.value, 10); if (!(days >= 1 && days <= 30)) days = 1;
    if (dd) dd.value = String(days);
    const at = (tm && /^\d{1,2}:\d{2}$/.test(tm.value)) ? tm.value : "07:00";
    try {
      await jpost("/setup/auto_update", { enabled: en.checked, interval_days: days, at_time: at, catch_up_on_launch: !!(cu && cu.checked), delete_sync: !!(ds && ds.checked) });
      if (msg) msg.textContent = en.checked
        ? `已开启：${_auDaysLabel(days)} ${at} 检查一次新增文献并自动增量更新${(cu && cu.checked) ? "；错过会在开应用时补跑" : ""}。`
        : "已关闭：只能用顶栏「⟳ 手动更新知识库」手动更新。";
    } catch (e) { if (msg) msg.textContent = "保存失败：" + e.message; }
  }
  (function wireAutoUpdate() {
    const en = $("#au-enabled"), dd = $("#au-days"), tm = $("#au-time"), cu = $("#au-catchup"), ds = $("#au-delsync"), pb = $("#au-purge");
    if (en) en.addEventListener("change", saveAutoUpdate);
    if (dd) dd.addEventListener("change", saveAutoUpdate);
    if (tm) tm.addEventListener("change", saveAutoUpdate);
    if (cu) cu.addEventListener("change", saveAutoUpdate);
    if (ds) ds.addEventListener("change", async () => { await saveAutoUpdate(); loadAutoUpdate(); });   // 保存后刷新删除同步提示
    if (pb) pb.addEventListener("click", async () => {
      const note = $("#au-del-note"); const lbl = pb.textContent;
      pb.disabled = true; pb.textContent = "清理中…";
      try {
        const r = await jpost("/setup/purge_deleted", {});
        if (note) note.textContent = (r && r.msg) || "已清理。";
      } catch (e) { if (note) note.textContent = "清理失败：" + (e.message || e); }
      pb.disabled = false; pb.textContent = lbl;
    });
  })();

  // ── 检索内存：首次检索加载，空闲 N 分钟后释放（0=始终保留）──
  function renderRetrievalMemory(s, saved = false) {
    const msg = $("#ret-mem-msg"); if (!msg || !s) return;
    const mins = Number(s.idle_unload_min || 0);
    let state = "";
    if (s.loading) state = "正在为这次检索加载组件…";
    else if (s.active > 0) state = `当前有 ${s.active} 个检索正在进行，不会中途释放。`;
    else if (!s.loaded) state = "当前已释放，内存占用较低；下次检索会自动重新加载。";
    else if (mins === 0) state = "当前已加载，并会一直保留；下次检索无需重新准备。";
    else {
      const remain = Math.max(0, Number(s.remaining_s || 0));
      const wait = remain >= 60 ? `约 ${Math.max(1, Math.ceil(remain / 60))} 分钟` : "不到 1 分钟";
      state = `当前已加载；如果不再检索，${wait}后释放。`;
    }
    msg.className = "hint";
    msg.textContent = (saved ? "✓ 已保存。" : "") + state + " 释放不会删除文献或索引。";
  }
  async function loadRetrievalMemory() {
    const sel = $("#ret-idle-min"); if (!sel) return;
    try {
      const s = await jget("/setup/retrieval_memory");
      const val = String(Number(s.idle_unload_min || 0));
      if (![...sel.options].some(o => o.value === val)) sel.add(new Option(`${val} 分钟`, val));
      sel.value = val;
      renderRetrievalMemory(s);
    } catch (e) {
      const msg = $("#ret-mem-msg"); if (msg) msg.textContent = "读取内存设置失败：" + e.message;
    }
  }
  (function wireRetrievalMemory() {
    const sel = $("#ret-idle-min"); if (!sel) return;
    sel.addEventListener("change", async () => {
      const msg = $("#ret-mem-msg"); if (msg) msg.textContent = "保存中…";
      try {
        const s = await jpost("/setup/retrieval_memory", { idle_unload_min: Number(sel.value) });
        renderRetrievalMemory(s, true);
      } catch (e) { if (msg) msg.textContent = "保存失败：" + e.message; }
    });
  })();

  // 只导入有全文附件（Zotero 模式）：旧设置键保留，改后需点「手动更新知识库」重建题录索引
  async function loadOnlyPdf() {
    const cb = $("#set-onlypdf"); if (!cb) return;
    try { const d = await jget("/setup/detect"); cb.checked = !!d.import_only_pdf; } catch (e) {}
  }
  (function wireOnlyPdf() {
    const cb = $("#set-onlypdf"); if (!cb) return;
    cb.addEventListener("change", async () => {
      const msg = $("#onlypdf-msg");
      try {
        await jpost("/setup/import_only_pdf", { only_pdf: cb.checked });
        if (msg) msg.textContent = (cb.checked
          ? "已设为只导入有全文附件的文献。"
          : "已允许导入纯题录（无受支持全文附件的也进库）。") + "点顶栏「手动更新知识库」重建题录索引即生效。";
      } catch (e) { if (msg) msg.textContent = "保存失败：" + e.message; }
    });
  })();
  // 重新查看引导：关设置、按当前状态重开首启向导
  (function wireRewizard() {
    const b = $("#set-rewizard"); if (!b) return;
    b.addEventListener("click", async () => {
      $("#settings-modal").hidden = true;
      try {
        const d = await jget("/setup/detect");
        WZ.detect = d;
        if (d.backend === "api" || d.backend === "local") WZ.backend = d.backend;
      } catch (e) {}
      $("#wizard").hidden = false;
      renderStep1();
    });
  })();

  function applyDisciplineMeta(s, id) {
    const meta = (s.disciplines || []).find((d) => d.id === id) || {};
    DISC.id = id || s.current || DISC.id;
    DISC.name = meta.name || s.current_name || DISC.name;
    DISC.notice = meta.notice || meta.description || s.notice || s.discipline_notice || "";
    absorbBandNames(meta); absorbBandNames(s);
    const note = $("#disc-note");
    if (note) note.textContent = DISC.notice || "普通页面只显示客观标签；四档评价用于排序、总览、手动改档和研究工作流。";
  }
  // ── 来源评价学科：整库锁定；娱乐学科的显示名和说明完全由后端元数据下发 ──
  async function loadDiscipline() {
    const sel = $("#disc-select"); if (!sel) return;
    try {
      const s = await jget("/setup/discipline");
      // 名称直接用后端 name（个人档已含「（开发者增强，欢迎试用）」）——不再前端追加，避免「（个人增强）（个人增强）」双重（F64）
      sel.innerHTML = (s.disciplines || []).map(d =>
        `<option value="${esc(d.id)}">${esc(d.name)}</option>`).join("");
      if (s.current) sel.value = s.current;
      applyDisciplineMeta(s, s.current || sel.value);
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
      const picked = sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].text : sel.value;
      const r = await jpost("/setup/discipline", { discipline: sel.value });
      await loadDiscipline();
      msg.textContent = "已切换：" + (DISC.name || picked || r.current) + "（检索即时生效；四档分布后台重算后自动刷新）";
      refreshAfterDiscipline();
    } catch (e) { msg.textContent = "保存失败：" + e.message; }
  });
  $("#btn-settings").addEventListener("click", openSettings);
  $("#set-close").addEventListener("click", () => $("#settings-modal").hidden = true);

  // W2：设置 / wiki 详情 / 入库进度三个弹窗补 Esc 与点遮罩关闭（参照 uiConfirm 的 onBackdrop/onKey 写法）。
  // 只是收起视图，后台任务（入库/构建）不中断；确认框开着时让 uiConfirm 自己吃掉 Esc，不连带关底下的弹窗。
  // EN-F3：时间线弹层 #wk-tl-modal 一并纳入（它可能叠在 wiki 详情之上，Esc 列表里放最前、先关最上层）
  ["#settings-modal", "#wiki-modal", "#ingest-modal", "#wk-tl-modal"].forEach((sel) => {
    const m = $(sel);
    if (m) m.addEventListener("mousedown", (e) => { if (e.target === m) m.hidden = true; });
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    // uiConfirm 在捕获阶段已 preventDefault 并自行关闭——此时确认框 hidden 已翻真，
    // 必须靠 defaultPrevented 识别「这次 Esc 已被确认框消费」，否则会连带关掉底下的弹窗
    if (e.defaultPrevented) return;
    const cm = $("#confirm-modal"); if (cm && !cm.hidden) return;
    for (const sel of ["#wk-tl-modal", "#ingest-modal", "#wiki-modal", "#settings-modal"]) {   // EN-F3：时间线在最上层，先关它
      const m = $(sel);
      if (m && !m.hidden) { m.hidden = true; return; }   // 一次只关最上层一个
    }
  });

  // ── F8：外观（主题 / 字号）——存 localStorage，冷启动即应用。跟随系统时解析成具体 light/dark 写到 data-theme（CSS 只需处理 data-theme）──
  function applyAppearance() {
    const a = safeParse(localStorage.getItem("localkb.ui"), {});
    const pref = a.theme || "system", fs = a.fontsize || "normal";
    const sysDark = !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
    const theme = pref === "system" ? (sysDark ? "dark" : "light") : pref;
    const root = document.documentElement;
    root.setAttribute("data-theme", theme);
    root.setAttribute("data-fontsize", fs);
    const ts = $("#ui-theme"); if (ts) ts.value = pref;   // 下拉显示用户选择（system/light/dark），非解析值
    const fsel = $("#ui-fontsize"); if (fsel) fsel.value = fs;
    const quick = $("#theme-quick-toggle");
    if (quick) {
      quick.dataset.theme = theme;
      const next = theme === "dark" ? "明亮" : "夜间";
      quick.setAttribute("aria-label", `切换到${next}模式`);
      quick.title = `切换到${next}模式`;
    }
  }
  function saveAppearance() {
    const a = safeParse(localStorage.getItem("localkb.ui"), {});
    const ts = $("#ui-theme"), fsel = $("#ui-fontsize");
    if (ts) a.theme = ts.value;
    if (fsel) a.fontsize = fsel.value;
    localStorage.setItem("localkb.ui", JSON.stringify(a));
    applyAppearance();
  }
  { const ts = $("#ui-theme"); if (ts) ts.addEventListener("change", saveAppearance);
    const fsel = $("#ui-fontsize"); if (fsel) fsel.addEventListener("change", saveAppearance);
    const quick = $("#theme-quick-toggle"); if (quick) quick.addEventListener("click", () => {
      const a = safeParse(localStorage.getItem("localkb.ui"), {});
      a.theme = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      localStorage.setItem("localkb.ui", JSON.stringify(a));
      applyAppearance();
    });
    // 跟随系统时，系统主题变化即时重解析
    if (window.matchMedia) { try { window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
      const a = safeParse(localStorage.getItem("localkb.ui"), {}); if ((a.theme || "system") === "system") applyAppearance();
    }); } catch (e) {} } }
  applyAppearance();

  // ── 备份与恢复 ────────────────────────────────────────────
  // 设计见 backup.py 文件头。一句话：备份包是**静态 zip**，放进云盘同步是安全的；
  // 而向量索引是持续读写的数据库，让云盘去实时同步它，早晚把索引搞坏。
  const _bkSize = (b) => b >= 1e9 ? (b / 1e9).toFixed(2) + " GB"
                       : b >= 1e6 ? (b / 1e6).toFixed(1) + " MB"
                       : Math.max(1, Math.round(b / 1e3)) + " KB";

  async function loadBackup() {
    const dirEl = $("#bk-dir"); if (!dirEl) return;
    try {
      const c = await jget("/backup/config");
      dirEl.value = c.dir || "";
      dirEl.placeholder = "（默认：" + (c.effective_dir || "数据目录下的 backups") + "）";
      $("#bk-with-index").checked = !!c.include_index;
      $("#bk-auto").checked = !!c.auto;
      $("#bk-days").value = c.every_days || 7;
      $("#bk-keep").value = c.keep || 3;
      const msg = $("#bk-msg");
      if (msg) msg.textContent = c.last_at ? "上次备份：" + c.last_at : "还没有备份过。";
    } catch (e) {}
    // 原生目录选择器只有 launcher 注入了 pywebview 桥时才有；浏览器回退时藏起来，别留死按钮
    const canPick = !!(window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder);
    const pick = $("#bk-pick"); if (pick) pick.hidden = !canPick;
    refreshBkSize();
    renderBkList();
  }

  async function refreshBkSize() {
    const el = $("#bk-size"); if (!el) return;
    const withIdx = $("#bk-with-index").checked ? 1 : 0;
    el.textContent = "…";
    try {
      const r = await jget("/backup/estimate?with_index=" + withIdx);
      el.textContent = r.ok ? "≈ " + _bkSize(r.bytes) : "";
    } catch (e) { el.textContent = ""; }
  }

  async function renderBkList() {
    const box = $("#bk-list"); if (!box) return;
    let r;
    try { r = await jget("/backup/list"); } catch (e) { return; }
    const items = (r && r.items) || [];
    if (!items.length) { box.innerHTML = '<p class="hint">还没有备份包。</p>'; return; }
    box.innerHTML = items.map((it) => {
      if (it.broken) return `<div class="bk-item"><b>${it.name}</b> <span class="danger-text">（包已损坏，不能用来恢复）</span></div>`;
      const m = it.manifest || {};
      const tags = [m.includes_index ? "含索引" : "仅手写资产"];
      if (m.has_api_key) tags.push("⚠️ 含密钥");
      const c = m.counts || {};
      const detail = [c.wiki_pages != null ? c.wiki_pages + " 篇综述" : null,
                      c.papers != null ? c.papers + " 条文献" : null,
                      c.agent_outputs ? c.agent_outputs + " 个交付物主题" : null]
                     .filter(Boolean).join(" · ");
      return `<div class="bk-item">
        <div><b>${it.mtime}</b> · ${_bkSize(it.size)} · ${tags.join(" / ")}</div>
        <div class="hint">${detail || "&nbsp;"}</div>
        <button class="ghost danger bk-restore" data-path="${it.path.replace(/"/g, "&quot;")}">↩ 从这个包恢复</button>
      </div>`;
    }).join("");
    box.querySelectorAll(".bk-restore").forEach(b =>
      b.addEventListener("click", () => doRestore(b.dataset.path)));
  }

  async function pollBackup(doneMsg) {
    const msg = $("#bk-msg");
    for (let i = 0; i < 3600; i++) {
      await new Promise(r => setTimeout(r, 700));
      let s;
      try { s = await jget("/backup/status"); } catch (e) { continue; }
      if (s.running) {
        const p = s.total ? ` ${s.done}/${s.total}` : "";
        if (msg) msg.textContent = (s.stage || "处理中") + p + "…";
        continue;
      }
      if (s.error) { if (msg) msg.textContent = "❌ " + s.error; return null; }
      if (msg) msg.textContent = doneMsg(s.result || {});
      return s.result || {};
    }
    return null;
  }

  async function doBackup() {
    const btn = $("#bk-create"), msg = $("#bk-msg");
    const withKey = $("#bk-with-key").checked;
    if (withKey && !(await uiConfirm(
        "备份包里将包含你的 API 密钥。\n\n" +
        "这个 zip 要是传到云盘、发给别人、或者存进 U 盘弄丢了，密钥就等于泄漏了。\n\n" +
        "确定包含密钥吗？（不含也没关系，恢复后重填一次即可）",
        { title: "确认在备份中包含密钥？", okText: "仍然包含", danger: true }))) return;
    btn.disabled = true;
    if (msg) msg.textContent = "打包中…";
    try {
      const r = await jpost("/backup/create", {
        include_index: $("#bk-with-index").checked,
        include_key: withKey,
      });
      if (!r.ok) { if (msg) msg.textContent = "❌ " + (r.error || "备份失败"); return; }
      await pollBackup((m) => "✅ 已备份 → " + (m.path || "") + "（" + _bkSize(m.size || 0) + "）");
      await renderBkList();
    } catch (e) {
      if (msg) msg.textContent = "❌ " + e;
    } finally {
      btn.disabled = false;
    }
  }

  async function doRestore(path) {
    const msg = $("#bk-msg");
    let info;
    try { info = await jpost("/backup/inspect", { path }); } catch (e) {
      await uiNotice("读不了这个包：" + e, { title: "无法读取备份包" }); return;
    }
    if (!info.ok) { await uiNotice(info.err || info.error || "这个备份包不可用", { title: "备份包不可用" }); return; }

    const warns = (info.warnings || []).map(w => "· " + w).join("\n");
    if (!(await uiConfirm(
        "要从这个备份恢复吗？\n\n" +
        "备份时间：" + ((info.manifest || {}).created || "?") + "\n" +
        (warns ? "\n注意：\n" + warns + "\n" : "") +
        "\n你现在的数据不会被删掉——会先整体挪进一个 _restore_backup_<时间> 文件夹，" +
        "万一恢复错了还能捞回来。\n\n恢复完需要重启应用。",
        { title: "从这个备份恢复？", okText: "开始恢复", danger: true }))) return;

    if (msg) msg.textContent = "恢复中…";
    try {
      const r = await jpost("/backup/restore", { path });
      if (!r.ok) { if (msg) msg.textContent = "❌ " + (r.error || "恢复失败"); return; }
      const res = await pollBackup(() => "✅ 恢复完成");
      if (res) await uiNotice("恢复完成。\n\n" + (res.msg || "") +
                     "\n\n请关闭并重新打开 PaperPiggy —— 内存里还是旧的索引。", { title: "恢复完成" });
    } catch (e) {
      if (msg) msg.textContent = "❌ " + e;
    }
  }

  async function saveBkConf(patch) {
    try {
      const r = await jpost("/backup/config", patch);
      if (!r.ok) { const m = $("#bk-msg"); if (m) m.textContent = "❌ " + (r.error || "保存失败"); return false; }
      const dirEl = $("#bk-dir");
      if (dirEl && r.effective_dir) dirEl.placeholder = "（默认：" + r.effective_dir + "）";
      await renderBkList();
      return true;
    } catch (e) { return false; }
  }

  {
    const bkc = $("#bk-create"); if (bkc) bkc.addEventListener("click", doBackup);
    const bwi = $("#bk-with-index"); if (bwi) bwi.addEventListener("change", () => {
      refreshBkSize(); saveBkConf({ include_index: bwi.checked });
    });
    const bka = $("#bk-auto"); if (bka) bka.addEventListener("change", () => saveBkConf({ auto: bka.checked }));
    const bkd = $("#bk-days"); if (bkd) bkd.addEventListener("change", () => saveBkConf({ every_days: +bkd.value || 7 }));
    const bkk = $("#bk-keep"); if (bkk) bkk.addEventListener("change", () => saveBkConf({ keep: +bkk.value || 3 }));
    const bkdir = $("#bk-dir"); if (bkdir) bkdir.addEventListener("change", () => saveBkConf({ dir: bkdir.value.trim() }));
    const bko = $("#bk-open"); if (bko) bko.addEventListener("click", async () => {
      const r = await jpost("/backup/open_dir", {});
      if (!r.ok) await uiNotice("打不开：" + (r.error || ""), { title: "无法打开备份文件夹" });
    });
    const bkp = $("#bk-pick"); if (bkp) bkp.addEventListener("click", async () => {
      try {
        const dir = await window.pywebview.api.pick_folder();
        if (dir) { bkdir.value = dir; await saveBkConf({ dir }); }
      } catch (e) {}
    });

    // 🧹 清空并从头重建索引（两步确认；destructive）
    const rbOpen = $("#rb-open"), rbConfirm = $("#rb-confirm"),
          rbCancel = $("#rb-cancel"), rbGo = $("#rb-go"), rbMsg = $("#rb-msg");
    if (rbOpen) rbOpen.addEventListener("click", () => {
      if (rbConfirm) rbConfirm.hidden = false;
      rbOpen.hidden = true; if (rbMsg) rbMsg.textContent = "";
    });
    if (rbCancel) rbCancel.addEventListener("click", () => {
      if (rbConfirm) rbConfirm.hidden = true;
      if (rbOpen) rbOpen.hidden = false;
    });
    if (rbGo) rbGo.addEventListener("click", async () => {
      rbGo.disabled = true; const old = rbGo.textContent; rbGo.textContent = "清空中…";
      try {
        const r = await jpost("/index/reset", { confirm: true });
        if (r && r.busy) {
          if (rbMsg) rbMsg.textContent = "⚠ " + (r.msg || "正在建索引，请稍后再试");
        } else if (r && r.ok) {
          if (rbConfirm) rbConfirm.hidden = true;
          if (rbMsg) { rbMsg.textContent = "✓ 已清空。" + (r.msg || ""); }
          flashToast("✓ 索引已清空。点顶栏「⟳ 更新知识库」重建，再「深索全部」。");
          // 刷新顶栏/首页，反映「未建库/待重建」
          poll(); if (dashLoaded) loadDashboard("silent");
        } else {
          if (rbMsg) rbMsg.textContent = "❌ " + ((r && r.msg) || "清空失败");
        }
      } catch (e) {
        if (rbMsg) rbMsg.textContent = "❌ 清空失败：" + e.message;
      } finally {
        rbGo.disabled = false; rbGo.textContent = old;
        if (rbOpen && rbConfirm && rbConfirm.hidden) rbOpen.hidden = false;
      }
    });
  }

  // ── 应用更新（版本升级）────────────────────────────────
  // 只换 app\，数据不动（server /update/* + launcher 桥 apply_update）。
  // 顶栏右上角「有新版」徽标：有更新且未按该版本忽略时显示。按版本记忆——发更新版本时自动重现。
  function renderUpdateBadge(r) {
    const b = $("#up-badge"); if (!b) return;
    const dismissed = localStorage.getItem("localkb.updateDismissed");
    if (r && r.ok && (r.has_update || r.needs_full_installer) && r.latest
        && (r.needs_full_installer || dismissed !== r.latest)) {
      b.textContent = r.needs_full_installer ? "⬇️ 需完整安装器" : "🎁 有新版 " + r.latest;
      b.dataset.latest = r.latest;
      b.hidden = false;
    } else {
      b.hidden = true;
    }
  }

  async function checkUpdate(quiet) {
    const msg = $("#up-msg"), panel = $("#up-panel");
    if (!msg) return null;
    if (!quiet) msg.textContent = "检查中…";
    try {
      const r = await jget("/update/check" + (quiet ? "" : "?force=1"));
      if (!r.ok || r.error) {
        if (!quiet) msg.textContent = "检查失败：" + (r.error || "未知") + "（多半是网络问题）";
        renderUpdateBadge(null);   // 检查失败不亮徽标
        return r;
      }
      if (r.has_update || r.needs_full_installer) {
        msg.textContent = "";
        const apply = $("#up-apply");
        if (r.needs_full_installer) {
          const missing = (r.missing_runtime || []).join("、") || "新增运行组件";
          $("#up-ver").innerHTML = r.has_update
            ? `当前 <b>${r.current}</b> → 新版 <b>${r.latest}</b> 需要完整安装器（缺少：${esc(missing)}）。<br>直接覆盖安装即可，<b>不用卸载</b>；索引、综述、设置和 Agent 文件都会保留。`
            : `程序代码已是 <b>${r.current}</b>，但还缺少 ${esc(missing)}。请再运行一次完整安装器补齐；<b>不用卸载</b>，用户数据不会被覆盖。`;
          apply.dataset.mode = "installer";
          apply.textContent = "⬇️ 下载完整安装器";
          apply.disabled = !r.installer_url;
          $("#up-apply-msg").textContent = r.installer_url ? "" : "该版本暂未提供完整安装器，请稍后再检查";
        } else {
          $("#up-ver").innerHTML = `当前 <b>${r.current}</b> → 有新版 <b>${r.latest}</b>`;
          apply.dataset.mode = "app";
          apply.textContent = "⬆️ 下载并升级";
          apply.disabled = false;
          $("#up-apply-msg").textContent = "";
        }
        if (r.notes) { $("#up-notes").textContent = r.notes; $("#up-notes-wrap").hidden = false; }
        panel.hidden = false;
      } else {
        if (!quiet) msg.textContent = `已是最新版（${r.current}）。`;
        panel.hidden = true;
      }
      renderUpdateBadge(r);        // 顶栏徽标随每次检查刷新（设置页打开 / 手动检查 / 启动静默检查都会走到）
      return r;
    } catch (e) { if (!quiet) msg.textContent = "❌ " + e; renderUpdateBadge(null); return null; }
  }

  async function doUpdate() {
    const btn = $("#up-apply"), msg = $("#up-apply-msg");
    if (btn.dataset.mode === "installer") {
      btn.disabled = true; msg.textContent = "正在打开官方下载…";
      try {
        const r = await jpost("/update/open_installer", {});
        msg.textContent = r.ok ? "已在默认浏览器打开下载。下载后直接运行，安装到原来的 PaperPiggy 文件夹即可；不用先卸载，用户数据不会被覆盖。" : "❌ 打开失败";
      } catch (e) { msg.textContent = "❌ " + (e.message || e); }
      finally { btn.disabled = false; }
      return;
    }
    const bridge = window.pywebview && window.pywebview.api && window.pywebview.api.apply_update;
    if (!bridge) { msg.textContent = "请在 PaperPiggy 应用窗口里升级（浏览器模式不支持）。"; return; }
    if (!(await uiConfirm("会下载新版程序 → 关闭应用 → 替换后自动重启。\n\n" +
                 "你的数据（索引、综述、Agent 交付物、来源评价数据）完全不受影响。",
                 { title: "开始升级？", okText: "下载并升级" }))) return;
    btn.disabled = true;
    msg.textContent = "下载中…";
    try {
      const d = await jpost("/update/download", {});
      if (!d.ok) { msg.textContent = "❌ " + (d.error || "下载失败"); btn.disabled = false; return; }
      let zip = null;
      for (let i = 0; i < 600; i++) {
        await new Promise(r => setTimeout(r, 700));
        const s = await jget("/update/status");
        if (s.error) { msg.textContent = "❌ " + s.error; btn.disabled = false; return; }
        if (!s.downloading) { zip = s.zip; break; }
        const pct = s.total ? Math.floor(s.done / s.total * 100) : 0;
        msg.textContent = `下载中… ${pct}%`;
      }
      if (!zip) { msg.textContent = "❌ 下载未完成"; btn.disabled = false; return; }
      msg.textContent = "即将关闭并升级…";
      const r = await window.pywebview.api.apply_update(zip);
      if (r && r.ok) {
        msg.textContent = "正在升级，应用马上会自动重启…（窗口即将关闭）";
      } else {
        msg.textContent = "❌ " + ((r && r.error) || "升级启动失败");
        btn.disabled = false;
      }
    } catch (e) { msg.textContent = "❌ " + e; btn.disabled = false; }
  }

  async function loadMirror() {
    const el = $("#up-mirror"); if (!el) return;
    try { const r = await jget("/update/mirror"); el.value = r.mirror_base || ""; } catch (e) {}
  }
  async function saveMirror() {
    const el = $("#up-mirror"), msg = $("#up-mirror-msg"); if (!el) return;
    try {
      const r = await jpost("/update/mirror", { mirror_base: el.value.trim() });
      if (msg) msg.textContent = r.ok ? (r.mirror_base ? "✅ 已保存镜像地址" : "已清空（只走 GitHub）") : "❌ 保存失败";
    } catch (e) { if (msg) msg.textContent = "❌ " + e; }
  }

  { const c = $("#up-check"); if (c) c.addEventListener("click", () => checkUpdate(false));
    const a = $("#up-apply"); if (a) a.addEventListener("click", doUpdate);
    const m = $("#up-mirror"); if (m) m.addEventListener("change", saveMirror);
    // 顶栏「有新版」徽标：左键→进设置更新区一步升级；右键→忽略此版本（发更新版本时自动重现）
    const ub = $("#up-badge");
    if (ub) {
      ub.addEventListener("click", () => openSettings("sec-update"));
      ub.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        if (ub.dataset.latest) localStorage.setItem("localkb.updateDismissed", ub.dataset.latest);
        ub.hidden = true;
      });
    }
    // 「启动时自动检查新版本」开关（纯客户端偏好，存 localStorage；默认开）
    const ac = $("#up-autocheck");
    if (ac) {
      ac.checked = localStorage.getItem("localkb.autoUpdateCheck") !== "0";
      ac.addEventListener("change", () => {
        localStorage.setItem("localkb.autoUpdateCheck", ac.checked ? "1" : "0");
        if (!ac.checked) { const b = $("#up-badge"); if (b) b.hidden = true; }  // 关掉即隐藏当前徽标
      });
    }
  }

  // ── 对话页「模型设置」折叠区（原设置弹窗的 LLM 服务商块内联到此，onChange 即存）──
  // 冷启动即回填对话页模型设置（原逻辑只在打开设置弹窗时回填）
  function initChatModel() {
    const c = cfg();
    provSel.value = c.provider || "siliconflow";
    applyProvider(provSel.value, true);
    $("#set-base").value = c.base || PROVIDERS[provSel.value].base;
    $("#set-model").value = c.model || PROVIDERS[provSel.value].model;
    // K3（副本#4/#5）：对话 key 存 localStorage 明文；这里不回填明文到框里，改用掩码占位（末4位）显示「填过了」。
    // 框留空＝不改（沿用已存 key）；输入新 key＝更新。
    const k = c.api_key || "";
    $("#set-key").value = "";
    $("#set-key").placeholder = k ? maskKey(k.slice(-4)) : "sk-…";
  }
  function saveChatModel(opts) {
    const keyIn = $("#set-key").value.trim();
    // BF20：resetKey（切服务商）时 api_key 落空串，不回退 cfg().api_key；input 事件传进来的是 Event 对象，天然不触发。
    // keyDirty（用户动过 Key 输入框）时 keyIn 原样保存——空串=主动清除，否则清空输入永远删不掉旧 key。
    const resetKey = !!(opts && opts.resetKey), keyDirty = !!(opts && opts.keyDirty);
    saveCfg({ provider: provSel.value, base: $("#set-base").value.trim(),
              api_key: (resetKey || keyDirty) ? keyIn : (keyIn || cfg().api_key || ""), model: $("#set-model").value.trim() });
    const s = $("#cm-saved");
    if (s) { s.textContent = "已保存 ✓"; s.classList.add("flash"); setTimeout(() => s.classList.remove("flash"), 800); }
  }
  ["#set-base", "#set-model"].forEach((sel) => {
    const el = $(sel); if (el) el.addEventListener("input", saveChatModel);
  });
  { const el = $("#set-key"); if (el) el.addEventListener("input", () => saveChatModel({ keyDirty: true })); }
  initChatModel();
  const _c2a = $("#chat-to-agent"); if (_c2a) _c2a.addEventListener("click", () => switchTab("agent"));
  const _c2a2 = $("#chat-agent-link"); if (_c2a2) _c2a2.addEventListener("click", () => switchTab("agent"));

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
    if (!(await uiConfirm("会清空：检索引擎回本地、期刊学科回标准法学、检索内存回默认 10 分钟、API/摘要 key、以及本机保存的对话模型 Key。文献索引不受影响。",
          { title: "恢复默认设置？", okText: "恢复默认", danger: true }))) return;
    try {
      await jpost("/setup/reset", {});
      localStorage.removeItem("localkb.cfg");   // 清对话 LLM 配置（存 localStorage）
      initChatModel();                          // 对话页模型设置回默认
      loadEngine(); loadSac(); loadDiscipline(); loadRetrievalMemory();
      poll();
      flashToast("已恢复默认设置。");   // UX10：成功/失败都走应用内浮层
    } catch (e) { flashToast("恢复默认失败：" + (e.message || e)); }
  });

  // ── 检索引擎（嵌入/重排）：设置里随时切换本地/API、改 key，不必重跑首启向导 ──
  function engApiVisible(be) { const box = $("#eng-api"); if (box) box.hidden = (be !== "api"); }
  async function loadEngine() {
    $("#eng-msg").textContent = "";
    // /setup/detect 只回 backend + 是否已设 key；base/模型名用标准默认（改过高级项的可自行重填）
    $("#eng-base").value = "https://api.siliconflow.cn/v1";
    $("#eng-embed").value = ""; $("#eng-rerank").value = ""; $("#eng-key").value = "";
    let be = "local", keySet = false, last4 = "";
    try { const d = await jget("/setup/detect"); be = d.backend === "api" ? "api" : "local"; keySet = !!d.api_key_set; last4 = d.api_key_last4 || ""; } catch (e) {}
    const r = document.querySelector(`input[name=eng-backend][value=${be}]`); if (r) r.checked = true;
    // K3：已设置时用掩码占位「••••••1234（留空＝不改）」，让用户知道填过；留空提交＝不改
    $("#eng-key").placeholder = keySet ? ((last4 ? maskKey(last4) + " " : "") + "你之前已经填过 API 了，留空即可、不用改") : "去 cloud.siliconflow.cn 领免费 key";
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
      // BF23：textContent 本身不解析 HTML，再套 esc() 会把 & < > 显示成 &amp; 等实体（双重转义）
      else { msg.className = "hint warn"; msg.textContent = "连接失败：" + ((r && r.msg) || "未知错误"); }
    } catch (e) { msg.className = "hint warn"; msg.textContent = "连接失败：" + e.message; }
    finally { btn.disabled = false; }
  });
  // 换引擎后旧向量不兼容 → 应用时强制重建索引，并显示「正在重建中」
  async function doRebuildIndex(msgEl, hadDeep) {
    if (msgEl) { msgEl.className = "hint"; msgEl.textContent = "正在重建索引（换引擎后旧向量不兼容）…"; }
    try {
      const r = await fetch("/build", { method: "POST" });
      let j = null; try { j = await r.json(); } catch (e) {}
      if (j && j.ok === false) { if (msgEl) { msgEl.className = "hint warn"; msgEl.textContent = "已有任务在跑，稍后再点「应用检索引擎」重建。"; } return; }
      poll();
      await new Promise((resolve) => {
        let tries = 0;
        const iv = setInterval(async () => {
          tries++;
          try {
            const s = await (await fetch("/build/status")).json();
            if (!s.running || tries > 240) {   // 6min 封顶，防后端卡 running 时 await 永不返回、按钮卡死
              clearInterval(iv);
              if (msgEl) {
                if (tries > 240) { msgEl.className = "hint"; msgEl.textContent = "重建仍在进行，可关闭设置，进度见顶部。"; }
                else if (s.cancelled) { msgEl.className = "hint warn"; msgEl.textContent = "重建已取消。"; }
                else if (s.rc && s.rc !== 0) { msgEl.className = "hint warn"; msgEl.textContent = "重建未成功（可能是 API Key 无效或余额不足），请检查检索引擎设置后重试。"; }
                else { msgEl.className = "hint ok"; msgEl.textContent = "✓ 题录索引已用新引擎重建完成。" + (hadDeep ? "已深索的正文向量需到「浏览」页重新深索，才能与新引擎一致。" : ""); }
              }
              poll(); if (dashLoaded) loadDashboard("silent"); resolve();
            } else if (msgEl) { msgEl.textContent = "正在重建索引中…（可关闭设置，进度见顶部）"; }
          } catch (e) { clearInterval(iv); resolve(); }
        }, 1500);
      });
    } catch (e) { if (msgEl) { msgEl.className = "hint warn"; msgEl.textContent = "重建失败：" + (e.message || e); } }   // BF23：textContent 去 esc
  }
  $("#eng-save").addEventListener("click", async () => {
    const msg = $("#eng-msg"), btn = $("#eng-save"); btn.disabled = true;
    msg.className = "hint"; msg.textContent = "保存中…";
    try {
      const r = await jpost("/setup/backend", engBody());
      msg.className = "hint"; msg.textContent = "✓ 已应用检索引擎（" + (r.backend === "api" ? "API" : "本地") + "）。" + (r.warn ? "⚠ " + r.warn + " " : "") + "开始重建索引…";
      await doRebuildIndex(msg, r.had_deep);   // had_deep>0 → 提示深索正文向量需重跑
    } catch (e) { msg.className = "hint warn"; msg.textContent = "保存失败：" + e.message; }   // BF23：textContent 去 esc
    finally { btn.disabled = false; }
  });

  // ── 深索摘要（SAC）：K2（副本#7）三选一 generator=server|agent|off ──
  // BF21：key 输入框的 dirty 标记——只有用户动过才把 key 放进请求体（空串＝清除落盘）；
  // 没动过就不带 key 字段，避免每次保存高级设置都把已存 key 清掉/覆盖
  let sacKeyDirty = false;
  { const sk = $("#sac-key"); if (sk) sk.addEventListener("input", () => { sacKeyDirty = true; }); }
  // 当前选中的生成方式（读单选按钮）
  function sacGen() { return (document.querySelector("input[name=sac-gen]:checked") || {}).value || "off"; }
  // 依据 generator + effective_ready 显示状态行
  function renderSacStatus(gen, effectiveReady) {
    const el = $("#sac-status");
    if (gen === "off") {
      el.className = "sac-status hint"; el.textContent = "已关闭：深索时不生成摘要。";
    } else if (gen === "agent") {
      el.className = "sac-status hint ok"; el.textContent = "✓ 交给 Agent：深索或维护知识库时，由 Claude Code / Codex 生成并修复摘要，不调用服务端摘要生成 API。";
    } else if (effectiveReady) {
      el.className = "sac-status hint ok"; el.textContent = "✓ 服务端自动：深索时会自动生成摘要前缀（用你配的 API key）。";
    } else {
      el.className = "sac-status hint warn"; el.textContent = "⚠ 服务端自动模式需先在检索引擎里配 API key，或在下方“高级”单独填 key。";
    }
  }
  async function loadSac() {
    // 先给个中性态，避免上次残留
    $("#sac-status").className = "sac-status hint";
    $("#sac-status").textContent = "";
    try {
      const s = await jget("/setup/sac");
      // 迁移兼容：后端未回 generator 时，用老的 enabled 推断（true→server，false→off）
      const gen = s.generator || (s.enabled ? "server" : "off");
      const r = document.querySelector(`input[name=sac-gen][value=${gen}]`); if (r) r.checked = true;
      $("#sac-base").value = s.base || "";
      $("#sac-model").value = s.model || "";
      // K3：key 是密码，后端只回末4位；已设则用掩码占位，不回填明文
      const last4 = s.key_last4 || s.sac_key_last4 || "";
      $("#sac-key").value = "";
      sacKeyDirty = false;   // BF21：程序回填不算用户改动
      $("#sac-key").placeholder = s.key_set
        ? ((last4 ? maskKey(last4) + " " : "") + "你之前已经填过了，留空即可（不填则复用检索引擎的 key）")
        : "不用填，会自动复用检索引擎的 key";
      renderSacStatus(gen, !!s.effective_ready);
    } catch (e) {
      $("#sac-status").className = "sac-status hint warn";
      $("#sac-status").textContent = "状态加载失败：" + e.message;
    }
  }
  // 三选一切换：立即 POST 保存 generator，并用返回的 effective_ready 刷新状态行
  $$("input[name=sac-gen]").forEach((radio) => radio.addEventListener("change", async () => {
    const gen = sacGen();
    try {
      const r = await jpost("/setup/sac", { generator: gen });
      renderSacStatus(gen, !!(r && r.effective_ready));
    } catch (e) {
      $("#sac-status").className = "sac-status hint warn";
      $("#sac-status").textContent = "保存失败：" + e.message;
    }
  }));
  // 高级：单独保存 base / key / model（随当前 generator 一起提交，只传填了的字段）
  $("#sac-adv-save").addEventListener("click", async () => {
    const msg = $("#sac-adv-msg");
    const gen = sacGen();
    const body = { generator: gen };
    const base = $("#sac-base").value.trim(), model = $("#sac-model").value.trim(), key = $("#sac-key").value.trim();
    // BF21：base/model 每次照发（空串＝清空落盘，后端已按 is not None 判）；key 只有用户动过输入框才发，
    // 动过且为空串＝明确清除——之前「填了清不掉」就是因为空值一律不发
    body.base = base; body.model = model;
    if (sacKeyDirty) body.key = key;
    msg.textContent = "保存中…";
    try {
      const r = await jpost("/setup/sac", body);
      renderSacStatus(gen, !!(r && r.effective_ready));
      msg.textContent = "已保存 ✓";
      await loadSac();   // BF21：以后端落盘结果回显校验（顺带清空明文输入、重置 dirty）
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
  //  手动更新知识库（点一次直接后台增量更新，进度走顶部状态条，不弹终端日志窗）
  // ══════════════════════════════════════════
  let manualUpdating = false;
  async function doManualUpdate() {
    if (manualUpdating) return;
    const btn = $("#btn-build"); const old = btn.innerHTML;
    const restore = () => { manualUpdating = false; btn.disabled = false; btn.innerHTML = old; };
    manualUpdating = true; btn.disabled = true; btn.textContent = "⟳ 更新中…";
    // UX6：更新前记一份篇数基线（total/deep），完成后对比告诉用户到底新增了几篇；取不到就退化为普通完成提示
    let baseTotal = null;
    try { const s0 = await jget("/stats"); baseTotal = ((s0 && s0.coverage) || {}).total; } catch (e) {}
    try {
      const r = await fetch("/build", { method: "POST" });
      let j = null; try { j = await r.json(); } catch (e) {}
      // 忙时后端返回 {ok:false}，提示而非假装已开始
      if (j && j.ok === false) { btn.textContent = "有任务在跑，稍后再试"; setTimeout(restore, 1800); return; }
      poll();  // 顶部状态/进度条立刻接管
      let tries = 0;
      const iv = setInterval(async () => {
        tries++;
        try {
          const s = await (await fetch("/build/status")).json();
          if (!s.running || tries > 240) {   // 240×1.5s≈6min 封顶，防后端卡 running 时按钮永久禁用
            clearInterval(iv); restore();
            if (dashLoaded) loadDashboard("silent");
            if (browseLoaded) { browseLoaded = false; if (!$("#panel-browse").hidden) loadBrowse(); }
            poll();
            // UX6：真跑完（非超时封顶）才对比基线报结果——但先看构建是否成功，别把「余额0/子进程崩溃」误报成完成
            if (!s.running) {
              if (s.cancelled) { flashToast("更新已取消。"); }
              else if (s.rc && s.rc !== 0) { flashToast("更新未成功（可能是 API Key 无效或余额不足，或建库子进程出错），请检查设置后重试。"); }
              else {
                try {
                  const s1 = await jget("/stats");
                  const nowTotal = ((s1 && s1.coverage) || {}).total;
                  if (baseTotal != null && nowTotal != null) {
                    const diff = nowTotal - baseTotal;
                    flashToast(diff > 0 ? `更新完成：新增 ${num(diff)} 篇文献。` : "更新完成，没有新增文献。");
                  } else flashToast("更新完成 ✓");
                } catch (e) { flashToast("更新完成 ✓"); }
              }
              refreshUpgradeHealth().catch((e) => reportErr(e && e.message, "refresh upgrade health after build"));
            }
          }
        } catch (e) { clearInterval(iv); restore(); }
      }, 1500);
    } catch (e) { btn.textContent = "更新失败"; setTimeout(restore, 1800); }
  }
  $("#btn-build").addEventListener("click", doManualUpdate);

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
  // 走到「进入知识库」即视为已引导：R16 用它避免空文件夹（无索引 manifest）每次重启重弹向导
  function closeWizard() { $("#wizard").hidden = true; try { localStorage.setItem("localkb.onboarded", "1"); } catch (e) {} }

  function renderStep1() {
    setStep(1);
    const d = WZ.detect || {};
    const row = (icOk, icMiss, k, val, okTxt, missTxt) => {
      const ok = !!val;
      return `<li><span class="ic">${ok ? icOk : icMiss}</span><span class="k">${k}</span>
        <span class="v ${ok ? "ok" : "miss"}">${ok ? esc(String(val)) : (missTxt || "未检测到")}</span></li>`;
    };
    const noZoteroHint = !d.zotero_dir
      ? `<div class="wz-note">未检测到 Zotero —— 没关系，第 3 步可选「文件夹模式」，直接放 PDF、EPUB、DOCX、Markdown 或 TXT 建库。</div>` : "";
    $("#wizard-body").innerHTML =
      `<div class="wz-note">只需 4 步、几分钟，就能把你的文库变成可秒级检索、可对话的本地知识库。先看看环境：</div>
      <ul class="wz-check">
        ${row("📁", "⚠️", "Zotero 目录", d.zotero_dir, null, "未探测到（可用文件夹模式）")}
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
      <div class="wz-actions-inline">
        <button class="ghost2c" id="wzk-test">测试连接（把要用的免费模型测一遍）</button>
        <span id="wzk-test-msg" class="wz-test-msg"></span>
      </div>
      <div id="wzk-msg"></div>
      <div class="wz-actions">
        <button class="ghost2c wz-back" id="wzk-back">← 上一步</button>
        <button class="primary" id="wzk-next">下一步：配置检索引擎 →</button>
      </div>`;
    $("#wzk-test").addEventListener("click", async () => {
      const k = $("#wzk-key").value.trim(), msg = $("#wzk-test-msg");
      if (!k) { msg.className = "wz-test-msg err"; msg.textContent = "请先填 Key"; return; }
      msg.className = "wz-test-msg"; msg.textContent = "测试中…";
      try {
        const r = await jpost("/setup/test_api", { base: (WZ.api && WZ.api.base) || "https://api.siliconflow.cn/v1", key: k,
          embed_model: (WZ.api && WZ.api.embed_model) || "BAAI/bge-m3", rerank_model: (WZ.api && WZ.api.rerank_model) || "BAAI/bge-reranker-v2-m3" });
        if (r && r.ok) {
          if (WZ.api) WZ.api.key = k; WZ.apiTested = true;
          msg.className = "wz-test-msg ok";
          msg.textContent = `✓ 免费模型可用（向量维度 ${num(r.dim)}，延迟 ${num(r.latency_ms)}ms）。下一步会自动用 API 引擎、无需再测。`;
        // BF23：textContent 去 esc（否则报错里的 & < > 显示成 &amp; 等实体）
        } else { WZ.apiTested = false; msg.className = "wz-test-msg err"; msg.textContent = "连接失败：" + ((r && r.msg) || "未知错误"); }
      } catch (e) { WZ.apiTested = false; msg.className = "wz-test-msg err"; msg.textContent = "连接失败：" + e.message; }
    });
    $("#wzk-back").addEventListener("click", renderStep1);
    $("#wzk-next").addEventListener("click", async () => {
      const k = $("#wzk-key").value.trim();
      if (k) {
        try {
          await jpost("/setup/backend", { backend: WZ.backend, key: k });  // 只存 key，不改后端
          WZ.api.key = k; if (WZ.detect) WZ.detect.meta_ready = true; APP.metaReady = true;
          // 对话页也用这个 key（siliconflow）
          const c = cfg(); c.provider = c.provider || "siliconflow"; c.api_key = c.api_key || k; saveCfg(c);
        } catch (e) {
          // R15：保存失败则停在本步显示错误、不前进（否则 renderStep2 整块重绘会抹掉刚写的提示）
          $("#wzk-msg").innerHTML = `<div class="wz-err">保存失败：${esc(e.message)}。请检查网络后重试，或清空此框跳过（之后在设置里补）。</div>`;
          return;
        }
      }
      renderStep2();
    });
  }

  // ── 第 2 步：选择检索引擎（本地 / API 二选一）──
  function renderStep2() {
    setStep(2);
    // 上一步填了 SiliconFlow Key → 默认用 API 引擎（免下模型、免再测）
    if (WZ.api && WZ.api.key && WZ.backend !== "api") WZ.backend = "api";
    const a = WZ.api;
    const localSel = WZ.backend === "local";
    $("#wizard-body").innerHTML =
      `<div class="wz-engines">
        <label class="wz-engine ${localSel ? "sel" : ""}" data-be="local">
          <input type="radio" name="wz-backend" value="local" ${localSel ? "checked" : ""} />
          <div class="wz-engine-body">
            <div class="wz-engine-h">🔒 本地模式</div>
            <div class="wz-engine-d">离线 · 隐私不出本机 · 需下载约 1.2GB 模型（首次一次性）</div>
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
    // 上一步已用该 Key 测通 → 提示免测（apiTested 已为 true，下一步不会拦）
    if (WZ.apiTested && WZ.api && WZ.api.key) {
      const tm0 = $("#wz-api-test-msg"); if (tm0) { tm0.className = "wz-test-msg ok"; tm0.textContent = "✓ 已用上一步的 Key 测通，无需再测。"; }
    }

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
          msg.textContent = "连接失败：" + ((r && r.msg) || "未知错误");   // BF23：textContent 去 esc
        }
      } catch (e) {
        WZ.apiTested = false;
        msg.className = "wz-test-msg err";
        msg.textContent = "连接失败：" + e.message;   // BF23
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
    setStep(3);
    const d = WZ.detect || {};
    if (!WZ.srcChoice) WZ.srcChoice = d.zotero_detected ? "zotero" : "folder";
    const zSel = WZ.srcChoice === "zotero";
    $("#wizard-body").innerHTML =
      `<div class="wz-engines">
        <label class="wz-engine ${zSel ? "sel" : ""}" data-src="zotero">
          <!-- UX1：未检测到 Zotero 也允许选中——装在非默认路径的用户可手动填数据目录，不该被 disabled 卡死 -->
          <input type="radio" name="wz-src" value="zotero" ${zSel ? "checked" : ""} />
          <div class="wz-engine-body">
            <div class="wz-engine-h">🔗 连接 Zotero ${d.zotero_detected ? '<span class="wz-badge-rec">已检测到</span>' : '<span class="wz-badge-save">未检测到（可手动填目录）</span>'}</div>
            <div class="wz-engine-d">直接读取 Zotero 里每一条文献（含题录和收藏夹分类），不会改动你的 Zotero 数据。</div>
          </div>
        </label>
        <label class="wz-engine ${zSel ? "" : "sel"}" data-src="folder">
          <input type="radio" name="wz-src" value="folder" ${zSel ? "" : "checked"} />
          <div class="wz-engine-body">
            <div class="wz-engine-h">📁 文件夹模式 <span class="wz-badge-save">无需 Zotero</span></div>
            <div class="wz-engine-d">指定一个文件夹放 PDF、EPUB、DOCX、Markdown 或 TXT，系统用 AI 自动读出题名、作者、年份、期刊等信息。适合没装 Zotero、手上就是一批全文文件的你。</div>
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
    // UX1：未自动检测到时给一句明确指引——手填目录后照常走 /setup/connect（zotero_dir 会随请求提交）
    const noDetectNote = d.zotero_detected ? "" :
      `<div class="wz-note wz-note-warn">⚠️ 未自动检测到 Zotero，请在下方手动填写数据目录（Zotero 的「首选项 → 高级 → 文件和文件夹」里能看到，目录下有 zotero.sqlite）。</div>`;
    $("#wz-src-body").innerHTML = noDetectNote +
      `<div class="wz-field">
        <label>Zotero 数据目录（含 zotero.sqlite，留空自动探测）</label>
        <input id="wz-zdir" value="${esc(d.zotero_dir || "")}" placeholder="如 D:\\Zotero（留空则自动探测）" />
      </div>
      <div class="wz-note">连接会直接读取 Zotero 的 zotero.sqlite 里每一条文献（含没有受支持全文附件的纯题录），<b>不修改</b>你的 Zotero 数据。</div>
      <label class="sac-toggle" style="margin:6px 0"><input type="checkbox" id="wz-onlypdf" ${(WZ.detect && WZ.detect.import_only_pdf) ? "checked" : ""} />
        <span>只导入有全文附件的文献（支持 PDF、EPUB、DOCX、Markdown、TXT；没有这些附件的纯题录不进库；之后可在设置里改）</span></label>
      <div id="wz3c-msg"></div>
      <div class="wz-actions">
        <button class="ghost2c wz-back" id="wz3-back">← 上一步</button>
        <button class="primary" id="wz3-connect">连接文库</button>
      </div>`;
    $("#wz3-back").addEventListener("click", renderStep2);
    const _op = $("#wz-onlypdf");
    // F1：连接结果按「只导入有全文附件」勾选状态区分「全库总数 / 将实际入库数」。
    const zoteroConnectedMsg = (r, tail) => {
      const only = _op ? _op.checked : !!(WZ.detect && WZ.detect.import_only_pdf);
      const total = r.entries, wp = withFulltext(r);
      let s = `✅ 已连接 Zotero，共 ${num(total)} 条文献`;
      if (only && wp != null) {
        const skip = (r.no_pdf != null) ? r.no_pdf : (total - wp);
        s += `，其中 <b>${num(wp)}</b> 条有受支持全文附件、将只导入这些${skip > 0 ? `（${num(skip)} 条纯题录已按你的选择跳过）` : ""}`;
      }
      return `<div class="wz-result">${s}。${tail}</div>`;
    };
    if (_op) _op.addEventListener("change", () => {
      jpost("/setup/import_only_pdf", { only_pdf: _op.checked }).catch(() => {});
      if (WZ.detect) WZ.detect.import_only_pdf = _op.checked;
      if (WZ.connected) $("#wz3c-msg").innerHTML = zoteroConnectedMsg(WZ.connected, "点「下一步」继续。");
    });
    $("#wz3-connect").addEventListener("click", async () => {
      const btn = $("#wz3-connect"); btn.disabled = true;
      $("#wz3c-msg").innerHTML = "";
      const zdir = $("#wz-zdir").value.trim();
      try {
        const r = await jpost("/setup/connect", zdir ? { zotero_dir: zdir } : {});
        WZ.connected = r; WZ.srcChoice = "zotero"; APP.source = "zotero";
        $("#wz3c-msg").innerHTML = zoteroConnectedMsg(r, "确认无误后点「下一步」。");
        const act = btn.parentNode;
        if (act) {
          act.innerHTML = `<button class="ghost2c wz-back" id="wz3-back2">← 上一步</button><button class="primary" id="wz3-next2">下一步：建立索引 →</button>`;
          $("#wz3-back2").addEventListener("click", renderStep2);
          $("#wz3-next2").addEventListener("click", renderStep4);
        }
      } catch (e) {
        $("#wz3c-msg").innerHTML = `<div class="wz-err">连接失败：${esc(e.message)}</div>`;
        btn.disabled = false;
      }
    });
    // 从后续步骤回退时：本会话已连过 Zotero → 直接恢复「下一步」态，免得重连
    if (WZ.connected) {
      $("#wz3c-msg").innerHTML = zoteroConnectedMsg(WZ.connected, "点「下一步」继续。");
      const cb0 = $("#wz3-connect"), act0 = cb0 && cb0.parentNode;
      if (act0) {
        act0.innerHTML = `<button class="ghost2c wz-back" id="wz3-back2">← 上一步</button><button class="primary" id="wz3-next2">下一步：建立索引 →</button>`;
        $("#wz3-back2").addEventListener("click", renderStep2);
        $("#wz3-next2").addEventListener("click", renderStep4);
      }
    }
  }
  async function renderStep3Folder() {
    // 只有当 launcher 进程注入了原生桥 pick_folder 时才显示「浏览…」；否则（浏览器回退）隐藏，避免死按钮。
    const nativePick = !!(typeof window !== "undefined" && window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder);
    // 建议默认目录：应用自己的数据目录旁 <HOME>/papers
    let def = WZ.folderDir || "";
    if (!def) { try { def = (await jget("/setup/folder_default")).default_dir || ""; } catch (e) {} }
    $("#wz-src-body").innerHTML =
      `<div class="wz-field">
        <label>知识库文件夹（PaperPiggy 会用它来放你的全文文件）</label>
        <div class="wz-folder-pick">
          <input id="wz-folder-dir" value="${esc(def)}" placeholder="如 D:\\我的论文库" />
          ${nativePick ? `<button class="ghost2c" id="wz-folder-browse">浏览…</button>` : ""}
        </div>
        <p class="wz-mini">默认就用应用自己的文件夹（上面这个路径）。点下面「📂 打开全文文件夹」会创建并直接打开它，把论文拖进去即可。</p>
        <div class="wz-actions-inline">
          <button class="ghost2c" id="wz-folder-open">📂 打开全文文件夹</button>
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
      // 目录选择器由 launcher 进程的 js_api 代理（原生窗口在那）；server 子进程里 create_file_dialog 恒失败。
      try {
        const dir = await window.pywebview.api.pick_folder();
        if (dir) $("#wz-folder-dir").value = dir;
      } catch (e) {}
    });
    // 打开文件夹：先把这个目录设为受管文件夹（创建）+ 在系统里打开 + 显示 PDF 数
    $("#wz-folder-open").addEventListener("click", async () => {
      const dir = $("#wz-folder-dir").value.trim();
      try {
        if (dir) await jpost("/setup/folder", { folder_dir: dir });
        const r = await jpost("/setup/open_folder", {});
        const cnt = $("#wz-folder-cnt");
        if (cnt) cnt.textContent = "已打开文件夹，把受支持全文文件拖进去后回来点「建立文件夹库」。";
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
        $("#wz3f-msg").innerHTML = `<div class="wz-result">✅ 已建立文件夹库，发现 ${n} 个受支持全文文件${r.entries ? "" : "（空文件夹也没关系，稍后把全文文件拖进窗口就会自动入库）"}。点「下一步」继续。</div>`;
        const act = btn.parentNode;
        if (act) {
          act.innerHTML = `<button class="ghost2c wz-back" id="wz3f-back2">← 上一步</button><button class="primary" id="wz3f-next2">下一步：建立索引 →</button>`;
          $("#wz3f-back2").addEventListener("click", renderStep2);
          $("#wz3f-next2").addEventListener("click", renderStep4);
        }
      } catch (e) {
        $("#wz3f-msg").innerHTML = `<div class="wz-err">建立失败：${esc(e.message)}</div>`;
        btn.disabled = false;
      }
    });
    // 从后续步骤回退时：本会话已建过文件夹库 → 直接恢复「下一步」态
    if (WZ.folderConnected) {
      const n0 = num(WZ.folderConnected.entries || 0);
      $("#wz3f-msg").innerHTML = `<div class="wz-result">✅ 已建立文件夹库，发现 ${n0} 个受支持全文文件。点「下一步」继续。</div>`;
      const cb1 = $("#wz3f-connect"), act1 = cb1 && cb1.parentNode;
      if (act1) {
        act1.innerHTML = `<button class="ghost2c wz-back" id="wz3f-back2">← 上一步</button><button class="primary" id="wz3f-next2">下一步：建立索引 →</button>`;
        $("#wz3f-back2").addEventListener("click", renderStep2);
        $("#wz3f-next2").addEventListener("click", renderStep4);
      }
    }
    renderMetaDep();
  }
  // 抽题录需 LLM Key 的三态引导
  function renderMetaDep() {
    const box = $("#wz-meta-dep"); if (!box) return;
    const hasKey = (WZ.backend === "api" && WZ.api.key) || (WZ.detect && WZ.detect.meta_ready) || APP.metaReady;
    if (hasKey) {
      box.innerHTML = `<div class="wz-note wz-note-ok">🤖 <b>题录抽取已就绪</b>：入库时会用你配置的 API Key，自动从全文文件读出题名 / 作者 / 年份 / 期刊 / 摘要。</div>`;
      return;
    }
    box.innerHTML = `
      <div class="wz-note wz-note-warn">⚠️ <b>还差一步：配一个 AI 的 API Key</b><br>
        文件夹里的全文文件没有结构化题名、作者等信息，需要 AI 从正文里读出来。推荐用 <b>SiliconFlow（硅基流动）</b>，
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
      } catch (e) { msg.className = "wz-test-msg err"; msg.textContent = "保存失败：" + e.message; }   // BF23：textContent 去 esc
    });
  }

  // ── 第 4 步：准备检索引擎（仅本地模式需下载模型；API 模式直接就绪）──
  function renderStep4() {
    setStep(4);
    const readyHtml =
      `<div class="wz-note">检索引擎已就绪 ✓${WZ.backend === "api" ? "（API 模式，无需本地模型）" : "（本地模型已在）"}。可直接进入下一步。</div>
      <div class="wz-actions">
        <button class="ghost2c wz-back" id="wz4-back">← 上一步</button>
        <button class="primary" id="wz4-next">下一步：建立索引 →</button>
        
      </div>`;
    $("#wizard-body").innerHTML = `<div id="wz4-inner"><div class="wz-note">正在检查检索引擎…</div></div>`;

    const bindReady = () => {
      $("#wz4-inner").innerHTML = readyHtml;
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
      if (nameEl) nameEl.textContent = `${phase}：${j.name || ""}`;   // BF23：textContent 去 esc
      if (pctEl) pctEl.textContent = pct + "%";
      if (fillEl) fillEl.style.width = pct + "%";
      if (subEl) subEl.textContent = total > 0 ? `${mb(dl)} / ${mb(total)} MB` : `${mb(dl)} MB`;
    };

    const done = (ok, msg) => {
      if (ok) {
        $("#wz4-msg").innerHTML = `<div class="wz-result">下载完成 ✓</div>`;
        // 就绪：把「开始下载」换成「下一步」
        const actions = btn ? btn.parentNode : null;
        if (actions) actions.innerHTML = `<button class="primary" id="wz4-next">下一步：建立索引 →</button>`;
        $("#wz4-next").addEventListener("click", renderStep5);
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
    setStep(4);
    const isFolder = WZ.srcChoice === "folder";
    const entries = WZ.connected ? WZ.connected.entries : (WZ.folderConnected ? WZ.folderConnected.entries : null);
    const emptyFolder = isFolder && (entries === 0);
    const note = isFolder
      ? (emptyFolder
          ? `<div class="wz-note">文件夹还是空的——没关系，进去后把全文文件拖进窗口就会自动入库。也可以先放好文件再点下面。</div>`
          : `<div class="wz-note">下一步会<b>逐篇读出每个全文文件的题录</b>（题名/作者/年份/期刊），再建索引。
              比 Zotero 慢一些（每篇要让 AI 读一次），${entries != null ? `约 <b>${num(entries)}</b> 篇，` : ""}可以放着，完成后自动可搜。</div>`)
      : `<div class="wz-note">下一步<b>建立索引</b>：把每篇的「标题+摘要+关键词」建成可搜索索引。
          <b>0 等待、秒级完成</b>，完成后检索框和库总览立刻可用。${entries != null ? ` 待索引约 <b>${num(entries)}</b> 篇。` : ""}</div>`;
    const goLabel = emptyFolder ? "进入知识库 →" : (isFolder ? "开始建库（读取题录）" : "开始建立索引");
    $("#wizard-body").innerHTML =
      note +
      `<div id="wz5-msg"></div>
      <div class="wz-actions">
        <button class="ghost2c wz-back" id="wz5-back">← 上一步</button>
        <button class="primary green" id="wz5-go">${goLabel}</button>
      </div>`;
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
          let r = await jpost(ep, {});
          // 先判 ok===false：need_key→退文件名重试；busy→已有构建在跑，提示重试并恢复按钮（别假报「已开始」）
          if (r && r.ok === false) {
            if (r.need_key) { r = await jpost("/index/folder_build_nokey", {}); }
            else {
              $("#wz5-msg").innerHTML = `<div class="wz-note">${esc(r.msg || "已有任务在跑，请稍后再试。")}</div>`;
              btn.disabled = false; btn.innerHTML = goLabel; return;
            }
          }
          if (r && r.ok === false) {   // nokey 仍忙
            $("#wz5-msg").innerHTML = `<div class="wz-note">${esc(r.msg || "已有任务在跑，请稍后再试。")}</div>`;
            btn.disabled = false; btn.innerHTML = goLabel; return;
          }
          $("#wz5-msg").innerHTML = `<div class="wz-result">🚀 已开始建库，正在逐篇读取题录（进度见顶部）。可直接进入，完成后自动可搜。</div>`;
          btn.outerHTML = `<button class="primary" id="wz5-enter">进入知识库 →</button>`;
          // C2：进入后强制重载库总览，刷掉冷启动残留的「统计加载失败」红字
          $("#wz5-enter").addEventListener("click", () => { closeWizard(); poll(); dashLoaded = false; loadDashboard("loud"); });
          poll();
          return;
        }
        const r = await jpost("/index/light", {});
        // BF9：/index/light 忙时返回 {ok:false,busy:true}——不能当成功渲染「已索引 0 篇」
        if (r && r.ok === false) {
          $("#wz5-msg").innerHTML = `<div class="wz-err">${esc(r.msg || "已有构建任务在跑，请稍后再试。")}</div>`;
          btn.disabled = false; btn.textContent = "重试";
          return;
        }
        const papers = r.meta_indexed != null ? r.meta_indexed : (r.total || 0);
        const wp = withFulltext(r);
        jpost("/index/semantic", {}).catch((e) => reportErr(e && e.message, "wizard auto-semantic"));
        $("#wz5-msg").innerHTML = `<div class="wz-result">🎉 已索引 ${num(papers)} 篇（其中 ${num(wp)} 篇有全文附件可深索）<br><span class="wz-subtle">检索质量已在后台自动提升，可直接进入使用。</span></div>`;
        btn.outerHTML = `<button class="primary" id="wz5-enter">进入知识库 →</button>`;
        // C2：进入后强制重载库总览，刷掉冷启动残留的失败态
        $("#wz5-enter").addEventListener("click", () => { closeWizard(); poll(); dashLoaded = false; loadDashboard("loud"); maybeDeepInvite(); });
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
    const withPdf = withFulltext(st), deep = st.deep_done || 0, blocked = extractCounts(st).blocked;
    if (!(withPdf > 0 && (deep + blocked) < withPdf)) return;
    renderDeepInvite(deep, withPdf, st.building && st.stage === "deep", st);
  }
  function renderDeepInvite(deep, withPdf, alreadyBusy, st) {
    if ($("#deep-invite")) return;
    const card = document.createElement("div");
    card.id = "deep-invite";
    card.className = "deep-invite";
    card.innerHTML =
      `<div class="di-ic">📄</div>
      <div class="di-txt"><b>深索让回答回溯到原文位置</b>
        <span>已深索 <b>${num(deep)}</b>/<b>${num(withPdf)}</b> 篇有全文附件的文献。把剩余文献的全文拆成可检索的小段，可后台进行。${sacFrag(st)}</span></div>
      <div class="di-btns"><button class="go">深索全库</button><button class="later">以后再说</button></div>`;
    $("#results").parentNode.insertBefore(card, $("#results"));
    card.querySelector(".later").addEventListener("click", () => {
      localStorage.setItem("localkb.deepDismissed", "1"); card.remove();
    });
    const startDeep = async () => {
      const b = card.querySelector(".go"); b.disabled = true;
      const later = card.querySelector(".later"); if (later) later.style.display = "none";
      try {
        // C7：scope=all 时若后台忙，后端返回 {ok:false,busy:true}——此时不假装已开始，恢复按钮并提示
        const r = await jpost("/index/deep", { scope: "all" });
        if (r && r.ok === false) { b.textContent = "已有任务在跑，稍后再试"; b.disabled = false; if (later) later.style.display = ""; return; }
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
      // R16：空文件夹进入后不建索引→无 manifest→d.indexed 恒 false 会每次重弹向导。
      // 用本机 onboarded 标记兜底：已走完向导的就不再弹，改走老用户回访路径。
      const onboarded = (() => { try { return localStorage.getItem("localkb.onboarded") === "1"; } catch (e) { return false; } })();
      if (!d.indexed && !onboarded) { $("#wizard").hidden = false; renderStep1(); }
      else { maybeDeepInvite(); maybeShowDropzone(); }  // 老用户回访：深索提示 + 文件夹空库 dropzone
    } catch (e) { /* 后端不可达时不弹向导，让状态 pill 提示 */ }
  }
  // ══════════════════════════════════════════
  //  文件夹模式：拖入 / 选择 PDF 入库
  // ══════════════════════════════════════════
  function openMetaKeyHelp() {
    // UX9：直达「检索 → 检索引擎」（Key 就填在这）。
    openSettings("sec-engine");
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
    // W2：弹窗可能已被 Esc/遮罩收起（入库照常后台跑）——结果用浮层补告知，否则失败信息会被完全错过
    if ($("#ingest-modal").hidden) {
      flashToast(failed.length ? `入库完成：新增 ${num(r.added || 0)} 篇，⚠ ${num(failed.length)} 篇未入库（重新拖入可看详情）`
                               : `入库完成：新增 ${num(r.added || 0)} 篇 ✓`);
    }
    // 收尾刷新
    browseLoaded = false; if (!$("#panel-browse").hidden) loadBrowse();
    if (dashLoaded) loadDashboard("silent");
    poll();
  }
  async function ingestFiles(fileList) {
    const files = Array.from(fileList || []).filter((f) => /\.(pdf|epub|docx|md|markdown|txt)$/i.test(f.name));
    if (!files.length) { toast("只支持 PDF、EPUB、DOCX、Markdown、TXT 文件（不支持 HTML）"); return; }
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
      if ($("#ingest-modal").hidden) flashToast("⚠ 入库失败：" + (e.message || e));   // W2：弹窗已收起也要告知
    }
  }
  function initDragIngest() {
    let dragDepth = 0;
    const overlay = $("#drop-overlay");
    const isFileDrag = (e) => Array.from((e.dataTransfer && e.dataTransfer.types) || []).includes("Files");
    // B3：只要拖入的是文件，就无条件 preventDefault（阻止浏览器导航/打开文件），再按模式分支。
    window.addEventListener("dragenter", (e) => {
      if (!isFileDrag(e)) return;
      e.preventDefault();
      if (APP.source !== "folder") return;   // zotero 模式不弹入库遮罩
      dragDepth++; if (overlay) overlay.hidden = false;
    });
    window.addEventListener("dragover", (e) => {
      if (!isFileDrag(e)) return;
      e.preventDefault();                      // 无条件阻止导航（否则松手浏览器会打开该 PDF、离开应用）
      if (APP.source === "folder") e.dataTransfer.dropEffect = "copy";
      else e.dataTransfer.dropEffect = "none";
    });
    window.addEventListener("dragleave", (e) => {
      if (APP.source !== "folder") return;
      if (--dragDepth <= 0) { dragDepth = 0; if (overlay) overlay.hidden = true; }
    });
    window.addEventListener("drop", (e) => {
      if (!isFileDrag(e)) return;
      e.preventDefault(); dragDepth = 0; if (overlay) overlay.hidden = true;
      if (APP.source === "folder") { ingestFiles((e.dataTransfer && e.dataTransfer.files) || []); }
      else { flashToast("Zotero 模式不支持直接拖入文件。请在 Zotero 里添加受支持全文附件后，点顶栏「⟳ 更新知识库」。"); }
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
  loadDiscipline();
  maybeWizard();
  // 冷启动：默认页＝库总览，主动加载一次（懒加载此前只在点 tab 时触发；loadDashboard 只 GET /stats，无副作用）
  if (!dashLoaded) loadDashboard("loud");
})();

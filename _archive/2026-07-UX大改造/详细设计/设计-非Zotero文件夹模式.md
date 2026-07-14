# LocalKB 详细设计 · 非 Zotero「文件夹模式」

> ---
> ⛔ **归档件 · 请勿执行**
> 本文档记录的是 2026 年 7 月某一轮改造的**方案/指令/调研**，相关内容**均已全量实施完毕**。
> 它保留在此仅为「当时为什么这么决定」的决策存档。
> **不要把它当作待办清单，不要照此重跑改造。**
> 项目当前的事实、规则与待办，一律以项目根 `CLAUDE.md` 为准。
> ---


> 面向照此写代码的工程师。目标：让没有 Zotero 的用户也能建库——指定/新建一个文件夹放 PDF，系统用 LLM 从正文自动抽题录，隐藏 Zotero 专属 UI，支持把 PDF 拖进窗口即自动入库。与 Zotero 模式并存、零破坏。
> 本文分两部分：**第一部分·后端**（数据源抽象 / folder_source / folder_meta / folder_ingest / 拖入端点 / 管线顺序）；**第二部分·前端**（向导文件夹分支 / 条件隐藏 Zotero / 拖入交互）。两部分已就共享契约（source 设置、记录 dict 形状、detect 字段、单构建锁）对齐。
> 日期：2026-07-08

---

# 第一部分 · 后端（数据源与管线）

# LocalKB「文件夹模式」后端设计（数据源抽象 + 文件夹管线 + LLM 元数据抽取）

> 本文档面向照此写代码的工程师。所有 `文件:行号` 均指向 `D:\Onedrive\AI\知识库应用\LocalKB源码` 下的现状代码。前端交互（第 5 节）与你的同伴 agent 共担，本文只给出后端契约要求的最小前端改动点。

## 1. 目标与范围

**要做的**
- 让**没有 Zotero**的用户也能建库：指定/新建一个"受管文件夹"，把 PDF 放进去。
- 文件夹里的 PDF 无题录 → 由 LLM 从正文首页抽题名/作者/年份/期刊/页码/摘要等（"系统帮我解决"）。
- 文件夹模式下隐藏 Zotero 专属 UI（收藏夹树等）。
- 拖 PDF 进窗口即自动入库（复制进受管文件夹→抽取→LLM 补元数据→索引→可深索）。
- 与现有 Zotero 模式**并存**，不破坏任何现有行为。

**不做的（本期）**
- 不做 OCR：扫描件/图片型 PDF 只能退回文件名当 title、标 `needs_review`（`extract.py:91` 已识别此类）。
- 不做在线题录补全（CrossRef/DOI 反查）——纯靠 LLM 从正文抽，未来可作增强。
- 不做元数据人工编辑的完整 UI（仅落 `needs_review` 标记 + 预留 `PATCH /paper/{key}` 接口位；实际编辑面板留给前端后续迭代）。
- 不改动 Zotero 模式的任何路径。

## 2. 现状（关键 `文件:行号`）

- **数据源唯一硬编码 Zotero**：`index_light.get_papers()`（`index_light.py:25-31`）内部 `import zotero_source`，`Z.available()` 失败即抛错。返回 `(papers, "zotero.sqlite")`。
- **记录 dict 形状**：`zotero_source.load_papers()`（`zotero_source.py:117-132`）产出 14 字段：`key/title/author/year/journal/doi/langid/keywords/abstract/itemtype/official_pages/has_pdf/pdf_path/collections`。
- **enrich 补字段（数据源无关）**：`index_light.enrich()`（`index_light.py:44-57`）给每条补 `text/stem/journal_tier/tier_rank/lang/ingested_at`，并 `setdefault` 了 `official_pages/has_pdf/collections`。**任何额外字段（如 `needs_review`）都会原样透传进 `papers.jsonl`**（`index_light.py:99-101` 全量 `json.dumps`）。
- **light 管线**：`index_light.main()`（`index_light.py:90-122`）= get_papers→build_dict→enrich→写 `papers.jsonl`→建 `bm25_meta`→`compute_stats`→写 `index_manifest`（记 `source/backend`）。
- **提取**：`extract.py:39-56 _extract_pages(pdf)` 逐页取文本（`pymupdf4llm` 优先，回退 `fitz`）；`extract_one`（`extract.py:75-100`）幂等（`:77` 已存在则 skip），只对 `has_pdf and pdf_path` 的篇跑（`:64`）。
- **LLM 复用范式**：`sac.py:37-43 summarize_one` 用 `llm.chat_once`；`sac._conf()`（`sac.py:46-54`）在自身 key 为空时**自动复用 `api_conf()` 的 key**——这是"一个 key 通吃"的落点。`llm.chat_once`（`llm.py:32-41`）非流式返回文本，`base/key/model` 缺一即 `ValueError`。
- **设置**：`settings.py:17-37 DEFAULT` 有 `backend/journal_discipline/api/sac`；`load/save` 带原子写与 mtime 缓存（`settings.py:52-79`）。
- **检测/连接**：`server.py:99-126 setup_detect` 返回 `zotero_dir/source/backend/...`；`server.py:131-143 setup_connect` 校验 zotero。
- **建库编排**：`build_all.py:28-40` 定义步骤，`steps` 字典按 `--stage` 选；`CAT/TOPICS` 在 `SOFT`（`:40`）失败不阻断。`build_all.py:30 CAT` = `build_categories.py`（只读 zotero.sqlite）。
- **建库触发/锁**：`server.py:490-505 /index/light` **在进程内**同步跑 `IL.main()`（会阻塞请求）；`server.py:507-531 _run_build` 用单例锁 `BUILD["running"]`（`server.py:23`）在后台子进程跑 `build_all.py` 并流式收日志，完成后 `R.load_all()` 重载。
- **进度**：`server.py:291-303 /index/status` 回 `light_done/source/papers/with_pdf/meta_done/deep_done/building/stage/log`。
- **浏览**：`server.py:451-487 /papers` 输出含 `collections`；前端左栏收藏夹树在 `app.js:527-536 loadTree` 拉 `/categories`。
- **向导**：`app.js:1448-1458 maybeWizard`→`renderStep1..5`。Step1 硬写"Zotero 目录"检查行（`app.js:1088`），Step3 连接 zotero（`app.js:1230-1259`），Step5 调阻塞式 `/index/light`（`app.js:1388`）。

## 3. 数据模型 / 存储

### 3.1 新增设置字段（`settings.py:17 DEFAULT`）
```python
DEFAULT = {
    "backend": "local",
    "source": "zotero",          # 新增：zotero | folder
    "folder_dir": "",            # 新增：受管库文件夹绝对路径（source=folder 时有效）
    "journal_discipline": "law",
    "api": {...}, "sac": {...},
    # 新增：文件夹模式元数据抽取的 LLM（key 空时复用 api/sac 的 key，见 folder_meta._conf）
    "folder_meta": {
        "enabled": True,
        "base": "https://api.siliconflow.cn/v1",
        "key": "",
        "model": "Qwen/Qwen2.5-7B-Instruct",   # 与 SAC 同款：纯指令、出词快、便宜
        "workers": 3,
    },
}
```
新增便捷读取器（仿 `settings.py:82-96`）：
```python
def source():     return load().get("source", "zotero")
def folder_dir(): return load().get("folder_dir", "")
def folder_meta_conf(): return load().get("folder_meta", DEFAULT["folder_meta"])
```

### 3.2 新增文件
| 文件 | 职责 |
|---|---|
| `folder_source.py` | 扫 PDF、生成稳定 key、读 meta_cache 组装记录 dict（形状 = zotero_source） |
| `folder_meta.py` | `extract_meta(首页文本)->{title,author,...}`（LLM，严格 JSON，兜底） |
| `folder_ingest.py` | 文件夹模式的"元数据预处理"build 步骤：扫描→抽首页→LLM→写 meta_cache（幂等/增量/并发） |

### 3.3 新增数据文件（`config.py` 加常量，仿 `config.py:30-37`）
```python
FOLDER_DIR_STATE = DATA / "folder"                 # 文件夹模式 sidecar 根
FOLDER_META_CACHE = FOLDER_DIR_STATE / "meta_cache.json"   # {key: {meta…, file, mtime, sha1, needs_review, extracted_at}}
```
并把 `FOLDER_DIR_STATE` 加进 `config.py:64-67` 的自动 mkdir 列表。

> `meta_cache.json` 是文件夹模式的**元数据事实来源**（对应 Zotero 的 sqlite）。`papers.jsonl` 仍是下游统一消费面，由 `index_light` 从 meta_cache 生成。

### 3.4 记录 dict 差异（folder vs zotero）
形状**完全一致**，来源不同，且多一个 `needs_review`：

| 字段 | zotero 来源 | folder 来源 |
|---|---|---|
| key | zotero item key | `"f_"+sha1(相对路径)[:10]`（重命名才变） |
| title | sqlite title | LLM，失败退文件名（去扩展名） |
| author/year/journal/doi/langid/abstract/official_pages/itemtype | sqlite 字段 | LLM 严格 JSON；缺则空串 |
| keywords | tags（`zotero_source.py:89`） | 空串（LLM 可选补，一般空） |
| has_pdf | 有附件 | **恒 True** |
| pdf_path | storage 路径 | 文件绝对路径 |
| collections | 收藏夹名列表 | `[]`（默认）；子文件夹→分类为可选增强（见 9.3） |
| **needs_review** | 无（视为 False） | **True**，直到人工确认 |

`enrich()`（`index_light.py:44-57`）无需改动即可透传 `needs_review`。

## 4. 后端接口 / 模块

### 4.1 数据源分派：`index_light.get_papers()`（改 `index_light.py:25-31`）
```python
def get_papers():
    import settings as S
    src = S.source()
    if src == "folder":
        import folder_source as F
        d = S.folder_dir()
        if not d or not Path(d).exists():
            raise RuntimeError("文件夹模式未配置受管库文件夹（请在向导/设置里指定）")
        return F.load_papers(d), f"folder:{d}"
    # 默认 zotero（保持现状）
    import zotero_source as Z
    if not Z.available():
        raise RuntimeError("未探测到 zotero.sqlite（…）")
    return Z.load_papers(), "zotero.sqlite"
```
`main()`（`index_light.py:93,117`）无需改：`source` 变量原样写进 manifest。`compute_stats`（`index_light.py:70` `by_collection`）在 collections 全空时自然产出空列表，前端据此隐藏。

### 4.2 `folder_source.py`（新增）
```python
# scan：递归找 PDF，返回绝对路径列表（排序稳定）
def scan(folder):
    return sorted(str(p) for p in Path(folder).rglob("*.pdf") if p.is_file())

def stable_key(folder, pdf_path):
    rel = os.path.relpath(pdf_path, folder).replace("\\", "/")
    return "f_" + hashlib.sha1(rel.encode("utf-8")).hexdigest()[:10]

# load_papers：扫描 + 读 meta_cache，组装记录 dict（形状=zotero_source）
def load_papers(folder):
    cache = _load_cache()                       # config.FOLDER_META_CACHE
    out = []
    for pdf in scan(folder):
        key = stable_key(folder, pdf)
        m = (cache.get(key) or {}).get("meta") or {}
        title = m.get("title") or Path(pdf).stem   # 兜底：文件名当题名
        out.append({
            "key": key, "title": title,
            "author": m.get("author",""), "year": m.get("year",""),
            "journal": m.get("journal",""), "doi": m.get("doi",""),
            "langid": m.get("langid",""), "keywords": m.get("keywords",""),
            "abstract": m.get("abstract",""), "itemtype": m.get("itemtype","journalArticle"),
            "official_pages": m.get("official_pages",""),
            "has_pdf": True, "pdf_path": pdf,
            "collections": _subfolder_cats(folder, pdf),   # 默认 []，见 9.3
            "needs_review": bool((cache.get(key) or {}).get("needs_review", True)),
        })
    return out
```
**关键**：`load_papers` 不调用 LLM，只读 cache。若 cache 为空（未预处理）→ 全部退化为文件名 title + needs_review，词法索引仍可秒建（只是元数据粗糙）。真正的 LLM 补全在 `folder_ingest`（4.4）里先跑。

### 4.3 `folder_meta.py`（新增，"api/agent 驱动"的落点）
```python
SYS = ("你是文献题录抽取器。下面是一篇学术文献PDF的首1-2页文本。"
       "只输出一个JSON对象，字段：title(题名), author(作者，多个用'; '分隔), "
       "year(4位年份字符串), journal(期刊/出版物名), official_pages(正式页码如'1-20'，无则空), "
       "abstract(摘要，无则空), itemtype(journalArticle/book/thesis/report之一), "
       "langid(zh或en). 无法确定的字段留空字符串。不要输出JSON以外的任何内容。")

def _conf():                                     # 仿 sac.py:46-54
    c = dict(S.folder_meta_conf())
    if not c.get("key"):
        for src in (S.sac_conf(), S.api_conf()):
            if src.get("key"):
                c["key"] = src["key"]; c["base"] = c.get("base") or src.get("base"); break
    return c

def available():                                 # 无 key → 不可用
    c = _conf(); return bool(c.get("enabled") and c.get("key"))

def extract_meta(head_text):
    """返回 (meta_dict, needs_review, err)。无 key 抛 NoKey；解析失败退空+needs_review。"""
    c = _conf()
    if not (c.get("enabled") and c.get("key")):
        raise NoKeyError("未配置 LLM，无法抽取题录")
    msgs = [{"role":"system","content":SYS},
            {"role":"user","content":(head_text or "")[:4000]}]
    try:
        raw = L.chat_once(msgs, c["base"], c["key"], c["model"], temperature=0.1, timeout=90)
        j = _parse_json(raw)                      # 剥离```json```围栏 + json.loads
        meta = {k: _clean(j.get(k,"")) for k in
                ("title","author","year","journal","official_pages","abstract","itemtype","langid")}
        meta["year"] = re.search(r"\d{4}", meta["year"] or "") and re.search(r"\d{4}", meta["year"]).group(0) or ""
        needs_review = True                       # LLM 抽的始终待人工确认
        return meta, needs_review, None
    except Exception as e:
        return {}, True, f"{type(e).__name__}: {e}"   # 兜底：上层退回文件名 title
```
**兜底策略**：JSON 解析失败/超时/网络错 → 返回空 meta + `needs_review=True` + err（记日志、不崩），`folder_source` 用文件名当 title。**无 key** → `extract_meta` 抛 `NoKeyError`，`folder_ingest` 捕获后整批停在"待配 key"态，UI 显式引导。

### 4.4 `folder_ingest.py`（新增 build 步骤 — 解决"管线顺序"难点）
```
用法: python folder_ingest.py [--workers 3] [--limit N]
职责: 扫 folder_dir → 对"新/未抽"的 PDF 抽首1-2页 → LLM 抽题录 → 写 meta_cache（幂等/增量/并发）。
必须先于 index_light 跑（folder 模式下 papers.jsonl 的元数据依赖它）。
```
```python
def _head_text(pdf, key):
    # 复用 extract：若深索已提取过整篇，直接读前2页，避免重开 PDF
    ex = C.EXTRACTED / f"{safe_name(key)}.json"
    if ex.exists() and ex.stat().st_size>0:
        pages = json.loads(ex.read_text("utf-8")).get("pages") or []
    else:
        import extract as E
        pages = E._extract_pages(pdf)[:2]        # 只要首1-2页
    return "\n".join(p.get("text","") for p in pages[:2]).strip()

def ingest_one(folder, pdf, cache):
    key = FS.stable_key(folder, pdf)
    if key in cache and cache[key].get("meta"):  # 幂等 skip（仿 extract.py:77 / sac.py:72）
        return "skip"
    text = _head_text(pdf, key)
    if not text:                                 # 扫描件/空文本
        cache[key] = {"meta":{"title":Path(pdf).stem}, "file":pdf,
                      "needs_review":True, "note":"no_text"}; return "empty"
    meta, needs_review, err = FM.extract_meta(text)
    if not meta.get("title"): meta["title"] = Path(pdf).stem
    cache[key] = {"meta":meta, "file":pdf, "sha1":_file_sha1(pdf),
                  "needs_review":needs_review, "extracted_at":now(), "err":err}
    return "ok" if not err else "fallback"

def main():
    folder = S.folder_dir(); pdfs = FS.scan(folder)
    cache = _load_cache()
    # 增量：删除的文件从 cache 剔除（config.FOLDER_META_CACHE）
    live = {FS.stable_key(folder,p) for p in pdfs}
    for k in list(cache): 
        if k not in live: del cache[k]
    if not FM.available():
        print("[folder] 未配置 LLM，跳过题录抽取（退回文件名题名）", flush=True)
        _save_cache(cache); return                # light 仍能建，只是元数据粗糙
    todo = [p for p in pdfs if FS.stable_key(folder,p) not in cache
                              or not cache[FS.stable_key(folder,p)].get("meta")]
    # 并发（仿 extract.py:119），连续失败熔断（仿 sac.py:84）
    with ThreadPoolExecutor(max_workers=S.folder_meta_conf().get("workers",3)) as ex:
        for i, fut in enumerate(as_completed({ex.submit(ingest_one,folder,p,cache):p for p in todo}),1):
            _ = fut.result()
            if i % 5 == 0: _save_cache(cache); print(f"[folder] {i}/{len(todo)} …", flush=True)
    _save_cache(cache)
```
> 并发写 `cache`（dict）在 CPython 下对单键赋值是安全的；每 5 篇落盘一次做断点续跑。若担心竞态，可给 `_save_cache` 加锁——量小、可忽略。

### 4.5 build_all：新增 `folder` 阶段（改 `build_all.py:28-40`）
```python
FOLDER_PREP = ("题录抽取", [PY, str(C.APP/"folder_ingest.py"), "--workers", str(args.workers)])
# folder 模式：先 FOLDER_PREP 补 meta_cache，再 LIGHT（读 cache 建词法），再 SEM
steps = {"light":[LIGHT], "semantic":[SEM,TOPICS], "deep":DEEP,
         "all":[LIGHT, CAT, SEM, TOPICS],
         "folder":[FOLDER_PREP, LIGHT, SEM]}[args.stage]   # 新增
```
**注意**：folder 模式不放 `CAT`（`build_categories.py` 只读 zotero.sqlite，会失败——虽在 SOFT 里不阻断，但徒增噪音）。让 `build_categories.py` 首行加 `if S.source()!="zotero": return`（早退），或直接不列入 folder steps（如上）。`FOLDER_PREP` 应视为**硬步骤**（失败要让用户知道），但若你希望"无 key 也能建库"，folder_ingest 在无 key 时正常退出（returncode 0），不会阻断 LIGHT。

### 4.6 路由 folder 建库到后台（改 `server.py`）
folder 模式的"light"实际是分钟级（含 N 次 LLM），**不能**走进程内阻塞的 `/index/light`（`server.py:490-505`）。新增：
```python
@app.post("/index/folder_build")
def index_folder_build():
    import settings as S
    if S.source() != "folder":
        return JSONResponse({"ok":False,"msg":"当前非文件夹模式"}, status_code=400)
    if not FM_ready():                            # folder_meta.available()
        return JSONResponse({"ok":False,"msg":"请先在设置里配置 API Key（用于自动抽取题录）",
                             "need_key":True}, status_code=400)
    return {"ok": _run_build("folder")}           # 复用 BUILD 锁 + 流式日志 + 完成后 R.load_all
```
向导 folder 分支的"开始建库"调此端点，然后轮询 `/index/status`（`server.py:291`，`stage` 会是 `folder`）。Zotero 模式仍走原 `/index/light`，零改动。

### 4.7 detect 扩展（改 `server.py:99-126 setup_detect`）
```python
st = S.load()
src = st.get("source") or (manifest.get("source","").startswith("folder") and "folder") or ("zotero" if zdir else None)
return {
    ...(现有字段)...,
    "source": src,                                # zotero | folder | None
    "folder_dir": st.get("folder_dir",""),
    "folder_meta_ready": _folder_meta_ready(),    # folder_meta.available()：LLM key 是否就绪
    "zotero_detected": bool(zdir),                # 与 source 解耦：探到 zotero≠一定用它
}
```
`source` 为 None（探不到 zotero 且未选 folder）时，向导应引导用户在 zotero / folder 间二选一。

### 4.8 保存文件夹选择（新增 `server.py`）
```python
class FolderQ(BaseModel):
    folder_dir: str
@app.post("/setup/folder")
def setup_folder(q: FolderQ):
    p = Path(q.folder_dir)
    try: p.mkdir(parents=True, exist_ok=True)     # 支持"新建文件夹"
    except Exception as e:
        return JSONResponse({"ok":False,"msg":f"无法创建/访问该文件夹：{e}"}, status_code=400)
    n = len(FS.scan(str(p)))
    S.save({"source":"folder","folder_dir":str(p.resolve())})
    return {"ok":True,"folder_dir":str(p.resolve()),"pdf_count":n,
            "folder_meta_ready":_folder_meta_ready()}
```

### 4.9 拖入入库 `POST /ingest/files`（新增 `server.py`）
契约：复制进 `folder_dir`→去重→跑该批 ingest→返回 added/failed。与全局锁 `BUILD["running"]`（`server.py:508`）的关系：**入库即建库，必须持锁**。采用"先复制（快）+ 后台增量 folder build（慢）"：
```python
@app.post("/ingest/files")
async def ingest_files(files: List[UploadFile] = File(...)):   # 或 body 传本地绝对路径列表
    import settings as S
    if S.source()!="folder":
        return JSONResponse({"ok":False,"msg":"仅文件夹模式支持拖入入库"}, status_code=400)
    folder = S.folder_dir()
    if not folder: return JSONResponse({"ok":False,"msg":"未配置受管文件夹"}, status_code=400)
    if BUILD["running"]:
        return JSONResponse({"ok":False,"msg":"正在建库/入库中，请稍后再拖入","building":True}, status_code=409)
    existing = {_file_sha1(p) for p in map(str, Path(folder).rglob("*.pdf"))}
    added, skipped, failed = [], [], []
    for f in files:
        if not f.filename.lower().endswith(".pdf"): failed.append((f.filename,"非PDF")); continue
        data = await f.read()
        h = hashlib.sha1(data).hexdigest()
        if h in existing: skipped.append(f.filename); continue      # 同内容去重
        dst = _dedupe_name(Path(folder)/Path(f.filename).name)      # 同名不同容→加后缀
        dst.write_bytes(data); existing.add(h); added.append(dst.name)
    if not FM_ready():
        return {"ok":True,"added":len(added),"skipped":len(skipped),"failed":failed,
                "building":False,"need_key":True,
                "msg":"已复制入库，但未配置 API Key，暂不能自动生成题录"}
    _run_build("folder")                          # 后台增量：只处理新 key（幂等），完成后重载
    return {"ok":True,"added":len(added),"skipped":len(skipped),"failed":failed,"building":True}
```
- **去重**：内容 sha1（同文件）+ 同名不同容加序号后缀。key 由相对路径决定，天然唯一。
- **锁**：`BUILD["running"]` 时返回 409，前端提示稍后再试；否则复制后由 `_run_build("folder")` 接管锁。增量靠 `folder_ingest` 的 skip 幂等（4.4），只抽新文件。
- **进度**：前端拉 `/index/status`（`stage=folder`）显示"抽取中 x/y"。

### 4.10 增量重扫（"更新知识库"）
folder 模式下"更新"按钮 → `POST /index/folder_build`（4.6）。`folder_ingest.main`（4.4）通过 `live` 集合对比：新增文件抽取、删除文件从 cache 剔除，随后 LIGHT 重建 `papers.jsonl` 与 bm25 → 与已入库 key 集合自动对齐。与 Zotero 模式"重跑=增量"（`build_all.py:8`）语义一致。

## 5. 前端交互与状态（后端契约要求的最小改动，细节由前端 agent 负责）

- **向导分支**（`app.js:1448 maybeWizard`→读 `/setup/detect.source/zotero_detected`）：
  - `zotero_detected=true` → 现状 Zotero 流程（Step1 目录检查 `app.js:1088` 保持）。
  - 探不到 → 新增"文件夹模式"入口：一屏让用户**选择/新建文件夹**（调 `/setup/folder`），并**显式引导配 API Key**（复用 Step2 的 API 表单 `app.js:1122-1147`，因为 folder_meta 默认复用同一 key）。若无 key，禁用"开始建库"并提示"文件夹模式需要 API Key 让系统自动读出每篇的题名、作者、年份"。
  - "开始建库"改调 `/index/folder_build`（4.6）并轮询 `/index/status`（stage=folder，显示"正在读取第 x/y 篇的题录"）——**不要**用阻塞式 `/index/light`。
- **隐藏 Zotero 专属 UI**：`app.js:527 loadTree`（收藏夹树）在 `source==="folder"` 时不渲染、隐藏 `#bt-head`/`#bt-tree`（`index.html:70-72`）。判据取 `/setup/detect.source` 或 `/index/status.source`（后者形如 `folder:<dir>`，用 `startsWith("folder")` 判）。`by_collection` 空数组也可作辅助判据。
- **拖拽入库**：知识库主窗口监听 `dragover/drop`，收集 PDF → `POST /ingest/files`（multipart 或本地路径），成功后拉 `/index/status` 接管进度；`need_key=true` 时弹配 key 引导。
- **needs_review 标记**：`/papers`（`server.py:471-478`）在输出里加透传 `needs_review`，列表对这些篇显示"待核对"角标（提示题录由 AI 生成、可能有误）。复用现有 `/papers` 渲染函数（`app.js` 列表渲染），仅加一个字段与样式类。

## 6. 文案（成稿，面向非工程师、非 Zotero 用户）

- **向导·检测不到 Zotero**：
  > 没检测到 Zotero，没关系。你可以直接用「文件夹模式」：选一个文件夹（或新建一个）当作你的知识库，把 PDF 论文放进去就行。这些 PDF 通常没有题名、作者这些信息，本软件会**自动读出**它们——只需要你配一个免费的 API Key。

- **配 Key 引导（folder 模式必需时）**：
  > 文件夹里的 PDF 只有正文，没有题名/作者/年份等信息。填入一个 API Key（推荐 SiliconFlow 免费额度），软件就能读每篇 PDF 的开头，自动帮你填好题录。这一步只需做一次。如果你在"对话"里已经填过 key，这里会自动复用。

- **无 Key 时的降级**：
  > 还没配 Key？也可以先建库——软件会先用**文件名**当题名，之后配好 Key 再"更新知识库"，就会自动补全题录。

- **建库进度（folder）**：
  > 正在读取每篇 PDF 的题录信息（第 12 / 68 篇）…比 Zotero 慢一些，因为要逐篇让 AI 读出题名、作者、年份。可以放着，完成后自动可搜。

- **拖入入库**：
  > 把 PDF 直接拖进这个窗口即可入库：软件会复制到你的知识库文件夹，读出题录，建好索引。重复的文件会自动跳过。

- **待核对角标**：
  > 「待核对」——这条题录是 AI 从正文读出来的，可能有出入，重要引用前建议核对一下题名/年份/期刊。

## 7. 迁移 / 兼容 / 回归

- **默认值兼容**：`source` 缺省 `"zotero"`（`settings.py DEFAULT`），老用户 `settings.json` 无该字段时 `_merge`（`settings.py:42-49`）自动补默认 → 行为不变。
- **Zotero 路径零改动**：`get_papers()` 的 folder 分支仅在 `source=="folder"` 触发；`/index/light`、`build_all --stage all/light`、`build_categories`、detect/connect 对 zotero 用户完全照旧。
- **manifest 一致性**：folder 模式 `source` 写成 `folder:<dir>`（`index_light.py:117`）；`/index/status`、`/setup/detect` 用 `startswith("folder")` 判别，不影响 backend 一致性校验（`settings.py:6` 铁律、`server.py:166-172`）——backend(local/api) 与 source(zotero/folder) 正交。
- **compute_stats**：`by_collection`（`index_light.py:70,82`）在 collections 全空时产出空列表，`/stats` 与前端不崩。
- **回归清单**：① Zotero 用户建库/检索/收藏夹树不变；② folder 用户从零建库；③ 两模式切换（改 `source` 后需重跑 folder_build 或 light）；④ 无 key 建 folder 库→退文件名；⑤ 拖入去重；⑥ 增量新增/删除文件重扫；⑦ backend=api 与 source=folder 组合（同一 key 通吃 embed + folder_meta）。

## 8. 分步实现清单（有序、可勾选，标 S/M/L 与依赖）

1. **[S]** `settings.py`：加 `source/folder_dir/folder_meta` 到 DEFAULT + 三个读取器。（无依赖）
2. **[S]** `config.py`：加 `FOLDER_DIR_STATE/FOLDER_META_CACHE` 常量并入 mkdir 列表。（无依赖）
3. **[M]** `folder_source.py`：`scan/stable_key/load_papers/_load_cache`。（依赖 2）
4. **[M]** `folder_meta.py`：`_conf/available/extract_meta/_parse_json`（严格 JSON + 兜底）。（依赖 1）
5. **[M]** `folder_ingest.py`：扫描→抽首页（复用 `extract._extract_pages`）→LLM→写 cache，幂等/并发/增量/熔断。（依赖 3、4）
6. **[S]** `index_light.get_papers()`：按 `source` 分派。（依赖 1、3）
7. **[S]** `build_all.py`：加 `folder` 阶段（FOLDER_PREP→LIGHT→SEM）。（依赖 5、6）
8. **[S]** `build_categories.py`：`source!="zotero"` 早退（防噪音）。（依赖 1）
9. **[M]** `server.py`：`/setup/folder`、`/index/folder_build`、扩展 `setup_detect`、`/papers` 透传 `needs_review`。（依赖 6、7）
10. **[M]** `server.py`：`POST /ingest/files`（复制+去重+触发 folder_build+锁）。（依赖 9）
11. **[L]** 前端（前端 agent）：向导 folder 分支、隐藏收藏夹树、拖拽入库、needs_review 角标、folder 进度轮询。（依赖 9、10）
12. **[S]** `sync_app.ps1`：两棵代码树同步新文件。（依赖 3-10）
13. **[S]** 回归验证：走第 7 节清单。（依赖全部）

## 9. 风险与未决点（诚实，附核实方法）

1. **速度代价（明确有代价）**：Zotero light 是秒级（0 LLM，`index_light.py:121`）；folder 模式每篇 1 次 LLM（首页抽题录）+ 首页 PDF 提取。粗估 SiliconFlow 免费 Qwen2.5-7B 单篇 2-5s，`workers=3` 下 **200 篇≈5-15 分钟**。这是"从正文抽元数据"的固有成本，无法秒级。缓解：并发 + 断点续跑 + 先建粗库（文件名 title）后台补题录。**核实**：拿 20 篇真实法学 PDF 跑 `folder_ingest.py`，看端到端耗时与失败率。
2. **LLM 抽取质量（journal 尤其不可靠）**：期刊名/页码/作者 LLM 易错或幻觉。下游 F38-B 期刊分级 `JT.tier_of(journal)`（`index_light.py:52`）吃这个字段 → folder 库 tier 多为"未知"或误判。**这是为何全部标 `needs_review`**。**核实**：抽样比对 LLM 输出与 PDF 实际题录，统计 title/year/journal 准确率；journal 命中期刊库的比例。缓解方向（未来）：预留 `PATCH /paper/{key}` 让用户改 journal 后重算 tier。
3. **半自动助手 page_map 衔接**：page_map 依赖 `official_pages` + 提取文本把"PDF 物理页"映射到"期刊正式页"。folder 模式 `official_pages` 来自 LLM（可能只给页范围如 "1-20" 或缺失）。若缺/错，页码映射退化或不可用。**核实**：读 page_map 构建代码（本次未覆盖，建议后续读 `page_map`/半自动助手模块）确认它对 `official_pages` 为空的容错行为。
4. **扫描件/图片 PDF**：`_extract_pages` 返回空（`extract.py:91`），首页无文本 → 无法抽题录，只能退文件名。本期不做 OCR。**核实**：folder_ingest 对空文本篇的 `note:"no_text"` 计数，向用户汇报"N 篇疑似扫描件，题录需手填"。
5. **拖入并发与锁**：`/ingest/files` 在 `BUILD["running"]` 时 409（`server.py:508`）。若用户连续拖多批，后续批被拒。**缓解**：前端排队；或后端把待入库文件先落盘、build 结束后自动再触发一次增量。本期用 409+提示，简单可靠。**核实**：拖入进行中再拖一批，确认提示合理、无数据丢失（文件已落盘，下次 build 会补）。
6. **key 复用来源顺序**：`folder_meta._conf` 复用顺序取 sac→api（4.3）。需确认与用户预期一致（对话用的 key、SAC 的 key、embedding 的 key 可能不同）。**核实**：三处 key 不同时，确认 folder_meta 用了对的那个；必要时在设置里给 folder_meta 独立 key 输入框（已在 DEFAULT 预留）。
7. **UploadFile vs 本地路径**：桌面应用拖拽通常能拿到**本地绝对路径**，比 multipart 上传大文件更省。`/ingest/files` 是否收路径列表取决于前端 dnd 能力（Electron/WebView2 可拿路径）。**核实**：确认宿主壳能否在 drop 事件拿到 `file.path`；能则改为传路径 + 后端 `shutil.copy2`，避免大文件走 HTTP body。

---

**新增/改动文件汇总（两棵树各一份，sync_app.ps1 同步）**：
- 新增：`folder_source.py`、`folder_meta.py`、`folder_ingest.py`
- 改动：`settings.py`（DEFAULT+读取器）、`config.py`（常量+mkdir）、`index_light.py:25-31`（分派）、`build_all.py:28-40`（folder 阶段）、`build_categories.py`（早退）、`server.py`（`/setup/folder`、`/index/folder_build`、`/ingest/files`、`setup_detect` 扩展、`/papers` 加 needs_review）
- 前端（前端 agent）：`web/app.js`、`web/index.html`、`web/style.css`

---

# 第二部分 · 前端（向导 / 隐藏 / 拖入）

# LocalKB 设计文档 · 文件夹模式（前端分支）

**范围**：本篇只覆盖分工内的三块前端 —— (A) 首启向导「文件夹」分支、(B) 浏览页条件隐藏 Zotero 内容、(C) 拖入自动入库。后端（`folder_source.py` / `folder_meta.py` / `POST /ingest/files` / `settings` 新字段 / `/setup/detect` 与 `/setup/connect` 的 folder 分支）由另一 agent 负责，本篇凡涉及后端处标记「【后端约定】」并给出前端所需的入参/出参契约，供两边对齐。

所有锚点来自 `D:\Onedrive\AI\知识库应用\LocalKB源码\`（另一棵树 `LocalKB\app\` 由 `sync_app.ps1` 同步，本篇不重复列）。

---

## 1. 目标与范围

### 1.1 要做什么
1. **向导文件夹分支**：向导第 1 步（环境自检，`app.js:1068`）没探到 Zotero 时，把第 3 步从「连接文库」升级为「选择文库来源」：A 连接 Zotero（现流程不动）/ B 文件夹模式（选/建文件夹 + 引导放 PDF + 讲清「抽题录需 LLM Key」这个额外依赖并就地引导配置）。
2. **条件隐藏 Zotero 内容**：`source=folder` 时，浏览左树整块隐藏「Zotero 分类」区（无 collections），只留「知识库分类（已深索）」+「全部文献」；其余 Zotero 字样（更新弹窗、向导副标题、F36 仅导入有 PDF）给出文件夹模式替代表述。
3. **拖入自动入库**：文件夹模式下整窗接住拖入的 PDF → 上传 `POST /ingest/files` → 进度「正在入库 N 篇（抽取元数据中…）」→ 完成刷新浏览/库总览。空库给显眼 dropzone。
4. **无 Key 引导**：抽题录依赖 LLM Key，没配则文献只有文件名 → UI 显式提示「配 API 或用 agent 补全文献信息」并给跳转。

### 1.2 不做什么
- 不改检索/排序/深索算法，不改 Zotero 模式的任何现有路径。
- 不做子文件夹→分类的镜像（契约里 collections=[]，子文件夹为可选增强，留待后续）。
- 不做拖入的「秒传/去重 UI」高级态；重复文件由后端判定，前端只展示 added/failed 计数。
- 不自己实现文件夹选择原生对话框逻辑（走【后端约定】或文本输入兜底，见 §4.2）。

---

## 2. 现状（关键锚点）

**向导**（`web/app.js`）
- 全局态 `WZ`（`app.js:1057`）：`{detect, step, backend, api:{...}, apiTested}`。`setStep(n)` 切步高亮（`app.js:1058`）。
- 五步：`renderStep1` 环境自检（`app.js:1068`，含 Zotero 目录检测行 `app.js:1088`）→ `renderStep2` 检索引擎本地/API（`app.js:1101`，API Key 存 `WZ.api.key`、测通存 `WZ.apiTested`）→ `renderStep3` 连接文库（`app.js:1230`，`POST /setup/connect`）→ `renderStep4` 准备引擎（`app.js:1262`）→ `renderStep5` 即时索引（`app.js:1371`，`POST /index/light`）。
- 步骤条 DOM：`index.html:255-261`；向导副标题「把你的 Zotero 文库…」`index.html:252`。
- `maybeWizard()`（`app.js:1448`）：进程启动读 `/setup/detect`，`!d.indexed` 则弹向导。**这是前端拿到 `source` 的天然落点。**

**浏览左树**（`web/app.js` / `index.html`）
- DOM：`aside.browse-tree` 内 `#bt-topics`（AI 主题）+ `📁收藏夹` 头 + `#bt-all`（全部）+ `#bt-tree`（Zotero 收藏夹树），`index.html:64-73`。
- `loadBrowse()`（`app.js:527`）：`loadTopics()` + `GET /categories`（渲染 `treeNodeEl` `app.js:438`）+ `loadPapers()`。
- `BR`（`app.js:435`）状态；`loadPapers()`（`app.js:669`）拼 `/papers` 参数，空态文案 `app.js:689`。
- **⚠ 与 F10-F11 epic 冲区**：`设计-分类体系-F10-F11.md:550-566` 已规划把左树改成「🗂 知识库分类（已深索）」+「📚 Zotero 分类」两区，并把 `BR.collection/topic` 收敛为 `BR.scope`。本篇的隐藏逻辑必须挂在那套结构上（见 §5.2 的两种落法）。

**Tab / 顶栏 / 更新弹窗**
- Tab 切换 `app.js:117-131`（`switchTab` 可复用）。
- 更新知识库弹窗文案「重新读取 Zotero 库」`index.html:236`；`refreshBuild` `app.js:1044`。
- 前端错误上报 `reportErr`（`app.js:8`）、`jget/jpost`（`app.js:37-43`）、`esc/num`（`app.js:34-35`）、SSE 流式读取范式（`app.js:1344-1364`）均可复用。

**无 Key 现状**
- 对话页提示需在设置里填 LLM Key（`index.html:104-107`）；`needKey()`（`app.js:552`）非硅基流动且无 key 就 `openSettings()`；`openSettings()`（`app.js:839`）。

**后端探测/连接**
- `/setup/detect`（`server.py:99`）现返回 `source`（取 manifest，`server.py:117`）、`backend`、`api_key_set`、`indexed` 等。**【后端约定】** 需扩展为返回 settings 里的 `source ∈ {zotero,folder}` 与 `folder_dir`、`meta_ready`（下文用）。
- `/setup/connect`（`server.py:131`）现只连 Zotero。**【后端约定】** 需支持 `{source:"folder", folder_dir}` 分支，返回 `{ok, source:"folder", entries, dir}`。

---

## 3. 数据模型 / 前端状态

前端不新增持久文件，新增两处内存态 + 复用 `localStorage`：

```js
// app.js 顶部（与 lastIdxStatus 同级），全局单例
const APP = { source: "zotero", folderDir: "", metaReady: false, srcLoaded: false };
```

- `APP.source`：`"zotero" | "folder"`，来源 `/setup/detect` 的 `source`。**未加载时保守当 `zotero`**（不误隐藏、不误挂拖拽）。
- `APP.metaReady`：抽题录 LLM Key 是否就绪（detect 返回 `meta_ready`，**【后端约定】**：`bool(settings.api.key or settings.sac.key)` —— 因 `folder_meta` 复用 `sac._conf()` 的 key 解析逻辑 `sac.py:46-59`，二者任一非空即可抽）。
- 向导态复用现有 `WZ`，新增字段：`WZ.srcChoice ∈ {"zotero","folder"}`、`WZ.folderDir`、`WZ.folderConnected`。

**记录 dict 差异（前端可见部分）**：文件夹模式的每篇多带 `needs_review:true`（**【后端约定】** 见全局契约），`collections:[]`。前端在浏览卡片上据 `needs_review` 加一个「待确认」小标（可选增强，§5.4），并据「title 是否等于文件名」判断是否「仅文件名、未抽到题录」以触发无 Key 提示。

---

## 4. 前端交互与状态 —— 向导文件夹分支

### 4.1 触发与默认选择
在 `maybeWizard()`（`app.js:1448`）读到 detect 后，缓存 `APP.source/folderDir/metaReady` 并置 `APP.srcLoaded=true`；向导初始来源默认：

```js
WZ.srcChoice = d.zotero_dir ? "zotero" : "folder";   // 探到 Zotero 默认 A，否则默认 B
```

第 1 步 `renderStep1`（`app.js:1068`）**微调**：当 `!d.zotero_dir`，把现有「未探测到（请确认已安装 Zotero）」那条（`app.js:1088`）下方补一句中性引导（不再暗示「必须装 Zotero」）：

```
未检测到 Zotero —— 没关系，第 3 步可选「文件夹模式」，直接放 PDF 建库。
```

按钮文案不变（「下一步：选择检索引擎 →」）。**第 2 步（检索引擎）完全不动** —— 两种来源都要检索引擎。

### 4.2 第 3 步改造：`renderStep3` → 选择文库来源

把 `renderStep3`（`app.js:1230`）改成来源选择器 + 两个分支渲染函数 `renderStep3Zotero()`（=原逻辑，整体挪入）与 `renderStep3Folder()`（新增）。骨架：

```js
function renderStep3() {
  setStep(3);
  const d = WZ.detect || {};
  const zSel = WZ.srcChoice === "zotero";
  $("#wizard-body").innerHTML = `
    <div class="wz-engines">
      <label class="wz-engine ${zSel ? "sel" : ""}" data-src="zotero">
        <input type="radio" name="wz-src" value="zotero" ${zSel ? "checked" : ""} ${d.zotero_dir ? "" : "disabled"} />
        <div class="wz-engine-body">
          <div class="wz-engine-h">🔗 连接 Zotero ${d.zotero_dir ? '<span class="wz-badge-rec">已检测到</span>' : '<span class="wz-badge-save">未检测到</span>'}</div>
          <div class="wz-engine-d">直接读取 Zotero 里每一条文献（含题录、收藏夹分类），不修改你的 Zotero 数据。</div>
        </div>
      </label>
      <label class="wz-engine ${zSel ? "" : "sel"}" data-src="folder">
        <input type="radio" name="wz-src" value="folder" ${zSel ? "" : "checked"} />
        <div class="wz-engine-body">
          <div class="wz-engine-h">📁 文件夹模式 <span class="wz-badge-save">无需 Zotero</span></div>
          <div class="wz-engine-d">指定一个文件夹放 PDF，系统用 AI 自动读出题名、作者、年份、期刊等信息。</div>
        </div>
      </label>
    </div>
    <div id="wz-src-body"></div>`;
  const sync = () => {
    WZ.srcChoice = (document.querySelector("input[name=wz-src]:checked") || {}).value || "folder";
    $$(".wz-engine").forEach(el => el.classList.toggle("sel", el.dataset.src === WZ.srcChoice));
    WZ.srcChoice === "zotero" ? renderStep3Zotero() : renderStep3Folder();
  };
  $$("input[name=wz-src]").forEach(r => r.addEventListener("change", sync));
  sync();  // 首次渲染当前分支
}
```

- `renderStep3Zotero()`：把原 `renderStep3` 的输入框/连接按钮/结果逻辑（`app.js:1234-1258`）原样搬进 `#wz-src-body`。连接成功后仍走 `renderStep4`。**连接成功时置 `WZ.srcChoice="zotero"`**，供第 4/5 步与提交用。

### 4.3 `renderStep3Folder()`：选文件夹 + 讲清 LLM 依赖

DOM（填入 `#wz-src-body`）：

```html
<div class="wz-field">
  <label>知识库文件夹（把 PDF 放进这里；可先建空文件夹，之后再拖 PDF 进来）</label>
  <div class="wz-folder-pick">
    <input id="wz-folder-dir" value="${esc(WZ.folderDir||'')}" placeholder="如 D:\\我的论文库" />
    <button class="ghost2c" id="wz-folder-browse">浏览…</button>
  </div>
</div>
<!-- 抽题录依赖：据 §4.4 计算的三态就地渲染 -->
<div id="wz-meta-dep"></div>
<div id="wz3f-msg"></div>
<div class="wz-actions">
  <button class="primary" id="wz3f-connect">建立文件夹库</button>
  <button class="skip" id="wz-skip">跳过向导</button>
</div>
```

**「浏览…」按钮**（`#wz-folder-browse`）：优先 **【后端约定】** `POST /setup/pick_folder` → 后端用 pywebview `create_file_dialog(webview.FOLDER_DIALOG)` 弹原生选择框，返回 `{dir}` 或空；把 `dir` 写回输入框。若后端未就绪或回退浏览器（无 pywebview），此按钮隐藏，仅留文本输入（用户手填/粘贴路径）。**判定回退**：`window.pywebview === undefined` 时隐藏该按钮（pywebview 会注入 `window.pywebview`）。

**「建立文件夹库」**（`#wz3f-connect`）：

```js
$("#wz3f-connect").addEventListener("click", async () => {
  const dir = $("#wz-folder-dir").value.trim();
  if (!dir) { $("#wz3f-msg").innerHTML = `<div class="wz-err">请先选择或填写一个文件夹。</div>`; return; }
  const btn = $("#wz3f-connect"); btn.disabled = true;
  try {
    const r = await jpost("/setup/connect", { source: "folder", folder_dir: dir });
    WZ.srcChoice = "folder"; WZ.folderDir = dir; WZ.folderConnected = r;
    APP.source = "folder"; APP.folderDir = dir;               // 立即生效，供后续隐藏/拖拽
    const n = num(r.entries || 0);
    $("#wz3f-msg").innerHTML = `<div class="wz-result">✅ 已建立文件夹库，发现 ${n} 个 PDF${r.entries ? "" : "（空文件夹也没关系，稍后拖 PDF 进来即可）"}</div>`;
    setTimeout(renderStep4, 700);
  } catch (e) {
    $("#wz3f-msg").innerHTML = `<div class="wz-err">建立失败：${esc(e.message)}</div>`;
    btn.disabled = false;
  }
});
```

**【后端约定】** `POST /setup/connect {source:"folder", folder_dir}`：校验/创建目录 → 写 `settings.source="folder"`、`settings.folder_dir=dir` → `folder_source.scan(dir)` 计数 → `{ok:true, source:"folder", entries:N, dir}`。目录不存在时可自动 `mkdir`（对应文案「可先建空文件夹」）。

### 4.4 讲清「抽题录需 LLM Key」——三态引导（核心）

抽题录用 `folder_meta.extract_meta` → `llm.py`，key 复用 `sac._conf()`（`sac.py:46-59`）：**settings.api.key 或 settings.sac.key 任一即可**。第 2 步若选了 API 模式，用户已填 SiliconFlow key（有免费对话模型），可直接复用；若第 2 步选本地模式，则没有 key，必须补。据此在 `#wz-meta-dep` 渲染三态：

```js
function renderMetaDep() {
  const box = $("#wz-meta-dep");
  const hasKey = (WZ.backend === "api" && WZ.api.key) || WZ.detect?.meta_ready;
  if (hasKey) {
    box.innerHTML = `<div class="wz-note wz-note-ok">🤖 <b>题录抽取已就绪</b>：入库时会用你上一步配置的 API Key，
      自动从 PDF 正文读出题名 / 作者 / 年份 / 期刊 / 摘要。</div>`;
    return;
  }
  // 无 key：就地给一个最小 key 表单（推荐 SiliconFlow 免费）
  box.innerHTML = `
    <div class="wz-note wz-note-warn">⚠️ <b>还差一步：配一个 AI 的 API Key</b><br>
      文件夹里的 PDF 没有题名、作者等信息，需要 AI 从正文里读出来。推荐用
      <b>SiliconFlow（硅基流动）</b>，有免费模型、几分钟搞定；不配的话，入库的文献只会显示文件名。</div>
    <div class="wz-field"><label>API Key（SiliconFlow）</label>
      <input id="wz-meta-key" type="password" placeholder="去 https://cloud.siliconflow.cn/account/ak 领免费 key" /></div>
    <div class="wz-actions-inline">
      <button class="ghost2c" id="wz-meta-save">保存 Key</button>
      <span id="wz-meta-msg" class="wz-test-msg"></span>
    </div>
    <p class="wz-mini">也可以先跳过、之后在「⚙ 设置」里补，或让 agent 代为补全题录。</p>`;
  $("#wz-meta-save").addEventListener("click", async () => {
    const k = $("#wz-meta-key").value.trim();
    const msg = $("#wz-meta-msg");
    if (!k) { msg.className = "wz-test-msg err"; msg.textContent = "请先填 Key"; return; }
    try {
      // 只存 key，不改检索后端：backend 透传当前值，避免把本地检索误切成 API
      await jpost("/setup/backend", { backend: WZ.backend, key: k });
      WZ.api.key = k; if (WZ.detect) WZ.detect.meta_ready = true; APP.metaReady = true;
      renderMetaDep();  // 重渲染成「已就绪」绿条
    } catch (e) { msg.className = "wz-test-msg err"; msg.textContent = "保存失败：" + esc(e.message); }
  });
}
```

在 `renderStep3Folder()` 末尾调用 `renderMetaDep()`。

> **依赖讲清的关键**：`/setup/backend {backend: WZ.backend, key}` 会把 key 存进 `settings.api.key`（`server.py:159`）而**不改 backend**（`setup_backend` 用 `q.backend` 决定后端，`server.py:156`），因此本地检索 + 已存抽题录 key 可以共存。**⚠ 需与后端 agent 确认这条复用路径**（见 §9 风险 R3）——若后端更希望用独立的 meta key 字段，把此处 endpoint 换成约定的即可，前端结构不变。

### 4.5 第 4 / 5 步：文件夹模式适配
- `renderStep4`（准备引擎，`app.js:1262`）：**逻辑不变**（本地模式查模型、API 模式直接就绪）。检索引擎与来源正交。
- `renderStep5`（即时索引，`app.js:1371`）：文案按来源分叉。文件夹模式下 `WZ.folderConnected.entries` 就是待索引 PDF 数；若为 0（空文件夹），把「开始即时索引」换成「进入知识库 →」并附一句「文件夹还是空的，进去后把 PDF 拖进窗口即可自动入库」。`POST /index/light` 后端已按 `settings.source` 决定读 Zotero 还是 folder（**【后端约定】**：`index_light.get_papers()` 分派，`index_light.py:25-31`），前端无需改调用。

---

## 5. 前端交互与状态 —— 条件隐藏 Zotero

### 5.1 拿到 source（前置）
浏览可能在 detect 返回前就被点开，需一个 `ensureSource()`：

```js
async function ensureSource() {
  if (APP.srcLoaded) return APP.source;
  try {
    const d = await jget("/setup/detect");
    APP.source = d.source === "folder" ? "folder" : "zotero";
    APP.folderDir = d.folder_dir || ""; APP.metaReady = !!d.meta_ready;
  } catch (e) { /* 保守留 zotero */ }
  APP.srcLoaded = true; return APP.source;
}
```

`maybeWizard()`（`app.js:1448`）里既然已调 detect，顺手写入 `APP.*` 并置 `srcLoaded=true`，`ensureSource()` 直接命中缓存。

### 5.2 隐藏 Zotero 分类区（两种落法，取决于 F10 是否先落）

**给 Zotero 专属 DOM 加一个可切换容器。** 目标结构（文件夹模式左树只剩：AI/知识库分类 + 「全部文献」）。

**落法 A（若 F10-F11 尚未落，改现结构 `index.html:64-73`）**：把「📁收藏夹」头 + `#bt-tree` 包进一个可隐藏容器，`#bt-all` 提到中性位置：

```html
<div id="bt-topics" class="bt-topics">…</div>
<div id="bt-all" class="bt-all active">📄 全部文献<span class="bt-cnt" id="bt-all-cnt"></span></div>
<div id="bt-zotero-sec">                          <!-- 新增包裹：文件夹模式隐藏 -->
  <div class="bt-head">📁 Zotero 分类</div>
  <div id="bt-tree" class="bt-tree"><div class="bt-loading">加载中…</div></div>
</div>
```

**落法 B（若 F10-F11 已落，`设计-分类体系-F10-F11.md:550-566` 的结构）**：把那份 DOM 里「📚 Zotero 分类」头 + `#bt-tree`（+ `#bt-all` 若归在该区则拆出）一并包进 `#bt-zotero-sec`。两种落法下游代码一致。

**`loadBrowse()`（`app.js:527`）改造**：

```js
async function loadBrowse() {
  const src = await ensureSource();
  loadTopics();                                    // AI/知识库分类：两模式都要
  const zsec = $("#bt-zotero-sec");
  if (src === "folder") {
    if (zsec) zsec.hidden = true;                  // 整块隐藏 Zotero 分类
    $("#bt-all").firstChild.nodeValue = "📄 全部文献";  // 措辞去 Zotero 化（可选）
    loadPapers();                                  // 跳过 /categories，不请求收藏夹树
    return;
  }
  if (zsec) zsec.hidden = false;
  try {
    const d = await jget("/categories");
    $("#bt-all-cnt").textContent = d.n_collections != null ? (num(d.n_collections)+" 夹") : "";
    const box = $("#bt-tree"); box.innerHTML = "";
    (d.tree||[]).forEach(n => box.appendChild(treeNodeEl(n, 0)));
    if (!(d.tree||[]).length) box.innerHTML = `<div class="bt-loading">（无收藏夹）</div>`;
  } catch (e) { $("#bt-tree").innerHTML = `<div class="bt-loading">收藏夹加载失败：${esc(e.message)}</div>`; }
  loadPapers();
}
```

文件夹模式下不调 `/categories`，避免「加载收藏夹中…」空转与后端空树往返。`BR.scope`/`BR.collection` 保持默认「全部」，`loadPapers()` 不带 collection 参数 → 返回全库，行为正确。

### 5.3 其它 Zotero 字样的文件夹替代
统一在启动后据 `APP.source` 打补丁（一个 `applySourceCopy()`，在 `ensureSource()` 后调用一次）：

| 位置 | Zotero 模式（不变） | 文件夹模式替代 |
|---|---|---|
| 向导副标题 `index.html:252` | 把你的 Zotero 文库变成… | 把一个文件夹里的 PDF 变成能秒级检索、可视化、可对话的本地知识库。 |
| 更新弹窗标题/说明 `index.html:235-236` | 重新读取 Zotero 库… | 重新扫描知识库文件夹，只处理新加入的 PDF，已入库的跳过。 |
| 更新按钮 tooltip `index.html:19` | 加了新文献后点这里增量更新 | 加了新 PDF 后点这里增量更新（或直接把 PDF 拖进窗口） |
| 浏览左树头 | 📁 Zotero 分类 | （整块隐藏） |
| F36「仅导入有 PDF」勾选（若已落） | 向导第 3 步 Zotero 分支的勾选 | 文件夹模式**不出现**（文件夹里本就全是 PDF，`has_pdf` 恒真） |

`applySourceCopy()` 用 `if (APP.source==="folder")` 逐个改 `textContent`/`title`。这些是纯文案替换，S 级。

### 5.4 待确认标（可选增强）
文件夹模式记录带 `needs_review:true`。在 `paperCard`（`app.js:635` 附近）可加一枚 `📝 待确认` 小标，点击进入编辑题录浮层（**编辑题录是另一独立功能，本篇不展开，留接口位**）。一期可先不做，仅在无 Key 时用 §6 的空态提示覆盖。

---

## 6. 前端交互与状态 —— 拖入自动入库

### 6.1 整窗拖拽（仅文件夹模式启用）
在 init 末尾（`ensureSource()` 之后）挂全局监听，`APP.source!=="folder"` 时全部 no-op：

```js
function initDragIngest() {
  let dragDepth = 0;
  const overlay = $("#drop-overlay");
  const isFileDrag = (e) => Array.from(e.dataTransfer?.types||[]).includes("Files");
  window.addEventListener("dragenter", (e) => {
    if (APP.source !== "folder" || !isFileDrag(e)) return;
    e.preventDefault(); dragDepth++; overlay.hidden = false;
  });
  window.addEventListener("dragover", (e) => {
    if (APP.source !== "folder" || !isFileDrag(e)) return;
    e.preventDefault(); e.dataTransfer.dropEffect = "copy";   // 必须 preventDefault，否则浏览器会打开该文件
  });
  window.addEventListener("dragleave", (e) => {
    if (APP.source !== "folder") return;
    if (--dragDepth <= 0) { dragDepth = 0; overlay.hidden = true; }
  });
  window.addEventListener("drop", (e) => {
    if (APP.source !== "folder") return;
    e.preventDefault(); dragDepth = 0; overlay.hidden = true;
    const files = Array.from(e.dataTransfer?.files || []).filter(f => /\.pdf$/i.test(f.name));
    if (files.length) ingestFiles(files);
    else toast("只支持拖入 PDF 文件");
  });
}
```

`#drop-overlay`（`index.html` 新增，默认 `hidden`）：全屏半透明遮罩 + 居中提示「松开即可入库」。

### 6.2 上传（content 上传，不依赖文件路径）
**关键决策**：走 **multipart/form-data 上传文件内容**，不依赖 `File.path`。WebView2/Chromium 出于安全**不暴露拖入文件的绝对路径**（`File.path` 是 Electron 私有属性，标准 WebView2 无此字段），内容上传是唯一稳的路子，且与「文件本就要复制进受管文件夹」的语义一致。

```js
async function ingestFiles(fileList) {
  showIngestPanel(fileList.length);                 // 进度面板（§6.3）
  const fd = new FormData();
  fileList.forEach(f => fd.append("files", f, f.name));
  try {
    // 首选：SSE 流式进度（复用 downloadModels 的读帧逻辑 app.js:1344-1364）
    const resp = await fetch("/ingest/files", { method: "POST", body: fd });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    if ((resp.headers.get("content-type")||"").includes("event-stream")) {
      await readIngestSSE(resp);                     // 逐帧更新「已入库 k/N，当前：xxx.pdf」
    } else {
      const r = await resp.json();                   // 兜底：同步返回 {added, failed}
      finishIngest(r.added || 0, r.failed || []);
    }
  } catch (e) { ingestError(e.message); }
}
```

**【后端约定】** `POST /ingest/files`（multipart，字段名 `files`，可多个）：逐个把内容写入 `settings.folder_dir` → 跑该批 ingest 管线（extract 首 1-2 页 → `folder_meta.extract_meta` → 追加 `papers.jsonl` + 更新词法索引；有 PDF 可后续深索）→ **推荐以 SSE 逐篇推进度**，帧形如 `{done:k, total:N, name:"xxx.pdf", ok:true}`，末帧 `{final:true, added, failed:[{name,reason}]}`；不便流式则同步返回 `{added, failed:[...]}`。前端两种都兼容。**注意后端单构建锁**（`server.py` `BUILD["running"]`，见 `设计-分类体系-F10-F11.md:49`）——入库与深索/更新互斥，需后端排队；前端在锁忙时收到 4xx/错误帧要提示「知识库正忙，请稍后再拖」。

### 6.3 进度面板
复用「更新弹窗」样式风格，新增 `#ingest-modal`（`index.html`）：

```html
<div id="ingest-modal" class="modal" hidden>
  <div class="modal-box">
    <h3>正在入库</h3>
    <p class="hint" id="ingest-sub">正在把 PDF 复制进知识库并用 AI 抽取题录…</p>
    <div class="hbar"><span class="track"><span id="ingest-fill" class="fill" style="width:0%"></span></span></div>
    <div id="ingest-detail" class="hint"></div>
    <div id="ingest-result"></div>
    <div class="modal-actions"><button id="ingest-close" class="ghost" hidden>完成</button></div>
  </div>
</div>
```

- `showIngestPanel(n)`：显示，`#ingest-sub` = `正在入库 ${n} 篇（抽取元数据中…）`，进度条 0%，关闭按钮隐藏。
- `readIngestSSE`：每帧更新 `#ingest-fill` 宽度 `k/N*100%`、`#ingest-detail` = `已完成 k/N · 当前：name`。
- **无 Key 场景**：若 `!APP.metaReady`，`#ingest-sub` 改为醒目提示（§6.5），仍会入库但只有文件名。
- `finishIngest(added, failed)`：`#ingest-result` 显示「✅ 新入库 added 篇」+（failed.length 时列出失败文件与原因）；显示「完成」按钮；**收尾刷新**：

```js
function finishIngest(added, failed) {
  // …渲染结果…
  browseLoaded = false; if (!$("#panel-browse").hidden) loadBrowse();  // 浏览可见则刷新
  if (dashLoaded) loadDashboard("silent");                            // 库总览静默刷新
  poll();                                                             // 顶栏篇数/进度即时更新
  if (added > 0 && !lastIdxStatus?.mode) jpost("/index/light", {}).catch(()=>{}); // 首批入库补建词法层
}
```

### 6.4 空库 dropzone（显眼引导）
文件夹模式且库为空（`/health` 的 `papers===0` 或 detect `!indexed`）时，在检索面板和浏览列表给一个大号 dropzone。放在检索面板 `#results` 上方 or 浏览 `#bl-list`：

```html
<div id="empty-dropzone" class="empty-dropzone" hidden>
  <div class="ed-ic">📥</div>
  <div class="ed-h">把 PDF 拖到这里，自动入库</div>
  <div class="ed-d">系统会把文件复制进你的知识库文件夹，并用 AI 读出题名、作者、年份等信息。</div>
  <button id="ed-pick" class="primary">或点击选择 PDF…</button>
  <input id="ed-file" type="file" accept="application/pdf" multiple hidden />
</div>
```

**文件选择器兜底（务必有）**：`#ed-pick` → `#ed-file.click()`；`#ed-file` 的 `change` → `ingestFiles([...e.target.files])`。**`<input type=file>` 在 WebView2 里必定能弹原生对话框**，是拖拽不生效时的可靠退路（见 §9 R1）。dropzone 本身也接 `drop`（同 §6.1）。

显隐：`ensureSource()` + `/health` 后，`APP.source==="folder" && papers===0` → 显示；入库出现 ≥1 篇后隐藏。

### 6.5 无 Key 引导（贯穿）
文件夹模式 `!APP.metaReady` 时，三处显式提示 + 一键跳转：

1. **入库时**（`#ingest-sub`）：`⚠️ 未配置 AI Key，本次只能按文件名入库。配置后可自动补全题名/作者/年份 →`，尾链 `openMetaKeyHelp()`。
2. **空库 dropzone**（`#empty-dropzone` 内追加一条 amber）：`还没配 AI Key —— 现在入库只有文件名。[去配置]`。
3. **浏览列表顶部**（`#bl-tip` 复用）：文件夹模式且检测到「多数文献 title==文件名」时挂一条「这些文献还没抽到题录，[配 API / 用 agent 补全] →」。

`openMetaKeyHelp()`：直接复用现有 `openSettings()`（`app.js:839`）并滚动到「🔎 检索引擎」区（那里就是填 SiliconFlow key 的地方 `index.html:129-148`，`folder_meta` 复用同一把 key）；同时在设置里加一句面向文件夹用户的说明（§7）。「用 agent 补全」指向对话页 —— agent 可调 MCP 工具批量补题录（本篇不实现 agent 侧，仅文案引导，与 F11「按分类对话/agent」呼应）。

---

## 7. 文案（成稿，面向非 Zotero、非工程师用户）

**向导第 3 步 · 来源选择**
- 卡 A 标题：`🔗 连接 Zotero`；副：`直接读取 Zotero 里每一条文献（含题录和收藏夹分类），不会改动你的 Zotero 数据。`
- 卡 B 标题：`📁 文件夹模式`；副：`指定一个文件夹放 PDF，系统用 AI 自动读出题名、作者、年份、期刊等信息。适合没装 Zotero、手上就是一堆 PDF 的你。`

**文件夹分支 · 选择框**
- 标签：`知识库文件夹（把 PDF 放进这里；可以先建一个空文件夹，之后再拖 PDF 进来）`
- 建立成功：`✅ 已建立文件夹库，发现 N 个 PDF`；空文件夹：`✅ 已建立文件夹库。文件夹还是空的也没关系，进去后把 PDF 拖进窗口就会自动入库。`

**抽题录依赖 · 已就绪**
> 🤖 题录抽取已就绪：入库时会用你上一步配置的 API Key，自动从 PDF 正文读出题名 / 作者 / 年份 / 期刊 / 摘要。

**抽题录依赖 · 需配 Key**
> ⚠️ 还差一步：配一个 AI 的 API Key
> 文件夹里的 PDF 没有题名、作者这些信息，需要 AI 从正文里读出来。推荐用 SiliconFlow（硅基流动），有免费模型、几分钟就能配好。不配也能入库，但文献只会显示文件名，检索和分类会大打折扣。

**拖入遮罩**：`松开鼠标，把 PDF 加入知识库`
**入库进行中**：`正在入库 N 篇（抽取元数据中…）` / 明细 `已完成 k/N · 当前：<文件名>`
**入库完成**：`✅ 新入库 N 篇` / 失败 `⚠️ M 篇未能入库：<文件名>（原因）`
**空库 dropzone**：主 `把 PDF 拖到这里，自动入库`；副 `系统会把文件复制进你的知识库文件夹，并用 AI 读出题名、作者、年份等信息。`；按钮 `或点击选择 PDF…`
**无 Key（入库时）**：`⚠️ 还没配 AI Key，这次只能按文件名入库。配好之后，新入库的文献会自动补全题名 / 作者 / 年份 —— 点这里去配置`
**更新弹窗（文件夹模式）**：标题 `更新知识库（增量）`；说明 `重新扫描你的知识库文件夹，只处理新加入的 PDF，已入库的会跳过。（其实你也可以直接把 PDF 拖进窗口，即时入库。）`
**设置页 · 检索引擎区补一句（仅文件夹模式显示）**：`📁 文件夹模式下，这把 Key 还会用来从 PDF 正文自动抽取题名/作者/年份等题录信息。`

---

## 8. 迁移 / 兼容 / 回归

- **默认零影响**：`APP.source` 未加载时保守当 `zotero`；detect 未返回 `source` 时也回落 `zotero`。现有 Zotero 用户所有路径（左树、更新、向导）行为不变。
- **拖拽仅文件夹模式激活**：§6.1 全局监听首行 `APP.source!=="folder"` 即 no-op，Zotero 模式不会误接文件、不会阻止默认行为。
- **detect 契约**：前端强依赖 detect 返回 `source`（现 `server.py:117` 返回的是 manifest source，需改为 settings 的 `source`，并新增 `folder_dir`、`meta_ready`）—— **上线前必须与后端 agent 对齐这三字段**，否则前端一律走 Zotero 分支（隐藏不生效、拖拽不激活）。
- **与 F10-F11 epic 的顺序**：两者都改左树。约定：
  - 若 F10 先落，本篇按 §5.2 落法 B，把「📚 Zotero 分类」头 + `#bt-tree` 包进 `#bt-zotero-sec`；`BR.scope` 结构下 folder 模式默认 `scope.type="all"`，正常。
  - 若本篇先落，按落法 A，F10 落地时把新「Zotero 分类」头并入同一 `#bt-zotero-sec` 即可，隐藏逻辑不动。
  - 二者互不阻塞，但**改到同一段 DOM/`loadBrowse`，需串行合并、避免各写一半**。
- **回归清单**：Zotero 模式向导五步跑通；文件夹模式向导跑通（含有/无 Key 两条）；文件夹模式左树无 Zotero 区、`/categories` 不被请求；拖入单/多 PDF、含非 PDF 混拖、空 Key 入库、失败文件展示；入库后顶栏篇数、库总览、浏览三处刷新；浏览器回退模式（无 pywebview）下「浏览…」按钮隐藏、文件选择器可用。

---

## 9. 分步实现清单（S<1h / M 半天 / L 一天+）

**前置（跨 agent 对齐，阻塞项）**
- [ ] **P1【后端】/setup/detect 返回 `source`/`folder_dir`/`meta_ready`**（S，`server.py:99`）—— 前端全部分支依赖。
- [ ] **P2【后端】/setup/connect 支持 `{source:"folder",folder_dir}`**（S–M）。
- [ ] **P3【后端】POST /ingest/files（multipart，建议 SSE 进度）**（M–L）。
- [ ] **P4【后端】POST /setup/pick_folder（pywebview 原生文件夹对话框）**（S，可选，无则文本输入兜底）。
- [ ] **P5【后端】settings 新增 source/folder_dir、index_light 分派 folder_source**（M，另一 agent 主责）。

**A 向导文件夹分支**
- [ ] A1 `renderStep1` 未探到 Zotero 时补中性引导句（S，无依赖）。
- [ ] A2 `renderStep3` 拆来源选择器 + `renderStep3Zotero`（搬原逻辑）+ `renderStep3Folder`（M，依赖 P2）。
- [ ] A3 `renderMetaDep()` 三态 + 就地存 Key（M，依赖 P1 的 meta_ready；存 key 走 `/setup/backend`，依 §4.4 与后端确认 R3）。
- [ ] A4 「浏览…」按钮 + pywebview 判定回退（S，依赖 P4）。
- [ ] A5 `renderStep5` 文案按来源分叉、空文件夹处理（S，依赖 A2）。

**B 条件隐藏 Zotero**
- [ ] B1 `APP` 全局 + `ensureSource()` + `maybeWizard` 写缓存（S，依赖 P1）。
- [ ] B2 左树 DOM 包 `#bt-zotero-sec`（S，与 F10 协调 §8）。
- [ ] B3 `loadBrowse` 按 source 隐藏 + 跳过 `/categories`（S，依赖 B1/B2）。
- [ ] B4 `applySourceCopy()` 文案替换（向导副标题/更新弹窗/tooltip）（S）。

**C 拖入入库**
- [ ] C1 `#drop-overlay` + `#ingest-modal` + `#empty-dropzone` DOM + 样式（M）。
- [ ] C2 `initDragIngest()` 整窗拖拽（仅 folder）（M，依赖 B1）。
- [ ] C3 `ingestFiles` + `readIngestSSE`（复用 `app.js:1344-1364`）+ `finishIngest` 刷新（M，依赖 P3）。
- [ ] C4 文件选择器兜底（`#ed-file`）+ 空库 dropzone 显隐（S，依赖 C1）。
- [ ] C5 无 Key 引导三处 + `openMetaKeyHelp()`（S，依赖 B1、`openSettings` `app.js:839`）。

**验证**
- [ ] V1 实机（pywebview/WebView2）跑拖拽 + 文件选择器（S，依赖 R1 结论）。

---

## 10. 风险与未决点（诚实）

**R1 · pywebview/WebView2 拖入文件的可行性（最大不确定）**
HTML5 `drop` 在 WebView2 里**不保证**默认可用：部分 WebView2 版本会把拖入文件当导航（直接在窗口打开 PDF），需靠 `dragover`+`drop` 都 `preventDefault()` 拦截；个别环境甚至不触发 JS `drop`。且 Chromium **不暴露文件绝对路径**（无 `File.path`）。
- **应对（已内建）**：(a) 上传**内容**而非路径（§6.2），绕开路径问题；(b) `<input type=file>` 文件选择器作可靠兜底（§6.4），WebView2 必定弹原生框；(c) `preventDefault` 拦默认打开。
- **核实方法**：实机启动 app，拖一个 PDF 上窗口，看 (1) 是否被浏览器打开（若是→确认 `preventDefault` 已在 window 级生效）、(2) `drop` 事件是否触发、`e.dataTransfer.files.length` 是否 >0。命令：正常启动后在对话/检索处临时 `console.log`，或用现有 `reportErr` 打点到 `logs/errors.log`。若 `drop` 完全不触发，则**降级为仅文件选择器 + 更新弹窗入库**，功能不残缺、只少「拖」这一手势。
- **pywebview 原生 drop**：新版 pywebview 有实验性拖放/`window.events`，但各 GUI 后端（EdgeChromium/CEF/GTK）支持不齐，不作主路径；如实机确认可用可作增强。

**R2 · 拖入 vs 后端单构建锁**
后端 `BUILD["running"]` 全局单锁（`设计-分类体系-F10-F11.md:49`）。用户在深索/更新进行时拖 PDF，或一次拖很多批，会撞锁。前端已在错误帧提示「知识库正忙」，但**根治要后端排队**（P3 内做队列）。未定：批量上传的体量上限（一次拖 200 个 PDF 的内存/超时）——建议后端分批、前端限每批（如 50）并串行提交。**核实**：与后端 agent 敲定 `/ingest/files` 是否内部排队、单请求文件数上限。

**R3 · 抽题录 Key 的存放位置**
本篇按「复用 `settings.api.key`（`folder_meta` 走 `sac._conf` 逻辑 `sac.py:46-59`）」设计，用 `/setup/backend {backend:当前, key}` 存（`server.py:152` 不改 backend 只写 key）。**风险**：若后端 agent 为文件夹模式设计了独立 meta key 字段，或 `folder_meta` 实际读的不是 `api.key`，此路径失效。**核实方法**：与后端确认 `folder_meta.extract_meta` 读哪个 key；据结论把 §4.4 的存 key endpoint 换成约定值（前端结构不变，仅一行 URL/字段）。

**R4 · detect 字段契约**
前端全部条件渲染押在 detect 的 `source` 上。若后端未按约定返回，隐藏/拖拽全部静默失效（回落 Zotero）。属**硬前置**，P1 未完成前 A/B/C 都无法验收。

**R5 · 空库判定的口径**
「空库」用 `/health.papers===0` 还是 detect `!indexed`？二者在「已 connect folder 但还没 index/light」时可能不一致。**建议**：以 `/health.papers` 为准（0 即显 dropzone），避免 connect 后、索引前的空窗期漏显。实现时统一，勿两处各判。

---

关键文件与锚点（前端改动集中处）：
- `D:\Onedrive\AI\知识库应用\LocalKB源码\web\app.js`：向导 `1068-1401`、`maybeWizard 1448`、左树 `435-539/669`、Tab `117-131`、SSE 范式 `1344-1364`、`openSettings 839`。
- `D:\Onedrive\AI\知识库应用\LocalKB源码\web\index.html`：向导 `245-264`、左树 `64-73`、更新弹窗 `233-243`、设置检索引擎区 `129-148`、顶栏 `10-22`。
- 需新增 DOM：`#drop-overlay` / `#ingest-modal` / `#empty-dropzone` / `#bt-zotero-sec` 包裹（`index.html`），配套 `style.css`。
- 跨 agent 契约点：`server.py:99`（detect）、`131`（connect）、`152`（backend 存 key）、新增 `/ingest/files`、可选 `/setup/pick_folder`。
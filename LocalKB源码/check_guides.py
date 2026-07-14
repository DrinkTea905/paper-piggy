# -*- coding: utf-8 -*-
"""指引 ↔ 代码 一致性校验器（**DEV_ONLY：不进分发包**，见 build_bundle.DEV_ONLY）。

背景：本项目有三套「不会自动跟着代码走」的指引（用户指引 / agent 指引 / 开发者文档），
历史上已经漂过一次——`MCP接入说明.md` 写 28 个工具而代码里有 32 个。靠人肉纪律已被证明失败，
所以把**机器能判定的部分**全部断言掉，塞进 build_bundle 的开头，校验不过就中止打包。
设计见 docs/MAINTENANCE.md §2.3。

本脚本**纯只读**，不改任何文件。任一检查失败 → 退出码 1。文件缺失等情况记「跳过」，不算失败。

用法：  python check_guides.py
"""
import io, re, sys, contextlib
from pathlib import Path

SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))

# Windows 控制台默认 cp936 编码不下 ✅/❌（U+2705 等），不 reconfigure 会直接 UnicodeEncodeError。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

FAILED = []


def ok(name, detail=""):
    print(f"✅ {name}" + (f" —— {detail}" if detail else ""))


def bad(name, detail):
    print(f"❌ {name} —— {detail}")
    FAILED.append(name)


def skip(name, why):
    print(f"⏭  跳过 {name} —— {why}")


# ── ① MCP 工具表是否过期（复用 gen_mcp_doc 的 --check，它是工具表的单一事实源）──────────
def check_tools_table():
    name = "① MCP 工具表与 mcp_server.TOOLS 一致"
    try:
        import gen_mcp_doc
    except Exception as e:
        return skip(name, f"import gen_mcp_doc 失败：{e}")
    buf, argv = io.StringIO(), sys.argv[:]
    sys.argv = ["gen_mcp_doc.py", "--check"]   # --check 模式只比对、不写文件
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = gen_mcp_doc.main()
    finally:
        sys.argv = argv
    msg = " ".join(buf.getvalue().split())
    ok(name, msg) if rc == 0 else bad(name, msg + "（跑 `python gen_mcp_doc.py` 修）")


# ── ② Resources / Prompts 表：gen_mcp_doc 只管 TOOLS，这两张表是手改的 ─────────────────
#    localkb://memory 当年就是这么漏掉的——所以这里做双向集合比对（漏了 / 多了都报）。
def _canon(u):
    """`localkb://page/{id}` 与文档里的 `localkb://page/<id>` 视为同一条。"""
    return re.sub(r"[{<][^}>]*[}>]", "*", u.strip())


def _doc_table_cells(txt, heading):
    """截出 `## <heading>…` 到下一个 `##` 之间的表格，返回每行第一列反引号里的内容。"""
    m = re.search(r"^##\s*" + heading + r".*?$(.*?)(?=^##\s|\Z)", txt, re.S | re.M)
    return None if not m else re.findall(r"^\|\s*`([^`]+)`", m.group(1), re.M)


def check_resources_prompts(doc_txt):
    import mcp_server as M
    # Resources：代码侧 = RESOURCES + RESOURCE_TEMPLATES（文档表里两者混在一张表）
    name = "② MCP接入说明.md 的 Resources 表与 RESOURCES/RESOURCE_TEMPLATES 一致"
    cells = _doc_table_cells(doc_txt, "Resources")
    code = {_canon(r["uri"]) for r in M.RESOURCES} | \
           {_canon(t["uriTemplate"]) for t in getattr(M, "RESOURCE_TEMPLATES", [])}
    if cells is None:
        skip(name, "文档里找不到 `## Resources` 段")
    else:
        doc = {_canon(c) for c in cells}
        miss, extra = sorted(code - doc), sorted(doc - code)
        if miss or extra:
            bad(name, f"文档 {len(cells)} 行 / 代码 {len(code)} 条；"
                      f"文档缺：{miss or '无'}；文档多余：{extra or '无'}（手改文档那张表）")
        else:
            ok(name, f"{len(code)} 条")

    # Prompts：文档单元格形如 `/ingest-source key=<论文key>`，取斜杠后的命令名
    name = "② MCP接入说明.md 的 Prompts 表与 PROMPTS 一致"
    cells = _doc_table_cells(doc_txt, "Prompts")
    if cells is None:
        return skip(name, "文档里找不到 `## Prompts` 段")
    doc = {c.strip().lstrip("/").split()[0] for c in cells if c.strip()}
    code = {p["name"] for p in M.PROMPTS}
    miss, extra = sorted(code - doc), sorted(doc - code)
    if miss or extra:
        bad(name, f"文档 {len(cells)} 行 / 代码 {len(code)} 条；文档缺：{miss or '无'}；文档多余：{extra or '无'}")
    else:
        ok(name, f"{len(code)} 条")


# ── ③ 内置工作流：_WF_* 常量数 == ensure_scaffold 落盘数 == _SKILLS_README 列表 == 首页卡片数 ──
_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def check_workflows():
    name = "③ 内置工作流数量：_WF_* == ensure_scaffold == _SKILLS_README == index.html 第3章卡片"
    ws = SRC / "agent_ws.py"
    html_p = SRC / "web" / "index.html"
    if not ws.exists() or not html_p.exists():
        return skip(name, "agent_ws.py 或 web/index.html 不存在")
    src = ws.read_text(encoding="utf-8")
    consts = re.findall(r"^(_WF_[A-Z_]+)\s*=", src, re.M)                       # 常量定义
    files = re.findall(r'skills_dir\(\)\s*/\s*"([^"]+\.md)"\s*,\s*_WF_[A-Z_]+', src)  # 真正落盘的
    import agent_ws
    sec = re.search(r"^##\s*现有工作流(.*?)(?=^##\s|\Z)", agent_ws._SKILLS_README, re.S | re.M)
    listed = re.findall(r"`([^`]+\.md)`", sec.group(1)) if sec else []          # 说明.md 里列出的

    html = html_p.read_text(encoding="utf-8")
    m = re.search(r"<!--\s*第\s*3\s*章.*?-->(.*?)<!--\s*第\s*4\s*章", html, re.S)
    if not m:
        return skip(name, "index.html 里找不到「第 3 章 … 第 4 章」注释锚点")
    ch3 = m.group(1)
    cards = len(re.findall(r'class="ag-trio-i"', ch3))
    mn = re.search(r"([一二三四五六七八九十两\d]+)\s*条开箱即用的工作流", ch3)
    n_txt = (_CN_NUM.get(mn.group(1)) or int(mn.group(1))) if (mn and (mn.group(1) in _CN_NUM or mn.group(1).isdigit())) else None

    n = len(consts)
    errs = []
    if len(files) != n:
        errs.append(f"ensure_scaffold 只落盘 {len(files)} 个（常量 {n} 个）")
    if sorted(listed) != sorted(files):
        errs.append(f"_SKILLS_README 列的是 {listed}，实际落盘 {files}")
    if cards != n:
        errs.append(f"index.html 第3章有 {cards} 张工作流卡（应为 {n}）")
    if n_txt is not None and n_txt != n:
        errs.append(f"index.html 硬写着「{mn.group(1)}条开箱即用的工作流」（应为 {n}）")
    # 卡片标题应含文件名（去掉 .md）——防止「数量对了但改的不是同一条」
    for f in files:
        if f[:-3] not in ch3:
            errs.append(f"index.html 第3章没提到「{f[:-3]}」")
    bad(name, "；".join(errs)) if errs else ok(name, f"{n} 条：{', '.join(files)}")


# ── ④ WIKI_MD_SEED 的 "schema vN" 必须等于 SCHEMA_VERSION ────────────────────────────
#    忘了 bump 会**静默**让老库永远收到过期规约（不报错、不告警）——本项目最阴的坑。
def check_wiki_schema():
    name = "④ WIKI_MD_SEED 里的 schema vN == wiki_store.SCHEMA_VERSION"
    try:
        import wiki_store as W
    except Exception as e:
        return skip(name, f"import wiki_store 失败：{e}")
    m = re.search(r"schema\s+(v\d+)", W.WIKI_MD_SEED)
    if not m:
        return bad(name, "WIKI_MD_SEED 标题里找不到 `schema vN` 字样")
    ok(name, f"都是 {m.group(1)}") if m.group(1) == W.SCHEMA_VERSION else \
        bad(name, f"种子写 {m.group(1)}，SCHEMA_VERSION 是 {W.SCHEMA_VERSION} —— 忘了同步会让老库永远收不到新规约")


# ── ⑤ 版本号只能有一处字面量：config.APP_VERSION ──────────────────────────────────────
#    收敛的写法（只匹配「名字里带 version 的东西 = 'X.Y.Z'」），宁可漏报也不误报。
_VER = re.compile(r"""(?i)\b([A-Za-z_][\w.]*version\w*)\s*[:=]\s*["'](v?\d+\.\d+(?:\.\d+)?)["']""")
_SKIP_DIRS = {"__pycache__", "data", "logs", "0_Agent交付物", "0_Agent资料库", "node_modules", ".git"}


def check_single_version():
    name = "⑤ 版本字面量只出现在 config.py（APP_VERSION 是唯一事实源）"
    hits = []
    for p in SRC.rglob("*"):
        if p.suffix.lower() not in (".py", ".js", ".html") or not p.is_file():
            continue
        if p.name in ("config.py", "check_guides.py") or _SKIP_DIRS & set(x.name for x in p.parents):
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            for var, ver in _VER.findall(line):
                hits.append(f"{p.relative_to(SRC).as_posix()}:{i} {var}={ver}")
    if hits:
        return bad(name, "别处也写死了版本号（应改读 config.APP_VERSION）：" + "；".join(hits[:8]))
    try:
        import config
        ok(name, f"config.APP_VERSION = {config.APP_VERSION}")
    except Exception:
        ok(name, "别处无版本字面量（config.APP_VERSION 未读到，另行确认）")


def main():
    print("=== 指引 ↔ 代码 一致性校验（只读）===")
    doc = SRC / "MCP接入说明.md"
    check_tools_table()
    if doc.exists():
        check_resources_prompts(doc.read_text(encoding="utf-8"))
    else:
        skip("② Resources/Prompts 表", "MCP接入说明.md 不存在")
    check_workflows()
    check_wiki_schema()
    check_single_version()
    print("-" * 60)
    if FAILED:
        print(f"❌ {len(FAILED)} 项不一致——改了功能忘了同步指引。逐条修完再打包。")
        return 1
    print("✅ 全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

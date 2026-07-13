# -*- coding: utf-8 -*-
"""
组装可分发 bundle（开发机跑）。产出 dist/LocalKB/：
  python/   内嵌 CPython + 瘦依赖（无 torch，pip 装好后就位）
  app/      本项目源码 + web/ + docs/（排除数据/日志/构建工具）
  models/   空（首启从云端下载；开发机自测可先 copy_slim_models 填入）
  data/     空（用户自建索引落这里；与 app/ 分离→自动更新不误删）
  run_localkb.py / 启动.bat / LocalKB.vbs  启动器（设 env→首启下模型→开原生窗口）
用法: python build_bundle.py            # 组装（假设 python/ 已 pip 装好）
      python build_bundle.py --slim-models  # 顺便把开发机模型的运行时文件 copy 进 models/（自测用）
"""
import sys, os, shutil, argparse, stat
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C

SRC = C.APP
BUNDLE = SRC / "dist" / "LocalKB"
APP_OUT = BUNDLE / "app"

# 仅开发/构建期用、终端用户不需要的 .py（排除出分发包）
DEV_ONLY = {
    "build_bundle.py", "pack_models.py", "setup_reranker_onnx.py", "setup_onnx.py",
    "import_fulltext.py", "fix_schema.py",
    "gen_mcp_doc.py",   # 从 mcp_server.TOOLS 生成文档工具表；只在开发机跑，不进分发包
    "fetch_mingit.py",  # 下载 MinGit 塞进包；构建期跑，不进分发包（它下载的 git/ 才进）
}
KEEP_MD = {"README.md", "MCP接入说明.md"}


def _rm_ro(func, path, _):
    os.chmod(path, stat.S_IWRITE); func(path)


def verify_manifest():
    """构建期护栏：分发版靠首启从云端下载模型，manifest 缺失、或 url 仍是占位符(<…>)，
    必然导致终端用户首启下载失败。此处提前报错，避免发出一个下载注定失败的包。
    （若用 --slim-models 把模型直接打进包，则无需下载，跳过此检查。）"""
    mm = SRC / "models_manifest.json"
    if not mm.exists():
        raise SystemExit(f"[bundle] ✗ 缺 {mm}：分发版首启需据此下载模型。"
                         f"先跑 pack_models.py 生成清单，并把 GitHub Release 真实直链填入各 urls。")
    import json as _json
    try:
        man = _json.loads(mm.read_text(encoding="utf-8"))
    except Exception as ex:
        raise SystemExit(f"[bundle] ✗ models_manifest.json 解析失败：{ex}")
    models = man.get("models") or []
    if not models:
        raise SystemExit("[bundle] ✗ models_manifest.json 未列出任何模型（models 为空）。")
    bad = []
    for e in models:
        cand = list(e.get("urls") or e.get("mirrors") or [])
        if e.get("url"):
            cand.append(e["url"])
        if not cand or any("<" in u for u in cand):
            bad.append(e.get("name", "?"))
    if bad:
        raise SystemExit("[bundle] ✗ models_manifest.json 仍含占位符 url（<…>）或缺 url："
                         + ", ".join(bad)
                         + "。请把真实下载直链填入后再构建（或用 --slim-models 把模型直接打进包）。")
    print("[bundle] models_manifest.json 校验通过（url 均为真实直链）")


def copy_app():
    if APP_OUT.exists():
        shutil.rmtree(APP_OUT, onerror=_rm_ro)
    APP_OUT.mkdir(parents=True)
    for p in SRC.glob("*.py"):
        if p.name in DEV_ONLY:
            continue
        shutil.copy2(p, APP_OUT / p.name)
    for p in SRC.glob("*.md"):
        if p.name in KEEP_MD:
            shutil.copy2(p, APP_OUT / p.name)
    # journal_grading = 期刊引用权重分级引擎（含 config/ 与 catalogs/ 目录数据）；必须整包带上，
    # 否则 retriever 里 import journal_grading 失败、权重回退旧离散档。
    # skills = Claude Code 技能包（EN-M7：localkb-paper 论文写作工作流），用户从包里复制到项目 .claude/skills/。
    for sub in ("web", "docs", "journal_grading", "skills"):
        s = SRC / sub
        if s.exists():
            shutil.copytree(s, APP_OUT / sub, ignore=shutil.ignore_patterns("__pycache__"))
    mm = SRC / "models_manifest.json"
    if mm.exists():
        shutil.copy2(mm, APP_OUT / "models_manifest.json")
    # 期刊分级种子数据（随源码分发；新机 data/ 无此文件时 journal_tiers.py 回退用它，避免分级退化默认档）
    jt = SRC / "journal_tiers.json"
    if jt.exists():
        shutil.copy2(jt, APP_OUT / "journal_tiers.json")
    # 清理 pycache
    for pc in APP_OUT.rglob("__pycache__"):
        shutil.rmtree(pc, onerror=_rm_ro)
    print(f"[bundle] app/ 就绪：{len(list(APP_OUT.glob('*.py')))} 个 .py + web/ + docs/ + skills/")


def _file_ver(path):
    """读 Windows 文件版本 (major, minor, build, rev)；失败返回 None。"""
    try:
        import ctypes
        from ctypes import wintypes
        size = ctypes.windll.version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        ctypes.windll.version.GetFileVersionInfoW(str(path), 0, size, buf)
        r = ctypes.c_void_p(); l = wintypes.UINT()
        ctypes.windll.version.VerQueryValueW(buf, "\\", ctypes.byref(r), ctypes.byref(l))
        ffi = ctypes.cast(r, ctypes.POINTER(ctypes.c_uint32 * 13)).contents
        ms, ls = ffi[2], ffi[3]
        return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except Exception:
        return None


def ensure_vc_runtime():
    """把 msvcp140*.dll 塞进 python/。python-build-standalone 只带 vcruntime140(_1).dll，
    不带 C++ STL 的 msvcp140.dll；而 onnxruntime(本地嵌入/重排) 需要它，且要 ≥14.40（VS2022），
    否则 import 时报 WinError 1114（DLL 初始化失败）。这里从构建机找一个足够新的 copy 进去，
    让分发包在没装 VC++ 运行库的机器上也能用本地模式。"""
    py = BUNDLE / "python"
    need = ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll")
    have = _file_ver(py / "msvcp140.dll")
    if have and have >= (14, 40, 0, 0):
        print(f"[bundle] VC++ 运行库已就位：msvcp140.dll {'.'.join(map(str,have))}")
        return
    # 候选来源：System32（若够新）+ WinSxS 里最新的一份
    import glob as _glob
    best = None  # (ver, dir)
    dirs = [r"C:\Windows\System32"] + _glob.glob(r"C:\Windows\WinSxS\amd64_microsoft-*")
    for d in dirs:
        f = os.path.join(d, "msvcp140.dll")
        if os.path.exists(f):
            v = _file_ver(f)
            if v and v >= (14, 40, 0, 0) and (best is None or v > best[0]):
                best = (v, d)
    if not best:
        print("[bundle] ⚠ 未在构建机找到 msvcp140.dll ≥14.40（本地嵌入模式在缺 VC++ 运行库的机器上会失败）。"
              "请装最新 VC++ 2015-2022 x64 运行库后重试，或手动放一份进 python/。")
        return
    ver, srcdir = best
    n = 0
    for name in need:
        src = os.path.join(srcdir, name)
        if os.path.exists(src):
            shutil.copy2(src, py / name); n += 1
    print(f"[bundle] VC++ 运行库补齐：从 {srcdir} copy {n} 个 msvcp140* (v{'.'.join(map(str,ver))}) → python/")


def ensure_git():
    """把 MinGit 放进 bundle/git/（综合层版本历史/回滚用）。已就位则跳过；
    缺了只提示、不阻断——wiki_vcs 会自动退回 .history 快照，功能不坏，只是看不了逐字 diff。"""
    exe = BUNDLE / "git" / "cmd" / "git.exe"
    if exe.exists():
        print(f"[bundle] MinGit 已就位：{exe}")
        return
    print("[bundle] ⚠ bundle/git/ 缺 MinGit（版本历史将退回快照模式）。"
          f"如需自带真 git，跑：python fetch_mingit.py --dest \"{BUNDLE}\"")


def slim_python():
    py = BUNDLE / "python"
    n = 0
    for pat in ("*.pdb",):
        for f in py.rglob(pat):
            try:
                f.unlink(); n += 1
            except Exception:
                pass
    for pc in py.rglob("__pycache__"):
        shutil.rmtree(pc, onerror=_rm_ro)
    print(f"[bundle] 瘦身 python/：删 {n} 个 .pdb + pycache")


def copy_slim_models():
    """自测用：把开发机模型的运行时文件 copy 进 bundle/models/（不含 fp32 大文件）。"""
    KEEP = ("model_quantized.onnx", "config.json", "ort_config.json",
            "sentencepiece.bpe.model", "special_tokens_map.json",
            "tokenizer.json", "tokenizer_config.json")
    dev_models = Path(r"D:\00Zotero知识库\rag\data\models")
    out = BUNDLE / "models"
    for name in ("bge-m3-onnx", "bge-reranker-v2-m3-onnx"):
        d = dev_models / name
        if not (d / "model_quantized.onnx").exists():
            print(f"  跳过 {name}"); continue
        (out / name).mkdir(parents=True, exist_ok=True)
        for fn in KEEP:
            if (d / fn).exists():
                shutil.copy2(d / fn, out / name / fn)
        print(f"  {name} 运行时文件已 copy")


RUN_LOCALKB = r'''# -*- coding: utf-8 -*-
"""LocalKB 分发版启动入口（由 python\\pythonw.exe 运行，无黑窗）。
数据与模型统一放 %LOCALAPPDATA%\\LocalKB：① 必可写（装到 Program Files 等只读位置也安全）；
② 自动更新替换 app/ 时不丢用户索引/模型。模型的下载在网页首启向导里完成（本地模式），
   API 模式无需下载。"""
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
appdata = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
# 便携模式：包目录放个 portable.txt 即把数据/模型放包内（供 U 盘等场景）
if (ROOT / "portable.txt").exists():
    HOME = ROOT
else:
    HOME = Path(os.environ.get("LOCALKB_HOME") or (Path(appdata) / "LocalKB"))
try:
    (HOME / "data").mkdir(parents=True, exist_ok=True)
    (HOME / "models").mkdir(parents=True, exist_ok=True)
except Exception:
    pass
os.environ.setdefault("LOCALKB_DATA", str(HOME / "data"))
# 模型优先用包内 models/（--slim-models 打进包/首启已下载都落这里），否则退回 HOME/models：
# 否则非便携首启会把 LOCALKB_MODELS 指向空的 HOME/models，忽略包内已带模型、误报“需下载”。
_bundled = ROOT / "models"
if (_bundled / "bge-m3-onnx" / "model_quantized.onnx").exists():
    os.environ.setdefault("LOCALKB_MODELS", str(_bundled))
else:
    os.environ.setdefault("LOCALKB_MODELS", str(HOME / "models"))
sys.path.insert(0, str(ROOT / "app"))

# server 无模型也能起（检索器空库优雅降级），模型下载/引擎选择交给网页向导。
import launcher
launcher.main()
'''

BAT = 'start "" "%~dp0python\\pythonw.exe" "%~dp0run_localkb.py"\r\n'

VBS = ('Set s = CreateObject("WScript.Shell")\r\n'
       'p = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\\"))\r\n'
       's.Run """" & p & "python\\pythonw.exe"" """ & p & "run_localkb.py""", 0, False\r\n')


def write_launchers():
    (BUNDLE / "run_localkb.py").write_text(RUN_LOCALKB, encoding="utf-8")
    (BUNDLE / "启动.bat").write_text(BAT, encoding="gbk")
    (BUNDLE / "LocalKB.vbs").write_text(VBS, encoding="gbk")
    print("[bundle] 启动器就绪：run_localkb.py / 启动.bat / LocalKB.vbs")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slim-models", action="store_true", help="自测：copy 开发机模型运行时文件进 models/")
    ap.add_argument("--sync-only", action="store_true",
                    help="只同步 app/ 源码（不碰 python/ 与 models/）——改代码后快速刷新已构建的 bundle")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if args.sync_only:
        if not BUNDLE.exists():
            print(f"[bundle] ✗ 找不到已构建的 bundle：{BUNDLE}（先完整构建/解压一次）"); return
        copy_app()
        print(f"[bundle] 已同步 app/（源码）→ {APP_OUT}"); return
    if not (BUNDLE / "python" / "python.exe").exists():
        print(f"[bundle] ✗ 缺 {BUNDLE/'python'}（先下 python-build-standalone 并 pip 装依赖）"); return
    if not args.slim_models:
        verify_manifest()   # 依赖首启下载模型的分发包：构建前先确保 manifest 有真实直链
    copy_app()
    (BUNDLE / "data").mkdir(exist_ok=True)
    (BUNDLE / "models").mkdir(exist_ok=True)
    ensure_vc_runtime()     # 补齐 onnxruntime 需要的 msvcp140*.dll（本地嵌入模式必需）
    ensure_git()            # 自带 MinGit（综合层版本历史/回滚用；缺了会退回快照，不阻断构建）
    slim_python()
    write_launchers()
    if args.slim_models:
        copy_slim_models()
    print(f"[bundle] 完成 → {BUNDLE}")


if __name__ == "__main__":
    main()

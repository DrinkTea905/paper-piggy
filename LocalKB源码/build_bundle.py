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
    "import_fulltext.py",
    "gen_mcp_doc.py",    # 从 mcp_server.TOOLS 生成文档工具表；只在开发机跑，不进分发包
    "check_guides.py",   # 指引↔代码一致性校验器；构建期跑（见 main()），不进分发包
    "fetch_mingit.py",   # 下载 MinGit 塞进包；构建期跑，不进分发包（它下载的 git/ 才进）
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
    # 注：早期这里还拷过一个 skills/（localkb-paper 技能包，要用户自己复制到 .claude/skills/）。
    # 已删除：它和 agent_ws 的内置工作流（应用自动写进「0_Agent资料库/技能」）是同一条流水线的
    # 两份事实源，只会打架。技能不再需要任何手动安装动作。
    # 注：这里曾经还拷 docs/（4 份实现设计稿 + 2 份 .docx）。设计稿已移到仓库根的 docs/，
    # 且终端用户拿到 Word 设计稿只会困惑 —— 分发包不再带。设计文档只活在仓库里。
    for sub in ("web", "journal_grading"):
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
    write_version_json()    # 必须在 pycache 清理之后：清单要对得上最终落盘的文件
    print(f"[bundle] app/ 就绪：{len(list(APP_OUT.glob('*.py')))} 个 .py + web/ + journal_grading/")


def write_version_json():
    """在 app/ 里写 version.json：版本 + 构建时间 + 每个文件的 sha256 清单。
    version 取 config.APP_VERSION（全项目唯一版本字面量，别在这里再写一个数字）。
    files 清单不是装饰：updater.local_modifications()（updater.py:166）靠它比对磁盘，
    找出「用户自己改过的文件」——开源明文分发，用户真会改代码，这些文件不能被自动更新静默覆盖。
    没有 version.json 时 updater 会保守地当作「谁都没改过」→ 直接覆盖 → 用户改动被抹掉。"""
    import json, hashlib, datetime
    files = {}
    for p in sorted(APP_OUT.rglob("*")):
        if p.is_dir() or p.name == "version.json":
            continue
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for blk in iter(lambda: f.read(1 << 20), b""):
                h.update(blk)
        files[p.relative_to(APP_OUT).as_posix()] = h.hexdigest()
    (APP_OUT / "version.json").write_text(json.dumps({
        "version": C.APP_VERSION,
        "built":   datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files":   files,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[bundle] version.json 就绪：v{C.APP_VERSION}，{len(files)} 个文件的 sha256")


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


def ensure_vc_runtime(allow_missing=False):
    """把 msvcp140*.dll 塞进 python/。python-build-standalone 只带 vcruntime140(_1).dll，
    不带 C++ STL 的 msvcp140.dll；而 onnxruntime(本地嵌入/重排) 需要它，且要 ≥14.40（VS2022），
    否则 import 时报 WinError 1114（DLL 初始化失败）。这里从构建机找一个足够新的 copy 进去，
    让分发包在没装 VC++ 运行库的机器上也能用本地模式。"""
    py = BUNDLE / "python"
    need = ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll")
    # ❗ 必须两个都在：onnxruntime.dll / onnxruntime_pybind11_state.pyd 的导入表**同时**依赖
    #    MSVCP140.dll 和 MSVCP140_1.dll。只检查前者会出现「构建时报 OK、干净机上照样 1114」。
    #    （site-packages 里的副本救不了：numpy/pyarrow 那几份被 delvewheel 改名成
    #     msvcp140-<hash>.dll，按名解析不到；sklearn 的那份名字对，但导入 onnxruntime 时
    #     根本不会先 import sklearn，且它同样不带 msvcp140_1.dll。）
    REQUIRED = ("msvcp140.dll", "msvcp140_1.dll")
    vers = {d: _file_ver(py / d) for d in REQUIRED}
    if all(v and v >= (14, 40, 0, 0) for v in vers.values()):
        shown = ", ".join(f"{d} {'.'.join(map(str, vers[d]))}" for d in REQUIRED)
        print(f"[bundle] VC++ 运行库已就位：{shown}")
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
        if allow_missing:
            print("[bundle] ⚠ 未找到 msvcp140.dll ≥14.40，但 --allow-missing-vcrt 已指定。\n"
                  "    这个包在没装 VC++ 运行库的机器上**只能跑 API 模式**，本地嵌入/重排会崩。")
            return
        raise SystemExit(
            "[bundle] ✗ 构建机上找不到 msvcp140.dll ≥14.40。\n"
            "    没有它，分发包在**没装 VC++ 2015-2022 的干净电脑**上，本地嵌入/重排 import 即 WinError 1114。\n"
            "    开发机能跑只是因为 System32 里正好有——这正是这个 bug 一直没被发现的原因。\n"
            "    修：装最新 VC++ 2015-2022 x64 运行库后重试，或手动拷一份进 python/。\n"
            "    （确实要出一个只跑 API 模式的包？加 --allow-missing-vcrt 跳过。）")
    ver, srcdir = best
    n = 0
    for name in need:
        src = os.path.join(srcdir, name)
        if os.path.exists(src):
            shutil.copy2(src, py / name); n += 1
    print(f"[bundle] VC++ 运行库补齐：从 {srcdir} copy {n} 个 msvcp140* (v{'.'.join(map(str,ver))}) → python/")

    # 补完再验一次：REQUIRED 两个都得在，且都 ≥14.40。别信"copy 了 n 个"就当成功。
    after = {d: _file_ver(py / d) for d in REQUIRED}
    missing = [d for d, v in after.items() if not (v and v >= (14, 40, 0, 0))]
    if missing:
        raise SystemExit(f"[bundle] ✗ 补齐后仍缺/过旧：{', '.join(missing)}（需 ≥14.40）")


def ensure_git():
    """把 MinGit 放进 bundle/git/（综合层版本历史/回滚用）。

    优先从 <仓库根>/build/assets/MinGit 拷（构建资产，不在 git 里）。
    缺了只提示、不阻断——wiki_vcs 会自动退回 .history 快照，功能不坏，只是看不了逐字 diff。"""
    exe = BUNDLE / "git" / "cmd" / "git.exe"
    if exe.exists():
        print(f"[bundle] MinGit 已就位：{exe}")
        return

    src = SRC.parent / "build" / "assets" / "MinGit"
    if (src / "cmd" / "git.exe").exists():
        print(f"[bundle] 拷贝 MinGit：{src} → bundle/git/（约 90MB）")
        shutil.copytree(src, BUNDLE / "git", dirs_exist_ok=True)
        print("[bundle] MinGit 就绪")
        return

    print("[bundle] ⚠ 没找到 MinGit（版本历史将退回 .history 快照模式，功能不坏）。"
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


def copy_slim_models(src_dir=None):
    """自测用：把开发机模型的运行时文件 copy 进 bundle/models/（不含 fp32 大文件）。
    模型母本目录取 --models-dir，其次环境变量 LOCALKB_MODELS，最后 C.MODELS。
    （曾经这里硬编码某台开发机的绝对路径——开源后对任何别人都是死路，且泄露本机目录结构。）"""
    KEEP = ("model_quantized.onnx", "config.json", "ort_config.json",
            "sentencepiece.bpe.model", "special_tokens_map.json",
            "tokenizer.json", "tokenizer_config.json")
    dev_models = Path(src_dir or os.environ.get("LOCALKB_MODELS") or C.MODELS)
    if not dev_models.exists():
        print(f"[bundle] ⚠ --slim-models：模型母本目录不存在 {dev_models}（用 --models-dir 或 LOCALKB_MODELS 指定）")
        return
    print(f"[bundle] --slim-models：模型母本 {dev_models}")
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


def verify_guides():
    """构建期护栏：指引（前端章节 / MCP接入说明 / agent 模板）与代码不一致就别打包。
    理由见 docs/MAINTENANCE.md §2.3——「改功能忘了同步指引」在这个项目里已经真的发生过
    （工具表漂了 4 个），靠人肉纪律已被证伪；打包是发布的必经关口，卡在这里代价最小。
    --skip-checks 可临时跳过（比如只是本地自测一个包），但正式发版不许跳。"""
    import subprocess
    cg = SRC / "check_guides.py"
    if not cg.exists():
        print("[bundle] ⚠ 没找到 check_guides.py，跳过指引校验")
        return
    r = subprocess.run([sys.executable, str(cg)], cwd=str(SRC))
    if r.returncode != 0:
        raise SystemExit("[bundle] ✗ 指引与代码不一致（上面 ❌ 那几条），已中止打包。"
                         "逐条修完再来；确要跳过用 --skip-checks。")


def ensure_python(src_dir=None):
    r"""把 Python 运行时拷进 bundle/python/。

    历史问题：这个函数以前不存在 —— main() 只**检查** bundle/python/python.exe 在不在，
    不在就直接 return。而全项目没有任何脚本负责创建它。结果就是「完整构建」这条路
    从来没跑通过一次，python/ 全靠人手工摆进去（且没有任何文档记录怎么摆）。

    现在：默认从 <仓库根>/build/py312 拷（那正是开发解释器，也是被 requirements.lock
    实机验证过的那套依赖）。也可用 --python-src / 环境变量 LOCALKB_PY_SRC 指定。
    重建 build/py312 的方法见 docs/RELEASE.md §0.2。
    """
    dst = BUNDLE / "python"
    if (dst / "python.exe").exists():
        print(f"[bundle] python/ 已就位：{dst}")
        return

    src = Path(src_dir or os.environ.get("LOCALKB_PY_SRC") or (SRC.parent / "build" / "py312"))
    if not (src / "python.exe").exists():
        raise SystemExit(
            f"[bundle] ✗ 找不到 Python 运行时：{src}\n"
            f"    这是全链路唯一不能自动重建的东西。重建方法见 docs/RELEASE.md §0.2：\n"
            f"      ① 下 python-build-standalone (CPython 3.12, install_only)\n"
            f"      ② pip install -r requirements.lock   ← 用 lock，别用 requirements.txt\n"
            f"      ③ 拷 msvcp140.dll + msvcp140_1.dll 进去\n"
            f"    或用 --python-src 指定一个现成的。")

    print(f"[bundle] 拷贝 Python 运行时：{src} → {dst}（约 800MB，稍等）")
    shutil.copytree(src, dst,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pdb"))
    print(f"[bundle] python/ 就绪")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slim-models", action="store_true", help="自测：copy 开发机模型运行时文件进 models/")
    ap.add_argument("--models-dir", default=None,
                    help="--slim-models 的模型母本目录（默认取环境变量 LOCALKB_MODELS / config.MODELS）")
    ap.add_argument("--python-src", default=None,
                    help="Python 运行时来源目录（默认 <仓库根>/build/py312，或环境变量 LOCALKB_PY_SRC）")
    ap.add_argument("--sync-only", action="store_true",
                    help="只同步 app/ 源码（不碰 python/ 与 models/）——改代码后快速刷新已构建的 bundle")
    ap.add_argument("--skip-checks", action="store_true",
                    help="跳过 check_guides.py 指引一致性校验（仅本地自测用；正式发版别跳）")
    ap.add_argument("--allow-missing-vcrt", action="store_true",
                    help="允许缺 msvcp140*.dll（只出 API 模式可用的包；本地嵌入/重排会在干净机上崩）")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if not args.skip_checks:
        verify_guides()     # 指引漂移 → 直接中止，别发出一个"说明书对不上代码"的包
    if args.sync_only:
        if not BUNDLE.exists():
            print(f"[bundle] ✗ 找不到已构建的 bundle：{BUNDLE}（先完整构建/解压一次）"); return
        copy_app()
        print(f"[bundle] 已同步 app/（源码）→ {APP_OUT}"); return
    if not args.slim_models:
        verify_manifest()   # 依赖首启下载模型的分发包：构建前先确保 manifest 有真实直链
    BUNDLE.mkdir(parents=True, exist_ok=True)
    ensure_python(args.python_src)   # ← 以前没有这一步，所以完整构建从没跑通过
    copy_app()
    (BUNDLE / "data").mkdir(exist_ok=True)
    (BUNDLE / "models").mkdir(exist_ok=True)
    ensure_vc_runtime(args.allow_missing_vcrt)   # onnxruntime 硬依赖；缺了干净机必崩
    ensure_git()            # 自带 MinGit（综合层版本历史/回滚用；缺了会退回快照，不阻断构建）
    slim_python()
    write_launchers()
    if args.slim_models:
        copy_slim_models(args.models_dir)
    print(f"[bundle] 完成 → {BUNDLE}")


if __name__ == "__main__":
    main()

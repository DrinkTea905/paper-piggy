# -*- coding: utf-8 -*-
r"""
出包总编排 —— 一条命令产出两样东西：

  ① 安装器   dist-installer\PaperPiggy-<ver>-win64.exe    （Inno Setup）
  ② 更新包   dist-installer\paper-piggy-app-<ver>.zip      （只含 app\，供 updater.py 用）
             + 同名 .sha256
             设置页「应用更新」会下载并校验它，再由 launcher 拉起独立 updater.py 完成替换与重启。

前置：先跑 build_bundle.py 生成 src\dist\LocalKB\。

【数据落在哪】
  安装器会把 installer\portable.txt 一起装进安装目录，它是「数据与程序同目录」的开关：
    有   → 索引/模型/wiki/0_Agent* 全在安装目录内（用户可整个装到 D:\PaperPiggy，C 盘不占）
    没有 → 落 %LOCALAPPDATA%\PaperPiggy
  这要求安装目录可写 → 安装器用**用户级**安装（PrivilegesRequired=lowest），
  而不是 Program Files。两个决定是一套的，详见 paperpiggy.iss 文件头 §数据同目录。

【便携 zip 已于 1.0.0 下线】
  数据与程序同目录之后，「删掉旧文件夹、解压新版」会把用户的索引和论文一次性删光。
  只发安装器。详见 paperpiggy.iss §为什么砍掉便携 zip。

用法：
    python installer\build_installer.py              # 全出
    python installer\build_installer.py --app-only   # 只出 ②（发小版本更新时用）
"""
import sys, os, json, shutil, hashlib, zipfile, argparse, subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "src"
BUNDLE = SRC / "dist" / "LocalKB"
OUT = ROOT / "dist-installer"

sys.path.insert(0, str(SRC))

ISCC_CANDIDATES = [
    # winget 装的是 per-user，落在 %LOCALAPPDATA%\Programs（不是 Program Files）
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\Inno Setup 6\ISCC.exe"),
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
]


def app_version():
    """版本号的唯一事实源 = config.APP_VERSION。"""
    import config as C
    v = getattr(C, "APP_VERSION", None)
    if not v:
        raise SystemExit("[installer] ✗ config.py 里没有 APP_VERSION —— 版本号必须有唯一事实源。")
    return v


def check_bundle():
    if not BUNDLE.exists():
        raise SystemExit(f"[installer] ✗ 找不到 {BUNDLE}\n"
                         f"    先跑：python src\\build_bundle.py")
    if not (BUNDLE / "python" / "python.exe").exists():
        raise SystemExit(f"[installer] ✗ {BUNDLE}\\python\\python.exe 不存在（bundle 不完整）")

    # 干净机 blocker：onnxruntime 同时需要 msvcp140.dll 和 msvcp140_1.dll，
    # 而 python-build-standalone 只自带 vcruntime140(_1)。少一个就 WinError 1114。
    missing = [d for d in ("msvcp140.dll", "msvcp140_1.dll")
               if not (BUNDLE / "python" / d).exists()]
    if missing:
        raise SystemExit(f"[installer] ✗ bundle\\python\\ 缺 {', '.join(missing)}\n"
                         f"    干净电脑（没装 VC++ 2015-2022）上本地模式会直接 WinError 1114。\n"
                         f"    跑 build_bundle.py 的 ensure_vc_runtime()，或手动 copy。")

    # ① 错位残留：数据绝不该落在 app\ 下（那是代码目录）。这种一定是 bug 产物，直接清。
    for junk in ("app/data", "app/logs"):
        p = BUNDLE / junk
        if p.exists():
            print(f"[installer] ⚠ 清理残留：{junk}")
            shutil.rmtree(p, ignore_errors=True)

    # ② 隐私闸门：数据与程序同目录之后，开发者自测 bundle 会在**包根**留下真实数据 ——
    #    data\（含 settings.json 里的硅基流动 API key、文献元数据、索引）、
    #    0_Agent交付物\（写好的论文）、0_Agent资料库\（项目记忆）。
    #    .iss 的 Excludes 已经排除它们，但那是第二道；这里是第一道。
    #    ⛔ 只报警中止，**绝不自动删** —— 那可能是你辛苦跑出来的自测索引，甚至是真实交付物。
    dirty = []
    for junk in ("data", "logs", "update", "0_Agent交付物", "0_Agent资料库"):
        p = BUNDLE / junk
        try:
            if p.exists() and any(p.iterdir()):
                dirty.append(junk)
        except Exception:
            pass
    if dirty:
        raise SystemExit(
            f"[installer] ✗ bundle 根目录里有非空的用户数据：{', '.join(dirty)}\n"
            f"    位置：{BUNDLE}\n"
            f"    这些是你自测这个包时产生的（数据与程序同目录）。里面可能有 API key、\n"
            f"    文献元数据、写好的论文 —— 不能打进公开安装包。\n"
            f"    确认无用就手动删掉再出包；有用就先挪走。（本脚本刻意不替你删。）")

    print(f"[installer] bundle 检查通过：{BUNDLE}")


def _sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


WEBVIEW2_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"   # Evergreen Bootstrapper（微软官方短链）


def ensure_webview2():
    r"""确保 installer\MicrosoftEdgeWebview2Setup.exe 存在（约 1.6MB）。

    不提交进 git —— 它是微软的可再分发二进制，让构建时现下更干净（也保证拿到最新版）。
    """
    dst = HERE / "MicrosoftEdgeWebview2Setup.exe"
    if dst.exists() and dst.stat().st_size > 100_000:
        print(f"[installer] WebView2 bootstrapper 已就位（{dst.stat().st_size/1e6:.1f} MB）")
        return dst

    print("[installer] 下载 WebView2 Evergreen Bootstrapper…")
    from urllib.request import urlopen, Request
    try:
        with urlopen(Request(WEBVIEW2_URL, headers={"User-Agent": "PaperPiggy-build"}), timeout=60) as r:
            data = r.read()
    except Exception as e:
        raise SystemExit(
            f"[installer] ✗ 下载 WebView2 bootstrapper 失败：{e}\n"
            f"    手动下载 {WEBVIEW2_URL}\n"
            f"    另存为 {dst}")
    if len(data) < 100_000:
        raise SystemExit(f"[installer] ✗ 下到的文件只有 {len(data)} 字节，不像 bootstrapper，已中止")
    dst.write_bytes(data)
    print(f"[installer] ✓ WebView2 bootstrapper → {dst}（{len(data)/1e6:.1f} MB）")
    return dst


def ensure_icon():
    r"""生成 installer\PaperPiggy.ico 供 Inno 用。

    仓库里只有 web\PaperPiggy.png —— .ico 一直是 launcher 运行时现封的（PNG-in-ICO），
    没有静态 .ico 文件。这里用同一套纯 stdlib 逻辑在构建期封一份出来。
    """
    ico = HERE / "PaperPiggy.ico"
    if ico.exists() and ico.stat().st_size > 0:
        return ico
    png = SRC / "web" / "PaperPiggy.png"
    if not png.exists():
        raise SystemExit(f"[installer] ✗ 找不到 {png}（应用图标的真源）")

    import struct
    data = png.read_bytes()
    # ICONDIR: reserved=0, type=1(icon), count=1
    hdr = struct.pack("<HHH", 0, 1, 1)
    # ICONDIRENTRY: w=0/h=0 表示 256px；PNG 数据直接内嵌（PNG-in-ICO，Vista+ 支持）
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(data), 6 + 16)
    ico.write_bytes(hdr + entry + data)
    print(f"[installer] 图标已生成：{ico}（从 web/PaperPiggy.png 封成 PNG-in-ICO）")
    return ico


def build_setup(ver):
    """Inno Setup 安装器。"""
    ensure_icon()
    iscc = next((p for p in ISCC_CANDIDATES if os.path.exists(p)), None)
    if not iscc:
        print("[installer] ⚠ 没找到 ISCC.exe，跳过安装器。")
        print("    装：winget install JRSoftware.InnoSetup")
        return None

    # portable.txt（=「数据与程序同目录」开关）由 .iss 从 installer\portable.txt 装入，
    # 不从 bundle 里带 —— bundle 里那份（如果开发者自测时留了一个）已被 .iss 的 Excludes 排除。
    if not (HERE / "portable.txt").exists():
        raise SystemExit(
            "[installer] ✗ 缺 installer\\portable.txt —— 它是「数据与程序同目录」的开关，\n"
            "    .iss 要把它装进安装目录。没有它，用户的数据会落到 %LOCALAPPDATA%\\PaperPiggy，\n"
            "    与「一个文件夹装下一切」的产品设计不符。")

    ensure_webview2()

    OUT.mkdir(exist_ok=True)
    cmd = [iscc, f"/DAppVersion={ver}", str(HERE / "paperpiggy.iss")]
    print(f"[installer] 编译安装器：{' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stdout[-3000:])
        print(r.stderr[-2000:], file=sys.stderr)
        raise SystemExit("[installer] ✗ Inno Setup 编译失败")
    exe = OUT / f"PaperPiggy-{ver}-win64.exe"
    print(f"[installer] ✓ 安装器 → {exe}  ({exe.stat().st_size / 1e6:.1f} MB)")
    return exe


# ⛔ 便携 zip 已于 1.0.0 下线（原 build_portable 见 git 历史）。
#    理由：数据与程序同目录之后，「删掉旧文件夹、解压新版」——便携软件最常规的升级姿势——
#    会把用户的索引、wiki、API key、写好的论文一次性删光。安装器升级不会（只覆盖 app\ 和
#    python\）。别为了「方便」把它加回来：那不是方便，是给用户挖坑。
#    详见 paperpiggy.iss 文件头 §为什么砍掉便携 zip。


def build_app_package(ver):
    """updater 用的增量包：只含 app\\，几 MB。资产名必须与 updater.py 的约定一致。"""
    OUT.mkdir(exist_ok=True)
    app = BUNDLE / "app"
    if not app.exists():
        raise SystemExit(f"[installer] ✗ 找不到 {app}")

    dst = OUT / f"paper-piggy-app-{ver}.zip"
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in app.rglob("*"):
            if p.is_dir() or "__pycache__" in p.parts:
                continue
            z.write(p, p.relative_to(app))   # 解压后直接落进 app\，与 updater.apply() 对应

    digest = _sha256(dst)
    (OUT / f"{dst.name}.sha256").write_text(f"{digest}  {dst.name}\n", encoding="utf-8")
    print(f"[installer] ✓ 更新包 → {dst}  ({dst.stat().st_size / 1e6:.1f} MB)")
    print(f"[installer]   sha256 = {digest}")
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app-only", action="store_true", help="只出 updater 用的 app 增量包")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ver = app_version()
    print(f"[installer] 版本 {ver}（源：config.APP_VERSION）")
    check_bundle()

    app_zip = build_app_package(ver)
    setup_exe = None if a.app_only else build_setup(ver)

    print(f"\n[installer] 产物都在 {OUT}")
    print("[installer] 下一步：把安装器 + 更新包 + sha256 传到 GitHub Release")
    # ⛔ 不用 dist-installer\*：目录里可能残留上一版产物，通配符会把旧安装器一起传上去。
    assets = ([str(setup_exe)] if setup_exe else []) + [str(app_zip), str(app_zip) + ".sha256"]
    quoted = " ".join(f'"{p}"' for p in assets)
    print(f"    gh release create v{ver} {quoted} --repo DrinkTea905/paper-piggy")


if __name__ == "__main__":
    main()

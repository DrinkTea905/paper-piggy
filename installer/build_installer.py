# -*- coding: utf-8 -*-
r"""
出包总编排 —— 一条命令产出三样东西：

  ① 安装器      dist-installer\PaperPiggy-<ver>-win64.exe   （Inno Setup；不带 portable.txt）
  ② 便携 zip    dist-installer\PaperPiggy-<ver>-portable.zip（带 portable.txt，解压即用）
  ③ 更新包      dist-installer\paper-piggy-app-<ver>.zip     （只含 app\，供 updater.py 用）
                + 同名 .sha256

前置：先跑 build_bundle.py 生成 LocalKB源码\dist\LocalKB\。

【为什么 ① 和 ② 必须不同】
  portable.txt 决定数据落哪：
    有   → 数据写包内（U 盘、免安装场景）
    没有 → 数据写 %LOCALAPPDATA%\LocalKB
  安装器版装在 Program Files（只读），**绝不能带 portable.txt**，否则首次建库就崩。
  这个脚本会强制检查这一点。

用法：
    python installer\build_installer.py              # 全出
    python installer\build_installer.py --app-only   # 只出 ③（发小版本更新时用）
"""
import sys, os, json, shutil, hashlib, zipfile, argparse, subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "LocalKB源码"
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
                         f"    先跑：python LocalKB源码\\build_bundle.py")
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

    # 包里不该有开发机的数据/日志残留
    for junk in ("app/data", "app/logs"):
        p = BUNDLE / junk
        if p.exists():
            print(f"[installer] ⚠ 清理残留：{junk}")
            shutil.rmtree(p, ignore_errors=True)
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

    # 安装器绝不能带 portable.txt（.iss 的 Excludes 已排除，这里双保险）
    pt = BUNDLE / "portable.txt"
    if pt.exists():
        print(f"[installer] ⚠ bundle 里有 portable.txt —— .iss 会排除它，但便携 zip 需要它，保留原文件")

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


def build_portable(ver):
    """便携 zip：解压即用，**带 portable.txt**（数据落包内）。"""
    OUT.mkdir(exist_ok=True)
    dst = OUT / f"PaperPiggy-{ver}-portable.zip"

    # 临时塞入 portable.txt（build_bundle 默认不生成它）
    pt = BUNDLE / "portable.txt"
    created = not pt.exists()
    if created:
        pt.write_text("portable\n", encoding="utf-8")

    try:
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for p in BUNDLE.rglob("*"):
                if p.is_dir() or "__pycache__" in p.parts:
                    continue
                z.write(p, Path("PaperPiggy") / p.relative_to(BUNDLE))
    finally:
        if created:
            pt.unlink(missing_ok=True)      # 别把 portable.txt 留在 bundle 里污染安装器

    print(f"[installer] ✓ 便携 zip → {dst}  ({dst.stat().st_size / 1e6:.1f} MB)")
    return dst


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

    if a.app_only:
        build_app_package(ver)
    else:
        build_app_package(ver)
        build_portable(ver)
        build_setup(ver)

    print(f"\n[installer] 产物都在 {OUT}")
    print("[installer] 下一步：把安装器/便携 zip/更新包+sha256 传到 GitHub Release")
    print(f"    gh release create v{ver} dist-installer\\* --repo DrinkTea905/paper-piggy")


if __name__ == "__main__":
    main()

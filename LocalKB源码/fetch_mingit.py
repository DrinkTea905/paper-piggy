# -*- coding: utf-8 -*-
"""下载 MinGit 塞进分发包（开发/构建期跑一次）。

为什么要它：装了 exe 的用户机器多半没装 git，而综合层的版本历史/回滚在有 git 时体验最好
（能看逐字 diff）。MinGit 是 git-for-windows 官方出的精简版（约 50MB，解压后可直接用），
放进 <bundle>/git/ 后，wiki_vcs._find_git() 会优先用它，所有用户都有真 git。
没有它也不会坏——wiki_vcs 会自动退回 .history 快照。

用法：
  python fetch_mingit.py                     # 下最新版到 <本包>/git/
  python fetch_mingit.py --dest "D:\\...\\LocalKB"   # 指定 bundle 根
只依赖 stdlib（urllib + zipfile），分发 python 也能跑。
"""
import sys, os, json, argparse, zipfile, tempfile, shutil, urllib.request, subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C

API = "https://api.github.com/repos/git-for-windows/git/releases/latest"


def latest_asset():
    req = urllib.request.Request(API, headers={"User-Agent": "PaperPiggy-build"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    # 要 64 位、非 busybox 的标准 MinGit
    for a in data.get("assets", []):
        n = a.get("name", "")
        if n.startswith("MinGit-") and n.endswith("-64-bit.zip") and "busybox" not in n:
            return a["browser_download_url"], n
    raise SystemExit("未在 latest release 找到 MinGit-*-64-bit.zip")


def _download(url, dst):
    print(f"[mingit] 下载 {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "PaperPiggy-build"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dst, "wb") as f:
        total = int(r.headers.get("Content-Length", 0))
        got = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk); got += len(chunk)
            if total:
                pct = got * 100 // total
                print(f"\r[mingit] {got >> 20}/{total >> 20} MB ({pct}%)", end="", flush=True)
        print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=str(C.APP.parent),
                    help="bundle 根目录（含 app/ python/），git 将解压到其下 git/。默认=本包上级")
    args = ap.parse_args()
    dest_root = Path(args.dest)
    git_dir = dest_root / "git"
    if (git_dir / "cmd" / "git.exe").exists():
        print(f"[mingit] 已存在：{git_dir}（如需更新先删掉它再跑）")
        return

    url, name = latest_asset()
    with tempfile.TemporaryDirectory() as td:
        zp = Path(td) / name
        _download(url, zp)
        print(f"[mingit] 解压 → {git_dir}")
        if git_dir.exists():
            shutil.rmtree(git_dir, ignore_errors=True)
        git_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zp) as z:
            z.extractall(git_dir)

    exe = git_dir / "cmd" / "git.exe"
    if not exe.exists():
        raise SystemExit(f"[mingit] ✗ 解压后没找到 {exe}")
    try:
        out = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=10)
        print(f"[mingit] ✓ {out.stdout.strip()} 就位于 {git_dir}")
    except Exception as e:
        print(f"[mingit] ⚠ 解压完成但 git --version 跑不起来：{e}")


if __name__ == "__main__":
    main()

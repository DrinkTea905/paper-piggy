# -*- coding: utf-8 -*-
r"""
自动更新器 —— 只换 app\，绝不碰 python\ 与用户数据。

【为什么只换 app\】
  · python\ 是 800M 的运行时，换它等于重装，且失败风险高（DLL 被占用）。
  · 用户数据在 %LOCALAPPDATA%\LocalKB\data，**任何情况下都不能动**。
  · app\ 是纯 .py + web\，几 MB，替换快、回滚易。

【开源明文 .py 的特有坑 —— 用户可能改过包里的代码】
  本项目刻意以明文 .py 分发（不编译不混淆），所以用户完全可能自己改了 app\ 里的文件。
  无脑覆盖 = 把人家的修改抹掉。所以更新前会拿 app\version.json 里的 sha256 清单
  逐文件比对：改过的文件**不会被静默覆盖**，而是备份成 <name>.bak-<旧版本> 并提示。

【流程】
  check()     查 GitHub Release latest → 比版本
  download()  下 app 包 → sha256 校验（不过就删掉重来，绝不用坏包）
  apply()     ← 由**独立进程**执行：等主进程退出 → 备份 app\ → 替换 → 校验 → 失败回滚

用法：
    python updater.py --check              # 只查，输出 JSON
    python updater.py --download           # 查 + 下载 + 校验（不应用）
    python updater.py --apply --pid <PID>  # 等 PID 退出后应用更新（由应用自己拉起）
"""
import sys, os, json, time, shutil, hashlib, zipfile, argparse, tempfile, subprocess
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

sys.path.insert(0, str(Path(__file__).parent))
import config as C

GH_OWNER = "DrinkTea905"
GH_REPO  = "paper-piggy"
API_LATEST = f"https://api.github.com/repos/{GH_OWNER}/{GH_REPO}/releases/latest"
UA = {"User-Agent": f"PaperPiggy/{getattr(C, 'APP_VERSION', '0')} (+https://github.com/{GH_OWNER}/{GH_REPO})"}

APP_DIR    = C.APP                       # 分发包里 = <bundle>\app
BUNDLE_DIR = APP_DIR.parent              # <bundle>
UPDATE_DIR = C.DATA.parent / "update"    # %LOCALAPPDATA%\LocalKB\update（可写，且不在 app\ 里）


# ───────────────────────── 版本比较 ─────────────────────────

def _ver_tuple(v):
    """语义化版本 → 可比较的元组。

    末位是「预发布标记」：正式版 1，预发布（-rc1/-beta 等）0 —— 这样 1.0.0 > 1.0.0-rc1，
    符合 semver。少了这一位的话，发了 rc1 的用户永远收不到正式版更新
    （因为 1.0.0-rc1 会被解析成 (1,0,0)，与正式版 (1,0,0) 相等）。

    >>> _ver_tuple("1.0.0")       # (1, 0, 0, 1)
    >>> _ver_tuple("1.0.0-rc1")   # (1, 0, 0, 0)
    """
    s = str(v).strip().lstrip("vV")
    core, _, pre = s.partition("-")          # "1.0.0-rc1" → core="1.0.0", pre="rc1"
    nums = []
    for part in core.split("."):
        try:
            nums.append(int(part))
        except ValueError:
            break
    while len(nums) < 3:                      # "1.2" → (1,2,0)，保证位数一致才好比较
        nums.append(0)
    nums.append(0 if pre else 1)              # 有预发布后缀 → 排在正式版之前
    return tuple(nums)


def is_newer(remote, local):
    return _ver_tuple(remote) > _ver_tuple(local)


# ───────────────────────── 查新版 ─────────────────────────

def check(timeout=10):
    """返回 {'current','latest','has_update','url','sha256','notes'}；网络失败返回 error 字段。"""
    cur = getattr(C, "APP_VERSION", "0.0.0")
    try:
        req = Request(API_LATEST, headers=UA)
        with urlopen(req, timeout=timeout) as r:
            rel = json.loads(r.read().decode("utf-8"))
    except (URLError, HTTPError, TimeoutError, OSError) as e:
        return {"current": cur, "has_update": False, "error": f"{type(e).__name__}: {e}"}

    latest = (rel.get("tag_name") or "").lstrip("vV")
    if not latest:
        return {"current": cur, "has_update": False, "error": "Release 没有 tag_name"}

    # 约定：app 更新包的资产名形如 paper-piggy-app-<version>.zip，同名 .sha256 放校验和
    asset = sha = None
    for a in rel.get("assets") or []:
        n = a.get("name") or ""
        if n.startswith("paper-piggy-app-") and n.endswith(".zip"):
            asset = a.get("browser_download_url")
        elif n.startswith("paper-piggy-app-") and n.endswith(".zip.sha256"):
            sha = a.get("browser_download_url")

    return {
        "current":    cur,
        "latest":     latest,
        "has_update": is_newer(latest, cur) and bool(asset),
        "url":        asset,
        "sha256_url": sha,
        "notes":      (rel.get("body") or "")[:2000],
    }


# ───────────────────────── 下载 + 校验 ─────────────────────────

def _sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def download(info=None, progress=None):
    """下 app 包并校验 sha256。校验不过 → 删掉，返回 error（绝不把坏包留在盘上）。"""
    info = info or check()
    if not info.get("has_update"):
        return {"ok": False, "error": "没有可用更新"}

    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    dst = UPDATE_DIR / f"app-{info['latest']}.zip"

    # 期望的 sha256（Release 里单独放一个 .sha256 资产）
    want = None
    if info.get("sha256_url"):
        try:
            with urlopen(Request(info["sha256_url"], headers=UA), timeout=15) as r:
                want = r.read().decode("utf-8").split()[0].strip().lower()
        except Exception:
            want = None

    try:
        with urlopen(Request(info["url"], headers=UA), timeout=60) as r:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            with open(dst, "wb") as f:
                while True:
                    b = r.read(1 << 16)
                    if not b:
                        break
                    f.write(b)
                    done += len(b)
                    if progress:
                        progress(done, total)
    except Exception as e:
        dst.unlink(missing_ok=True)
        return {"ok": False, "error": f"下载失败：{type(e).__name__}: {e}"}

    got = _sha256(dst)
    if want and got != want:
        dst.unlink(missing_ok=True)   # 坏包立即删除，不给"下次凑合用"的机会
        return {"ok": False, "error": f"sha256 校验失败（期望 {want[:12]}…，实际 {got[:12]}…），已删除"}

    return {"ok": True, "zip": str(dst), "version": info["latest"], "sha256": got, "verified": bool(want)}


# ───────────────────────── 用户改动检测 ─────────────────────────

def local_modifications():
    """拿 app/version.json 的 sha256 清单比对磁盘，找出用户改过的文件。
    开源明文分发的直接后果：用户完全可能自己改代码。这些文件不能被静默覆盖。"""
    vj = APP_DIR / "version.json"
    if not vj.exists():
        return []                      # 没有清单就无从判断，保守起见当作没改过
    try:
        man = json.loads(vj.read_text(encoding="utf-8"))
    except Exception:
        return []
    changed = []
    for rel, want in (man.get("files") or {}).items():
        p = APP_DIR / rel
        if not p.exists():
            continue                   # 用户删了文件，覆盖回来即可
        if _sha256(p) != want:
            changed.append(rel)
    return changed


# ───────────────────────── 应用更新（独立进程执行）─────────────────────────

def _wait_pid_exit(pid, timeout=60):
    r"""等主进程退出。app\ 里的 .py 被占用时替换会失败。"""
    if not pid:
        return True
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            os.kill(pid, 0)            # Windows 上 signal 0 = 只探测存在性
        except OSError:
            return True                # 进程没了
        time.sleep(0.5)
    return False


def apply(zip_path, pid=None):
    r"""备份 app\ → 解压新版覆盖 → 校验 → 失败整体回滚。只碰 app\。"""
    zip_path = Path(zip_path)
    if not zip_path.exists():
        return {"ok": False, "error": f"更新包不存在：{zip_path}"}

    if not _wait_pid_exit(pid):
        return {"ok": False, "error": f"主进程（PID {pid}）60 秒内没退出，放弃更新"}

    old_ver = getattr(C, "APP_VERSION", "0")
    backup = BUNDLE_DIR / f"app.bak-{old_ver}"

    # 用户改过的文件：先单独留一份，别让他们的修改无声消失
    mods = local_modifications()
    for rel in mods:
        src = APP_DIR / rel
        keep = src.with_suffix(src.suffix + f".bak-{old_ver}")
        try:
            shutil.copy2(src, keep)
        except Exception:
            pass

    # ① 整体备份 app\（回滚的依据）
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    try:
        shutil.copytree(APP_DIR, backup)
    except Exception as e:
        return {"ok": False, "error": f"备份 app\\ 失败，已中止（未做任何改动）：{e}"}

    # ② 解压覆盖
    try:
        with zipfile.ZipFile(zip_path) as z:
            bad = z.testzip()
            if bad:
                raise RuntimeError(f"更新包损坏（首个坏文件：{bad}）")
            z.extractall(APP_DIR)
    except Exception as e:
        # ③ 回滚
        shutil.rmtree(APP_DIR, ignore_errors=True)
        shutil.move(str(backup), str(APP_DIR))
        return {"ok": False, "error": f"解压失败，已回滚到 {old_ver}：{e}"}

    # ④ 落地校验：新版必须能 import（否则回滚）
    try:
        r = subprocess.run(
            [sys.executable, "-c", "import sys; sys.path.insert(0, r'%s'); import config; print(config.APP_VERSION)" % APP_DIR],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            raise RuntimeError((r.stderr or "")[-500:])
        new_ver = (r.stdout or "").strip()
    except Exception as e:
        shutil.rmtree(APP_DIR, ignore_errors=True)
        shutil.move(str(backup), str(APP_DIR))
        return {"ok": False, "error": f"新版无法启动，已回滚到 {old_ver}：{e}"}

    shutil.rmtree(backup, ignore_errors=True)
    zip_path.unlink(missing_ok=True)
    return {"ok": True, "from": old_ver, "to": new_ver, "user_modified_kept": mods}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--apply", metavar="ZIP", nargs="?", const="")
    ap.add_argument("--pid", type=int, default=0, help="等这个进程退出后再替换 app\\")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if a.check:
        print(json.dumps(check(), ensure_ascii=False, indent=2))
    elif a.download:
        print(json.dumps(download(), ensure_ascii=False, indent=2))
    elif a.apply is not None:
        z = a.apply
        if not z:                                        # 没给路径就找 update/ 里最新的
            cands = sorted(UPDATE_DIR.glob("app-*.zip"), key=lambda p: p.stat().st_mtime)
            if not cands:
                print(json.dumps({"ok": False, "error": "update/ 里没有待应用的包"}, ensure_ascii=False)); return 1
            z = cands[-1]
        print(json.dumps(apply(z, a.pid), ensure_ascii=False, indent=2))
    else:
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

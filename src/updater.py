# -*- coding: utf-8 -*-
r"""
自动更新器 —— 只换 app\，绝不碰 python\ 与用户数据。

本模块已由设置页「应用更新」接线：server 下载增量包，launcher 用独立的无窗进程执行本文件，
主应用退出后再替换 app\。更新结果写到数据家下的 update\update.log；失败时会尝试重启旧版并
给出可见提示，不能把失败静默吞掉。

【为什么只换 app\】
  · python\ 是 800M 的运行时，换它等于重装，且失败风险高（DLL 被占用）。
  · 用户数据在安装目录的 data\（数据与程序同目录）或 %LOCALAPPDATA%\PaperPiggy\data，
    **任何情况下都不能动**。
  · app\ 是纯 .py + web\，几 MB，替换快、回滚易。

【开源明文 .py 的特有坑 —— 用户可能改过包里的代码】
  本项目刻意以明文 .py 分发（不编译不混淆），所以用户完全可能自己改了 app\ 里的文件。
  无脑覆盖 = 把人家的修改抹掉。所以更新前会拿 app\version.json 里的 sha256 清单
  逐文件比对：改过的文件会复制到「你改过的旧代码-<旧版本>\」，不会静默丢失。
  ⚠️ Inno 覆盖安装仍会以 `Flags: ignoreversion` 覆盖 app\；这项保护只适用于应用内更新。

【流程】
  check()     查 GitHub Release latest → 比版本
  download()  下 app 包 → sha256 校验（不过就删掉重来，绝不用坏包）
  apply()     ← 由**独立进程**执行：等主进程退出 → 备份 app\ → 替换 → 校验 → 失败回滚

用法：
    python updater.py --check              # 只查，输出 JSON
    python updater.py --download           # 查 + 下载 + 校验（不应用）
    python updater.py --apply --pid <PID>  # 等 PID 退出后应用更新（由应用自己拉起）
"""
import sys, os, json, time, shutil, hashlib, zipfile, argparse, tempfile, subprocess, traceback
import importlib.util
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
UPDATE_DIR = C.DATA.parent / "update"    # 数据家下的 update\（可写，且不在 app\ 里）
UPDATE_LOG = UPDATE_DIR / "update.log"


def _log(event, detail=None):
    """独立 updater 没有控制台；把每次开始、结果和未捕获异常写进持久日志。"""
    try:
        UPDATE_DIR.mkdir(parents=True, exist_ok=True)
        payload = detail if isinstance(detail, str) else json.dumps(detail or {}, ensure_ascii=False)
        with open(UPDATE_LOG, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {event}: {payload}\n")
    except Exception:
        pass


def _notify(title, message):
    """应用已经关闭时用系统消息框提示结果；非 Windows 至少保留日志。"""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, str(message), str(title), 0x10 | 0x40000)
            return
        except Exception:
            pass
    _log(title, message)


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


def missing_runtime_components():
    """应用增量包补不了 python/site-packages；返回当前运行时缺失的必需组件。"""
    required = (("rapidocr", "本地 OCR"), ("cv2", "OpenCV"))
    missing = []
    for module, label in required:
        try:
            if importlib.util.find_spec(module) is None:
                missing.append(label)
        except Exception:
            missing.append(label)
    return missing


# ───────────────────────── 查新版 ─────────────────────────

def check(timeout=12, tries=3):
    """返回 {'current','latest','has_update','url','sha256','notes'}；网络失败返回 error 字段。
    带重试：国内连 GitHub 偶发超时，多试两次往往就通了。"""
    cur = getattr(C, "APP_VERSION", "0.0.0")
    rel = None
    last = None
    for i in range(tries):
        try:
            req = Request(API_LATEST, headers=UA)
            with urlopen(req, timeout=timeout) as r:
                rel = json.loads(r.read().decode("utf-8"))
            break
        except (URLError, HTTPError, TimeoutError, OSError) as e:
            last = e
            if i < tries - 1:
                time.sleep(1.5 * (i + 1))
    if rel is None:
        return {"current": cur, "has_update": False, "error": f"{type(last).__name__}: {last}"}

    latest = (rel.get("tag_name") or "").lstrip("vV")
    if not latest:
        return {"current": cur, "has_update": False, "error": "Release 没有 tag_name"}

    # 约定：app 更新包的资产名形如 paper-piggy-app-<version>.zip，同名 .sha256 放校验和
    asset = sha = installer = None
    for a in rel.get("assets") or []:
        n = a.get("name") or ""
        if n.startswith("paper-piggy-app-") and n.endswith(".zip"):
            asset = a.get("browser_download_url")
        elif n.startswith("paper-piggy-app-") and n.endswith(".zip.sha256"):
            sha = a.get("browser_download_url")
        elif n.startswith("PaperPiggy-") and n.endswith("-win64.exe"):
            installer = a.get("browser_download_url")

    missing_runtime = missing_runtime_components()

    return {
        "current":    cur,
        "latest":     latest,
        "has_update": is_newer(latest, cur) and bool(asset),
        "url":        asset,
        "sha256_url": sha,
        # 旧运行时即使先装了 app 增量包，也拿不到新增 wheel。新版启动后会据此
        # 明确引导用户运行完整安装器；覆盖安装不删除任何用户数据。
        "needs_full_installer": bool(missing_runtime),
        "missing_runtime": missing_runtime,
        "installer_url": installer,
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


def _mirror_base():
    """国内镜像目录前缀（settings.update.mirror_base）。空则不启用。"""
    try:
        import settings as S
        b = (S.load().get("update") or {}).get("mirror_base") or ""
        return b.rstrip("/") + "/" if b else ""
    except Exception:
        return ""


def _candidate_urls(primary_url, filename):
    """下载源，按序尝试：GitHub 直链优先，再镜像。镜像 URL = mirror_base + 文件名。"""
    urls = [primary_url] if primary_url else []
    mb = _mirror_base()
    if mb and filename:
        murl = mb + filename
        if murl not in urls:
            urls.append(murl)
    return urls


def _fetch_to(url, dst, progress=None, timeout=60):
    """把 url 下到 dst。任一步失败抛异常（调用方负责删残包、试下一个源）。"""
    with urlopen(Request(url, headers=UA), timeout=timeout) as r:
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


def _fetch_text(url, timeout=15, tries=2):
    for i in range(tries):
        try:
            with urlopen(Request(url, headers=UA), timeout=timeout) as r:
                return r.read().decode("utf-8")
        except Exception:
            if i < tries - 1:
                time.sleep(1.0)
    return None


def download(info=None, progress=None, tries=3):
    """下 app 包并校验 sha256。校验不过 → 删掉，返回 error（绝不把坏包留在盘上）。
    多源 + 重试：每个源（GitHub 直链、国内镜像）各试几次；国内连 GitHub 易超时，镜像兜底。"""
    info = info or check()
    if not info.get("has_update"):
        return {"ok": False, "error": "没有可用更新"}

    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    dst = UPDATE_DIR / f"app-{info['latest']}.zip"
    filename = f"paper-piggy-app-{info['latest']}.zip"

    # 期望的 sha256（GitHub 直链 + 镜像都试）
    want = None
    for surl in _candidate_urls(info.get("sha256_url"), filename + ".sha256"):
        txt = _fetch_text(surl)
        if txt:
            want = txt.split()[0].strip().lower()
            break

    urls = _candidate_urls(info.get("url"), filename)
    if not urls:
        return {"ok": False, "error": "Release 里没有可下载的 app 包资产"}

    last_err = None
    for url in urls:
        src = "镜像" if url != info.get("url") else "GitHub"
        for i in range(tries):
            try:
                _fetch_to(url, dst, progress=progress, timeout=90)
                got = _sha256(dst)
                if want and got != want:
                    dst.unlink(missing_ok=True)
                    last_err = f"{src} sha256 不符（期望 {want[:12]}…，实际 {got[:12]}…）"
                    break                      # 校验失败换下一个源，别在同一坏源上重试
                return {"ok": True, "zip": str(dst), "version": info["latest"],
                        "sha256": got, "verified": bool(want), "source": src}
            except Exception as e:
                dst.unlink(missing_ok=True)
                last_err = f"{src}: {type(e).__name__}: {e}"
                if i < tries - 1:
                    time.sleep(2.0 * (i + 1))

    hint = "" if _mirror_base() else "（可在设置里填「国内镜像」兜底，见 README）"
    return {"ok": False, "error": f"下载失败{hint}：{last_err}"}


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
#
# ★★ 数据安全的全部保证都在这一节 ★★
# apply() **只操作 app\ 和它的临时兄弟目录**（.app.new / .app.old / 你改过的旧代码-<ver>）。
# 从头到尾**不引用** DATA、0_Agent交付物、0_Agent资料库、models\、python\、git\ ——
# 数据与程序同目录后，这条边界就是「升级不丢数据」的命根子，改这一节前先想清楚这句话。
#
# 与旧版的关键区别（旧版有「假回滚」bug）：
#   旧：备份 app\ → 就地 extractall 覆盖 live app\ → 失败时 rmtree(live)+move(backup)。
#       rmtree 删不干净（文件被占用）时，move 会把备份塞进残缺目录里，app\ 留在半坏状态。
#   新：新版**先解压到暂存目录并验证能启动**，全程不碰 live app\；确认没问题后才做
#       **两次改名**交换（同卷 rename，快且原子）。回滚 = 一次改名，最稳。

def _pid_alive(pid):
    r"""进程还活着吗 —— **非破坏性**探测。
    ⚠️ 绝不能用 os.kill(pid, 0)：在 Windows 上 Python 的 os.kill 对非 CTRL 信号一律
       调 TerminateProcess **强杀**目标（signal 0 也不例外）—— 那会在升级时把 launcher
       打死，而不是"等它自己退"。所以用 OpenProcess + GetExitCodeProcess 只读地查。"""
    if sys.platform == "win32":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not h:
                return False                       # 打不开句柄 → 当作已退出
            code = ctypes.c_ulong()
            k32.GetExitCodeProcess(h, ctypes.byref(code))
            k32.CloseHandle(h)
            return code.value == 259               # STILL_ACTIVE
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _wait_pid_exit(pid, timeout=60):
    r"""等主进程退出。app\ 里的 .py 被占用时改名会失败。"""
    if not pid:
        return True
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not _pid_alive(pid):
            return True
        time.sleep(0.5)
    return False


def _writable(d):
    try:
        d.mkdir(parents=True, exist_ok=True)
        t = d / ".w"
        t.write_text("x", encoding="utf-8"); t.unlink()
        return True
    except Exception:
        return False


def _rename_retry(src, dst, tries=20, wait=0.5):
    r"""改名，带重试。杀软/OneDrive/刚退出的进程可能还攥着 app\ 里的文件句柄，
    第一下 rename 会 PermissionError；等一会儿句柄释放就成了。20×0.5s = 最多等 10s。"""
    last = None
    for _ in range(tries):
        try:
            os.rename(src, dst)
            return
        except OSError as e:
            last = e
            time.sleep(wait)
    raise last


# ★ 无窗铁律（CLAUDE.md §0.5）：更新链路里的每个子进程都必须无控制台窗口，
#   否则升级时会闪黑窗。用包内 pythonw.exe（GUI 子系统、无控制台）+ CREATE_NO_WINDOW 双保险。
_NO_WINDOW = C.SUBPROC_NO_WINDOW   # CREATE_NO_WINDOW；与其它子进程共用 config 单一事实源


def _pyw():
    """无窗解释器：优先包内 pythonw.exe，回退当前解释器（源码态可能是 python.exe，但那不发布）。"""
    p = BUNDLE_DIR / "python" / "pythonw.exe"
    return str(p) if p.exists() else sys.executable


def _importable(app_dir):
    """在给定 app 目录里能不能 import config 并读出版本 —— 换代码前后都用它把关。"""
    r = subprocess.run(
        [_pyw(), "-c",
         "import sys; sys.path.insert(0, r'%s'); import config; print(config.APP_VERSION)" % app_dir],
        capture_output=True, text=True, timeout=60,
        creationflags=_NO_WINDOW,        # ★ 不闪黑窗
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "")[-500:])
    return (r.stdout or "").strip()


def _relaunch():
    r"""换完代码后把应用重新拉起来（分发包里 = pythonw run_localkb.py，无黑窗）。"""
    try:
        run = BUNDLE_DIR / "run_localkb.py"
        child = subprocess.Popen([_pyw(), str(run)], cwd=str(BUNDLE_DIR),
                                 creationflags=_NO_WINDOW, close_fds=True,
                                 stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)   # ★ 不闪黑窗
        _log("已拉起应用", {"pid": child.pid, "run": str(run)})
        return True
    except Exception as e:
        _log("自动重启失败", {"error": repr(e), "run": str(BUNDLE_DIR / "run_localkb.py")})
        return False


def apply(zip_path, pid=None, relaunch=True):
    r"""把新版 app\ 换上去。**只碰 app\**，数据一律不动。失败必回滚，且回滚是真的。

    流程：等主进程退出 → 解压新版到暂存区并验证能启动（不碰 live）→ 保留用户改过的代码
    → 两次改名交换 app\ → 落地再验一次 → 成功清理并重启 / 失败一次改名回滚。
    """
    zip_path = Path(zip_path)
    if not zip_path.exists():
        return {"ok": False, "error": f"更新包不存在：{zip_path}"}
    if not _wait_pid_exit(pid):
        return {"ok": False, "error": f"主进程（PID {pid}）60 秒内没退出，放弃更新（未做任何改动）"}

    old_ver = getattr(C, "APP_VERSION", "0")
    parent = BUNDLE_DIR
    if not _writable(parent):
        return {"ok": False, "error": f"安装目录不可写（{parent}），无法更新（未做任何改动）"}

    staging = parent / ".app.new"
    old_keep = parent / f".app.old-{old_ver}"
    for tmp in (staging, old_keep):
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)

    # ① 解压新版到暂存区（全新目录）——失败/坏包都不碰 live app\
    try:
        with zipfile.ZipFile(zip_path) as z:
            bad = z.testzip()
            if bad:
                raise RuntimeError(f"更新包损坏（首个坏文件：{bad}）")
            z.extractall(staging)
    except Exception as e:
        shutil.rmtree(staging, ignore_errors=True)
        return {"ok": False, "error": f"更新包解压失败，未改动应用：{e}"}

    # app 增量包不会更新同级 python/。新版若需要不同运行环境，必须在碰 live app 前挡住，
    # 否则会出现「代码更新成功、启动才发现缺依赖」的半升级状态。
    try:
        target_meta = json.loads((staging / "version.json").read_text(encoding="utf-8"))
        expected_runtime = str(target_meta.get("runtime_fingerprint") or "").strip()
    except Exception:
        expected_runtime = ""              # 兼容旧更新包：没有指纹时仍走既有 import 验证
    runtime_file = parent / "python" / ".paperpiggy-runtime.sha256"
    try:
        actual_runtime = runtime_file.read_text(encoding="utf-8").strip()
    except Exception:
        actual_runtime = ""
    if expected_runtime and expected_runtime != actual_runtime:
        shutil.rmtree(staging, ignore_errors=True)
        return {"ok": False,
                "error": "新版需要同步更新运行环境，应用内增量更新已安全停止、未改动应用。"
                         "请下载最新版完整安装器覆盖安装（你的数据和 Agent 资料库不会被删除）。"}

    # ② 在暂存区就把关：新版能不能 import 起来。**这一步在碰 live app\ 之前**，
    #    所以「新版需要装新依赖 / 代码有语法错」都会在这里被挡下、应用毫发无伤。
    try:
        new_ver = _importable(staging)
    except Exception as e:
        shutil.rmtree(staging, ignore_errors=True)
        return {"ok": False,
                "error": f"新版无法在本机启动（可能需要新依赖），已放弃、未改动应用。"
                         f"请到 GitHub 下载完整安装器升级。详情：{e}"}

    # ③ 用户改过的 .py：复制到一个清晰命名、成功后也不删的文件夹，绝不让改动无声消失
    mods = local_modifications()
    kept_dir = None
    if mods:
        kept_dir = parent / f"你改过的旧代码-{old_ver}"
        for rel in mods:
            try:
                dst = kept_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(APP_DIR / rel, dst)
            except Exception:
                pass

    # ④ 交换：两次改名（同卷、瞬间）。这是唯一真正动 live app\ 的窗口，只有毫秒级。
    try:
        _rename_retry(APP_DIR, old_keep)          # app\ → .app.old（旧版让位）
    except Exception as e:
        shutil.rmtree(staging, ignore_errors=True)
        return {"ok": False,
                "error": f"移走旧 app\\ 失败（可能有进程仍占用），已放弃、未改动应用：{e}"}
    try:
        _rename_retry(staging, APP_DIR)           # .app.new → app\（新版就位）
    except Exception as e:
        # 新版没放上去 → 把旧版改名回来（一次 rename，最稳的回滚）
        try:
            _rename_retry(old_keep, APP_DIR)
        except Exception:
            pass
        shutil.rmtree(staging, ignore_errors=True)
        return {"ok": False, "error": f"放置新版失败，已回滚到 {old_ver}：{e}"}

    # ⑤ 落地再验一次（暂存验过了，这是双保险，防跨目录后路径/权限异常）
    try:
        new_ver = _importable(APP_DIR)
    except Exception as e:
        # 回滚：把新版挪走、旧版改名回来
        try:
            _rename_retry(APP_DIR, parent / ".app.failed")
            _rename_retry(old_keep, APP_DIR)
        except Exception:
            pass
        shutil.rmtree(parent / ".app.failed", ignore_errors=True)
        return {"ok": False, "error": f"新版落地后仍无法启动，已回滚到 {old_ver}：{e}"}

    # ⑥ 成功：旧版可删（用户改过的代码已另存在「你改过的旧代码-<ver>\」），删更新包
    shutil.rmtree(old_keep, ignore_errors=True)
    zip_path.unlink(missing_ok=True)

    result = {"ok": True, "from": old_ver, "to": new_ver}
    if kept_dir:
        result["user_modified_kept"] = str(kept_dir)
    if relaunch:
        result["relaunched"] = _relaunch()
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--apply", metavar="ZIP", nargs="?", const="")
    ap.add_argument("--pid", type=int, default=0, help="等这个进程退出后再替换 app\\")
    ap.add_argument("--no-relaunch", action="store_true", help="换完不自动重启应用（测试用）")
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
                result = {"ok": False, "error": "update/ 里没有待应用的包"}
            else:
                z = cands[-1]
                result = None
        else:
            result = None

        if result is None:
            _log("开始应用更新", {"zip": str(z), "wait_pid": a.pid})
            try:
                result = apply(z, a.pid, relaunch=not a.no_relaunch)
            except Exception as e:
                result = {"ok": False, "error": f"更新器异常：{e}", "traceback": traceback.format_exc()[-4000:]}

        if not result.get("ok"):
            # apply 的所有正常失败路径都承诺未改动或已回滚；主应用已经退出，必须把旧版重新拉起。
            if not a.no_relaunch:
                result["old_relaunched"] = _relaunch()
            _log("应用更新失败", result)
            restart = "旧版已重新打开。" if result.get("old_relaunched") else "旧版自动重启失败，请手动重新打开 PaperPiggy。"
            _notify("PaperPiggy 升级失败",
                    f"{result.get('error') or '未知错误'}\n\n{restart}\n\n诊断日志：{UPDATE_LOG}")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1

        _log("应用更新完成", result)
        if not a.no_relaunch and not result.get("relaunched"):
            _notify("PaperPiggy 已升级，但未能自动重启",
                    f"新版已经安装完成，请手动重新打开 PaperPiggy。\n\n诊断日志：{UPDATE_LOG}")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

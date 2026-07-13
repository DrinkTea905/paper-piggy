# -*- coding: utf-8 -*-
"""综合层版本历史 —— 让人敢把写权交给 agent。

gist：「The wiki is just a git repo of markdown files. You get version history,
branching, and collaboration for free.」——但那是对开发者说的。本应用最终打包成 exe
给法学研究者用，**他们的机器上大概率没有 git**。所以：

- 有 git（开发机）→ 真 git 仓库，可 diff / 可 log / 可回滚到任意版本。
- 无 git（分发版常态）→ 自动退回 `.history/<page_id>/<时间戳>.md` 快照，保留最近 N 份。

两种后端同一套接口（snapshot / history / restore / diff），上层不必关心用的哪个。
任何一步失败都只记日志、**绝不阻塞 wiki 写入**——版本历史是安全网，不是关卡。

只版本化 `.md`：每页的 frontmatter 已自足到能重建 index.json（见 wiki_store._rebuild_index_from_disk），
所以 index.json 不入库，省掉每次写入都变更一个大 JSON 的提交噪声。
"""
import sys, os, re, time, shutil, subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C

KEEP_SNAPSHOTS = 20          # 无 git 时每页保留的历史份数
_GIT = {"checked": False, "exe": None, "source": "?"}   # source: bundle/env/system/none

# 自动提交用的身份。不读用户全局 git 配置——分发版机器多半没配 user.email，
# 没有它 `git commit` 会直接失败。gpgsign 同理关掉：这是应用自己的数据目录，
# 不该因为用户全局开了签名而让 wiki 存不进历史。
_GIT_ID = ["-c", "user.name=PaperPiggy", "-c", "user.email=paperpiggy@localhost",
           "-c", "commit.gpgsign=false"]


def log(*a):
    print("[wiki-vcs]", *a, file=sys.stderr, flush=True)


# ═══ git 探测 ═══════════════════════════════════════════════════
def _verify(exe):
    """确认这个 git 真能跑（存在≠可执行）。"""
    if not exe:
        return None
    try:
        subprocess.run([exe, "--version"], capture_output=True, timeout=5, check=True,
                       creationflags=(0x08000000 if sys.platform == "win32" else 0))
        return exe
    except Exception:
        return None


def _find_git():
    """查 git，优先级：分发包自带的 MinGit > 环境变量 > 系统 PATH。
       分发版（exe）机器上多半没装 git，所以安装包自带一份 MinGit（见 build_bundle.py），
       放在 <bundle>/git/ 下。开发机没有这个目录，自动走系统 git。"""
    # 1) 包内自带 MinGit：C.APP 是 app/，其上级即 bundle 根（LocalKB/）
    try:
        bundle = C.APP.parent
        for rel in ("git/cmd/git.exe", "git/bin/git.exe", "git/mingw64/bin/git.exe"):
            p = bundle / rel
            if p.exists():
                got = _verify(str(p))
                if got:
                    _GIT["source"] = "bundle"
                    return got
    except Exception:
        pass
    # 2) 环境变量显式指定
    env = os.environ.get("LOCALKB_GIT")
    if env and _verify(env):
        _GIT["source"] = "env"
        return env
    # 3) 系统 PATH（开发机的常态）
    got = _verify(shutil.which("git"))
    if got:
        _GIT["source"] = "system"
        return got
    _GIT["source"] = "none"
    return None


def git_exe():
    if not _GIT["checked"]:
        _GIT["checked"] = True
        _GIT["exe"] = _find_git()
    return _GIT["exe"]


def _git(*args, check=False, timeout=20):
    exe = git_exe()
    if not exe:
        raise RuntimeError("git 不可用")
    p = subprocess.run([exe, "-C", str(C.WIKI_DIR)] + list(args),
                       capture_output=True, timeout=timeout,
                       creationflags=(0x08000000 if sys.platform == "win32" else 0))  # 不弹黑窗
    out = (p.stdout or b"").decode("utf-8", "replace")
    err = (p.stderr or b"").decode("utf-8", "replace")
    if check and p.returncode != 0:
        raise RuntimeError(err.strip() or f"git {args[0]} 失败（rc={p.returncode}）")
    return p.returncode, out, err


def _is_repo():
    return (C.WIKI_DIR / ".git").exists()


GITIGNORE = """# index.json 可由各 .md 的 frontmatter 完整重建，不入库（否则每次写入都产生一次大 JSON 变更）
index.json
index.corrupt-*.json
# 无 git 时的快照兜底，与 git 互斥
.history/
"""


def ensure_repo():
    """有 git 就把 data/wiki 变成仓库（幂等）。没有 git 返回 False，上层自动走快照后端。"""
    if not git_exe():
        return False
    if _is_repo():
        return True
    try:
        C.WIKI_DIR.mkdir(parents=True, exist_ok=True)
        _git("init", "-q", check=True)
        (C.WIKI_DIR / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
        _git("add", "-A", check=True)
        rc, _, err = _git(*_GIT_ID, "commit", "-q", "-m", "初始化综合层版本历史")
        if rc != 0 and "nothing to commit" not in err:
            log("初始提交失败：", err.strip())
        log(f"已在 {C.WIKI_DIR} 建立 git 仓库")
        return True
    except Exception as e:
        log("git init 失败，退回快照模式：", e)
        return False


def backend():
    """'git' 或 'snapshot'。探测一次即可，git 装没装不会中途变。"""
    return "git" if (git_exe() and (_is_repo() or ensure_repo())) else "snapshot"


def status():
    return {"backend": backend(), "git_available": bool(git_exe()), "git_source": _GIT.get("source"),
            "repo": _is_repo(), "dir": str(C.WIKI_DIR)}


# ═══ 快照后端（无 git）══════════════════════════════════════════
def _snap_dir(page_id):
    return C.WIKI_HISTORY_DIR / re.sub(r'[\\/:*?"<>|]+', "_", page_id)


def _snap_save(page_id, path, message):
    d = _snap_dir(page_id)
    d.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = d / f"{ts}.md"
    i = 0
    while dst.exists():                      # 同一秒内多次写入
        i += 1
        dst = d / f"{ts}-{i}.md"
    shutil.copy2(path, dst)
    (d / f"{dst.stem}.msg").write_text(message or "", encoding="utf-8")
    snaps = sorted(d.glob("*.md"))
    for old in snaps[:-KEEP_SNAPSHOTS]:       # 只留最近 N 份
        try:
            old.unlink()
            (d / f"{old.stem}.msg").unlink(missing_ok=True)
        except Exception:
            pass
    return dst.stem


def _snap_history(page_id):
    d = _snap_dir(page_id)
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("*.md"), reverse=True):
        msg = ""
        m = d / f"{f.stem}.msg"
        if m.exists():
            try:
                msg = m.read_text(encoding="utf-8").strip()
            except Exception:
                pass
        out.append({"rev": f.stem, "ts": int(f.stat().st_mtime), "message": msg})
    return out


def _snap_read(page_id, rev):
    f = _snap_dir(page_id) / f"{rev}.md"
    if not f.exists():
        raise ValueError(f"无此版本：{rev}")
    return f.read_text(encoding="utf-8")


# ═══ 统一接口 ═══════════════════════════════════════════════════
def _git_commit_all(message):
    """提交**所有**挂起变更，而不是只提交某个 pathspec。

    只提交单个文件的话，删除页、WIKI.md 升级这类变更永远进不了库：
    工作区会一直脏，被删的 .md 还留在 git 索引里，用户一 checkout 就复活。"""
    _git("add", "-A")
    rc, _, err = _git(*_GIT_ID, "commit", "-q", "-m", message)
    if rc != 0 and "nothing to commit" not in err and "no changes added" not in err:
        log("提交失败：", err.strip())
        return None
    _, out, _ = _git("rev-parse", "--short", "HEAD")
    return out.strip() or None


def snapshot(page_id, path, message=""):
    """记录一版。path 是该页 .md 的绝对路径。失败只记日志，绝不抛给写入路径。"""
    try:
        path = Path(path)
        if not path.exists():
            return None
        if backend() == "git":
            return _git_commit_all(f"{page_id}: {message}")
        return _snap_save(page_id, path, message)
    except Exception as e:
        log(f"记录 {page_id} 版本失败（不影响写入）：", e)
        return None


def record_delete(page_id, message="删除该页"):
    """页被删除后提交这次删除（git 后端）。快照后端无需处理——历史快照本就独立留存。"""
    try:
        if backend() == "git":
            return _git_commit_all(f"{page_id}: {message}")
    except Exception as e:
        log(f"记录删除 {page_id} 失败：", e)
    return None


def commit(message):
    """提交当前所有挂起变更（如 WIKI.md 升级）。快照后端为空操作。"""
    try:
        if backend() == "git":
            return _git_commit_all(message)
    except Exception as e:
        log("提交失败：", e)
    return None


def history(page_id, kind_dir_name=None, limit=30):
    """返回 [{rev, ts, message}]，新的在前。"""
    try:
        if backend() == "git":
            rel = f"{kind_dir_name}/{page_id}.md" if kind_dir_name else None
            args = ["log", f"-{limit}", "--format=%h\t%ct\t%s"]
            if rel:
                args += ["--follow", "--", rel]
            rc, out, _ = _git(*args)
            if rc != 0:
                return []
            rows = []
            for ln in out.strip().splitlines():
                parts = ln.split("\t", 2)
                if len(parts) == 3:
                    msg = parts[2]
                    if msg.startswith(page_id + ": "):
                        msg = msg[len(page_id) + 2:]
                    rows.append({"rev": parts[0], "ts": int(parts[1]), "message": msg})
            return rows
        return _snap_history(page_id)[:limit]
    except Exception as e:
        log(f"读 {page_id} 历史失败：", e)
        return []


def _fmt_ts(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts)))


def log_events(limit=100):
    """EN-W3：全库时间线——最近的 wiki 变更事件（新的在前），给前端「综合层在长什么」一条脉络。

    返回 {"events":[{"time","page_id","action","by_agent"}], "source":"git"|"history"}。
    - git 后端：解析 git log。提交信息格式是「page_id: 动作」（见 snapshot/_git_commit_all）；
      提交**作者恒为 PaperPiggy**（_GIT_ID 写死，避免分发机没配 user.email），所以没法从作者
      区分人/agent——但 wiki_store._snapshot 的动作文案是「agent写入 …」/「人写入 …」，
      据动作前缀判 by_agent。无前缀的系统提交（init / WIKI.md 升级）page_id 给空串。
    - 无 git：退回 .history/<page_id>/<时间戳>.md 快照的 mtime + 同名 .msg 文案。
    任何一步失败都退空列表，绝不抛给上层——时间线是展示件，不是关卡。"""
    limit = max(1, int(limit or 100))
    try:
        if backend() == "git":
            rc, out, _ = _git("log", f"-{limit}", "--format=%ct\t%s")
            if rc == 0:
                events = []
                for ln in out.strip().splitlines():
                    parts = ln.split("\t", 1)
                    if len(parts) != 2:
                        continue
                    try:
                        t = _fmt_ts(parts[0])
                    except Exception:
                        continue
                    subj = parts[1].strip()
                    pid, sep, act = subj.partition(": ")
                    if not sep:                    # 系统提交（初始化/WIKI.md 升级）没有页前缀
                        pid, act = "", subj
                    events.append({"time": t, "page_id": pid, "action": act,
                                   "by_agent": act.strip().startswith("agent")})
                return {"events": events, "source": "git"}
            log("git log 失败（退快照目录）：rc=", rc)
    except Exception as e:
        log("读 git 时间线失败（退快照目录）：", e)

    # 快照兜底：每份 <ts>.md 就是一次事件，动作取旁边的 .msg
    events = []
    try:
        hd = C.WIKI_HISTORY_DIR
        if hd.exists():
            for d in hd.iterdir():
                if not d.is_dir():
                    continue
                for f in d.glob("*.md"):
                    msg = ""
                    m = d / f"{f.stem}.msg"
                    if m.exists():
                        try:
                            msg = m.read_text(encoding="utf-8").strip()
                        except Exception:
                            pass
                    try:
                        t = _fmt_ts(f.stat().st_mtime)
                    except Exception:
                        continue
                    events.append({"time": t, "page_id": d.name, "action": msg or "写入",
                                   "by_agent": msg.strip().startswith("agent")})
    except Exception as e:
        log("读快照时间线失败：", e)
    events.sort(key=lambda ev: ev["time"], reverse=True)
    return {"events": events[:limit], "source": "history"}


def read_at(page_id, rev, kind_dir_name=None):
    """取某版本的 .md 全文（含 frontmatter）。"""
    if backend() == "git":
        rel = f"{kind_dir_name}/{page_id}.md"
        rc, out, err = _git("show", f"{rev}:{rel}")
        if rc != 0:
            raise ValueError(f"无此版本或该版本里没有这一页：{rev}")
        return out
    return _snap_read(page_id, rev)

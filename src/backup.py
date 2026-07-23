# -*- coding: utf-8 -*-
r"""备份与恢复 —— 把「丢了就再也没有的东西」打包成一个 zip。

【为什么是 zip，而不是把 data\ 目录丢进 OneDrive 同步】
  lancedb 是**持续读写的数据库**：一堆会被反复改写、合并（compaction）的数据文件 + 一个
  记录「当前哪些文件有效」的 manifest。云盘实时同步它，会在同步时锁文件、在冲突时造出
  `xxx-DESKTOP-ABC.lance` 这样的重复副本 —— 而数据库的 manifest 里根本没有那个文件名。
  轻则写入失败，重则索引损坏。数据库和云同步天生互相打架。
  zip 不一样：**写完就不再动**。云盘同步一个静态 zip，跟同步一份 Word 文档一样安全。
  所以「云备份」的正确形态是：应用打包成 zip → 放进用户指定的目录（那个目录可以就在
  OneDrive 里）→ 云盘去同步这个 zip。用户既拿到云备份，又完全绕开数据库损坏。

【三类数据，分层处理 —— 这是本模块的全部设计】
  ① 必备（几 MB）：不可再生、或者**再生要花钱**
     · 人写的：wiki/（综述页 + 版本历史）、0_Agent交付物/（写好的论文）、
       0_Agent资料库/（项目记忆·技能·定时任务）、categories/（收藏夹）
     · 花过 API 钱的：summaries/（每篇 ~150 字的 SAC 检索摘要）、
       grading_memo.json（689 条期刊分级的 LLM 结果）—— **重跑一次要再付一次钱**
     · 重建代价高的：meta/（papers.jsonl）、pagemap/（要重解析 PDF）
     · settings.json（默认剥掉 API key，见下）
  ② 贵重派生（可选，几个 G）：能重建，但 1400 篇要跑几小时、或者再花一次嵌入的钱
     lancedb/（向量库）、bm25/、bm25_meta/、chunks/、extracted/、state/
     ⚠️ 这几个**必须一起备份、一起恢复**：state/embedded_keys.txt 记的是「哪些篇已嵌入」，
        和 lancedb 的内容一一对应。只恢复其中一个，增量索引就会算错该补哪些篇。
  ③ 廉价派生（永不备份）
     models/（1~2G，重新下就是了）、logs/、stats_cache.json、
     jieba_legal_dict.txt（extract 会重新生成）、backups/（别套娃）

【API key】
  settings.json 里有三处 key（api / sac / folder_meta）。备份包是要放进云盘、甚至可能
  发给别人或存 U 盘的，所以**默认剥掉**。用户显式勾选「包含 API 密钥」才写进去，
  且 manifest 会标记 has_api_key=true（恢复时会提示）。
"""
import sys, os, json, time, shutil, zipfile, platform, stat
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).parent))
import config as C

# 备份包格式版本。**不兼容变更时 +1**，恢复端据此拒绝「新包 + 老应用」的组合。
FORMAT = 1

# settings.json 里的密钥字段（剥离用）。加了新的 key 字段务必同步这里，否则会泄进备份包。
_KEY_FIELDS = (("api", "key"), ("sac", "key"), ("folder_meta", "key"))

# ① 必备 —— 相对 C.DATA
# ⚠️ 改这个清单前，先把 config.py 的目录定义（尤其 :174 那段 mkdir 清单）从头到尾看一遍，
#    再 `ls` 一下真实的 data/ 目录对照。**别凭印象列**——第一版就是这么漏掉 grading_memo.json
#    和 summaries/ 的（两个都是花过 API 钱的东西，而清单里却写了一个根本不存在的
#    journal_tiers.json）。是实机跑了一次备份、去数产物才发现的。
CORE_IN_DATA = [
    "wiki",                  # 综述页 + index.json + WIKI.md + .history/.git 版本历史
    "categories",            # 收藏夹 / AI 主题
    "meta",                  # papers.jsonl：全库文献元数据
    "pagemap",               # PDF 顺序页 → 印刷页码映射（重建要重解析 PDF）
    "folder",                # 文件夹模式 sidecar
    "summaries",             # ★ SAC 检索摘要（每篇 ~150 字，LLM 生成）—— **花过 API 钱**
    "grading_memo.json",     # ★ 689 条期刊分级的 LLM 结果 —— **花过真钱**，重跑要再花一次
    "tier_overrides.json",   # ★ 用户一条条**手动改**的期刊档位（source_rules.set_override）—— 纯人工
    "grading_dist.json",     # 分级分布（小，顺手带上）
    "grading_mappings.json", # ★ 用户按学科调整的目录/文献性质四档映射
    "journal_tiers.json",    # 期刊权重表（可能不存在，不存在就跳过）
    "legal_synonyms.txt",    # 法学同义词（用户可自定义）
    "index_manifest.json",   # 整库状态清单
]
# ① 必备 —— 相对 C.DATA.parent（0_Agent* 落在数据家根，不在 data/ 里面）
CORE_IN_HOME = [C.AGENT_OUTPUT_NAME, C.AGENT_RELY_NAME,
                "AGENTS.md", "CLAUDE.md"]   # Agent 工作流强入口（用户可定制）

# ② 贵重派生（可选）—— 相对 C.DATA。见文件头：这几个必须同进同出。
INDEX_IN_DATA = ["lancedb", "bm25", "bm25_meta", "chunks", "extracted", "state"]

# ③ 明确「永不备份」。这个清单不是注释，是**给 check_guides.py 用的**：
#    它会扫全 src 里所有 `C.DATA / "xxx"` 的落点，断言每一个都被分到了
#    CORE / INDEX / NEVER / SPECIAL 四类之一。新加一个数据文件却忘了分类 → 构建期直接失败。
#    （这条护栏不是多余的：第一版清单是凭印象列的，漏了 grading_memo.json、summaries/、
#      tier_overrides.json 三样 —— 前两个花过 API 钱，第三个是用户手动改的。全是这么发现的。）
NEVER_IN_DATA = [
    "logs",                  # 日志
    "backups",               # 备份自己（别套娃）
    "stats_cache.json",      # 仪表盘预聚合缓存，可重算
    "jieba_legal_dict.txt",  # extract 会重新生成
    "PaperPiggy.ico",        # launcher 从图标真源生成的多尺寸窗口/任务栏图标
]
# ④ 特殊：不是「拷文件」，而是读出来剥掉 API key 再写进包（见 _sanitized_settings）
SPECIAL_IN_DATA = ["settings.json"]

_SKIP_NAMES = {"__pycache__", ".DS_Store", "Thumbs.db"}
_SKIP_SUFFIX = (".tmp", ".lock")


def _is_link_or_reparse(path):
    """备份绝不跟随 symlink、junction 或其他 Windows reparse point。"""
    try:
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode):
            return True
        return bool(getattr(st, "st_file_attributes", 0) & 0x400)
    except OSError:
        return False


def _declared_archive_roots(include_index=False):
    """返回备份格式允许出现的归档根；bool 表示该根是否可含子文件。"""
    roots = {}
    for name in CORE_IN_DATA + (INDEX_IN_DATA if include_index else []):
        roots[f"data/{name}"] = not name.lower().endswith((".json", ".txt"))
    for name in CORE_IN_HOME:
        roots[f"home/{name}"] = not name.lower().endswith(".md")
    roots["data/settings.json"] = False
    return roots


def _normalized_archive_name(name):
    """把 zip 成员规范成安全的 POSIX 相对路径；非法路径返回 None。"""
    raw = str(name or "")
    if not raw or "\\" in raw or "\x00" in raw or raw.startswith("/"):
        return None
    clean = raw.rstrip("/")
    p = PurePosixPath(clean)
    if not clean or any(part in ("", ".", "..") for part in p.parts):
        return None
    if p.as_posix() != clean:
        return None
    return clean


def _archive_name_allowed(name, include_index=False):
    clean = _normalized_archive_name(name)
    if clean == "manifest.json":
        return True
    if clean is None:
        return False
    for root, is_dir in _declared_archive_roots(include_index).items():
        if clean == root or (is_dir and clean.startswith(root + "/")):
            return True
    return False


def _zipinfo_is_link(info):
    """拒绝 Unix symlink 等特殊成员；备份只应含普通文件/目录。"""
    mode = (int(info.external_attr) >> 16) & 0xFFFF
    file_type = stat.S_IFMT(mode)
    # zipfile.writestr 常只写 0o600 权限位而不写文件类型（file_type=0），应视为普通文件。
    return file_type not in (0, stat.S_IFREG, stat.S_IFDIR)


def _validate_zip(zip_path, verify_crc=True):
    """完整校验备份包结构、成员白名单、数量与 CRC，不落盘。"""
    try:
        with zipfile.ZipFile(zip_path) as z:
            infos = z.infolist()
            names = [i.filename for i in infos]
            if len(names) != len(set(names)):
                raise ValueError("备份包含重复路径")
            try:
                manifest = json.loads(z.read("manifest.json").decode("utf-8"))
            except KeyError as e:
                raise ValueError("备份包缺少 manifest.json") from e
            fmt = int(manifest.get("format", 0))
            if fmt < 1:
                raise ValueError("备份包格式号无效")
            if fmt > FORMAT:
                return {"ok": False, "manifest": manifest,
                        "err": f"这个备份包来自更新版本的应用（包格式 v{fmt}，"
                               f"本应用只认到 v{FORMAT}）。请先升级 PaperPiggy 再恢复。"}
            include_index = bool(manifest.get("includes_index"))
            payload = []
            for info in infos:
                if not _archive_name_allowed(info.filename, include_index):
                    raise ValueError(f"备份包含未声明或不安全的路径：{info.filename}")
                if _zipinfo_is_link(info):
                    raise ValueError(f"备份包含链接或特殊文件：{info.filename}")
                if info.filename != "manifest.json" and not info.is_dir():
                    payload.append(info)
            source_payload = [i for i in payload if i.filename != "data/settings.json"]
            if int(manifest.get("n_files", -1)) != len(source_payload):
                raise ValueError(
                    f"备份文件数与 manifest 不一致：声明 {manifest.get('n_files')}，实际 {len(source_payload)}")
            if not payload:
                raise ValueError("备份包没有任何可恢复内容")
            if verify_crc:
                bad = z.testzip()
                if bad:
                    raise ValueError(f"备份包校验失败：{bad}")
            return {"ok": True, "manifest": manifest, "names": names,
                    "n_entries": len(names),
                    "uncompressed_size": sum(int(i.file_size) for i in payload)}
    except Exception as e:
        return {"ok": False, "err": f"不是有效的备份包（或已损坏）：{e}"}


def home_dir():
    return C.DATA.parent


def backup_dir():
    """备份包放哪。用户可在设置里指到任意目录 —— 包括 OneDrive 里的某个文件夹。"""
    try:
        import settings as S
        d = (S.load().get("backup") or {}).get("dir") or ""
    except Exception:
        d = ""
    return Path(d) if d else (C.DATA / "backups")


def _iter_files(root):
    """遍历一个目录下要备份的文件（跳过垃圾与临时文件）。root 不存在则不产出。"""
    if not root.exists():
        return
    if _is_link_or_reparse(root):
        raise RuntimeError(f"拒绝备份链接或重解析点：{root}")
    if root.is_file():
        yield root
        return
    for p in root.rglob("*"):
        if _is_link_or_reparse(p):
            raise RuntimeError(f"拒绝备份链接或重解析点：{p}")
        if p.is_dir():
            continue
        if any(part in _SKIP_NAMES for part in p.parts):
            continue
        if p.name.endswith(_SKIP_SUFFIX):
            continue
        yield p


def _sanitized_settings(include_key):
    """settings.json 的内容（不是直接拷文件——要按需剥掉 API key）。返回 (json 文本, 是否含 key)。"""
    try:
        import settings as S
        raw = S.load()
    except Exception:
        return None, False
    data = json.loads(json.dumps(raw))          # 深拷贝，绝不动内存里的真设置
    has_key = False
    if include_key:
        for sec, fld in _KEY_FIELDS:
            if (data.get(sec) or {}).get(fld):
                has_key = True
    else:
        for sec, fld in _KEY_FIELDS:
            if isinstance(data.get(sec), dict) and data[sec].get(fld):
                data[sec][fld] = ""             # 剥掉
    return json.dumps(data, ensure_ascii=False, indent=2), has_key


def _counts():
    """给 manifest 用的内容概览（让用户在恢复前知道这个包里有什么）。"""
    out = {}
    try:
        wiki = C.DATA / "wiki"
        out["wiki_pages"] = len(list(wiki.glob("*.md"))) if wiki.exists() else 0
    except Exception:
        pass
    try:
        pj = C.DATA / "meta" / "papers.jsonl"
        out["papers"] = sum(1 for _ in pj.open("r", encoding="utf-8")) if pj.exists() else 0
    except Exception:
        pass
    try:
        od = home_dir() / C.AGENT_OUTPUT_NAME
        out["agent_outputs"] = len([p for p in od.iterdir() if p.is_dir()]) if od.exists() else 0
    except Exception:
        pass
    return out


def estimate(include_index=False):
    """预估备份体积（字节）。前端用它在用户点之前就告知「这一下要写 3.2 GB」。"""
    total = 0
    roots = [C.DATA / n for n in CORE_IN_DATA] + [home_dir() / n for n in CORE_IN_HOME]
    if include_index:
        roots += [C.DATA / n for n in INDEX_IN_DATA]
    for r in roots:
        for f in _iter_files(r):
            try:
                total += f.stat().st_size
            except Exception:
                pass
    return total


class BackupCancelled(RuntimeError):
    """用户主动停止创建备份；调用方应把它当作正常取消，而不是备份失败。"""


def create(include_index=False, include_key=False, on_progress=None, should_cancel=None):
    """打一个备份包。返回 manifest dict（含 path/size）。

    ⚠️ 调用方**必须**先确认没有正在建索引（server 里查 BUILD["running"]）——
       数据库写到一半打包，会得到一个看着正常、实则损坏的副本。这是本模块最危险的坑，
       而它无法在这里自查（BUILD 是 server 的进程内状态）。
    """
    out_dir = backup_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    tag = "full" if include_index else "core"
    dst = out_dir / f"PaperPiggy-backup-{stamp}-{tag}.zip"
    tmp = dst.with_suffix(".zip.part")           # 先写 .part，成了才改名 —— 免得云盘同步半个包

    items = [(C.DATA / n, f"data/{n}") for n in CORE_IN_DATA] + \
            [(home_dir() / n, f"home/{n}") for n in CORE_IN_HOME]
    if include_index:
        items += [(C.DATA / n, f"data/{n}") for n in INDEX_IN_DATA]

    files = []
    for root, arc_root in items:
        for f in _iter_files(root):
            rel = f.name if root.is_file() else str(f.relative_to(root)).replace("\\", "/")
            arc = arc_root if root.is_file() else f"{arc_root}/{rel}"
            files.append((f, arc))

    settings_text, has_key = _sanitized_settings(include_key)

    manifest = {
        "format": FORMAT,
        "app_version": C.APP_VERSION,
        "wiki_schema": _wiki_schema(),
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "machine": platform.node(),
        "includes_index": bool(include_index),
        "has_api_key": bool(has_key),
        "counts": _counts(),
        "n_files": len(files),
    }

    def check_cancel():
        if should_cancel and should_cancel():
            raise BackupCancelled("备份已停止")

    done = 0
    try:
        check_cancel()
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            if settings_text is not None:
                z.writestr("data/settings.json", settings_text)   # 单独写：key 已按需剥离
            if on_progress:
                on_progress(0, len(files))
            for f, arc in files:
                check_cancel()
                try:
                    z.write(f, arc)
                except Exception as e:
                    # 备份最怕“看起来成功、恢复时才发现贵重文件没进去”。任何漏项都拒绝定稿。
                    raise RuntimeError(f"无法读取备份文件 {f}：{e}") from e
                done += 1
                if on_progress and (done % 10 == 0 or done == len(files)):
                    on_progress(done, len(files))
        check_cancel()
        verified = _validate_zip(tmp, verify_crc=True)
        if not verified.get("ok"):
            raise RuntimeError(verified.get("err") or "备份包完整性校验失败")
        check_cancel()
        tmp.replace(dst)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    manifest["path"] = str(dst)
    manifest["size"] = dst.stat().st_size
    _prune(out_dir)
    return manifest


def _wiki_schema():
    try:
        import wiki_store as W
        return getattr(W, "SCHEMA_VERSION", None)
    except Exception:
        return None


def _prune(out_dir):
    """只保留最近 N 份，免得把用户的云盘撑爆。"""
    try:
        import settings as S
        keep = int((S.load().get("backup") or {}).get("keep") or 3)
    except Exception:
        keep = 3
    if keep <= 0:
        return
    packs = sorted(out_dir.glob("PaperPiggy-backup-*.zip"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for p in packs[keep:]:
        try:
            p.unlink()
        except Exception:
            pass


def list_backups():
    """备份目录里现有的包（新的在前）。读每个包的 manifest，让用户知道里面是什么。"""
    out = []
    d = backup_dir()
    if not d.exists():
        return out
    for p in sorted(d.glob("PaperPiggy-backup-*.zip"),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        item = {"path": str(p), "name": p.name, "size": p.stat().st_size,
                "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime))}
        try:
            with zipfile.ZipFile(p) as z:
                item["manifest"] = json.loads(z.read("manifest.json").decode("utf-8"))
        except Exception as e:
            item["broken"] = repr(e)             # 损坏的包也列出来，别让用户以为它是好的
        out.append(item)
    return out


def inspect(zip_path):
    """恢复前先看看这个包：能不能用、里面有什么。不改任何东西。"""
    p = Path(zip_path)
    if not p.exists():
        return {"ok": False, "err": f"文件不存在：{p}"}
    checked = _validate_zip(p, verify_crc=True)
    if not checked.get("ok"):
        return checked
    m = checked["manifest"]

    warn = []
    cur_schema = _wiki_schema()
    if m.get("wiki_schema") is not None and cur_schema is not None \
            and m["wiki_schema"] != cur_schema:
        warn.append(f"备份包的 wiki 规约是 v{m['wiki_schema']}，当前应用是 v{cur_schema} —— "
                    f"恢复后综述页可能需要重新生成。")
    if m.get("has_api_key"):
        warn.append("这个包里**含有 API 密钥**，恢复后会覆盖当前设置里的密钥。")
    if not m.get("includes_index"):
        warn.append("这个包**不含向量索引**，恢复后需要重新建库（几十分钟到几小时）。")
    return {"ok": True, "manifest": m, "warnings": warn,
            "n_entries": checked["n_entries"],
            "uncompressed_size": checked["uncompressed_size"]}


def restore(zip_path):
    """从备份包恢复。

    ⚠️ 两条铁律：
      ① 调用方必须先确认没有在建索引（同 create()）。
      ② **恢复前把现有数据整体挪到一边**，不是删掉 —— 用户可能是手滑点的，
         或者选错了包。挪走的东西留在 <数据家>\\_restore_backup_<时间戳>\\，他能自己捞回来。
    恢复完必须**重启应用**：内存里的 LanceDB 句柄、wiki 索引、papers 缓存全是旧的。
    """
    info = inspect(zip_path)
    if not info.get("ok"):
        return info

    m = info["manifest"]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    stash = home_dir() / f"_restore_backup_{stamp}"
    staging = home_dir() / f"_restore_staging_{stamp}"

    # 先在全新 staging 中完整解压并校验，绝不边解压边覆盖 live 数据。
    try:
        if staging.exists() or stash.exists():
            return {"ok": False, "err": "同秒恢复目录已存在，请稍等一秒后重试"}
        free = shutil.disk_usage(home_dir()).free
        if int(info.get("uncompressed_size", 0)) > free:
            return {"ok": False, "err": "磁盘剩余空间不足，无法先完整展开备份包"}
        staging.mkdir(parents=True)
        stage_root = staging.resolve()
        with zipfile.ZipFile(zip_path) as z:
            for zi in z.infolist():
                name = _normalized_archive_name(zi.filename)
                if name == "manifest.json" or zi.is_dir():
                    continue
                parts = PurePosixPath(name).parts
                out = (staging / Path(*parts)).resolve()
                try:
                    out.relative_to(stage_root)
                except ValueError as e:
                    raise ValueError(f"恢复路径越界：{zi.filename}") from e
                out.parent.mkdir(parents=True, exist_ok=True)
                with z.open(zi) as fsrc, open(out, "wb") as fdst:
                    shutil.copyfileobj(fsrc, fdst)
    except Exception as e:
        return {"ok": False, "err": f"备份包预展开失败，现有数据未改动：{e}",
                "staging": str(staging)}

    # 这次恢复会覆盖哪些目录 —— 只挪这些，别动用户的其它东西
    targets = [(C.DATA / n, f"data/{n}") for n in CORE_IN_DATA] + \
              [(home_dir() / n, f"home/{n}") for n in CORE_IN_HOME] + \
              [(C.DATA / "settings.json", "data/settings.json")]
    if m.get("includes_index"):
        targets += [(C.DATA / n, f"data/{n}") for n in INDEX_IN_DATA]

    moved = []
    installed = []
    try:
        for src, arc in targets:
            if not src.exists():
                continue
            dst = stash / arc
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            moved.append((src, dst, arc))
    except Exception as e:
        # 挪到一半失败 → 把已挪走的搬回去，绝不留一个残缺的库
        for src, dst, arc in reversed(moved):
            try:
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dst), str(src))
            except Exception:
                pass
        return {"ok": False, "err": f"恢复前挪走现有数据时失败，已原样还原：{e}"}

    # staging 已完整；现在只按白名单目标做同卷移动。任何一步失败都把 live 回滚到原数据。
    try:
        for live, arc in targets:
            staged = staging / Path(*PurePosixPath(arc).parts)
            if not staged.exists():
                continue
            live.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged), str(live))
            installed.append((live, staged, arc))
    except Exception as e:
        rollback_errors = []
        failed_live = staging / "_failed_live"
        for live, _staged, arc in reversed(installed):
            try:
                if live.exists():
                    keep = failed_live / Path(*PurePosixPath(arc).parts)
                    keep.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(live), str(keep))
            except Exception as re:
                rollback_errors.append(f"暂存新数据 {arc}: {re}")
        for live, old, arc in reversed(moved):
            try:
                if old.exists():
                    live.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(old), str(live))
            except Exception as re:
                rollback_errors.append(f"还原旧数据 {arc}: {re}")
        if rollback_errors:
            return {"ok": False,
                    "err": f"恢复安装失败且自动回滚不完整：{e}；" + "；".join(rollback_errors),
                    "stash": str(stash), "staging": str(staging)}
        return {"ok": False, "err": f"恢复安装失败，现有数据已原样还原：{e}",
                "staging": str(staging)}

    # 只清理本次创建且已空的 staging 目录；原数据 stash 永久保留给用户人工确认。
    for p in (staging / "data", staging / "home", staging):
        try:
            p.rmdir()
        except OSError:
            pass

    return {"ok": True, "manifest": m, "stash": str(stash),
            "need_restart": True,
            "msg": f"已恢复。你原来的数据挪到了 {stash}（确认没问题后可以删掉）。"
                   f"**请重启 PaperPiggy** —— 内存里还是旧的索引。"}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="PaperPiggy 备份/恢复（命令行）")
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--with-index", action="store_true", help="连向量索引一起打包（大）")
    ap.add_argument("--with-key", action="store_true", help="包含 API 密钥（默认剥离）")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--inspect", metavar="ZIP")
    ap.add_argument("--restore", metavar="ZIP")
    ap.add_argument("--estimate", action="store_true")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if a.estimate:
        print(f"预估体积：{estimate(a.with_index) / 1e6:.1f} MB（含索引={a.with_index}）")
    elif a.create:
        m = create(include_index=a.with_index, include_key=a.with_key,
                   on_progress=lambda d, t: print(f"  {d}/{t}", end="\r"))
        print(f"\n✅ {m['path']}  ({m['size'] / 1e6:.1f} MB)")
        print(json.dumps(m, ensure_ascii=False, indent=2))
    elif a.list:
        for b in list_backups():
            print(f"{b['mtime']}  {b['size'] / 1e6:8.1f} MB  {b['name']}")
    elif a.inspect:
        print(json.dumps(inspect(a.inspect), ensure_ascii=False, indent=2))
    elif a.restore:
        print(json.dumps(restore(a.restore), ensure_ascii=False, indent=2))
    else:
        ap.print_help()

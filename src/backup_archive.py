"""完整备份：格式 3 普通 ZIP 分包及格式 2 历史包的原文件视图与数据库校验。"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import struct
import sys
import tempfile
import uuid
import zipfile

from backup_jobs import (BackupError, BackupCancelled, check_path, is_reparse,
                         check_snapshot_source, ensure_work_dir, io_path, machine_identity, now_text, source_fingerprint)

FORMAT = 2
ROOT_IDS = {"paperpiggy_data", "paperpiggy_home", "browser", "zotero_data",
            "zotero_profile", "zotero_registry"}
MAX_ENTRIES = 500000
MAX_MANIFEST = 64 * 1024 * 1024
_CHUNK = 1024 * 1024


def open_archive(path, cancel=None):
    """读取历史单包或任意新分包，向恢复端提供同一个原文件视图。"""
    from backup_parts import open_archive as open_parts
    return open_parts(path, cancel)


def archive_files(path, cancel=None):
    """只核对整套物理文件与归属，不展开数据库，用于安全清理。"""
    from backup_parts import archive_files as package_files
    return package_files(path, cancel)


def _cancel(cancel):
    if cancel and cancel():
        raise BackupCancelled("用户已停止备份；已有成功版本未改动。")


def file_sha256(path, cancel=None):
    digest = hashlib.sha256()
    with Path(io_path(path)).open("rb") as f:
        while data := f.read(_CHUNK):
            _cancel(cancel)
            digest.update(data)
    return digest.hexdigest()


def safe_name(name):
    if (not isinstance(name, str) or not name or "\\" in name or "\0" in name
            or name.startswith("/") or ":" in name):
        raise BackupError("备份包包含非法文件路径。")
    parts = PurePosixPath(name).parts
    if (not parts or any(p in (".", "..", "") or p.endswith((".", " ")) for p in parts)
            or any(any(ord(c) < 32 or c in '<>"|?*' for c in p) for p in parts)
            or PurePosixPath(name).as_posix() != name
            or any(re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³])(?:\..*)?", p) for p in parts)):
        raise BackupError(f"备份包路径不安全：{name}")
    return name


def _matches(path, patterns):
    for pattern in patterns or []:
        pattern = str(pattern).strip("/").replace("\\", "/")
        if not pattern:
            continue
        if path == pattern or path.startswith(pattern + "/") or fnmatch.fnmatchcase(path, pattern):
            return True
        # 目录通配规则也排除其后代。
        if any(fnmatch.fnmatchcase("/".join(PurePosixPath(path).parts[:i]), pattern)
               for i in range(1, len(PurePosixPath(path).parts))):
            return True
    return False


def _selected(path, root):
    return not _matches(path, root.get("exclude")) and (
        root.get("include") is None or _matches(path, root["include"]))


def _walk_roots(roots, cancel=None):
    output = []
    directories = set()
    seen_roots = set()
    seen_names = set()
    for root in roots:
        rid = root.get("id")
        if rid not in ROOT_IDS or rid in seen_roots:
            raise BackupError("备份来源标识无效或重复。")
        seen_roots.add(rid)
        trusted = root.get("_snapshot_source")
        source = (check_snapshot_source(root["source"], trusted) if trusted is not None else
                  check_path(root["source"], allow_missing=False))
        os_source = trusted.os_path if trusted is not None else source
        if not os.path.isdir(os_source):
            raise BackupError(f"备份来源不是目录：{source}")
        overlays = root.get("overlay_files") or {}
        for rel in overlays:
            safe_name(rel)
            if not isinstance(overlays[rel], bytes):
                raise BackupError("替换备份内容必须为字节。")
        files = {}

        def failed(exc):
            raise BackupError(f"来源目录无法完整读取：{exc}") from exc

        for folder, dirs, names in os.walk(os_source, topdown=True, followlinks=False, onerror=failed):
            _cancel(cancel)
            base = Path(folder)
            for d in list(dirs):
                p = base / d
                rel = p.relative_to(source).as_posix()
                if _matches(rel, root.get("exclude")):
                    dirs.remove(d)
                    continue
                includes = root.get("include")
                if includes is not None and not _matches(rel, includes) and not any(
                        str(p).replace("\\", "/").startswith(rel + "/")
                        or any(c in str(p) for c in "*?[") for p in includes):
                    dirs.remove(d)
                    continue
                if is_reparse(p):
                    raise BackupError(f"来源含目录联接或链接，未跟随：{p}")
                if _selected(rel, root):
                    directories.add(f"roots/{rid}/{safe_name(rel)}")
            for name in names:
                p = base / name
                rel = p.relative_to(source).as_posix()
                if not _selected(rel, root):
                    continue
                safe_name(rel)
                st = p.lstat()
                if is_reparse(p) or not stat.S_ISREG(st.st_mode):
                    raise BackupError(f"来源包含重解析链接或特殊文件，未读取：{p}")
                # 普通硬链接是来源内的有效文件名。每个名字都完整写入归档，
                # 恢复为独立文件，不依赖其他链接；仍拒绝可重定向目录的重解析点。
                files[rel] = (p, st)
        for rel in overlays:
            if not _selected(rel, root):
                raise BackupError(f"替换内容不在声明备份范围内：{rel}")
            files.setdefault(rel, (None, None))
        directories.add(f"roots/{rid}")
        for rel, (path, st) in sorted(files.items()):
            name = f"roots/{rid}/{rel}"
            if name.casefold() in seen_names:
                raise BackupError("备份来源存在大小写冲突文件名。")
            seen_names.add(name.casefold())
            payload = overlays.get(rel)
            output.append({"path": name, "file": path, "payload": payload,
                           "size": len(payload) if payload is not None else st.st_size,
                           "mtime_ns": st.st_mtime_ns if st else 0})
    if not roots:
        raise BackupError("尚未选择备份来源。")
    return output, sorted(directories)


def _wal_check(path, name, cancel=None):
    """核验当前 WAL 世代的累积校验和，避免 SQLite 静默忽略损坏的提交。

    依据 https://www.sqlite.org/fileformat2.html#wal_file_format 。reset 后可留下
    旧 salt 的尾帧，它们不属于当前世代；当前世代截断或校验失败则保守拒绝备份。
    """
    if not path.exists() or not path.stat().st_size:
        return
    with path.open("rb") as stream:
        header = stream.read(32)
        if len(header) != 32:
            raise BackupError(f"WAL 文件头不完整：{name}")
        magic, version, page_size = struct.unpack(">III", header[:12])
        if magic not in (0x377F0682, 0x377F0683) or version != 3007000 or page_size < 512 or page_size > 65536 or page_size & (page_size - 1):
            raise BackupError(f"WAL 文件头无效：{name}")
        order = "<" if magic == 0x377F0682 else ">"
        def checksum(data, state):
            a, b = state
            for x, y in struct.iter_unpack(order + "II", data):
                a = (a + x + b) & 0xFFFFFFFF
                b = (b + y + a) & 0xFFFFFFFF
            return a, b
        state = checksum(header[:24], (0, 0))
        if state != struct.unpack(">II", header[24:]):
            raise BackupError(f"WAL 文件头校验失败：{name}")
        # 快照中的 SHM 可证明有多少已提交帧。不能把这些帧的 salt 损坏误认成
        # reset 留下的旧尾帧，再让 SQLite 静默退回较旧的主库。
        committed = 0
        shm = Path(str(path)[:-4] + "-shm")
        if shm.is_file() and shm.stat().st_size:
            with shm.open("rb") as shared:
                copies = shared.read(96)
            valid = []
            native = "<" if sys.byteorder == "little" else ">"
            for offset in (0, 48):
                value = copies[offset:offset + 48]
                if len(value) != 48 or value[12] != 1 or struct.unpack(native + "I", value[:4])[0] != 3007000:
                    continue
                a = b = 0
                for x, y in struct.iter_unpack(native + "II", value[:40]):
                    a = (a + x + b) & 0xFFFFFFFF
                    b = (b + y + a) & 0xFFFFFFFF
                if (a, b) == struct.unpack(native + "II", value[40:48]):
                    valid.append(value)
            if not valid or len(valid) == 2 and valid[0] != valid[1]:
                raise BackupError(f"WAL 提交索引不一致，未登记可能不完整的快照：{name}")
            committed = struct.unpack(native + "I", valid[0][16:20])[0]
            if committed and valid[0][32:40] != header[16:24]:
                raise BackupError(f"WAL 与提交索引世代不匹配：{name}")
        frame_number = 0
        while True:
            _cancel(cancel)
            frame = stream.read(24)
            if not frame:
                if frame_number < committed:
                    raise BackupError(f"WAL 已提交帧缺失：{name}")
                return
            if len(frame) != 24:
                raise BackupError(f"WAL 尾帧不完整：{name}")
            if frame[8:16] != header[16:24]:
                if frame_number < committed:
                    raise BackupError(f"WAL 已提交帧世代校验失败：{name}")
                return  # SQLite reset 后未覆盖的旧世代帧。
            page = stream.read(page_size)
            if len(page) != page_size or not struct.unpack(">I", frame[:4])[0]:
                raise BackupError(f"WAL 帧不完整或页号无效：{name}")
            state = checksum(page, checksum(frame[:8], state))
            if state != struct.unpack(">II", frame[16:24]):
                raise BackupError(f"WAL 帧校验失败：{name}")
            frame_number += 1


def _sqlite_check(z, entries, cancel=None, work_dir=None):
    """只对独立临时副本执行 SQLite 恢复与校验；归档原字节不变。"""
    work_dir = ensure_work_dir(work_dir)
    checks = []
    names = {x["path"] for x in entries}
    for item in entries:
        _cancel(cancel)
        name = item["path"]
        # Chrome 的 Cookies/Login Data 等没有扩展名，必须按文件头识别。
        # WAL/SHM/journal 本身由对应主库一同验证，不单独作为主库打开。
        if name.lower().endswith(("-wal", "-shm", "-journal")):
            continue
        with z.open(name) as f:
            header = f.read(16)
        if header != b"SQLite format 3\0":
            relative = PurePosixPath(name).parts
            browser_db = (len(relative) >= 3 and relative[1] == "browser" and
                          relative[-1].casefold() in {"cookies", "login data", "login data for account",
                              "web data", "history", "favicons", "top sites", "shortcuts", "network action predictor",
                              "affiliation database", "account web data", "trust tokens", "reporting and nel"})
            if (name == "roots/zotero_data/zotero.sqlite"
                    or name.lower().endswith(".sqlite") and item["size"] or browser_db
                    or name + "-wal" in names or name + "-shm" in names):
                raise BackupError(f"SQLite 数据库文件头无效：{name}")
            continue  # 可能是插件的其他数据库格式；内容仍已逐字节校验。
        with tempfile.TemporaryDirectory(prefix="pp-db-verify-", dir=io_path(work_dir) if work_dir else None) as temp:
            target = Path(temp) / "database.sqlite"
            for suffix in ("", "-wal", "-shm", "-journal"):
                source = name + suffix
                if source not in names:
                    continue
                with z.open(source) as src, (Path(str(target) + suffix)).open("xb") as dst:
                    while chunk := src.read(_CHUNK):
                        _cancel(cancel)
                        dst.write(chunk)
            _wal_check(Path(str(target) + "-wal"), name + "-wal", cancel)
            try:
                connection = sqlite3.connect(str(target), timeout=3)
                try:
                    connection.execute("PRAGMA trusted_schema=OFF")
                    connection.set_progress_handler(lambda: 1 if cancel and cancel() else 0, 10000)
                    quick = connection.execute("PRAGMA quick_check").fetchall()
                    full = connection.execute("PRAGMA integrity_check").fetchall()
                    if quick != [("ok",)] or full != [("ok",)]:
                        raise BackupError(f"数据库完整性检查失败：{name}：{(quick + full)[:5]}")
                finally:
                    connection.close()
            except sqlite3.Error as exc:
                _cancel(cancel)
                if "no such collation sequence" in str(exc).lower():
                    raise BackupError(f"数据库无法完成离线完整性检查：{name}：缺少原程序的排序规则（{exc}）。"
                                      "未判定为损坏，也未冒报校验成功；需要与原程序一致的校验器，已有成功备份保持不动。") from exc
                raise BackupError(f"数据库无法完成离线完整性检查：{name}：{exc}") from exc
            checks.append({"path": name, "quick_check": "ok", "integrity_check": "ok"})
    return checks


def _validate_structure(z):
    infos = z.infolist()
    by_name = {info.filename: info for info in infos}
    if len(infos) > MAX_ENTRIES:
        raise BackupError("备份包文件数超过单次安全校验上限。")
    seen = set()
    for info in infos:
        name = safe_name(info.filename)
        if name.casefold() in seen:
            raise BackupError("备份包包含重复或大小写冲突路径。")
        seen.add(name.casefold())
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_IFMT(mode) not in (0, stat.S_IFREG):
            raise BackupError("备份包含链接或特殊成员。")
        if info.flag_bits & 1:
            raise BackupError("此备份格式不接受加密成员。")
        if name != "manifest.json":
            parts = PurePosixPath(name).parts
            if len(parts) < 3 or parts[0] != "roots" or parts[1] not in ROOT_IDS:
                raise BackupError("备份包包含未声明的恢复来源。")
    if "manifest.json" not in by_name:
        raise BackupError("缺少备份清单。")
    if by_name["manifest.json"].file_size > MAX_MANIFEST:
        raise BackupError("备份清单过大。")
    try:
        m = json.loads(z.read("manifest.json"))
    except (ValueError, UnicodeError) as exc:
        raise BackupError("备份清单损坏。") from exc
    if not isinstance(m, dict) or m.get("format") != FORMAT:
        raise BackupError("不是受支持的格式 2 完整备份。")
    for key in ("backup_id", "job_id"):
        if not re.fullmatch(r"[a-f0-9]{32}", str(m.get(key, ""))):
            raise BackupError("备份清单身份无效。")
    if m.get("kind") not in ("paperpiggy", "zotero"):
        raise BackupError("备份类型无效。")
    if not isinstance(m.get("roots"), list) or not isinstance(m.get("files"), list):
        raise BackupError("备份来源或文件清单无效。")
    ids = [x.get("id") for x in m["roots"] if isinstance(x, dict)]
    if len(ids) != len(m["roots"]) or len(ids) != len(set(ids)) or not set(ids) <= ROOT_IDS:
        raise BackupError("备份来源清单无效。")
    allowed_ids = ({"paperpiggy_data", "paperpiggy_home", "browser"} if m["kind"] == "paperpiggy"
                   else {"zotero_data", "zotero_profile", "zotero_registry"})
    if not set(ids) <= allowed_ids:
        raise BackupError("备份类型与来源不一致，无法安全核验对应程序。")
    declared = set()
    for item in m["files"]:
        if not isinstance(item, dict):
            raise BackupError("备份文件清单无效。")
        name = safe_name(item.get("path"))
        parts = PurePosixPath(name).parts
        if len(parts) < 3 or parts[0] != "roots" or parts[1] not in ids or name.casefold() in declared:
            raise BackupError("备份文件声明不安全或重复。")
        declared.add(name.casefold())
        if type(item.get("mtime_ns", 0)) is not int:
            raise BackupError("备份文件时间格式无效。")
        if (type(item.get("size")) is not int or item["size"] < 0
                or not re.fullmatch(r"[a-f0-9]{64}", str(item.get("sha256", "")))):
            raise BackupError("备份文件大小或哈希无效。")
        if name not in by_name or by_name[name].file_size != item["size"]:
            raise BackupError(f"备份清单与实际文件不一致：{name}")
    if declared != seen - {"manifest.json"}:
        raise BackupError("备份包存在漏项或清单外文件。")
    for name in declared:
        parts = PurePosixPath(name).parts
        if any("/".join(parts[:i]) in declared for i in range(1, len(parts))):
            raise BackupError("备份包存在文件与目录的路径冲突。")
    for name in m.get("directories", []):
        safe_name(name)
        parts = PurePosixPath(name).parts
        if len(parts) < 2 or parts[0] != "roots" or parts[1] not in ids:
            raise BackupError("备份目录声明越界。")
        if name.casefold() in declared:
            raise BackupError("备份目录声明与文件冲突。")
    return m


def _inspect_open(z, cancel=None, progress=None, work_dir=None, verify_databases=True):
    m = _validate_structure(z)
    for index, item in enumerate(m["files"]):
        _cancel(cancel)
        digest = hashlib.sha256()
        size = 0
        with z.open(item["path"]) as f:
            while chunk := f.read(_CHUNK):
                _cancel(cancel)
                digest.update(chunk)
                size += len(chunk)
        if digest.hexdigest() != item["sha256"] or size != item["size"]:
            raise BackupError(f"文件内容哈希不匹配：{item['path']}")
        if progress:
            progress(index + 1, len(m["files"]))
    db_checks = _sqlite_check(z, m["files"], cancel, work_dir) if verify_databases else []
    return {"ok": True, "manifest": m, "database_checks": db_checks,
            "n_entries": len(m["files"]),
            "uncompressed_size": sum(x["size"] for x in m["files"]),
            "warnings": list(m.get("warnings", [])), **getattr(z, "package_info", {})}


def inspect(path, cancel=None, progress=None, work_dir=None, verify_databases=True):
    """完整读取校验；失败抛 BackupError，绝不修改归档或原来源。"""
    path = check_path(path, allow_missing=False)
    work_dir = ensure_work_dir(work_dir)
    try:
        with open_archive(path, cancel) as z:
            info = _inspect_open(z, cancel, progress, work_dir, verify_databases)
            if "archive_format" not in info:
                size = path.stat().st_size
                info.update(archive_format=2, parts=[{"path": str(path), "size": size,
                    "sha256": file_sha256(path, cancel)}], index_path="", index_size=0,
                    index_sha256="", part_count=1, total_size=size)
            return info
    except (zipfile.BadZipFile, OSError, RuntimeError, EOFError) as exc:
        if isinstance(exc, (BackupError, BackupCancelled)):
            raise
        raise BackupError(f"备份包无法完整校验：{exc}") from exc


def _create_v2(job, roots, metadata=None, progress=None, cancel=None, work_dir=None):
    """历史兼容性夹具专用；正常任务只调用 create 生成受5GB限制的新格式。"""
    _cancel(cancel)
    work_dir = ensure_work_dir(work_dir)
    destination = check_path(job.get("destination") or "")
    if not job.get("destination") or not Path(job["destination"]).is_absolute():
        raise BackupError("请先选择绝对路径的备份目的地。")
    if "过时备份" in destination.parts:
        raise BackupError("现有过时备份目录受保护。")
    for root in roots:
        original = Path(root.get("original_source") or root["source"]).absolute()
        try:
            relative = destination.relative_to(original).as_posix()
        except ValueError:
            pass
        else:
            # PP data/home是白名单备份，默认data/backups并不属于所选资产。
            # 仅当目的地确实会被遍历纳入，或通配规则无法证明排除时才拒绝。
            excluded = _matches(relative, root.get("exclude"))
            include = root.get("include")
            uncertain = include is None or _matches(relative, include) or any(
                any(c in str(pattern) for c in "*?[") for pattern in include or [])
            if not excluded and uncertain:
                raise BackupError("备份目的地不能位于已选备份来源内部。")
    items, directories = _walk_roots(roots, cancel)
    destination.mkdir(parents=True, exist_ok=True)
    total_bytes = sum(x["size"] for x in items)
    if shutil.disk_usage(destination).free < total_bytes + 64 * 1024 * 1024:
        raise BackupError("空间不足，无法先创建并校验新备份；已有版本未删除。")
    backup_id = uuid.uuid4().hex
    created = now_text()
    root_meta = [{"id": r["id"], "original_source": str(r.get("original_source") or r["source"]),
                  "include": r.get("include"), "exclude": r.get("exclude", [])} for r in roots]
    manifest = {"format": FORMAT, "backup_id": backup_id, "job_id": job["job_id"],
                "machine_id": job.get("machine_id") or machine_identity(), "kind": job["kind"],
                "created_at": created, "source_fingerprint": source_fingerprint(roots),
                "roots": root_meta, "files": [], "directories": directories,
                "metadata": metadata or {}, "warnings": [],
                "include_index": bool(job.get("include_index")),
                "include_key": bool(job.get("include_key")),
                "include_browser": any(r["id"] == "browser" for r in roots)}
    name = f"PaperPiggy-{job['kind']}-{created[:19].replace(':', '').replace('-', '')}-{backup_id}.zip"
    target = destination / name
    partial = target.with_name(target.name + ".part")
    try:
        with zipfile.ZipFile(partial, "x", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
            for index, item in enumerate(items):
                _cancel(cancel)
                digest = hashlib.sha256()
                size = 0
                zi = zipfile.ZipInfo(item["path"])
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.external_attr = (stat.S_IFREG | 0o600) << 16
                with z.open(zi, "w", force_zip64=True) as dst:
                    if item["payload"] is not None:
                        data = item["payload"]
                        digest.update(data)
                        dst.write(data)
                        size = len(data)
                    else:
                        before = item["file"].stat()
                        with item["file"].open("rb") as src:
                            while chunk := src.read(_CHUNK):
                                _cancel(cancel)
                                dst.write(chunk)
                                digest.update(chunk)
                                size += len(chunk)
                        after = item["file"].stat()
                        if (before.st_size, before.st_mtime_ns, before.st_ino) != (
                                after.st_size, after.st_mtime_ns, after.st_ino):
                            raise BackupError("来源在读取期间变化，未将此次操作登记为成功。")
                if size != item["size"]:
                    raise BackupError("来源文件大小在读取期间变化。")
                manifest["files"].append({"path": item["path"], "size": size,
                                           "sha256": digest.hexdigest(), "mtime_ns": item["mtime_ns"]})
                if progress:
                    progress(index + 1, len(items))
            z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False).encode("utf-8"))
        with partial.open("r+b") as f:
            os.fsync(f.fileno())
        verified = inspect(partial, cancel=cancel, work_dir=work_dir)
        _cancel(cancel)
        os.replace(partial, target)
        return {"ok": True, "path": str(target), "size": target.stat().st_size,
                "manifest": manifest, "warnings": [],
                "database_count": len(verified["database_checks"])}
    except Exception:
        # 只处理本次独占创建的临时包，不触碰任何成功版本。
        partial.unlink(missing_ok=True)
        raise


def create(job, roots, metadata=None, progress=None, cancel=None, work_dir=None,
           *, max_part_size=None):
    """来源由调用者固定为 VSS 快照，按普通 ZIP 分包创建一套完整恢复点。"""
    from backup_parts import MAX_PART_SIZE, write_set
    _cancel(cancel)
    work_dir = ensure_work_dir(work_dir)
    destination = check_path(job.get("destination") or "")
    if not job.get("destination") or not Path(job["destination"]).is_absolute():
        raise BackupError("请先选择绝对路径的备份目的地。")
    if "过时备份" in destination.parts:
        raise BackupError("现有过时备份目录受保护。")
    for root in roots:
        original = Path(root.get("original_source") or root["source"]).absolute()
        try:
            relative = destination.relative_to(original).as_posix()
        except ValueError:
            continue
        excluded = _matches(relative, root.get("exclude"))
        include = root.get("include")
        uncertain = include is None or _matches(relative, include) or any(
            any(c in str(pattern) for c in "*?[") for pattern in include or [])
        if not excluded and uncertain:
            raise BackupError("备份目的地不能位于已选备份来源内部。")
    items, directories = _walk_roots(roots, cancel)
    destination.mkdir(parents=True, exist_ok=True)
    total_bytes = sum(x["size"] for x in items)
    # Includes deflate's conservative expansion bound and per-entry/index costs;
    # database verification still uses its own isolated temporary work directory.
    needed = total_bytes + (total_bytes >> 3) + (total_bytes >> 6) + len(items) * 2048 + 64 * 1024 * 1024
    if shutil.disk_usage(destination).free < needed:
        raise BackupError("空间不足，无法先创建并校验新备份；已有版本未删除。")
    root_meta = [{"id": r["id"], "original_source": str(r.get("original_source") or r["source"]),
                  "include": r.get("include"), "exclude": r.get("exclude", [])} for r in roots]
    manifest = {"format": FORMAT, "backup_id": uuid.uuid4().hex, "job_id": job["job_id"],
                "machine_id": job.get("machine_id") or machine_identity(), "kind": job["kind"],
                "created_at": now_text(), "source_fingerprint": source_fingerprint(roots),
                "roots": root_meta, "files": [], "directories": directories,
                "metadata": metadata or {}, "warnings": [],
                "include_index": bool(job.get("include_index")),
                "include_key": bool(job.get("include_key")),
                "include_browser": any(r["id"] == "browser" for r in roots)}
    return write_set(destination, items, manifest, progress, cancel, work_dir,
                     MAX_PART_SIZE if max_part_size is None else max_part_size)

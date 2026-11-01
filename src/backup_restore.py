"""单包及成套分包备份的安全展开与可回滚原位恢复。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import uuid
import zipfile

import backup_archive as A
from backup_jobs import BackupError, atomic_json, check_path, ensure_work_dir, io_path, now_text, read_json


def _fs(path):
    """I/O边界使用extended path；返回给UI、prepare hook和配置的路径不带此实现前缀。"""
    return Path(io_path(path))


def _logical(path):
    value = os.fspath(path)
    if os.name == "nt":
        if value.casefold().startswith("\\\\?\\unc\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\") and len(value) > 5 and value[5] == ":":
            value = value[4:]
    return Path(value)


def inspect(path, **kwargs):
    return A.inspect(path, **kwargs)


def _cancel(cancel):
    A._cancel(cancel)


def _extract_checked(path, destination, info, cancel=None, progress=None):
    root = check_path(destination)
    _fs(root).mkdir(parents=True, exist_ok=False)
    try:
        with A.open_archive(path, cancel=cancel) as z:
            for rel in info["manifest"].get("directories", []):
                _fs(check_path(root / Path(*PurePosixPath(A.safe_name(rel)).parts))).mkdir(parents=True, exist_ok=True)
            for index, item in enumerate(info["manifest"]["files"]):
                _cancel(cancel)
                target = check_path(root / Path(*PurePosixPath(item["path"]).parts))
                target.relative_to(root)
                _fs(target.parent).mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                written = 0
                with z.open(item["path"]) as src, _fs(target).open("xb") as dst:
                    while chunk := src.read(1024 * 1024):
                        _cancel(cancel)
                        dst.write(chunk)
                        digest.update(chunk)
                        written += len(chunk)
                    dst.flush()
                    os.fsync(dst.fileno())
                if digest.hexdigest() != item["sha256"] or written != item["size"]:
                    raise BackupError("恢复展开期间校验失败，原目录未改动。")
                if item.get("mtime_ns"):
                    os.utime(io_path(target), ns=(item["mtime_ns"], item["mtime_ns"]))
                if progress:
                    progress(index + 1, len(info["manifest"]["files"]))
        atomic_json(root / "restore-manifest.json", info["manifest"])
    except Exception:
        # 这是本次独占创建的新目录；保留不完整资料供诊断，不冒充成功结果。
        atomic_json(root / "restore-incomplete.json", {"state": "incomplete", "at": now_text()})
        raise
    return root


def extract_to(path, destination, cancel=None, progress=None, work_dir=None):
    """destination 必须不存在或为空；成功时输出 root-ID 命名的来源目录。"""
    destination = check_path(destination)
    work_dir = ensure_work_dir(work_dir)
    was_empty = False
    if _fs(destination).exists():
        if not _fs(destination).is_dir() or any(_fs(destination).iterdir()):
            raise BackupError("新目录恢复要求目标不存在或为空，不会覆盖已有内容。")
        was_empty = True
    info = A.inspect(path, cancel=cancel, work_dir=work_dir)
    parent = destination.parent
    _fs(parent).mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(io_path(parent)).free < info["uncompressed_size"] + 64 * 1024 * 1024:
        raise BackupError("剩余空间不足以完整展开备份，原目录未改动。")
    if was_empty:
        _fs(destination).rmdir()  # 校验完成后，只移除已确认空的目标，以便独占创建。
    _extract_checked(path, destination, info, cancel, progress)
    # 用户目标直接按root-ID排列；无额外roots导航层。
    roots = destination / "roots"
    for child in list(_fs(roots).iterdir()) if _fs(roots).exists() else []:
        child.rename(_fs(destination / child.name))
    if _fs(roots).exists():
        _fs(roots).rmdir()
    return {"ok": True, "destination": str(destination), "manifest": info["manifest"],
            "roots": {r["id"]: str(destination / r["id"]) for r in info["manifest"]["roots"]},
            "warnings": info.get("warnings", [])}


def _operations(manifest, extracted, root_targets, operation_id):
    available = {r["id"]: r for r in manifest["roots"]}
    if not root_targets or not set(root_targets) <= set(available):
        raise BackupError("恢复目标来源无效。")
    output = []
    for rid, value in root_targets.items():
        target_root = check_path(value)
        if not Path(value).is_absolute():
            raise BackupError("原位恢复目标必须为绝对路径。")
        source_root = extracted / "roots" / rid
        include = available[rid].get("include")
        if include is None:
            pairs = [(source_root, target_root)]
        else:
            # PaperPiggy只恢复清单中的资产，不覆盖未选择的索引、模型或其他数据。
            selected = []
            for relative in include:
                A.safe_name(relative)
                if any(c in relative for c in "*?["):
                    raise BackupError("原位恢复白名单不接受通配规则，请先选择明确目录。")
                if any(relative == p or relative.startswith(p + "/") for p in selected):
                    continue
                selected = [p for p in selected if not p.startswith(relative + "/")]
                selected.append(relative)
            pairs = [(source_root / Path(*PurePosixPath(rel).parts),
                      target_root / Path(*PurePosixPath(rel).parts)) for rel in selected]
        for source, live in pairs:
            if not _fs(source).exists():
                continue
            check_path(live)
            if _fs(live).is_file() and _fs(live).stat().st_nlink > 1:
                raise BackupError(f"恢复目标是硬链接，未替换：{live}")
            for item in output:
                earlier = Path(item["live"])
                a, b = _logical(live), _logical(earlier)
                if a == b or a in b.parents or b in a.parents:
                    raise BackupError("恢复目标相互重叠，未改动任何目录。")
            prefix = "." + live.name + ".paperpiggy-"
            output.append({"root_id": rid, "live": str(live), "source": str(source),
                           "stage": str(live.with_name(prefix + "stage-" + operation_id)),
                           "rollback": str(live.with_name(prefix + "rollback-" + operation_id)),
                           "failed_new": str(live.with_name(prefix + "failed-new-" + operation_id)),
                           "had_original": _fs(live).exists(), "phase": "planned"})
    if not output:
        raise BackupError("所选备份来源没有可恢复内容。")
    return output


def _process_check(ensure_stopped, manifest, targets):
    if ensure_stopped is None:
        raise BackupError("原位恢复必须提供相应程序已退出的核验，未替换目录。")
    if ensure_stopped(manifest, targets) is False:
        raise BackupError("相关程序仍在运行，请先自行退出，再恢复；应用不会结束进程。")


def _copy_stage(source, target):
    check_path(target)
    if _fs(target).exists():
        raise BackupError("本次恢复暂存路径已存在，未覆盖。")
    if _fs(source).is_dir():
        shutil.copytree(_fs(source), _fs(target), symlinks=False)
    else:
        with _fs(source).open("rb") as src, _fs(target).open("xb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        shutil.copystat(_fs(source), _fs(target))


def _validate_journal(journal):
    oid = journal.get("operation_id", "")
    if len(oid) != 32 or any(c not in "0123456789abcdef" for c in oid):
        raise BackupError("恢复记录标识无效。")
    for op in journal.get("operations", []):
        live = check_path(op["live"])
        for key, part in (("stage", "stage"), ("rollback", "rollback"), ("failed_new", "failed-new")):
            path = check_path(op[key])
            if path.parent != live.parent or path.name != f".{live.name}.paperpiggy-{part}-{oid}":
                raise BackupError("恢复记录中的回退路径不安全，未执行。")


def recover_journal(path):
    """失败后或重启后按记录回滚；始终保留恢复前资料及失败新资料。"""
    path = check_path(path, allow_missing=False)
    journal = read_json(path, {})
    _validate_journal(journal)
    if journal.get("state") == "completed":
        return {"ok": True, "state": "completed", "journal": str(path)}
    errors = []
    for op in reversed(journal["operations"]):
        live, rollback, stage = map(Path, (op["live"], op["rollback"], op["stage"]))
        try:
            check_path(live)
            check_path(rollback)
            if _fs(rollback).exists():
                if _fs(live).exists():
                    failed = check_path(op["failed_new"])
                    if _fs(failed).exists():
                        raise BackupError("失败的新目录已经存在，未覆盖任何恢复资料。")
                    _fs(live).rename(_fs(failed))
                _fs(rollback).rename(_fs(live))
            elif not op["had_original"] and op["phase"] in ("before_install", "installed") and _fs(live).exists():
                failed = check_path(op["failed_new"])
                if _fs(failed).exists():
                    raise BackupError("失败的新目录已经存在。")
                _fs(live).rename(_fs(failed))
            op["phase"] = "rolled_back"
            atomic_json(path, journal)
        except Exception as exc:
            errors.append(f"{live}：{exc}")
    journal["state"] = "rollback_failed" if errors else "rolled_back"
    journal["rollback_errors"] = errors
    atomic_json(path, journal)
    return {"ok": not errors, "state": journal["state"], "journal": str(path), "errors": errors}


def restore_in_place(path, root_targets, confirm=False, ensure_stopped=None,
                     prepare=None, progress=None, cancel=None, work_dir=None,
                     journal_dir=None):
    """先验证、同卷暂存，然后记录每次改名；任何异常均尝试回滚。

    prepare(extracted_root, manifest, targets) 可在校验后修改副本中的路径映射，
    例如保持其他设置的同时调整Zotero数据路径。注册其他profile由调用者处理。
    """
    if confirm is not True:
        raise BackupError("原位恢复需要用户明确确认。")
    work_dir = ensure_work_dir(work_dir)
    info = A.inspect(path, cancel=cancel, work_dir=work_dir)
    manifest = info["manifest"]
    _process_check(ensure_stopped, manifest, root_targets)
    oid = uuid.uuid4().hex
    temp = tempfile.TemporaryDirectory(prefix="pp-restore-", dir=io_path(work_dir) if work_dir else None)
    temp_root = _logical(temp.name)
    extracted = temp_root / "verified"
    try:
        _extract_checked(path, extracted, info, cancel, progress)
        if prepare:
            prepare(extracted, manifest, root_targets)
        operations = _operations(manifest, extracted, root_targets, oid)
        archive_paths = [_logical(Path(part["path"]).absolute()) for part in info.get("parts", [])]
        if info.get("index_path"):
            archive_paths.append(_logical(Path(info["index_path"]).absolute()))
        if not archive_paths:
            archive_paths.append(_logical(Path(path).absolute()))
        for op in operations:
            live = Path(op["live"])
            logical_live = _logical(live)
            if any(p == logical_live or logical_live in p.parents for p in archive_paths):
                raise BackupError("原位恢复目标不能包含正在使用的任何分包或总清单。")
            if temp_root == logical_live or logical_live in temp_root.parents:
                raise BackupError("恢复工作目录不能位于被替换的目录中。")
            _fs(live.parent).mkdir(parents=True, exist_ok=True)
            source_io = _fs(op["source"])
            needed = (sum(p.stat().st_size for p in source_io.rglob("*") if p.is_file())
                      if source_io.is_dir() else source_io.stat().st_size)
            if shutil.disk_usage(io_path(live.parent)).free < needed + 64 * 1024 * 1024:
                raise BackupError("空间不足，无法先暂存恢复内容；当前目录未改动。")
        # 所有来源先准备完毕，任何复制失败均不影响当前目录。
        for op in operations:
            _cancel(cancel)
            _copy_stage(Path(op["source"]), Path(op["stage"]))
        record_dir = check_path(journal_dir or Path(operations[0]["live"]).parent)
        _fs(record_dir).mkdir(parents=True, exist_ok=True)
        journal_path = record_dir / ("PaperPiggy-restore-" + oid + ".json")
        if any(Path(op["live"]) == record_dir or Path(op["live"]) in record_dir.parents for op in operations):
            raise BackupError("恢复记录不能位于即将替换的目录内。")
        journal = {"schema": 1, "operation_id": oid, "state": "prepared",
                   "created_at": now_text(), "backup_id": manifest["backup_id"], "operations": operations}
        atomic_json(journal_path, journal)
        try:
            for op in operations:
                _process_check(ensure_stopped, manifest, root_targets)
                live, stage, rollback = map(Path, (op["live"], op["stage"], op["rollback"]))
                check_path(live)
                if _fs(rollback).exists():
                    raise BackupError("恢复回退目录已存在，未覆盖。")
                op["phase"] = "before_backup"
                atomic_json(journal_path, journal)
                if op["had_original"]:
                    if not _fs(live).exists():
                        raise BackupError("核验后原目录发生变化，停止恢复。")
                    _fs(live).rename(_fs(rollback))
                elif _fs(live).exists():
                    raise BackupError("核验后目标被其他程序创建，停止恢复。")
                op["phase"] = "before_install"
                atomic_json(journal_path, journal)
                _process_check(ensure_stopped, manifest, root_targets)
                _fs(stage).rename(_fs(live))
                op["phase"] = "installed"
                atomic_json(journal_path, journal)
            journal["state"] = "completed"
            journal["finished_at"] = now_text()
            atomic_json(journal_path, journal)
            return {"ok": True, "manifest": manifest, "journal": str(journal_path),
                    "rollback_dirs": [op["rollback"] for op in operations if op["had_original"]],
                    "roots": {k: str(v) for k, v in root_targets.items()},
                    "warnings": info.get("warnings", [])}
        except Exception as exc:
            journal["error"] = str(exc)
            atomic_json(journal_path, journal)
            recovery = recover_journal(journal_path)
            raise BackupError(f"恢复未完成：{exc}；回滚状态：{recovery['state']}；记录：{journal_path}") from exc
    finally:
        temp.cleanup()

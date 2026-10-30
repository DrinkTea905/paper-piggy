"""旧格式只读兼容及恢复时转换；不把旧包认领为新任务版本。"""
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

import config as C
from backup_jobs import BackupError, check_path


def format_of(path):
    path = check_path(path, allow_missing=False)
    try:
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo("manifest.json")
            if info.file_size > 64 * 1024 * 1024:
                raise BackupError("备份清单超过合理大小。")
            value = json.loads(archive.read(info).decode("utf-8"))
            return int(value.get("format", 0))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, AttributeError) as exc:
        raise BackupError("不是有效的备份包或清单已损坏。") from exc


def inspect(path):
    import backup
    checked = backup.inspect(path)
    if not checked.get("ok"):
        raise BackupError(checked.get("err") or "旧备份包损坏。")
    old = checked["manifest"]
    with zipfile.ZipFile(path) as archive:
        names = [x.filename for x in archive.infolist() if not x.is_dir()]
    roots = []
    for prefix, rid, source in (("data", "paperpiggy_data", C.DATA),
                                ("home", "paperpiggy_home", C.DATA.parent)):
        include = sorted({x.split("/")[1] for x in names if x.startswith(prefix + "/")})
        if include:
            roots.append({"id": rid, "source": str(source), "original_source": str(source), "include": include})
    warnings = list(checked.get("warnings", [])) + ["旧格式备份不包含专用浏览器资料；创建时没有同批快照与逐文件哈希证明。"]
    return {**checked, "legacy": True, "warnings": warnings, "manifest": {
        **old, "kind": "paperpiggy", "created_at": old.get("created", ""), "roots": roots,
        "metadata": {"legacy": True, "includes_index": old.get("includes_index", False),
                     "has_api_key": old.get("has_api_key", False)}, "has_browser": False}}


@contextmanager
def resolved_archive(path, current, job):
    """转换只发生在已验证的独占临时目录，原包、成功ledger和旧备份完全不动。"""
    if format_of(path) != 1:
        yield str(path)
        return
    import backup
    import backup_archive
    checked = backup._validate_zip(path, verify_crc=True)
    if not checked.get("ok"):
        raise BackupError(checked.get("err") or "旧备份校验失败。")
    preview = inspect(path)
    work = check_path(current.base / "work")
    work.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(work).free < checked["uncompressed_size"] * 2 + 64 * 1024 * 1024:
        raise BackupError("空间不足以安全验证和转换旧格式备份。")
    with tempfile.TemporaryDirectory(prefix="legacy-restore-", dir=work) as temp:
        temporary = Path(temp)
        extracted = temporary / "source"
        extracted.mkdir()
        with zipfile.ZipFile(path) as archive:
            for item in archive.infolist():
                if item.filename == "manifest.json" or item.is_dir():
                    continue
                target = check_path(extracted / Path(item.filename))
                target.relative_to(extracted)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as source, target.open("xb") as dest:
                    shutil.copyfileobj(source, dest, 1024 * 1024)
        roots = []
        for root in preview["manifest"]["roots"]:
            prefix = "data" if root["id"] == "paperpiggy_data" else "home"
            roots.append({**root, "source": str(extracted / prefix)})
        original = checked["manifest"]
        result = backup_archive.create({**job, "destination": str(temporary / "converted"),
                    "include_index": bool(original.get("includes_index")),
                    "include_key": bool(original.get("has_api_key"))}, roots,
                 metadata={"legacy": True, "legacy_created": checked["manifest"].get("created", ""),
                           "legacy_original_path": str(Path(path).absolute()),
                           "legacy_warnings": preview["warnings"],
                           "has_browser": False, "not_a_new_snapshot": True}, work_dir=work)
        yield result["path"]

"""独立备份任务、成功登记、调度与清理。此模块不启动用户程序。"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import socket
import stat
import threading
import time
import uuid

import config as C


class BackupError(RuntimeError):
    pass


class BackupBusy(BackupError):
    pass


class BackupCancelled(BackupError):
    pass


def now_text():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def paperpiggy_maintenance_lock(data_dir):
    """建库维护与静态快照共用实际数据家的跨进程锁，不依赖服务端口。"""
    return file_lock(Path(data_dir) / "backup_jobs" / "locks" / "paperpiggy-maintenance.lock")


def _first_schedule(enabled_at, at_time):
    enabled = datetime.fromisoformat(enabled_at)
    hour, minute = map(int, at_time.split(":"))
    planned = enabled.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return planned if planned >= enabled else planned + timedelta(days=1)


def machine_identity():
    """仅保存不可逆本机标识，防止共享目录内的跨电脑清理。"""
    value = socket.gethostname() + "|" + str(uuid.getnode())
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Cryptography") as key:
                value = str(winreg.QueryValueEx(key, "MachineGuid")[0])
        except OSError:
            pass
    return hashlib.sha256(value.encode()).hexdigest()


def io_path(path):
    """仅供文件系统调用的Windows长路径表示；逻辑路径、配置与返回值保持原样。"""
    value = os.fspath(path)
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return value
    value = os.path.abspath(value)
    return "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value


def is_reparse(path):
    try:
        # 保留调用方显式传入的卷根尾反斜杠；Path会去掉VSS设备卷根的该分隔符。
        st = os.lstat(io_path(path))
        return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & 0x400)
    except FileNotFoundError:
        return False


def _device_namespace(path):
    value = os.fspath(path).replace("/", "\\").casefold()
    return (value.startswith("\\\\?\\globalroot") or value.startswith("\\\\.\\")
            or value.startswith("\\device\\") or value.startswith("\\??\\"))


_SNAPSHOT_DEVICE = re.compile(r"(?i)(\\\\\?\\GLOBALROOT\\Device\\HarddiskVolumeShadowCopy[0-9]+)(?:\\(.*))?")


class _SnapshotSource:
    """仅存在于snapshot.map_path之后的内存根；JSON配置/归档不能携带此能力。"""
    __slots__ = ("source", "volume_root", "os_path")

    def __init__(self, path):
        value = os.fspath(path).replace("/", "\\")
        matched = _SNAPSHOT_DEVICE.fullmatch(value)
        if not matched or any(part in (".", "..") for part in value.split("\\")):
            raise BackupError("快照来源不是受支持的具体VSS卷路径。")
        self.source = ntpath.normcase(ntpath.normpath(value))
        self.volume_root = matched[1]
        self.os_path = value if matched[2] else self.volume_root + "\\"


def _snapshot_source(path):
    """来源映射器专用：真实VSS路径授予有界检查能力；隔离FakeSnapshot沿用普通路径。"""
    value = os.fspath(path).replace("/", "\\")
    return _SnapshotSource(value) if _SNAPSHOT_DEVICE.fullmatch(value) else None


def check_snapshot_source(path, trusted, allow_missing=False):
    """只检查具体快照卷根及卷内父链，绝不stat GLOBALROOT或Device命名空间。"""
    if not isinstance(trusted, _SnapshotSource) or ntpath.normcase(ntpath.normpath(os.fspath(path))) != trusted.source:
        raise BackupError("来源不匹配本次受信任快照映射，未绕过普通路径检查。")
    p = Path(path).absolute()
    boundary = Path(trusted.volume_root)
    current = p
    while current != boundary:
        if boundary not in current.parents:
            raise BackupError("快照来源越过具体卷根，未读取。")
        if is_reparse(current):
            raise BackupError(f"快照来源含符号链接或目录联接：{current}")
        current = current.parent
    if is_reparse(trusted.volume_root + "\\"):
        raise BackupError("快照卷根是重解析点，未读取。")
    if not allow_missing and not os.path.exists(trusted.os_path):
        raise BackupError(f"快照来源不存在：{p}")
    return p


def check_source_path(root, relative="", allow_missing=True):
    """静态来源读取入口；能力只限已声明根及其后代，不能用于目的地或恢复目标。"""
    source = Path(root["source"])
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise BackupError("来源相对路径越界，未读取。")
    trusted = root.get("_snapshot_source")
    if trusted is None:
        return check_path(source / relative, allow_missing=allow_missing)
    check_snapshot_source(source, trusted)
    target = source / relative
    return check_snapshot_source(target, _SnapshotSource(target), allow_missing=allow_missing)


def check_path(path, allow_missing=True):
    """拒绝链接、联接和重解析路径，连父目录也检查。"""
    if _device_namespace(path):
        raise BackupError("普通来源、备份位置或恢复目标不能使用Windows设备命名空间。")
    p = Path(path).absolute()
    for part in [p, *p.parents]:
        if is_reparse(part):
            raise BackupError(f"备份路径含符号链接或目录联接，未操作：{part}")
    if not allow_missing and not os.path.exists(io_path(p)):
        raise BackupError(f"路径不存在：{p}")
    return p


def ensure_work_dir(path=None):
    """安全创建显式临时作业父目录；首次备份/恢复不能依赖之前已运行过。"""
    if path is None:
        return None
    directory = check_path(path)
    try:
        if not os.path.exists(io_path(directory)):
            Path(io_path(directory)).mkdir(parents=True, exist_ok=True)
        directory = check_path(directory, allow_missing=False)
        if not os.path.isdir(io_path(directory)):
            raise BackupError(f"临时作业位置不是目录：{directory}")
    except OSError as exc:
        raise BackupError(f"临时作业目录无法安全创建：{directory}：{exc}") from exc
    return directory


def atomic_json(path, value):
    p = check_path(path)
    Path(io_path(p.parent)).mkdir(parents=True, exist_ok=True)
    temp = p.with_name(p.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with Path(io_path(temp)).open("x", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(io_path(temp), io_path(p))
    finally:
        Path(io_path(temp)).unlink(missing_ok=True)


def read_json(path, default):
    p = check_path(path)
    if not os.path.exists(io_path(p)):
        return default
    try:
        with Path(io_path(p)).open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        raise BackupError(f"备份状态文件无法读取，已保留原文件：{p}：{exc}") from exc


_THREAD_LOCKS = {}
_THREAD_LOCKS_GUARD = threading.Lock()


@contextmanager
def file_lock(path, blocking=False):
    path = check_path(path)
    Path(io_path(path.parent)).mkdir(parents=True, exist_ok=True)
    with _THREAD_LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(str(path), threading.Lock())
    if not thread_lock.acquire(blocking=blocking):
        raise BackupBusy("同一备份任务已经运行，请稍后再试。")
    try:
        with Path(io_path(path)).open("a+b") as f:
            if f.seek(0, 2) == 0:
                f.write(b"\0")
                f.flush()
            f.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except OSError as exc:
                raise BackupBusy("同一备份任务已在其他进程运行。") from exc
            try:
                yield
            finally:
                f.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    finally:
        thread_lock.release()


def _job_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise BackupError("备份任务标识无效。")
    return value


def source_fingerprint(roots):
    values = sorted((str(r["id"]), os.path.normcase(os.path.abspath(str(
        r.get("original_source") or r["source"])))) for r in roots)
    return hashlib.sha256(json.dumps(values, ensure_ascii=True).encode()).hexdigest()


def defaults(kind, machine_id=None):
    if kind not in ("paperpiggy", "zotero"):
        raise BackupError("未知备份任务类型。")
    return {"job_id": uuid.uuid4().hex, "kind": kind,
            "machine_id": machine_id or machine_identity(), "enabled": False,
            "destination": "", "sources": {}, "every_days": 7, "at_time": "07:00",
            "catch_up": True, "keep": 2 if kind == "zotero" else 3,
            "include_index": False, "include_key": False, "include_browser": kind == "paperpiggy",
            "enabled_at": "", "first_planned_at": "",
            "created_at": now_text(), "updated_at": now_text()}


def result_summary(result):
    """状态与历史不复制逐文件清单，避免每次备份重复保存万条哈希。"""
    result = result or {}
    summary = {k: result[k] for k in ("ok", "path", "size", "warnings", "destination",
                                     "journal", "rollback_dirs", "state", "backup_id", "kind",
                                     "created_at", "file_count", "roots", "include_index",
                                     "include_key", "include_browser", "database_count") if k in result}
    manifest = result.get("manifest") or {}
    for key in ("backup_id", "kind", "created_at", "include_index", "include_key", "include_browser"):
        if key in manifest:
            summary[key] = manifest[key]
    if manifest:
        summary["file_count"] = len(manifest.get("files", []))
        summary["roots"] = [r.get("id") for r in manifest.get("roots", [])]
    return summary


def validate_job(job):
    _job_id(job.get("job_id"))
    if job.get("kind") not in ("paperpiggy", "zotero"):
        raise BackupError("未知备份任务类型。")
    if not isinstance(job.get("machine_id"), str) or not job["machine_id"]:
        raise BackupError("备份缺少本机归属。")
    for key in ("enabled", "catch_up", "include_index", "include_key", "include_browser"):
        if not isinstance(job.get(key), bool):
            raise BackupError(f"{key} 必须为布尔值。")
    for key in ("every_days", "keep"):
        val = job.get(key)
        if isinstance(val, bool) or not isinstance(val, int) or not 1 <= val <= 365:
            raise BackupError(f"{key} 必须为 1 到 365 的整数。")
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(job.get("at_time", ""))):
        raise BackupError("备份时间必须为 HH:MM。")
    if not isinstance(job.get("sources"), dict):
        raise BackupError("备份来源格式无效。")
    for value in job["sources"].values():
        if value and (not isinstance(value, str) or not Path(value).is_absolute()):
            raise BackupError("备份来源须为绝对路径。")
    dest = job.get("destination", "")
    if dest and not Path(dest).is_absolute():
        raise BackupError("备份目的地须为绝对路径。")
    if dest and "过时备份" in Path(dest).parts:
        raise BackupError("现有过时备份目录受保护，请为新任务选择其他目录。")
    if job.get("enabled"):
        if not dest:
            raise BackupError("请先选择备份目的地，再启用自动任务。")
        if job["kind"] == "zotero" and not all(job["sources"].get(x) for x in ("data_dir", "profile_dir")):
            raise BackupError("请先选择 Zotero 数据目录和用户配置目录。")
    return job


class JobStore:
    def __init__(self, base_dir=None, machine_id=None):
        self.base = check_path(base_dir or C.DATA / "backup_jobs")
        self.machine_id = machine_id or machine_identity()

    def _lock(self):
        return file_lock(self.base / "locks" / "store.lock", blocking=True)

    def _jobs(self):
        raw = read_json(self.base / "jobs.json", {"schema": 1, "jobs": []})
        if not isinstance(raw, dict) or raw.get("schema") != 1 or not isinstance(raw.get("jobs"), list):
            raise BackupError("备份任务配置格式无效，未覆盖。")
        return raw

    def list(self):
        return [dict(j) for j in self._jobs()["jobs"]]

    def get(self, job_id):
        job_id = _job_id(job_id)
        for j in self.list():
            if j["job_id"] == job_id:
                return j
        raise BackupError("备份任务不存在。")

    def _require_owned(self, job_id):
        job = self.get(job_id)
        if job.get("machine_id") != self.machine_id:
            raise BackupError("这是另一台电脑的只读历史任务，请配置本机新任务；未接管原备份或清理权限。")
        return job

    def ensure_defaults(self, legacy_paperpiggy=None):
        with self._lock():
            raw = self._jobs()
            kinds = {j["kind"] for j in raw["jobs"] if j.get("machine_id") == self.machine_id}
            has_foreign = any(j.get("machine_id") != self.machine_id for j in raw["jobs"])
            changed = False
            for kind in ("paperpiggy", "zotero"):
                if kind in kinds:
                    continue
                job = defaults(kind, self.machine_id)
                if kind == "paperpiggy" and legacy_paperpiggy and not has_foreign:
                    legacy = legacy_paperpiggy
                    for a, b in (("dir", "destination"), ("every_days", "every_days"),
                                 ("keep", "keep"), ("include_index", "include_index"),
                                 ("include_key", "include_key"), ("at_time", "at_time"),
                                 ("catch_up", "catch_up")):
                        if legacy.get(a) is not None:
                            job[b] = legacy[a]
                    job["enabled"] = bool(legacy.get("auto") and job["destination"])
                    if job["enabled"]:
                        job["enabled_at"] = now_text()
                        job["first_planned_at"] = _first_schedule(job["enabled_at"], job["at_time"]).isoformat()
                validate_job(job)
                raw["jobs"].append(job)
                changed = True
            if changed:
                atomic_json(self.base / "jobs.json", raw)
        return self.list()

    def save(self, job_or_id, patch=None):
        with self._lock():
            raw = self._jobs()
            if isinstance(job_or_id, dict):
                update = dict(job_or_id)
                target = update.get("job_id")
            else:
                target = _job_id(job_or_id)
                update = dict(patch or {})
            index = next((i for i, j in enumerate(raw["jobs"]) if j["job_id"] == target), None)
            if index is None:
                if not isinstance(job_or_id, dict):
                    raise BackupError("备份任务不存在。")
                job = defaults(update.get("kind"), self.machine_id)
                if target:
                    job["job_id"] = _job_id(target)
            else:
                job = dict(raw["jobs"][index])
                if job.get("machine_id") != self.machine_id:
                    raise BackupError("另一台电脑的历史任务只读，请配置本机新任务。")
                if self.status(job["job_id"]).get("running"):
                    raise BackupBusy("任务正在运行，请完成或停止后再修改备份设置。")
            was_enabled = job["enabled"]
            old_schedule = (job["every_days"], job["at_time"])
            job.setdefault("enabled_at", "")
            job.setdefault("first_planned_at", "")
            immutable = {"job_id", "kind", "machine_id", "created_at", "enabled_at", "first_planned_at"}
            allowed = set(job) - immutable - {"updated_at"}
            for k in update:
                if k not in set(job):
                    raise BackupError(f"未知备份设置：{k}")
                if k in allowed:
                    job[k] = update[k]
            job["updated_at"] = now_text()
            validate_job(job)
            if job["enabled"] and (not was_enabled or not job["enabled_at"] or
                                   old_schedule != (job["every_days"], job["at_time"])):
                job["enabled_at"] = job["updated_at"]
                job["first_planned_at"] = _first_schedule(job["enabled_at"], job["at_time"]).isoformat()
            if index is None:
                raw["jobs"].append(job)
            else:
                raw["jobs"][index] = job
            atomic_json(self.base / "jobs.json", raw)
        return dict(job)

    def status(self, job_id):
        return read_json(self.base / "status" / (_job_id(job_id) + ".json"),
                         {"job_id": job_id, "running": False, "state": "idle"})

    def set_status(self, job_id, **values):
        self._require_owned(job_id)
        if "result" in values:
            values["result"] = result_summary(values["result"])
        with self._lock():
            state = self.status(job_id)
            state.update(values)
            state["updated_at"] = now_text()
            atomic_json(self.base / "status" / (_job_id(job_id) + ".json"), state)
        return state

    def history(self, job_id, limit=100):
        data = read_json(self.base / "history" / (_job_id(job_id) + ".json"), [])
        return data[-max(1, min(int(limit), 1000)):]

    def _event(self, job_id, event):
        path = self.base / "history" / (_job_id(job_id) + ".json")
        rows = read_json(path, [])
        rows.append({"at": now_text(), **event})
        atomic_json(path, rows[-1000:])

    @contextmanager
    def claim(self, job_id, trigger="manual", slot=None, request_id=None):
        job = self.get(job_id)
        if job["machine_id"] != self.machine_id:
            raise BackupError("这是另一台电脑的任务，未接管执行。")
        with file_lock(self.base / "locks" / (job_id + ".lock")):
            # 与 cancel 的状态写入共用store锁，防止排队取消恰好在claim时被清掉。
            with self._lock():
                state = self.status(job_id)
                if state.get("running") and not state.get("pending"):
                    self._event(job_id, {"state": "interrupted", "reason": "上次任务意外中断；已有成功备份未改动。"})
                attempts = list(state.get("attempted_slots") or [])
                if slot and slot not in attempts:
                    attempts.append(slot)
                cancelled = bool(request_id and state.get("request_id") == request_id and state.get("cancel_requested"))
                state.update(running=True, state="running", trigger=trigger,
                             started_at=now_text(), error="", slot=slot, updated_at=now_text(),
                             attempted_slots=attempts[-64:], cancel_requested=cancelled,
                             pending=False, queued=False, pid=os.getpid())
                atomic_json(self.base / "status" / (job_id + ".json"), state)
                self._event(job_id, {"state": "started", "trigger": trigger, "slot": slot})
            try:
                yield job
            except BackupCancelled as exc:
                self.finish(job_id, "cancelled", str(exc))
                raise
            except Exception as exc:
                self.finish(job_id, "failed", str(exc))
                raise
            finally:
                if self.status(job_id).get("running"):
                    self.finish(job_id, "interrupted", "任务未完成成功登记，已有成功版本未改动。")

    def finish(self, job_id, state, error="", result=None):
        result = result_summary(result)
        view = self.set_status(job_id, running=False, state=state, error=error,
                               finished_at=now_text(), pending=False, queued=False, result=result or {})
        with self._lock():
            self._event(job_id, {"state": state, "error": error,
                                 "result": result or {}, "slot": view.get("slot")})
        return view

    def cancel(self, job_id):
        job_id = _job_id(job_id)
        self._require_owned(job_id)
        with self._lock():
            state = self.status(job_id)
            if not state.get("running"):
                return state
            state.update(cancel_requested=True, updated_at=now_text())
            atomic_json(self.base / "status" / (job_id + ".json"), state)
            return state

    def check_cancel(self, job_id):
        if self.status(job_id).get("cancel_requested"):
            raise BackupCancelled("用户已停止备份；已有成功版本未改动。")

    def ledger(self, job_id):
        return read_json(self.base / "ledger" / (_job_id(job_id) + ".json"), [])

    def record_success(self, job_id, result):
        job = self._require_owned(job_id)
        manifest = result["manifest"]
        if (manifest.get("job_id") != job_id or manifest.get("machine_id") != self.machine_id
                or manifest.get("kind") != job["kind"]):
            raise BackupError("备份包任务归属不匹配，未登记成功。")
        path = check_path(result["path"], allow_missing=False)
        if path.parent != Path(job["destination"]).absolute():
            raise BackupError("备份文件不在本任务目的地，未登记或清理。")
        from backup_archive import inspect as inspect_archive, file_sha256
        checked = inspect_archive(path)
        if checked["manifest"] != manifest:
            raise BackupError("备份包校验结果与待登记内容不同。")
        record = {"backup_id": manifest["backup_id"], "job_id": job_id,
                  "machine_id": self.machine_id, "source_fingerprint": manifest["source_fingerprint"],
                  "created_at": manifest["created_at"], "path": str(path),
                  "sha256": file_sha256(path), "size": path.stat().st_size}
        record.update({key: bool(manifest.get(key)) for key in
                       ("include_browser", "include_index", "include_key")})
        with self._lock():
            rows = self.ledger(job_id)
            if not any(x["backup_id"] == record["backup_id"] for x in rows):
                rows.append(record)
                atomic_json(self.base / "ledger" / (job_id + ".json"), rows)
        warnings = self.prune(job_id, source=record["source_fingerprint"])
        final = {**result, "warnings": list(result.get("warnings", [])) + warnings}
        self.set_status(job_id, last_success=manifest["created_at"])
        self.finish(job_id, "success_with_warning" if final["warnings"] else "success", result=final)
        return final

    def prune(self, job_id, source=None):
        from backup_archive import file_sha256, _validate_structure
        import zipfile
        job = self._require_owned(job_id)
        warnings = []
        with self._lock():
            rows = self.ledger(job_id)
        if source is None:
            owned = [x for x in rows if x.get("machine_id") == self.machine_id
                     and x.get("job_id") == job_id]
            if not owned:
                return []
            source = owned[-1]["source_fingerprint"]
        candidates = [x for x in rows if x.get("machine_id") == self.machine_id
                      and x.get("job_id") == job_id and x.get("source_fingerprint") == source]
        # 哈希大量备份包时不持全局配置锁，其他任务仍可写入进度及失败记录。
        candidates.reverse()
        removed = set()
        retained_valid = 0
        for item in candidates:
            try:
                path = check_path(item["path"], allow_missing=False)
                if "过时备份" in path.parts or path.parent != Path(job["destination"]).absolute():
                    raise BackupError("目录归属改变，未清理")
                before = path.stat()
                if before.st_size != item["size"] or file_sha256(path) != item["sha256"]:
                    raise BackupError("文件已被修改，未清理")
                with zipfile.ZipFile(path) as archive:
                    m = _validate_structure(archive)
                if any(m.get(k) != item.get(k) for k in
                       ("backup_id", "job_id", "machine_id", "source_fingerprint")):
                    raise BackupError("清单归属不匹配，未清理")
                if retained_valid < job["keep"]:
                    retained_valid += 1
                    continue
                after = path.stat()
                if (before.st_ino, before.st_size, before.st_mtime_ns) != (
                        after.st_ino, after.st_size, after.st_mtime_ns):
                    raise BackupError("校验后文件变化，未清理")
                if self.get(job_id)["destination"] != job["destination"]:
                    raise BackupError("任务目的地发生变化，未清理")
                path.unlink()
                removed.add(item["backup_id"])
            except Exception as exc:
                warnings.append(f"备份成功，旧版清理失败：{item.get('path')}：{exc}")
        if removed:
            with self._lock():
                current_rows = self.ledger(job_id)
                atomic_json(self.base / "ledger" / (job_id + ".json"),
                            [x for x in current_rows if x["backup_id"] not in removed])
        return warnings

    def note_login(self, login_id, now=None):
        """记录会话起点。禁用登录补跑时跳过此前错过的槽，但不跳过本会话未来任务。"""
        now = now or datetime.now().astimezone()
        if now.tzinfo is None:
            now = now.astimezone()
        session = str(login_id or now.date())
        for job in self.list():
            if job.get("machine_id") != self.machine_id:
                continue
            state = self.status(job["job_id"])
            if state.get("login_session") != session:
                self.set_status(job["job_id"], login_session=session,
                                session_started_at=now.isoformat(), schedule_not_before=now.isoformat())

    def due(self, job_id, now=None, trigger="timer", login_id=None):
        job = self.get(job_id)
        if job.get("machine_id") != self.machine_id:
            return {"due": False, "reason": "foreign_machine"}
        now = now or datetime.now().astimezone()
        if now.tzinfo is None:
            now = now.astimezone()
        if trigger == "login":
            self.note_login(login_id, now)
        state = self.status(job_id)
        if state.get("pending") or state.get("queued") or state.get("state") in ("pending", "queued"):
            return {"due": False, "reason": "pending"}
        if state.get("running"):
            try:
                with file_lock(self.base / "locks" / (job_id + ".lock")):
                    self.finish(job_id, "interrupted", "上次任务已中断，成功备份未改动。")
                    state = self.status(job_id)
            except BackupBusy:
                return {"due": False, "reason": "running"}
        if not job["enabled"] or state.get("running") or job["machine_id"] != self.machine_id:
            return {"due": False, "reason": "disabled_or_running"}
        hour, minute = map(int, job["at_time"].split(":"))
        today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        last = state.get("last_success")
        enabled = job.get("enabled_at") or job.get("updated_at") or job["created_at"]
        first = datetime.fromisoformat(job["first_planned_at"]).astimezone(now.tzinfo) if job.get("first_planned_at") else _first_schedule(enabled, job["at_time"]).astimezone(now.tzinfo)
        if last and datetime.fromisoformat(last) >= datetime.fromisoformat(enabled):
            last_dt = datetime.fromisoformat(last).astimezone(now.tzinfo)
            planned = last_dt.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=job["every_days"])
        else:
            planned = first
        if not job["catch_up"] and state.get("schedule_not_before"):
            boundary = datetime.fromisoformat(state["schedule_not_before"]).astimezone(now.tzinfo)
            if planned < boundary:
                days = max(0, (boundary.date() - planned.date()).days)
                planned += timedelta(days=(days // job["every_days"]) * job["every_days"])
                if planned < boundary:
                    planned += timedelta(days=job["every_days"])
        if now < planned:
            return {"due": False, "next_at": planned.isoformat()}
        if trigger == "login" and not job["catch_up"]:
            return {"due": False, "reason": "catch_up_disabled"}
        # 失败不会推进last_success，但同一个定时时刻只尝试一次；新登录可再补一次。
        slot = ("login:" + str(login_id or now.date()) if trigger == "login" else
                "timer:" + today.isoformat())
        if state.get("slot") == slot or slot in (state.get("attempted_slots") or []):
            return {"due": False, "reason": "already_attempted", "slot": slot}
        return {"due": True, "slot": slot, "planned_at": planned.isoformat()}

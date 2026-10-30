"""固定受保护更新通道。普通应用只投递资料，管理员任务仅执行公钥验签的组件 PE。

高权端不导入应用目录源码，不接受命令参数、安装目标或公钥覆盖。业务数据始终由
原备份任务的普通文件令牌处理。失败保留旧 current、原任务及所有已有备份。
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import time

import backup_service as S

CHANNEL = "component-update-channel.json"
STATE = "component-update-state.json"
REQUEST = "backup_work/component-update-request.json"
CLIENT_ERROR = "backup_work/component-update-client-error.json"
TRANSACTION = "component-update-transaction.json"
MAX_HELPER = 256 * 1024 * 1024


def _read_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def _channel(data_dir):
    data, sid, instance = S._instance(data_dir)
    path = instance / CHANNEL
    if not path.is_file():
        return data, sid, instance, None
    S._no_links(path)
    S._check_protected_directory(path)
    channel = _read_json(path)
    if (channel.get("schema") != 1 or channel.get("owner_sid") != sid
            or S._canonical(channel.get("data_dir", "")) != S._canonical(data)
            or channel.get("task_name") != "PaperPiggyBackupUpdate-" + instance.name):
        raise S.ServiceError("组件更新通道关联无效，请重新授权修复。")
    manifest = Path(channel["manifest_path"])
    if (manifest.name != "payload.json" or manifest.parent.parent != instance
            or not re.fullmatch(r"[a-f0-9]{64}", manifest.parent.name)
            or S._sha(manifest) != channel["manifest_sha256"]):
        raise S.ServiceError("受保护的组件更新入口校验失败。")
    return data, sid, instance, channel


def _current_manifest(instance):
    record = _read_json(Path(instance) / "current.json")
    if not record or not re.fullmatch(r"[a-f0-9]{64}", str(record.get("payload_id", ""))):
        raise S.ServiceError("找不到可更新的已安装组件。")
    path = Path(instance) / record["payload_id"] / "payload.json"
    if S._sha(path) != record["manifest_sha256"]:
        raise S.ServiceError("现有组件关联清单校验失败。")
    return record, _read_json(path)


def channel_status(data_dir):
    _, _, instance, channel = _channel(data_dir)
    state = _read_json(instance / STATE, {})
    # 请求文件属于普通用户，仅用于显示待处理状态，绝不把它当作高权成功记录。
    data = Path(data_dir)
    display_error = ""
    try:
        request = _read_json(data / REQUEST, {})
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
    except (OSError, ValueError):
        request = {}
        display_error = "待处理的组件更新请求损坏；原备份任务继续运行，可点击重试。"
    if channel and request.get("request_id") and request.get("request_id") != state.get("request_id"):
        state = {"state": "pending", "pending": True, "error": "",
                 "request_id": request.get("request_id"), "helper_sha256": request.get("helper_sha256"),
                 "target_version": request.get("target_version")}
    try:
        client = _read_json(data / CLIENT_ERROR, {})
        if not isinstance(client, dict):
            raise ValueError("client state must be an object")
    except (OSError, ValueError):
        client = {}
        display_error = "组件更新客户端记录损坏；原备份任务继续运行，可点击重试。"
    client_time = client.get("updated_at", 0)
    state_time = state.get("updated_at", 0)
    if (client.get("error") and isinstance(client_time, (int, float)) and client_time > state_time
            and state.get("state") not in ("pending", "deferred", "updating")):
        state = {**client, "state": "failed"}
    if display_error:
        state = {**state, "state": "failed", "error": display_error}
    state.setdefault("state", "idle")
    state.setdefault("error", "")
    state["pending"] = state["state"] in ("pending", "deferred", "updating")
    if channel:
        _, manifest = _current_manifest(instance)
        state["component_version"] = manifest.get("component_version", 0)
    return {"auto_update_supported": bool(channel), "auto_update": state}


def ensure_channel(manifest_path, *, enabled=True):
    """仅原生安装器调用；已有通道保持初始保护代，不能被新业务入口替换。"""
    manifest_path = Path(manifest_path)
    manifest = S._verified_manifest(manifest_path)
    data, sid, instance, existing = _channel(manifest["data_dir"])
    if existing:
        return existing
    xml, name = S.task_xml(data, manifest_path.parent, sid, enabled=enabled, update_channel=True)
    sddl = f"O:BAG:BAD:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGX;;;{sid})"
    with S._scheduler() as folder:
        task = folder.invoke("RegisterTask", name, xml, 22, sid, None, 3, sddl)
        if not task:
            raise S.ServiceError("未建立独立的组件更新任务。")
        task.close()
    record = {"schema": 1, "owner_sid": sid, "data_dir": str(data), "task_name": name,
              "manifest_path": str(manifest_path), "manifest_sha256": S._sha(manifest_path),
              "installed_at": time.time()}
    S._atomic(instance / CHANNEL, record, protected=True)
    return record


def _package_paths():
    root = Path(__file__).absolute().parent
    for directory in (root, root.parent / "build/backup-component"):
        helper, envelope = directory / "backup_component.exe", directory / "backup_component.sig.json"
        if helper.is_file() and envelope.is_file():
            return envelope, helper
    raise S.ServiceError("本次应用缺少官方签名的备份组件更新包，请使用完整官方安装包。")


def _read_user_file(path, maximum):
    path = Path(path)
    if not path.is_absolute() or str(path).startswith("\\\\"):
        raise S.ServiceError("组件更新包必须位于本机绝对路径。")
    S._no_links(path)
    with path.open("rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise S.ServiceError("组件更新包大小或类型无效。")
        raw = stream.read(maximum + 1)
        if len(raw) > maximum:
            raise S.ServiceError("组件更新包在读取期间增大，已拒绝。")
        return raw


def _verify_envelope(raw):
    from backup_component_auth import verify_envelope
    return verify_envelope(raw)


def request_update(data_dir, envelope_path=None, helper_path=None, *, retry=False):
    """普通端验证失败持久可见；同一份坏包不会随界面轮询反复投递。"""
    data, _, _, channel = _channel(data_dir)
    if not channel:
        raise S.ServiceError("请先完成一次管理员授权迁移，之后组件升级自动完成。")
    fingerprint = None
    try:
        if envelope_path is None and helper_path is None:
            envelope_path, helper_path = _package_paths()
        if envelope_path is not None and helper_path is not None:
            parts = []
            for path in (Path(envelope_path), Path(helper_path)):
                info = path.stat()
                parts.append((str(path.absolute()), info.st_size, info.st_mtime_ns))
            fingerprint = hashlib.sha256(json.dumps(parts).encode()).hexdigest()
        previous = _read_json(data / CLIENT_ERROR, {})
        if not retry and previous.get("error") and previous.get("fingerprint") == fingerprint:
            return {"ok": False, "pending": False, "state": "failed", **previous}
        result = _request_update(data, envelope_path, helper_path, retry=retry)
        work = data / "backup_work"
        S._no_links(work); work.mkdir(exist_ok=True)
        S._atomic(data / CLIENT_ERROR, {})
        return result
    except Exception as exc:
        try:
            work = data / "backup_work"
            S._no_links(work); work.mkdir(exist_ok=True)
            S._atomic(data / CLIENT_ERROR, {"state": "failed", "pending": False,
                "error": str(exc), "fingerprint": fingerprint, "updated_at": time.time()})
        except OSError:
            pass
        raise


def _request_update(data_dir, envelope_path=None, helper_path=None, *, retry=False):
    data, _, instance, channel = _channel(data_dir)
    if not channel:
        raise S.ServiceError("请先完成一次管理员授权迁移，之后组件升级自动完成。")
    if envelope_path is None and helper_path is None:
        envelope_path, helper_path = _package_paths()
    elif envelope_path is None or helper_path is None:
        raise S.ServiceError("组件更新包及签名必须同时提供。")
    manifest = _verify_envelope(_read_user_file(envelope_path, 1024 * 1024))
    if manifest["helper_size"] > MAX_HELPER:
        raise S.ServiceError("签名组件超过允许大小。")
    raw = _read_user_file(helper_path, manifest["helper_size"])
    if len(raw) != manifest["helper_size"] or hashlib.sha256(raw).hexdigest() != manifest["helper_sha256"]:
        raise S.ServiceError("组件更新包与官方签名不一致。")
    _, current = _current_manifest(instance)
    floor = current.get("component_version", 0)
    if manifest["component_version"] < floor:
        raise S.ServiceError("拒绝回退到更旧的后台组件。")
    if manifest["component_version"] == floor:
        if manifest["build_id"] != current.get("build_id"):
            raise S.ServiceError("同一组件版本对应不同内容，已拒绝更新。")
        return {"ok": True, "pending": False, "state": "success", "already_current": True}
    state = channel_status(data)["auto_update"]
    if state.get("helper_sha256") == manifest["helper_sha256"] and (
            state["pending"] or state["state"] == "success" or (state["state"] == "failed" and not retry)):
        return {"ok": state["state"] != "failed", **state}
    request = {"schema": 1, "request_id": secrets.token_hex(16),
               "envelope_path": str(Path(envelope_path).absolute()), "helper_path": str(Path(helper_path).absolute()),
               "helper_sha256": manifest["helper_sha256"], "target_version": manifest["component_version"],
               "created_at": time.time()}
    S._no_links(data / "backup_work")
    (data / "backup_work").mkdir(exist_ok=True)
    S._atomic(data / REQUEST, request)
    result = subprocess.run([str(S._system_dir() / "schtasks.exe"), "/Run", "/TN", channel["task_name"]],
                            capture_output=True, creationflags=S.NO_WINDOW)
    if result.returncode:
        raise S.ServiceError("未能立即唤醒组件更新任务；它将在下次后台检查时重试。")
    return {"ok": True, "pending": True, "state": "pending", "request_id": request["request_id"]}


def _secure_directory(path):
    path = Path(path)
    S._no_links(path)
    if path.exists():
        S._check_protected_directory(path)
        return
    kernel, adv = S._libraries()
    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("descriptor", ctypes.c_void_p), ("inherit", wintypes.BOOL)]
    descriptor = ctypes.c_void_p()
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p)
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    kernel.CreateDirectoryW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(SECURITY_ATTRIBUTES))
    kernel.CreateDirectoryW.restype = wintypes.BOOL
    sddl = f"O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;{S.current_sid()})"
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
        raise S.ServiceError("无法准备受保护的更新暂存目录。")
    try:
        security = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), descriptor, False)
        if not kernel.CreateDirectoryW(str(path), ctypes.byref(security)):
            raise S.ServiceError("无法原子创建受保护的更新暂存目录。")
    finally:
        kernel.LocalFree(descriptor)
    S._check_protected_directory(path)


def _save_state(instance, request, state, **extra):
    request = request if isinstance(request, dict) else {}
    value = {"state": state, "error": "", "request_id": request.get("request_id"),
             "helper_sha256": request.get("helper_sha256"), "target_version": request.get("target_version"),
             "updated_at": time.time(), **extra}
    S._atomic(Path(instance) / STATE, value, protected=True)
    return value


def _backup_task(folder, instance):
    return folder.invoke("GetTask", "PaperPiggyBackup-" + Path(instance).name)


def _active(task):
    collection = task.invoke("GetInstances", 0)
    try:
        return int(collection.invoke("Count", flags=2))
    finally:
        collection.close()


def _restore_backup(folder, instance, old_record, old_xml):
    sid = S.current_sid()
    task = folder.invoke("RegisterTask", "PaperPiggyBackup-" + instance.name, old_xml, 22, sid, None, 3,
                         f"O:BAG:BAD:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGX;;;{sid})")
    if not task:
        raise S.ServiceError("未恢复原后台任务，已保留恢复记录等待下次重试。")
    task.close()
    S._atomic(instance / "current.json", old_record, protected=True)


def _recover_transaction(instance):
    """恢复尚未提交的保护事务；不依赖可写请求文件仍然存在。"""
    transaction = _read_json(instance / TRANSACTION, {})
    if transaction.get("phase") in (None, "committed", "rolled_back"):
        return True
    if transaction.get("phase") != "prepared":
        raise S.ServiceError("后台更新事务阶段无效，未修改备份任务。")
    if (transaction.get("schema") != 1 or transaction.get("instance") != instance.name
            or not isinstance(transaction.get("old_xml"), str)
            or not isinstance(transaction.get("old_record"), dict)):
        raise S.ServiceError("后台更新恢复记录无效，未修改备份任务。")
    with S._scheduler() as folder:
        task = _backup_task(folder, instance)
        try:
            if _active(task):
                return False
        finally:
            task.close()
        _restore_backup(folder, instance, transaction["old_record"], transaction["old_xml"])
    S._atomic(instance / TRANSACTION, {**transaction, "phase": "rolled_back", "recovered_at": time.time()}, protected=True)
    return True


def _run_helper(arguments, cwd, timeout=300):
    """只管理本次签名 PE 的 JobObject；暂停创建，先归入 Job，再允许执行。"""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    SIZE_T = ctypes.c_size_t
    class BASIC_LIMIT(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
            ("flags", wintypes.DWORD), ("minimum_ws", SIZE_T), ("maximum_ws", SIZE_T),
            ("active_limit", wintypes.DWORD), ("affinity", SIZE_T), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
    class LIMIT(ctypes.Structure):
        _fields_ = [("basic", BASIC_LIMIT), ("io", IO), ("process_memory", SIZE_T),
            ("job_memory", SIZE_T), ("peak_process", SIZE_T), ("peak_job", SIZE_T)]
    class ACCOUNTING(ctypes.Structure):
        _fields_ = [(n, ctypes.c_int64) for n in ("user_time", "kernel_time", "period_user", "period_kernel")] + [
            (n, wintypes.DWORD) for n in ("page_faults", "total", "active", "terminated")]
    class STARTUP(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("reserved", wintypes.LPWSTR), ("desktop", wintypes.LPWSTR),
            ("title", wintypes.LPWSTR)] + [(n, wintypes.DWORD) for n in ("x", "y", "xsize", "ysize", "xchars", "ychars", "fill", "flags")] + [
            ("show", wintypes.WORD), ("reserved_size", wintypes.WORD), ("reserved_bytes", ctypes.c_void_p),
            ("input", wintypes.HANDLE), ("output", wintypes.HANDLE), ("error", wintypes.HANDLE)]
    class PROCESS(ctypes.Structure):
        _fields_ = [("process", wintypes.HANDLE), ("thread", wintypes.HANDLE), ("pid", wintypes.DWORD), ("tid", wintypes.DWORD)]
    kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR); kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD); kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p); kernel.QueryInformationJobObject.restype = wintypes.BOOL
    kernel.CreateProcessW.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL,
        wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(STARTUP), ctypes.POINTER(PROCESS)); kernel.CreateProcessW.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE); kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.ResumeThread.argtypes = (wintypes.HANDLE,); kernel.ResumeThread.restype = wintypes.DWORD
    kernel.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT); kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT); kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD); kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)); kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,); kernel.CloseHandle.restype = wintypes.BOOL
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise S.ServiceError("无法创建本次组件安装的进程保护容器。")
    process = PROCESS(); assigned = False
    def active():
        account = ACCOUNTING()
        if not kernel.QueryInformationJobObject(job, 1, ctypes.byref(account), ctypes.sizeof(account), None):
            raise S.ServiceError("无法核对组件安装子进程是否结束。")
        return account.active
    try:
        limits = LIMIT(); limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise S.ServiceError("无法保护组件安装子进程生命周期。")
        startup = STARTUP(); startup.size = ctypes.sizeof(startup)
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline(arguments))
        if not kernel.CreateProcessW(str(arguments[0]), command, None, None, False, S.NO_WINDOW | 4,
                None, str(cwd), ctypes.byref(startup), ctypes.byref(process)):
            raise S.ServiceError("无法启动已验证的组件安装器。")
        if not kernel.AssignProcessToJobObject(job, process.process):
            # 此时只存在由本次 CreateProcess 返回的暂停进程，还没有运行过任何代码。
            kernel.TerminateProcess(process.process, 75)
            kernel.WaitForSingleObject(process.process, 10000)
            raise S.ServiceError("组件安装进程无法进入保护容器，未开始执行。")
        assigned = True
        if kernel.ResumeThread(process.thread) == 0xffffffff:
            raise S.ServiceError("无法恢复已验证的组件安装进程。")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if kernel.WaitForSingleObject(process.process, 200) == 0 and not active():
                code = wintypes.DWORD()
                if not kernel.GetExitCodeProcess(process.process, ctypes.byref(code)):
                    raise S.ServiceError("无法读取组件安装结果。")
                return int(code.value)
            time.sleep(.02)
        raise S.ServiceError("组件自动更新超时，已结束本次安装并恢复原版本。")
    finally:
        if assigned:
            try:
                if active():
                    kernel.TerminateJobObject(job, 75)
                    deadline = time.monotonic() + 10
                    while active() and time.monotonic() < deadline:
                        time.sleep(.05)
                    if active():
                        # 不能一边让子进程继续注册任务、一边回滚。退出让 Windows 关闭
                        # KILL_ON_JOB_CLOSE 容器，下次稳定任务依据保护 journal 恢复。
                        kernel.CloseHandle(job)
                        os._exit(74)
            except Exception:
                kernel.CloseHandle(job)
                os._exit(74)
        for handle in (process.thread, process.process, job):
            if handle:
                kernel.CloseHandle(handle)


def _apply(data, instance, request):
    # 所有可变来源的读取限于桌面用户令牌；高权端只使用已经取得的字节副本。
    with S.restricted_user():
        envelope = _read_user_file(request["envelope_path"], 1024 * 1024)
        manifest = _verify_envelope(envelope)
        if manifest["helper_size"] > MAX_HELPER:
            raise S.ServiceError("签名组件超过允许大小。")
        helper = _read_user_file(request["helper_path"], manifest["helper_size"])
    old_record, current = _current_manifest(instance)
    if manifest["component_version"] <= current.get("component_version", 0):
        if (manifest["component_version"] == current.get("component_version") and manifest["build_id"] == current.get("build_id")):
            return _save_state(instance, request, "success", already_current=True)
        raise S.ServiceError("后台拒绝组件版本回放或同版本替换。")
    if (request.get("helper_sha256") != manifest["helper_sha256"]
            or request.get("target_version") != manifest["component_version"]
            or len(helper) != manifest["helper_size"]
            or hashlib.sha256(helper).hexdigest() != manifest["helper_sha256"]):
        raise S.ServiceError("组件来源在投递后改变或与签名不匹配。")
    base = instance / "component-update-staging"
    _secure_directory(base)
    stage = base / secrets.token_hex(16)
    _secure_directory(stage)
    staged_helper, staged_envelope = stage / "component.exe", stage / "component.sig.json"
    S._protected_bytes(staged_helper, helper)
    S._protected_bytes(staged_envelope, envelope)
    if (_verify_envelope(staged_envelope.read_bytes()) != manifest
            or S._sha(staged_helper) != manifest["helper_sha256"]):
        raise S.ServiceError("受保护暂存组件校验失败，未执行安装。")
    S._check_protected_directory(staged_helper)
    with S._scheduler() as folder:
        task = _backup_task(folder, instance)
        try:
            if _active(task):
                return _save_state(instance, request, "deferred", reason="备份任务运行中，等待空闲")
            old_xml = task.invoke("Xml", flags=2)
            was_enabled = bool(task.invoke("Enabled", flags=2))
        finally:
            task.close()
        generation = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
        arguments = [str(staged_helper), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/SP-", "/NORESTART",
                     "/DATA=" + str(data), "/SID=" + S.current_sid(), "/INSTANCE=" + instance.name,
                     "/GENERATION=" + generation]
        transaction = {"schema": 1, "instance": instance.name, "phase": "prepared",
            "request_id": request["request_id"], "old_record": old_record, "old_xml": old_xml,
            "was_enabled": was_enabled, "generation": generation, "created_at": time.time()}
        # 保护恢复件落盘成功之前不暂停任务。此后任何异常都在事务覆盖范围内。
        S._atomic(instance / TRANSACTION, transaction, protected=True)
        try:
            task = _backup_task(folder, instance)
            try:
                task.invoke("Enabled", False, flags=4)
            finally:
                task.close()
            _save_state(instance, request, "updating", generation=generation)
            # 固定参数、固定受保护 PE；不经 shell，不使用 runas，不传用户命令。
            result = _run_helper(arguments, stage)
            if result:
                raise S.ServiceError(f"签名组件安装未完成（退出码{result}），已保留原版本。")
            new_record, installed = _current_manifest(instance)
            expected = {"code/" + k: v for k, v in manifest["source_hashes"].items()}
            expected.update({"runtime/" + k: v for k, v in manifest["runtime_hashes"].items()})
            actual = {row["path"]: row["sha256"] for row in installed["files"]}
            if (new_record["payload_id"] != generation or installed.get("build_id") != manifest["build_id"]
                    or installed.get("component_version") != manifest["component_version"] or actual != expected):
                raise S.ServiceError("安装结果与已签名组件清单不一致，已恢复原版本。")
            S._verified_manifest(instance / generation / "payload.json")
            if not was_enabled:
                current_task = _backup_task(folder, instance)
                try:
                    current_task.invoke("Enabled", False, flags=4)
                finally:
                    current_task.close()
            S._atomic(instance / TRANSACTION, {**transaction, "phase": "committed", "committed_at": time.time()}, protected=True)
        except BaseException:
            _restore_backup(folder, instance, old_record, old_xml)
            S._atomic(instance / TRANSACTION, {**transaction, "phase": "rolled_back", "recovered_at": time.time()}, protected=True)
            raise
    return _save_state(instance, request, "success", component_version=manifest["component_version"], generation=generation)


def run_update(data_dir, entry_manifest):
    data, _, instance, channel = _channel(data_dir)
    if not channel or channel["manifest_path"] != str(Path(__file__).absolute().parent.parent / "payload.json"):
        raise S.ServiceError("更新入口不是固定受保护通道。")
    if entry_manifest.get("payload_id") != Path(channel["manifest_path"]).parent.name:
        raise S.ServiceError("更新入口与通道安装代不符。")
    request = {}
    try:
        with S.component_guard(instance) as acquired:
            if not acquired:
                return 0
            if not _recover_transaction(instance):
                return 0
            with S.restricted_user():
                path = data / REQUEST
                if not path.is_file():
                    return 0
                request = json.loads(_read_user_file(path, 32768))
            if (not isinstance(request, dict) or request.get("schema") != 1 or not re.fullmatch(r"[a-f0-9]{32}", str(request.get("request_id", "")))
                    or set(request) != {"schema", "request_id", "envelope_path", "helper_path", "helper_sha256", "target_version", "created_at"}):
                raise S.ServiceError("组件更新请求格式无效。")
            state = _read_json(instance / STATE, {})
            if state.get("request_id") == request["request_id"] and state.get("state") in ("success", "failed"):
                return 0
            _apply(data, instance, request)
    except Exception as exc:
        _save_state(instance, request, "failed", error=str(exc))
        return 1
    return 0

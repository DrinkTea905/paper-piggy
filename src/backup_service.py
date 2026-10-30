# -*- coding: utf-8 -*-
"""备份组件安装、受保护计划任务与低权限 worker 包装。

普通界面不提权。只有显式安装操作显示一次 Windows UAC；计划任务的 Python、
标准库和代码先按哈希复制到 Program Files，并关闭继承写权限。
后台文件处理始终模拟同一交互桌面用户的普通令牌，VSS 模块只在原生快照操作期间提权。
"""
from __future__ import annotations

from contextlib import contextmanager
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

SCHEMA = 1
MODULES = frozenset({"backup_runner.py", "backup_sources.py", "backup_jobs.py",
    "backup_archive.py", "backup_restore.py", "backup_legacy.py", "backup_vss.py", "backup_service.py",
    "backup.py", "config.py", "settings.py", "dedicated_browser.py",
    "backup_component_auth.py", "backup_component_trust.py", "backup_component_update.py"})
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_INSTALL_PROCESSES = {}


class ServiceError(RuntimeError):
    pass


class _GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16),
                ("d", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class _VariantUnion(ctypes.Union):
    _fields_ = [("number", ctypes.c_int32), ("boolean", ctypes.c_int16),
                ("pointer", ctypes.c_void_p),
                ("record", ctypes.c_void_p * 2)]


class _Variant(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("vt", ctypes.c_uint16), ("reserved", ctypes.c_uint16 * 3),
                ("value", _VariantUnion)]


class _DispatchParams(ctypes.Structure):
    _fields_ = [("args", ctypes.POINTER(_Variant)), ("named", ctypes.POINTER(ctypes.c_int32)),
                ("count", wintypes.UINT), ("named_count", wintypes.UINT)]


class _ExceptionInfo(ctypes.Structure):
    _fields_ = [("code", ctypes.c_uint16), ("reserved", ctypes.c_uint16),
                ("source", ctypes.c_void_p), ("description", ctypes.c_void_p),
                ("help", ctypes.c_void_p), ("context", wintypes.DWORD),
                ("extra", ctypes.c_void_p), ("deferred", ctypes.c_void_p), ("scode", ctypes.c_int32)]


def _com_method(pointer, index, types, *args):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    function = ctypes.WINFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, *types)(table[index])
    return function(pointer, *args)


class _Dispatch:
    """最小 IDispatch；仅调用固定 Task Scheduler API，不执行 PowerShell 或任意命令。"""
    def __init__(self, pointer, automation):
        self.pointer, self.automation = pointer, automation

    def close(self):
        if self.pointer:
            _com_method(self.pointer, 2, ())
            self.pointer = None

    def invoke(self, name, *values, flags=1):
        names = (wintypes.LPWSTR * 1)(name)
        member, null_guid = ctypes.c_int32(), _GUID()
        code = _com_method(self.pointer, 5, (ctypes.POINTER(_GUID), ctypes.POINTER(wintypes.LPWSTR),
            wintypes.UINT, wintypes.DWORD, ctypes.POINTER(ctypes.c_int32)), ctypes.byref(null_guid),
            names, 1, 0, ctypes.byref(member))
        if code < 0:
            raise ServiceError(f"无法解析计划任务接口 {name}（0x{code & 0xffffffff:08X}）。")
        args = (_Variant * len(values))()
        result, exception, arg_error = _Variant(), _ExceptionInfo(), wintypes.UINT()
        named = ctypes.c_int32(-3)
        params = _DispatchParams(args, ctypes.pointer(named) if flags == 4 else None,
                                 len(values), 1 if flags == 4 else 0)
        try:
            for item, value in zip(args, reversed(values)):
                if value is None:
                    item.vt = 1  # VT_NULL：计划任务不保存密码。
                elif isinstance(value, bool):
                    item.vt, item.boolean = 11, -1 if value else 0
                elif isinstance(value, int):
                    item.vt, item.number = 3, value
                elif isinstance(value, str):
                    item.vt, item.pointer = 8, self.automation.SysAllocString(value)
                    if not item.pointer:
                        raise ServiceError("无法分配计划任务参数。")
                else:
                    raise ServiceError("计划任务参数类型不受支持。")
            code = _com_method(self.pointer, 6, (ctypes.c_int32, ctypes.POINTER(_GUID),
                wintypes.DWORD, ctypes.c_uint16, ctypes.POINTER(_DispatchParams),
                ctypes.POINTER(_Variant), ctypes.POINTER(_ExceptionInfo), ctypes.POINTER(wintypes.UINT)),
                member.value, ctypes.byref(null_guid), 0, flags, ctypes.byref(params),
                ctypes.byref(result), ctypes.byref(exception), ctypes.byref(arg_error))
            if code < 0:
                detail = ctypes.wstring_at(exception.description) if exception.description else ""
                raise ServiceError(f"计划任务 {name} 失败（0x{code & 0xffffffff:08X}）：{detail}")
            if result.vt == 9:
                pointer, result.vt = result.pointer, 0  # 将引用移交给包装，不重复释放。
                return _Dispatch(pointer, self.automation)
            if result.vt == 8:
                return ctypes.wstring_at(result.pointer) if result.pointer else ""
            if result.vt == 11:
                return bool(result.boolean)
            if result.vt in (3, 19):
                return int(result.number)
            if result.vt in (0, 1):
                return None
            raise ServiceError(f"计划任务返回了不支持的类型 {result.vt}。")
        finally:
            for item in args:
                self.automation.VariantClear(ctypes.byref(item))
            self.automation.VariantClear(ctypes.byref(result))
            for name in ("source", "description", "help"):
                if getattr(exception, name):
                    self.automation.SysFreeString(getattr(exception, name))


@contextmanager
def _scheduler():
    _windows()
    ole = ctypes.OleDLL("ole32")
    ole.CoInitializeEx.argtypes = (ctypes.c_void_p, wintypes.DWORD)
    ole.CoInitializeEx.restype = ctypes.c_int32
    code = int(ole.CoInitializeEx(None, 0))
    if code < 0:
        raise ServiceError(f"无法初始化计划任务 COM（0x{code & 0xffffffff:08X}）。")
    service = folder = None
    try:
        automation = ctypes.WinDLL("oleaut32")
        automation.SysAllocString.argtypes = (wintypes.LPCWSTR,)
        automation.SysAllocString.restype = ctypes.c_void_p
        automation.SysFreeString.argtypes = (ctypes.c_void_p,)
        automation.VariantClear.argtypes = (ctypes.POINTER(_Variant),)
        automation.VariantClear.restype = ctypes.c_int32
        ole.CoCreateInstance.argtypes = (ctypes.POINTER(_GUID), ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))
        ole.CoCreateInstance.restype = ctypes.c_int32
        pointer = ctypes.c_void_p()
        code = int(ole.CoCreateInstance(ctypes.byref(_GUID.parse("0f87369f-a4e5-4cfc-bd3e-73e6154572dd")),
            None, 1, ctypes.byref(_GUID.parse("00020400-0000-0000-c000-000000000046")), ctypes.byref(pointer)))
        if code < 0:
            raise ServiceError(f"无法取得计划任务服务（0x{code & 0xffffffff:08X}）。")
        service = _Dispatch(pointer, automation)
        service.invoke("Connect")
        folder = service.invoke("GetFolder", "\\")
        yield folder
    finally:
        if folder:
            folder.close()
        if service:
            service.close()
        ole.CoUninitialize()


def _windows():
    if sys.platform != "win32":
        raise ServiceError("独立后台备份与 VSS 组件目前需要 Windows。")


def _libraries():
    _windows()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetCurrentThread.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel.LocalFree.restype = ctypes.c_void_p
    adv.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    adv.OpenProcessToken.restype = wintypes.BOOL
    adv.GetTokenInformation.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                       wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    adv.GetTokenInformation.restype = wintypes.BOOL
    adv.ConvertSidToStringSidW.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR))
    adv.ConvertSidToStringSidW.restype = wintypes.BOOL
    return kernel, adv


def _token_info(token, kind):
    _, adv = _libraries()
    size = wintypes.DWORD()
    adv.GetTokenInformation(token, kind, None, 0, ctypes.byref(size))
    if not size.value or size.value > 1048576:
        raise ServiceError("无法核对备份组件的用户身份。")
    buffer = ctypes.create_string_buffer(size.value)
    if not adv.GetTokenInformation(token, kind, buffer, len(buffer), ctypes.byref(size)):
        raise ServiceError("无法核对备份组件的用户权限。")
    return buffer


@contextmanager
def _process_token():
    kernel, adv = _libraries()
    token = wintypes.HANDLE()
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008 | 0x0002, ctypes.byref(token)):
        raise ServiceError("无法打开备份组件用户令牌。")
    try:
        yield token
    finally:
        kernel.CloseHandle(token)


def _sid_string(pointer):
    kernel, adv = _libraries()
    text = wintypes.LPWSTR()
    if not adv.ConvertSidToStringSidW(pointer, ctypes.byref(text)):
        raise ServiceError("无法读取备份组件用户标识。")
    try:
        return text.value
    finally:
        kernel.LocalFree(text)


def current_sid():
    with _process_token() as token:
        info = _token_info(token, 1)  # TOKEN_USER starts with PSID
        return _sid_string(ctypes.c_void_p.from_buffer(info).value)


def _logon_sid():
    class GROUP(ctypes.Structure):
        _fields_ = [("sid", ctypes.c_void_p), ("attributes", wintypes.DWORD)]

    class GROUPS(ctypes.Structure):
        _fields_ = [("count", wintypes.DWORD), ("groups", GROUP * 1)]

    with _process_token() as token:
        buffer = _token_info(token, 2)
        count = wintypes.DWORD.from_buffer(buffer).value
        offset = GROUPS.groups.offset
        if count > 4096 or offset + count * ctypes.sizeof(GROUP) > len(buffer):
            raise ServiceError("登录会话令牌格式无效。")
        for i in range(count):
            item = GROUP.from_buffer(buffer, offset + i * ctypes.sizeof(GROUP))
            if item.attributes & 0xC0000000 == 0xC0000000:
                return _sid_string(item.sid)
    raise ServiceError("无法识别当前 Windows 登录会话。")


def _elevated():
    with _process_token() as token:
        return bool(wintypes.DWORD.from_buffer(_token_info(token, 20)).value)


@contextmanager
def _ordinary_token():
    """取同 SID、同交互会话桌面的真实普通令牌，不使用 Identification 级 linked clone。"""
    kernel, adv = _libraries()
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetShellWindow.argtypes = ()
    user.GetShellWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    pid = wintypes.DWORD()
    window = user.GetShellWindow()
    if not window or not user.GetWindowThreadProcessId(window, ctypes.byref(pid)):
        raise ServiceError("未找到当前交互桌面，不能取得普通用户文件令牌；未运行备份。")
    process = kernel.OpenProcess(0x1000, False, pid.value)
    token = wintypes.HANDLE()
    if not process:
        raise ServiceError("无法核对当前桌面进程；未运行备份。")
    try:
        if not adv.OpenProcessToken(process, 0x000A, ctypes.byref(token)):
            raise ServiceError("无法取得当前桌面的普通令牌；未运行备份。")
    finally:
        kernel.CloseHandle(process)
    try:
        identity = _token_info(token, 1)  # SID 指针指向此缓冲，转换前必须保持其存活。
        actual_sid = _sid_string(ctypes.c_void_p.from_buffer(identity).value)
        with _process_token() as current:
            same_session = (wintypes.DWORD.from_buffer(_token_info(current, 12)).value ==
                            wintypes.DWORD.from_buffer(_token_info(token, 12)).value)
        if (actual_sid != current_sid() or not same_session
                or wintypes.DWORD.from_buffer(_token_info(token, 20)).value
                or wintypes.DWORD.from_buffer(_token_info(token, 8)).value != 1):
            raise ServiceError("桌面令牌不属于同一用户、会话或仍已提升；未使用管理员权限处理文件。")
        yield token
    finally:
        kernel.CloseHandle(token)


@contextmanager
def restricted_user():
    """高权限任务只以低权限用户执行文件业务；同线程内使用，禁止派生未包装线程。"""
    if sys.platform != "win32":
        yield
        return
    if not _elevated():
        yield
        return
    kernel, adv = _libraries()
    adv.ImpersonateLoggedOnUser.argtypes = (wintypes.HANDLE,)
    adv.ImpersonateLoggedOnUser.restype = wintypes.BOOL
    adv.RevertToSelf.restype = wintypes.BOOL
    adv.OpenThreadToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL,
                                    ctypes.POINTER(wintypes.HANDLE))
    adv.OpenThreadToken.restype = wintypes.BOOL
    adv.SetThreadToken.argtypes = (ctypes.POINTER(wintypes.HANDLE), wintypes.HANDLE)
    adv.SetThreadToken.restype = wintypes.BOOL
    previous = wintypes.HANDLE()
    had_previous = bool(adv.OpenThreadToken(kernel.GetCurrentThread(), 0x000C, True,
                                           ctypes.byref(previous)))
    if not had_previous and ctypes.get_last_error() != 1008:
        raise ServiceError("无法核对已有线程令牌；未运行备份。")
    try:
        with _ordinary_token() as ordinary:
            if not adv.ImpersonateLoggedOnUser(ordinary):
                raise ServiceError("无法切换普通用户文件权限；未运行备份。")
            try:
                yield
            finally:
                restored = adv.SetThreadToken(None, previous) if had_previous else adv.RevertToSelf()
                if not restored:
                    os._exit(74)
    finally:
        if had_previous:
            kernel.CloseHandle(previous)


def _system_dir():
    _windows()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetSystemDirectoryW.argtypes = (wintypes.LPWSTR, wintypes.UINT)
    output = ctypes.create_unicode_buffer(32768)
    if not kernel.GetSystemDirectoryW(output, len(output)):
        raise ServiceError("无法找到 Windows 系统工具。")
    return Path(output.value)


def _program_files():
    _windows()
    shell = ctypes.WinDLL("shell32")
    shell.SHGetFolderPathW.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.HANDLE,
                                      wintypes.DWORD, wintypes.LPWSTR)
    shell.SHGetFolderPathW.restype = ctypes.c_int32
    output = ctypes.create_unicode_buffer(32768)
    if shell.SHGetFolderPathW(None, 0x26, None, 0, output) < 0:
        raise ServiceError("无法定位受保护的程序目录。")
    return Path(output.value)


def _canonical(path):
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _no_links(path, existing_only=False):
    p = Path(path)
    for item in (p, *p.parents):
        if not item.exists():
            if existing_only:
                continue
            continue
        data = item.lstat()
        if stat.S_ISLNK(data.st_mode) or getattr(data, "st_file_attributes", 0) & 0x400:
            raise ServiceError(f"备份组件路径不能包含符号链接或目录联接：{item}")


def _identity(data_dir):
    data = Path(data_dir)
    if not data.is_absolute() or str(data).startswith("\\\\"):
        raise ServiceError("备份数据家必须是本机绝对路径。")
    data = data.absolute()
    _no_links(data)
    sid = current_sid()
    digest = hashlib.sha256((sid + "\n" + _canonical(data)).encode()).hexdigest()[:20]
    return data, sid, digest


def _instance(data_dir):
    data, sid, identifier = _identity(data_dir)
    return data, sid, _program_files() / "PaperPiggyBackup" / identifier


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def _safe_relative(value):
    raw = str(value)
    p = PurePosixPath(raw)
    if (not raw or "\\" in raw or ":" in raw or "\x00" in raw or p.is_absolute()
            or any(part in ("", ".", "..") for part in p.parts) or p.as_posix() != raw):
        raise ServiceError("备份组件载荷路径无效。")
    return p


def _runtime_files(root):
    root = Path(root)
    found = {}
    for name in ("python.exe", "pythonw.exe", "python3.dll", "LICENSE.txt"):
        p = root / name
        if p.is_file():
            found[name] = p
    for pattern in ("python3*.dll", "vcruntime*.dll", "msvcp*.dll", "python*.zip"):
        for p in root.glob(pattern):
            if p.is_file():
                found[p.name] = p
    excluded = {"site-packages", "__pycache__", "test", "tests", "idlelib", "tkinter",
                "turtledemo", "ensurepip", "venv"}
    for directory in ("Lib", "DLLs"):
        start = root / directory
        if not start.is_dir():
            continue
        for parent, dirs, names in os.walk(start, followlinks=False):
            dirs[:] = [x for x in dirs if x not in excluded]
            for name in names:
                p = Path(parent) / name
                if p.suffix.lower() in (".pyc", ".pyo", ".pdb"):
                    continue
                _no_links(p)
                found[p.relative_to(root).as_posix()] = p
    if not {"python.exe", "pythonw.exe"} <= set(found):
        raise ServiceError("备份组件需要可复制的 Python / Pythonw 运行时。")
    if not any(name.startswith("Lib/encodings/") or name.endswith(".zip") for name in found):
        raise ServiceError("找不到 Python 标准库，未生成不完整安装组件。")
    return found


def install_payload(data_dir, worker_source_files, runtime_dir=None, *, acceptance_once=False):
    """只准备载荷与哈希清单，不显示 UAC、不复制到受保护目录、不创建任务。"""
    _windows()
    data, sid, identifier = _identity(data_dir)
    source_files = [Path(p).absolute() for p in worker_source_files]
    by_name = {p.name: p for p in source_files}
    if len(by_name) != len(source_files) or not set(by_name) <= MODULES:
        raise ServiceError("备份组件源码必须来自明确的模块白名单。")
    required = {"backup_runner.py", "backup_service.py", "backup_vss.py"}
    if not required <= set(by_name):
        raise ServiceError("备份组件缺少运行入口、权限包装或 VSS 模块。")
    files = []
    for name, source in sorted(by_name.items()):
        if not source.is_file():
            raise ServiceError(f"备份组件源码不存在：{source.name}")
        _no_links(source)
        files.append({"source": str(source), "path": "code/" + name,
                      "sha256": _sha(source), "size": source.stat().st_size})
    for relative, source in sorted(_runtime_files(runtime_dir or Path(sys.executable).parent).items()):
        files.append({"source": str(source), "path": "runtime/" + relative,
                      "sha256": _sha(source), "size": source.stat().st_size})
    for item in files:
        _safe_relative(item["path"])
    # 每次安装使用新一代目录；拒绝复用可能仍有旧写句柄的安装目标。
    payload_id = hashlib.sha256((secrets.token_hex(16) + json.dumps(
        [(x["path"], x["sha256"]) for x in files], separators=(",", ":"))).encode()).hexdigest()
    manifest = {"schema": SCHEMA, "owner_sid": sid, "data_dir": str(data),
                "instance": identifier, "payload_id": payload_id, "files": files}
    if acceptance_once:
        manifest["acceptance_once"] = True
    stage = data / "backup_work" / ("install-" + secrets.token_hex(8))
    stage.mkdir(parents=True, exist_ok=False)
    path = stage / "payload.json"
    raw = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    path.write_bytes(raw)
    return {"manifest_path": str(path), "manifest_sha256": hashlib.sha256(raw).hexdigest(),
            "payload_id": payload_id, "files": len(files), "bytes": sum(x["size"] for x in files),
            "protected_dir": str(_program_files() / "PaperPiggyBackup" / identifier)}


prepare_install = install_payload


def request_install(payload):
    """仅由明确点击“授权安装”的界面调用；Windows 自行展示 UAC，不自动接受。"""
    _windows()
    path = Path(payload["manifest_path"]).absolute()
    if _sha(path) != payload["manifest_sha256"]:
        raise ServiceError("安装清单已经改变，请重新准备安装。")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    helper = component_helper()
    if not helper["ready"]:
        raise ServiceError(helper["error"])
    data, sid, identifier = _identity(manifest["data_dir"])
    if sid != manifest["owner_sid"] or identifier != manifest["instance"]:
        raise ServiceError("安装载荷不属于当前用户和数据家。")
    if not re.fullmatch(r"[a-f0-9]{64}", manifest.get("payload_id", "")):
        raise ServiceError("安装代标识无效。")
    command = Path(helper["path"])
    arguments = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/SP-", "/NORESTART",
                 "/DATA=" + str(data), "/SID=" + sid, "/INSTANCE=" + identifier,
                 "/GENERATION=" + manifest["payload_id"]]
    if manifest.get("acceptance_once") is True:
        arguments.append("/ACCEPTANCE=1")

    class INFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    info = INFO()
    info.cbSize, info.fMask, info.lpVerb = ctypes.sizeof(INFO), 0x40 | 0x400, "runas"
    info.lpFile, info.lpParameters, info.nShow = str(command), subprocess.list2cmdline(arguments), 0
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = (ctypes.POINTER(INFO),)
    shell.ShellExecuteExW.restype = wintypes.BOOL
    if not shell.ShellExecuteExW(ctypes.byref(info)):
        code = ctypes.get_last_error()
        if code == 1223:
            raise ServiceError("已取消 Windows 管理员授权；没有启用后台备份。")
        raise ServiceError(f"无法启动 Windows 管理员授权（{code}）。")
    if info.hProcess:
        _INSTALL_PROCESSES[_canonical(data)] = info.hProcess
    pending = Path(manifest["data_dir"]) / "backup_work" / "component-install.json"
    _atomic(pending, {"payload_id": payload["payload_id"], "started_at": time.time()})
    return {"ok": True, "pending": True, "message": "请在 Windows 授权窗口确认；完成后刷新组件状态。"}


def component_helper():
    """已发布的固定 PE 安装器；不在用户机器编译、不从可写清单复制高权代码。"""
    root = Path(__file__).absolute().parent
    candidates = [root, root.parent / "build/backup-component"]
    for directory in candidates:
        executable, metadata = directory / "backup_component.exe", directory / "backup_component.json"
        if not executable.is_file() or not metadata.is_file():
            continue
        try:
            meta = json.loads(metadata.read_text(encoding="utf-8"))
            if meta.get("schema") != 1 or _sha(executable) != meta.get("sha256"):
                raise ServiceError("备份组件安装器校验失败，请重新安装官方应用版本。")
            current = {name: _sha(root / name) for name in MODULES}
            if meta.get("source_hashes") != current:
                raise ServiceError("备份组件安装器与当前源码不匹配，请重新构建或更新完整安装器。")
            return {"ready": True, "path": str(executable), "sha256": meta["sha256"],
                    "build_id": meta["build_id"], "error": ""}
        except Exception as exc:
            return {"ready": False, "error": str(exc)}
    return {"ready": False, "error": "缺少原生备份组件安装器，请安装包含该组件的完整应用版本。"}


def _poll_install(data):
    key = _canonical(data)
    handle = _INSTALL_PROCESSES.get(key)
    if not handle:
        return
    kernel, _ = _libraries()
    kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    code = wintypes.DWORD()
    if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259:
        return
    _INSTALL_PROCESSES.pop(key, None)
    kernel.CloseHandle(handle)
    path = Path(data) / "backup_work/component-install.json"
    if path.is_file():
        pending = json.loads(path.read_text(encoding="utf-8"))
        pending["exit_code"] = code.value
        if code.value:
            pending["error"] = f"备份组件安装未完成（退出码 {code.value}）；已有版本与资料保留。"
        _atomic(path, pending)


def _check_protected_directory(path):
    """低权限用户不得向代码目录新增文件或改写已有模块。"""
    _no_links(path)
    kernel, _ = _libraries()
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel.CreateFileW.restype = wintypes.HANDLE
    directory = Path(path).is_dir()
    with restricted_user():
        # 分别请求权限，避免“可写其中一种但组合请求被拒绝”漏检；尤其检查所有者
        # 隐含的 WRITE_DAC，只有 RX 的 DACL 不足以防用户自行授权写入。
        for access in (0x0002, 0x0004, 0x0010, 0x0100, 0x10000, 0x40000, 0x80000):
            handle = kernel.CreateFileW(str(path), access, 7, None, 3,
                                        0x02000000 if directory else 0, None)
            if handle != ctypes.c_void_p(-1).value:
                kernel.CloseHandle(handle)
                raise ServiceError("备份代码目录可由普通用户改写，拒绝以高权限执行。")
            if ctypes.get_last_error() != 5:
                raise ServiceError("无法确认备份代码目录受到写保护。")


def _verified_manifest(path):
    path = Path(path).absolute()
    raw = path.read_bytes()
    manifest = json.loads(raw.decode("utf-8"))
    if manifest.get("schema") != SCHEMA or manifest.get("owner_sid") != current_sid():
        raise ServiceError("后台组件身份或版本不匹配。")
    if not re.fullmatch(r"[a-f0-9]{64}", manifest.get("payload_id", "")):
        raise ServiceError("后台组件版本标识无效。")
    _, _, instance = _instance(manifest["data_dir"])
    expected = instance / manifest["payload_id"]
    if path.parent != expected or path.name != "payload.json":
        raise ServiceError("后台组件不在受保护的安装位置。")
    for parent in (instance, expected, expected / "code", expected / "runtime"):
        _check_protected_directory(parent)
    seen = set()
    for item in manifest["files"]:
        relative = str(_safe_relative(item["path"]))
        if not relative.startswith(("code/", "runtime/")) or relative in seen:
            raise ServiceError("后台组件清单路径无效或重复。")
        seen.add(relative)
        target = expected / Path(relative)
        _no_links(target)
        if not target.is_file() or _sha(target) != item["sha256"]:
            raise ServiceError(f"后台组件文件校验失败：{relative}")
        _check_protected_directory(target)
    return manifest


def _protected_bytes(path, raw):
    """创建时即设置 Administrators 所有者，避免普通用户拿到可持续写入的句柄。"""
    kernel, adv = _libraries()
    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("descriptor", ctypes.c_void_p),
                    ("inherit", wintypes.BOOL)]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD))
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(SECURITY_ATTRIBUTES), wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.WriteFile.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
    kernel.WriteFile.restype = wintypes.BOOL
    kernel.FlushFileBuffers.argtypes = (wintypes.HANDLE,)
    kernel.FlushFileBuffers.restype = wintypes.BOOL
    descriptor = ctypes.c_void_p()
    sddl = f"O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;0x1200a9;;;{current_sid()})"
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1,
            ctypes.byref(descriptor), None):
        raise ServiceError("无法保护后台组件状态文件。")
    handle = None
    try:
        security = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), descriptor, False)
        handle = kernel.CreateFileW(str(path), 0x40000000, 0, ctypes.byref(security), 1, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            handle = None
            raise ServiceError("无法新建受保护的后台状态文件。")
        buffer = ctypes.create_string_buffer(raw)
        written = wintypes.DWORD()
        if (not kernel.WriteFile(handle, buffer, len(raw), ctypes.byref(written), None)
                or written.value != len(raw) or not kernel.FlushFileBuffers(handle)):
            raise ServiceError("后台状态文件写入未完成。")
    finally:
        if handle:
            kernel.CloseHandle(handle)
        kernel.LocalFree(descriptor)


def _atomic(path, value, protected=False):
    path = Path(path)
    temporary = path.with_name(path.name + "." + secrets.token_hex(5) + ".tmp")
    raw = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    if protected:
        _protected_bytes(temporary, raw)
    else:
        temporary.write_bytes(raw)
    os.replace(temporary, path)


def task_xml(data_dir, payload_dir, owner_sid=None, *, enabled=True, update_channel=False):
    """纯函数：固定受保护入口，用户参数通过 Windows argv 规则引用，绝不经 shell。"""
    data, sid, identifier = _identity(data_dir)
    sid = owner_sid or sid
    if not re.fullmatch(r"S-1-\d+(?:-\d+)+", sid):
        raise ServiceError("计划任务用户 SID 无效。")
    payload = Path(payload_dir)
    namespace = "http://schemas.microsoft.com/windows/2004/02/mit/task"
    ET.register_namespace("", namespace)
    def element(parent, name, text=None, attrs=None):
        node = ET.SubElement(parent, "{" + namespace + "}" + name, attrs or {})
        if text is not None:
            node.text = str(text)
        return node
    task = ET.Element("{" + namespace + "}Task", {"version": "1.2"})
    reg = element(task, "RegistrationInfo")
    element(reg, "Description", "PaperPiggy 独立备份；只运行受保护代码，文件业务使用普通用户权限。")
    if enabled:  # 验收任务仅按需运行，临时启用时也没有 timer/logon 与手动 Run 竞争。
        triggers = element(task, "Triggers")
        daily = element(triggers, "TimeTrigger")
        repetition = element(daily, "Repetition")
        element(repetition, "Interval", "PT1M")
        element(repetition, "StopAtDurationEnd", "false")
        element(daily, "StartBoundary", time.strftime("%Y-%m-%dT00:00:00"))
        element(daily, "Enabled", "true")
        login = element(triggers, "LogonTrigger")
        element(login, "Enabled", "true")
        element(login, "UserId", sid)
    principals = element(task, "Principals")
    principal = element(principals, "Principal", attrs={"id": "Owner"})
    element(principal, "UserId", sid)
    element(principal, "LogonType", "InteractiveToken")
    element(principal, "RunLevel", "HighestAvailable")
    settings = element(task, "Settings")
    for key, value in [("MultipleInstancesPolicy", "IgnoreNew"),
        ("DisallowStartIfOnBatteries", "false"), ("StopIfGoingOnBatteries", "false"),
        ("AllowHardTerminate", "false"), ("StartWhenAvailable", "true"),
        ("RunOnlyIfNetworkAvailable", "false"), ("AllowStartOnDemand", "true"),
        ("Enabled", "true" if enabled else "false"), ("Hidden", "true"), ("ExecutionTimeLimit", "PT12H")]:
        element(settings, key, value)
    actions = element(task, "Actions", attrs={"Context": "Owner"})
    execution = element(actions, "Exec")
    element(execution, "Command", payload / "runtime/pythonw.exe")
    element(execution, "Arguments", subprocess.list2cmdline(["-I", "-S",
        str(payload / "code/backup_service.py"), "--update-worker" if update_channel else "--worker", "--data", str(data)]))
    element(execution, "WorkingDirectory", payload / "code")
    # Task Scheduler 接收 BSTR，审阅副本也以 UTF-16 写入；声明须与二者一致。
    return '<?xml version="1.0" encoding="UTF-16"?>\n' + ET.tostring(
        task, encoding="unicode"), ("PaperPiggyBackupUpdate-" if update_channel else "PaperPiggyBackup-") + identifier


def _queued_acceptance_jobs(data_dir):
    with restricted_user():
        output = set()
        for path in (Path(data_dir) / "backup_jobs/requests").glob("*.json"):
            request = json.loads(path.read_text(encoding="utf-8"))
            job = request.get("job_id", "")
            if re.fullmatch(r"[a-f0-9]{32}", job):
                output.add(job)
        return sorted(output)


def _acceptance_complete(data_dir, generation, job_ids):
    with restricted_user():
        evidence = Path(data_dir) / "backup_work/acceptance-snapshot.json"
        if not evidence.is_file():
            return False
        snapshot = json.loads(evidence.read_text(encoding="utf-8"))
        if snapshot.get("payload_id") != generation or snapshot.get("snapshot_released") is not True:
            return False
        if not job_ids:
            raise ServiceError("隔离验收没有预期任务，未冒报完成。")
        for job in job_ids:
            status = json.loads((Path(data_dir) / "backup_jobs/status" / (job + ".json")).read_text(encoding="utf-8"))
            if status.get("state") not in ("success", "success_with_warning"):
                raise ServiceError("隔离夹具任务未成功：" + job + "：" + str(status.get("error") or status.get("state")))
        return True


def _active_instance_ids(task):
    collection = task.invoke("GetInstances", 0)
    try:
        output = []
        for index in range(1, collection.invoke("Count", flags=2) + 1):
            instance = collection.invoke("Item", index, flags=2)
            try:
                output.append(instance.invoke("InstanceGuid", flags=2).casefold())
            finally:
                instance.close()
        return output
    finally:
        collection.close()


def _wait_acceptance(task, running, data_dir, generation, job_ids, timeout=480):
    # 完成后的 IRunningTask 是历史对象，Refresh 可报错；只用其初始 GUID 关联当前实例。
    instance_id = running.invoke("InstanceGuid", flags=2).casefold()
    deadline = time.monotonic() + timeout
    idle_count = 0
    while True:
        active = _active_instance_ids(task)
        state = task.invoke("State", flags=2)
        if instance_id not in active and state in (1, 3):
            idle_count += 1
            if idle_count >= 2:
                result = task.invoke("LastTaskResult", flags=2)
                if result:
                    raise ServiceError("隔离任务退出失败（0x" + format(result & 0xffffffff, "08X") + "）。")
                if _acceptance_complete(data_dir, generation, job_ids):
                    return {"instance_guid": instance_id, "last_task_result": result,
                            "worker_completed": True, "job_ids": job_ids}
        else:
            idle_count = 0
        if time.monotonic() >= deadline:
            raise ServiceError("隔离任务等待超时；任务将禁用，运行实例不强杀。")
        time.sleep(.25)


def _register(path, *, commit=True):
    if not _elevated():
        raise ServiceError("注册后台备份任务需要 Windows 管理员授权。")
    manifest = _verified_manifest(path)
    target = Path(path).parent
    acceptance = manifest.get("acceptance_once") is True
    xml, name = task_xml(manifest["data_dir"], target, manifest["owner_sid"], enabled=not acceptance)
    xml_path = target / "task.xml"
    xml_path.write_text(xml, encoding="utf-16")
    sddl = f"O:BAG:BAD:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGX;;;{manifest['owner_sid']})"
    # 同一次原生 COM 调用设置所有者与 DACL；不依赖 PowerShell 的语言模式。
    report, task, running = {}, None, None
    with _scheduler() as folder:
        try:
            task = folder.invoke("RegisterTask", name, xml, 22, manifest["owner_sid"], None, 3, sddl)
            if not task:
                raise ServiceError("未建立后台任务。")
            if acceptance:
                report.update(task_name=name, registered=True,
                              security_sddl=task.invoke("GetSecurityDescriptor", 7))
                expected_jobs = _queued_acceptance_jobs(manifest["data_dir"])
                task.invoke("Enabled", True, flags=4)
                running = task.invoke("Run", None)
                report["started"] = True
                report.update(_wait_acceptance(task, running, manifest["data_dir"],
                                               manifest["payload_id"], expected_jobs))
        except Exception as exc:
            if acceptance:
                report["error"] = str(exc)
            raise
        finally:
            if running:
                running.close()
            if acceptance:
                if task:
                    try:
                        task.invoke("Enabled", False, flags=4)
                        report["disabled"] = not task.invoke("Enabled", flags=2)
                    except Exception as exc:
                        report["disable_error"] = str(exc)
                report["finished_at"] = time.time()
                _atomic(target.parent / "acceptance-task.json", report, protected=True)
                with restricted_user():
                    _atomic(Path(manifest["data_dir"]) / "backup_work/acceptance-task.json", report)
            if task:
                task.close()
    if acceptance and (report.get("disable_error") or not report.get("disabled")):
        raise ServiceError("未确认隔离任务已禁用，请查看验收报告。")
    record = {"schema": SCHEMA, "owner_sid": manifest["owner_sid"],
        "data_dir": manifest["data_dir"], "payload_id": manifest["payload_id"],
        "manifest_sha256": _sha(path), "task_name": name, "installed_at": time.time()}
    if commit:
        _atomic(target.parent / "current.json", record, protected=True)
    return record


def _register_native(data_dir, owner_sid, generation, acceptance=False):
    """仅由固定 Inno 载荷拉起的受保护 Python 调用，不接受源码或任意命令参数。"""
    if not _elevated() or owner_sid != current_sid():
        raise ServiceError("原生组件必须由当前用户明确授权安装。")
    if not re.fullmatch(r"[a-f0-9]{64}", generation or ""):
        raise ServiceError("原生组件安装代无效。")
    data, sid, instance = _instance(data_dir)
    target = instance / generation
    here = Path(__file__).absolute()
    if here != target / "code/backup_service.py":
        raise ServiceError("原生注册入口不在预期保护位置。")
    template = target / "payload-template.json"
    for p in (instance.parent, instance, target, target / "code", target / "runtime", template):
        _check_protected_directory(p)
    manifest = json.loads(template.read_text(encoding="utf-8"))
    if type(manifest.get("component_version")) is not int or manifest["component_version"] < 1:
        raise ServiceError("组件载荷缺少有效发布版本。")
    manifest.update(owner_sid=sid, data_dir=str(data), instance=instance.name, payload_id=generation)
    if acceptance:
        manifest["acceptance_once"] = True
    path = target / "payload.json"
    _atomic(path, manifest, protected=True)
    _verified_manifest(path)
    if {row["path"][5:] for row in manifest["files"] if row["path"].startswith("code/")} != MODULES:
        raise ServiceError("组件源码白名单不完整，未更换现有任务。")
    for row in manifest["files"]:
        if row["path"].startswith("code/"):
            compile((target / row["path"]).read_bytes(), row["path"], "exec")
    # 备份任务和独立更新通道都注册成功后才提交 current；失败恢复原任务定义。
    old_xml = None
    name = "PaperPiggyBackup-" + instance.name
    with _scheduler() as folder:
        try:
            previous = folder.invoke("GetTask", name)
            try:
                old_xml = previous.invoke("Xml", flags=2)
            finally:
                previous.close()
        except ServiceError:
            pass
    try:
        record = _register(path, commit=False)
        sys.path.insert(0, str(here.parent))
        import backup_component_update
        backup_component_update.ensure_channel(path, enabled=not acceptance)
        _atomic(instance / "current.json", record, protected=True)
        return record
    except Exception:
        with _scheduler() as folder:
            if old_xml is not None:
                task = folder.invoke("RegisterTask", name, old_xml, 22, sid, None, 3,
                    f"O:BAG:BAD:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGX;;;{sid})")
                if task:
                    task.close()
            else:
                try:
                    task = folder.invoke("GetTask", name)
                    try:
                        task.invoke("Enabled", False, flags=4)
                    finally:
                        task.close()
                except ServiceError:
                    pass
        raise


def install_status(data_dir):
    try:
        data, sid, instance = _instance(data_dir)
        _poll_install(data)
        pending_path = data / "backup_work" / "component-install.json"
        pending = json.loads(pending_path.read_text(encoding="utf-8")) if pending_path.is_file() else {}
        error_path = instance / "install_error.json"
        failure = json.loads(error_path.read_text(encoding="utf-8")) if error_path.is_file() else {}
        record_path = instance / "current.json"
        if not record_path.is_file():
            error = str(pending.get("error") or "")
            native_error = instance / ("native-error-" + str(pending.get("payload_id", "")) + ".txt")
            if re.fullmatch(r"[a-f0-9]{64}", str(pending.get("payload_id", ""))) and native_error.is_file():
                error = native_error.read_text(encoding="mbcs", errors="replace")
            if pending and failure.get("payload_id") == pending.get("payload_id"):
                error = str(failure.get("error") or "后台组件安装未完成。")
            waiting = bool(pending and not error and time.time() - pending.get("started_at", 0) < 600)
            if pending and not error and not waiting:
                error = "后台组件安装未完成，请检查 Windows 授权或载荷校验后重试。"
            return {"installed": False, "pending": waiting, "error": error, "schema": SCHEMA,
                    "can_run": False, "migration_required": False, "auto_update_supported": False,
                    "auto_update": {"state": "idle", "pending": False, "error": ""}}
        _no_links(record_path)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if (record.get("owner_sid") != sid or _canonical(record.get("data_dir", "")) != _canonical(data)
                or not re.fullmatch(r"[a-f0-9]{64}", record.get("payload_id", ""))):
            raise ServiceError("受保护的备份组件关联不匹配。")
        manifest_path = instance / record["payload_id"] / "payload.json"
        if _sha(manifest_path) != record["manifest_sha256"]:
            raise ServiceError("备份组件安装清单发生变化。")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        code_entry = next(x for x in manifest["files"] if x["path"] == "code/backup_service.py")
        _check_protected_directory(manifest_path.parent / "code")
        if _sha(manifest_path.parent / "code/backup_service.py") != code_entry["sha256"]:
            raise ServiceError("受保护的备份入口校验失败。")
        updating = pending and pending.get("payload_id") != record["payload_id"]
        error = str(pending.get("error") or "") if updating else ""
        if updating and failure.get("payload_id") == pending.get("payload_id"):
            error = str(failure.get("error") or error)
        native_error = instance / ("native-error-" + str(pending.get("payload_id", "")) + ".txt")
        if updating and re.fullmatch(r"[a-f0-9]{64}", str(pending.get("payload_id", ""))) and native_error.is_file():
            error = native_error.read_text(encoding="mbcs", errors="replace")
        if updating and not error and time.time() - pending.get("started_at", 0) >= 600:
            error = "后台组件更新未完成；已有版本保留，请重新授权安装。"
        from backup_component_update import channel_status
        channel = channel_status(data)
        changed = _sources_changed(manifest)
        supported = channel["auto_update_supported"]
        return {"installed": True, "pending": bool(updating and not error and time.time() - pending.get("started_at", 0) < 600),
                "error": error, "schema": record["schema"],
                "task_name": record["task_name"], "installed_at": record["installed_at"],
                "needs_update": changed, "can_run": supported or not changed,
                "migration_required": changed and not supported, **channel}
    except Exception as exc:
        return {"installed": False, "pending": False, "error": str(exc), "schema": SCHEMA,
                "can_run": False, "migration_required": False, "auto_update_supported": False,
                "auto_update": {"state": "failed", "pending": False, "error": str(exc)}}


def _sources_changed(manifest):
    """应用升级后比较全部备份模块，不能只看包装入口是否改过。"""
    code = {row["path"][5:]: row["sha256"] for row in manifest.get("files", [])
            if row.get("path", "").startswith("code/")}
    if set(code) != MODULES:
        return True
    root = Path(__file__).absolute().parent
    return any(not (root / name).is_file() or _sha(root / name) != digest
               for name, digest in code.items())


def trigger_task(data_dir):
    info = install_status(data_dir)
    if not info["installed"]:
        raise ServiceError(info.get("error") or "请先授权安装后台备份组件。")
    result = subprocess.run([str(_system_dir() / "schtasks.exe"), "/Run", "/TN", info["task_name"]],
                            capture_output=True, creationflags=NO_WINDOW)
    if result.returncode:
        raise ServiceError("无法唤醒后台备份任务，请检查 Windows 任务计划程序。")
    return {"ok": True, "queued": True, "task_name": info["task_name"]}


def request_component_update(data_dir, envelope_path=None, helper_path=None, *, retry=False):
    """普通权限投递签名包；已安装通道自行验证和更新，此入口绝不显示 UAC。"""
    from backup_component_update import request_update
    return request_update(data_dir, envelope_path, helper_path, retry=retry)


@contextmanager
def component_guard(instance):
    """高权更新和备份共享保护锁；不强制结束正在运行的备份。"""
    path = Path(instance) / "component-operation.lock"
    _no_links(path)
    if not path.exists():
        try:
            _protected_bytes(path, b"")
        except ServiceError:
            if not path.exists():
                raise
    # 锁文件仅能由受保护父目录中的本组件创建。核对父目录而不对正在被另一
    # worker 独占的文件做写权限探针，避免把正常 sharing violation 当 ACL 错误。
    _check_protected_directory(path.parent)
    kernel, _ = _libraries()
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel.CreateFileW.restype = wintypes.HANDLE
    handle = kernel.CreateFileW(str(path), 0xC0000000, 0, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        code = ctypes.get_last_error()
        if code in (32, 33):
            yield False
            return
        raise ServiceError("无法取得后台组件维护锁。")
    try:
        yield True
    finally:
        kernel.CloseHandle(handle)


def _file_token_elevated():
    kernel, adv = _libraries()
    adv.OpenThreadToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL,
                                    ctypes.POINTER(wintypes.HANDLE))
    adv.OpenThreadToken.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    if not adv.OpenThreadToken(kernel.GetCurrentThread(), 8, True, ctypes.byref(token)):
        raise ServiceError("验收未取得普通用户文件令牌，未读取真实来源。")
    try:
        return bool(wintypes.DWORD.from_buffer(_token_info(token, 20)).value)
    finally:
        kernel.CloseHandle(token)


def _acceptance_db_checks(snapshot, scopes, work):
    """固定私有验收：只读快照 DB，经同一个生产校验器验证，不生成真实资料归档。"""
    import backup_archive
    import backup_sources
    import stat
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    report = {"set_id": snapshot.set_id, "volumes": snapshot.snapshots,
              "file_token_elevated": _file_token_elevated(), "checks": [], "tree_checks": []}
    if report["file_token_elevated"]:
        raise ServiceError("验收文件令牌仍为管理员，未读取真实来源。")
    class ReadOnlyFiles:
        def __init__(self, paths):
            self.paths = paths
        def open(self, name):
            return self.paths[name].open("rb")
    for scope in scopes:
        root = Path(snapshot.map_path(scope["path"]))
        tree = {"scope": scope["id"], "category": scope["category"]}
        try:
            original = {"id": scope["category"], "source": scope["path"],
                        "original_source": scope["path"]}
            if scope["category"] == "browser":
                original["exclude"] = backup_sources.browser_excludes(Path(scope["path"]))
            mapped = backup_sources.prepare_snapshot_roots({"include_key": False}, [original], snapshot)
            files, directories = backup_archive._walk_roots(mapped)
            tree.update(ok=True, file_count=len(files), directories_count=len(directories))
        except Exception as exc:
            tree.update(ok=False, error_type=type(exc).__name__, error_summary=str(exc)[:700])
        report["tree_checks"].append(tree)
        before_scope = len(report["checks"])
        try:
            def failed(error):
                raise error
            for folder, dirs, files in os.walk(root, followlinks=False, onerror=failed):
                dirs[:] = [name for name in dirs if not getattr((Path(folder) / name).lstat(),
                                          "st_file_attributes", 0) & 0x400]
                for filename in files:
                    if filename.lower().endswith(("-wal", "-shm", "-journal")):
                        continue
                    source = Path(folder) / filename
                    state = source.lstat()
                    if not stat.S_ISREG(state.st_mode) or getattr(state, "st_file_attributes", 0) & 0x400:
                        continue
                    with source.open("rb") as stream:
                        header = stream.read(16)
                    if header != b"SQLite format 3\0":
                        if (filename.lower().endswith(".sqlite") and state.st_size
                                or Path(str(source) + "-wal").is_file()):
                            report["checks"].append({"scope": scope["id"], "category": scope["category"],
                                "relative_path": source.relative_to(root).as_posix(), "ok": False,
                                "error_summary": "疑似 SQLite 来源的文件头无效，未忽略该数据库。"})
                        continue
                    relative = source.relative_to(root).as_posix()
                    name = "roots/" + scope["category"] + "/" + relative
                    paths = {name: source}
                    for suffix in ("-wal", "-shm", "-journal"):
                        sidecar = Path(str(source) + suffix)
                        if sidecar.is_file():
                            sidecar_state = sidecar.lstat()
                            if getattr(sidecar_state, "st_file_attributes", 0) & 0x400:
                                raise ServiceError("数据库旁文件含链接，未读取。")
                            paths[name + suffix] = sidecar
                    row = {"scope": scope["id"], "category": scope["category"],
                           "relative_path": relative}
                    try:
                        entries = [{"path": n, "size": p.stat().st_size} for n, p in paths.items()]
                        checks = backup_archive._sqlite_check(ReadOnlyFiles(paths), entries, work_dir=work)
                        row.update(ok=True, checks=checks)
                    except Exception as exc:
                        message = str(exc).lower()
                        row.update(ok=False, error_type=type(exc).__name__,
                            error_summary=("缺少原程序排序规则；未判定损坏或冒报完整校验成功。"
                                if "collation" in message or "排序规则" in message else
                                "独立快照副本未通过生产数据库校验；没有修改来源。"))
                    report["checks"].append(row)
        except Exception as exc:
            report["checks"].append({"scope": scope["id"], "category": scope["category"],
                "ok": False, "error_type": type(exc).__name__, "error_summary": "来源无法完整读取，未冒报成功。"})
        covered = sum("relative_path" in row for row in report["checks"][before_scope:])
        if covered < scope.get("minimum_databases", 1):
            report["checks"].append({"scope": scope["id"], "category": scope["category"],
                "ok": False, "error_summary": "数据库覆盖数量不足，未冒报完整核查。"})
    report["database_count"] = sum("relative_path" in row for row in report["checks"])
    report["passed"] = sum(bool(row.get("ok")) for row in report["checks"])
    report["failed"] = len(report["checks"]) - report["passed"]
    report["tree_failed_count"] = sum(not row["ok"] for row in report["tree_checks"])
    return report


def _acceptance_main(data_dir, runner, arguments, generation):
    """只在固定安装器显式 acceptance_once 时使用，不加载用户代码或任意模块。"""
    data = Path(data_dir)
    current = runner.store(data)
    import backup_sources
    import backup_vss
    options_path = data / "backup_work/acceptance-options.json"
    options = json.loads(options_path.read_text(encoding="utf-8")) if options_path.exists() else {}
    scopes = options.get("sqlite_scopes", [])
    if not isinstance(scopes, list) or len(scopes) > 3:
        raise ServiceError("真实数据库验收来源格式无效。")
    for scope in scopes:
        if (not isinstance(scope, dict) or scope.get("category") not in
                ("zotero_data", "zotero_profile", "browser")
                or not re.fullmatch(r"[a-z_]{1,32}", str(scope.get("id", "")))
                or not Path(scope.get("path", "")).is_absolute()
                or type(scope.get("minimum_databases", 1)) is not int
                or not 1 <= scope.get("minimum_databases", 1) <= 10000):
            raise ServiceError("真实数据库验收只接受本机目录类别与路径。")
        _no_links(scope["path"])
    roots = []
    for job in current.list():
        if current.status(job["job_id"]).get("state") == "queued":
            selected, _ = backup_sources.validate_sources(job, data)
            roots.extend(root["source"] for root in selected)
    if not roots:
        raise ServiceError("隔离验收没有已排队的夹具任务。")
    original = backup_vss.snapshot
    with original([*roots, *(scope["path"] for scope in scopes)]) as shared:
        report = _acceptance_db_checks(shared, scopes, data / "backup_work/acceptance-db-work")
        _atomic(data / "backup_work/acceptance-db-report.json", report)
        @contextmanager
        def borrowed(requested, **kwargs):
            for path in requested:
                shared.map_path(path)  # 未声明的来源不能借用同卷快照。
            yield shared
        backup_vss.snapshot = borrowed
        try:
            result = runner.main(arguments)
        finally:
            backup_vss.snapshot = original
    _atomic(data / "backup_work/acceptance-snapshot.json",
            {"set_id": shared.set_id, "volumes": shared.snapshots, "snapshot_released": True,
             "payload_id": generation})
    return result


def _worker(data_dir):
    if not _elevated():
        raise ServiceError("后台备份包装必须由已授权的计划任务启动。")
    _, _, instance = _instance(data_dir)
    with component_guard(instance) as acquired:
        return _worker_unlocked(data_dir) if acquired else 0


def _worker_unlocked(data_dir):
    if not _elevated():
        raise ServiceError("后台备份包装必须由已授权的计划任务启动。")
    _, _, instance = _instance(data_dir)
    here = Path(__file__).absolute()
    manifest = _verified_manifest(here.parent.parent / "payload.json")
    if _canonical(data_dir) != _canonical(manifest["data_dir"]):
        raise ServiceError("任务的数据家与受保护安装记录不符。")
    session = _logon_sid()
    session_file = instance / "login_sessions.json"
    sessions = json.loads(session_file.read_text(encoding="utf-8")) if session_file.exists() else []
    login = session not in sessions
    # -I -S 不接受 cwd、PYTHONPATH 或 .pth；只添加经过受保护清单核验的代码目录。
    sys.path.insert(0, str(here.parent))
    with restricted_user():
        os.environ["LOCALKB_DATA"] = str(data_dir)
        import backup_runner
        arguments = ["--login" if login else "--tick", "--login-id", session, "--data", str(data_dir)]
        result = (_acceptance_main(data_dir, backup_runner, arguments, manifest["payload_id"]) if manifest.get("acceptance_once") is True
                  else backup_runner.main(arguments))
    if login and not result:
        _atomic(session_file, (sessions + [session])[-32:], protected=True)
    return result


def _update_worker(data_dir):
    if not _elevated():
        raise ServiceError("组件更新只由已授权的独立任务执行。")
    here = Path(__file__).absolute()
    manifest = _verified_manifest(here.parent.parent / "payload.json")
    if _canonical(data_dir) != _canonical(manifest["data_dir"]):
        raise ServiceError("更新任务的数据家与安装记录不符。")
    sys.path.insert(0, str(here.parent))
    import backup_component_update
    return backup_component_update.run_update(data_dir, manifest)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--register-native", action="store_true")
    parser.add_argument("--owner-sid")
    parser.add_argument("--generation")
    parser.add_argument("--acceptance-once", action="store_true")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--update-worker", action="store_true")
    parser.add_argument("--manifest")
    parser.add_argument("--data")
    args = parser.parse_args(argv)
    try:
        if args.update_worker and args.data and not args.register and not args.register_native and not args.worker:
            return int(_update_worker(args.data) or 0)
        if args.register_native and args.data and args.owner_sid and args.generation and not args.worker and not args.register:
            _register_native(args.data, args.owner_sid, args.generation, args.acceptance_once)
            return 0
        if args.register and args.manifest and not args.worker:
            _register(args.manifest)
            return 0
        if args.worker and args.data and not args.register:
            result = _worker(args.data)
            return int(result or 0)
        raise ServiceError("备份组件启动参数不完整。")
    except Exception as exc:
        if args.register or args.register_native or args.worker:
            try:
                instance = Path(__file__).absolute().parents[2]
                base = _program_files() / "PaperPiggyBackup"
                if base in instance.parents:
                    _check_protected_directory(instance)
                    _atomic(instance / ("install_error.json" if args.register or args.register_native else "worker_error.json"),
                            {"payload_id": Path(__file__).absolute().parents[1].name,
                            "error": str(exc), "at": time.time()}, protected=True)
            except Exception:
                pass
        # pythonw 可能没有 stderr；不打印用户配置内容或凭据。
        if sys.stderr:
            print("[PaperPiggy backup] " + str(exc), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

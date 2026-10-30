# -*- coding: utf-8 -*-
"""VSS 同批多卷只读快照。只调用原生 API，不复制活动数据库或操作其他快照。

ABI 来源：Microsoft win32metadata 的 WinSDK vsbackup.h / vss.h。
NO_WRITERS 上下文明确提供 crash-consistent 落盘状态，调用方仍须验证数据库副本。
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import ntpath
import os
from pathlib import Path
import sys
import time
import uuid


class VssError(RuntimeError):
    pass


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    def __str__(self):
        return str(uuid.UUID(bytes_le=bytes(self)))


class SNAPSHOT_PROP(ctypes.Structure):
    _fields_ = [("snapshot_id", GUID), ("set_id", GUID), ("count", ctypes.c_int32),
                ("device", ctypes.c_wchar_p), ("volume", ctypes.c_wchar_p),
                ("origin", ctypes.c_wchar_p), ("service", ctypes.c_wchar_p),
                ("exposed_name", ctypes.c_wchar_p), ("exposed_path", ctypes.c_wchar_p),
                ("provider", GUID), ("attributes", ctypes.c_int32),
                ("created", ctypes.c_int64), ("status", ctypes.c_int32)]


HRESULT = ctypes.c_int32
_METHODS = {"InitializeForBackup": 5, "SetBackupState": 6, "AbortBackup": 15,
            "SetContext": 35, "StartSnapshotSet": 36, "AddToSnapshotSet": 37,
            "DoSnapshotSet": 38, "DeleteSnapshots": 39, "GetSnapshotProperties": 42}


def _check(hr, action):
    value = int(hr) & 0xFFFFFFFF
    if value & 0x80000000:
        if value == 0x80070005:
            raise VssError(f"{action}失败：需要已授权的管理员备份组件（0x{value:08X}）；未改为复制活动文件。")
        raise VssError(f"{action}失败（0x{value:08X}）；未改为复制活动文件。")
    return value


def _call(pointer, index, argtypes=(), *args):
    if not pointer:
        raise VssError("VSS 接口未初始化。")
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    function = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, *argtypes)(table[index])
    return function(pointer, *args)


def _release(pointer):
    if pointer:
        _call(pointer, 2)


@contextmanager
def _native_privileges():
    """仅原生 VSS 调用临时退出线程模拟；文件处理仍留在调用方的普通令牌下。"""
    if sys.platform != "win32":
        raise VssError("运行中一致备份目前需要 Windows VSS。")
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    adv.OpenThreadToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL,
                                    ctypes.POINTER(wintypes.HANDLE))
    adv.OpenThreadToken.restype = wintypes.BOOL
    adv.SetThreadToken.argtypes = (ctypes.POINTER(wintypes.HANDLE), wintypes.HANDLE)
    adv.SetThreadToken.restype = wintypes.BOOL
    adv.RevertToSelf.restype = wintypes.BOOL
    kernel.GetCurrentThread.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    token = wintypes.HANDLE()
    had_token = bool(adv.OpenThreadToken(kernel.GetCurrentThread(), 0x0004 | 0x0008,
                                         True, ctypes.byref(token)))
    if not had_token and ctypes.get_last_error() != 1008:  # ERROR_NO_TOKEN
        raise VssError("无法核对备份组件的线程权限，未创建快照。")
    if had_token and not adv.RevertToSelf():
        kernel.CloseHandle(token)
        raise VssError("无法启用已授权的 VSS 权限。")
    try:
        yield
    finally:
        if had_token:
            restored = adv.SetThreadToken(None, token)
            kernel.CloseHandle(token)
            if not restored:
                # 不能让后续文件处理意外以管理员令牌继续。
                os._exit(74)


def _volume(path):
    text = os.path.abspath(os.fspath(path))
    if text.startswith(("\\\\", "\\\\?\\")):
        raise VssError("备份源必须是本机卷上的普通目录，不能直接使用网络或设备路径。")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetVolumePathNameW.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
    kernel.GetVolumePathNameW.restype = wintypes.BOOL
    kernel.GetVolumeNameForVolumeMountPointW.argtypes = (
        wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
    kernel.GetVolumeNameForVolumeMountPointW.restype = wintypes.BOOL
    mount = ctypes.create_unicode_buffer(32768)
    if not kernel.GetVolumePathNameW(text, mount, len(mount)):
        raise VssError(f"无法识别备份来源所在卷：{text}")
    name = ctypes.create_unicode_buffer(128)
    if not kernel.GetVolumeNameForVolumeMountPointW(mount.value, name, len(name)):
        raise VssError(f"无法识别备份来源卷标识：{text}")
    return mount.value, name.value


class _NativeVss:
    def __init__(self, timeout=180, cancel=None):
        self.timeout, self.cancel = timeout, cancel
        self.pointer = ctypes.c_void_p()
        self.set_guid = GUID()
        self.ids = []
        self.com_initialized = False
        self.api = None

    def _method(self, name, types=(), *args):
        return _check(_call(self.pointer, _METHODS[name], types, *args), name)

    def _await(self, pointer):
        deadline = time.monotonic() + self.timeout
        try:
            while True:
                if self.cancel and self.cancel():
                    _call(pointer, 3)
                    raise VssError("已取消创建备份快照。")
                _check(_call(pointer, 4, (wintypes.DWORD,), 1000), "等待 VSS")
                result, reserved = HRESULT(), ctypes.c_int32()
                _check(_call(pointer, 5, (ctypes.POINTER(HRESULT), ctypes.POINTER(ctypes.c_int32)),
                             ctypes.byref(result), ctypes.byref(reserved)), "查询 VSS 状态")
                value = _check(result.value, "创建同批快照")
                if value in (0, 0x0004230A):
                    return
                if value != 0x00042309:
                    raise VssError(f"VSS 返回未完成状态 0x{value:08X}。")
                if time.monotonic() >= deadline:
                    _call(pointer, 3)
                    raise VssError("创建 VSS 快照超时，未生成成功备份。")
        finally:
            _release(pointer)

    def start(self, volumes):
        with _native_privileges():
            shell = ctypes.WinDLL("shell32")
            shell.IsUserAnAdmin.restype = wintypes.BOOL
            if not shell.IsUserAnAdmin():
                raise VssError("运行中备份需要先授权安装管理员备份组件；未复制活动数据库。")
            ole = ctypes.OleDLL("ole32")
            ole.CoInitializeEx.argtypes = (ctypes.c_void_p, wintypes.DWORD)
            ole.CoInitializeEx.restype = HRESULT
            _check(ole.CoInitializeEx(None, 0), "初始化 VSS COM")
            self.com_initialized = True
            ole.CoInitializeSecurity.argtypes = (ctypes.c_void_p, ctypes.c_int32,
                ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p)
            ole.CoInitializeSecurity.restype = HRESULT
            # 本机卷遵循微软本机 requester 建议：Privacy / Identify / EOAC_NONE。
            # 原生调用已临时恢复进程令牌，COM 无需沿用普通文件业务的模拟令牌。
            result = ole.CoInitializeSecurity(None, -1, None, None, 6, 2, None, 0, None)
            if (int(result) & 0xFFFFFFFF) != 0x80010119:  # RPC_E_TOO_LATE
                _check(result, "初始化 VSS 安全上下文")
            self.api = ctypes.WinDLL("vssapi")
            self.api.CreateVssBackupComponentsInternal.argtypes = (ctypes.POINTER(ctypes.c_void_p),)
            self.api.CreateVssBackupComponentsInternal.restype = HRESULT
            self.api.VssFreeSnapshotPropertiesInternal.argtypes = (ctypes.POINTER(SNAPSHOT_PROP),)
            _check(self.api.CreateVssBackupComponentsInternal(ctypes.byref(self.pointer)), "取得 VSS 接口")
            self._method("InitializeForBackup", (ctypes.c_wchar_p,), None)
            self._method("SetContext", (ctypes.c_int32,), 0x10)  # FILE_SHARE_BACKUP, auto-release
            self._method("SetBackupState", (ctypes.c_bool, ctypes.c_bool, ctypes.c_int32, ctypes.c_bool),
                         False, False, 5, False)  # COPY：不影响其他备份的归档记录
            self._method("StartSnapshotSet", (ctypes.POINTER(GUID),), ctypes.byref(self.set_guid))
            for volume in volumes:
                identifier = GUID()
                self._method("AddToSnapshotSet", (ctypes.c_wchar_p, GUID, ctypes.POINTER(GUID)),
                             volume, GUID(), ctypes.byref(identifier))
                self.ids.append(identifier)
            asynchronous = ctypes.c_void_p()
            self._method("DoSnapshotSet", (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(asynchronous))
            self._await(asynchronous)
            snapshots = []
            for identifier in self.ids:
                prop = SNAPSHOT_PROP()
                self._method("GetSnapshotProperties", (GUID, ctypes.POINTER(SNAPSHOT_PROP)),
                             identifier, ctypes.byref(prop))
                try:
                    if str(prop.set_id) != str(self.set_guid) or str(prop.snapshot_id) != str(identifier):
                        raise VssError("VSS 返回的快照不属于本次快照集，未连接其他快照。")
                    if not prop.device or not prop.volume:
                        raise VssError("VSS 快照缺少可读取的卷路径。")
                    snapshots.append({"snapshot_id": str(identifier), "set_id": str(prop.set_id),
                                      "volume": prop.volume, "device": prop.device,
                                      "created": int(prop.created)})
                finally:
                    self.api.VssFreeSnapshotPropertiesInternal(ctypes.byref(prop))
            return str(self.set_guid), snapshots

    def close(self):
        failures = []
        try:
            with _native_privileges():
                if self.pointer and self.ids:
                    try:
                        deleted, failed = ctypes.c_int32(), GUID()
                        self._method("DeleteSnapshots", (GUID, ctypes.c_int32, wintypes.BOOL,
                            ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(GUID)),
                            self.set_guid, 2, False, ctypes.byref(deleted), ctypes.byref(failed))
                    except VssError as exc:
                        failures.append(str(exc))
                if self.pointer:
                    _release(self.pointer)
                    self.pointer = ctypes.c_void_p()
        finally:
            if self.com_initialized:
                ctypes.OleDLL("ole32").CoUninitialize()
                self.com_initialized = False
        if failures:
            raise VssError("备份快照释放未完成：" + "; ".join(failures))


class SnapshotSet:
    def __init__(self, roots, timeout=180, cancel=None, _backend_factory=None):
        self.roots = [os.path.abspath(os.fspath(root)) for root in roots]
        if not self.roots:
            raise VssError("没有可创建快照的备份来源。")
        self.set_id, self.snapshots, self.mounts = "", [], []
        self.backend = (_backend_factory or _NativeVss)(timeout=timeout, cancel=cancel)
        self.cleanup_error = None

    def __enter__(self):
        if sys.platform != "win32":
            raise VssError("运行中一致备份目前需要 Windows VSS。")
        volumes = {}
        for root in self.roots:
            mount, volume = _volume(root)
            volumes.setdefault(volume.casefold(), volume)
            self.mounts.append((mount, volume))
        try:
            self.set_id, self.snapshots = self.backend.start(list(volumes.values()))
            actual = {row["volume"].casefold() for row in self.snapshots}
            if actual != set(volumes) or any(row["set_id"] != self.set_id for row in self.snapshots):
                raise VssError("来源卷未全部进入同一个 VSS 快照集，未继续备份。")
            return self
        except BaseException:
            try:
                self.backend.close()
            except Exception as exc:
                self.cleanup_error = str(exc)
            raise

    def map_path(self, path):
        value = os.path.abspath(os.fspath(path))
        # 映射限于用户声明的来源，不能借一个卷快照任意读取其他目录。
        if not any(_under(value, root) for root in self.roots):
            raise VssError("请求路径不属于本次备份来源。")
        candidates = [(mount, vol) for mount, vol in self.mounts if _under(value, mount)]
        if not candidates:
            raise VssError("来源路径没有对应的卷快照。")
        mount, volume = max(candidates, key=lambda item: len(item[0]))
        row = next(item for item in self.snapshots if item["volume"].casefold() == volume.casefold())
        relative = ntpath.relpath(value, mount)
        return Path(row["device"].rstrip("\\") + ("\\" + relative if relative != "." else "\\"))

    def __exit__(self, exc_type, exc, tb):
        try:
            self.backend.close()
        except Exception as cleanup:
            self.cleanup_error = str(cleanup)
            if exc is None:
                raise
            if hasattr(exc, "add_note"):
                exc.add_note(self.cleanup_error)


def _under(path, parent):
    path, parent = ntpath.normcase(ntpath.normpath(path)), ntpath.normcase(ntpath.normpath(parent))
    try:
        return ntpath.commonpath((path, parent)) == parent
    except ValueError:
        return False


def snapshot(roots, timeout=180, cancel=None):
    return SnapshotSet(roots, timeout=timeout, cancel=cancel)

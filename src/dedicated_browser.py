# -*- coding: utf-8 -*-
"""专用外部浏览器：配置为唯一连接依据，核对本机进程后才复用或保存。

不会终止浏览器、清理资料、迁移密码或在冲突时更换已保存的端口。
浏览器登录资料属于浏览器自身；此模块不读取其 Cookie 或密码文件。
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

import config as C

CONFIG_FILE = C.DATA / "dedicated_browser.json"
PROFILE_DIR = C.DATA / "browser_profile"
LOCK_FILE = C.DATA / "dedicated_browser.lock"
_LOCK = threading.RLock()
_HTTP = build_opener(ProxyHandler({}))


class BrowserError(RuntimeError):
    """可直接向用户显示的浏览器启动或归属错误。"""


def _windows_only():
    if sys.platform != "win32":
        raise BrowserError("打开专用浏览器目前支持 Windows；其他系统请手动使用浏览器。")


def _normal_path(value):
    return os.path.normcase(os.path.realpath(os.path.abspath(str(value))))


def _configuration(raw):
    if not isinstance(raw, dict):
        raise BrowserError("专用浏览器配置格式不正确，请保留原文件并联系支持。")
    executable = str(raw.get("executable") or "")
    profile = str(raw.get("user_data_dir") or "")
    port = raw.get("port")
    if (not Path(executable).is_absolute() or not Path(profile).is_absolute()
            or isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535):
        raise BrowserError("专用浏览器配置缺少有效的程序、资料目录或调试端口；未更换原关联。")
    if Path(executable).name.lower() not in ("chrome.exe", "msedge.exe"):
        raise BrowserError("关联的程序必须是 Chrome 或 Edge；未启动其他程序。")
    # 留下未来版本或用户添加的元数据；实际连接地址始终由校验后的本机端口派生。
    out = {**raw, "schema": 1, "executable": executable, "user_data_dir": profile, "port": port}
    out.pop("endpoint", None)
    if raw.get("source") in ("created", "adopted"):
        out["source"] = raw["source"]
    return out


def _load():
    if not CONFIG_FILE.exists():
        return None
    try:
        return _configuration(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except BrowserError:
        raise
    except (OSError, ValueError) as exc:
        raise BrowserError(f"无法读取专用浏览器关联，已保留原配置：{exc}") from exc


def _save(cfg):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = CONFIG_FILE.with_name(CONFIG_FILE.name + "." + secrets.token_hex(6) + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as stream:
            json.dump(_configuration(cfg), stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, CONFIG_FILE)
    finally:
        if temp.exists():
            temp.unlink()


@contextmanager
def _exclusive():
    """锁文件不删除，避免另一进程持有旧文件句柄时发生并发启动。"""
    _windows_only()
    import msvcrt
    with _LOCK:
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOCK_FILE.open("a+b") as stream:
            if stream.seek(0, 2) == 0:
                stream.write(b"\0")
                stream.flush()
            deadline = time.monotonic() + 25
            while True:
                try:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise BrowserError("另一个专用浏览器打开操作尚未结束，请稍后再试。")
                    time.sleep(0.1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def _winlibs():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    return kernel


def _process(pid):
    """用有限查询权限读取实际程序与命令行，不依赖 WMI 或外部命令。"""
    kernel = _winlibs()
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        image = ctypes.create_unicode_buffer(size.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
            return None
        ntdll = ctypes.WinDLL("ntdll")
        query = ntdll.NtQueryInformationProcess
        query.argtypes = (wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p,
                          wintypes.ULONG, ctypes.POINTER(wintypes.ULONG))
        query.restype = ctypes.c_long
        needed = wintypes.ULONG()
        query(handle, 60, None, 0, ctypes.byref(needed))
        if not needed.value or needed.value > 1048576:
            return None
        buf = ctypes.create_string_buffer(needed.value)
        if query(handle, 60, buf, len(buf), ctypes.byref(needed)) < 0:
            return None

        class UNICODE_STRING(ctypes.Structure):
            _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT),
                        ("Buffer", ctypes.c_void_p)]

        value = UNICODE_STRING.from_buffer(buf)
        # 系统把 UNICODE_STRING.Buffer 指向调用者提供的缓冲区，先核验范围。
        start = ctypes.addressof(buf)
        if not value.Buffer or not start <= value.Buffer <= start + len(buf) - value.Length:
            return None
        command = ctypes.wstring_at(value.Buffer, value.Length // 2)
        shell = ctypes.WinDLL("shell32")
        shell.CommandLineToArgvW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int))
        shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
        count = ctypes.c_int()
        argv = shell.CommandLineToArgvW(command, ctypes.byref(count))
        if not argv:
            return None
        try:
            args = [argv[i] for i in range(count.value)]
        finally:
            kernel.LocalFree.argtypes = (ctypes.c_void_p,)
            kernel.LocalFree(argv)
        return {"pid": int(pid), "executable": image.value, "args": args}
    finally:
        kernel.CloseHandle(handle)


def _processes():
    kernel = _winlibs()

    class ENTRY(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    kernel.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(ENTRY))
    kernel.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(ENTRY))
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise BrowserError("无法核对本机浏览器进程，未尝试重复启动。")
    try:
        item = ENTRY()
        item.dwSize = ctypes.sizeof(item)
        ok = kernel.Process32FirstW(snapshot, ctypes.byref(item))
        rows = []
        while ok:
            if item.szExeFile.lower() in ("chrome.exe", "msedge.exe"):
                process = _process(item.th32ProcessID)
                if process:
                    rows.append(process)
                else:
                    rows.append({"pid": int(item.th32ProcessID), "name": item.szExeFile,
                                 "args": [], "unreadable": True})
            ok = kernel.Process32NextW(snapshot, ctypes.byref(item))
        return rows
    finally:
        kernel.CloseHandle(snapshot)


def _flag(args, name):
    for index, value in enumerate(args):
        if value.startswith(name + "="):
            return value[len(name) + 1:]
        if value == name and index + 1 < len(args):
            return args[index + 1]
    return None


def _listener_pids(port):
    library = ctypes.WinDLL("iphlpapi")
    function = library.GetExtendedTcpTable
    function.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.ULONG), wintypes.BOOL,
                         wintypes.ULONG, wintypes.ULONG, wintypes.ULONG)
    function.restype = wintypes.DWORD
    size = wintypes.ULONG()
    code = function(None, ctypes.byref(size), False, socket.AF_INET, 5, 0)
    if code not in (0, 122):
        raise BrowserError("无法核对本机调试端口归属，未连接或启动浏览器。")
    buffer = ctypes.create_string_buffer(size.value)
    if function(buffer, ctypes.byref(size), False, socket.AF_INET, 5, 0):
        raise BrowserError("无法核对本机调试端口归属，未连接或启动浏览器。")
    count = struct.unpack_from("=I", buffer, 0)[0]
    owners = set()
    for index in range(count):
        state, address, local_port, _, _, pid = struct.unpack_from("=6I", buffer, 4 + index * 24)
        # 只关心 IPv4 localhost 可到达的监听者；公网专用绑定不阻止 localhost 启动。
        if state == 2 and socket.ntohs(local_port & 0xFFFF) == port:
            ip = socket.inet_ntoa(struct.pack("=I", address))
            if ip in ("127.0.0.1", "0.0.0.0"):
                owners.add(pid)
    return owners


def _profile_processes(cfg, rows=None):
    wanted = _normal_path(cfg["user_data_dir"])
    rows = _processes() if rows is None else rows
    if any(row.get("unreadable") and row.get("name", "").lower()
           == Path(cfg["executable"]).name.lower() for row in rows):
        raise BrowserError("有浏览器进程的资料目录无法读取，不能确认原资料是否已在使用。请检查浏览器权限后重试；未重复启动。")
    return [row for row in rows
            if (profile := _flag(row["args"], "--user-data-dir"))
            and _normal_path(profile) == wanted
            and not _flag(row["args"], "--type")]


def _matches(row, cfg):
    return bool(row and _normal_path(row["executable"]) == _normal_path(cfg["executable"])
                and _flag(row["args"], "--remote-debugging-port") == str(cfg["port"])
                and (profile := _flag(row["args"], "--user-data-dir"))
                and _normal_path(profile) == _normal_path(cfg["user_data_dir"])
                and not any(arg.startswith("--headless") for arg in row["args"]))


def _version(cfg):
    endpoint = f"http://127.0.0.1:{cfg['port']}"
    with _HTTP.open(endpoint + "/json/version", timeout=2) as response:
        result = json.loads(response.read(131072))
    ws = urlsplit(str(result.get("webSocketDebuggerUrl") or ""))
    if (ws.scheme != "ws" or ws.hostname not in ("localhost", "127.0.0.1")
            or ws.port != cfg["port"] or not ws.path.startswith("/devtools/browser/")):
        raise BrowserError("调试端口未返回预期的本机浏览器地址，未连接。")
    return result


def _verify(cfg):
    owners = _listener_pids(cfg["port"])
    if not owners:
        return None
    if len(owners) != 1:
        raise BrowserError(f"端口 {cfg['port']} 的归属不明确，未连接或更换端口。")
    pid = next(iter(owners))
    if not _matches(_process(pid), cfg):
        raise BrowserError(f"已保存的调试端口 {cfg['port']} 被其他程序或其他资料目录占用。请先自行处理占用；应用不会关闭程序或更换端口。")
    try:
        _version(cfg)
    except BrowserError:
        raise
    except Exception as exc:
        raise BrowserError(f"端口 {cfg['port']} 属于已关联浏览器，但调试服务尚不可用；未重复启动。") from exc
    # 避免 HTTP 响应期间监听者退出并被其他进程接替。
    if _listener_pids(cfg["port"]) != {pid} or not _matches(_process(pid), cfg):
        raise BrowserError("核对期间浏览器进程发生变化，请重试；未接管其他程序。")
    return pid


def _result(cfg, pid=None, **extra):
    return {**(cfg or {}), "configured": bool(cfg), "config_file": str(CONFIG_FILE),
            "endpoint": f"http://127.0.0.1:{cfg['port']}" if cfg else "",
            "running": bool(pid), "pid": pid, **extra}


def discovery():
    """只读发现连接信息；冲突时不返回可供工具误连的 endpoint。"""
    cfg = _load()
    if not cfg:
        return _result(None)
    try:
        _windows_only()
        _validate_profile(cfg, required=True)
        pid = _verify(cfg)
        return _result(cfg, pid)
    except BrowserError as exc:
        return _result(cfg, error=str(exc), endpoint="")


def _find_browser():
    paths = {"chrome.exe": [], "msedge.exe": []}
    try:
        import winreg
        for name in paths:
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for flags in (winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
                              winreg.KEY_READ | winreg.KEY_WOW64_32KEY):
                    try:
                        key = "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\" + name
                        with winreg.OpenKey(hive, key, 0, flags) as handle:
                            paths[name].append(winreg.QueryValueEx(handle, None)[0])
                    except OSError:
                        pass
    except ImportError:
        pass
    for name, relative in (("chrome.exe", "Google/Chrome/Application/chrome.exe"),
                           ("msedge.exe", "Microsoft/Edge/Application/msedge.exe")):
        for env in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
            if root := os.environ.get(env):
                paths[name].append(str(Path(root) / relative))
        if path := shutil.which(name):
            paths[name].append(path)
    for name in ("chrome.exe", "msedge.exe"):
        for value in paths[name]:
            candidate = Path(str(value).strip('"'))
            if candidate.is_file() and candidate.name.lower() == name:
                return str(candidate.resolve())
    raise BrowserError("未找到 Chrome 或 Edge。请先自行安装其中一个浏览器，再点击打开。")


def _new_configuration():
    executable = _find_browser()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return _configuration({"executable": executable, "user_data_dir": str(PROFILE_DIR.resolve()),
                           "port": port, "source": "created"})


def _window_handles(pid):
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user.IsWindowVisible.argtypes = (wintypes.HWND,)
    user.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    windows = []

    @callback_type
    def collect(hwnd, _):
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        name = ctypes.create_unicode_buffer(256)
        user.GetClassNameW(hwnd, name, len(name))
        if owner.value == pid and name.value.startswith("Chrome_WidgetWin_") and user.IsWindowVisible(hwnd):
            windows.append(hwnd)
        return True

    user.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
    user.EnumWindows(collect, 0)
    return windows


def _read_exact(stream, length):
    output = bytearray()
    while len(output) < length:
        data = stream.recv(length - len(output))
        if not data:
            raise BrowserError("浏览器调试连接已断开。")
        output.extend(data)
    return bytes(output)


def _cdp(cfg, method, params=None):
    """最小标准库 WebSocket 客户端，仅对已核验的本机 browser endpoint 发命令。"""
    address = urlsplit(_version(cfg)["webSocketDebuggerUrl"])
    key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    with socket.create_connection(("127.0.0.1", cfg["port"]), timeout=4) as connection:
        request = (f"GET {address.path} HTTP/1.1\r\nHost: 127.0.0.1:{cfg['port']}\r\n"
                   f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                   "Sec-WebSocket-Version: 13\r\n\r\n")
        connection.sendall(request.encode("ascii"))
        headers = bytearray()
        while not headers.endswith(b"\r\n\r\n"):
            if len(headers) > 16384:
                raise BrowserError("浏览器调试握手响应过长。")
            headers.extend(_read_exact(connection, 1))
        expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        fields = dict(line.split(b":", 1) for line in bytes(headers).split(b"\r\n")[1:] if b":" in line)
        accepted = next((value.strip() for name, value in fields.items()
                         if name.lower() == b"sec-websocket-accept"), None)
        if not headers.startswith(b"HTTP/1.1 101 ") or accepted != expected:
            raise BrowserError("浏览器未接受调试连接。")
        payload = json.dumps({"id": 1, "method": method, "params": params or {}}).encode("utf-8")
        mask = secrets.token_bytes(4)
        length = len(payload)
        frame = bytes([0x81, 0x80 | length]) if length < 126 else bytes([0x81, 0xFE]) + struct.pack("!H", length)
        connection.sendall(frame + mask + bytes(value ^ mask[i % 4] for i, value in enumerate(payload)))
        for _ in range(100):
            a, b = _read_exact(connection, 2)
            size = b & 0x7F
            if size == 126:
                size = struct.unpack("!H", _read_exact(connection, 2))[0]
            elif size == 127:
                size = struct.unpack("!Q", _read_exact(connection, 8))[0]
            if size > 1048576:
                raise BrowserError("浏览器调试响应过大。")
            masking = _read_exact(connection, 4) if b & 0x80 else None
            data = _read_exact(connection, size)
            if masking:
                data = bytes(value ^ masking[i % 4] for i, value in enumerate(data))
            if a & 0x0F == 8:
                raise BrowserError("浏览器已关闭调试连接。")
            if a & 0x0F != 1:
                continue
            result = json.loads(data)
            if result.get("id") == 1:
                if "error" in result:
                    raise BrowserError(f"浏览器无法执行窗口操作：{result['error'].get('message', '未知错误')}")
                return result.get("result", {})
        raise BrowserError("浏览器未返回窗口操作结果。")


def _show_window(cfg, pid):
    windows = _window_handles(pid)
    if not windows:
        # 浏览器可能仅剩后台进程。新建普通空白窗口，不访问任何导航页或改变既有标签。
        if _verify(cfg) != pid:
            raise BrowserError("浏览器已退出，请重新打开。")
        _cdp(cfg, "Target.createTarget", {"url": "about:blank", "newWindow": True})
        deadline = time.monotonic() + 5
        while not windows and time.monotonic() < deadline:
            time.sleep(0.1)
            windows = _window_handles(pid)
    if not windows:
        raise BrowserError("浏览器已运行，但未找到可显示的普通窗口。")
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    user.IsIconic.argtypes = (wintypes.HWND,)
    user.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user.SetForegroundWindow.restype = wintypes.BOOL
    # 已最大化的窗口保留原尺寸；只有最小化窗口需要 SW_RESTORE。
    user.ShowWindow(windows[0], 9 if user.IsIconic(windows[0]) else 5)
    return bool(user.SetForegroundWindow(windows[0]))


def adopt_configuration(executable, user_data_dir, port):
    """采用用户明确指定的旧关联；必须已运行并完成程序/目录/端口核验才保存。"""
    cfg = _configuration({"executable": executable, "user_data_dir": user_data_dir,
                          "port": port, "source": "adopted"})
    with _exclusive():
        saved = _load()
        if saved and (saved["port"] != cfg["port"] or any(
                _normal_path(saved[key]) != _normal_path(cfg[key]) for key in ("executable", "user_data_dir"))):
            raise BrowserError("已有专用浏览器关联，未覆盖。请先核对现有配置。")
        _validate_profile(cfg, required=True)
        pid = _verify(cfg)
        if not pid:
            raise BrowserError("旧专用浏览器尚未运行，无法核对关联；未保存或重建资料。")
        _save(cfg)
        return _result(cfg, pid, ok=True, reused=True)


def _is_development():
    root = C.APP.parent
    return not ((root / "run_localkb.py").exists()
                or (root / "python" / "python.exe").exists()
                or (root / "portable.txt").exists())


def _legacy_cdp_endpoint():
    """旧版外部工具的明确连接配置；不读取令牌、账号或自动化正文。"""
    import tomllib
    path = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"
    if not path.is_file():
        return None
    try:
        with path.open("rb") as stream:
            content = tomllib.load(stream)
        server = (content.get("mcp_servers") or {}).get("chrome-devtools") or {}
        args = server.get("args") or []
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise ValueError("browser connector args must be a string list")
        value = _flag(args, "--browserUrl") or _flag(args, "--browser-url")
    except (OSError, ValueError, TypeError) as exc:
        raise BrowserError("无法核对既有浏览器工具配置；未另建浏览器资料。请检查 Codex 配置后重试。") from exc
    if not value:
        return None
    try:
        address = urlsplit(value)
        valid = (address.scheme == "http" and address.hostname in ("127.0.0.1", "localhost")
                 and address.port and not address.username and not address.password
                 and address.path in ("", "/") and not address.query and not address.fragment)
        if not valid:
            raise ValueError("not a local browser endpoint")
        return f"http://127.0.0.1:{address.port}"
    except ValueError as exc:
        raise BrowserError("既有浏览器工具配置不是明确的本机调试地址；未采用或另建浏览器资料。") from exc


def _default_profiles():
    root = os.environ.get("LOCALAPPDATA")
    return {_normal_path(Path(root) / relative) for relative in
            ("Google/Chrome/User Data", "Microsoft/Edge/User Data")} if root else set()


def _validate_profile(cfg, required=False):
    """旧关联的资料丢失时不能让 Chromium 静默重建；重解析点也不能冒充原目录。"""
    profile = Path(cfg["user_data_dir"])
    try:
        normalized = Path(_normal_path(profile))
        if any(normalized == Path(daily) or normalized.is_relative_to(Path(daily))
               for daily in _default_profiles()):
            raise BrowserError("专用资料目录指向日常 Chrome 或 Edge 资料，未启动或接管日常浏览器。")
        try:
            info = profile.lstat()
        except FileNotFoundError:
            if required:
                raise BrowserError(f"原专用浏览器资料目录不存在：{profile}。请恢复原目录后重试；未重建登录资料或更换关联。")
            return
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise BrowserError("专用浏览器资料目录是符号链接、联接或其他重解析点，无法确认原资料归属；未启动或重建资料。")
        if not stat.S_ISDIR(info.st_mode):
            raise BrowserError(f"专用浏览器资料路径不是目录：{profile}。未覆盖文件或重建登录资料。")
    except BrowserError:
        raise
    except OSError as exc:
        raise BrowserError(f"无法读取专用浏览器资料目录：{exc}；未启动或重建登录资料。") from exc


def legacy_configuration():
    """沿用明确历史关联；开发态绝不导入正式浏览器或其固定端口。"""
    if _is_development():
        return None
    import settings
    state = settings.load()
    for key in ("dedicated_browser", "browser"):
        raw = state.get(key)
        if raw:
            cfg = _configuration(raw)
            cfg["source"] = "adopted"
            _validate_profile(cfg, required=True)
            return cfg
    endpoint = _legacy_cdp_endpoint()
    if endpoint:
        port = urlsplit(endpoint).port
        owners = _listener_pids(port)
        if len(owners) != 1:
            raise BrowserError(f"发现原专用浏览器连接端口 {port}，但尚不能确认其运行状态。请先打开原专用浏览器，再点击本按钮；未更换端口或建立新资料。")
        row = _process(next(iter(owners)))
        profile = _flag(row["args"], "--user-data-dir") if row else None
        if (not row or not profile or _normal_path(profile) in _default_profiles()
                or Path(row["executable"]).name.lower() not in ("chrome.exe", "msedge.exe")):
            raise BrowserError(f"原调试端口 {port} 的程序或独立资料目录无法确认；未接管该程序。请先打开原专用浏览器后重试。")
        cfg = _configuration({"executable": row["executable"], "user_data_dir": profile,
                              "port": port, "source": "adopted"})
        _validate_profile(cfg, required=True)
        if not _verify(cfg):
            raise BrowserError("原专用浏览器已退出，未保存关联；请先打开原浏览器再试。")
        return cfg
    return None


def discover_legacy_candidate():
    """只读展示旧关联供核对；不启动进程、不创建资料目录、不保存配置。"""
    cfg = legacy_configuration()
    return _result(cfg, _verify(cfg) if cfg else None, candidate=bool(cfg))


def open_browser(foreground=True, candidate=None):
    """手动打开普通窗口；后台仅复用现成会话，永不抢焦点或自动执行任务。

    candidate 仅用于调用者从用户已有配置发现的明确旧关联，不能由远程请求任意指定。
    已保存配置永远优先，无任何端口冲突自动换号分支。
    """
    with _exclusive():
        cfg = _load()
        saved = cfg is not None
        if cfg is None:
            cfg = _configuration(candidate) if candidate else legacy_configuration()
            if cfg is not None:
                cfg["source"] = "adopted"
        if cfg is None:
            cfg = _new_configuration()
        _validate_profile(cfg, required=saved or cfg.get("source") == "adopted")
        if not Path(cfg["executable"]).is_file():
            raise BrowserError(f"关联的浏览器程序不存在：{cfg['executable']}。未更换浏览器或资料目录。")
        pid = _verify(cfg)
        reused = bool(pid)
        if pid is None:
            existing = _profile_processes(cfg)
            if existing:
                raise BrowserError("关联的资料目录已被浏览器使用，但调试端口未按原配置开放。请自行关闭该资料目录的浏览器后重试；应用不会结束进程或重复启动。")
            if not foreground:
                raise BrowserError("专用浏览器尚未运行；后台任务不会弹窗抢焦点。请手动点击“打开专用浏览器”后重试。")
            command = [cfg["executable"], f"--user-data-dir={cfg['user_data_dir']}",
                       f"--remote-debugging-port={cfg['port']}", "--remote-debugging-address=127.0.0.1",
                       "--no-first-run", "--no-default-browser-check", "--restore-last-session",
                       "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                       "--disable-backgrounding-occluded-windows"]
            _validate_profile(cfg, required=saved or cfg.get("source") == "adopted")
            try:
                subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, creationflags=C.SUBPROC_NO_WINDOW,
                                 close_fds=True)
            except OSError as exc:
                raise BrowserError(f"浏览器启动失败：{exc}；未保存新关联。") from exc
            deadline = time.monotonic() + 20
            last_error = None
            while time.monotonic() < deadline:
                try:
                    pid = _verify(cfg)
                    if pid:
                        break
                except BrowserError as exc:
                    last_error = exc
                time.sleep(0.15)
            if not pid:
                raise BrowserError(str(last_error) if last_error else "浏览器未在预期端口启动成功；未保存新关联。请检查浏览器提示后重试。")
        _validate_profile(cfg, required=True)
        if foreground:
            activated = _show_window(cfg, pid)
        else:
            activated = False
        # 只有成功运行、归属确认且窗口可显示后，首次关联才持久化。
        if not saved:
            _save(cfg)
        return _result(cfg, pid, ok=True, reused=reused, activated=activated)


def main(argv=None):
    """只读命令行发现；不会启动、激活、迁移浏览器或写配置。"""
    import argparse
    parser = argparse.ArgumentParser(description="读取 PaperPiggy 专用浏览器的实际本机连接配置")
    parser.add_argument("--config", action="store_true", required=True, help="标准输出只读发现 JSON")
    parser.parse_args(argv)
    try:
        result = discovery()
    except BrowserError as exc:
        result = {"configured": CONFIG_FILE.exists(), "config_file": str(CONFIG_FILE),
                  "endpoint": "", "running": False, "error": str(exc)}
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())

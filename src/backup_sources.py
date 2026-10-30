"""备份来源发现与范围约束；不打开活动数据库，不读取密码或 Cookie 内容。"""
from __future__ import annotations

import configparser
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

import config as C


class SourceError(RuntimeError):
    pass


def _norm(path):
    return os.path.normcase(os.path.abspath(str(path)))


def _under(path, parent):
    try:
        return os.path.commonpath([_norm(path), _norm(parent)]) == _norm(parent)
    except ValueError:
        return False


def _prefs_string(text, name):
    match = re.search(r'user_pref\(\s*' + re.escape(json.dumps(name)) +
                      r'\s*,\s*("(?:\\.|[^"\\])*")\s*\)', text)
    return json.loads(match[1]) if match else ""


def _registry_root():
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / "Zotero/Zotero"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Zotero"
    return Path.home() / ".zotero/zotero"


def _profile_candidates(registry):
    found = []
    ini = configparser.RawConfigParser()
    if (registry / "profiles.ini").is_file():
        ini.read(registry / "profiles.ini", encoding="utf-8-sig")
        for section in ini.sections():
            if not section.startswith("Profile") or not ini.has_option(section, "Path"):
                continue
            value = Path(ini.get(section, "Path"))
            if ini.get(section, "IsRelative", fallback="1") == "1":
                value = registry / value
            found.append((value.absolute(), ini.get(section, "Default", fallback="0") == "1"))
    for path in sorted((registry / "Profiles").glob("*")):
        if path.is_dir() and all(_norm(path) != _norm(x[0]) for x in found):
            found.append((path, False))
    return found


def discover_zotero():
    """只读匹配数据目录与 profile；多义时返回候选，不能悄悄选另一个库。"""
    import settings
    registry = _registry_root()
    configured = (os.environ.get("LOCALKB_ZOTERO_DIR") or
                  settings.load().get("zotero_dir") or "")
    candidates = []
    for profile, default in _profile_candidates(registry):
        if not (profile / "prefs.js").is_file():
            continue
        text = (profile / "prefs.js").read_text(encoding="utf-8", errors="replace")
        data = _prefs_string(text, "extensions.zotero.dataDir") or str(Path.home() / "Zotero")
        candidates.append({"data_dir": str(Path(data).absolute()), "profile_dir": str(profile),
                           "registry_dir": str(registry), "default": default})
    matching = [x for x in candidates if not configured or _norm(x["data_dir"]) == _norm(configured)]
    preferred = [x for x in matching if x["default"]]
    selected = preferred[0] if len(preferred) == 1 else matching[0] if len(matching) == 1 else None
    if selected:
        return {**selected, "found": (Path(selected["data_dir"]) / "zotero.sqlite").is_file(),
                "candidates": candidates}
    return {"found": False, "data_dir": str(configured), "profile_dir": "",
            "registry_dir": str(registry), "candidates": candidates,
            "error": "请核对并选择当前 Zotero 数据目录与对应用户配置目录。"}


def discover_browser():
    import dedicated_browser
    try:
        # 配置即唯一来源；不要求浏览器运行，也不为备份启动浏览器。
        cfg = dedicated_browser._load()
        if not cfg:
            return {"configured": False, "error": "请先用顶部按钮建立专用浏览器关联。"}
        return {**cfg, "configured": True, "config_file": str(dedicated_browser.CONFIG_FILE)}
    except Exception as exc:
        return {"configured": False, "error": str(exc)}


_CACHE_NAMES = {"Cache", "Code Cache", "GPUCache", "DawnCache", "DawnWebGPUCache",
                "DawnGraphiteCache", "GrShaderCache", "GraphiteDawnCache", "ShaderCache",
                "component_crx_cache", "extensions_crx_cache"}


def browser_excludes(root):
    """仅列举约定的根/顶层profile渲染缓存，绝不按任意子目录名过滤站点数据。"""
    root = Path(root)
    values = [name for name in _CACHE_NAMES if (root / name).exists()]
    for profile in root.iterdir():
        if profile.is_dir() and (profile.name == "Default" or profile.name.startswith("Profile ")):
            for name in _CACHE_NAMES:
                if (profile / name).exists():
                    values.append((Path(profile.name) / name).as_posix())
    return sorted(values)


def _directory(value, label):
    from backup_jobs import check_path
    if not value or not Path(value).is_absolute():
        raise SourceError(f"请先选择{label}的绝对路径。")
    path = check_path(value, allow_missing=False)
    if not path.is_dir():
        raise SourceError(f"{label}不是可读目录：{path}")
    return path


def validate_sources(job, data_dir=None):
    """返回来源根和元数据。实际文件内容必须在快照建立后读取。"""
    import backup
    data = Path(data_dir or C.DATA).absolute()
    roots, metadata = [], {"app_version": C.APP_VERSION}
    if job["kind"] == "zotero":
        sources = job.get("sources") or {}
        library = _directory(sources.get("data_dir"), "Zotero 数据目录")
        profile = _directory(sources.get("profile_dir"), "Zotero 用户配置目录")
        if not (library / "zotero.sqlite").is_file() or not (profile / "prefs.js").is_file():
            raise SourceError("所选目录缺少 zotero.sqlite 或 prefs.js，未开始备份。")
        prefs = (profile / "prefs.js").read_text(encoding="utf-8", errors="replace")
        configured = _prefs_string(prefs, "extensions.zotero.dataDir")
        if configured and _norm(configured) != _norm(library):
            raise SourceError("所选 profile 指向另一个 Zotero 数据目录，请核对关联。")
        roots.extend([{"id": "zotero_data", "source": str(library)},
                      {"id": "zotero_profile", "source": str(profile)}])
        # 默认结构为 .../Zotero/Profiles/<name>，其他位置须从profiles.ini中证实关联。
        registry = _registry_root()
        selected = [x for x in _profile_candidates(registry) if _norm(x[0]) == _norm(profile)]
        if selected:
            roots.append({"id": "zotero_registry", "source": str(registry),
                          "include": ["profiles.ini", "installs.ini"]})
        metadata["zotero"] = {"profile_name": profile.name,
                               "registry_dir": str(registry) if selected else "",
                               "version": _zotero_version(profile)}
    elif job["kind"] == "paperpiggy":
        _directory(str(data), "PaperPiggy 数据目录")
        include = [*backup.CORE_IN_DATA, "settings.json"]
        if job.get("include_index"):
            include.extend(backup.INDEX_IN_DATA)
        roots.extend([
            {"id": "paperpiggy_data", "source": str(data), "include": include,
             "exclude": ["backup_jobs/work", "backup_jobs/locks", "backup_jobs/requests",
                         "backup_jobs/service", "backup_jobs/install", "backup_jobs/restore_journals",
                         "backup_jobs/restore-gate.json", "backup_jobs/recovery-error.json"]},
            {"id": "paperpiggy_home", "source": str(data.parent),
             "include": list(backup.CORE_IN_HOME)},
        ])
        if job.get("include_browser"):
            cfg = discover_browser()
            if not cfg.get("configured"):
                raise SourceError(cfg.get("error") or "专用浏览器尚未关联。")
            profile = _directory(cfg.get("user_data_dir"), "专用浏览器资料目录")
            roots.append({"id": "browser", "source": str(profile),
                          "exclude": browser_excludes(profile)})
            metadata["browser"] = {k: cfg[k] for k in ("executable", "user_data_dir", "port", "schema")}
    else:
        raise SourceError("未知备份任务类型。")
    for root in roots:
        root["original_source"] = root["source"]
    destination = job.get("destination")
    if not destination:
        raise SourceError("请先在备份与恢复页面选择本任务备份位置。")
    if "过时备份" in Path(destination).parts:
        raise SourceError("过时备份目录受保护，不能作为本任务目的地。")
    for root in roots:
        # PP home可能包含其自有backups目录，但include白名单不会包含它。
        if _under(destination, root["source"]):
            relative = Path(os.path.relpath(destination, root["source"])).as_posix()
            included = root.get("include")
            if included is None or any(relative == x or relative.startswith(x + "/") for x in included):
                raise SourceError("备份目的地不能位于已选备份来源之内，避免递归备份。")
    return roots, metadata


def _zotero_version(profile):
    ini = configparser.RawConfigParser()
    try:
        ini.read(Path(profile) / "compatibility.ini", encoding="utf-8-sig")
        return ini.get("Compatibility", "LastVersion", fallback="")
    except (OSError, configparser.Error):
        return ""


def installed_zotero_version(profile=None):
    """读取已安装程序版本，不启动 Zotero；找不到时明确返回未知。"""
    if sys.platform != "win32":
        return ""
    import ctypes
    from ctypes import wintypes
    candidates = []
    if profile:
        ini = configparser.RawConfigParser()
        ini.read(Path(profile) / "compatibility.ini", encoding="utf-8-sig")
        directory = ini.get("Compatibility", "LastAppDir", fallback="")
        if directory:
            candidates.append(Path(directory) / "zotero.exe")
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for flags in (winreg.KEY_READ | winreg.KEY_WOW64_64KEY, winreg.KEY_READ | winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\zotero.exe", 0, flags) as key:
                    candidates.append(Path(str(winreg.QueryValueEx(key, None)[0]).strip('"')))
            except OSError:
                pass
    version = ctypes.WinDLL("version", use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD))
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p)
    version.VerQueryValueW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT))
    for path in candidates:
        if not path.is_file():
            continue
        ignored = wintypes.DWORD()
        size = version.GetFileVersionInfoSizeW(str(path), ctypes.byref(ignored))
        if not size:
            continue
        buffer = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(str(path), 0, size, buffer):
            continue
        pointer, length = ctypes.c_void_p(), wintypes.UINT()
        if version.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)) and length.value >= 52:
            fields = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD * 13)).contents
            if fields[0] == 0xFEEF04BD:
                return ".".join(map(str, (fields[2] >> 16, fields[2] & 65535, fields[3] >> 16)))
    return ""


def prepare_snapshot_roots(job, roots, snapshot):
    """只有此处把真实来源映射到静态快照；脱敏在静态 settings 上进行。"""
    import backup
    from backup_jobs import _snapshot_source, check_source_path
    output = []
    for original in roots:
        root = {**original, "source": str(snapshot.map_path(original["source"]))}
        root.setdefault("original_source", original["source"])
        trusted = _snapshot_source(root["source"])
        if trusted is not None:
            root["_snapshot_source"] = trusted
        check_source_path(root, allow_missing=False)
        if root["id"] == "paperpiggy_data" and not job.get("include_key"):
            path = check_source_path(root, "settings.json")
            if path.is_file():
                state = json.loads(path.read_text(encoding="utf-8"))
                for section, field in backup._KEY_FIELDS:
                    if isinstance(state.get(section), dict):
                        state[section][field] = ""
                root["overlay_files"] = {"settings.json": json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8")}
        output.append(root)
    return output


def validate_zotero_attachments(mapped_roots):
    """在静态副本中检查附件覆盖。原库与其WAL从快照读，不打开活动库。"""
    import shutil
    import tempfile
    from backup_jobs import check_source_path
    data = next((r for r in mapped_roots if r["id"] == "zotero_data"), None)
    if not data:
        return
    source = Path(data["source"])
    with tempfile.TemporaryDirectory(prefix="pp-zotero-check-") as temp:
        db = Path(temp) / "zotero.sqlite"
        for suffix in ("", "-wal", "-shm", "-journal"):
            src = check_source_path(data, "zotero.sqlite" + suffix)
            if src.is_file():
                shutil.copyfile(src, Path(str(db) + suffix))
        con = sqlite3.connect(str(db), timeout=5)
        try:
            if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='itemAttachments'").fetchone():
                raise SourceError("Zotero 库缺少附件表，未能验证附件覆盖。")
            rows = con.execute("SELECT items.key, itemAttachments.linkMode, itemAttachments.path "
                               "FROM itemAttachments JOIN items USING(itemID)").fetchall()
            missing, linked = [], []
            for key, mode, path in rows:
                if not re.fullmatch(r"[A-Z0-9]{8}", str(key)):
                    raise SourceError("附件标识格式无效，未从异常路径读取文件。")
                if mode == 2:
                    linked.append(key)
                elif path and str(path).startswith("storage:"):
                    relative = str(path).split(":", 1)[1]
                    candidate = source / "storage" / key / relative
                    if not _under(candidate, source / "storage" / key) or not candidate.is_file():
                        missing.append(key)
            if linked or missing:
                raise SourceError(f"附件覆盖未通过：{len(linked)} 个外部链接附件、{len(missing)} 个本地附件缺失。"
                                  "未把不完整来源登记为成功备份，请先处理来源。")
        finally:
            con.close()


def _process_entries():
    """Toolhelp 只枚举 PID/程序名；命令行另用有限权限查询，绝不调用 WMI。"""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes
    import dedicated_browser as DB
    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]
    kernel = DB._winlibs()
    kernel.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(Entry))
    kernel.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(Entry))
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise SourceError("无法核对程序运行状态，未执行原位恢复。")
    try:
        entry = Entry(); entry.dwSize = ctypes.sizeof(entry)
        more = kernel.Process32FirstW(handle, ctypes.byref(entry))
        rows = []
        while more:
            rows.append({"pid": int(entry.th32ProcessID), "name": entry.szExeFile.lower()})
            more = kernel.Process32NextW(handle, ctypes.byref(entry))
        return rows
    finally:
        kernel.CloseHandle(handle)


def ensure_paperpiggy_mcp_stopped(app_dir):
    """核验当前安装的 MCP 已退出；不结束进程，也不接管其他安装。"""
    if sys.platform != "win32":
        return
    import dedicated_browser as DB
    expected = _norm(Path(app_dir) / "mcp_server.py")
    for entry in _process_entries():
        if not re.fullmatch(r"(?:python(?:w|\d+(?:\.\d+)?)?|pypy\d*|py)\.exe", entry["name"]):
            continue
        row = DB._process(entry["pid"])
        if not row:
            raise SourceError("无法核对某个 Python 进程是否为小猪 MCP，请先退出相关助手连接再恢复。")
        args = row.get("args", [])
        for arg in args[1:]:
            if Path(arg).name.lower() == "mcp_server.py":
                if not Path(arg).is_absolute() or _norm(arg) == expected:
                    raise SourceError("小猪 MCP 仍在运行，请先断开助手的 PaperPiggy MCP 连接，再原位恢复。")
        if any(args[i] == "-m" and args[i + 1] == "mcp_server" for i in range(len(args) - 1)):
            raise SourceError("检测到模块方式运行的小猪 MCP，无法证实安装归属，请先退出该连接。")


def ensure_restore_stopped(kind, targets):
    """只探测，不终止用户进程。"""
    if sys.platform != "win32":
        return
    import dedicated_browser as DB
    if "browser" in targets:
        profile = targets["browser"]
        for row in DB._processes():
            if row.get("unreadable"):
                raise SourceError("无法核对某个浏览器进程的资料目录，未执行覆盖恢复。请先手动退出相关浏览器。")
            value = DB._flag(row.get("args", []), "--user-data-dir")
            if value and _norm(value) == _norm(profile):
                raise SourceError("所选专用浏览器仍在运行，请手动退出后再恢复；应用不会关闭其他浏览器。")
    if kind == "zotero" and any(row["name"] == "zotero.exe" for row in _process_entries()):
        raise SourceError("Zotero 仍在运行，请手动退出后再原位恢复。")

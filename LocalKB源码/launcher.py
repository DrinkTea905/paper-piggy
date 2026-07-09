# -*- coding: utf-8 -*-
"""
LocalKB 启动器 —— pywebview 原生应用窗口（真应用，非浏览器标签、非 Edge）。
后台起 server → 等就绪 → 原生窗口打开 UI；关窗口即停本次服务、退出。
双击 启动.bat（pythonw，无黑窗）调用本文件。
pywebview 不可用（如缺 WebView2 运行时）时自动回退系统浏览器，并给出可见提示。
"""
import subprocess, sys, time, socket
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C
import requests


def _ts():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _logline(msg):
    """写 launcher.log（pythonw 无控制台，日志是唯一诊断途径）。"""
    try:
        C.LOGS.mkdir(parents=True, exist_ok=True)
        with open(C.LOGS / "launcher.log", "a", encoding="utf-8", errors="replace") as f:
            f.write(f"[{_ts()}] {msg}\n")
    except Exception:
        pass


def _notify(title, msg):
    """给用户一个可见提示（pythonw 无黑窗，出错/回退必须用弹窗而非 print，否则用户对着空屏干等）。"""
    if sys.platform == "win32":
        try:
            import ctypes
            # 0x40=信息图标；0x40000=置顶，确保能看到
            ctypes.windll.user32.MessageBoxW(0, str(msg), str(title), 0x40 | 0x40000)
            return
        except Exception:
            pass
    _logline(f"{title}：{msg}")


def _is_localkb_health(j):
    """校验 /health 响应确实来自 LocalKB，避免把占用 8770 的别的服务误判成本应用（P2）。"""
    if not isinstance(j, dict):
        return False
    if j.get("app") == "localkb" or j.get("service") == "localkb":
        return True
    # 兼容：LocalKB /health 的特征字段组合（后端未加显式标识时据此判定）
    return "ready" in j and "mode" in j and "building" in j


def _health():
    try:
        j = requests.get(C.DAEMON_URL + "/health", timeout=2).json()
    except Exception:
        return None
    return j if _is_localkb_health(j) else None


def server_running():
    return _health() is not None


def _port_in_use(host, port):
    """端口探测：仅判断 TCP 是否可连（区分「端口空闲」与「被别的程序占用」）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.6)
    try:
        return s.connect_ex((host, port)) == 0
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def _rotate_and_open_server_log():
    """server 子进程 stdout/stderr 落 logs/server.log（换机排障命脉，替代原来的 DEVNULL 静默）。
    超 2MB 先轮转一份 server.log.1，避免无限增长。返回可传给 Popen 的二进制句柄。"""
    C.LOGS.mkdir(parents=True, exist_ok=True)
    p = C.LOGS / "server.log"
    try:
        if p.exists() and p.stat().st_size > 2 * 1024 * 1024:
            bak = C.LOGS / "server.log.1"
            try:
                bak.unlink()
            except Exception:
                pass
            p.rename(bak)
    except Exception:
        pass
    f = open(p, "ab")
    try:
        f.write((f"\n===== [{_ts()}] 启动 server.py =====\n").encode("utf-8", "replace"))
        f.flush()
    except Exception:
        pass
    return f


def _tail(path, n=15):
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:]) if lines else "(日志为空)"
    except Exception:
        return "(无法读取 server.log)"


def _check_path_ascii():
    """首启轻量检测（P10）：数据/模型目录含非 ASCII 或空格时写日志提示
    （onnxruntime/lancedb/HF 在此类路径下有加载失败风险，便于事后首查）。"""
    for label, p in (("数据目录", C.DATA), ("模型目录", C.MODELS)):
        s = str(p)
        if any(ord(ch) > 127 for ch in s) or " " in s:
            _logline(f"⚠ {label}含非 ASCII 字符或空格：{s} —— 若本地模型加载失败，"
                     f"请优先排查此路径（可设环境变量 LOCALKB_DATA / LOCALKB_MODELS 指向纯英文无空格目录）。")


def _system_light():
    """系统是否浅色主题（HKCU AppsUseLightTheme）。读不到默认浅色。"""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        return bool(winreg.QueryValueEx(k, "AppsUseLightTheme")[0])
    except Exception:
        return True


def _ensure_icon():
    """把 web/PaperPiggy.png 封成 .ico（纯 stdlib，PNG-in-ICO），供窗口/任务栏图标用。返回 ico 路径或 None。"""
    try:
        png = C.APP / "web" / "PaperPiggy.png"
        ico = C.DATA / "PaperPiggy.ico"
        if ico.exists() and ico.stat().st_size > 0:
            return str(ico)
        if not png.exists():
            return None
        import struct
        data = png.read_bytes()
        w = int.from_bytes(data[16:20], "big"); h = int.from_bytes(data[20:24], "big")
        bw = 0 if w >= 256 else w; bh = 0 if h >= 256 else h   # 0 表示 256
        hdr = struct.pack("<HHH", 0, 1, 1)                     # ICONDIR: reserved,type=icon,count
        entry = struct.pack("<BBBBHHII", bw, bh, 0, 0, 1, 32, len(data), 22)  # ICONDIRENTRY
        C.DATA.mkdir(parents=True, exist_ok=True)
        ico.write_bytes(hdr + entry + data)
        return str(ico)
    except Exception:
        return None


def _tint_titlebar_async():
    """#1：把原生窗口的标题栏从系统默认黑色改成跟随系统浅/深色，与应用内容协调。
    用 FindWindowW 按标题找到窗口再 DWM 着色（跨 pywebview 后端稳），纯外观、失败静默。
    #1b：顺手把窗口图标从 pythonw 默认的 Python 图标换成 PaperPiggy。"""
    if sys.platform != "win32":
        return
    import threading
    def work():
        try:
            import ctypes
            light = _system_light()
            user32 = ctypes.windll.user32
            dwm = ctypes.windll.dwmapi
            for _ in range(50):                       # 最多等 ~10s 直到窗口出现
                hwnd = user32.FindWindowW(None, "PaperPiggy")
                if hwnd:
                    # DWMWA_USE_IMMERSIVE_DARK_MODE=20：0=浅色标题栏，1=深色（Win10 2004+）
                    val = ctypes.c_int(0 if light else 1)
                    dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), ctypes.sizeof(val))
                    # DWMWA_CAPTION_COLOR=35：直接给标题栏底色，与内容近似（Win11 22000+；老系统忽略）
                    cap = ctypes.c_int(0x00F7F7F7 if light else 0x001C1C1E)   # 浅≈#F7F7F7 / 深≈#1E1C1C
                    dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(cap), ctypes.sizeof(cap))
                    # #1b：设应用图标，替换 pythonw 默认的 Python 图标（失败静默）
                    try:
                        ico = _ensure_icon()
                        if ico:
                            hicon = user32.LoadImageW(None, ico, 1, 0, 0, 0x00000010)   # IMAGE_ICON, LR_LOADFROMFILE
                            if hicon:
                                user32.SendMessageW(hwnd, 0x0080, 0, hicon)   # WM_SETICON, ICON_SMALL
                                user32.SendMessageW(hwnd, 0x0080, 1, hicon)   # WM_SETICON, ICON_BIG
                    except Exception:
                        pass
                    return
                time.sleep(0.2)
        except Exception as e:
            _logline(f"标题栏着色跳过（纯外观，不影响使用）：{repr(e)}")
    threading.Thread(target=work, daemon=True).start()


def main():
    _check_path_ascii()
    proc = None
    logf = None
    if not server_running():
        # 端口已被别的（非 LocalKB）程序占用 → 我们的 server 起不来，先给人话而非静默崩溃
        if _port_in_use(C.DAEMON_HOST, C.DAEMON_PORT):
            _logline(f"端口 {C.DAEMON_PORT} 被非 LocalKB 程序占用，无法启动。")
            _notify("端口被占用",
                    f"端口 {C.DAEMON_PORT} 已被其它程序占用，PaperPiggy 无法启动。\n"
                    f"请关闭占用该端口的程序后重试。")
            return
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW（无控制台）
        logf = _rotate_and_open_server_log()
        proc = subprocess.Popen([sys.executable, str(C.APP / "server.py")],
                                stdout=logf, stderr=logf,
                                stdin=subprocess.DEVNULL, creationflags=flags)

    up = False
    for _ in range(60):
        if server_running():
            up = True
            break
        # 子进程已崩溃退出 → 不必再空等满 60 秒
        if proc is not None and proc.poll() is not None:
            break
        time.sleep(1)

    if not up:
        # 启动失败：读日志末尾给人话，而不是打开一个连不上的死链让用户干等
        tail = _tail(C.LOGS / "server.log")
        _logline("服务启动失败（轮询超时或子进程退出）。server.log 末尾：\n" + tail)
        _notify("服务启动失败",
                f"PaperPiggy 后台服务未能启动，已停止等待。\n\n"
                f"详见日志：{C.LOGS / 'server.log'}\n\n最后几行：\n{tail}")
        try:
            if proc is not None:
                proc.terminate()
        except Exception:
            pass
        if logf:
            try:
                logf.close()
            except Exception:
                pass
        return

    try:
        import webview
        # text_select=True：允许在原生窗口里选中/复制文本（pywebview 默认整窗禁选，
        # 导致「检索结果不可复制」——根因在此，非 CSS）。改后须重启原生窗口实测。
        webview.create_window("PaperPiggy", C.DAEMON_URL,
                              width=1300, height=880, min_size=(960, 640),
                              text_select=True)
        _tint_titlebar_async()   # #1：标题栏跟随系统浅/深色，别再是突兀的黑条
        webview.start()   # 阻塞直到窗口关闭
        if proc:          # 关窗口 → 停掉本次启动的 server（关窗即退出应用）
            try:
                proc.terminate()
            except Exception:
                pass
        if logf:
            try:
                logf.close()
            except Exception:
                pass
    except Exception as e:
        # pywebview 打不开（如缺 WebView2 运行时）：记日志、给可见提示，回退系统浏览器（server 常驻供其访问）
        _logline(f"pywebview 启动失败，回退浏览器：{repr(e)}")
        try:
            import traceback
            _logline(traceback.format_exc())
        except Exception:
            pass
        _notify("已用系统浏览器打开",
                "未检测到 WebView2 运行时，PaperPiggy 已改用系统浏览器打开。\n\n"
                "注意：关闭浏览器标签不会退出后台服务；如需完全退出 PaperPiggy，"
                "请在任务管理器结束 python / pythonw 进程。")
        import webbrowser
        webbrowser.open(C.DAEMON_URL)
        # 回退分支收尾：server 需常驻供浏览器访问，故不 terminate proc；本进程随即退出，
        # logf 句柄由进程退出自动释放（子进程持有各自的 fd 副本，仍能继续写 server.log）。


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
PaperPiggy 启动器 —— pywebview 原生应用窗口（真应用，非浏览器标签、非 Edge）。
后台起 server → 等就绪 → 原生窗口打开 UI；关窗口即停本次服务、退出。
双击 启动.bat（pythonw，无黑窗）调用本文件。
pywebview 不可用（如缺 WebView2 运行时）时自动回退系统浏览器，并给出可见提示。
"""
import subprocess, sys, time, socket, os, threading
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
    """校验 /health 响应确实来自本应用，避免把占用 8770 的别的服务误判成本应用（P2）。"""
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


def _focus_existing_window():
    """单实例：把已经在跑的那个 PaperPiggy 窗口拉到前台。找到并唤起 → True。

    窗口标题固定是 "PaperPiggy"（`webview.create_window` 的第一个参数），
    与 `_tint_titlebar_async()` 用的是同一套 FindWindowW 查找。
    ⚠️ 必须先 ShowWindow(SW_SHOW)，不能只 SetForegroundWindow：
       窗口有可能正处于**隐藏**状态（见 CLAUDE.md §6「启动器的 SW_HIDE」），
       而 SetForegroundWindow 对隐藏窗口是无效的 —— 用户仍然什么都看不到。
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        u32 = ctypes.windll.user32
        hwnd = u32.FindWindowW(None, "PaperPiggy")
        if not hwnd:
            return False
        SW_SHOW, SW_RESTORE = 5, 9
        u32.ShowWindow(hwnd, SW_RESTORE if u32.IsIconic(hwnd) else SW_SHOW)
        u32.SetForegroundWindow(hwnd)     # 前台锁下可能失败，但窗口已经 SW_SHOW 出来了
        return True
    except Exception as e:
        _logline(f"唤起已有窗口失败：{repr(e)}")
        return False


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
    try:
        C.LOGS.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
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
        # 源 PNG 更新时必须重建；否则已生成的 PaperPiggy.ico 会让新版图标永远不生效。
        if (ico.exists() and ico.stat().st_size > 0 and png.exists()
                and ico.stat().st_mtime >= png.stat().st_mtime):
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


class _JsApi:
    """暴露给前端的原生桥（window.pywebview.api）。必须住在 launcher 进程——原生窗口在这里，
       server 是独立子进程、其 webview.windows 恒空，之前的 /setup/pick_folder 在 server 里
       调 create_file_dialog 永远失败（死按钮）。"""
    def pick_folder(self):
        """弹原生目录选择器，返回选中的绝对路径（取消/失败返回空串）。"""
        try:
            import webview
            w = webview.windows[0] if getattr(webview, "windows", None) else None
            if not w:
                return ""
            r = w.create_file_dialog(webview.FOLDER_DIALOG)
            if not r:
                return ""
            return (r[0] if isinstance(r, (list, tuple)) else str(r)) or ""
        except Exception as e:
            _logline(f"pick_folder 失败：{repr(e)}")
            return ""

    def apply_update(self, zip_path=""):
        r"""执行版本升级：拉起**独立的** updater 进程（它会等本应用退出→换 app\→重启），
        然后关掉窗口让本应用退出。必须在 launcher 进程里做——只有这里能关原生窗口、
        也只有本应用退出后 app\ 里的 .py 才解锁、才能被替换。
        为什么另起进程：updater 自己就在 app\ 里，不能一边替换 app\ 一边还在运行它。

        返回 {ok, error?}。ok=True 后窗口即将关闭、应用会自动重启到新版。
        """
        try:
            import config as C
            up_dir = C.DATA.parent / "update"
            zp = Path(zip_path) if zip_path else None
            if not (zp and zp.exists()):
                cands = sorted(up_dir.glob("app-*.zip"), key=lambda p: p.stat().st_mtime)
                if not cands:
                    return {"ok": False, "error": "没找到已下载的更新包，请先下载"}
                zp = cands[-1]

            updater = C.APP / "updater.py"
            # 分发包必须用 pythonw；源码态测试时也优先找当前解释器旁的 pythonw。
            pyw = C.APP.parent / "python" / "pythonw.exe"
            sibling_pyw = Path(sys.executable).with_name("pythonw.exe")
            exe = str(pyw if pyw.exists() else sibling_pyw if sibling_pyw.exists() else Path(sys.executable))
            # CREATE_NO_WINDOW 不会把子进程绑死在父进程上；close_fds=True 后 launcher 退出，
            # updater 仍可继续等待 PID、替换 app 并重启。不要再用 DETACHED_PROCESS：它会让
            # CREATE_NO_WINDOW 失效，也违背项目「所有子进程统一无窗」的硬约束。
            up_dir.mkdir(parents=True, exist_ok=True)
            update_log = up_dir / "update.log"
            logf = open(update_log, "ab", buffering=0)
            try:
                child = subprocess.Popen(
                    [exe, str(updater), "--apply", str(zp), "--pid", str(os.getpid())],
                    cwd=str(C.APP.parent), creationflags=C.SUBPROC_NO_WINDOW, close_fds=True,
                    stdin=subprocess.DEVNULL,
                    stdout=logf, stderr=subprocess.STDOUT)
            finally:
                logf.close()
            _logline(f"已拉起 updater（子进程 {child.pid}，launcher {os.getpid()} 退出后替换）；"
                     f"诊断日志：{update_log}")

            # 稍等一下让 updater 起来并进入「等本进程退出」的循环，再关窗口
            def _close():
                time.sleep(1.0)
                try:
                    import webview
                    if getattr(webview, "windows", None):
                        webview.windows[0].destroy()   # → webview.start() 返回 → 应用退出
                except Exception as e:
                    _logline(f"关窗失败（updater 仍会在超时后继续）：{repr(e)}")
            threading.Thread(target=_close, daemon=True).start()
            return {"ok": True}
        except Exception as e:
            _logline(f"apply_update 失败：{repr(e)}")
            return {"ok": False, "error": str(e)}


def _stop_server(pid, proc):
    """关窗 → 彻底停掉后端及其子进程树，根治「关掉应用后后端没关掉」的 orphan 堆积。
       此前只有 `if proc: proc.terminate()`——① 连上已有 server（proc=None）时根本不杀；② terminate 只杀直接
       子进程、不杀子孙。现在按 server /health 回传的 pid 杀整棵树（Windows taskkill /T /F，与 server.py 同款），
       无论本次是否亲自起的 server 都杀。失败/非 Windows 退回 terminate。"""
    if pid:
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               creationflags=0x08000000,   # CREATE_NO_WINDOW：不弹黑窗（§0.5②）
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
            else:
                os.kill(int(pid), 15)   # SIGTERM（mac 未打包，best-effort）
        except Exception as e:
            _logline(f"关窗停后端失败（taskkill pid={pid}）：{repr(e)}")
    if proc is not None:   # 兜底：本次亲起的 proc 再 terminate 一次（pid 拿不到时的后备）
        try:
            proc.terminate()
        except Exception:
            pass


def main():
    _check_path_ascii()
    proc = None
    logf = None

    running = server_running()

    # ── 单实例 ──────────────────────────────────────────────────────────────
    # 已经有一个 PaperPiggy 在跑 → 唤起它的窗口，自己退出，绝不再开第二个。
    # 老行为：server 在跑就跳过起 server、直接再 create_window 一个 —— 于是**每双击一次
    # 就多一个 launcher 进程**（都连同一个 server）。用户实机点了 6 次，攒了 6 个进程。
    # 而当时窗口还因为 VBS 的 SW_HIDE 是隐藏的（见 CLAUDE.md §6），他看不见、以为没启动，
    # 就继续点 —— 两个 bug 一叠加，就是「点了没反应，进程越攒越多」。
    if running and _focus_existing_window():
        _logline("已有实例在运行 → 已把它的窗口拉到前台，本次启动退出。")
        return
    # 找不到窗口（上一个实例的窗口已关、只剩 server 还活着）→ 往下走，开一个窗口连上它。

    if not running:
        # 端口已被别的（非 LocalKB）程序占用 → 我们的 server 起不来，先给人话而非静默崩溃
        if _port_in_use(C.DAEMON_HOST, C.DAEMON_PORT):
            _logline(f"端口 {C.DAEMON_PORT} 被其它程序占用，无法启动。")
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

    # 关窗时按 pid 杀掉整棵 server 进程树（含子进程），无论本次是否亲自起的 server —— 根治 orphan 堆积。
    # server /health 现在回传自己的 pid；拿不到就退回本次 Popen 的 proc.pid。
    srv_pid = None
    try:
        j = _health()
        srv_pid = (j or {}).get("pid")
    except Exception:
        pass
    if srv_pid is None and proc is not None:
        srv_pid = proc.pid

    try:
        import webview
        # text_select=True：允许在原生窗口里选中/复制文本（pywebview 默认整窗禁选，
        # 导致「检索结果不可复制」——根因在此，非 CSS）。改后须重启原生窗口实测。
        webview.create_window("PaperPiggy", C.DAEMON_URL,
                              width=1300, height=880, min_size=(960, 640),
                              text_select=True, js_api=_JsApi())   # 原生目录选择器桥（pick_folder）
        _tint_titlebar_async()   # #1：标题栏跟随系统浅/深色，别再是突兀的黑条
        webview.start()   # 阻塞直到窗口关闭
        _stop_server(srv_pid, proc)   # 关窗即彻底停掉后端及其子进程树（含「连上已有 server」的情形），不再留 orphan
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

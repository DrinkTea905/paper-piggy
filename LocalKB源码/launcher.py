# -*- coding: utf-8 -*-
"""
LocalKB 启动器 —— pywebview 原生应用窗口（真应用，非浏览器标签、非 Edge）。
后台起 server → 等就绪 → 原生窗口打开 UI；关窗口即停本次服务、退出。
双击 启动.bat（pythonw，无黑窗）调用本文件。
pywebview 不可用时自动回退系统浏览器。
"""
import subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import config as C
import requests

def server_running():
    try:
        return bool(requests.get(C.DAEMON_URL + "/health", timeout=2).json())
    except Exception:
        return False

def main():
    proc = None
    if not server_running():
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW（无控制台）
        proc = subprocess.Popen([sys.executable, str(C.APP / "server.py")],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, creationflags=flags)

    for _ in range(60):
        if server_running():
            break
        time.sleep(1)

    try:
        import webview
        # text_select=True：允许在原生窗口里选中/复制文本（pywebview 默认整窗禁选，
        # 导致「检索结果不可复制」——根因在此，非 CSS）。改后须重启原生窗口实测。
        webview.create_window("PaperPiggy", C.DAEMON_URL,
                              width=1300, height=880, min_size=(960, 640),
                              text_select=True)
        webview.start()   # 阻塞直到窗口关闭
        if proc:          # 关窗口 → 停掉本次启动的 server（关窗即退出应用）
            try:
                proc.terminate()
            except Exception:
                pass
    except Exception as e:
        # pywebview 打不开（如缺 WebView2 运行时）：记日志便于诊断，回退系统浏览器（server 常驻供其访问）
        try:
            import traceback, time as _t
            C.LOGS.mkdir(parents=True, exist_ok=True)
            with open(C.LOGS / "launcher.log", "a", encoding="utf-8") as f:
                f.write(f"\n[{_t.strftime('%Y-%m-%d %H:%M:%S')}] pywebview 启动失败，回退浏览器：{repr(e)}\n")
                f.write(traceback.format_exc())
        except Exception:
            pass
        import webbrowser
        webbrowser.open(C.DAEMON_URL)

if __name__ == "__main__":
    main()

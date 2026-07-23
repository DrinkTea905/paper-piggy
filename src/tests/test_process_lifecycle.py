# -*- coding: utf-8 -*-
"""Windows 进程生命周期安全回归：不接触真实库、不启动建库。"""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import launcher
import localkb
import mcp_server
import server


IDENTITY = {"app": "paperpiggy", "service": "paperpiggy-local-api", "pid": 1234}


class HealthIdentityTests(unittest.TestCase):
    def test_health_exposes_explicit_identity(self):
        got = server.health()
        self.assertEqual("paperpiggy", got["app"])
        self.assertEqual("paperpiggy-local-api", got["service"])
        self.assertEqual(__import__("os").getpid(), got["pid"])

    def test_launcher_rejects_shape_only_and_legacy_identity(self):
        self.assertFalse(launcher._is_localkb_health({
            "ready": True, "mode": "full", "building": False, "pid": 1234,
        }))
        self.assertFalse(launcher._is_localkb_health({"app": "localkb", "pid": 1234}))
        self.assertTrue(launcher._is_localkb_health(IDENTITY))


class LauncherShutdownTests(unittest.TestCase):
    def test_own_server_is_stopped_via_popen_handle(self):
        proc = mock.Mock()
        proc.poll.return_value = None
        launcher._stop_server(proc=proc)
        proc.terminate.assert_called_once_with()
        proc.wait.assert_called_once_with(timeout=8)
        proc.kill.assert_not_called()

    def test_attached_server_requires_identity_and_same_pid_recheck(self):
        attached = mock.Mock(pid=1234)
        attached.is_running.return_value = True
        with mock.patch.object(launcher, "_health", return_value=dict(IDENTITY)):
            launcher._stop_server(attached=attached)
        attached.terminate.assert_called_once_with(0)
        attached.close.assert_called_once_with()

    def test_attached_server_is_not_stopped_after_pid_or_identity_change(self):
        for health in (
                {**IDENTITY, "pid": 9876},
                {"ready": True, "mode": "full", "building": False, "pid": 1234},
                None):
            with self.subTest(health=health):
                attached = mock.Mock(pid=1234)
                attached.is_running.return_value = True
                with mock.patch.object(launcher, "_health", return_value=health):
                    launcher._stop_server(attached=attached)
                attached.terminate.assert_not_called()
                attached.close.assert_called_once_with()


class ServerSpawnTests(unittest.TestCase):
    def test_cli_launchers_use_pythonw_and_no_window_flag(self):
        for module in (mcp_server, localkb):
            with self.subTest(module=module.__name__), \
                    mock.patch.object(module, "health", return_value=None), \
                    mock.patch.object(module, "_pythonw_executable", return_value=r"C:\runtime\pythonw.exe"), \
                    mock.patch.object(module.subprocess, "Popen") as popen:
                extra = mock.patch.object(module, "_server_log", return_value=subprocess.DEVNULL) \
                    if module is mcp_server else mock.patch.object(module, "URL", "http://127.0.0.1:1")
                with extra:
                    self.assertFalse(module.ensure_up(wait=0))
                args, kwargs = popen.call_args
                self.assertEqual(r"C:\runtime\pythonw.exe", args[0][0])
                self.assertEqual(module.C.SUBPROC_NO_WINDOW, kwargs["creationflags"])
                self.assertTrue(kwargs["close_fds"])

    @unittest.skipUnless(sys.platform == "win32", "Windows Job Object 专项")
    def test_windows_job_terminates_owned_build_process_tree(self):
        import ctypes
        from ctypes import wintypes
        proc = job = child_handle = None
        try:
            code = (
                "import subprocess,sys,time; time.sleep(.4); "
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
                "creationflags=0x08000000); print(p.pid,flush=True); time.sleep(60)"
            )
            proc, job = server._spawn_build_process(
                [sys.executable, "-c", code],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=server.C.SUBPROC_NO_WINDOW)
            child_pid = int(proc.stdout.readline().decode("ascii").strip())
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel32.WaitForSingleObject.restype = wintypes.DWORD
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            child_handle = kernel32.OpenProcess(0x00100000, False, child_pid)  # SYNCHRONIZE
            self.assertTrue(child_handle)
            server._kill_tree(proc, job)
            self.assertIsNotNone(proc.wait(timeout=5))
            self.assertEqual(0, kernel32.WaitForSingleObject(child_handle, 5000))
        finally:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
            if proc is not None and proc.stdout is not None:
                proc.stdout.close()
            server._close_build_job(job)
            if child_handle:
                kernel32.CloseHandle(child_handle)

    def test_production_python_sources_do_not_use_system_process_tree_killer(self):
        forbidden = "task" + "kill"
        offenders = []
        for path in SRC.rglob("*.py"):
            rel = path.relative_to(SRC)
            if "tests" in rel.parts or "dist" in rel.parts:
                continue
            if forbidden in path.read_text(encoding="utf-8", errors="replace").lower():
                offenders.append(str(rel))
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()

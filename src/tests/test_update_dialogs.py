# -*- coding: utf-8 -*-
"""应用内弹窗与独立更新进程的回归测试；不启动窗口、不触碰正式安装目录。"""
import subprocess
import sys
import tempfile
import time
import unittest
import json
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))

import check_guides as G  # noqa: E402
import launcher  # noqa: E402
import updater  # noqa: E402


class DialogGuardTests(unittest.TestCase):
    def test_guard_detects_native_calls_but_not_app_dialogs(self):
        self.assertEqual(G._native_dialog_hits("confirm('x'); window.alert('y')"),
                         [(1, "confirm"), (1, "alert")])
        self.assertEqual(G._native_dialog_hits("uiConfirm('x'); uiNotice('y')"), [])

    def test_app_js_has_no_native_dialog_calls(self):
        text = (SRC / "web" / "app.js").read_text(encoding="utf-8")
        self.assertEqual(G._native_dialog_hits(text), [])
        self.assertIn("function uiNotice", text)
        self.assertIn("_uiDialogQueue", text)

    def test_full_installer_fallback_is_visible_in_update_ui(self):
        text = (SRC / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("needs_full_installer", text)
        self.assertIn("/update/open_installer", text)
        self.assertIn("不用卸载", text)


class RuntimeDependencyUpdateTests(unittest.TestCase):
    def test_missing_ocr_runtime_is_reported(self):
        def find_spec(name):
            return None if name == "rapidocr" else object()
        with mock.patch.object(updater.importlib.util, "find_spec", side_effect=find_spec):
            self.assertEqual(updater.missing_runtime_components(), ["本地 OCR"])

    def test_release_check_returns_full_installer_url(self):
        fake_version = ".".join(["9", "9", "9"])
        release = {
            "tag_name": "v" + fake_version,
            "body": "测试",
            "assets": [
                {"name": f"paper-piggy-app-{fake_version}.zip", "browser_download_url": "https://example/app.zip"},
                {"name": f"paper-piggy-app-{fake_version}.zip.sha256", "browser_download_url": "https://example/app.sha"},
                {"name": f"PaperPiggy-{fake_version}-win64.exe",
                 "browser_download_url": ("https://github.com/DrinkTea905/paper-piggy/releases/download/"
                                          f"v{fake_version}/PaperPiggy-{fake_version}-win64.exe")},
            ],
        }
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(release).encode("utf-8")
        with mock.patch.object(updater, "urlopen", return_value=response), \
                mock.patch.object(updater, "missing_runtime_components", return_value=["本地 OCR"]):
            info = updater.check(tries=1)
        self.assertTrue(info["has_update"])
        self.assertTrue(info["needs_full_installer"])
        self.assertTrue(info["installer_url"].endswith(f"PaperPiggy-{fake_version}-win64.exe"))


class LauncherUpdateProcessTests(unittest.TestCase):
    def test_apply_update_uses_pythonw_no_window_and_persistent_log(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            app = home / "app"
            pyw = home / "python" / "pythonw.exe"
            data = home / "data"
            zip_path = home / "update" / "app-test.zip"
            app.mkdir(); pyw.parent.mkdir(); data.mkdir(); zip_path.parent.mkdir()
            (app / "updater.py").write_text("# test", encoding="utf-8")
            pyw.write_bytes(b"")
            zip_path.write_bytes(b"zip")
            child = mock.Mock(pid=4321)

            with (mock.patch.object(launcher.C, "APP", app),
                  mock.patch.object(launcher.C, "DATA", data),
                  mock.patch.object(launcher.subprocess, "Popen", return_value=child) as popen,
                  mock.patch.object(launcher.threading, "Thread") as thread):
                result = launcher._JsApi().apply_update(str(zip_path))

            self.assertTrue(result["ok"])
            args, kwargs = popen.call_args
            self.assertEqual(Path(args[0][0]), pyw)
            self.assertEqual(kwargs["creationflags"], launcher.C.SUBPROC_NO_WINDOW)
            self.assertTrue(kwargs["close_fds"])
            self.assertIs(kwargs["stderr"], subprocess.STDOUT)
            self.assertIsNot(kwargs["stdout"], subprocess.DEVNULL)
            self.assertTrue((home / "update" / "update.log").exists())
            thread.assert_called_once()

    def test_no_window_child_survives_parent_exit(self):
        """CREATE_NO_WINDOW 不等于绑定父进程；launcher 退出后 updater 仍能继续执行。"""
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "child-finished"
            child_code = (
                "import pathlib,time; time.sleep(0.3); "
                f"pathlib.Path({str(marker)!r}).write_text('ok', encoding='utf-8')"
            )
            parent_code = (
                "import subprocess,sys; "
                f"subprocess.Popen([sys.executable, '-c', {child_code!r}], "
                f"creationflags={launcher.C.SUBPROC_NO_WINDOW}, close_fds=True, "
                "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)"
            )
            subprocess.run([sys.executable, "-c", parent_code], check=True, timeout=10,
                           creationflags=launcher.C.SUBPROC_NO_WINDOW)
            for _ in range(30):
                if marker.exists():
                    break
                time.sleep(0.1)
            self.assertEqual(marker.read_text(encoding="utf-8"), "ok")


class UpdaterFailureFallbackTests(unittest.TestCase):
    def test_failed_apply_relaunches_old_app_and_notifies(self):
        failure = {"ok": False, "error": "测试失败"}
        argv = ["updater.py", "--apply", "bad.zip", "--pid", "123"]
        with (mock.patch.object(sys, "argv", argv),
              mock.patch.object(updater, "apply", return_value=failure.copy()),
              mock.patch.object(updater, "_relaunch", return_value=True) as relaunch,
              mock.patch.object(updater, "_notify") as notify,
              mock.patch.object(updater, "_log")):
            rc = updater.main()
        self.assertEqual(rc, 1)
        relaunch.assert_called_once_with()
        self.assertIn("旧版已重新打开", notify.call_args.args[1])

    def test_success_without_relaunch_shows_manual_restart_notice(self):
        result = {"ok": True, "from": "old", "to": "new", "relaunched": False}
        argv = ["updater.py", "--apply", "good.zip"]
        with (mock.patch.object(sys, "argv", argv),
              mock.patch.object(updater, "apply", return_value=result),
              mock.patch.object(updater, "_notify") as notify,
              mock.patch.object(updater, "_log")):
            rc = updater.main()
        self.assertEqual(rc, 0)
        notify.assert_called_once()
        self.assertIn("手动重新打开", notify.call_args.args[1])


if __name__ == "__main__":
    unittest.main()

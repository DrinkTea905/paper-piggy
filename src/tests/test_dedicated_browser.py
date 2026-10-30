# -*- coding: utf-8 -*-
"""专用浏览器的关联与进程安全契约；全部进程/网络边界均隔离。"""
import concurrent.futures
from contextlib import redirect_stdout
import io
import json
import stat
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import dedicated_browser as B
import settings as S


class DedicatedBrowserTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.exe = self.root / "Chrome" / "chrome.exe"
        self.exe.parent.mkdir()
        self.exe.touch()
        self.cfg = {
            "schema": 1,
            "executable": str(self.exe),
            "user_data_dir": str(self.data / "browser_profile"),
            "port": 49261,
        }
        Path(self.cfg["user_data_dir"]).mkdir()
        self.file = self.data / "dedicated_browser.json"
        for name, value in (
                ("CONFIG_FILE", self.file),
                ("PROFILE_DIR", Path(self.cfg["user_data_dir"])),
                ("LOCK_FILE", self.data / "dedicated_browser.lock")):
            patch = mock.patch.object(B, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        # Any accidental escape from a test's specified boundary fails closed.
        for name in ("_listener_pids", "_process", "_processes", "_version", "_cdp"):
            patch = mock.patch.object(B, name, side_effect=AssertionError("未隔离的系统访问: " + name))
            patch.start()
            self.addCleanup(patch.stop)
        self.original_legacy = B.legacy_configuration
        legacy_patch = mock.patch.object(B, "legacy_configuration", return_value=None)
        self.legacy = legacy_patch.start()
        self.addCleanup(legacy_patch.stop)

    def save_existing(self, cfg=None):
        cfg = cfg or self.cfg
        self.file.write_text(json.dumps(cfg), encoding="utf-8")
        return self.file.read_bytes()

    def process(self, cfg=None, **overrides):
        cfg = cfg or self.cfg
        row = {
            "pid": 7123,
            "executable": cfg["executable"],
            "args": [cfg["executable"],
                     "--user-data-dir=" + cfg["user_data_dir"],
                     "--remote-debugging-port=" + str(cfg["port"])],
        }
        row.update(overrides)
        return row

    def test_first_launch_saves_only_after_process_and_window_verified(self):
        def show(cfg, pid):
            self.assertFalse(self.file.exists(), "窗口核验前不应保存关联")
            self.assertEqual(7123, pid)
            return True

        with mock.patch.object(B, "_new_configuration", return_value=self.cfg), \
                mock.patch.object(B, "_verify", side_effect=[None, 7123]), \
                mock.patch.object(B, "_profile_processes", return_value=[]), \
                mock.patch.object(B, "_show_window", side_effect=show), \
                mock.patch.object(B.subprocess, "Popen") as launch:
            got = B.open_browser()
        self.assertTrue(got["ok"])
        self.assertFalse(got["reused"])
        self.assertEqual(self.cfg, json.loads(self.file.read_text(encoding="utf-8")))
        command = launch.call_args.args[0]
        self.assertIn("--remote-debugging-port=49261", command)
        self.assertIn("--user-data-dir=" + self.cfg["user_data_dir"], command)
        self.assertIn("--restore-last-session", command)
        self.assertFalse(any(arg.startswith(("http:", "https:")) for arg in command))
        self.assertEqual(B.C.SUBPROC_NO_WINDOW, launch.call_args.kwargs["creationflags"])
        launch.assert_called_once()

    def test_failed_process_start_does_not_save_or_change_configuration(self):
        with mock.patch.object(B, "_new_configuration", return_value=self.cfg), \
                mock.patch.object(B, "_verify", return_value=None), \
                mock.patch.object(B, "_profile_processes", return_value=[]), \
                mock.patch.object(B, "_show_window") as show, \
                mock.patch.object(B.subprocess, "Popen", side_effect=OSError("blocked")):
            with self.assertRaisesRegex(B.BrowserError, "启动失败"):
                B.open_browser()
        self.assertFalse(self.file.exists())
        show.assert_not_called()

    def test_launched_but_wrong_process_or_profile_is_never_saved(self):
        with mock.patch.object(B, "_new_configuration", return_value=self.cfg), \
                mock.patch.object(B, "_verify", side_effect=[None, B.BrowserError("资料目录不符")]), \
                mock.patch.object(B, "_profile_processes", return_value=[]), \
                mock.patch.object(B.time, "monotonic", side_effect=[0, 0, 1, 21]), \
                mock.patch.object(B.time, "sleep"), \
                mock.patch.object(B, "_show_window") as show, \
                mock.patch.object(B.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(B.BrowserError, "资料目录不符"):
                B.open_browser()
        self.assertFalse(self.file.exists())
        launch.assert_called_once()
        show.assert_not_called()

    def test_failed_window_display_does_not_commit_first_association(self):
        with mock.patch.object(B, "_new_configuration", return_value=self.cfg), \
                mock.patch.object(B, "_verify", return_value=7123), \
                mock.patch.object(B, "_show_window", side_effect=B.BrowserError("未找到窗口")), \
                mock.patch.object(B.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(B.BrowserError, "未找到窗口"):
                B.open_browser()
        self.assertFalse(self.file.exists())
        launch.assert_not_called()

    def test_saved_port_owned_by_other_program_fails_without_launch_or_reassignment(self):
        before = self.save_existing()
        other = self.process(executable=str(self.root / "unrelated.exe"))
        with mock.patch.object(B, "_listener_pids", return_value={7123}), \
                mock.patch.object(B, "_process", return_value=other), \
                mock.patch.object(B, "_new_configuration") as select_new, \
                mock.patch.object(B, "_show_window") as show, \
                mock.patch.object(B.subprocess, "Popen") as launch, \
                mock.patch.object(B.os, "kill") as kill:
            with self.assertRaisesRegex(B.BrowserError, "49261.*占用"):
                B.open_browser()
        self.assertEqual(before, self.file.read_bytes())
        select_new.assert_not_called()
        launch.assert_not_called()
        show.assert_not_called()
        kill.assert_not_called()

    def test_same_browser_using_another_profile_is_not_adopted(self):
        self.save_existing()
        row = self.process()
        row["args"][1] = "--user-data-dir=" + str(self.root / "private-daily-profile")
        with mock.patch.object(B, "_listener_pids", return_value={7123}), \
                mock.patch.object(B, "_process", return_value=row), \
                mock.patch.object(B.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(B.BrowserError, "其他资料目录占用"):
                B.open_browser()
        launch.assert_not_called()

    def test_listener_identity_is_rechecked_after_debugging_response(self):
        with mock.patch.object(B, "_listener_pids", side_effect=[{7123}, {8999}]), \
                mock.patch.object(B, "_process", return_value=self.process()), \
                mock.patch.object(B, "_version", return_value={}):
            with self.assertRaisesRegex(B.BrowserError, "进程发生变化"):
                B._verify(self.cfg)

    def test_existing_profile_without_expected_debug_port_is_not_started_again(self):
        before = self.save_existing()
        with mock.patch.object(B, "_verify", return_value=None), \
                mock.patch.object(B, "_profile_processes", return_value=[self.process()]), \
                mock.patch.object(B.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(B.BrowserError, "资料目录已被浏览器使用"):
                B.open_browser()
        self.assertEqual(before, self.file.read_bytes())
        launch.assert_not_called()

    def test_explicit_existing_configuration_preserves_all_three_values(self):
        old = {"schema": 1, "executable": r"D:\Chrome\chrome.exe",
               "user_data_dir": r"D:\Chrome-cnki-profile", "port": 9222,
               "source": "adopted"}
        with mock.patch.object(B, "_verify", return_value=7123) as verify, \
                mock.patch.object(B, "_validate_profile"), \
                mock.patch.object(B.subprocess, "Popen") as launch:
            got = B.adopt_configuration(old["executable"], old["user_data_dir"], old["port"])
        verify.assert_called_once_with(old)
        self.assertEqual(old, json.loads(self.file.read_text(encoding="utf-8")))
        self.assertEqual("http://127.0.0.1:9222", got["endpoint"])
        launch.assert_not_called()

    def test_development_does_not_import_formal_browser_or_read_external_endpoint(self):
        with mock.patch.object(B, "_is_development", return_value=True), \
                mock.patch.object(B, "_legacy_cdp_endpoint") as endpoint, \
                mock.patch.object(S, "load") as load_settings:
            got = self.original_legacy()
        self.assertIsNone(got)
        endpoint.assert_not_called()
        load_settings.assert_not_called()

    def test_explicit_legacy_settings_are_reused_without_searching_other_browsers(self):
        with mock.patch.object(B, "_is_development", return_value=False), \
                mock.patch.object(S, "load", return_value={"browser": self.cfg}), \
                mock.patch.object(B, "_legacy_cdp_endpoint") as endpoint:
            got = self.original_legacy()
        for key in ("executable", "user_data_dir", "port"):
            self.assertEqual(self.cfg[key], got[key])
        self.assertEqual("adopted", got["source"])
        endpoint.assert_not_called()
        self.assertFalse(self.file.exists())

    def test_formal_legacy_endpoint_requires_matching_running_process(self):
        old = {"schema": 1, "executable": r"D:\Chrome\chrome.exe",
               "user_data_dir": r"D:\Chrome-cnki-profile", "port": 9222}
        with mock.patch.object(B, "_is_development", return_value=False), \
                mock.patch.object(S, "load", return_value={}), \
                mock.patch.object(B, "_legacy_cdp_endpoint", return_value="http://127.0.0.1:9222"), \
                mock.patch.object(B, "_listener_pids", return_value={7123}), \
                mock.patch.object(B, "_process", return_value=self.process(old)), \
                mock.patch.object(B, "_default_profiles", return_value=set()), \
                mock.patch.object(B, "_validate_profile"), \
                mock.patch.object(B, "_verify", return_value=7123), \
                mock.patch.object(B.subprocess, "Popen") as launch, \
                mock.patch.object(B, "_show_window") as show:
            got = self.original_legacy()
        for key in ("executable", "user_data_dir", "port"):
            self.assertEqual(old[key], got[key])
        self.assertEqual("adopted", got["source"])
        self.assertFalse(self.file.exists())
        launch.assert_not_called()
        show.assert_not_called()

    def test_unconfirmed_legacy_endpoint_never_falls_back_to_new_profile_or_port(self):
        with mock.patch.object(B, "legacy_configuration", side_effect=self.original_legacy), \
                mock.patch.object(B, "_is_development", return_value=False), \
                mock.patch.object(S, "load", return_value={}), \
                mock.patch.object(B, "_legacy_cdp_endpoint", return_value="http://127.0.0.1:9222"), \
                mock.patch.object(B, "_listener_pids", return_value=set()), \
                mock.patch.object(B, "_new_configuration") as select_new, \
                mock.patch.object(B.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(B.BrowserError, "9222.*未更换端口"):
                B.open_browser()
        select_new.assert_not_called()
        launch.assert_not_called()
        self.assertFalse(self.file.exists())

    def test_browser_discovery_prefers_chrome_then_falls_back_to_edge(self):
        import winreg
        chrome = self.root / "Google" / "Chrome" / "Application" / "chrome.exe"
        edge = self.root / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        for exe in (chrome, edge):
            exe.parent.mkdir(parents=True)
            exe.touch()
        with mock.patch.dict(B.os.environ, {"LOCALAPPDATA": str(self.root)}, clear=True), \
                mock.patch.object(winreg, "OpenKey", side_effect=OSError("not registered")), \
                mock.patch.object(B.shutil, "which", return_value=None):
            self.assertEqual(str(chrome.resolve()), B._find_browser())
            with mock.patch.object(Path, "is_file", autospec=True,
                                   side_effect=lambda p: p == edge):
                self.assertEqual(str(edge.resolve()), B._find_browser())

    def test_saved_configuration_wins_over_a_different_candidate(self):
        before = self.save_existing()
        other = dict(self.cfg, port=49333)
        with mock.patch.object(B, "_verify", return_value=7123) as verify, \
                mock.patch.object(B, "_show_window", return_value=True), \
                mock.patch.object(B.subprocess, "Popen") as launch:
            got = B.open_browser(candidate=other)
        verify.assert_called_once_with(self.cfg)
        self.assertEqual(49261, got["port"])
        self.assertEqual(before, self.file.read_bytes())
        launch.assert_not_called()

    def test_concurrent_first_clicks_start_once_and_then_reuse_without_navigation(self):
        running = threading.Event()
        barrier = threading.Barrier(6)

        def verify(cfg):
            return 7123 if running.is_set() else None

        def launch(*args, **kwargs):
            running.set()
            return mock.Mock()

        def click():
            barrier.wait(timeout=5)
            return B.open_browser()

        with mock.patch.object(B, "_new_configuration", return_value=self.cfg) as select_new, \
                mock.patch.object(B, "_verify", side_effect=verify), \
                mock.patch.object(B, "_profile_processes", return_value=[]), \
                mock.patch.object(B, "_show_window", return_value=True) as show, \
                mock.patch.object(B.subprocess, "Popen", side_effect=launch) as start:
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                results = list(pool.map(lambda _: click(), range(6)))
        self.assertTrue(all(r["ok"] for r in results))
        self.assertEqual(1, sum(not r["reused"] for r in results))
        self.assertEqual(6, show.call_count)
        select_new.assert_called_once()
        start.assert_called_once()
        B._cdp.assert_not_called()

    def test_read_only_discovery_reports_actual_port_and_does_not_focus(self):
        before = self.save_existing()
        with mock.patch.object(B, "_verify", return_value=7123), \
                mock.patch.object(B, "_show_window") as show, \
                mock.patch.object(B.subprocess, "Popen") as launch:
            got = B.discovery()
        self.assertEqual("http://127.0.0.1:49261", got["endpoint"])
        self.assertTrue(got["running"])
        self.assertEqual(before, self.file.read_bytes())
        show.assert_not_called()
        launch.assert_not_called()

    def test_conflicting_discovery_withholds_connectable_endpoint(self):
        before = self.save_existing()
        with mock.patch.object(B, "_verify", side_effect=B.BrowserError("端口被占用")), \
                mock.patch.object(B, "_show_window") as show, \
                mock.patch.object(B.subprocess, "Popen") as launch:
            got = B.discovery()
        self.assertEqual("", got["endpoint"])
        self.assertIn("占用", got["error"])
        self.assertFalse(got["running"])
        self.assertEqual(before, self.file.read_bytes())
        show.assert_not_called()
        launch.assert_not_called()

    def test_background_reuse_does_not_focus_or_create_a_window(self):
        self.save_existing()
        with mock.patch.object(B, "_verify", return_value=7123), \
                mock.patch.object(B, "_show_window") as show, \
                mock.patch.object(B.subprocess, "Popen") as launch:
            got = B.open_browser(foreground=False)
        self.assertTrue(got["reused"])
        self.assertFalse(got["activated"])
        show.assert_not_called()
        launch.assert_not_called()

    def test_background_never_starts_a_stopped_browser(self):
        self.save_existing()
        with mock.patch.object(B, "_verify", return_value=None), \
                mock.patch.object(B, "_profile_processes", return_value=[]), \
                mock.patch.object(B, "_show_window") as show, \
                mock.patch.object(B.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(B.BrowserError, "后台任务不会弹窗"):
                B.open_browser(foreground=False)
        show.assert_not_called()
        launch.assert_not_called()

    def test_missing_saved_profile_is_not_silently_recreated(self):
        cfg = dict(self.cfg, user_data_dir=str(self.root / "missing-saved-logins"))
        before = self.save_existing(cfg)
        with mock.patch.object(B, "_verify") as verify, \
                mock.patch.object(B, "_new_configuration") as select_new, \
                mock.patch.object(B.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(B.BrowserError, "资料目录不存在.*未重建"):
                B.open_browser()
        self.assertEqual(before, self.file.read_bytes())
        self.assertFalse(Path(cfg["user_data_dir"]).exists())
        verify.assert_not_called()
        select_new.assert_not_called()
        launch.assert_not_called()

    def test_missing_adopted_profile_is_not_created_or_saved(self):
        cfg = dict(self.cfg, user_data_dir=str(self.root / "missing-original-profile"))
        with mock.patch.object(B, "_verify") as verify, \
                mock.patch.object(B.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(B.BrowserError, "资料目录不存在"):
                B.open_browser(candidate=cfg)
        self.assertFalse(self.file.exists())
        self.assertFalse(Path(cfg["user_data_dir"]).exists())
        verify.assert_not_called()
        launch.assert_not_called()

    def test_genuine_first_launch_may_create_a_new_profile(self):
        profile = self.root / "genuine-first-profile"
        cfg = dict(self.cfg, user_data_dir=str(profile), source="created")
        self.assertFalse(profile.exists())

        def launch(*args, **kwargs):
            profile.mkdir()

        with mock.patch.object(B, "_new_configuration", return_value=cfg), \
                mock.patch.object(B, "_verify", side_effect=[None, 7123]), \
                mock.patch.object(B, "_profile_processes", return_value=[]), \
                mock.patch.object(B, "_show_window", return_value=True), \
                mock.patch.object(B.subprocess, "Popen", side_effect=launch) as start:
            got = B.open_browser()
        self.assertTrue(got["ok"])
        self.assertEqual(str(profile), got["user_data_dir"])
        start.assert_called_once()
        self.assertTrue(self.file.exists())

    def test_profile_path_that_is_a_file_is_never_overwritten(self):
        profile = self.root / "valuable-document"
        profile.write_text("keep", encoding="utf-8")
        cfg = dict(self.cfg, user_data_dir=str(profile))
        self.save_existing(cfg)
        with mock.patch.object(B.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(B.BrowserError, "不是目录"):
                B.open_browser()
        self.assertEqual("keep", profile.read_text(encoding="utf-8"))
        launch.assert_not_called()

    def test_symbolic_link_or_junction_profile_is_not_followed(self):
        for mode, attributes in ((stat.S_IFLNK, 0), (stat.S_IFDIR, 0x400)):
            with self.subTest(mode=mode, attributes=attributes), \
                    mock.patch.object(Path, "lstat", return_value=SimpleNamespace(
                        st_mode=mode, st_file_attributes=attributes)):
                with self.assertRaisesRegex(B.BrowserError, "符号链接、联接"):
                    B._validate_profile(self.cfg, required=True)

    def test_daily_profile_or_its_child_is_not_used_for_dedicated_browser(self):
        daily = B._normal_path(self.root / "daily-profile")
        with mock.patch.object(B, "_default_profiles", return_value={daily}):
            for profile in (daily, str(Path(daily) / "Default")):
                with self.subTest(profile=profile):
                    with self.assertRaisesRegex(B.BrowserError, "日常 Chrome 或 Edge 资料"):
                        B._validate_profile(dict(self.cfg, user_data_dir=profile))

    def test_ordinary_backup_allows_association_but_never_login_files(self):
        import backup as BK
        self.assertTrue(BK._archive_name_allowed("data/dedicated_browser.json"))
        for include_index in (False, True):
            self.assertFalse(BK._archive_name_allowed(
                "data/browser_profile/Default/Cookies", include_index=include_index))
            self.assertFalse(BK._archive_name_allowed(
                "data/browser_profile/Default/Login Data", include_index=include_index))

    def test_mcp_resource_and_cli_share_actual_endpoint_without_starting_services(self):
        import mcp_server as MCP
        before = self.save_existing()
        output = io.StringIO()
        with mock.patch.object(B, "_verify", return_value=7123), \
                mock.patch.object(B, "_show_window") as show, \
                mock.patch.object(B.subprocess, "Popen") as launch, \
                mock.patch.object(MCP, "ensure_up") as ensure_up:
            resource, mime = MCP.read_resource("localkb://browser")
            with redirect_stdout(output):
                code = B.main(["--config"])
        cli = json.loads(output.getvalue())
        mcp = json.loads(resource)
        self.assertEqual(0, code)
        self.assertEqual("application/json", mime)
        self.assertEqual(mcp, cli)
        self.assertEqual("http://127.0.0.1:49261", cli["endpoint"])
        self.assertEqual(before, self.file.read_bytes())
        ensure_up.assert_not_called()
        show.assert_not_called()
        launch.assert_not_called()

    def test_open_http_route_rejects_cross_site_and_form_requests_before_opening(self):
        import server
        from starlette.requests import Request

        def request(extra):
            headers = {"host": "127.0.0.1:8771", **extra}
            return Request({"type": "http", "method": "POST", "scheme": "http",
                            "server": ("127.0.0.1", 8771), "path": "/browser/open",
                            "root_path": "", "query_string": b"",
                            "headers": [(k.encode(), v.encode()) for k, v in headers.items()]})

        with mock.patch.object(B, "open_browser", return_value={"ok": True}) as open_browser:
            for headers in (
                    {"origin": "https://unrelated.example", "content-type": "application/json"},
                    {"sec-fetch-site": "cross-site", "content-type": "application/json"},
                    {"origin": "http://127.0.0.1:8771",
                     "content-type": "application/x-www-form-urlencoded"}):
                with self.subTest(headers=headers):
                    denied = server.browser_open(request(headers))
                    self.assertEqual(403, denied.status_code)
            open_browser.assert_not_called()
            accepted = server.browser_open(request({
                "origin": "http://127.0.0.1:8771", "content-type": "application/json"}))
            self.assertTrue(accepted["ok"])
            open_browser.assert_called_once_with(foreground=True)

    def test_settings_reset_preserves_browser_association_and_login_files(self):
        before = self.save_existing()
        login = Path(self.cfg["user_data_dir"]) / "Default" / "Cookies"
        login.parent.mkdir(parents=True)
        login.write_bytes(b"test-only-login-sentinel")
        settings_file = self.data / "settings.json"
        settings_file.write_text('{"backend":"api"}', encoding="utf-8")
        with mock.patch.object(S, "SETTINGS_FILE", settings_file), \
                mock.patch.dict(S._CACHE, {"data": None, "mtime": -1}):
            result = S.reset()
        self.assertEqual("local", result["backend"])
        self.assertEqual(before, self.file.read_bytes())
        self.assertEqual(b"test-only-login-sentinel", login.read_bytes())


if __name__ == "__main__":
    unittest.main()

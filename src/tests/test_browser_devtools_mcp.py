# -*- coding: utf-8 -*-
"""浏览器工具桥只消费核验后的动态连接，不拉起或激活浏览器。"""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import browser_devtools_mcp as M


class BrowserDevtoolsBridgeTests(unittest.TestCase):
    def state(self, **patch):
        return {"configured": True, "running": True, "endpoint": "http://127.0.0.1:49291", **patch}

    def test_uses_dynamic_endpoint_replaces_both_old_spellings_and_does_not_focus(self):
        with mock.patch.object(M.DB, "discovery", return_value=self.state()), \
                mock.patch.object(M.DB, "open_browser") as open_browser, \
                mock.patch.object(M.DB, "_show_window") as focus:
            got = M.build_command(["node.exe", "existing.js", "--browserUrl", "http://127.0.0.1:9222",
                                   "--browser-url=http://127.0.0.1:8111", "--telemetry=false"])
        self.assertEqual(["node.exe", "existing.js", "--telemetry=false",
                          "--browserUrl", "http://127.0.0.1:49291"], got)
        open_browser.assert_not_called()
        focus.assert_not_called()

    def test_unconfigured_stopped_or_conflicting_state_never_executes(self):
        for state in (self.state(configured=False), self.state(running=False),
                      self.state(endpoint=""), self.state(error="已保存端口被其他程序占用")):
            with self.subTest(state=state), \
                    mock.patch.object(M.DB, "discovery", return_value=state), \
                    mock.patch.object(M.subprocess, "Popen") as execute, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(1, M.main(["--", "node.exe", "existing.js"]))
                execute.assert_not_called()

    def test_browser_start_or_alternative_connection_options_are_rejected(self):
        for option in ("--executablePath=chrome.exe", "--isolated", "--autoConnect",
                       "--user-data-dir=private", "--headless", "--channel=stable", "--wsEndpoint=ws://localhost"):
            with self.subTest(option=option), self.assertRaises(M.BridgeError):
                M.build_command(["node.exe", "existing.js", option], self.state())

    def test_nonlocal_or_malformed_discovered_endpoint_is_rejected(self):
        for endpoint in ("http://example.com:9222", "https://127.0.0.1:9222", "http://127.0.0.1:9222/path",
                         "http://user@127.0.0.1:9222", "http://127.0.0.1:0"):
            with self.subTest(endpoint=endpoint), self.assertRaises(M.BridgeError):
                M.build_command(["node.exe", "existing.js"], self.state(endpoint=endpoint))

    def test_windows_npx_uses_node_and_local_cli_with_no_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            node = root / "node.exe"
            npx = root / "npx.cmd"
            cli = root / "node_modules" / "npm" / "bin" / "npx-cli.js"
            cli.parent.mkdir(parents=True)
            for path in (node, npx, cli):
                path.touch()
            with mock.patch.object(M.sys, "platform", "win32"), \
                    mock.patch.object(M.shutil, "which", return_value=str(npx)):
                command = M.build_command(["npx", "chrome-devtools-mcp"], self.state())
            self.assertEqual([str(node), str(cli), "--no-install", "chrome-devtools-mcp",
                              "--browserUrl", self.state()["endpoint"]], command)

    def test_npx_install_options_missing_local_cli_and_command_scripts_are_rejected(self):
        for option in ("--yes", "-y", "--yes=true", "--package=chrome-devtools-mcp", "--no-install=false"):
            with self.subTest(option=option), self.assertRaises(M.BridgeError):
                M.build_command(["npx", option, "chrome-devtools-mcp"], self.state())
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "npx.cmd"
            fake.touch()
            with mock.patch.object(M.sys, "platform", "win32"), \
                    mock.patch.object(M.shutil, "which", return_value=str(fake)), self.assertRaises(M.BridgeError):
                M.build_command(["npx", "chrome-devtools-mcp"], self.state())
        with mock.patch.object(M.sys, "platform", "win32"), self.assertRaises(M.BridgeError):
            M.build_command(["custom.cmd", "tool"], self.state())

    def test_stdio_is_inherited_shell_is_disabled_and_exit_code_is_forwarded(self):
        child = mock.Mock()
        child.wait.return_value = 17
        with mock.patch.object(M.DB, "discovery", return_value=self.state()), \
                mock.patch.object(M.subprocess, "Popen", return_value=child) as execute, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            result = M.main(["--", "node.exe", "existing.js", "--browserUrl=http://127.0.0.1:9222"])
        self.assertEqual(17, result)
        self.assertEqual("", output.getvalue())
        self.assertFalse(execute.call_args.kwargs["shell"])
        self.assertEqual(M.DB.C.SUBPROC_NO_WINDOW, execute.call_args.kwargs["creationflags"])
        for name in ("stdin", "stdout", "stderr"):
            self.assertNotIn(name, execute.call_args.kwargs)

    def test_missing_separator_or_browser_url_value_never_executes(self):
        with mock.patch.object(M.subprocess, "Popen") as execute, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(1, M.main(["node.exe", "existing.js"]))
            execute.assert_not_called()
        with self.assertRaises(M.BridgeError):
            M.build_command(["node.exe", "existing.js", "--browserUrl", "--verbose"], self.state())

    def test_default_command_preserves_existing_installed_path_and_pinned_version_without_private_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "custom-node" / "npx.cmd"
            path.parent.mkdir()
            path.touch()
            configured = {"command": str(path), "args": ["-y", "chrome-devtools-mcp@1.8.0",
                          "--browserUrl", "http://127.0.0.1:9222", "--usageStatistics=false"]}
            with mock.patch.object(M, "_existing_tool_configuration", return_value=configured), \
                    mock.patch.object(M.shutil, "which") as search, \
                    mock.patch.object(M.subprocess, "Popen") as execute, \
                    contextlib.redirect_stdout(io.StringIO()) as output, \
                    contextlib.redirect_stderr(io.StringIO()) as errors:
                command = M.default_command()
            self.assertEqual([str(path), "--no-install", "chrome-devtools-mcp@1.8.0",
                              "--usageStatistics=false"], command)
            self.assertEqual("", output.getvalue() + errors.getvalue())
            search.assert_not_called()
            execute.assert_not_called()

    def test_default_command_falls_back_to_path_without_changing_pinned_version(self):
        existing = {"command": "missing-npx.cmd", "args": ["-y", "chrome-devtools-mcp@1.8.0"]}
        with mock.patch.object(M, "_existing_tool_configuration", return_value=existing), \
                mock.patch.object(M.shutil, "which", return_value="installed-npx.cmd"):
            command = M.default_command()
        self.assertEqual(["installed-npx.cmd", "--no-install", "chrome-devtools-mcp@1.8.0",
                          "--usageStatistics=false"], command)

    def test_default_command_fails_clearly_without_installed_npx_and_never_installs(self):
        with mock.patch.object(M, "_existing_tool_configuration", return_value=None), \
                mock.patch.object(M.shutil, "which", return_value=None), \
                mock.patch.object(M.os, "environ", {}), \
                mock.patch.object(M.subprocess, "Popen") as execute, \
                self.assertRaisesRegex(M.BridgeError, "未找到已安装.*npx"):
            M.default_command()
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()

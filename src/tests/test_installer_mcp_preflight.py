from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "installer"
ISS = INSTALLER / "paperpiggy.iss"
PREFLIGHT = INSTALLER / "mcp_preflight.iss"


@unittest.skipUnless(sys.platform == "win32", "安装器预检只适用于 Windows")
class InstallerMcpPreflightTests(unittest.TestCase):
    def test_installer_blocks_instead_of_killing(self) -> None:
        iss = ISS.read_text(encoding="utf-8")
        preflight = PREFLIGHT.read_text(encoding="utf-8")
        self.assertIn("CloseApplications=no", iss)
        self.assertIn("RestartApplications=no", iss)
        self.assertIn('#include "mcp_preflight.iss"', iss)
        self.assertIn("function NextButtonClick(CurPageID: Integer): Boolean;", iss)
        self.assertIn("function PrepareToInstall(var NeedsRestart: Boolean): String;", iss)
        self.assertIsNone(
            re.search(r"(?m)^\s*#\d", iss),
            "以 #13/#10 开头的续行会被 Inno 预处理器误判为指令",
        )
        self.assertIn("python.exe", preflight)
        self.assertIn("pythonw.exe", preflight)
        self.assertIn("app\\mcp_server.py", preflight)
        combined = (iss + "\n" + preflight).lower()
        for forbidden in ("taskkill", "stop-process", "terminateprocess"):
            self.assertNotIn(forbidden, combined)

    def test_wmi_preflight_compiles_and_runs_without_writing_install_dir(self) -> None:
        candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Inno Setup 6/ISCC.exe",
            Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
            Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
        ]
        iscc = next((path for path in candidates if path.exists()), None)
        if iscc is None:
            self.skipTest("未安装 Inno Setup，跳过运行时编译验证")

        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            fake_install = temp / "模拟 安装目录"
            python_dir = fake_install / "python"
            python_dir.mkdir(parents=True)
            # 只用于迫使扫描器进入 WMI 分支；不会执行，也不会写正式安装目录。
            (python_dir / "python.exe").write_bytes(b"")
            result_file = temp / "scan-result.txt"
            escaped_include = str(PREFLIGHT).replace("\\", "\\\\")
            escaped_root = str(fake_install).replace("'", "''")
            escaped_result = str(result_file).replace("'", "''")
            harness = temp / "preflight-harness.iss"
            harness.write_text(
                "\n".join(
                    [
                        "[Setup]",
                        "AppName=PaperPiggy Preflight Test",
                        "AppVersion=0.0.0",
                        f"DefaultDirName={{{{tmp}}}}\\PaperPiggyPreflightHarness",
                        "PrivilegesRequired=lowest",
                        "Uninstallable=no",
                        f"OutputDir={temp}",
                        "OutputBaseFilename=preflight-harness",
                        "[Code]",
                        f'#include "{escaped_include}"',
                        "function InitializeSetup: Boolean;",
                        "var Details, ErrorText: String; ScanResult: Integer;",
                        "begin",
                        f"  ScanResult := ScanPaperPiggyProcesses('{escaped_root}', Details, ErrorText);",
                        f"  SaveStringToFile('{escaped_result}', IntToStr(ScanResult) + '|' + Details + '|' + ErrorText, False);",
                        "  Result := False;",
                        "end;",
                    ]
                ),
                encoding="utf-8-sig",
            )
            compile_result = subprocess.run(
                [str(iscc), "/Q", str(harness)],
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=30,
                creationflags=0x08000000,
            )
            self.assertEqual(
                compile_result.returncode,
                0,
                compile_result.stdout + compile_result.stderr,
            )
            run_result = subprocess.run(
                [str(temp / "preflight-harness.exe"), "/VERYSILENT", "/NORESTART"],
                timeout=20,
                creationflags=0x08000000,
            )
            # InitializeSetup intentionally returns False; the scan result is the assertion target.
            self.assertNotEqual(run_result.returncode, 0)
            self.assertTrue(result_file.exists())
            result = result_file.read_text(encoding="utf-8-sig")
            self.assertTrue(result.startswith("0|"), result)


if __name__ == "__main__":
    unittest.main()

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SRC = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'win32', 'Windows 原生启动保护')
class BackupStartupGateTests(unittest.TestCase):
    def _runtime(self):
        exe = Path(sys.executable).with_name('pythonw.exe')
        if not exe.is_file():
            self.skipTest('需要配套 pythonw.exe')
        return str(exe)

    def test_config_import_does_not_recreate_roots_during_restore(self):
        with tempfile.TemporaryDirectory() as td:
            area = Path(td)
            data = area / 'data'
            jobs = data / 'backup_jobs'
            jobs.mkdir(parents=True)
            (jobs / 'restore-gate.json').write_text('{}', encoding='utf-8')
            output = area / 'result.json'
            code = ('import sys,json; from pathlib import Path; '
                    'sys.path.insert(0,sys.argv[1]); import config; '
                    'Path(sys.argv[2]).write_text(json.dumps(str(config.DATA)),encoding="utf-8")')
            result = subprocess.run([self._runtime(), '-c', code, str(SRC), str(output)],
                env=dict(os.environ, LOCALKB_DATA=str(data)), creationflags=0x08000000,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
            self.assertEqual(0, result.returncode)
            self.assertEqual(str(data), json.loads(output.read_text(encoding='utf-8')))
            self.assertEqual(['backup_jobs'], sorted(p.name for p in data.iterdir()))

    def test_mcp_refuses_start_without_writing_restored_data(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / 'data'
            jobs = data / 'backup_jobs'
            jobs.mkdir(parents=True)
            (jobs / 'restore-gate.json').write_text('{}', encoding='utf-8')
            result = subprocess.run([self._runtime(), str(SRC / 'mcp_server.py')],
                env=dict(os.environ, LOCALKB_DATA=str(data)), creationflags=0x08000000,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
            self.assertEqual(0, result.returncode)
            self.assertEqual(['backup_jobs'], sorted(p.name for p in data.iterdir()))
            self.assertEqual(['restore-gate.json'], sorted(p.name for p in jobs.iterdir()))


if __name__ == '__main__':
    unittest.main()

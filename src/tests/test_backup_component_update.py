from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup_component_update as U
import backup_service as S


class ComponentUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data, self.instance = self.root / "data", self.root / "protected" / ("a" * 20)
        self.data.mkdir(); self.instance.mkdir(parents=True)
        self.sid = "S-1-5-21-1-2-3-1001"
        self.helper, self.envelope = self.root / "helper.exe", self.root / "envelope.json"
        self.helper.write_bytes(b"reviewed helper fixture")
        self.envelope.write_bytes(b"signed fixture")
        self.manifest = {"component_version": 2, "helper_size": self.helper.stat().st_size,
            "helper_sha256": S._sha(self.helper), "build_id": "c" * 64,
            "source_hashes": {"backup_service.py": "d" * 64}, "runtime_hashes": {"pythonw.exe": "e" * 64}}
        self.old_record = {"payload_id": "b" * 64, "manifest_sha256": "0" * 64}
        self.old_manifest = {"component_version": 1, "build_id": "b" * 64}
        self.channel = {"task_name": "PaperPiggyBackupUpdate-" + self.instance.name}
        self.request = {"schema": 1, "request_id": "f" * 32, "envelope_path": str(self.envelope),
            "helper_path": str(self.helper), "helper_sha256": self.manifest["helper_sha256"],
            "target_version": 2, "created_at": 1}
        self.folder, self.task = Mock(), Mock()
        self.folder.invoke.return_value = self.task
        def invoke(name, *args, flags=1):
            if name == "Xml": return "<reviewed-old-task/>"
            if name == "Enabled": return True
            raise AssertionError(name)
        self.task.invoke.side_effect = invoke
        self.patch(S, "_instance", return_value=(self.data, self.sid, self.instance))
        self.patch(S, "current_sid", return_value=self.sid)
        self.patch(S, "_check_protected_directory")
        self.patch(S, "restricted_user", side_effect=lambda: nullcontext())
        self.patch(S, "_atomic", side_effect=self.atomic)
        self.patch(S, "_protected_bytes", side_effect=lambda p, b: Path(p).write_bytes(b))
        self.patch(S, "_scheduler", side_effect=lambda: nullcontext(self.folder))
        self.patch(U, "_verify_envelope", return_value=self.manifest)
        self.patch(U, "_secure_directory", side_effect=lambda p: Path(p).mkdir(exist_ok=True))

    def patch(self, target, name, **kwargs):
        p = patch.object(target, name, **kwargs)
        self.addCleanup(p.stop)
        return p.start()

    def atomic(self, path, value, **kwargs):
        Path(path).write_text(json.dumps(value), encoding="utf-8")

    def request_update(self, **kwargs):
        with patch.object(U, "_channel", return_value=(self.data, self.sid, self.instance, self.channel)), \
                patch.object(U, "_current_manifest", return_value=(self.old_record, self.old_manifest)), \
                patch.object(U, "channel_status", return_value={"auto_update": {"state": "idle", "pending": False}}), \
                patch.object(S, "_system_dir", return_value=self.root), \
                patch.object(U.subprocess, "run", return_value=Mock(returncode=0)) as run:
            result = U.request_update(self.data, self.envelope, self.helper, **kwargs)
        return result, run

    def test_normal_request_only_queues_and_wakes_fixed_task_without_uac(self):
        result, run = self.request_update()
        self.assertTrue(result["pending"])
        request = json.loads((self.data / U.REQUEST).read_text())
        self.assertEqual(request["helper_sha256"], self.manifest["helper_sha256"])
        self.assertEqual(run.call_args.args[0][1:], ["/Run", "/TN", self.channel["task_name"]])
        self.assertNotIn("runas", str(run.call_args).lower())
        self.assertEqual(run.call_args.kwargs["creationflags"], S.NO_WINDOW)

    def test_request_rejects_changed_bytes_before_any_process(self):
        self.helper.write_bytes(b"changed")
        with self.assertRaisesRegex(S.ServiceError, "不一致"), patch.object(U.subprocess, "run") as run:
            self.request_update()
        run.assert_not_called()
        self.assertIn("不一致", json.loads((self.data / U.CLIENT_ERROR).read_text())["error"])

    def test_client_validation_failure_is_persisted_and_poll_is_idempotent(self):
        self.helper.write_bytes(b"changed")
        with self.assertRaises(S.ServiceError): self.request_update()
        with patch.object(U, "_channel", return_value=(self.data, self.sid, self.instance, self.channel)), \
                patch.object(U, "_verify_envelope") as verify, patch.object(U.subprocess, "run") as execute:
            result = U.request_update(self.data, self.envelope, self.helper)
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "failed")
        verify.assert_not_called(); execute.assert_not_called()

    def test_request_requires_migration_if_no_channel(self):
        with patch.object(U, "_channel", return_value=(self.data, self.sid, self.instance, None)), \
                self.assertRaisesRegex(S.ServiceError, "一次管理员授权"):
            U.request_update(self.data)

    def test_failed_same_package_is_not_requeued_without_explicit_retry(self):
        failed = {"state": "failed", "pending": False, "helper_sha256": self.manifest["helper_sha256"], "error": "bad package"}
        with patch.object(U, "_channel", return_value=(self.data, self.sid, self.instance, self.channel)), \
                patch.object(U, "_current_manifest", return_value=(self.old_record, self.old_manifest)), \
                patch.object(U, "channel_status", return_value={"auto_update": failed}), \
                patch.object(U.subprocess, "run") as run:
            result = U.request_update(self.data, self.envelope, self.helper)
        self.assertFalse(result["ok"])
        run.assert_not_called()

    def test_request_rejects_replay_and_same_version_different_contents(self):
        for version in (0, 1):
            self.manifest["component_version"] = version
            with self.subTest(version=version), self.assertRaises(S.ServiceError):
                self.request_update(retry=True)

    def _apply(self, *, exitcode=0, active=0, installed_mismatch=False):
        installed = {"component_version": 2, "build_id": "c" * 64,
            "files": [{"path": "code/backup_service.py", "sha256": "d" * 64},
                      {"path": "runtime/pythonw.exe", "sha256": "e" * 64}]}
        if installed_mismatch: installed["build_id"] = "0" * 64
        current_calls = [(self.old_record, self.old_manifest)]
        def current(instance):
            if current_calls: return current_calls.pop()
            status = json.loads((self.instance / U.STATE).read_text())
            return {"payload_id": status["generation"]}, installed
        with patch.object(U, "_current_manifest", side_effect=current), \
                patch.object(U, "_active", return_value=active), \
                patch.object(S, "_verified_manifest", return_value=installed), \
                patch.object(U, "_run_helper", return_value=exitcode) as run:
            result = U._apply(self.data, self.instance, self.request)
        return result, run

    def test_staged_bytes_verified_then_only_fixed_helper_parameters_execute(self):
        result, run = self._apply()
        self.assertEqual(result["state"], "success")
        command = run.call_args.args[0]
        self.assertTrue(Path(command[0]).is_relative_to(self.instance / "component-update-staging"))
        self.assertEqual(Path(command[0]).read_bytes(), b"reviewed helper fixture")
        self.assertEqual(command[1:5], ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/SP-", "/NORESTART"])
        self.assertIn("/DATA=" + str(self.data), command)
        self.assertNotIn("runas", str(run.call_args).lower())

    def test_installer_failure_restores_old_task_and_current(self):
        with self.assertRaisesRegex(S.ServiceError, "保留原版本"):
            self._apply(exitcode=1)
        self.assertEqual(json.loads((self.instance / "current.json").read_text()), self.old_record)
        self.folder.invoke.assert_any_call("RegisterTask", "PaperPiggyBackup-" + self.instance.name,
            "<reviewed-old-task/>", 22, self.sid, None, 3,
            f"O:BAG:BAD:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGX;;;{self.sid})")

    def test_postinstall_mismatch_rolls_back_original(self):
        with self.assertRaisesRegex(S.ServiceError, "恢复原版本"):
            self._apply(installed_mismatch=True)
        self.assertEqual(json.loads((self.instance / "current.json").read_text()), self.old_record)

    def test_active_backup_defers_without_disabling_or_installing(self):
        result, run = self._apply(active=1)
        self.assertEqual(result["state"], "deferred")
        run.assert_not_called()
        self.task.invoke.assert_not_called()

    def test_source_changed_after_queue_is_rejected_before_execution(self):
        self.helper.write_bytes(b"mutated")
        with self.assertRaisesRegex(S.ServiceError, "来源"), patch.object(U.subprocess, "run") as run:
            self._apply()
        run.assert_not_called()

    def test_protected_staging_rechecked_before_execution(self):
        def write(path, raw):
            Path(path).write_bytes(b"tampered" if Path(path).suffix == ".exe" else raw)
        with patch.object(S, "_protected_bytes", side_effect=write), \
                self.assertRaisesRegex(S.ServiceError, "暂存"), patch.object(U.subprocess, "run") as run:
            self._apply()
        run.assert_not_called()

    def test_no_updates_execute_when_component_lock_is_busy(self):
        path = self.data / U.REQUEST; path.parent.mkdir()
        path.write_text(json.dumps(self.request))
        channel = {"manifest_path": str(Path(U.__file__).absolute().parent.parent / "payload.json")}
        entry = {"payload_id": Path(channel["manifest_path"]).parent.name}
        with patch.object(U, "_channel", return_value=(self.data, self.sid, self.instance, channel)), \
                patch.object(S, "component_guard", return_value=nullcontext(False)), \
                patch.object(U, "_apply") as apply:
            self.assertEqual(U.run_update(self.data, entry), 0)
        apply.assert_not_called()
        self.assertFalse((self.instance / U.STATE).exists())

    def test_state_write_failure_after_disable_restores_original_task(self):
        original = U._save_state
        def fail(instance, request, state, **kw):
            if state == "updating": raise OSError("injected state write failure")
            return original(instance, request, state, **kw)
        with patch.object(U, "_save_state", side_effect=fail), self.assertRaisesRegex(OSError, "injected"):
            self._apply()
        self.assertEqual(json.loads((self.instance / "current.json").read_text()), self.old_record)
        self.assertEqual(json.loads((self.instance / U.TRANSACTION).read_text())["phase"], "rolled_back")

    def test_prepared_transaction_recovers_after_process_death_without_request(self):
        transaction = {"schema": 1, "instance": self.instance.name, "phase": "prepared",
                       "old_record": self.old_record, "old_xml": "<original-enabled-task/>"}
        self.atomic(self.instance / U.TRANSACTION, transaction)
        channel = {"manifest_path": str(Path(U.__file__).absolute().parent.parent / "payload.json")}
        entry = {"payload_id": Path(channel["manifest_path"]).parent.name}
        with patch.object(U, "_channel", return_value=(self.data, self.sid, self.instance, channel)), \
                patch.object(S, "component_guard", return_value=nullcontext(True)), \
                patch.object(U, "_active", return_value=0), patch.object(U, "_apply") as apply:
            self.assertEqual(U.run_update(self.data, entry), 0)
        apply.assert_not_called()
        self.assertEqual(json.loads((self.instance / "current.json").read_text()), self.old_record)
        self.assertEqual(json.loads((self.instance / U.TRANSACTION).read_text())["phase"], "rolled_back")

    def test_committed_transaction_does_not_roll_back_new_generation(self):
        self.atomic(self.instance / U.TRANSACTION, {"phase": "committed"})
        self.assertTrue(U._recover_transaction(self.instance))
        self.folder.invoke.assert_not_called()

    def test_unknown_protected_transaction_phase_fails_closed(self):
        self.atomic(self.instance / U.TRANSACTION, {"phase": "unknown"})
        with self.assertRaisesRegex(S.ServiceError, "阶段无效"):
            U._recover_transaction(self.instance)
        self.folder.invoke.assert_not_called()

    def test_failed_rollback_registration_does_not_claim_old_current_restored(self):
        self.folder.invoke.return_value = None
        with self.assertRaisesRegex(S.ServiceError, "未恢复原后台任务"):
            U._restore_backup(self.folder, self.instance, self.old_record, "original task")
        self.assertFalse((self.instance / "current.json").exists())

    def test_existing_update_channel_is_never_replaced_by_new_business_install(self):
        with patch.object(S, "_verified_manifest", return_value={"data_dir": str(self.data)}), \
                patch.object(U, "_channel", return_value=(self.data, self.sid, self.instance, self.channel)):
            self.assertEqual(U.ensure_channel(self.root / "new/payload.json"), self.channel)
        self.folder.invoke.assert_not_called()

    def test_update_task_xml_uses_independent_fixed_protected_entry(self):
        with patch.object(S, "_identity", return_value=(self.data, self.sid, self.instance.name)):
            xml, name = S.task_xml(self.data, self.instance / "initial", update_channel=True)
        self.assertEqual(name, "PaperPiggyBackupUpdate-" + self.instance.name)
        self.assertIn("--update-worker", xml)
        self.assertIn("PT1M", xml)
        self.assertNotIn("SYSTEM", xml)
        self.assertNotIn("Password", xml)

    @unittest.skipUnless(sys.platform == "win32", "native Windows JobObject")
    def test_owned_job_native_process_completes_without_console(self):
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        result = U._run_helper([str(pythonw), "-I", "-S", "-c", "raise SystemExit(0)"], self.root, timeout=5)
        self.assertEqual(result, 0)

    @unittest.skipUnless(sys.platform == "win32", "native Windows JobObject")
    def test_owned_job_timeout_stops_only_its_child_tree_before_return(self):
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        child_id = self.root / "owned-child.pid"
        command = "import subprocess,sys,time;from pathlib import Path;p=subprocess.Popen([sys.executable,'-I','-S','-c','import time;time.sleep(60)'],creationflags=0x08000000);Path(" + repr(str(child_id)) + ").write_text(str(p.pid));time.sleep(60)"
        errors = []
        def run():
            try: U._run_helper([str(pythonw), "-I", "-S", "-c", command], self.root, timeout=1)
            except Exception as exc: errors.append(exc)
        worker = threading.Thread(target=run)
        worker.start()
        deadline = time.monotonic() + 5
        while not child_id.exists() and worker.is_alive() and time.monotonic() < deadline: time.sleep(.01)
        self.assertTrue(child_id.exists(), errors)
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD); kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)); kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, int(child_id.read_text()))
        self.assertTrue(handle)
        try:
            worker.join(15)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIn("超时", str(errors[0]))
            code = wintypes.DWORD()
            self.assertTrue(kernel.GetExitCodeProcess(handle, ctypes.byref(code)))
            self.assertNotEqual(code.value, 259)
        finally: kernel.CloseHandle(handle)


if __name__ == "__main__":
    unittest.main()

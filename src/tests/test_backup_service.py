from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup_service as S


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / "data & 中文"
        self.data.mkdir()
        self.sources = self.root / "src"
        self.sources.mkdir()
        self.runtime = self.root / "python"
        self.runtime.mkdir()
        for name in ("python.exe", "pythonw.exe", "python3.dll", "python312.dll"):
            (self.runtime / name).write_bytes(b"runtime fixture " + name.encode())
        (self.runtime / "LICENSE.txt").write_text("Python Software Foundation license fixture")
        (self.runtime / "Lib/encodings").mkdir(parents=True)
        (self.runtime / "Lib/encodings/__init__.py").write_text("# stdlib")
        (self.runtime / "Lib/site-packages/unsafe").mkdir(parents=True)
        (self.runtime / "Lib/site-packages/unsafe/__init__.py").write_text("raise Exception()")
        self.modules = []
        for name in ("backup_service.py", "backup_runner.py", "backup_vss.py"):
            p = self.sources / name
            p.write_text("# fixture " + name)
            self.modules.append(p)
        for name, value in [("_windows", lambda: None), ("current_sid", lambda: "S-1-5-21-1-2-3-1001"),
                            ("_program_files", lambda: self.root / "Program Files")]:
            p = patch.object(S, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_prepare_only_creates_reviewable_payload_not_tasks(self):
        with patch.object(S.subprocess, "run") as execute:
            payload = S.install_payload(self.data, self.modules, self.runtime)
        execute.assert_not_called()
        manifest = json.loads(Path(payload["manifest_path"]).read_text(encoding="utf-8"))
        paths = {x["path"] for x in manifest["files"]}
        self.assertIn("code/backup_runner.py", paths)
        self.assertIn("runtime/Lib/encodings/__init__.py", paths)
        self.assertIn("runtime/LICENSE.txt", paths)
        self.assertFalse(any("site-packages" in p for p in paths))
        self.assertEqual(payload["manifest_sha256"], S._sha(payload["manifest_path"]))
        self.assertFalse((self.root / "Program Files").exists())

    def test_payload_rejects_unknown_module_and_missing_entry(self):
        bad = self.sources / "untrusted.py"
        bad.write_text("malicious()")
        with self.assertRaisesRegex(S.ServiceError, "白名单"):
            S.install_payload(self.data, self.modules + [bad], self.runtime)
        with self.assertRaisesRegex(S.ServiceError, "缺少"):
            S.install_payload(self.data, self.modules[:1], self.runtime)

    def test_payload_does_not_rely_on_site_packages(self):
        result = S._runtime_files(self.runtime)
        self.assertIn("pythonw.exe", result)
        self.assertIn("LICENSE.txt", result)
        self.assertFalse(any("site-packages" in name for name in result))

    def test_invalid_archive_member_paths_are_not_installable(self):
        for value in ["../x", "runtime/../x", "/absolute", "runtime\\x", "runtime/x:stream"]:
            with self.subTest(value=value), self.assertRaises(S.ServiceError):
                S._safe_relative(value)

    def test_uac_request_rejects_changed_manifest_before_launch(self):
        payload = S.install_payload(self.data, self.modules, self.runtime)
        with Path(payload["manifest_path"]).open("ab") as stream:
            stream.write(b" ")
        with patch.object(S.ctypes, "WinDLL") as dll:
            with self.assertRaisesRegex(S.ServiceError, "已经改变"):
                S.request_install(payload)
            dll.assert_not_called()

    def test_install_request_only_launches_fixed_native_helper(self):
        payload = S.install_payload(self.data, self.modules, self.runtime)
        shell = Mock()
        captured = {}
        def launch(pointer):
            info = pointer._obj
            captured.update(file=info.lpFile, arguments=info.lpParameters, verb=info.lpVerb)
            return True
        shell.ShellExecuteExW.side_effect = launch
        with patch.object(S, "component_helper", return_value={"ready": True, "path": str(self.root / "backup_component.exe")}), \
                patch.object(S.ctypes, "WinDLL", return_value=shell):
            result = S.request_install(payload)
        self.assertTrue(result["pending"])
        self.assertEqual(captured["verb"], "runas")
        self.assertTrue(captured["file"].endswith("backup_component.exe"))
        self.assertIn("/VERYSILENT", captured["arguments"])
        self.assertIn("/GENERATION=", captured["arguments"])
        self.assertNotIn("powershell", captured["file"].lower())
        self.assertNotIn("python", captured["file"].lower())

    def test_each_install_uses_a_fresh_protected_generation(self):
        first = S.install_payload(self.data, self.modules, self.runtime)
        second = S.install_payload(self.data, self.modules, self.runtime)
        self.assertNotEqual(first["payload_id"], second["payload_id"])

    def test_update_detection_checks_all_worker_modules(self):
        source = Path(S.__file__).absolute().parent
        manifest = {"files": [{"path": "code/" + name, "sha256": S._sha(source / name)}
                              for name in S.MODULES]}
        self.assertFalse(S._sources_changed(manifest))
        next(row for row in manifest["files"] if row["path"] == "code/backup_archive.py")["sha256"] = "0" * 64
        self.assertTrue(S._sources_changed(manifest))

    def test_task_registration_sets_admin_owner_and_user_run_only_atomically(self):
        from contextlib import nullcontext
        payload = S.install_payload(self.data, self.modules, self.runtime)
        path = Path(payload["manifest_path"])
        manifest = json.loads(path.read_text(encoding="utf-8"))
        folder, task = Mock(), Mock()
        folder.invoke.return_value = task
        with patch.object(S, "_elevated", return_value=True), \
                patch.object(S, "_verified_manifest", return_value=manifest), \
                patch.object(S, "_scheduler", return_value=nullcontext(folder)), \
                patch.object(S, "_atomic") as save:
            S._register(path)
        args = folder.invoke.call_args.args
        self.assertEqual(args[0], "RegisterTask")
        self.assertEqual(args[3], 22)
        self.assertIsNone(args[5])
        self.assertEqual(args[6], 3)
        self.assertIn("O:BAG:BAD:P", args[7])
        self.assertIn("(A;;GRGX;;;" + manifest["owner_sid"] + ")", args[7])
        self.assertNotIn("(A;;GA;;;" + manifest["owner_sid"] + ")", args[7])
        self.assertTrue(save.call_args.kwargs["protected"])
        task.close.assert_called_once()

    def _acceptance_registration(self, fail=False):
        from contextlib import nullcontext
        payload = S.install_payload(self.data, self.modules, self.runtime, acceptance_once=True)
        path = Path(payload["manifest_path"])
        manifest = json.loads(path.read_text(encoding="utf-8"))
        folder, task, running = Mock(), Mock(), Mock()
        folder.invoke.return_value = task
        enabled = [False]
        def invoke(name, *args, flags=1):
            if name == "Enabled":
                if flags == 4:
                    enabled[0] = args[0]
                return enabled[0]
            if name == "Run":
                return running
            if name == "LastTaskResult":
                return 0
            if name == "GetSecurityDescriptor":
                return "O:BAG:BAD:P"
            raise AssertionError(name)
        task.invoke.side_effect = invoke
        running.invoke.side_effect = (S.ServiceError("isolated failure") if fail else
                                      lambda name, **kw: 3 if name == "State" else None)
        with patch.object(S, "_elevated", return_value=True), \
                patch.object(S, "_verified_manifest", return_value=manifest), \
                patch.object(S, "_scheduler", return_value=nullcontext(folder)), \
                patch.object(S, "_atomic") as save, \
                patch.object(S, "restricted_user", return_value=nullcontext()), \
                patch.object(S, "_queued_acceptance_jobs", return_value=["a" * 32]), \
                patch.object(S, "_wait_acceptance", side_effect=S.ServiceError("isolated failure") if fail else None,
                             return_value={"worker_completed": True, "last_task_result": 0}), \
                patch.object(S.time, "sleep"):
            if fail:
                with self.assertRaisesRegex(S.ServiceError, "isolated failure"):
                    S._register(path)
            else:
                S._register(path)
        self.assertFalse(enabled[0])
        task.invoke.assert_any_call("Enabled", False, flags=4)
        self.assertTrue(any(call.args[1].get("disabled") for call in save.call_args_list
                            if call.args[0].name == "acceptance-task.json"))
        return path

    def test_acceptance_task_is_initially_disabled_and_disabled_again_in_finally(self):
        path = self._acceptance_registration()
        document = ET.parse(path.parent / "task.xml")
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        self.assertEqual(document.findtext("t:Settings/t:Enabled", namespaces=ns), "false")
        self.assertIsNone(document.find("t:Triggers", ns))

    def test_acceptance_task_is_disabled_even_after_running_failure(self):
        self._acceptance_registration(fail=True)

    def test_failed_install_exposes_reason_without_claiming_success(self):
        data, _, instance = S._instance(self.data)
        pending = data / "backup_work/component-install.json"
        pending.parent.mkdir(parents=True)
        pending.write_text(json.dumps({"payload_id": "a" * 64, "started_at": S.time.time()}))
        instance.mkdir(parents=True)
        (instance / "install_error.json").write_text(json.dumps({"payload_id": "a" * 64,
                                                               "error": "payload mismatch"}))
        result = S.install_status(self.data)
        self.assertFalse(result["installed"])
        self.assertFalse(result["pending"])
        self.assertEqual(result["error"], "payload mismatch")

    def test_stale_pending_install_has_explicit_failure(self):
        pending = self.data / "backup_work/component-install.json"
        pending.parent.mkdir(parents=True)
        pending.write_text(json.dumps({"payload_id": "a" * 64, "started_at": 1}))
        result = S.install_status(self.data)
        self.assertFalse(result["installed"])
        self.assertFalse(result["pending"])
        self.assertIn("安装未完成", result["error"])

    def test_task_has_no_password_and_uses_protected_entry(self):
        target = self.root / "Program Files/PaperPiggyBackup/fixture/payload"
        xml, task_name = S.task_xml(self.data, target)
        document = ET.fromstring(xml)
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        self.assertEqual(document.findtext("t:Principals/t:Principal/t:LogonType", namespaces=ns),
                         "InteractiveToken")
        self.assertEqual(document.findtext("t:Settings/t:StartWhenAvailable", namespaces=ns), "true")
        self.assertEqual(document.findtext("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval", namespaces=ns), "PT1M")
        arguments = document.findtext("t:Actions/t:Exec/t:Arguments", namespaces=ns)
        self.assertIn("-I -S", arguments)
        self.assertIn("--worker", arguments)
        self.assertNotIn("SYSTEM", xml)
        self.assertNotIn("Password", xml)
        self.assertTrue(task_name.startswith("PaperPiggyBackup-"))
        self.assertEqual(document.findtext("t:Actions/t:Exec/t:Command", namespaces=ns),
                         str(target / "runtime/pythonw.exe"))

    def test_data_directory_must_be_absolute(self):
        with self.assertRaisesRegex(S.ServiceError, "绝对路径"):
            S._identity("relative-data")

    def test_different_data_homes_have_different_task_identity(self):
        _, _, first = S._identity(self.data)
        _, _, second = S._identity(self.root / "different")
        self.assertNotEqual(first, second)

    def test_trigger_without_installed_component_does_not_execute(self):
        with patch.object(S, "install_status", return_value={"installed": False, "error": "not installed"}), \
                patch.object(S.subprocess, "run") as execute:
            with self.assertRaisesRegex(S.ServiceError, "not installed"):
                S.trigger_task(self.data)
        execute.assert_not_called()

    def test_native_register_rejects_user_writable_source_entry(self):
        with patch.object(S, "_elevated", return_value=True), patch.object(S, "_register") as register:
            with self.assertRaisesRegex(S.ServiceError, "保护位置"):
                S._register_native(self.data, S.current_sid(), "a" * 64)
        register.assert_not_called()

    def test_native_channel_failure_restores_old_task_before_committing_current(self):
        from contextlib import nullcontext
        import backup_component_update as U
        _, sid, instance = S._instance(self.data)
        generation = "a" * 64
        target = instance / generation
        (target / "code").mkdir(parents=True)
        entries = []
        for name in S.MODULES:
            source = target / "code" / name
            source.write_text("# protected fixture\n")
            entries.append({"path": "code/" + name, "sha256": S._sha(source), "size": source.stat().st_size})
        template = {"schema": 1, "component_version": 1, "files": entries, "build_id": "b" * 64}
        (target / "payload-template.json").write_text(json.dumps(template))
        old = {"payload_id": "c" * 64}
        (instance / "current.json").write_text(json.dumps(old))
        manifest = {**template, "owner_sid": sid, "data_dir": str(self.data), "instance": instance.name, "payload_id": generation}
        folder, task = Mock(), Mock()
        folder.invoke.return_value = task
        task.invoke.return_value = "<original-task/>"
        saved_path = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), saved_path))
        def write(path, value, **kwargs): Path(path).write_text(json.dumps(value))
        with patch.object(S, "__file__", str(target / "code/backup_service.py")), \
                patch.object(S, "_elevated", return_value=True), \
                patch.object(S, "_check_protected_directory"), \
                patch.object(S, "_verified_manifest", return_value=manifest), \
                patch.object(S, "_scheduler", side_effect=lambda: nullcontext(folder)), \
                patch.object(S, "_register", return_value={"payload_id": generation}) as register, \
                patch.object(S, "_atomic", side_effect=write), \
                patch.object(U, "ensure_channel", side_effect=S.ServiceError("channel registration failed")), \
                self.assertRaisesRegex(S.ServiceError, "channel registration failed"):
            S._register_native(self.data, sid, generation)
        register.assert_called_once_with(target / "payload.json", commit=False)
        self.assertEqual(json.loads((instance / "current.json").read_text()), old)
        self.assertTrue(any(call.args[0] == "RegisterTask" and call.args[2] == "<original-task/>" for call in folder.invoke.call_args_list))

    def test_real_database_diagnostic_uses_copies_and_reports_missing_collation(self):
        import sqlite3
        source = self.root / "diagnostic-source"
        source.mkdir()
        good = source / "good.sqlite"
        with sqlite3.connect(good) as database:
            database.execute("CREATE TABLE entries(value TEXT)")
            database.execute("INSERT INTO entries VALUES('fixture')")
        database.close()
        custom = source / "custom.sqlite"
        with sqlite3.connect(custom) as database:
            database.create_collation("fixture_custom_sort", lambda a, b: (a > b) - (a < b))
            database.executescript("CREATE TABLE entries(value TEXT);"
                                  "CREATE INDEX sorted ON entries(value COLLATE fixture_custom_sort);")
        database.close()
        before = {p.name: S._sha(p) for p in (good, custom)}
        snapshot = Mock(set_id="same-set", snapshots=[{"set_id": "same-set"}])
        snapshot.map_path.side_effect = lambda p: p
        scopes = [{"id": "profile", "category": "zotero_profile", "path": str(source)}]
        with patch.object(S, "_file_token_elevated", return_value=False):
            report = S._acceptance_db_checks(snapshot, scopes, self.root / "work")
        self.assertEqual(report["database_count"], 2)
        self.assertEqual(report["passed"], 1)
        self.assertEqual(report["failed"], 1)
        self.assertEqual(report["tree_failed_count"], 0)
        self.assertEqual(report["tree_checks"][0]["file_count"], 2)
        self.assertEqual(before, {p.name: S._sha(p) for p in (good, custom)})
        self.assertFalse(list((self.root / "work").glob("*.zip")))
        self.assertNotIn("CREATE TABLE", json.dumps(report))

    def test_real_diagnostic_refuses_administrator_file_token(self):
        snapshot = Mock(set_id="test", snapshots=[])
        with patch.object(S, "_file_token_elevated", return_value=True):
            with self.assertRaisesRegex(S.ServiceError, "仍为管理员"):
                S._acceptance_db_checks(snapshot, [], self.root / "work")
        snapshot.map_path.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "需要 Windows 交互桌面令牌")
    def test_desktop_token_is_primary_nonelevated_and_same_user(self):
        with S._process_token() as token:
            identity = S._token_info(token, 1)
            sid = S._sid_string(S.ctypes.c_void_p.from_buffer(identity).value)
        with patch.object(S, "current_sid", return_value=sid):
            with S._ordinary_token() as token:
                self.assertEqual(S.wintypes.DWORD.from_buffer(S._token_info(token, 8)).value, 1)
                self.assertEqual(S.wintypes.DWORD.from_buffer(S._token_info(token, 20)).value, 0)

    @unittest.skipUnless(sys.platform == "win32", "需要 Windows 交互桌面令牌")
    def test_desktop_token_for_different_sid_is_rejected(self):
        with self.assertRaisesRegex(S.ServiceError, "不属于同一用户"):
            with S._ordinary_token():
                self.fail("不匹配的桌面身份不能用于文件业务")

    def test_finished_task_is_confirmed_without_refreshing_expired_instance(self):
        running, task = Mock(), Mock()
        running.invoke.return_value = "{MY-INSTANCE}"
        states = iter([4, 3, 3])
        task.invoke.side_effect = lambda name, **kw: next(states) if name == "State" else 0
        with patch.object(S, "_active_instance_ids", side_effect=[["{my-instance}"], [], []]), \
                patch.object(S, "_acceptance_complete", return_value=True) as complete, \
                patch.object(S.time, "sleep"):
            result = S._wait_acceptance(task, running, self.data, "generation", ["job"])
        self.assertTrue(result["worker_completed"])
        running.invoke.assert_called_once_with("InstanceGuid", flags=2)
        complete.assert_called_once_with(self.data, "generation", ["job"])

    def test_finished_task_cannot_hide_failed_fixture_job(self):
        running, task = Mock(), Mock()
        running.invoke.return_value = "guid"
        task.invoke.side_effect = lambda name, **kw: 3 if name == "State" else 0
        with patch.object(S, "_active_instance_ids", return_value=[]), \
                patch.object(S, "_acceptance_complete", side_effect=S.ServiceError("fixture failed")), \
                patch.object(S.time, "sleep"):
            with self.assertRaisesRegex(S.ServiceError, "fixture failed"):
                S._wait_acceptance(task, running, self.data, "generation", ["job"])

    def test_acceptance_completion_requires_this_generation_and_successful_jobs(self):
        from contextlib import nullcontext
        work = self.data / "backup_work"
        status = self.data / "backup_jobs/status"
        work.mkdir()
        status.mkdir(parents=True)
        S._atomic(work / "acceptance-snapshot.json", {"payload_id": "generation", "snapshot_released": True})
        S._atomic(status / "job.json", {"state": "success"})
        with patch.object(S, "restricted_user", return_value=nullcontext()):
            self.assertTrue(S._acceptance_complete(self.data, "generation", ["job"]))
            self.assertFalse(S._acceptance_complete(self.data, "stale-generation", ["job"]))
            S._atomic(status / "job.json", {"state": "failed", "error": "fixture database failed"})
            with self.assertRaisesRegex(S.ServiceError, "fixture database failed"):
                S._acceptance_complete(self.data, "generation", ["job"])

    def test_builder_reuses_tested_pe_only_while_all_fixed_inputs_match(self):
        import hashlib
        import importlib.util
        script = Path(__file__).resolve().parents[2] / "installer/build_backup_component.py"
        spec = importlib.util.spec_from_file_location("test_backup_component_builder", script)
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        for name in S.MODULES:
            if not (self.sources / name).exists():
                (self.sources / name).write_text("# immutable fixture " + name)
        installer = self.root / "installer/backup_component.iss"
        installer.parent.mkdir()
        installer.write_text("fixed installer source")
        output = self.root / "component-output"
        output.mkdir()
        executable = output / "backup_component.exe"
        executable.write_bytes(b"already tested fixed PE fixture")
        files = {"code/" + name: self.sources / name for name in S.MODULES}
        files.update({"runtime/" + name: path for name, path in S._runtime_files(self.runtime).items()})
        entries = [{"path": name, "sha256": builder.sha(path), "size": path.stat().st_size}
                   for name, path in sorted(files.items())]
        metadata = {"schema": 1, "build_id": hashlib.sha256(json.dumps(entries, separators=(",", ":")).encode()).hexdigest(),
            "app_version": builder.C.APP_VERSION, "component_version": builder.release_component_version(builder.C.APP_VERSION),
            "installer_sha256": builder.sha(installer),
            "builder_sha256": builder.sha(script), "sha256": builder.sha(executable),
            "files": len(entries), "bytes": sum(e["size"] for e in entries),
            "source_hashes": {e["path"][5:]: e["sha256"] for e in entries if e["path"].startswith("code/")},
            "runtime_hashes": {e["path"][8:]: e["sha256"] for e in entries if e["path"].startswith("runtime/")}}
        (output / "backup_component.json").write_text(json.dumps(metadata), encoding="utf-8")
        with patch.object(builder, "ROOT", self.root), patch.object(builder.subprocess, "run") as compile:
            reused = builder.build(self.runtime, output, self.root / "missing-iscc.exe")
            self.assertTrue(reused["cached"])
            compile.assert_not_called()
            installer.write_text("changed installer source")
            with self.assertRaisesRegex(RuntimeError, "ISCC"):
                builder.build(self.runtime, output, self.root / "missing-iscc.exe")
            compile.assert_not_called()
        self.assertEqual(executable.read_bytes(), b"already tested fixed PE fixture")


def acceptance_prepare():
    """仅创建隔离夹具；不启动 writer、不提权、不创建任务或快照。"""
    import hashlib
    import os
    import sqlite3
    import uuid
    repo = Path(__file__).resolve().parents[2]
    token = uuid.uuid4().hex
    root = repo / "build/backup-acceptance" / token
    croot = Path(tempfile.gettempdir()) / ("PaperPiggyBackupAcceptance-" + token)
    if root.drive.upper() != "D:" or croot.drive.upper() != "C:":
        raise RuntimeError("本机验收要求隔离夹具分别位于 C/D 卷。")
    library, profile, data = root / "library", croot / "profile", root / "data"
    for directory in (library, profile, data, root / "destination"):
        directory.mkdir(parents=True, exist_ok=False)
    attachment = library / "storage/A1B2C3D4/fixture.pdf"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"%PDF-1.4\nPaperPiggy isolated immutable attachment\n%%EOF\n")
    (profile / "prefs.js").write_text('// 保留插件设置及原注释的隔离夹具\n' +
        'user_pref("extensions.zotero.dataDir", ' + json.dumps(str(library)) + ');\n' +
        'user_pref("extensions.zotero.sync.autoSync", true);\n' +
        'user_pref("extensions.fixture.customPreference", "preserve-verbatim");\n', encoding="utf-8")
    (profile / "extensions").mkdir()
    (profile / "extensions/fixture.xpi").write_bytes(b"isolated plugin fixture")
    (profile / "plugin-state").mkdir()
    databases = [library / "zotero.sqlite", profile / "plugin-state/plugin.sqlite"]
    for database in databases:
        connection = sqlite3.connect(database)
        connection.executescript("CREATE TABLE committed(tick INTEGER PRIMARY KEY, payload TEXT);"
            "CREATE TABLE unfinished(tick INTEGER PRIMARY KEY, payload TEXT);")
        if database.name == "zotero.sqlite":
            connection.executescript("CREATE TABLE items(itemID INTEGER PRIMARY KEY,key TEXT);"
                "CREATE TABLE itemAttachments(itemID INTEGER,linkMode INTEGER,path TEXT);"
                "INSERT INTO items VALUES(1,'A1B2C3D4');"
                "INSERT INTO itemAttachments VALUES(1,0,'storage:fixture.pdf');")
        connection.commit()
        connection.close()
    os.environ["LOCALKB_DATA"] = str(data)
    import backup_runner
    current = backup_runner.store(data)
    job = backup_runner.get_job(current, "zotero")
    current.save(job["job_id"], {"enabled": False, "destination": str(root / "destination"),
        "sources": {"data_dir": str(library), "profile_dir": str(profile)}, "keep": 2})
    pp = backup_runner.get_job(current, "paperpiggy")
    current.save(pp["job_id"], {"enabled": False, "destination": str(root / "paperpiggy-destination"),
                               "include_browser": False})
    plan = {"schema": 1, "token": token, "root": str(root), "c_root": str(croot),
        "data": str(data), "databases": [str(x) for x in databases], "job_id": job["job_id"],
        "paperpiggy_job_id": pp["job_id"],
        "attachment_sha256": hashlib.sha256(attachment.read_bytes()).hexdigest(),
        "prefs_sha256": hashlib.sha256((profile / "prefs.js").read_bytes()).hexdigest(),
        "plan_path": str(root / "acceptance-plan.json")}
    Path(plan["plan_path"]).write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    instructions = ("# 隔离 VSS / UAC 验收\n\n"
        "此目录仅含人工夹具。准备动作没有提权、启动数据库 writer、注册任务或创建快照。\n\n"
        "1. 先执行 payload 命令生成最新 12 模块与最小运行时清单，审阅 payload-review.json 及其 manifest_path。"
        "再普通权限执行 request 命令。它核对源码仍与审阅载荷一致，启动独立普通权限 WAL writer，再显示一次 Windows UAC。\n"
        "2. 安装到由隔离 data 家派生的独立 Program Files instance。验收任务初始 Disabled；"
        "同次提升中显式启用并运行，try/finally 再禁用，失败同样禁用。保留文件和 Disabled 任务供检查。\n"
        "3. UAC 操作结束后执行 verify。只有静态附件哈希、两卷同一 set、两个 SQLite integrity_check、"
        "已提交记录存在、未提交记录未混入、writer 期间仍持续运行及任务 Disabled 全通过才算成功。\n"
        "4. 无论结果执行 stop，以文件信号让本任务 writer 自行退出；不结束其他进程。\n\n"
        "未修改正式安装、实际 Zotero/Chrome 或 9222。没有自动删除夹具、任务或保护组件。\n\n"
        "```powershell\n" +
        f"& '{sys.executable}' '{Path(__file__).absolute()}' --acceptance-payload '{plan['plan_path']}'\n" +
        f"& '{sys.executable}' '{Path(__file__).absolute()}' --acceptance-request '{plan['plan_path']}'\n" +
        f"& '{sys.executable}' '{Path(__file__).absolute()}' --acceptance-verify '{plan['plan_path']}'\n" +
        f"& '{sys.executable}' '{Path(__file__).absolute()}' --acceptance-stop '{plan['plan_path']}'\n```\n")
    (root / "执行说明.md").write_text(instructions, encoding="utf-8")
    return plan


def _acceptance_plan(path):
    import os
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    root = Path(plan["root"])
    if (root.name != plan["token"] or len(plan["token"]) != 32 or root.parent.name != "backup-acceptance"
            or Path(path).absolute() != root / "acceptance-plan.json"):
        raise RuntimeError("验收计划归属不符。")
    os.environ["LOCALKB_DATA"] = plan["data"]
    return plan


def acceptance_writer(path):
    import os
    import sqlite3
    import time
    plan = _acceptance_plan(path)
    root = Path(plan["root"])
    connections = [sqlite3.connect(x) for x in plan["databases"]]
    try:
        for connection in connections:
            if connection.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() != "wal":
                raise RuntimeError("无法启用夹具 WAL。")
            connection.execute("PRAGMA wal_autocheckpoint=0")
            connection.execute("PRAGMA synchronous=FULL")
        deadline, tick = time.monotonic() + 900, 0
        while time.monotonic() < deadline and not (root / "writer.stop").exists():
            tick += 1
            for connection in connections:
                connection.execute("INSERT INTO committed VALUES(?,?)", (tick, "x" * 512))
                connection.commit()
                connection.execute("INSERT INTO unfinished VALUES(?,?)", (tick, "must roll back"))
            status = {"pid": os.getpid(), "tick": tick, "time": time.time(),
                "wal_sizes": [Path(x + "-wal").stat().st_size for x in plan["databases"]]}
            S._atomic(root / "writer.json", status)
            time.sleep(.04)
            for connection in connections:
                connection.rollback()
    finally:
        for connection in connections:
            connection.close()
        S._atomic(root / "writer-exited.json", {"pid": os.getpid(), "time": time.time()})


def acceptance_request(path):
    import time
    plan = _acceptance_plan(path)
    root = Path(plan["root"])
    review = root / "payload-review.json"
    if not review.is_file():
        raise RuntimeError("请先执行 acceptance-payload，审阅最新载荷后再请求 UAC。")
    payload = json.loads(review.read_text(encoding="utf-8"))
    if S._sha(payload["manifest_path"]) != payload["manifest_sha256"]:
        raise RuntimeError("审阅的载荷清单已改变，请重新准备。")
    manifest = json.loads(Path(payload["manifest_path"]).read_text(encoding="utf-8"))
    if not manifest.get("acceptance_once") or manifest["data_dir"] != plan["data"]:
        raise RuntimeError("载荷不是本隔离验收的单次禁用任务。")
    if any(S._sha(item["source"]) != item["sha256"] for item in manifest["files"]):
        raise RuntimeError("源码或运行时在审阅后已改变，请重新执行 acceptance-payload。")
    helper = S.component_helper()
    if not helper["ready"]:
        raise RuntimeError(helper["error"])
    if (root / "writer.stop").exists() or (root / "writer.json").exists():
        raise RuntimeError("每个夹具只触发一次验收，请为重试重新准备夹具。")
    writer = S.subprocess.Popen([str(Path(sys.executable).with_name("pythonw.exe")),
        str(Path(__file__).absolute()), "--acceptance-writer", str(path)], creationflags=S.NO_WINDOW)
    deadline = time.monotonic() + 15
    while not (root / "writer.json").is_file():
        if writer.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError("隔离 WAL writer 未能启动，未触发 UAC。")
        time.sleep(.1)
    import backup_runner
    request = backup_runner.enqueue("zotero", data_dir=plan["data"])
    paperpiggy_request = backup_runner.enqueue("paperpiggy", data_dir=plan["data"])
    S._atomic(root / "request-review.json", request)
    S._atomic(root / "paperpiggy-request-review.json", paperpiggy_request)
    try:
        return S.request_install(payload)
    except BaseException:
        (root / "writer.stop").write_text("UAC cancelled or failed", encoding="utf-8")
        raise


def acceptance_payload(path):
    plan = _acceptance_plan(path)
    source = Path(__file__).resolve().parents[1]
    payload = S.install_payload(plan["data"], [source / x for x in sorted(S.MODULES)],
                                acceptance_once=True)
    S._atomic(Path(plan["root"]) / "payload-review.json", payload)
    return payload


def acceptance_verify(path):
    import hashlib
    import sqlite3
    import time
    plan = _acceptance_plan(path)
    root, data = Path(plan["root"]), Path(plan["data"])
    report_path = data / "backup_work/acceptance-task.json"
    if not report_path.exists():
        raise RuntimeError("提升验收尚未结束或未写出报告；暂不判成功。")
    task = json.loads(report_path.read_text(encoding="utf-8"))
    if not task.get("disabled") or task.get("error") or task.get("disable_error"):
        raise RuntimeError("任务验收失败或未确认禁用：" + json.dumps(task, ensure_ascii=False))
    import backup_archive
    import backup_jobs
    import backup_restore
    current = backup_jobs.JobStore(data / "backup_jobs")
    state = current.status(plan["job_id"])
    if state.get("state") not in ("success", "success_with_warning"):
        raise RuntimeError("备份未成功：" + json.dumps(state, ensure_ascii=False))
    ledger = current.ledger(plan["job_id"])
    if len(ledger) != 1:
        raise RuntimeError("隔离任务成功登记数量不符。")
    archive = ledger[0]["path"]
    manifest = backup_archive.inspect(archive)["manifest"]
    snapshot = manifest["metadata"]["snapshot"]
    if len(snapshot["volumes"]) != 2 or {x["set_id"] for x in snapshot["volumes"]} != {snapshot["set_id"]}:
        raise RuntimeError("C/D 未进入同批双卷快照。")
    pp_ledger = current.ledger(plan["paperpiggy_job_id"])
    if len(pp_ledger) != 1:
        raise RuntimeError("PaperPiggy 夹具未生成一个独立成功包。")
    pp_archive = pp_ledger[0]["path"]
    pp_manifest = backup_archive.inspect(pp_archive)["manifest"]
    if pp_manifest["metadata"]["snapshot"]["set_id"] != snapshot["set_id"]:
        raise RuntimeError("两个独立夹具包未使用同一个快照集。")
    cleanup = json.loads((data / "backup_work/acceptance-snapshot.json").read_text(encoding="utf-8"))
    if not cleanup.get("snapshot_released") or cleanup.get("set_id") != snapshot["set_id"]:
        raise RuntimeError("未确认本次快照集已释放。")
    database_report = json.loads((data / "backup_work/acceptance-db-report.json").read_text(encoding="utf-8"))
    if database_report.get("file_token_elevated") is not False:
        raise RuntimeError("没有普通文件令牌读取证据。")
    restored = root / "verified-restore"
    backup_restore.extract_to(archive, restored)
    checks = []
    for relative in ("zotero_data/zotero.sqlite", "zotero_profile/plugin-state/plugin.sqlite"):
        with sqlite3.connect(restored / relative) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchall()
            committed = connection.execute("SELECT COUNT(*) FROM committed").fetchone()[0]
            unfinished = connection.execute("SELECT COUNT(*) FROM unfinished").fetchone()[0]
        if integrity != [("ok",)] or not committed or unfinished:
            raise RuntimeError("快照数据库校验失败：" + relative)
        checks.append({"database": relative, "integrity_check": "ok", "committed": committed, "unfinished": 0})
    attachment = restored / "zotero_data/storage/A1B2C3D4/fixture.pdf"
    if hashlib.sha256(attachment.read_bytes()).hexdigest() != plan["attachment_sha256"]:
        raise RuntimeError("静态附件哈希不一致。")
    if hashlib.sha256((restored / "zotero_profile/prefs.js").read_bytes()).hexdigest() != plan["prefs_sha256"]:
        raise RuntimeError("插件 prefs 或注释未逐字保留。")
    heartbeat = json.loads((root / "writer.json").read_text(encoding="utf-8"))
    if (root / "writer-exited.json").exists() or time.time() - heartbeat["time"] > 3:
        raise RuntimeError("普通权限 writer 在验收完成前停止，不能宣称热备份通过。")
    result = {"ok": not database_report["failed"] and not database_report.get("tree_failed_count"), "fixture_ok": True,
        "real_database_checks_ok": not database_report["failed"],
        "real_tree_checks_ok": not database_report.get("tree_failed_count"),
        "task": task, "snapshot": snapshot, "databases": checks,
        "writer_alive": True, "writer": heartbeat, "archive": archive,
        "paperpiggy_archive": pp_archive, "real_database_report": database_report,
        "low_token_globalroot_read": "受保护 worker 全程普通文件令牌，真实任务成功读取 GLOBALROOT 并打包",
        "attachment_sha256": plan["attachment_sha256"]}
    S._atomic(root / "acceptance-result.json", result)
    return result


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].startswith("--acceptance-"):
        if sys.stdout:
            sys.stdout.reconfigure(encoding="utf-8")
        mode = sys.argv[1]
        if mode == "--acceptance-prepare":
            result = acceptance_prepare()
        elif mode == "--acceptance-writer":
            result = acceptance_writer(sys.argv[2])
        elif mode == "--acceptance-request":
            result = acceptance_request(sys.argv[2])
        elif mode == "--acceptance-payload":
            result = acceptance_payload(sys.argv[2])
        elif mode == "--acceptance-verify":
            result = acceptance_verify(sys.argv[2])
        elif mode == "--acceptance-stop":
            plan = _acceptance_plan(sys.argv[2])
            (Path(plan["root"]) / "writer.stop").write_text("manual acceptance stop", encoding="utf-8")
            result = {"stop_requested": True}
        else:
            raise SystemExit("未知验收参数")
        if sys.stdout:
            print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        unittest.main()

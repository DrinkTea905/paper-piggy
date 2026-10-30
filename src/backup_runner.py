"""无界面的独立备份入口。由受保护组件调用，绝不加载检索模型或打开桌面窗口。"""
from __future__ import annotations

import argparse
import configparser
from contextlib import ExitStack, nullcontext
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid


GATE_FILE = "restore-gate.json"
BROWSER_SESSION_WARNING = "浏览器可能提示恢复上次会话，请在专用窗口中核对并确认恢复标签。"


def _restore_app_dir(options):
    value = options.get("app_dir")
    script = options.get("wait_for_script")
    if not value and script and Path(script).is_absolute() and Path(script).name == "server.py":
        value = str(Path(script).parent)
    if not value:
        import config as C
        value = str(C.APP)
    if not Path(value).is_absolute():
        from backup_jobs import BackupError
        raise BackupError("小猪安装目录必须为绝对路径。")
    return Path(value)


def _clear_gate(current, request_id=None):
    from backup_jobs import check_path, read_json
    path = check_path(current.base / GATE_FILE)
    if path.is_file():
        value = read_json(path, {})
        if request_id is None or value.get("request_id") == request_id:
            path.unlink()


def store(data_dir=None):
    if data_dir and "config" not in sys.modules:
        os.environ["LOCALKB_DATA"] = str(Path(data_dir).absolute())
    import config as C
    import settings
    from backup_jobs import JobStore
    current = JobStore(Path(data_dir or C.DATA) / "backup_jobs")
    current.ensure_defaults(settings.load().get("backup") or {})
    return current


def get_job(current, kind):
    return next((j for j in current.list() if j["kind"] == kind and j.get("machine_id") == current.machine_id), None)


def enqueue(kind, operation="backup", options=None, data_dir=None):
    from backup_jobs import BackupError, BackupBusy, atomic_json, file_lock
    current = store(data_dir)
    job = get_job(current, kind)
    if not job or operation not in ("backup", "restore"):
        raise BackupError("未知备份任务或操作。")
    request_id = uuid.uuid4().hex
    with file_lock(current.base / "locks" / (job["job_id"] + ".enqueue")):
        status = current.status(job["job_id"])
        if status.get("running"):
            raise BackupBusy("本任务已经运行或排队，未重复提交。")
        if active_paperpiggy_restore(current.base.parent):
            raise BackupBusy("存在待完成的小猪原位恢复，请先等待恢复或安全回滚。")
        request = {"schema": 1, "request_id": request_id, "job_id": job["job_id"],
                   "kind": kind, "operation": operation, "options": options or {},
                   "created": datetime.now().astimezone().isoformat(timespec="seconds")}
        if kind == "paperpiggy" and operation == "restore" and request["options"].get("mode") == "in_place":
            # 在请求可被 worker 看见之前设置持久门禁；进程崩溃不能解除门禁。
            atomic_json(current.base / GATE_FILE, {**request, "app_dir": str(_restore_app_dir(request["options"]))})
        atomic_json(current.base / "requests" / (request_id + ".json"), request)
        current.set_status(job["job_id"], running=True, pending=True, state="queued",
                           stage="已排队，等待后台备份组件", request_id=request_id,
                           operation=operation, cancel_requested=False, error="", result={}, done=0, total=0)
    return request


def _progress(current, job_id):
    last = [0.0]
    def callback(done, total):
        now = time.monotonic()
        if now - last[0] >= .5 or done == total:
            current.set_status(job_id, done=done, total=total)
            last[0] = now
    return callback


def _cancel(current, job_id):
    last = [0.0]
    cancelled = [False]
    def callback():
        if time.monotonic() - last[0] >= .2:
            cancelled[0] = bool(current.status(job_id).get("cancel_requested"))
            last[0] = time.monotonic()
        return cancelled[0]
    return callback


def run_backup(current, job, trigger="manual", slot=None, request_id=None, snapshot_factory=None):
    import backup_archive
    import backup_sources
    import backup_vss
    snapshot_factory = snapshot_factory or backup_vss.snapshot
    from backup_jobs import paperpiggy_maintenance_lock, ensure_work_dir
    maintenance = paperpiggy_maintenance_lock(current.base.parent) if job["kind"] == "paperpiggy" else nullcontext()
    with current.claim(job["job_id"], trigger=trigger, slot=slot, request_id=request_id), ExitStack() as resources:
        current.check_cancel(job["job_id"])
        resources.enter_context(maintenance)
        work_dir = ensure_work_dir(current.base / "work")
        current.set_status(job["job_id"], pending=False, stage="核对来源与备份位置",
                           request_id=request_id, operation="backup", done=0, total=0)
        roots, metadata = backup_sources.validate_sources(job, current.base.parent)
        current.check_cancel(job["job_id"])
        cancelled = _cancel(current, job["job_id"])
        current.set_status(job["job_id"], stage="创建同批卷快照")
        with snapshot_factory([r["source"] for r in roots], cancel=cancelled) as snapshot:
            mapped = backup_sources.prepare_snapshot_roots(job, roots, snapshot)
            metadata["snapshot"] = {"set_id": str(snapshot.set_id), "volumes": snapshot.snapshots}
            current.set_status(job["job_id"], stage="验证来源附件覆盖")
            backup_sources.validate_zotero_attachments(mapped)
            current.set_status(job["job_id"], stage="打包并逐文件校验")
            result = backup_archive.create(job, mapped, metadata=metadata,
                       progress=_progress(current, job["job_id"]), cancel=cancelled,
                       work_dir=work_dir)
        current.set_status(job["job_id"], stage="登记成功版本并安全清理旧版")
        final = current.record_success(job["job_id"], result)
        current.set_status(job["job_id"], pending=False,
                           stage="备份成功，旧版清理失败" if final.get("warnings") else "备份完成")
        return final


def _set_pref(path, name, value):
    text = path.read_text(encoding="utf-8")
    line = "user_pref(" + json.dumps(name) + ", " + json.dumps(value, ensure_ascii=False) + ");"
    pattern = r'user_pref\(\s*' + re.escape(json.dumps(name)) + r'\s*,[^\r\n]*?\);'
    if re.search(pattern, text):
        text = re.sub(pattern, lambda _: line, text)
    else:
        text += "\n" + line + "\n"
    path.write_text(text, encoding="utf-8")


def _browser_program(manifest, requested):
    from backup_jobs import BackupError, check_path
    metadata = manifest.get("metadata") or {}
    cfg = metadata.get("browser")
    if not cfg:
        return None
    source = Path(cfg.get("executable", "")).name.lower()
    value = requested or cfg.get("executable")
    if not value or not Path(value).is_absolute():
        raise BackupError("请指定本机已安装的同类浏览器程序路径。")
    path = check_path(value, allow_missing=False)
    if not path.is_file() or path.name.lower() != source or source not in ("chrome.exe", "msedge.exe"):
        raise BackupError("浏览器程序缺失或类型与备份不同；未切换浏览器或覆盖资料。")
    return str(path)


def _prepare_restored(extracted_roots, manifest, targets, browser_executable=None, in_place=False):
    """只改待恢复副本的路径映射；不改自动同步，不操作云端。"""
    from backup_jobs import BackupError, atomic_json, io_path
    extracted_roots = Path(io_path(extracted_roots))
    library = targets.get("zotero_data")
    profile = extracted_roots / "zotero_profile"
    if library and (profile / "prefs.js").is_file():
        _set_pref(profile / "prefs.js", "extensions.zotero.dataDir", str(Path(library).absolute()))
        _set_pref(profile / "prefs.js", "extensions.zotero.useDataDir", True)
    registry = extracted_roots / "zotero_registry"
    if registry.is_dir() and targets.get("zotero_profile"):
        _prepare_profile_registry(registry, manifest, targets, in_place)
    if targets.get("browser"):
        executable = _browser_program(manifest, browser_executable)
        recorded = dict((manifest.get("metadata") or {}).get("browser") or {})
        config_path = extracted_roots / "paperpiggy_data/dedicated_browser.json"
        cfg = json.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else recorded.copy()
        if not isinstance(cfg, dict) or cfg.get("port") != recorded.get("port"):
            raise BackupError("浏览器关联与备份元数据中的端口不一致，未覆盖恢复。")
        cfg.update({"schema": 1, "executable": executable,
                    "user_data_dir": str(Path(targets["browser"]).absolute())})
        if not isinstance(cfg.get("port"), int) or not 1 <= cfg["port"] <= 65535:
            raise BackupError("备份中的浏览器端口无效，未自动更换端口。")
        config_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(config_path, cfg)
    if in_place:
        for root in manifest["roots"]:
            if root["id"] == "paperpiggy_data":
                if root.get("include") is None:
                    raise BackupError("小猪原位恢复必须使用明确的内容清单；请先恢复到新目录核对。")
                # 不用历史任务状态覆盖当前正在执行恢复的锁、队列或跨机器任务归属。
                root["include"] = [x for x in root["include"] if x != "backup_jobs" and not x.startswith("backup_jobs/")]


def _version_warnings(manifest, targets):
    import backup_sources
    from backup_jobs import BackupError
    recorded = str((manifest.get("metadata") or {}).get("zotero", {}).get("version", ""))
    if not recorded:
        return []
    installed = backup_sources.installed_zotero_version(targets.get("zotero_profile"))
    if not installed:
        return ["尚未确认目标 Zotero 程序版本；请在首次启动前安装与备份兼容的版本（备份版本：" + recorded + "）。"]
    def numbers(value):
        found = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?", value)
        return tuple(int(x or 0) for x in found.groups()) if found else None
    old, new = numbers(recorded), numbers(installed)
    if old and new and new < old:
        raise BackupError("目标 Zotero 版本 " + installed + " 早于备份版本 " + recorded + "，请先安装兼容版本；原目录未改动。")
    return []


def _prepare_profile_registry(staging, manifest, targets, in_place):
    """合并所选profile注册，保留目标机器上的其他profile与安装项。"""
    from backup_jobs import io_path
    target = Path(targets["zotero_registry"])
    config = configparser.RawConfigParser()
    config.optionxform = str  # profiles.ini 的键名大小写必须保留。
    current = target / "profiles.ini"
    if in_place and Path(io_path(current)).exists():
        config.read(io_path(current), encoding="utf-8-sig")
    selected = str(Path(targets["zotero_profile"]).absolute())
    existing = None
    for section in config.sections():
        if section.startswith("Profile") and config.has_option(section, "Path"):
            value = config.get(section, "Path")
            resolved = target / value if config.get(section, "IsRelative", fallback="1") == "1" else Path(value)
            if os.path.normcase(os.path.abspath(resolved)) == os.path.normcase(selected):
                existing = section
    if existing is None:
        i = 0
        while config.has_section("Profile" + str(i)):
            i += 1
        existing = "Profile" + str(i)
        config.add_section(existing)
    if not config.has_section("General"):
        config.add_section("General")
        config.set("General", "StartWithLastProfile", "1")
    config.set(existing, "Name", (manifest.get("metadata") or {}).get("zotero", {}).get("profile_name", "PaperPiggy-restored"))
    config.set(existing, "Path", selected)
    config.set(existing, "IsRelative", "0")
    # 不夺走目标机器上其他profile的默认地位。
    if len([s for s in config.sections() if s.startswith("Profile")]) == 1:
        config.set(existing, "Default", "1")
    with (staging / "profiles.ini").open("w", encoding="utf-8") as stream:
        config.write(stream)
    # 安装hash绑定不能从另一机器整体覆盖。保持目标现存值，新目录仅保留作参考。
    if in_place:
        installs = Path(io_path(target / "installs.ini"))
        if installs.exists():
            (staging / "installs.ini").write_bytes(installs.read_bytes())
        else:
            (staging / "installs.ini").unlink(missing_ok=True)
        for root in manifest["roots"]:
            if root["id"] == "zotero_registry":
                root["include"] = ["profiles.ini"] + (["installs.ini"] if installs.exists() else [])


def _waiter_alive(options):
    pid = options.get("wait_for_pid")
    if not pid or sys.platform != "win32":
        return False
    import dedicated_browser
    row = dedicated_browser._process(int(pid))
    if row is None:
        import backup_sources
        if any(r["pid"] == int(pid) for r in backup_sources._process_entries()):
            raise backup_sources.SourceError("无法核验等待退出的小猪进程，未执行原位恢复。")
    expected = options.get("wait_for_script")
    return bool(row and expected and expected in row.get("args", []))


def _ensure_stopped(kind, targets, options):
    import backup_sources
    if kind == "paperpiggy":
        if _waiter_alive(options):
            raise backup_sources.SourceError("小猪仍在运行，请退出后等待原位恢复。")
        backup_sources.ensure_paperpiggy_mcp_stopped(_restore_app_dir(options))
    backup_sources.ensure_restore_stopped(kind, targets)


def run_restore(current, job, options, request_id=None):
    import backup_legacy
    with backup_legacy.resolved_archive(options.get("path"), current, job) as resolved:
        return _run_restore_resolved(current, job, {**options, "path": resolved}, request_id)


def _run_restore_resolved(current, job, options, request_id=None):
    import backup_archive
    import backup_restore
    import backup_sources
    from backup_jobs import BackupError
    path = options.get("path")
    inspected = backup_archive.inspect(path)
    manifest = inspected["manifest"]
    if manifest["kind"] != job["kind"]:
        raise BackupError("备份类型与所选任务不一致。")
    mode = options.get("mode", "new")
    with current.claim(job["job_id"], trigger="restore"):
        current.set_status(job["job_id"], request_id=request_id, pending=False,
                           operation="restore", stage="验证并恢复到目标位置", done=0, total=0)
        if mode == "new":
            destination = Path(options.get("destination") or "")
            if not destination.is_absolute():
                raise BackupError("请选择新目录恢复的绝对目标路径。")
            targets = {r["id"]: str(destination / r["id"]) for r in manifest["roots"]}
            version_warnings = _version_warnings(manifest, targets)
            if "browser" in targets:
                _browser_program(manifest, options.get("browser_executable"))
            result = backup_restore.extract_to(path, destination,
                       progress=_progress(current, job["job_id"]))
            try:
                _prepare_restored(destination, manifest, targets, options.get("browser_executable"))
            except Exception as exc:
                from backup_jobs import atomic_json
                atomic_json(destination / "restore-incomplete.json", {"error": str(exc), "stage": "路径映射未完成"})
                raise
            result.setdefault("warnings", []).extend(version_warnings)
        elif mode == "in_place":
            if not options.get("confirm"):
                raise BackupError("原位恢复需要明确确认。")
            targets = options.get("targets") or {}
            if set(targets) != {r["id"] for r in manifest["roots"]}:
                raise BackupError("原位恢复必须明确映射备份中的全部来源，未执行部分替换。")
            if "paperpiggy_data" in targets and os.path.normcase(os.path.abspath(targets["paperpiggy_data"])) != os.path.normcase(str(current.base.parent.absolute())):
                raise BackupError("小猪原位恢复只可还原当前数据家；换路径请先用新目录恢复。")
            _ensure_stopped(job["kind"], targets, options)
            version_warnings = _version_warnings(manifest, targets)
            from backup_jobs import atomic_json
            context = {"schema": 1, "backup_id": manifest["backup_id"], "kind": job["kind"],
                       "job_id": job["job_id"], "request_id": request_id, "targets": targets,
                       "options": {k: options[k] for k in ("wait_for_pid", "wait_for_script", "app_dir") if k in options}}
            journal_dir = current.base / "restore_journals"
            context["previous_journals"] = [p.name for p in journal_dir.glob("PaperPiggy-restore-*.json")]
            context_path = journal_dir / (manifest["backup_id"] + ".context.json")
            atomic_json(context_path, context)
            if job["kind"] == "paperpiggy" and not (current.base / GATE_FILE).exists():
                atomic_json(current.base / GATE_FILE, {**context, "operation": "restore"})
            def prepare(extracted, mutable_manifest, actual_targets):
                _prepare_restored(extracted / "roots", mutable_manifest, actual_targets,
                                  options.get("browser_executable"), in_place=True)
                candidate = extracted / "roots/paperpiggy_data/backup_jobs/jobs.json"
                if candidate.is_file():
                    pending = current.base / ("restored-jobs-" + manifest["backup_id"] + ".json")
                    if not pending.exists():
                        with pending.open("xb") as stream:
                            stream.write(candidate.read_bytes())
            result = backup_restore.restore_in_place(path, targets, confirm=True,
                       ensure_stopped=lambda m, t: _ensure_stopped(m["kind"], t, options),
                       prepare=prepare, journal_dir=journal_dir,
                       progress=_progress(current, job["job_id"]))
            result.setdefault("warnings", []).extend(version_warnings)
            atomic_json(context_path, {**context, "resolved": True})
            if job["kind"] == "paperpiggy":
                result["requires_restart"] = True
                result.setdefault("warnings", []).append("恢复的历史任务配置已单独保留供核对；当前任务归属和系统计划未被静默覆盖。")
        else:
            raise BackupError("未知恢复方式。")
        result.setdefault("warnings", []).extend((manifest.get("metadata") or {}).get("legacy_warnings", []))
        if any(root["id"] == "browser" for root in manifest["roots"]):
            result.setdefault("warnings", []).append(BROWSER_SESSION_WARNING)
        current.finish(job["job_id"], "success", result=result)
        current.set_status(job["job_id"], pending=False, stage="恢复完成" + ("，请重新打开小猪" if result.get("requires_restart") else ""))
        if mode == "in_place" and job["kind"] == "paperpiggy":
            _clear_gate(current, request_id)
        return result


def recover_interrupted_restores(current):
    """核验持久上下文后恢复中断的目录交换；失败保留门禁及全部回退件。

    返回 False 时调用方必须停止任务，不能在半恢复数据上继续备份或写入。
    """
    import backup_restore
    from backup_jobs import BackupError, BackupBusy, check_path, read_json, atomic_json, file_lock
    directory = current.base / "restore_journals"
    pending = []
    for file in sorted(directory.glob("PaperPiggy-restore-*.json")):
        try:
            check_path(file, allow_missing=False)
            journal = read_json(file, {})
            terminal = journal.get("state") in ("completed", "rolled_back")
            if not re.fullmatch(r"[a-f0-9]{32}", str(journal.get("backup_id", ""))):
                raise BackupError("恢复记录中的备份标识无效。")
            context_path = check_path(directory / (str(journal.get("backup_id", "")) + ".context.json"))
            context = read_json(context_path, {})
            context_rid = context.get("request_id") or ""
            has_request = bool(re.fullmatch(r"[a-f0-9]{32}", context_rid) and
                               (current.base / "requests" / (context_rid + ".json")).is_file())
            if file.name in context.get("previous_journals", []) or context.get("resolved") and not has_request:
                if not terminal:
                    raise BackupError("有未完成的历史恢复记录，需先核对回退状态。")
                continue
            if not context:
                if terminal:
                    continue
                raise BackupError("恢复上下文缺失，未擅自改动目录；请核对恢复记录。")
            if context.get("backup_id") != journal.get("backup_id"):
                raise BackupError("恢复记录与上下文不匹配。")
            if current.get(context["job_id"]).get("machine_id") != current.machine_id:
                raise BackupError("这是另一台电脑的恢复记录，未自动接管其目录交换。")
            backup_restore._validate_journal(journal)
            targets = context.get("targets") or {}
            for op in journal.get("operations", []):
                target = targets.get(op.get("root_id"))
                live = check_path(op["live"])
                if not target or not Path(target).is_absolute():
                    raise BackupError("恢复上下文的目标不完整。")
                root = check_path(target)
                if live != root and root not in live.parents:
                    raise BackupError("恢复记录越过已确认目标，未自动回滚。")
            if not terminal:
                _ensure_stopped(context["kind"], targets, context.get("options") or {})
                result = backup_restore.recover_journal(file)
                if not result.get("ok"):
                    raise BackupError("中断恢复未能安全回滚，请保留回退件并核对记录。")
                journal["state"] = result["state"]
            state = "success" if journal["state"] == "completed" else "interrupted"
            current.finish(context["job_id"], state,
                           "" if state == "success" else "恢复被中断，已安全回滚；可重新提交恢复。",
                           result={"journal": str(file), "warnings": ["已核对上次中断后的目录交换记录。"]})
            rid = context.get("request_id")
            if rid and re.fullmatch(r"[a-f0-9]{32}", rid):
                request = check_path(current.base / "requests" / (rid + ".json"))
                if request.exists():
                    request.rename(request.with_suffix(".recovered"))
            atomic_json(context_path, {**context, "resolved": True})
        except Exception as exc:
            pending.append(str(exc))
    if pending:
        atomic_json(current.base / "recovery-error.json", {"errors": pending,
                    "at": datetime.now().astimezone().isoformat()})
        return False
    gate = current.base / GATE_FILE
    if gate.exists():
        try:
            check_path(gate, allow_missing=False)
            value = read_json(gate, {})
            if value.get("schema") != 1 or value.get("kind") != "paperpiggy" or value.get("operation") != "restore":
                raise BackupError("恢复门禁记录不完整，需核对后再启动。")
            rid = value.get("request_id") or ""
            request = current.base / "requests" / (rid + ".json")
            # 已有 journal 全部终结；或请求未落盘/前置核验失败，原目录尚未交换。
            if not re.fullmatch(r"[a-f0-9]{32}", rid) or not request.is_file():
                job_id = value.get("job_id") or ""
                if not re.fullmatch(r"[a-f0-9]{32}", job_id):
                    raise BackupError("恢复门禁任务标识无效。")
                try:
                    with file_lock(current.base / "locks" / (job_id + ".enqueue")):
                        if not request.is_file():
                            _clear_gate(current)
                except BackupBusy:
                    return True  # UI 仍在原子发布请求；不能提前解除门禁。
        except Exception:
            return False
    return True


def process_requests(current):
    from backup_jobs import BackupError, BackupBusy, check_path, read_json, file_lock
    if not recover_interrupted_restores(current):
        return False
    directory = current.base / "requests"
    if not directory.exists():
        return
    for file in sorted(directory.glob("*.json")):
        request = None
        try:
            check_path(file, allow_missing=False)
            if not re.fullmatch(r"[a-f0-9]{32}", file.stem) or file.stat().st_size > 262144:
                raise BackupError("后台请求标识或大小无效。")
            request = read_json(file, {})
            if request.get("schema") != 1 or request.get("request_id") != file.stem:
                raise BackupError("后台请求格式无效。")
            job = current.get(request["job_id"])
            if job.get("machine_id") != current.machine_id:
                continue  # 外机请求仅保留历史，不执行、不改名、不接管其来源。
            try:
                with file_lock(current.base / "locks" / (job["job_id"] + ".enqueue")):
                    state = current.status(job["job_id"])
            except BackupBusy:
                continue  # 请求已落盘，但 UI 还没发布其状态；下一tick处理完整请求。
            if state.get("request_id") != request["request_id"]:
                raise BackupError("请求已失效，未重复执行。")
            if request["operation"] == "backup" and state.get("cancel_requested"):
                current.finish(job["job_id"], "cancelled", "用户已停止排队备份；已有成功版本未改动。")
                current.set_status(job["job_id"], stage="已停止排队备份")
                file.unlink()
                continue
            options = request.get("options") or {}
            if request["operation"] == "restore" and _waiter_alive(options):
                current.set_status(job["job_id"], stage="等待退出小猪后进行原位恢复", pending=True)
                continue
            if request["operation"] == "backup":
                options = {k: v for k, v in options.items() if k in ("include_index", "include_key", "include_browser")}
                run_backup(current, {**job, **options}, request_id=request["request_id"])
            elif request["operation"] == "restore":
                run_restore(current, job, options, request_id=request["request_id"])
            else:
                raise BackupError("未知后台请求。")
            file.unlink()
        except Exception as exc:
            if request and request.get("job_id"):
                try:
                    current.finish(request["job_id"], "failed", str(exc))
                    current.set_status(request["job_id"], pending=False, stage="任务失败")
                except Exception:
                    pass
            rejected = file.with_suffix(".failed")
            try:
                if not rejected.exists():
                    file.rename(rejected)
            except OSError:
                pass
    return recover_interrupted_restores(current)


def active_paperpiggy_restore(data_dir=None):
    """启动器和MCP在任务待恢复/进行中拒绝新写入；只读，不创建任务。"""
    loaded = sys.modules.get("config")
    data = data_dir or getattr(loaded, "DATA", None) or os.environ.get("LOCALKB_DATA")
    if not data:
        raise ValueError("启动门禁必须传入实际数据目录，不能猜测资料归属。")
    base = Path(data) / "backup_jobs"
    if (base / GATE_FILE).exists():
        return True
    for path in (base / "restore_journals").glob("PaperPiggy-restore-*.json"):
        try:
            journal = json.loads(path.read_text(encoding="utf-8"))
            if journal.get("state") not in ("completed", "rolled_back") and any(
                    op.get("root_id", "").startswith("paperpiggy_") for op in journal.get("operations", [])):
                return True
        except (OSError, ValueError, TypeError, AttributeError):
            return True  # 不完整记录不能被误当作安全完成。
    jobs = base / "jobs.json"
    if not jobs.exists():
        return False
    try:
        values = json.loads(jobs.read_text(encoding="utf-8")).get("jobs", [])
    except (OSError, ValueError, AttributeError):
        return True
    for job in values:
        if job["kind"] != "paperpiggy":
            continue
        state_path = base / "status" / (job["job_id"] + ".json")
        try:
            state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        except (OSError, ValueError):
            return True
        if state.get("operation") == "restore" and state.get("running"):
            # 新目录验证无需退出，不作为原位门禁。
            rid = state.get("request_id") or ""
            request = base / "requests" / (rid + ".json")
            if re.fullmatch(r"[a-f0-9]{32}", rid) and request.is_file():
                try:
                    value = json.loads(request.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    return True
                if (value.get("options") or {}).get("mode") == "in_place":
                    return True
    return False


def _run_once(current, args):
    if args.login:
        current.note_login(args.login_id, datetime.now().astimezone())
    if process_requests(current) is False:
        return 1
    if active_paperpiggy_restore(args.data):
        return 0  # 等待应用退出；本轮不在待恢复数据上继续启动计划备份。
    now = datetime.now().astimezone()
    for job in current.list():
        if job.get("machine_id") != current.machine_id:
            continue
        due = current.due(job["job_id"], now=now,
                          trigger="login" if args.login else "timer", login_id=args.login_id)
        if not due.get("due"):
            continue
        try:
            run_backup(current, job, trigger="login" if args.login else "timer", slot=due.get("slot"))
        except Exception:
            continue  # claim 已持久记录；同一时刻不反复尝试。
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--tick", action="store_true")
    parser.add_argument("--login", action="store_true")
    parser.add_argument("--login-id", default="")
    args = parser.parse_args(argv)
    current = store(args.data)
    from backup_jobs import BackupBusy, file_lock
    try:
        with file_lock(current.base / "locks" / "runner"):
            return _run_once(current, args)
    except BackupBusy:
        return 0  # 登录补跑和定时触发重叠时复用当前 worker。


if __name__ == "__main__":
    raise SystemExit(main())

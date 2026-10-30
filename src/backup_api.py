"""独立备份任务的 HTTP 适配；所有文件业务由普通用户身份的后台执行器完成。"""
from datetime import datetime
import hashlib
import json
import math
import os
import threading
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import config as C
import backup_runner as RUN
import backup_sources as SOURCES
import backup_service as SERVICE


class Configure(BaseModel):
    kind: str
    patch: dict = Field(default_factory=dict)


class Operation(BaseModel):
    kind: str = "paperpiggy"
    path: str = ""
    mode: str = "new"
    destination: str = ""
    targets: dict = Field(default_factory=dict)
    confirm: bool = False
    browser_executable: str = ""
    include_index: bool | None = None
    include_key: bool | None = None
    include_browser: bool | None = None


def _context(kind=None):
    current = RUN.store(C.DATA)
    if kind is None:
        return current
    job = RUN.get_job(current, kind)
    if job is None:
        raise ValueError("未知备份任务类型。")
    return current, job


def _summary_timestamp(value):
    """Compare instants, never lexically compare timestamps with different offsets."""
    try:
        if type(value) in (int, float):
            result = float(value) / (1000 if abs(value) >= 1e12 else 1)
        elif isinstance(value, str) and value:
            result = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        else:
            return None
        return result if math.isfinite(result) else None
    except (ValueError, OverflowError, OSError):
        return None


def _summary_operation(event, context="unknown"):
    if event.get("operation") in ("backup", "restore"):
        return event["operation"]
    result = event.get("result") or {}
    if result.get("destination") or result.get("journal") or result.get("rollback_dirs"):
        return "restore"
    if result.get("path"):
        return "backup"
    if context in ("backup", "restore"):
        return context
    # A raw status can retain the previous operation if a newly claimed job fails
    # before setting operation. Explicit history context is therefore stronger.
    return event.get("_status_operation", "unknown")


def _summary_validate(state, history):
    """Broken-but-valid JSON is unknown, never a synthetic running/success state."""
    states = {"idle", "pending", "queued", "running", "started", "success", "success_with_warning",
              "failed", "interrupted", "cancelled"}
    if not isinstance(state, dict) or not isinstance(history, list):
        raise ValueError("备份任务状态或记录格式无效。")
    for record in [state, *history]:
        if not isinstance(record, dict):
            raise ValueError("备份任务记录格式无效。")
        for key in ("running", "pending", "queued"):
            if key in record and not isinstance(record[key], bool):
                raise ValueError("备份任务运行标记格式无效。")
        if "state" in record and record["state"] not in states:
            raise ValueError("备份任务状态值无效。")
        if "operation" in record and record["operation"] not in ("backup", "restore", "unknown"):
            raise ValueError("备份任务操作类型无效。")
        for key in ("stage", "error", "reason", "trigger"):
            if key in record and not isinstance(record[key], str):
                raise ValueError("备份任务文字记录格式无效。")
        for key in ("done", "total"):
            if key in record and (type(record[key]) is not int or record[key] < 0):
                raise ValueError("备份任务进度格式无效。")
        for key in ("at", "finished_at", "started_at", "updated_at", "last_success"):
            if key in record and record[key] != "" and _summary_timestamp(record[key]) is None:
                raise ValueError("备份任务时间格式无效。")
        result = record.get("result", {})
        if not isinstance(result, dict):
            raise ValueError("备份任务结果格式无效。")
        for key in ("path", "destination", "journal", "warning", "cleanup_warning"):
            if key in result and not isinstance(result[key], str):
                raise ValueError("备份任务结果文字格式无效。")
        for key in ("warnings", "cleanup_errors", "rollback_dirs"):
            if key in result and (not isinstance(result[key], list)
                                  or any(not isinstance(item, str) for item in result[key])):
                raise ValueError("备份任务提醒列表格式无效。")


def _summary_warning(result):
    messages = []
    for field in ("warning", "cleanup_warning", "cleanup_errors", "warnings"):
        value = result.get(field)
        if isinstance(value, list):
            messages.extend(str(item) for item in value if item)
        elif value:
            messages.append(str(value))
    return "；".join(dict.fromkeys(messages))


def _summary_issue(job_id, history, state):
    """Reconstruct unresolved outcomes without clearing a failed backup on retry/restore.

    Legacy terminal history has no operation; started.trigger provides its context.
    Unknown failures stay conservative until a later successful backup. Reading the
    status after history also covers finish()'s status-before-history write window.
    """
    events = [dict(event) for event in history]
    terminal = {"success", "success_with_warning", "failed", "interrupted", "cancelled"}
    current_state = state.get("state")
    if not (state.get("running") or state.get("pending") or state.get("queued")) and current_state in terminal:
        current = {"state": current_state, "error": state.get("error") or "",
                   "result": state.get("result") or {},
                   "_status_operation": state.get("operation", "unknown"),
                   "at": state.get("finished_at") or state.get("updated_at") or ""}
        previous = next((event for event in reversed(events) if event.get("state") in terminal), None)
        same = previous is not None and all(
            (previous.get(key) or ({} if key == "result" else "")) == current[key]
            for key in ("state", "error", "result"))
        old_at = _summary_timestamp(previous.get("at")) if previous else None
        current_at = _summary_timestamp(current["at"])
        if same and (current_at is None or old_at is not None and current_at <= old_at):
            # The current status can disambiguate the latest old-format event.
            if current["_status_operation"] in ("backup", "restore"):
                previous["_status_operation"] = current["_status_operation"]
            if current_at is not None:
                # finish() writes its status before appending history, possibly in
                # the next second. Keep one failure identity across that boundary.
                previous["at"] = current["at"]
        elif previous is None or (current_at is not None and (old_at is None or current_at >= old_at)):
            events.append(current)
        elif current_at is None and current_state in ("failed", "interrupted"):
            # Invalid times must never be used to erase a known failure with success.
            current["_status_operation"] = "unknown"
            events.append(current)

    unresolved = {}
    context = "unknown"
    attempt = None
    previous_terminal = None
    for sequence, event in enumerate(events):
        outcome = event.get("state")
        if outcome == "started":
            context = "restore" if event.get("trigger") == "restore" else (
                "backup" if event.get("trigger") in ("manual", "timer", "login") else "unknown")
            attempt = [event.get("at") or "", event.get("trigger") or "", event.get("slot")]
            continue
        operation = _summary_operation(event, context)
        event_attempt = attempt
        if outcome in terminal:
            if (event_attempt is None and previous_terminal is not None
                    and previous_terminal[2] is not None
                    and all(event.get(key) == previous_terminal[0].get(key)
                            for key in ("state", "error", "result"))):
                # Both claim() and the request worker can record the same failed
                # attempt. No intervening started event means one notification.
                operation, event_attempt = previous_terminal[1:]
            elif (operation == "unknown" and previous_terminal is not None
                    and all(event.get(key) == previous_terminal[0].get(key)
                            for key in ("state", "error", "result", "at"))):
                operation = previous_terminal[1]
                event_attempt = previous_terminal[2]
            previous_terminal = (event, operation, event_attempt)
            context = "unknown"
            attempt = None
        if outcome in ("success", "success_with_warning"):
            if operation == "unknown":
                continue  # An unclassified old success cannot prove a backup succeeded.
            unresolved.pop(operation, None)
            if operation == "backup":
                unresolved.pop("unknown", None)
            warning = _summary_warning(event.get("result") or {})
            if not warning:
                continue
            level, message = "warning", warning
        elif outcome in ("failed", "interrupted"):
            level = "failed"
            message = event.get("error") or event.get("reason") or (
                "上次任务意外中断，已有成功备份保留。" if outcome == "interrupted" else "上次任务失败，请查看任务记录。")
        else:
            continue  # Starting, retrying, and cancellation cannot dismiss an issue.
        at = event.get("at") or ""
        # finish() status and history may be written in different seconds. The
        # attempt's started event is immutable even after retrying clears status.
        # Old pre-claim failures without a started record retain their event time.
        identity_time = ["attempt", event_attempt] if event_attempt is not None else ["event", at]
        identity = json.dumps([job_id, operation, outcome, identity_time, message], ensure_ascii=False, sort_keys=True)
        unresolved[operation] = {"id": hashlib.sha256(identity.encode()).hexdigest(),
                                 "level": level, "message": str(message), "at": at,
                                 "operation": operation, "_sequence": sequence}
    if not unresolved:
        return None
    selected = max(unresolved.values(), key=lambda issue: (issue["level"] == "failed", issue["_sequence"]))
    return {key: value for key, value in selected.items() if not key.startswith("_")}


def _backup_summary():
    """Strictly read-only top-bar status: no defaults, scheduling, discovery, or update."""
    from backup_jobs import JobStore, validate_job
    current = JobStore(C.DATA / "backup_jobs")
    jobs = []
    for job in current.list():
        if not isinstance(job, dict):
            raise ValueError("备份任务配置格式无效。")
        if job.get("machine_id") != current.machine_id:
            continue
        validate_job(job)
        # Read history first so a subsequent status can cover an in-flight finish.
        history = current.history(job["job_id"], 1000)
        state = current.status(job["job_id"])
        _summary_validate(state, history)
        jobs.append({"kind": job["kind"], "job_id": job["job_id"],
                     "enabled": job["enabled"], "configured": bool(job.get("destination")),
                     "running": bool(state.get("running") or state.get("pending") or state.get("queued")),
                     "operation": state.get("operation", "backup"), "state": state.get("state", "idle"),
                     "stage": state.get("stage", ""), "done": state.get("done", 0),
                     "total": state.get("total", 0), "last_success": state.get("last_success", ""),
                     "issue": _summary_issue(job["job_id"], history, state)})
    return {"ok": True, "jobs": jobs}


def _service_info():
    """只读过滤已被受保护成功注册时间覆盖的普通端历史标记。"""
    info = SERVICE.install_status(C.DATA)
    if info.get("installed") is not True:
        return info
    try:
        from backup_jobs import read_json
        marker = read_json(C.DATA / "backup_work" / "component-install.json", {})
        pending_at = marker.get("started_at") if isinstance(marker, dict) else None
        installed_at = info.get("installed_at")
        if (type(pending_at) in (int, float) and type(installed_at) in (int, float)
                and math.isfinite(pending_at) and math.isfinite(installed_at)
                and 0 < pending_at <= installed_at):
            return {**info, "pending": False, "error": ""}
    except Exception:
        pass  # 缺失、损坏或无法确认归属时保留原状态，不隐藏真正错误。
    return info


def _component_status():
    """Only the signed, already-migrated channel may queue updates automatically."""
    info = _service_info()
    if info.get("auto_update_supported") and info.get("needs_update"):
        try:
            result = SERVICE.request_component_update(C.DATA)
            if result.get("ok") is False:
                raise ValueError(result.get("error") or "后台组件自动更新未能排队。")
            info = _service_info()
        except Exception as exc:
            info = {**info, "auto_update": {**(info.get("auto_update") or {}),
                    "state": "failed", "pending": False, "error": str(exc)}}
    return info


def _service_ready():
    info = _component_status()
    can_run = info.get("can_run", bool(info.get("installed")) and not info.get("needs_update")
                       and not info.get("pending") and not info.get("error"))
    if not can_run:
        raise ValueError(info.get("error") or
                         "请先在原生小猪窗口完成首次配置或一次性迁移；之后后台更新无需重复管理员确认。")
    return info


def _inspect(path):
    import backup_legacy
    import backup_archive
    if backup_legacy.format_of(path) == 1:
        return backup_legacy.inspect(path)
    value = backup_archive.inspect(path)
    return {"ok": True, **value, "legacy": False,
            "has_browser": value["manifest"].get("include_browser", False)}


def _queued(request):
    try:
        SERVICE.trigger_task(C.DATA)
    except Exception as exc:
        # 已登记请求不会谎称启动成功；保持排队，交已安装任务的下一分钟重试。
        return {"ok": True, "started": True, "queued": True,
                "request_id": request["request_id"],
                "warning": "请求已保存，正在等待后台任务：" + str(exc)}
    return {"ok": True, "started": True, "queued": True, "request_id": request["request_id"]}


def submit_restore(value, server):
    _service_ready()
    kind = value.get("kind", "paperpiggy")
    current, job = _context(kind)
    if value.get("mode") not in ("new", "in_place"):
        raise ValueError("请选择新目录验证或原位恢复。")
    if not value.get("confirm"):
        raise ValueError("恢复操作需要明确确认。")
    info = _inspect(value.get("path"))
    manifest = info["manifest"]
    if manifest["kind"] != kind:
        raise ValueError("所选备份类型与当前任务不同。")
    options = {k: value.get(k) for k in ("path", "mode", "destination", "targets", "confirm", "browser_executable")}
    if value["mode"] == "in_place":
        targets = value.get("targets") or {r["id"]: r["original_source"] for r in manifest["roots"]}
        if info.get("legacy"):
            targets = {r["id"]: str(C.DATA if r["id"] == "paperpiggy_data" else C.DATA.parent)
                       for r in manifest["roots"]}
        if set(targets) != {r["id"] for r in manifest["roots"]}:
            raise ValueError("请为备份中的全部来源设置恢复目标。")
        for path in targets.values():
            if not isinstance(path, str) or not Path(path).is_absolute():
                raise ValueError("恢复目标须为绝对路径。")
        SOURCES.ensure_restore_stopped(kind, targets)
        RUN._version_warnings(manifest, targets)
        if "browser" in targets:
            RUN._browser_program(manifest, value.get("browser_executable"))
        options["targets"] = targets
        if kind == "paperpiggy":
            busy = server._backup_busy_reason()
            if busy:
                raise ValueError(busy)
            runtime = getattr(server.R, "retrieval_status", None)
            if runtime and runtime().get("active"):
                raise ValueError("仍有检索进行中，请等查询结束后再原位恢复。")
            SOURCES.ensure_paperpiggy_mcp_stopped(C.APP)
            options.update({"wait_for_pid": os.getpid(), "wait_for_script": str(C.APP / "server.py")})
    else:
        target = Path(value.get("destination") or "")
        if not target.is_absolute() or target.exists() and (not target.is_dir() or any(target.iterdir())):
            raise ValueError("新目录恢复要求绝对路径，目标必须不存在或为空。")
    request = RUN.enqueue(kind, "restore", options, C.DATA)
    return {**_queued(request), "requires_exit": kind == "paperpiggy" and value["mode"] == "in_place"}


def register(app, server):
    from backup_jobs import BackupBusy

    def component_update_startup():
        # This path never invokes request_install/runas and never blocks launcher health.
        if not RUN.active_paperpiggy_restore(C.DATA):
            threading.Thread(target=_component_status, daemon=True,
                             name="paperpiggy-component-check").start()
    app.router.add_event_handler("startup", component_update_startup)

    @app.exception_handler(BackupBusy)
    async def maintenance_conflict(request: Request, exc):
        return JSONResponse({"ok": False, "detail": str(exc)}, status_code=409)

    @app.middleware("http")
    async def backup_guard(request: Request, call_next):
        if request.url.path.startswith("/backup2/") and request.method == "POST":
            origin = request.headers.get("origin")
            parsed = urlsplit(origin) if origin else None
            hosts = {"127.0.0.1", "localhost", C.DAEMON_HOST}
            if (request.headers.get("content-type", "").split(";")[0] != "application/json"
                or request.headers.get("sec-fetch-site") == "cross-site"
                or parsed and (parsed.scheme != "http" or parsed.hostname not in hosts
                               or parsed.port != request.url.port)):
                return JSONResponse({"ok": False, "error": "请在 PaperPiggy 备份与恢复页面操作。"}, status_code=403)
        # 确认后的PP原位恢复等待本server退出。门禁阻止MCP/页面继续写当前数据。
        allowed = ("/backup2/status", "/backup2/summary", "/backup/status", "/health", "/static/")
        if (not any(request.url.path.startswith(x) for x in allowed)
                and RUN.active_paperpiggy_restore(C.DATA)):
            return JSONResponse({"ok": False, "error": "小猪原位恢复已排队，请退出小猪，完成后再重新打开。"}, status_code=503)
        return await call_next(request)

    @app.get("/backup2/summary")
    def summary():
        try:
            return _backup_summary()
        except Exception as exc:
            return {"ok": False, "error": str(exc), "jobs": []}

    @app.get("/backup2/config")
    def config_get():
        try:
            current = _context()
            jobs = []
            all_jobs = current.list()
            foreign_count = sum(job.get("machine_id") != current.machine_id for job in all_jobs)
            for job in all_jobs:
                if job.get("machine_id") != current.machine_id:
                    continue
                state = current.status(job["job_id"])
                jobs.append({**job, "id": job["job_id"], "title": "Zotero 完整备份" if job["kind"] == "zotero" else "小猪与专用浏览器备份",
                             "status": state})
            notice = ("已保留其他电脑的 " + str(foreign_count) + " 个历史任务及备份记录，只显示本机任务。"
                      "换机后请重新选择本机来源、备份位置和计划，再确认启用；不会接管或清理旧电脑的备份。") if foreign_count else ""
            return {"ok": True, "jobs": jobs, "service": _component_status(),
                    "foreign_jobs_count": foreign_count, "notice": notice,
                    "discovered": {"zotero": SOURCES.discover_zotero(), "browser": SOURCES.discover_browser()}}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @app.post("/backup2/component/update")
    def component_update():
        try:
            info = _service_info()
            if not info.get("auto_update_supported"):
                raise ValueError("请先在原生小猪窗口完成首次配置或一次性迁移（管理员确认）。")
            result = SERVICE.request_component_update(C.DATA, retry=True)
            if result.get("ok") is False:
                raise ValueError(result.get("error") or "后台更新未能排队。")
            return {**result, "ok": True, "service": _service_info()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @app.post("/backup2/config")
    def config_set(q: Configure):
        try:
            current, job = _context(q.kind)
            patch = q.patch
            allowed = {"enabled", "destination", "sources", "every_days", "at_time", "catch_up", "keep", "include_index", "include_key", "include_browser"}
            if not set(patch) <= allowed:
                raise ValueError("备份设置含未知字段。")
            for field in ("enabled", "catch_up", "include_index", "include_key", "include_browser"):
                if field in patch and not isinstance(patch[field], bool):
                    raise ValueError("备份开关格式不正确。")
            if patch.get("enabled"):
                _service_ready()
            changed = {**job, **patch}
            if changed.get("destination"):
                from backup_jobs import check_path
                check_path(changed["destination"])
            if changed.get("enabled"):
                SOURCES.validate_sources(changed, C.DATA)
            saved = current.save(job["job_id"], patch)
            return {"ok": True, "job": saved}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @app.get("/backup2/status")
    def status(kind: str = "paperpiggy"):
        try:
            current, job = _context(kind)
            state = current.status(job["job_id"])
            due = current.due(job["job_id"])
            return {"ok": True, **state, "history": current.history(job["job_id"], 30),
                    "next_at": due.get("next_at", due.get("planned_at", "")),
                    "cancellable": state.get("operation") == "backup" and state.get("running", False)}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "running": False}

    @app.get("/backup2/list")
    def listing(kind: str = "paperpiggy"):
        try:
            current, job = _context(kind)
            items = []
            for entry in reversed(current.ledger(job["job_id"])):
                path = Path(entry["path"])
                items.append({**entry, "path": str(path), "name": path.name,
                              "created": entry.get("created_at", ""), "kind": kind,
                              "available": path.is_file()})
            return {"ok": True, "items": items, "dir": job["destination"]}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "items": []}

    @app.post("/backup2/create")
    def create(q: Operation):
        try:
            _service_ready()
            current, job = _context(q.kind)
            options = {k: getattr(q, k) for k in ("include_index", "include_key", "include_browser") if getattr(q, k) is not None}
            if q.kind == "paperpiggy" and server._backup_busy_reason():
                raise ValueError(server._backup_busy_reason())
            SOURCES.validate_sources({**job, **options}, C.DATA)
            return _queued(RUN.enqueue(q.kind, "backup", options, C.DATA))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @app.post("/backup2/cancel")
    def cancel(q: Operation):
        try:
            current, job = _context(q.kind)
            state = current.status(job["job_id"])
            if state.get("operation") != "backup":
                raise ValueError("恢复不能中途取消。")
            current.cancel(job["job_id"])
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @app.post("/backup2/inspect")
    def inspection(q: Operation):
        try:
            return _inspect(q.path)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @app.post("/backup2/restore")
    def restore(q: Operation):
        try:
            return submit_restore(q.dict(), server)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @app.post("/backup2/open_dir")
    def open_dir(q: Operation):
        try:
            _, job = _context(q.kind)
            if not job["destination"] or not Path(job["destination"]).is_dir():
                raise ValueError("请先选择已存在的备份位置。")
            if os.name == "nt":
                os.startfile(job["destination"])
            else:
                raise ValueError("请在文件管理器打开所选备份位置。")
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

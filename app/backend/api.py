"""传统后端 API：导入、监控周期、任务调度与数据库快照分析。"""

from __future__ import annotations

from datetime import datetime
from copy import deepcopy
from functools import lru_cache
from typing import Any, Literal

from pathlib import Path

from fastapi import APIRouter, File, Header, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.deps import ForbiddenError, get_trace_id
from app.api.envelope import ok
from app.backend.repository import RiskRepository
from app.backend.document_conversion import convert_docx
from app.backend.imports import save_upload, validate_event_csv, validate_supplier_csv
from app.backend.official_api import sync_source
from app.backend.mock_service import bootstrap_frontend_demo, mock_login
from app.data.loader import canonical_supplier_ids, load_supplier_dataset, normalize_supplier_id
from app.reporting.renderer import render_html, render_markdown
from app.config import get_settings
from app.service import analyze_supplier_snapshot


router = APIRouter(prefix="/backend", tags=["backend-foundation"])


@lru_cache
def _repository(database_url: str) -> RiskRepository:
    return RiskRepository(database_url)


def repository() -> RiskRepository:
    return _repository(get_settings().database_url)


class SupplierImportRequest(BaseModel):
    records: list[dict[str, Any]] = Field(min_length=1, max_length=1000)
    configured_by: str | None = Field(default=None, max_length=100)


class RiskEventImportRequest(BaseModel):
    records: list[dict[str, Any]] = Field(min_length=1, max_length=5000)
    source_type: str = Field(default="MANUAL", max_length=50)


class MonitorOverrideRequest(BaseModel):
    frequency: Literal["WEEKLY", "MONTHLY", "QUARTERLY", "CUSTOM"]
    next_due_at: datetime
    interval_days: int | None = Field(default=None, ge=1, le=365)
    configured_by: str | None = Field(default=None, max_length=100)


class DispatchRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=1000)
    analyze_now: bool = False
    current_week: int | None = Field(default=None, ge=1, le=52)
    enable_live_llm: bool = True


class AnalyzeMonitorRequest(BaseModel):
    current_week: int | None = Field(default=None, ge=1, le=52)
    enable_live_llm: bool = True
    window_unit: Literal["week", "month"] | None = None
    window_size: int | None = Field(default=None, ge=1, le=52)


class RefreshRiskStateRequest(AnalyzeMonitorRequest):
    supplier_ids: list[str] = Field(default_factory=list, max_length=1000)
    limit: int = Field(default=1000, ge=1, le=1000)
    input_source: Literal["AUTO", "DATABASE", "PROVIDED_DATA"] = "AUTO"
    # A full refresh can span hundreds of suppliers.  Keep deterministic
    # scoring/report generation as the safe default; callers may opt in.
    enable_live_llm: bool = False


class SourceRequest(BaseModel):
    source_code: str = Field(min_length=2, max_length=50)
    source_name: str = Field(min_length=2, max_length=100)
    base_url: str = Field(min_length=12, max_length=300)
    endpoint_path: str = Field(min_length=1, max_length=300)
    auth_env_var: str = Field(default="", max_length=100)
    auth_header: str = Field(default="Authorization", max_length=100)
    records_path: str | None = Field(default=None, max_length=200)
    query_template: dict[str, Any] = Field(default_factory=dict)
    field_mapping: dict[str, str] = Field(default_factory=dict)
    access_mode: Literal["API", "MANUAL_WEB", "MOCK"] = "API"
    live_http_enabled: bool = False
    enabled: bool = True


class SourceRuntimeRequest(BaseModel):
    live_http_enabled: bool


class CollectionTaskRequest(BaseModel):
    source_code: str = Field(min_length=2, max_length=50)
    supplier_id: str = Field(min_length=1, max_length=100)
    query: dict[str, Any] = Field(default_factory=dict)
    assigned_to: str | None = Field(default=None, max_length=100)


class CollectionSubmissionRequest(BaseModel):
    submitted_by: str = Field(min_length=1, max_length=100)
    source_url: str = Field(min_length=8, max_length=1000)
    result: dict[str, Any]
    file_id: str | None = Field(default=None, max_length=100)


class CollectionReviewRequest(BaseModel):
    approved: bool
    review_comment: str | None = Field(default=None, max_length=2000)


class RoleRequest(BaseModel):
    role_code: str = Field(min_length=2, max_length=50)
    role_name: str = Field(min_length=2, max_length=100)
    level: int = Field(ge=1, le=100)
    permissions: list[str] = Field(default_factory=list)
    enabled: bool = True


class UserRequest(BaseModel):
    user_id: str | None = Field(default=None, max_length=100)
    username: str = Field(min_length=2, max_length=100)
    display_name: str = Field(min_length=1, max_length=100)
    status: Literal["ACTIVE", "DISABLED"] = "ACTIVE"


class UserRolesRequest(BaseModel):
    role_ids: list[str] = Field(default_factory=list)


class UserSupplierScopeRequest(BaseModel):
    supplier_ids: list[str] = Field(default_factory=list)


class ReviewRecordRequest(BaseModel):
    report_id: str = Field(min_length=1, max_length=100)
    reviewer_id: str = Field(min_length=1, max_length=100)
    review_result: Literal["APPROVED", "REJECTED", "NEED_MORE_EVIDENCE", "REDECLARED"]
    final_action: str | None = Field(default=None, max_length=30)
    review_comment: str | None = Field(default=None, max_length=3000)
    confirmed_risk_level: Literal["RED", "YELLOW", "GREEN"] | None = None


class NotificationRuleRequest(BaseModel):
    risk_level: Literal["RED", "YELLOW", "GREEN"]
    notification_type: Literal["RISK_ALERT", "REPORT", "TASK_REMINDER"]
    role_id: str = Field(min_length=1, max_length=100)
    channel: Literal["WEB", "EMAIL", "WECHAT"]
    enabled: bool = True


class RiskEventStatusRequest(BaseModel):
    status: Literal["ACTIVE", "RESOLVED", "REVOKED"]


class RedeclareCreateRequest(BaseModel):
    supplier_id: str = Field(min_length=1, max_length=100)
    report_id: str | None = Field(default=None, max_length=100)
    submitted_by: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=3000)
    attachment_refs: list[str] = Field(default_factory=list, max_length=50)


class RedeclareReviewRequest(BaseModel):
    status: Literal["PROCESSING", "APPROVED", "REJECTED", "CANCELLED"]
    review_comment: str | None = Field(default=None, max_length=3000)


class MockLoginRequest(BaseModel):
    username: str = Field(min_length=2, max_length=100)


def _authorize(raw: Request, permission: str, supplier_id: str | None = None) -> str | None:
    """Enforce RBAC only after the deployment turns RBAC_ENFORCED on."""
    if not get_settings().rbac_enforced:
        return raw.headers.get("X-User-Id")
    user_id = (raw.headers.get("X-User-Id") or "").strip()
    if not user_id:
        raise ForbiddenError("X-User-Id from the authenticated gateway is required")
    try:
        repository().authorize(user_id, permission, supplier_id)
    except ValueError as exc:
        raise ForbiddenError(str(exc)) from exc
    return user_id


def _ensure_mock_mode() -> None:
    settings = get_settings()
    if not settings.integration_mock_mode or settings.app_env.strip().lower() in {"production", "prod"}:
        raise ForbiddenError("frontend integration mock mode is disabled")


def _project_report_for_user(raw: Request, report_row: dict[str, Any]) -> dict[str, Any]:
    """Remove fields the authenticated user's selected permissions do not allow."""
    projected = deepcopy(report_row)
    user_id = _authorize(raw, "report.read", report_row["supplier_id"])
    if not get_settings().rbac_enforced or not user_id:
        return projected
    permissions = set(repository().access_profile(user_id)["permissions"])
    report = projected["report"]
    if "report.comparison.read" not in permissions:
        report["cross_supplier_comparison"] = None
        report["cross_supplier_comparison_withheld"] = True
    if "report.evidence.read" not in permissions:
        report["evidence_summary"] = "证据明细因权限未下发"
        report["evidence_by_dimension"] = {}
        report["evidence_withheld"] = True
    if "audit.read" not in permissions:
        report.pop("audit_trace", None)
        report.pop("errors", None)
    projected["granted_permissions"] = sorted(code for code in permissions if code.startswith("report."))
    return projected


@router.get("/suppliers", summary="查询持久化供应商")
def list_suppliers(raw: Request) -> Any:
    user_id = _authorize(raw, "supplier.read")
    records = repository().list_suppliers()
    if get_settings().rbac_enforced and user_id:
        scope = repository().access_profile(user_id)["supplier_scope"]
        if scope:
            records = [item for item in records if item["supplier_id"] in set(scope)]
    return ok({"records": records}, get_trace_id(raw))


@router.get("/suppliers/{supplier_id}", summary="查询供应商详情")
def get_supplier(supplier_id: str, raw: Request) -> Any:
    _authorize(raw, "supplier.read", supplier_id)
    return ok(repository().get_supplier(supplier_id), get_trace_id(raw))


@router.get("/suppliers/{supplier_id}/risk-events", summary="查询供应商风险事件")
def list_supplier_risk_events(supplier_id: str, raw: Request, status: Literal["ACTIVE", "RESOLVED", "REVOKED"] | None = Query(default="ACTIVE"), limit: int = Query(default=200, ge=1, le=1000)) -> Any:
    _authorize(raw, "risk_event.read", supplier_id)
    return ok({"records": repository().list_risk_events(supplier_id, status=status, limit=limit)}, get_trace_id(raw))


@router.patch("/risk-events/{risk_event_id}/status", summary="关闭、恢复或撤销风险事件")
def update_risk_event_status(risk_event_id: str, request: RiskEventStatusRequest, raw: Request) -> Any:
    current = repository().get_risk_event(risk_event_id)
    actor = _authorize(raw, "risk_event.import", current["supplier_id"])
    result = repository().set_risk_event_status(risk_event_id, request.status)
    repository().audit(actor_user_id=actor, action="SET_RISK_EVENT_STATUS", resource_type="RISK_EVENT", resource_id=risk_event_id, trace_id=get_trace_id(raw), after={"status": request.status})
    return ok(result, get_trace_id(raw))


@router.get("/suppliers/{supplier_id}/reports", summary="查询供应商历史报告")
def list_supplier_reports(supplier_id: str, raw: Request, limit: int = Query(default=50, ge=1, le=500)) -> Any:
    _authorize(raw, "report.read", supplier_id)
    return ok({"records": repository().list_supplier_reports(supplier_id, limit)}, get_trace_id(raw))


@router.post("/suppliers/import", summary="批量导入供应商并分配默认监控策略")
def import_suppliers(request: SupplierImportRequest, raw: Request) -> Any:
    _authorize(raw, "supplier.write")
    payload = repository().import_suppliers(request.records, configured_by=request.configured_by)
    return ok(payload, get_trace_id(raw))


@router.post("/risk-events/import", summary="批量导入风险事件和证据")
def import_risk_events(request: RiskEventImportRequest, raw: Request) -> Any:
    _authorize(raw, "risk_event.import")
    payload = repository().import_risk_events(request.records, source_type=request.source_type)
    return ok(payload, get_trace_id(raw))


async def _csv_import(file: UploadFile, *, kind: str, confirm: bool) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise ValueError("only .csv files are accepted")
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise ValueError("CSV file must not exceed 10 MB")
    repo = repository()
    file_id = save_upload(repo, content, file.filename, file.content_type, f"{kind}_CSV", get_settings().upload_dir)
    valid, errors = (validate_supplier_csv(content) if kind == "SUPPLIER" else validate_event_csv(content))
    result: dict[str, Any] = {"file_id": file_id, "preview": not confirm, "valid_count": len(valid), "error_count": len(errors), "errors": errors[:200]}
    if confirm and not errors:
        result["import_result"] = repo.import_suppliers(valid) if kind == "SUPPLIER" else repo.import_risk_events(valid, source_type="CSV")
    elif confirm:
        result["import_result"] = None
        result["message"] = "validation errors must be fixed before import"
    return result


@router.post("/imports/suppliers/csv", summary="预检或确认导入供应商 CSV")
async def import_suppliers_csv(raw: Request, file: UploadFile = File(...), confirm: bool = Query(default=False)) -> Any:
    _authorize(raw, "supplier.write")
    return ok(await _csv_import(file, kind="SUPPLIER", confirm=confirm), get_trace_id(raw))


@router.post("/imports/risk-events/csv", summary="预检或确认导入风险事件 CSV")
async def import_events_csv(raw: Request, file: UploadFile = File(...), confirm: bool = Query(default=False)) -> Any:
    _authorize(raw, "risk_event.import")
    return ok(await _csv_import(file, kind="RISK_EVENT", confirm=confirm), get_trace_id(raw))


@router.post("/documents/convert", summary="将 DOCX 转成 TXT、Markdown 或 HTML")
async def convert_document(raw: Request, file: UploadFile = File(...), target_format: str = Query(default="markdown", pattern="^(txt|markdown|html)$")) -> Any:
    _authorize(raw, "file.convert")
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise ValueError("built-in conversion currently accepts .docx only; .doc/PDF need a controlled deployment converter")
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise ValueError("DOCX file must not exceed 20 MB")
    repo = repository()
    source_id = save_upload(repo, content, file.filename, file.content_type, "SOURCE_DOCX", get_settings().upload_dir)
    converted, extension, content_type = convert_docx(content, target_format)
    output_name = f"{Path(file.filename).stem}{extension}"
    output_id = save_upload(repo, converted, output_name, content_type, "CONVERTED_DOCUMENT", get_settings().upload_dir)
    return ok({"source_file_id": source_id, "converted_file_id": output_id, "download_url": f"/backend/files/{output_id}", "target_format": target_format}, get_trace_id(raw))


@router.get("/files/{file_id}", summary="下载已存储的导入或转换文件")
def download_file(file_id: str, raw: Request) -> Any:
    record = repository().get_file_object(file_id)
    _authorize(raw, "report.export" if record["purpose"] == "RISK_REPORT_EXPORT" else "file.read")
    path = Path(record["stored_path"])
    if not path.is_file():
        raise ValueError(f"stored file is missing: {file_id}")
    return FileResponse(path, media_type=record["content_type"] or "application/octet-stream", filename=record["original_filename"])


@router.get("/import-templates/{template_type}", summary="下载银行 CSV 填写模板")
def download_import_template(template_type: Literal["supplier", "risk-event"], raw: Request) -> Any:
    _authorize(raw, "supplier.read")
    filename = "supplier_template.csv" if template_type == "supplier" else "risk_event_template.csv"
    path = Path("templates/import") / filename
    if not path.is_file():
        raise ValueError(f"template is missing: {template_type}")
    return FileResponse(path.resolve(), media_type="text/csv; charset=utf-8", filename=filename)


@router.get("/data-sources", summary="查询已注册的官方授权数据源")
def list_sources(raw: Request) -> Any:
    _authorize(raw, "source.manage")
    return ok({"records": repository().list_sources()}, get_trace_id(raw))


@router.post("/data-sources", summary="注册官方授权 API 数据源配置")
def register_source(request: SourceRequest, raw: Request) -> Any:
    _authorize(raw, "source.manage")
    return ok(repository().register_source(request.model_dump()), get_trace_id(raw))


@router.post("/data-sources/{source_code}/sync/{supplier_id}", summary="从官方授权 API 同步一户供应商风险事件")
def sync_official_source(source_code: str, supplier_id: str, raw: Request) -> Any:
    _authorize(raw, "source.sync", supplier_id)
    return ok(sync_source(repository(), source_code, supplier_id), get_trace_id(raw))


@router.put("/data-sources/{source_code}/live-http", summary="显式开启或关闭数据源真实 HTTP")
def set_source_live_http(source_code: str, request: SourceRuntimeRequest, raw: Request) -> Any:
    actor = _authorize(raw, "source.manage")
    result = repository().set_source_live_http(source_code, request.live_http_enabled)
    repository().audit(actor_user_id=actor, action="SET_SOURCE_LIVE_HTTP", resource_type="SOURCE", resource_id=source_code.upper(), trace_id=get_trace_id(raw), after={"live_http_enabled": request.live_http_enabled})
    return ok(result, get_trace_id(raw))


@router.post("/collection-tasks", summary="为无 API 的公开网站创建人工辅助采集任务")
def create_collection_task(request: CollectionTaskRequest, raw: Request) -> Any:
    actor = _authorize(raw, "source.sync", request.supplier_id)
    result = repository().create_collection_task(**request.model_dump())
    repository().audit(actor_user_id=actor, action="CREATE_COLLECTION_TASK", resource_type="COLLECTION_TASK", resource_id=result["collection_task_id"], trace_id=get_trace_id(raw), after=result)
    return ok(result, get_trace_id(raw))


@router.get("/collection-tasks", summary="查询人工辅助采集任务")
def list_collection_tasks(raw: Request, status: str | None = Query(default=None), limit: int = Query(default=100, ge=1, le=1000)) -> Any:
    _authorize(raw, "source.sync")
    return ok({"records": repository().list_collection_tasks(status=status, limit=limit)}, get_trace_id(raw))


@router.post("/collection-tasks/{task_id}/submit", summary="提交人工查询结果和原始证据引用")
def submit_collection_task(task_id: str, request: CollectionSubmissionRequest, raw: Request) -> Any:
    task = repository().get_collection_task(task_id)
    actor = _authorize(raw, "source.sync", task["supplier_id"])
    result = repository().submit_collection_task(task_id, **request.model_dump())
    repository().audit(actor_user_id=actor or request.submitted_by, action="SUBMIT_COLLECTION_TASK", resource_type="COLLECTION_TASK", resource_id=task_id, trace_id=get_trace_id(raw), after=result)
    return ok(result, get_trace_id(raw))


@router.post("/collection-tasks/{task_id}/review", summary="复核人工采集结果")
def review_collection_task(task_id: str, request: CollectionReviewRequest, raw: Request) -> Any:
    task = repository().get_collection_task(task_id)
    actor = _authorize(raw, "source.manage", task["supplier_id"])
    result = repository().review_collection_task(task_id, **request.model_dump())
    repository().audit(actor_user_id=actor, action="REVIEW_COLLECTION_TASK", resource_type="COLLECTION_TASK", resource_id=task_id, trace_id=get_trace_id(raw), after={"approved": request.approved})
    return ok(result, get_trace_id(raw))


@router.put("/suppliers/{supplier_id}/monitoring", summary="为供应商覆盖默认监控周期")
def override_monitoring(supplier_id: str, request: MonitorOverrideRequest, raw: Request) -> Any:
    _authorize(raw, "monitor.manage", supplier_id)
    payload = repository().set_monitor_override(
        supplier_id,
        frequency=request.frequency,
        interval_days=request.interval_days,
        next_due_at=request.next_due_at,
        configured_by=request.configured_by,
    )
    return ok(payload, get_trace_id(raw))


def _analyze_monitor(monitor_id: str, request: AnalyzeMonitorRequest) -> dict[str, Any]:
    repo = repository()
    task, snapshot = repo.snapshot_for_monitor(monitor_id)
    analysis_run_id, run_id = repo.create_analysis_run(monitor_id, snapshot)
    try:
        state = analyze_supplier_snapshot(
            supplier_id=task["supplier_id"],
            snapshot=snapshot,
            current_week=request.current_week,
            enable_live_llm=request.enable_live_llm,
            run_id=run_id,
            window_unit=request.window_unit,
            window_size=request.window_size,
        )
        identifiers = repo.persist_analysis(analysis_run_id, state)
        return {"monitor_id": monitor_id, "run_id": run_id, **identifiers, "report": state["risk_report"]}
    except Exception as exc:
        repo.mark_analysis_failed(analysis_run_id, exc)
        raise


def _provided_data_snapshot(supplier_id: str) -> dict[str, Any] | None:
    """Build a snapshot from the repository-provided integration dataset."""
    normalized_id = normalize_supplier_id(supplier_id)
    resolved = canonical_supplier_ids().get(normalized_id)
    if resolved is None:
        return None
    dataset = load_supplier_dataset()
    profile = dict(dataset["profile_by_supplier"].get(resolved, {}))
    profile["supplier_id"] = normalized_id
    events = [dict(item) for item in dataset["events_by_supplier"].get(resolved, [])]
    for event in events:
        event["supplier_id"] = normalized_id
    return {
        "supplier_profile": profile,
        "events": events,
        "rectifies": list(dataset["rectifies_by_supplier"].get(resolved, [])),
        "weekly": dict(dataset["weekly_by_supplier"].get(resolved, {})),
        "max_week": int(dataset["max_week"]),
    }


def _analyze_refresh_task(
    task: dict[str, Any], request: RefreshRiskStateRequest
) -> tuple[dict[str, Any], str]:
    repo = repository()
    db_task, database_snapshot = repo.snapshot_for_monitor(task["monitor_id"])
    provided_snapshot = None
    if request.input_source in {"AUTO", "PROVIDED_DATA"}:
        provided_snapshot = _provided_data_snapshot(task["supplier_id"])
    if request.input_source == "PROVIDED_DATA" and provided_snapshot is None:
        raise ValueError(f"supplier is not present in provided source data: {task['supplier_id']}")
    use_provided = provided_snapshot is not None and (
        request.input_source == "PROVIDED_DATA"
        or (request.input_source == "AUTO" and not database_snapshot.get("events"))
    )
    snapshot = provided_snapshot if use_provided else database_snapshot
    input_source = "PROVIDED_DATA" if use_provided else "DATABASE"
    analysis_run_id, run_id = repo.create_analysis_run(task["monitor_id"], snapshot)
    try:
        state = analyze_supplier_snapshot(
            supplier_id=db_task["supplier_id"],
            snapshot=snapshot,
            current_week=request.current_week,
            enable_live_llm=request.enable_live_llm,
            run_id=run_id,
            window_unit=request.window_unit,
            window_size=request.window_size,
        )
        identifiers = repo.persist_analysis(analysis_run_id, state)
        return {
            "monitor_id": task["monitor_id"],
            "run_id": run_id,
            **identifiers,
            "report": state["risk_report"],
        }, input_source
    except Exception as exc:
        repo.mark_analysis_failed(analysis_run_id, exc)
        raise


@router.post("/monitoring/dispatch-due", summary="生成到期监控任务，可选择立即分析")
def dispatch_due_monitoring(request: DispatchRequest, raw: Request) -> Any:
    _authorize(raw, "monitor.run")
    tasks = repository().dispatch_due_monitoring(limit=request.limit)
    result: dict[str, Any] = {"created_count": len(tasks), "tasks": tasks}
    if request.analyze_now:
        analyze_request = AnalyzeMonitorRequest(
            current_week=request.current_week,
            enable_live_llm=request.enable_live_llm,
        )
        result["analyses"] = [_analyze_monitor(item["monitor_id"], analyze_request) for item in tasks]
    return ok(result, get_trace_id(raw))


@router.get("/monitoring/tasks", summary="查询持久化监控任务")
def list_monitor_tasks(raw: Request) -> Any:
    _authorize(raw, "monitor.run")
    return ok({"records": repository().list_monitor_tasks()}, get_trace_id(raw))


@router.post("/monitoring/tasks/{monitor_id}/analyze", summary="用数据库快照执行一项监控分析")
def analyze_monitor(monitor_id: str, request: AnalyzeMonitorRequest, raw: Request) -> Any:
    _authorize(raw, "monitor.run")
    return ok(_analyze_monitor(monitor_id, request), get_trace_id(raw))


@router.post("/monitoring/refresh-risk-state", summary="全量或按供应商刷新数据库风险状态")
def refresh_risk_state(request: RefreshRiskStateRequest, raw: Request) -> Any:
    """Run one-off analyses and persist levels used by the dashboard.

    Unlike periodic dispatch, this endpoint does not advance weekly/monthly
    schedules.  Failures are isolated per supplier so a bad record does not
    discard successful refreshes for the rest of the batch.
    """
    _authorize(raw, "monitor.run")
    tasks = repository().create_manual_monitoring_tasks(
        supplier_ids=request.supplier_ids or None,
        limit=request.limit,
    )
    successes: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for task in tasks:
        try:
            result, input_source = _analyze_refresh_task(task, request)
            successes.append(
                {
                    "supplier_id": task["supplier_id"],
                    "monitor_id": task["monitor_id"],
                    "report_id": result["report_id"],
                    "risk_level": result["report"]["risk_grade"]["risk_level"],
                    "risk_score": result["report"]["risk_grade"]["score"],
                    "input_source": input_source,
                }
            )
        except Exception as exc:
            failures.append({"supplier_id": task["supplier_id"], "monitor_id": task["monitor_id"], "error": str(exc)})
    return ok(
        {
            "requested_count": len(tasks),
            "success_count": len(successes),
            "failure_count": len(failures),
            "analyses": successes,
            "failures": failures,
        },
        get_trace_id(raw),
    )


@router.get("/reports/{report_id}", summary="查询持久化风险报告")
def get_report(report_id: str, raw: Request) -> Any:
    report = repository().get_report(report_id)
    return ok(_project_report_for_user(raw, report), get_trace_id(raw))


@router.get("/reports/{report_id}/visualization", summary="返回六维分布和历史趋势的前端图表数据")
def get_report_visualization(report_id: str, raw: Request) -> Any:
    report = repository().get_report(report_id)
    _authorize(raw, "report.read", report["supplier_id"])
    return ok(repository().report_visualization(report_id), get_trace_id(raw))


@router.post("/reports/{report_id}/export", summary="导出权限裁剪后的 Markdown 或 HTML 报告")
def export_report(report_id: str, raw: Request, target_format: Literal["markdown", "html"] = Query(default="html")) -> Any:
    report_row = repository().get_report(report_id)
    user_id = _authorize(raw, "report.export", report_row["supplier_id"])
    projected = _project_report_for_user(raw, report_row)["report"]
    if target_format == "html":
        content, suffix, content_type = render_html(projected).encode("utf-8"), ".html", "text/html; charset=utf-8"
    else:
        content, suffix, content_type = render_markdown(projected).encode("utf-8"), ".md", "text/markdown; charset=utf-8"
    filename = f"risk_report_{report_row['supplier_id']}_{report_id}{suffix}"
    file_id = save_upload(repository(), content, filename, content_type, "RISK_REPORT_EXPORT", get_settings().upload_dir)
    repository().audit(actor_user_id=user_id, action="EXPORT_REPORT", resource_type="REPORT", resource_id=report_id, trace_id=get_trace_id(raw), after={"file_id": file_id, "target_format": target_format})
    return ok({"file_id": file_id, "download_url": f"/backend/files/{file_id}", "target_format": target_format}, get_trace_id(raw))


# --------------------------------------------------------------------------- #
# RBAC: permissions are selectable on roles; users receive one or more roles
# plus an optional supplier data scope.  SSO/JWT remains the identity source.
# --------------------------------------------------------------------------- #
@router.get("/rbac/permissions", summary="列出全部可勾选权限")
def list_permissions(raw: Request) -> Any:
    _authorize(raw, "role.manage")
    return ok({"records": repository().list_permissions()}, get_trace_id(raw))


@router.get("/rbac/roles", summary="列出角色、级别和已勾选权限")
def list_roles(raw: Request) -> Any:
    _authorize(raw, "role.manage")
    return ok({"records": repository().list_roles()}, get_trace_id(raw))


@router.post("/rbac/roles", summary="创建或更新自定义角色")
def upsert_role(request: RoleRequest, raw: Request) -> Any:
    actor = _authorize(raw, "role.manage")
    result = repository().upsert_role(request.model_dump())
    repository().audit(actor_user_id=actor, action="UPSERT_ROLE", resource_type="ROLE", resource_id=result["role_id"], trace_id=get_trace_id(raw), after=result)
    return ok(result, get_trace_id(raw))


@router.get("/rbac/users", summary="列出用户、角色和供应商数据范围")
def list_users(raw: Request) -> Any:
    _authorize(raw, "user.manage")
    return ok({"records": repository().list_users()}, get_trace_id(raw))


@router.post("/rbac/users", summary="创建或更新用户目录记录")
def upsert_user(request: UserRequest, raw: Request) -> Any:
    actor = _authorize(raw, "user.manage")
    result = repository().upsert_user(request.model_dump())
    repository().audit(actor_user_id=actor, action="UPSERT_USER", resource_type="USER", resource_id=result["user_id"], trace_id=get_trace_id(raw), after=result)
    return ok(result, get_trace_id(raw))


@router.put("/rbac/users/{user_id}/roles", summary="勾选用户拥有的角色")
def set_user_roles(user_id: str, request: UserRolesRequest, raw: Request) -> Any:
    actor = _authorize(raw, "user.manage")
    result = repository().set_user_roles(user_id, request.role_ids)
    repository().audit(actor_user_id=actor, action="SET_USER_ROLES", resource_type="USER", resource_id=user_id, trace_id=get_trace_id(raw), after={"role_ids": request.role_ids})
    return ok(result, get_trace_id(raw))


@router.put("/rbac/users/{user_id}/supplier-scope", summary="设置用户可见的供应商范围")
def set_user_supplier_scope(user_id: str, request: UserSupplierScopeRequest, raw: Request) -> Any:
    actor = _authorize(raw, "user.manage")
    result = repository().set_user_supplier_scope(user_id, request.supplier_ids)
    repository().audit(actor_user_id=actor, action="SET_SUPPLIER_SCOPE", resource_type="USER", resource_id=user_id, trace_id=get_trace_id(raw), after={"supplier_ids": request.supplier_ids})
    return ok(result, get_trace_id(raw))


@router.get("/rbac/me", summary="返回认证用户的有效权限、级别和数据范围")
def my_access(raw: Request, x_user_id: str | None = Header(default=None, alias="X-User-Id")) -> Any:
    user_id = x_user_id or _authorize(raw, "report.read")
    if not user_id:
        raise ForbiddenError("X-User-Id is required")
    return ok(repository().access_profile(user_id), get_trace_id(raw))


@router.get("/audit-logs", summary="查询后台配置与授权审计日志")
def list_audit_logs(raw: Request, limit: int = Query(default=100, ge=1, le=1000)) -> Any:
    _authorize(raw, "audit.read")
    return ok({"records": repository().list_audit(limit)}, get_trace_id(raw))


@router.post("/reviews", summary="将人工复核结论持久化到数据库")
def submit_database_review(request: ReviewRecordRequest, raw: Request) -> Any:
    report = repository().get_report(request.report_id)
    _authorize(raw, "review.submit", report["supplier_id"])
    result = repository().submit_review(request.model_dump())
    repository().audit(actor_user_id=request.reviewer_id, action="SUBMIT_REVIEW", resource_type="REPORT", resource_id=request.report_id, trace_id=get_trace_id(raw), after=result)
    return ok(result, get_trace_id(raw))


@router.get("/reviews", summary="查询已持久化的人工复核记录")
def list_database_reviews(raw: Request, report_id: str | None = Query(default=None), limit: int = Query(default=100, ge=1, le=1000)) -> Any:
    if report_id:
        report = repository().get_report(report_id)
        _authorize(raw, "review.submit", report["supplier_id"])
    else:
        _authorize(raw, "review.decide")
    return ok({"records": repository().list_reviews(report_id, limit)}, get_trace_id(raw))


@router.post("/redeclares", summary="提交供应商重新申报")
def submit_redeclare(request: RedeclareCreateRequest, raw: Request) -> Any:
    actor = _authorize(raw, "review.submit", request.supplier_id)
    result = repository().submit_redeclare(request.model_dump())
    repository().audit(actor_user_id=actor or request.submitted_by, action="SUBMIT_REDECLARE", resource_type="REDECLARE", resource_id=result["redeclare_request_id"], trace_id=get_trace_id(raw), after=result)
    return ok(result, get_trace_id(raw))


@router.get("/redeclares", summary="查询供应商重新申报")
def list_redeclares(raw: Request, supplier_id: str | None = Query(default=None), status: str | None = Query(default=None), limit: int = Query(default=100, ge=1, le=1000)) -> Any:
    _authorize(raw, "review.decide", supplier_id)
    return ok({"records": repository().list_redeclares(supplier_id=supplier_id, status=status, limit=limit)}, get_trace_id(raw))


@router.patch("/redeclares/{request_id}", summary="受理或审核供应商重新申报")
def review_redeclare(request_id: str, request: RedeclareReviewRequest, raw: Request) -> Any:
    current = repository().get_redeclare(request_id)
    actor = _authorize(raw, "review.decide", current["supplier_id"])
    result = repository().review_redeclare(request_id, **request.model_dump())
    repository().audit(actor_user_id=actor, action="REVIEW_REDECLARE", resource_type="REDECLARE", resource_id=request_id, trace_id=get_trace_id(raw), after=result)
    return ok(result, get_trace_id(raw))


@router.get("/notification-rules", summary="列出风险通知规则")
def list_notification_rules(raw: Request) -> Any:
    _authorize(raw, "notification.manage")
    return ok({"records": repository().list_notification_rules()}, get_trace_id(raw))


@router.post("/notification-rules", summary="配置风险等级到角色和渠道的通知规则")
def upsert_notification_rule(request: NotificationRuleRequest, raw: Request) -> Any:
    actor = _authorize(raw, "notification.manage")
    result = repository().upsert_notification_rule(request.model_dump())
    repository().audit(actor_user_id=actor, action="UPSERT_NOTIFICATION_RULE", resource_type="NOTIFICATION_RULE", resource_id=result["notification_rule_id"], trace_id=get_trace_id(raw), after=result)
    return ok(result, get_trace_id(raw))


@router.get("/notifications", summary="查询当前用户站内通知")
def list_notifications(raw: Request, x_user_id: str | None = Header(default=None, alias="X-User-Id"), status: Literal["UNREAD", "READ", "FAILED"] | None = Query(default=None), limit: int = Query(default=100, ge=1, le=500)) -> Any:
    user_id = (x_user_id or "").strip()
    if not user_id:
        raise ForbiddenError("X-User-Id is required")
    if get_settings().rbac_enforced:
        repository().access_profile(user_id)
    return ok({"records": repository().list_notifications(user_id, status=status, limit=limit)}, get_trace_id(raw))


@router.patch("/notifications/{notification_id}/read", summary="标记当前用户站内通知为已读")
def mark_notification_read(notification_id: str, raw: Request, x_user_id: str | None = Header(default=None, alias="X-User-Id")) -> Any:
    user_id = (x_user_id or "").strip()
    if not user_id:
        raise ForbiddenError("X-User-Id is required")
    return ok(repository().mark_notification_read(notification_id, user_id), get_trace_id(raw))


@router.get("/capabilities", summary="查询后端能力和运行配置状态")
def backend_capabilities(raw: Request) -> Any:
    settings = get_settings()
    return ok(
        {
            "prediction_enabled": False,
            "live_llm_default": True,
            "live_llm_configured": bool(settings.llm_model and settings.llm_base_url and settings.llm_api_key),
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model or None,
            "llm_fallback_enabled": True,
            "database_backend": "sqlite" if settings.database_url.startswith("sqlite:///") else "postgresql",
            "rbac_enforced": settings.rbac_enforced,
            "visualizations": ["SIX_DIMENSION_RADAR", "RISK_TREND"],
            "csv_imports": ["SUPPLIER", "RISK_EVENT"],
            "document_conversion": {"input": ["docx"], "output": ["txt", "markdown", "html"]},
            "source_modes": ["API", "MANUAL_WEB", "MOCK"],
        },
        get_trace_id(raw),
    )


@router.get("/dashboard/summary", summary="返回首页风险统计和重点供应商列表")
def dashboard_summary(raw: Request) -> Any:
    user_id = _authorize(raw, "supplier.read")
    scope = None
    if get_settings().rbac_enforced and user_id:
        configured_scope = repository().access_profile(user_id)["supplier_scope"]
        scope = configured_scope or None
    return ok(repository().dashboard_summary(scope), get_trace_id(raw))


@router.post("/mock/bootstrap", summary="初始化前端联调样例数据和演示账号")
def mock_bootstrap(raw: Request, analyze_now: bool = Query(default=True), enable_live_llm: bool = Query(default=True)) -> Any:
    _ensure_mock_mode()
    repo = repository()
    result = bootstrap_frontend_demo(repo)
    analyses: list[dict[str, Any]] = []
    if analyze_now:
        tasks = repo.dispatch_due_monitoring(limit=20)
        request = AnalyzeMonitorRequest(current_week=36, enable_live_llm=enable_live_llm)
        analyses = [_analyze_monitor(item["monitor_id"], request) for item in tasks]
    result["analyses"] = analyses
    result["reports"] = {
        supplier_id: repo.list_supplier_reports(supplier_id, limit=10)
        for supplier_id in result["supplier_ids"]
    }
    return ok(result, get_trace_id(raw))


@router.post("/mock/login", summary="前端联调 Mock 登录")
def frontend_mock_login(request: MockLoginRequest, raw: Request) -> Any:
    _ensure_mock_mode()
    return ok(mock_login(repository(), request.username), get_trace_id(raw))


@router.get("/mock/users", summary="列出可用于联调的 Mock 账号")
def list_mock_users(raw: Request) -> Any:
    _ensure_mock_mode()
    allowed = {"demo_admin", "demo_manager", "demo_leadership"}
    users = [item for item in repository().list_users() if item["username"] in allowed]
    return ok({"records": users}, get_trace_id(raw))

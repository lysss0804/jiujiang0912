"""FastAPI 联调服务（含交付包装层）。

供后端直接对接；同时也保留了纯函数入口（app.service.analyze_supplier、
app.batch.service.analyze_batch），两种方式并存。

交付包装（相对裸接口新增）：
- 统一响应信封 ``{code, message, data, trace_id}`` + 全局异常处理器；
- ``X-API-Key`` 鉴权（``API_KEY`` 未配置时自动放行，便于联调）；
- 批量异步模式：``async=true`` 返回 ``task_id``，配套 ``GET /agent/task/{task_id}``；
- 人工审核回写：``POST /agent/review`` / ``GET /agent/reviews``；
- 统计窗口可按周/按月（``window_unit`` / ``window_size``）；
- 按角色下发（``audience``）：领导层可见横向对比，普通管理人员仅见汇总（预留接口）；
- 供应商重新申报（预留）：``POST /agent/redeclare``。

启动：
    python -m app.main serve
    或 uvicorn app.api.server:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import Depends, FastAPI, Path as PathParam, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from app.api.deps import get_trace_id, require_api_key
from app.api.envelope import ErrorCode, fail, http_status_for, ok
from app.api.task_store import task_store
from app.batch.service import analyze_batch
from app.common.errors import JiujiangError
from app.config import get_settings
from app.data.loader import canonical_supplier_ids, normalize_supplier_id
from app.review.schemas import HumanReviewRequest, SupplierRedeclareRequest
from app.review.service import ReviewFeedbackService
from app.schemas.report import build_risk_report
from app.service import analyze_supplier


class UnknownSupplierError(JiujiangError):
    """供应商在源数据中不存在。"""


class UnknownTaskError(JiujiangError):
    """异步任务 ID 不存在。"""


app = FastAPI(
    title="银行外包供应商风险 Multi-Agent 服务",
    description="当前风险分级 + 四智能体协同分析 + 结构化风险报告（不含预测）",
    version="0.3.0",
    dependencies=[Depends(require_api_key)],
)


# --------------------------------------------------------------------------- #
# 请求模型
# --------------------------------------------------------------------------- #
class AnalyzeRequest(BaseModel):
    supplier_ids: list[str] = Field(default_factory=list, description="留空则分析全部供应商")
    current_week: int | None = Field(default=None, ge=1, le=52, description="留空则取数据中最大周次")
    enable_live_llm: bool = Field(default=False, description="是否启用真实大模型（需在线 API Key）")
    include_reports: bool = Field(default=True, description="是否在响应中返回全部报告（同步批量模式生效）")
    watchlist_size: int = Field(default=10, ge=1, le=100)
    async_mode: bool = Field(default=False, alias="async", description="true 时批量分析转异步，立即返回 task_id")
    # 统计窗口：不传时保持现状（近 12 周）
    window_unit: Literal["week", "month"] | None = Field(
        default=None, description="统计窗口单位：week（按周）/ month（按月，1月=4周）；留空取配置默认"
    )
    window_size: int | None = Field(default=None, ge=1, le=52, description="窗口大小（周数或月数）")
    # 受众角色（两层）：决定是否下发横向对比数据；缺省按领导层下发（保持现状兼容）
    audience: Literal["LEADERSHIP", "MANAGER"] = Field(
        default="LEADERSHIP", description="请求方角色：LEADERSHIP=高级管理人员；MANAGER=普通管理人员"
    )


class ReviewRequest(HumanReviewRequest):
    """人工审核回写请求。"""

    run_id: str = Field(min_length=1, max_length=100)
    supplier_id: str = Field(min_length=1, max_length=100)
    risk_level: str | None = Field(default=None, description="审核人确认的风险等级，可选")
    disposition_state: str | None = Field(default=None, description="处置状态，可选（与报告 disposition 对齐）")


class RedeclareRequest(SupplierRedeclareRequest):
    """供应商重新申报请求（预留端点，智能体只做登记与受理）。"""


# 领导层可见横向对比；普通管理人员仅见汇总（由后端据此裁剪下发）
def _apply_audience(payload: dict[str, Any], audience: str) -> dict[str, Any]:
    """按角色裁剪横向对比数据：普通管理人员不下发横向对比块。"""
    if audience == "MANAGER":
        comparison = payload.get("cross_supplier_comparison")
        if comparison is not None:
            payload["cross_supplier_comparison"] = None
            payload["cross_supplier_comparison_withheld"] = (
                "普通管理人员角色不下发横向对比数据；如需请以领导层角色请求"
            )
    return payload


def _apply_audience_to_reports(result: dict[str, Any], audience: str) -> dict[str, Any]:
    if audience == "MANAGER" and result.get("cross_supplier_comparison") is not None:
        result["cross_supplier_comparison"] = None
        result["cross_supplier_comparison_withheld"] = (
            "普通管理人员角色不下发横向对比数据；如需请以领导层角色请求"
        )
    return result


# --------------------------------------------------------------------------- #
# 全局异常处理：所有错误也走统一信封
# --------------------------------------------------------------------------- #
@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    trace_id = get_trace_id(request)
    errors = [
        {"type": item.get("type"), "loc": list(item.get("loc", [])), "msg": item.get("msg")}
        for item in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=fail(
            ErrorCode.UNPROCESSABLE,
            "request validation failed",
            trace_id,
            data={"errors": errors},
        ),
    )


@app.exception_handler(ValueError)
async def _value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    """业务层校验失败（如重复提交、跨字段约束）按 400 返回。"""
    trace_id = get_trace_id(request)
    return JSONResponse(
        status_code=400,
        content=fail(ErrorCode.INVALID_REQUEST, str(exc), trace_id),
    )


@app.exception_handler(ValidationError)
async def _pydantic_validation_handler(request: Request, exc: ValidationError) -> JSONResponse:
    """模型内部跨字段校验失败（如 APPROVED 缺 final_action）按 422 返回。"""
    trace_id = get_trace_id(request)
    errors = [
        {"type": item.get("type"), "loc": list(item.get("loc", [])), "msg": item.get("msg")}
        for item in exc.errors(include_input=False)
    ]
    return JSONResponse(
        status_code=422,
        content=fail(
            ErrorCode.UNPROCESSABLE,
            "request validation failed",
            trace_id,
            data={"errors": errors},
        ),
    )


@app.exception_handler(JiujiangError)
async def _domain_handler(request: Request, exc: JiujiangError) -> JSONResponse:
    trace_id = get_trace_id(request)
    code = {
        "DataValidationError": ErrorCode.INVALID_REQUEST,
        "UnauthorizedError": ErrorCode.UNAUTHORIZED,
        "UnknownSupplierError": ErrorCode.NOT_FOUND,
        "UnknownTaskError": ErrorCode.NOT_FOUND,
    }.get(type(exc).__name__, ErrorCode.INTERNAL_ERROR)
    message = {
        "UnknownSupplierError": f"unknown supplier_id: {exc}",
        "UnknownTaskError": f"unknown task_id: {exc}",
    }.get(type(exc).__name__, str(exc))
    return JSONResponse(status_code=http_status_for(code), content=fail(code, message, trace_id))


@app.exception_handler(Exception)
async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    trace_id = get_trace_id(request)
    return JSONResponse(
        status_code=500,
        content=fail(ErrorCode.INTERNAL_ERROR, f"{type(exc).__name__}: {exc}", trace_id),
    )


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def _resolve_supplier_id(supplier_id: str) -> str:
    """把外部输入（普通连字符）映射回源数据真实 ID；未知 ID 抛 404 由处理器兜底。"""
    canonical = canonical_supplier_ids()
    resolved = canonical.get(normalize_supplier_id(supplier_id))
    if resolved is None:
        raise UnknownSupplierError(supplier_id)
    return resolved


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")


def _run_analyze(request: AnalyzeRequest) -> tuple[str, dict[str, Any]]:
    """执行单/批量分析，返回 (mode, payload)。同步与异步共用的纯逻辑。"""
    if len(request.supplier_ids) <= 1:
        supplier_id = request.supplier_ids[0] if request.supplier_ids else ""
        if supplier_id:
            state = analyze_supplier(
                supplier_id=supplier_id,
                current_week=request.current_week,
                enable_live_llm=request.enable_live_llm,
                window_unit=request.window_unit,
                window_size=request.window_size,
            )
            report = state.get("risk_report") or build_risk_report(state)
            return "single", {"report": _apply_audience(report, request.audience)}

    result = analyze_batch(
        supplier_ids=request.supplier_ids or None,
        current_week=request.current_week,
        enable_live_llm=request.enable_live_llm,
        watchlist_size=request.watchlist_size,
        window_unit=request.window_unit,
        window_size=request.window_size,
    )
    if not request.include_reports:
        result.pop("reports", None)
    return "batch", _apply_audience_to_reports(result, request.audience)


# --------------------------------------------------------------------------- #
# 接口
# --------------------------------------------------------------------------- #
@app.get("/health", summary="健康检查")
def health(raw: Request) -> Any:
    return ok(
        {"status": "ok", "service": "jiujiang-multi-agent"},
        get_trace_id(raw),
    )


@app.post("/agent/analyze", summary="单/批量风险分析")
def agent_analyze(request: AnalyzeRequest, raw: Request) -> Any:
    """批量分析入口：单供应商传一个 id，批量传多个或留空。

    ``async`` 为 true 且为批量模式时，立即返回 ``task_id``，
    通过 ``GET /agent/task/{task_id}`` 轮询结果。
    """
    trace_id = get_trace_id(raw)
    is_batch = len(request.supplier_ids) != 1

    if request.async_mode and is_batch:
        task_id = task_store.create()
        task_store.submit(
            task_id,
            lambda: {"mode": "batch", **_run_analyze(request)[1]},
        )
        return ok({"task_id": task_id, "status": "PENDING", "polling_url": f"/agent/task/{task_id}"}, trace_id)

    mode, payload = _run_analyze(request)
    return ok({"mode": mode, **payload}, trace_id)


@app.get("/agent/task/{task_id}", summary="查询异步分析任务")
def agent_task(task_id: str, raw: Request) -> Any:
    trace_id = get_trace_id(raw)
    task = task_store.get(task_id)
    if task is None:
        raise UnknownTaskError(task_id)
    body = {
        "task_id": task["task_id"],
        "status": task["status"],
        "created_at": task["created_at"],
        "started_at": task["started_at"],
        "finished_at": task["finished_at"],
        "error": task["error"],
        "result": task["result"],
    }
    return ok(body, trace_id)


@app.get("/agent/report/{supplier_id}", summary="查询单个供应商结构化报告")
def agent_report(
    supplier_id: str,
    raw: Request,
    current_week: int | None = None,
    window_unit: Literal["week", "month"] | None = Query(default=None),
    window_size: int | None = Query(default=None, ge=1, le=52),
    audience: Literal["LEADERSHIP", "MANAGER"] = Query(default="LEADERSHIP"),
) -> Any:
    trace_id = get_trace_id(raw)
    state = analyze_supplier(
        supplier_id=_resolve_supplier_id(supplier_id),
        current_week=current_week,
        window_unit=window_unit,
        window_size=window_size,
    )
    report = state.get("risk_report") or build_risk_report(state)
    return ok(_apply_audience(report, audience), trace_id)


@app.get("/agent/tools/supplier/{supplier_id}/evidence", summary="证据取数（便于替换为后端真实接口）")
def supplier_evidence(supplier_id: str, raw: Request, current_week: int | None = None) -> Any:
    trace_id = get_trace_id(raw)
    state = analyze_supplier(supplier_id=_resolve_supplier_id(supplier_id), current_week=current_week)
    return ok(
        {
            "supplier_id": state.get("supplier_id", supplier_id),
            "current_week": state.get("current_week"),
            "evidence_result": state.get("evidence_result", {}),
        },
        trace_id,
    )


@app.get("/agent/tools/supplier/{supplier_id}/history", summary="历史风险概况（分级 + 趋势 + 维度）")
def supplier_history(supplier_id: str, raw: Request, current_week: int | None = None) -> Any:
    trace_id = get_trace_id(raw)
    state = analyze_supplier(supplier_id=_resolve_supplier_id(supplier_id), current_week=current_week)
    return ok(
        {
            "supplier_id": state.get("supplier_id", supplier_id),
            "current_week": state.get("current_week"),
            "risk_grade": state.get("risk_grade", {}),
            "risk_trend": state.get("risk_trend", {}),
            "dimension_stats": state.get("dimension_stats", {}),
        },
        trace_id,
    )


@app.post("/agent/review", summary="人工审核回写")
def submit_review(request: ReviewRequest, raw: Request) -> Any:
    """把人工审核结论写回审核记录；同一 run_id 不允许重复提交。"""
    trace_id = get_trace_id(raw)
    service = ReviewFeedbackService(get_settings().review_output_path)
    supplier_id = _resolve_supplier_id(request.supplier_id)
    review_request = HumanReviewRequest(
        reviewer_id=request.reviewer_id,
        review_result=request.review_result,
        final_action=request.final_action,
        review_comment=request.review_comment,
    )
    record = service.record(
        run_id=request.run_id,
        supplier_id=supplier_id,
        request=review_request,
        risk_level=request.risk_level,
        disposition_state=request.disposition_state,
    )
    return ok(record.model_dump(mode="json"), trace_id)


@app.post("/agent/redeclare", summary="供应商重新申报（预留端点）")
def submit_redeclare(request: RedeclareRequest, raw: Request) -> Any:
    """供应商侧重新申报受理（预留）。

    说明：是否**允许**重新申报由确定性处置门禁在分析与人工复核阶段判定
    （报告 ``disposition.can_redeclare``）。本端点只做登记：把供应商补充材料
    写入人工复核队列（``review_result=REDECLARED``），交由后端通知与人工复核处理。
    是否落在「问题较小」分支需先由后端查询该供应商报告确认。
    """
    trace_id = get_trace_id(raw)
    service = ReviewFeedbackService(get_settings().review_output_path)
    supplier_id = _resolve_supplier_id(request.supplier_id)
    review_request = HumanReviewRequest(
        reviewer_id=request.submitted_by or "SUPPLIER",
        review_result="REDECLARED",
        review_comment=request.redeclare_reason,
    )
    # 重新申报以独立 run_id 登记，避免与人工复核记录去重规则冲突
    redeclare_run_id = f"redeclare::{supplier_id}::{_now_iso()}"
    record = service.record(
        run_id=redeclare_run_id,
        supplier_id=supplier_id,
        request=review_request,
        disposition_state="AWAITING_SUPPLIER_REDECLARE",
    )
    payload = record.model_dump(mode="json")
    payload["attachment_refs"] = request.attachment_refs
    return ok(payload, trace_id)


@app.get("/agent/reviews", summary="查询已回写的人工审核记录")
def list_reviews(
    raw: Request,
    supplier_id: str | None = Query(default=None),
    disposition_state: str | None = Query(default=None, description="按处置状态过滤"),
    limit: int = Query(default=50, ge=1, le=500),
) -> Any:
    trace_id = get_trace_id(raw)
    records = ReviewFeedbackService(get_settings().review_output_path).list_records()
    if supplier_id:
        target = normalize_supplier_id(_resolve_supplier_id(supplier_id))
        records = [item for item in records if normalize_supplier_id(item.supplier_id) == target]
    if disposition_state:
        records = [item for item in records if item.disposition_state == disposition_state]
    payload = [item.model_dump(mode="json") for item in records[-limit:]]
    return ok({"total": len(payload), "records": payload}, trace_id)


__all__ = ["app"]

"""Coordinator：装载并校验输入，完成风险分级，决定后续分析链路。

这是「规则 → 分级 → 条件路由 → Multi-Agent」链路的起点。
"""

from app.agents.helpers import trace
from app.config import get_settings
from app.data.loader import is_in_rectify
from app.rules.engine import grade_supplier
from app.rules.window import resolve_window
from app.schemas.output_contract import OUTPUT_SCHEMA_VERSION

HIGH_RISK_ROUTE = "HIGH_RISK_DEEP_DIVE"
STANDARD_ROUTE = "STANDARD"


def coordinator_node(state: dict) -> dict:
    required = ("run_id", "supplier_id", "current_week")
    missing = [key for key in required if state.get(key) in (None, "")]
    if missing:
        return {
            "status": "FAILED_VALIDATION",
            "errors": [f"Missing workflow fields: {missing}"],
            "audit_trace": trace("Coordinator", "FAIL", detail="missing workflow fields"),
        }

    settings = get_settings()
    supplier_id = str(state["supplier_id"])
    current_week = int(state["current_week"])
    profile = state.get("supplier_profile", {}) or {}
    events = list(state.get("events", []))

    rectifies = state.get("rectifies", []) or []
    in_rectify = is_in_rectify(rectifies, current_week)

    # 统计窗口：请求参数 > Settings > rules.yaml > 内置默认；月按 weeks_per_month 换算为周
    spec = resolve_window(
        current_week=current_week,
        unit=state.get("window_unit"),
        size=state.get("window_size"),
        window_weeks=settings.recent_window_weeks,
        weeks_per_month=settings.window_weeks_per_month,
    )

    grade = grade_supplier(
        supplier_id=supplier_id,
        current_week=current_week,
        events=events,
        # 银行名单优先（系统只读不算）；名单缺失时引擎自动回退合成
        importance_level=profile.get("importance_level"),
        contract_importance=profile.get("contract_importance"),
        system_level=profile.get("system_level"),
        in_rectify=in_rectify,
        window=spec,
    )

    route = HIGH_RISK_ROUTE if grade.risk_level == "RED" else STANDARD_ROUTE
    return {
        "status": "RUNNING",
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "risk_grade": grade.model_dump(),
        "analysis_route": route,
        "window_unit": spec.unit,
        "window_size": spec.size,
        "window_display": spec.display,
        "audit_trace": trace(
            "Coordinator",
            "PASS",
            detail=(
                f"schema={OUTPUT_SCHEMA_VERSION}; risk_level={grade.risk_level}; "
                f"score={grade.score}; tier={grade.grade_tier}({grade.grade_range}); "
                f"importance={grade.importance_tier}"
                f"({grade.importance_source}); window={spec.display}; "
                f"report_period={grade.report_period}; route={route}"
            ),
        ),
    }


def route_after_grading(state: dict) -> str:
    """条件路由：红色走高风险专项链路，其余走常规链路。

    两条链路都会经过四个 Agent，差异体现在分析深度提示（state.analysis_route）
    与是否追加专项核查建议，从而体现真正的条件协同而非简单串行。
    """
    return "high_risk" if state.get("analysis_route") == HIGH_RISK_ROUTE else "standard"

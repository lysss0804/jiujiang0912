"""关联分析 Agent。

职责：
1. 分析多个风险维度之间是否存在关联（交叉风险）；
2. 对比「近期窗口」与「上一段窗口」的事件分布，研判风险走向（上升/持平/恶化/突增）；
3. 承接原趋势研判职责（系统不做未来概率预测，只做基于历史的走向研判）。

边界：
- 不做概率预测，不做未来风险升级判断；
- 趋势类型（RISING/STEADY/FALLING/SUDDEN_JUMP）与阈值判断**仍由确定性逻辑产出**，
  只有 trend_desc / cross_dimension_note 两段自然语言交给模型生成；
- 模型不可用时回退确定性模板并标记 FALLBACK。
"""

import json

from app.agents.helpers import align_ids, dimension_breakdown, events_in_window, trace
from app.config import get_settings
from app.llm.text_nodes import invoke_text_llm
from app.rules.window import resolve_window, shift_window
from app.schemas.contracts import AssociationLLMResult

TREND_TO_TYPE = {
    "RISING": "RISING",
    "STEADY": "STEADY",
    "FALLING": "FALLING",
    "SUDDEN_JUMP": "SUDDEN_JUMP",
}

SYSTEM_PROMPT = """你是银行外包供应商关联分析与趋势研判助手。
只输出 JSON 对象，并严格包含 trend_desc、cross_dimension_note、trend_support_evidence_ids。

硬性要求：
1. 趋势类型（RISING/STEADY/FALLING/SUDDEN_JUMP）与所有数值（加权严重度、计数、窗口）
   均已由确定性逻辑计算完成，你只能引用，不得修改、重新计算或改变趋势类型。
2. trend_desc 必须忠实解释给定的趋势类型与数值，不得与之矛盾。
3. cross_dimension_note 必须基于给定的活跃维度与是否跨维度关联，不得编造维度。
4. trend_support_evidence_ids 只能引用输入中列出的 evidence_id，不得编造；无证据时返回空数组。
5. 不得做未来风险概率预测。
6. 每段文字不超过 200 字，语言客观、书面化。

措辞方向约束（务必遵守，避免自相矛盾）：
- 趋势类型与措辞方向必须严格一一对应，禁止出现相反方向的用词：
  * RISING        → 只能用「上升 / 走高 / 恶化 / 加大」等方向词，禁止写「下降 / 缓和 / 减少 / 缓解」；
  * FALLING       → 只能用「下降 / 缓和 / 走低 / 减少」等方向词，禁止写「上升 / 恶化 / 增加 / 加大」；
  * STEADY        → 只能用「持平 / 稳定 / 基本不变」，禁止写「上升 / 下降 / 增加 / 减少」；
  * SUDDEN_JUMP   → 只能用「突增 / 激增 / 骤然抬升」，禁止写「缓和 / 下降」。
- 判断方向的唯一依据是 recent_weighted_severity 与 previous_weighted_severity 的大小关系：
  前者更大即为上升，前者更小即为下降，两者相等即为持平。不得依据事件条数等其他口径另作判断。
- 描述原因时，只能引用输入中确实存在的维度计数变化；
  若 recent_dimension_counts 与 previous_dimension_counts 的变化方向与总体趋势不一致，
  应先陈述总体加权严重度方向，再客观说明个别维度的计数变动，不得让局部计数
  的措辞与总体趋势方向冲突（例如总体下降时不得写「风险加大」）。
- 不得臆造数据中不存在的维度、事件或数量。"""


def _severity_weighted(events: list[dict]) -> float:
    return float(sum(int(event.get("event_severity", 0)) for event in events))


def _template_trend_desc(
    *,
    trend_key: str,
    window_display: str,
    recent_weighted: float,
    previous_weighted: float,
    in_rectify: bool,
) -> str:
    if trend_key == "SUDDEN_JUMP":
        return (
            f"{window_display}出现严重程度≥3的突发事件，风险呈突增态势，"
            "建议立即提高监测频率并启动专项核查。"
        )
    if trend_key == "RISING":
        return (
            f"{window_display}风险事件加权值{recent_weighted:g}，较上一窗口{previous_weighted:g}上升，"
            "风险呈持续恶化趋势。"
        )
    if trend_key == "FALLING":
        return (
            f"{window_display}风险事件加权值{recent_weighted:g}，较上一窗口{previous_weighted:g}下降，"
            "风险呈缓和趋势" + ("（处于整改期内）" if in_rectify else "") + "。"
        )
    return (
        f"{window_display}风险事件加权值{recent_weighted:g}，与上一窗口{previous_weighted:g}基本持平，"
        "风险水平保持稳定。"
    )


def _template_cross_note(*, cross_dimension: bool, active_dimensions: list[str]) -> str:
    if cross_dimension:
        return (
            f"近期风险跨{len(active_dimensions)}个维度（{'、'.join(active_dimensions)}），"
            "多维度风险同时出现，说明问题并非单点偶发，需关注经营稳定性等共性成因。"
        )
    return "近期风险集中在单一维度，尚未观察到明显的跨维度关联。"


def association_analysis_node(state: dict) -> dict:
    settings = get_settings()
    supplier_id = str(state["supplier_id"])
    current_week = int(state["current_week"])
    events = list(state.get("events", []))
    grade = state.get("risk_grade", {})

    # 统计窗口与对照窗口统一由 WindowSpec 推导（不再各自重算 start_week）
    spec = resolve_window(
        current_week=current_week,
        unit=state.get("window_unit") or grade.get("window_unit"),
        size=state.get("window_size") or grade.get("window_size"),
        window_weeks=grade.get("window_weeks") or settings.recent_window_weeks,
    )
    baseline = int(settings.trend_baseline_weeks)
    baseline_start, baseline_end = shift_window(spec, offset_weeks=-spec.window_weeks)

    recent = events_in_window(
        events, supplier_id=supplier_id, week_start=spec.start_week, week_end=spec.end_week
    )
    previous = events_in_window(
        events, supplier_id=supplier_id, week_start=baseline_start, week_end=baseline_end
    )

    recent_counts = dimension_breakdown(recent)
    previous_counts = dimension_breakdown(previous)
    recent_weighted = _severity_weighted(recent)
    previous_weighted = _severity_weighted(previous)

    # ---- 多维度关联：同时出现 2 个及以上维度视为存在跨维度关联（确定性）----
    active_dimensions = [name for name, count in recent_counts.items() if count > 0]
    cross_dimension = len(active_dimensions) >= 2

    # ---- 趋势研判：类型与阈值判定完全确定性 ----
    max_severity = max((int(event.get("event_severity", 0)) for event in recent), default=0)
    delta = recent_weighted - previous_weighted
    if max_severity >= 3 and (previous_weighted == 0 or recent_weighted >= previous_weighted * 1.5):
        trend_key = "SUDDEN_JUMP"
    elif delta > 0 and recent_weighted >= previous_weighted * 1.2:
        trend_key = "RISING"
    elif delta < 0:
        trend_key = "FALLING"
    else:
        trend_key = "STEADY"

    in_rectify = bool(grade.get("in_rectify"))
    evidence_ids = [str(event.get("evidence_id")) for event in recent[-8:]]

    def _fallback() -> AssociationLLMResult:
        return AssociationLLMResult(
            trend_desc=_template_trend_desc(
                trend_key=trend_key,
                window_display=spec.display,
                recent_weighted=recent_weighted,
                previous_weighted=previous_weighted,
                in_rectify=in_rectify,
            ),
            cross_dimension_note=_template_cross_note(
                cross_dimension=cross_dimension, active_dimensions=active_dimensions
            ),
            trend_support_evidence_ids=evidence_ids,
        )

    payload = {
        "supplier_id": supplier_id,
        "current_week": current_week,
        "window_display": spec.display,
        "window_weeks": spec.window_weeks,
        "baseline_weeks": baseline,
        "trend_type": TREND_TO_TYPE[trend_key],
        "recent_weighted_severity": recent_weighted,
        "previous_weighted_severity": previous_weighted,
        "recent_dimension_counts": recent_counts,
        "previous_dimension_counts": previous_counts,
        "active_dimensions": active_dimensions,
        "cross_dimension": cross_dimension,
        "max_event_severity": max_severity,
        "in_rectify": in_rectify,
        "candidate_evidence_ids": evidence_ids,
    }

    result, llm_status = invoke_text_llm(
        node="association_analysis",
        system_prompt=SYSTEM_PROMPT,
        payload=payload,
        response_model=AssociationLLMResult,
        enabled=bool(state.get("enable_live_llm", False)) and settings.llm_text_association,
        fallback=_fallback,
        validate=lambda output: _validate_association(output, evidence_ids),
        settings=settings,
    )

    trend_support_ids = align_ids(result.trend_support_evidence_ids, evidence_ids) or evidence_ids

    return {
        "risk_trend": {
            "trend_type": TREND_TO_TYPE[trend_key],
            "trend_desc": result.trend_desc,
            "trend_support_evidence_ids": trend_support_ids,
        },
        "association_result": {
            "business_exposure": float(state.get("business_context", {}).get("business_exposure", 0.0)),
            "active_dimensions": active_dimensions,
            "cross_dimension": cross_dimension,
            "cross_dimension_note": result.cross_dimension_note,
            "recent_weighted_severity": recent_weighted,
            "previous_weighted_severity": previous_weighted,
            "recent_dimension_counts": recent_counts,
            "previous_dimension_counts": previous_counts,
            "window_weeks": spec.window_weeks,
            "window_display": spec.display,
            "baseline_weeks": baseline,
            "llm_status": llm_status,
        },
        "audit_trace": trace(
            "AssociationAnalysisAgent",
            "PASS",
            detail=(
                f"trend={trend_key}; cross_dimension={cross_dimension}; "
                f"dims={len(active_dimensions)}; llm={llm_status}"
            ),
        ),
    }


def _validate_association(output: AssociationLLMResult, candidate_ids: list[str]) -> None:
    """拦截越界证据引用：模型只能引用输入中给出的候选证据编号。"""
    from app.agents.helpers import require_grounded

    require_grounded(output.trend_support_evidence_ids, candidate_ids, kind="evidence")

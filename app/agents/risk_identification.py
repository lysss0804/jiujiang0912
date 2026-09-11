"""风险识别 Agent。

职责：读取规则引擎的分级结果，归纳「当前到底发生了什么风险」，
包括风险等级、主要风险因素与命中规则。

边界：
- 不重新判断红黄绿（由规则引擎负责），只做归纳与解释；
- 数值统计（事件数、维度分布、命中规则）保持确定性，只有自然语言 summary 交给模型；
- 模型不可用时回退确定性模板并标记 FALLBACK，保证报告始终可生成。
"""

import json

from app.agents.helpers import dimension_breakdown, event_dimension, events_in_window, trace
from app.config import get_settings
from app.llm.text_nodes import invoke_text_llm
from app.rules.window import resolve_window
from app.schemas.contracts import RiskIdentificationLLMResult

HIGH_RISK_ROUTE = "HIGH_RISK_DEEP_DIVE"

SYSTEM_PROMPT = """你是银行外包供应商风险识别助手。
只输出 JSON 对象，并严格包含 summary 字段。

硬性要求：
1. 风险等级、规则得分、事件数量、维度分布均已由规则引擎计算完成，你只能引用，不得修改或重新计算。
2. summary 必须基于输入中给出的事实（风险等级、窗口周数、事件数、维度分布、主要风险维度、命中规则）进行归纳。
3. 不得编造输入中不存在的事件、维度、规则、编号或数据。
4. 不得做未来风险概率预测，不得给出处置建议。
5. 语言简洁、客观、书面化，不超过 200 字。"""


def _template_summary(
    *,
    window_display: str,
    risk_level: str,
    event_count: int,
    dimension_count: int,
    main_risks: list[dict],
    hit_rule_count: int,
) -> str:
    """确定性兜底文案：不依赖任何 LLM。"""
    summary = (
        f"当前风险等级：{risk_level}。{window_display}共识别到{event_count}条风险事件，"
        f"覆盖{dimension_count}个风险维度"
    )
    if main_risks:
        summary += "；主要风险集中在" + "、".join(item["dimension"] for item in main_risks) + "维度"
    summary += f"。命中规则{hit_rule_count}条。"
    return summary


def risk_identification_node(state: dict) -> dict:
    settings = get_settings()
    supplier_id = str(state["supplier_id"])
    current_week = int(state["current_week"])
    events = list(state.get("events", []))
    grade = state.get("risk_grade", {})

    # 统计窗口统一由 WindowSpec 决定，避免各 Agent 各自推导导致口径不一致
    spec = resolve_window(
        current_week=current_week,
        unit=state.get("window_unit") or grade.get("window_unit"),
        size=state.get("window_size") or grade.get("window_size"),
        window_weeks=grade.get("window_weeks") or settings.recent_window_weeks,
    )
    recent = events_in_window(
        events, supplier_id=supplier_id, week_start=spec.start_week, week_end=spec.end_week
    )

    counts = dimension_breakdown(recent)

    # 主要风险：按维度事件数降序，取前 3 个有事件的维度
    ranked = sorted(
        ((name, count) for name, count in counts.items() if count > 0),
        key=lambda item: (-item[1], item[0]),
    )
    main_risks = [
        {
            "dimension": name,
            "event_count": count,
            "subtypes": sorted(
                {str(event.get("event_subtype")) for event in recent if event_dimension(event) == name}
            ),
        }
        for name, count in ranked[:3]
    ]

    hit_rules = grade.get("hit_rules", [])
    risk_level = grade.get("risk_level", "GREEN")
    deep_dive = state.get("analysis_route") == HIGH_RISK_ROUTE
    dimension_count = sum(1 for value in counts.values() if value > 0)
    no_data_dimensions = [name for name, value in counts.items() if value == 0]
    hit_rule_digest = [
        {"rule_id": item.get("rule_id"), "description": item.get("description")} for item in hit_rules
    ]

    def _fallback() -> RiskIdentificationLLMResult:
        return RiskIdentificationLLMResult(
            summary=_template_summary(
                window_display=spec.display,
                risk_level=risk_level,
                event_count=len(recent),
                dimension_count=dimension_count,
                main_risks=main_risks,
                hit_rule_count=len(hit_rules),
            )
        )

    payload = {
        "supplier_id": supplier_id,
        "current_week": current_week,
        "window_display": spec.display,
        "window_weeks": spec.window_weeks,
        "risk_level": risk_level,
        "grade_tier": grade.get("grade_tier", risk_level),
        "grade_label": grade.get("grade_label", ""),
        "grade_range": grade.get("grade_range", ""),
        "score": grade.get("score", 0.0),
        "grade_basis": grade.get("grade_basis", ""),
        "recent_event_count": len(recent),
        "dimension_counts": counts,
        "no_data_dimensions": no_data_dimensions,
        "main_risks": main_risks,
        "hit_rules": hit_rule_digest,
        "analysis_depth": "DEEP_DIVE" if deep_dive else "STANDARD",
    }

    result, llm_status = invoke_text_llm(
        node="risk_identification",
        system_prompt=SYSTEM_PROMPT,
        payload=payload,
        response_model=RiskIdentificationLLMResult,
        enabled=bool(state.get("enable_live_llm", False))
        and settings.llm_text_risk_identification,
        fallback=_fallback,
        settings=settings,
    )

    return {
        "visible_events": recent,
        "dimension_stats": {
            "counts": counts,
            "window_weeks": spec.window_weeks,
            "window_display": spec.display,
            "start_week": spec.start_week,
            "end_week": spec.end_week,
            "no_data_dimensions": no_data_dimensions,
        },
        "risk_identification_result": {
            "risk_level": risk_level,
            "grade_tier": grade.get("grade_tier", risk_level),
            "grade_label": grade.get("grade_label", ""),
            "grade_range": grade.get("grade_range", ""),
            "score": grade.get("score", 0.0),
            "grade_basis": grade.get("grade_basis", ""),
            "main_risks": main_risks,
            "hit_rules": hit_rule_digest,
            "summary": result.summary,
            "analysis_depth": "DEEP_DIVE" if deep_dive else "STANDARD",
            "llm_status": llm_status,
        },
        "audit_trace": trace(
            "RiskIdentificationAgent",
            "PASS",
            detail=(
                f"risk_level={risk_level}; visible_events={len(recent)}; "
                f"window={spec.display}; no_data_dims={len(no_data_dimensions)}; "
                f"depth={'DEEP_DIVE' if deep_dive else 'STANDARD'}; llm={llm_status}"
            ),
        ),
    }


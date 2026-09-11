"""人工复核节点。

系统不做自动处置：Agent 只输出候选建议，最终由人工确认。
本节点负责：
1. 把流程状态落到人工复核队列（含处置门禁判定）；
2. 生成结构化风险报告；
3. 产出「分发提示」（按角色可见范围）与「是否可重新申报」的确定性结论。

处置门禁完全确定性（模型不参与），优先级：
- 证据不足 → ``EVIDENCE_INSUFFICIENT``（优先走该分支，不进入重新申报）；
- 否则由 ``is_minor_issue`` 判定：问题较小 → ``AWAITING_SUPPLIER_REDECLARE``；
- 其余 → ``PENDING_HUMAN_REVIEW``。
"""

from app.agents.helpers import trace
from app.rules.config import get_rules
from app.rules.engine import is_minor_issue
from app.schemas.report import build_risk_report

# 角色可见范围：领导层可见综合汇总 + 横向对比；普通管理人员仅见汇总
LEADERSHIP_AUDIENCE = ["LEADERSHIP", "MANAGER"]
MANAGER_AUDIENCE = ["MANAGER"]


def _max_severity(state: dict) -> int:
    """统计窗口内事件的最高严重度（用于重新申报门禁）。"""
    events = state.get("visible_events") or state.get("events", [])
    return max((int(event.get("event_severity", 0)) for event in events), default=0)


def judge_disposition(state: dict) -> dict:
    """处置门禁判定（确定性），返回 disposition 块。"""
    grade = state.get("risk_grade", {}) or {}
    evidence_passed = state.get("evidence_result", {}).get("status") == "PASS"
    risk_level = str(grade.get("risk_level", "GREEN"))
    score = float(grade.get("score", 0.0))
    red_line_ids = {str(item.get("rule_id")) for item in get_rules().get("red_line_rules", [])}
    red_line_hit = any(str(hit.get("rule_id")) in red_line_ids for hit in grade.get("hit_rules", []))
    max_severity = _max_severity(state)

    if not evidence_passed:
        return {
            "delivery_audience": list(LEADERSHIP_AUDIENCE),
            "disposition_state": "EVIDENCE_INSUFFICIENT",
            "can_redeclare": False,
            "reason": "证据链不完整，需人工补充材料后复核，不进入供应商重新申报流程",
        }

    minor, reason = is_minor_issue(
        risk_level=risk_level,
        score=score,
        red_line_hit=red_line_hit,
        max_severity=max_severity,
        rules=get_rules(),
    )
    if minor:
        return {
            "delivery_audience": list(LEADERSHIP_AUDIENCE),
            "disposition_state": "AWAITING_SUPPLIER_REDECLARE",
            "can_redeclare": True,
            "reason": reason,
        }
    return {
        "delivery_audience": list(LEADERSHIP_AUDIENCE),
        "disposition_state": "PENDING_HUMAN_REVIEW",
        "can_redeclare": False,
        "reason": reason,
    }


def human_review_node(state: dict) -> dict:
    disposition = judge_disposition(state)
    status = disposition["disposition_state"]

    report = build_risk_report(
        {**state, "status": status, "disposition": disposition}
    )
    return {
        "status": status,
        "disposition": disposition,
        "risk_report": report,
        "audit_trace": trace(
            "HumanReview",
            "PENDING" if status != "EVIDENCE_INSUFFICIENT" else "BLOCKED",
            detail=(
                f"{status}; can_redeclare={disposition['can_redeclare']}; "
                f"report_id={report.get('report_id')}"
            ),
        ),
    }

"""处置门禁单测：证据不足优先、问题较小可重新申报、其余进入人工复核。

门禁完全确定性（模型不参与）：
- 证据不足 → EVIDENCE_INSUFFICIENT（优先，不进入重新申报）；
- 问题较小（中/低档 + 分值 ≤ 上限 + 无红线 + 最高严重度 < 3）→ AWAITING_SUPPLIER_REDECLARE；
- 其余 → PENDING_HUMAN_REVIEW。
"""

from __future__ import annotations

from app.agents.human_review import (
    LEADERSHIP_AUDIENCE,
    judge_disposition,
)
from app.rules.engine import is_minor_issue
from app.rules.config import get_rules

GATE_RULES = {
    "grade_tiers": [
        {"tier": "RED", "label": "高风险", "min_score": 2.0, "range": "[2.0, +∞)"},
        {"tier": "YELLOW", "label": "中风险", "min_score": 0.8, "range": "[0.8, 2.0)"},
        {"tier": "GREEN", "label": "低风险", "min_score": 0.0, "range": "[0, 0.8)"},
    ],
    "disposition_gate": {"redeclare_max_score": 0.8, "max_severity_allowed": 3},
    "tier_rules": {"dimensions_hit_min_count": 3, "enabled": True},
}


def _state(*, level="GREEN", score=0.2, red_line=False, severity=1, evidence="PASS") -> dict:
    return {
        "risk_grade": {
            "risk_level": level,
            "score": score,
            "hit_rules": [{"rule_id": "RL-SEV-3"}] if red_line else [],
        },
        "evidence_result": {"status": evidence},
        "events": [{"event_severity": severity}],
    }


# --------------------------------------------------------------------------- #
# is_minor_issue 组合条件
# --------------------------------------------------------------------------- #
def test_minor_issue_true_when_all_conditions_met():
    minor, reason = is_minor_issue(
        risk_level="GREEN", score=0.2, red_line_hit=False, max_severity=1, rules=GATE_RULES
    )
    assert minor is True
    assert reason


def test_minor_issue_false_when_score_exceeds_limit():
    minor, _ = is_minor_issue(
        risk_level="GREEN", score=1.9, red_line_hit=False, max_severity=1, rules=GATE_RULES
    )
    assert minor is False


def test_minor_issue_false_when_high_risk_level():
    minor, _ = is_minor_issue(
        risk_level="RED", score=0.5, red_line_hit=False, max_severity=1, rules=GATE_RULES
    )
    assert minor is False


def test_minor_issue_false_when_red_line_hit():
    """命中红线即使分值低也不允许重新申报。"""
    minor, _ = is_minor_issue(
        risk_level="GREEN", score=0.1, red_line_hit=True, max_severity=1, rules=GATE_RULES
    )
    assert minor is False


def test_minor_issue_false_when_severity_too_high():
    minor, _ = is_minor_issue(
        risk_level="GREEN", score=0.1, red_line_hit=False, max_severity=3, rules=GATE_RULES
    )
    assert minor is False


def test_minor_issue_boundary_score_inclusive():
    minor, _ = is_minor_issue(
        risk_level="YELLOW", score=0.8, red_line_hit=False, max_severity=2, rules=GATE_RULES
    )
    assert minor is True


# --------------------------------------------------------------------------- #
# judge_disposition 状态机
# --------------------------------------------------------------------------- #
def test_evidence_insufficient_takes_priority():
    """证据不足优先，即使分值极低也不进入重新申报。"""
    disposition = judge_disposition(_state(evidence="FAIL"))
    assert disposition["disposition_state"] == "EVIDENCE_INSUFFICIENT"
    assert disposition["can_redeclare"] is False
    assert "证据" in disposition["reason"]


def test_minor_issue_awaits_supplier_redeclare():
    disposition = judge_disposition(_state(level="GREEN", score=0.2))
    assert disposition["disposition_state"] == "AWAITING_SUPPLIER_REDECLARE"
    assert disposition["can_redeclare"] is True


def test_high_score_pends_human_review():
    disposition = judge_disposition(_state(level="RED", score=3.0, severity=4))
    assert disposition["disposition_state"] == "PENDING_HUMAN_REVIEW"
    assert disposition["can_redeclare"] is False


def test_red_line_pends_human_review():
    disposition = judge_disposition(_state(level="YELLOW", score=0.3, red_line=True))
    assert disposition["disposition_state"] == "PENDING_HUMAN_REVIEW"
    assert disposition["can_redeclare"] is False


def test_disposition_carries_delivery_audience():
    """产出「分发提示」：高角色可见汇总与横向对比。"""
    disposition = judge_disposition(_state())
    assert disposition["delivery_audience"] == LEADERSHIP_AUDIENCE
    assert "LEADERSHIP" in disposition["delivery_audience"]
    assert "MANAGER" in disposition["delivery_audience"]


def test_real_rules_are_loadable_and_valid():
    """真实 rules.yaml 需包含门禁配置，保证线上判定可用。"""
    rules = get_rules()
    gate = rules.get("disposition_gate", {})
    assert "redeclare_max_score" in gate
    assert "max_severity_allowed" in gate

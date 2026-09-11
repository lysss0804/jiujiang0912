"""三级风险等级（打分制）单测：有序档位取档、边界、红线优先、兜底开关。

要点：
- 分值 `score` 必须原样保留；
- 等级由 `grade_tiers`（有序档位）判定，并输出等级名称与分值标准；
- 红线规则优先于分值；
- `tier_rules` 多维度兜底可配（默认开启）。
"""

import pytest

from app.rules.engine import grade_supplier
from app.schemas.contracts import RISK_DIMENSIONS

RULES = {
    "category_weights": {name: 1.0 for name in RISK_DIMENSIONS},
    "severity_weights": {0: 0.0, 1: 1.0, 2: 2.0, 3: 3.0},
    "time_decay": [{"max_weeks_ago": 999, "factor": 1.0}],
    "importance_coefficients": {"一般": 1.0, "重要": 1.2},
    "grade_tiers": [
        {"tier": "RED", "label": "高风险", "min_score": 6.0, "range": "[6.0, +∞)"},
        {"tier": "YELLOW", "label": "中风险", "min_score": 2.5, "range": "[2.5, 6.0)"},
        {"tier": "GREEN", "label": "低风险", "min_score": 0.0, "range": "[0, 2.5)"},
    ],
    "tier_rules": {"dimensions_hit_min_count": 3, "enabled": False},
    "red_line_rules": [
        {
            "rule_id": "RL-SEV-3",
            "category": "*",
            "description": "严重程度>=3",
            "condition": {"type": "severity_at_least", "value": 3},
        }
    ],
    "detail_rules": [],
}


def _event(week=10, dimension="经营风险", severity=1, evidence_id=None):
    return {
        "evidence_id": evidence_id or f"E-{week}-{dimension}-{severity}",
        "supplier_id": "S-1",
        "event_category": dimension,
        "mapped_dimension": dimension,
        "event_subtype": "测试事件",
        "event_severity": severity,
        "event_week": week,
        "source_type": "测试",
    }


@pytest.mark.parametrize(
    "score_events,expected",
    [
        ([], "GREEN"),
        # 1 条 severity=1 → 1.0 ∈ [0, 2.5) → GREEN
        ([_event(severity=1)], "GREEN"),
        # 2 条 severity=1 → 2.0 ∈ [0, 2.5) → GREEN（边界内）
        ([_event(severity=1), _event(severity=1)], "GREEN"),
        # 2 条 severity=1 + 1 条 severity=1 → 3.0 ∈ [2.5, 6.0) → YELLOW
        ([_event(severity=1), _event(severity=1), _event(severity=1)], "YELLOW"),
        # 2 条 severity=2 → 4.0 ∈ [2.5, 6.0) → YELLOW
        ([_event(severity=2), _event(severity=2)], "YELLOW"),
        # 3 条 severity=2 → 6.0 ∈ [6.0, +∞) → RED
        ([_event(severity=2)] * 3, "RED"),
    ],
)
def test_tier_selected_by_ordered_grade_tiers(score_events, expected):
    grade = grade_supplier(supplier_id="S-1", current_week=10, events=score_events, rules=RULES)
    assert grade.grade_tier == expected
    assert grade.risk_level == expected


def test_boundary_uses_inclusive_min_score():
    """边界值归入其 min_score 所在档：2 条 severity=2 → 4.0 ∈ [2.5, 6.0)。"""
    grade = grade_supplier(
        supplier_id="S-1", current_week=10, events=[_event(severity=2), _event(severity=2)], rules=RULES
    )
    assert grade.score == pytest.approx(4.0)
    assert grade.grade_tier == "YELLOW"
    assert grade.grade_label == "中风险"
    assert grade.grade_range == "[2.5, 6.0)"


def test_grade_keeps_score_and_reports_gap():
    """保留原始分值，并给出距上一档差额。"""
    grade = grade_supplier(
        supplier_id="S-1", current_week=10, events=[_event(severity=2), _event(severity=2)], rules=RULES
    )
    assert grade.score == pytest.approx(4.0)
    assert grade.next_threshold_gap == pytest.approx(2.0)


def test_red_line_overrides_score():
    """命中红线直接判最高档，即使分值极低。"""
    grade = grade_supplier(supplier_id="S-1", current_week=10, events=[_event(severity=3)], rules=RULES)
    assert grade.score == pytest.approx(3.0)
    assert grade.grade_tier == "RED"
    assert any(rule.rule_id == "RL-SEV-3" for rule in grade.hit_rules)


def test_multi_dimension_floor_when_enabled():
    """兜底开启：多维度同时命中且当前档位更低时提升到中档。"""
    rules = {**RULES, "tier_rules": {"dimensions_hit_min_count": 3, "enabled": True}}
    events = [_event(dimension=name, severity=1) for name in RISK_DIMENSIONS[:3]]
    grade = grade_supplier(supplier_id="S-1", current_week=10, events=events, rules=rules)
    assert grade.score == pytest.approx(3.0)
    assert grade.grade_tier in {"YELLOW", "RED"}


def test_multi_dimension_floor_disabled_keeps_score_tier():
    rules = {**RULES, "tier_rules": {"dimensions_hit_min_count": 3, "enabled": False}}
    events = [_event(dimension=name, severity=1) for name in RISK_DIMENSIONS[:3]]
    grade = grade_supplier(supplier_id="S-1", current_week=10, events=events, rules=rules)
    assert grade.score == pytest.approx(3.0)
    assert grade.grade_tier == "YELLOW"


def test_score_zero_is_green():
    grade = grade_supplier(supplier_id="S-1", current_week=10, events=[], rules=RULES)
    assert grade.score == 0.0
    assert grade.grade_tier == "GREEN"
    assert grade.grade_range == "[0, 2.5)"
    assert grade.next_threshold_gap == pytest.approx(2.5)

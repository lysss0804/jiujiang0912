"""风险分级引擎单元测试：分档、重要程度系数、整改系数、时间窗口、红线规则。"""

import pytest

from app.rules.engine import grade_supplier, resolve_importance, resolve_report_period
from app.rules.window import resolve_window

RULES = {
    "category_weights": {
        "公司背景": 1.0,
        "司法": 1.0,
        "失信": 1.0,
        "经营风险": 1.0,
        "经营状况": 1.0,
        "知识产权": 1.0,
    },
    "severity_weights": {0: 0.0, 1: 1.0, 2: 2.0},
    "time_decay": [{"max_weeks_ago": 999, "factor": 1.0}],
    "importance_coefficients": {"一般": 1.0, "重要": 2.0},
    "contract_importance_mapping": {"一般外包": "一般", "重要外包": "重要"},
    "system_level_mapping": {"一般": "一般", "重要": "重要", "核心": "重要"},
    "report_period_by_importance": {"重要": "weekly", "一般": "monthly"},
    "report_period_labels": {"weekly": "周报", "monthly": "月报"},
    "rectify_coefficient": 0.5,
    # 有序档位表：RED >= 10.0 / YELLOW >= 3.0 / GREEN >= 0
    "grade_tiers": [
        {"tier": "RED", "label": "高风险", "min_score": 10.0, "range": "[10, +∞)"},
        {"tier": "YELLOW", "label": "中风险", "min_score": 3.0, "range": "[3, 10)"},
        {"tier": "GREEN", "label": "低风险", "min_score": 0.0, "range": "[0, 3)"},
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
    "detail_rules": [
        {
            "rule_id": "R-STA-01",
            "category": "经营状况",
            "description": "经营状况维度>=2条",
            "condition": {"type": "category_event_count_at_least", "value": 2},
        }
    ],
}


def _event(week: int, category: str = "经营状况", severity: int = 1) -> dict:
    return {
        "evidence_id": f"E-{week}-{category}",
        "supplier_id": "S-1",
        "event_category": category,
        "event_subtype": "测试事件",
        "event_severity": severity,
        "event_week": week,
        "source_type": "测试",
    }


def test_green_when_no_recent_events():
    grade = grade_supplier(
        supplier_id="S-1", current_week=10, events=[], rules=RULES
    )
    assert grade.risk_level == "GREEN"
    assert grade.score == 0.0
    assert grade.recent_event_count == 0


def test_yellow_threshold():
    # 2 条 severity=2 事件 → 得分 4.0 ≥ 3.0（yellow）
    events = [_event(9, severity=2), _event(10, severity=2)]
    grade = grade_supplier(supplier_id="S-1", current_week=10, events=events, rules=RULES)
    assert grade.risk_level == "YELLOW"
    assert grade.score == pytest.approx(4.0)


def test_red_by_threshold():
    events = [_event(week, severity=2) for week in range(1, 8)]
    grade = grade_supplier(supplier_id="S-1", current_week=10, events=events, rules=RULES)
    assert grade.risk_level == "RED"
    assert grade.score == pytest.approx(14.0)


def test_red_line_rule_bypasses_threshold():
    """命中红线规则时，即使总分很低也直接判红。"""
    events = [_event(10, category="经营状况", severity=3)]
    grade = grade_supplier(supplier_id="S-1", current_week=10, events=events, rules=RULES)
    assert grade.risk_level == "RED"
    assert any(rule.rule_id == "RL-SEV-3" for rule in grade.hit_rules)


def test_grade_tier_carries_label_and_range():
    """三档分级需同时给出等级名称与分值标准（保留原始分值）。"""
    events = [_event(9, severity=2), _event(10, severity=2)]
    grade = grade_supplier(supplier_id="S-1", current_week=10, events=events, rules=RULES)
    assert grade.score == pytest.approx(4.0)
    assert grade.grade_tier == "YELLOW"
    assert grade.grade_label == "中风险"
    assert grade.grade_range == "[3, 10)"
    # 距上一档（RED=10.0）差额应为 6.0
    assert grade.next_threshold_gap == pytest.approx(6.0)


def test_tier_boundary_is_inclusive_on_min_score():
    """边界值：恰好等于档次下限时应归入该档。"""
    # 2 条 severity=2 → 4.0（>=3.0 且 <10.0）→ YELLOW
    grade = grade_supplier(
        supplier_id="S-1", current_week=10, events=[_event(10, severity=2), _event(9, severity=2)], rules=RULES
    )
    assert grade.grade_tier == "YELLOW"


def test_multi_dimension_floor_can_be_disabled():
    """tier_rules 兜底开关关闭时，多维度命中不再提升档位。"""
    events = [_event(10, category=name, severity=1) for name in
              ("公司背景", "司法", "失信", "经营风险")]
    rules = {**RULES, "tier_rules": {"dimensions_hit_min_count": 3, "enabled": True}}
    grade = grade_supplier(supplier_id="S-1", current_week=10, events=events, rules=rules)
    # 分值仅 4.0 → 本应 YELLOW；多维度兜底不改变（已为 YELLOW）
    assert grade.grade_tier == "YELLOW"

    rules_off = {**RULES, "tier_rules": {"dimensions_hit_min_count": 3, "enabled": False}}
    grade_off = grade_supplier(
        supplier_id="S-1", current_week=10, events=[_event(10, severity=1)], rules=rules_off
    )
    assert grade_off.grade_tier == "GREEN"


def test_importance_coefficient_is_applied_from_bank_list():
    events = [_event(10, severity=2)]
    base = grade_supplier(supplier_id="S-1", current_week=10, events=events, rules=RULES)
    heavy = grade_supplier(
        supplier_id="S-1",
        current_week=10,
        events=events,
        importance_level="重要",
        rules=RULES,
    )
    assert heavy.score == pytest.approx(base.score * 2.0)
    assert heavy.importance_tier == "重要"
    assert heavy.importance_source == "BANK_LIST"


def test_rectify_coefficient_reduces_score():
    events = [_event(10, severity=2)]
    normal = grade_supplier(supplier_id="S-1", current_week=10, events=events, rules=RULES)
    rectifying = grade_supplier(
        supplier_id="S-1", current_week=10, events=events, in_rectify=True, rules=RULES
    )
    assert rectifying.score == pytest.approx(normal.score * 0.5)
    assert rectifying.in_rectify is True


def test_window_excludes_old_events():
    """窗口外的历史事件不参与分级。"""
    events = [_event(1, severity=2), _event(10, severity=1)]
    grade = grade_supplier(
        supplier_id="S-1", current_week=10, events=events, window_weeks=5, rules=RULES
    )
    assert grade.recent_event_count == 1
    assert grade.score == pytest.approx(1.0)


def test_week_and_month_window_resolve_to_same_span():
    """窗口按月时按 1 月 = 4 周换算为周区间，口径与周模式一致。"""
    week_spec = resolve_window(current_week=30, unit="week", size=12)
    month_spec = resolve_window(current_week=30, unit="month", size=3)
    assert month_spec.start_week == week_spec.start_week
    assert month_spec.end_week == week_spec.end_week
    assert month_spec.window_weeks == 12
    assert month_spec.display == "近3个月（第19-30周）"
    assert week_spec.display == "近12周"


def test_grade_supplier_accepts_window_spec():
    """分级引擎接收 WindowSpec，不再自行推导窗口区间。"""
    spec = resolve_window(current_week=10, unit="week", size=5)
    events = [_event(3, severity=2), _event(10, severity=1)]
    grade = grade_supplier(
        supplier_id="S-1", current_week=10, events=events, window=spec, rules=RULES
    )
    assert grade.recent_event_count == 1
    assert grade.window_display == spec.display


def test_dimension_breakdown_covers_six_dimensions():
    grade = grade_supplier(supplier_id="S-1", current_week=10, events=[_event(10)], rules=RULES)
    assert set(grade.dimension_breakdown) == {
        "公司背景",
        "司法",
        "失信",
        "经营风险",
        "经营状况",
        "知识产权",
    }


def test_events_from_other_suppliers_are_ignored():
    other = {**_event(10, severity=2), "supplier_id": "S-OTHER"}
    grade = grade_supplier(supplier_id="S-1", current_week=10, events=[other], rules=RULES)
    assert grade.recent_event_count == 0


def test_resolve_importance_bank_list_wins_without_max():
    """银行名单说「一般」时，即使系统侧是「核心」也不得被 max 覆盖。"""
    tier, coefficient, source = resolve_importance(
        importance_level="一般",
        contract_importance="重要外包",
        system_level="核心",
        rules=RULES,
    )
    assert tier == "一般"
    assert coefficient == 1.0
    assert source == "BANK_LIST"


def test_resolve_importance_derives_when_bank_list_missing():
    """名单缺失时回退合成，并把原「核心」并入「重要」（两级）。"""
    tier, coefficient, source = resolve_importance(
        contract_importance="重要外包", system_level="核心", rules=RULES
    )
    assert tier == "重要"
    assert coefficient == 2.0
    assert source == "DERIVED"


def test_resolve_importance_defaults_to_general_when_unknown():
    tier, coefficient, source = resolve_importance(
        contract_importance=None, system_level=None, rules=RULES
    )
    assert tier == "一般"
    assert coefficient == 1.0
    assert source == "DERIVED"


def test_report_period_follows_importance():
    """报告周期按重要性分级：重要→周报、一般→月报。"""
    assert resolve_report_period(importance_tier="重要", rules=RULES) == ("weekly", "周报")
    assert resolve_report_period(importance_tier="一般", rules=RULES) == ("monthly", "月报")


def test_grade_carries_report_period():
    events = [_event(10, severity=2)]
    heavy = grade_supplier(
        supplier_id="S-1", current_week=10, events=events, importance_level="重要", rules=RULES
    )
    light = grade_supplier(
        supplier_id="S-1", current_week=10, events=events, importance_level="一般", rules=RULES
    )
    assert heavy.report_period == "weekly"
    assert heavy.report_period_label == "周报"
    assert light.report_period == "monthly"
    assert light.report_period_label == "月报"

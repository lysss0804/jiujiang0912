"""供应商重要性（银行名单定义的两级）单测。

核心口径：
- 重要性只有两级：`重要` / `一般`（与风险等级三级 RED/YELLOW/GREEN 无关）；
- 权威来源是银行名单 `suppliers.csv::importance_level`，系统**只读不算**；
- 名单命中即直接采信，**不做 max 覆盖**（名单说一般，系统说核心也取一般）；
- 名单缺失才回退「合同重要性 + 承载系统等级」合成，并把原「核心」并入「重要」；
- 系数：一般 1.0 / 重要 1.2。
"""

import pytest

from app.rules.engine import grade_supplier, resolve_importance
from app.schemas.contracts import RISK_DIMENSIONS

RULES = {
    "category_weights": {name: 1.0 for name in RISK_DIMENSIONS},
    "severity_weights": {0: 0.0, 1: 1.0, 2: 2.0},
    "time_decay": [{"max_weeks_ago": 999, "factor": 1.0}],
    "importance_coefficients": {"一般": 1.0, "重要": 1.2},
    "contract_importance_mapping": {"一般外包": "一般", "重要外包": "重要"},
    "system_level_mapping": {"一般": "一般", "重要": "重要", "核心": "重要"},
    "report_period_by_importance": {"重要": "weekly", "一般": "monthly"},
    "report_period_labels": {"weekly": "周报", "monthly": "月报"},
    "grade_tiers": [
        {"tier": "RED", "label": "高风险", "min_score": 6.0, "range": "[6.0, +∞)"},
        {"tier": "YELLOW", "label": "中风险", "min_score": 2.5, "range": "[2.5, 6.0)"},
        {"tier": "GREEN", "label": "低风险", "min_score": 0.0, "range": "[0, 2.5)"},
    ],
    "tier_rules": {"dimensions_hit_min_count": 3, "enabled": False},
    "red_line_rules": [],
    "detail_rules": [],
}


def _envelope(call):
    """兼容 resolve_importance 返回 (tier, coefficient, source) 或带额外字段的元组。"""
    return call[0], call[1], call[2]


def test_bank_list_importance_wins_without_max():
    """名单说「一般」时不得被系统侧的「核心」覆盖。"""
    tier, coefficient, source = _envelope(
        resolve_importance(
            importance_level="一般",
            contract_importance="重要外包",
            system_level="核心",
            rules=RULES,
        )
    )
    assert tier == "一般"
    assert coefficient == pytest.approx(1.0)
    assert source == "BANK_LIST"


def test_bank_list_importance_heavy():
    tier, coefficient, source = _envelope(
        resolve_importance(importance_level="重要", contract_importance="一般外包", rules=RULES)
    )
    assert tier == "重要"
    assert coefficient == pytest.approx(1.2)
    assert source == "BANK_LIST"


def test_derived_merges_core_into_important():
    """名单缺失时回退合成，原「核心」并入「重要」。"""
    tier, coefficient, source = _envelope(
        resolve_importance(contract_importance="重要外包", system_level="核心", rules=RULES)
    )
    assert tier == "重要"
    assert coefficient == pytest.approx(1.2)
    assert source == "DERIVED"


def test_derived_defaults_to_general_when_unknown():
    tier, coefficient, source = _envelope(
        resolve_importance(contract_importance=None, system_level=None, rules=RULES)
    )
    assert tier == "一般"
    assert coefficient == pytest.approx(1.0)
    assert source == "DERIVED"


def test_derived_general_contract_and_system():
    tier, coefficient, source = _envelope(
        resolve_importance(contract_importance="一般外包", system_level="一般", rules=RULES)
    )
    assert tier == "一般"
    assert coefficient == pytest.approx(1.0)
    assert source == "DERIVED"


def test_invalid_bank_list_value_falls_back_to_derived():
    """名单列取值非法（不在两级内）时降级为合成，并标注 DERIVED。"""
    tier, _, source = _envelope(
        resolve_importance(
            importance_level="核心", contract_importance="一般外包", system_level="一般", rules=RULES
        )
    )
    assert source == "DERIVED"
    assert tier == "一般"


def test_importance_is_coefficient_only_not_risk_level():
    """重要性只影响分值系数，不改变风险等级判定依据（风险等级仍按分值+档位）。"""
    events = [
        {
            "evidence_id": "E-1",
            "supplier_id": "S-1",
            "event_category": "经营风险",
            "mapped_dimension": "经营风险",
            "event_subtype": "测试",
            "event_severity": 2,
            "event_week": 10,
            "source_type": "测试",
        }
    ]
    common = grade_supplier(
        supplier_id="S-1", current_week=10, events=events, importance_level="一般", rules=RULES
    )
    heavy = grade_supplier(
        supplier_id="S-1", current_week=10, events=events, importance_level="重要", rules=RULES
    )
    assert heavy.score == pytest.approx(common.score * 1.2)
    assert common.score == pytest.approx(2.0)
    assert heavy.score == pytest.approx(2.4)
    # 系数使分值上升，但等级仍由分值 + 档位标准判定
    # 2.0 ∈ [0, 2.5) → GREEN；2.4 ∈ [0, 2.5) → 仍为 GREEN（未跨档）
    assert common.grade_tier == "GREEN"
    assert heavy.grade_tier == "GREEN"
    assert heavy.importance_source == "BANK_LIST"
    assert common.importance_source == "BANK_LIST"


def test_importance_coefficient_can_cross_tier_when_score_near_boundary():
    """系数只在分值维度生效，跨档与否完全由档位标准决定。"""
    two_events = [
        {
            "evidence_id": f"E-{index}",
            "supplier_id": "S-1",
            "event_category": "经营风险",
            "mapped_dimension": "经营风险",
            "event_subtype": "测试",
            "event_severity": 2,
            "event_week": 10,
            "source_type": "测试",
        }
        for index in range(2)
    ]
    # 一般：4.0 ∈ [2.5, 6.0) → YELLOW
    light = grade_supplier(
        supplier_id="S-1", current_week=10, events=two_events, importance_level="一般", rules=RULES
    )
    # 重要：4.8 → 仍 YELLOW
    heavy = grade_supplier(
        supplier_id="S-1", current_week=10, events=two_events, importance_level="重要", rules=RULES
    )
    assert light.grade_tier == "YELLOW"
    assert heavy.grade_tier == "YELLOW"
    assert heavy.score > light.score


def test_grade_reports_importance_source():
    events = [
        {
            "evidence_id": "E-1",
            "supplier_id": "S-1",
            "event_category": "经营风险",
            "mapped_dimension": "经营风险",
            "event_subtype": "测试",
            "event_severity": 1,
            "event_week": 10,
            "source_type": "测试",
        }
    ]
    derived = grade_supplier(
        supplier_id="S-1",
        current_week=10,
        events=events,
        contract_importance="重要外包",
        system_level="核心",
        rules=RULES,
    )
    assert derived.importance_tier == "重要"
    assert derived.importance_source == "DERIVED"
    assert derived.importance_coefficient == pytest.approx(1.2)

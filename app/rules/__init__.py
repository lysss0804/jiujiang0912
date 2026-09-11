"""可配置的供应商当前风险分级引擎。

分级结果仅用于报告展示，不参与任何自动处置决策。
"""

from app.rules.config import get_rules, load_rules
from app.rules.engine import (
    grade_supplier,
    is_minor_issue,
    resolve_grade_tier,
    resolve_importance,
    resolve_report_period,
)
from app.rules.window import WindowSpec, resolve_window

__all__ = [
    "get_rules",
    "load_rules",
    "grade_supplier",
    "resolve_grade_tier",
    "resolve_importance",
    "resolve_report_period",
    "is_minor_issue",
    "WindowSpec",
    "resolve_window",
]

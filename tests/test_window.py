"""统计窗口解析单测：周/月换算、优先级、边界与可读描述。"""

import pytest

from app.rules.window import WindowSpec, resolve_window, shift_window


def test_default_window_matches_legacy_behaviour():
    """不传任何参数时，与既有「近 12 周」口径完全等价。"""
    spec = resolve_window(current_week=52)
    assert spec.unit == "week"
    assert spec.size == 12
    assert spec.window_weeks == 12
    assert spec.start_week == 41
    assert spec.end_week == 52
    assert spec.display == "近12周"


def test_week_window_display_is_plain():
    spec = resolve_window(current_week=30, unit="week", size=8)
    assert spec.start_week == 23
    assert spec.end_week == 30
    assert spec.display == "近8周"


def test_month_window_converts_one_month_to_four_weeks():
    """1 月 = 4 周：近 3 个月应等于近 12 周。"""
    week_spec = resolve_window(current_week=30, unit="week", size=12)
    month_spec = resolve_window(current_week=30, unit="month", size=3)
    assert month_spec.window_weeks == 12
    assert month_spec.start_week == week_spec.start_week
    assert month_spec.end_week == week_spec.end_week
    assert month_spec.unit == "month"
    assert month_spec.size == 3


def test_month_window_display_keeps_week_range():
    spec = resolve_window(current_week=30, unit="month", size=3)
    assert spec.display == "近3个月（第19-30周）"


def test_weeks_per_month_is_configurable():
    spec = resolve_window(current_week=30, unit="month", size=2, weeks_per_month=5)
    assert spec.window_weeks == 10
    assert spec.start_week == 21


def test_start_week_never_drops_below_one():
    """窗口跨越年初时起点钳制为第 1 周。"""
    spec = resolve_window(current_week=3, unit="week", size=12)
    assert spec.start_week == 1
    assert spec.end_week == 3
    assert spec.window_weeks == 12


def test_explicit_parameters_override_rules_and_settings():
    """显式入参优先级最高，压过 Settings 与 rules.yaml。"""
    rules = {"window_unit": "month", "window_size": 6, "weeks_per_month": 4}
    spec = resolve_window(current_week=40, unit="week", size=4, rules=rules)
    assert spec.unit == "week"
    assert spec.size == 4
    assert spec.window_weeks == 4


def test_rules_used_when_settings_and_request_omit_parameters(monkeypatch: pytest.MonkeyPatch):
    """Settings 与请求均未指定时，回落到 rules.yaml 的窗口配置。"""
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "window_unit", None, raising=False)
    monkeypatch.setattr(settings, "window_size", None, raising=False)
    monkeypatch.setattr(settings, "window_weeks_per_month", None, raising=False)

    rules = {"window_unit": "month", "window_size": 2, "weeks_per_month": 4}
    spec = resolve_window(current_week=40, rules=rules)
    assert spec.unit == "month"
    assert spec.size == 2
    assert spec.window_weeks == 8


def test_invalid_unit_falls_back_to_week():
    spec = resolve_window(current_week=20, unit="quarter", size=4)
    assert spec.unit == "week"
    assert spec.display == "近4周"


def test_shift_window_returns_previous_period():
    spec = resolve_window(current_week=30, unit="week", size=12)
    start, end = shift_window(spec, offset_weeks=-spec.window_weeks)
    assert end == spec.start_week - 1
    assert start == end - spec.window_weeks + 1


def test_window_spec_rejects_out_of_range_values():
    with pytest.raises(Exception):
        WindowSpec(unit="week", size=0, current_week=10, start_week=1, end_week=10)

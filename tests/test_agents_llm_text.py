"""三个文案节点（风险识别 / 关联分析 / 证据转述）的 LLM 生成与降级路径测试。

这些测试不访问网络：通过 monkeypatch 替换 `app.llm.factory.build_llm_client`，
分别验证「真实模型产出文案」与「不可用时回退确定性模板并标记 FALLBACK」两条路径。
"""

from __future__ import annotations

from typing import Any

import pytest


class _StubLLM:
    """返回预置 JSON 的假客户端，用于模拟结构化输出。"""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response

    def invoke(self, *, system_prompt: str, user_prompt: str, response_model):
        del system_prompt, user_prompt
        return response_model.model_validate(self.response)


class _BrokenLLM:
    def invoke(self, *, system_prompt: str, user_prompt: str, response_model):
        raise RuntimeError("llm unavailable")


def _use_stub(monkeypatch: pytest.MonkeyPatch, response: dict[str, Any]) -> None:
    monkeypatch.setattr(
        "app.llm.factory.build_llm_client", lambda settings=None: _StubLLM(response)
    )


def _use_broken(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.llm.factory.build_llm_client", lambda settings=None: _BrokenLLM()
    )


SUPPLIER = "S-TEST-01"


def _events() -> list[dict]:
    """两条事件分属两个新维度（经营风险 / 经营状况），便于验证跨维度判定。"""
    return [
        {
            "evidence_id": "E-TEST-1",
            "supplier_id": SUPPLIER,
            "event_category": "经营",
            "event_subtype": "重大数据泄露",
            "event_severity": 4,
            "event_week": 9,
            "source_type": "安全公告",
            "mapped_dimension": "经营风险",
        },
        {
            "evidence_id": "E-TEST-2",
            "supplier_id": SUPPLIER,
            "event_category": "履约",
            "event_subtype": "交付延期",
            "event_severity": 2,
            "event_week": 10,
            "source_type": "履约记录",
            "mapped_dimension": "经营状况",
        },
    ]


def _base_state(**overrides) -> dict:
    state = {
        "supplier_id": SUPPLIER,
        "current_week": 10,
        "events": _events(),
        "risk_grade": {
            "risk_level": "YELLOW",
            "grade_tier": "YELLOW",
            "grade_label": "中风险",
            "grade_range": "[0.8, 2.0)",
            "score": 3.5,
            "grade_basis": "命中事件加权得分 3.5",
            "window_weeks": 12,
            "window_display": "近12周",
            "hit_rules": [{"rule_id": "RL-SEV-3", "description": "严重事件"}],
            "in_rectify": False,
        },
        "business_context": {"business_exposure": 0.5},
        "enable_live_llm": True,
    }
    state.update(overrides)
    return state


# --------------------------------------------------------------------------- #
# 风险识别
# --------------------------------------------------------------------------- #
def test_risk_identification_uses_model_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_stub(monkeypatch, {"summary": "模型生成的风险识别摘要。"})
    from app.agents.risk_identification import risk_identification_node

    identification = risk_identification_node(_base_state())["risk_identification_result"]
    assert identification["summary"] == "模型生成的风险识别摘要。"
    assert identification["llm_status"] == "SUCCESS"
    # 数值统计仍来自规则引擎/确定性计算
    assert identification["risk_level"] == "YELLOW"
    assert identification["main_risks"]


def test_risk_identification_falls_back_when_model_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_broken(monkeypatch)
    from app.agents.risk_identification import risk_identification_node

    identification = risk_identification_node(_base_state())["risk_identification_result"]
    assert identification["llm_status"] == "FALLBACK"
    assert "当前风险等级：YELLOW" in identification["summary"]


def test_risk_identification_disabled_when_live_llm_off() -> None:
    from app.agents.risk_identification import risk_identification_node

    identification = risk_identification_node(_base_state(enable_live_llm=False))[
        "risk_identification_result"
    ]
    assert identification["llm_status"] == "DISABLED"
    assert "当前风险等级：YELLOW" in identification["summary"]


# --------------------------------------------------------------------------- #
# 关联分析
# --------------------------------------------------------------------------- #
def test_association_uses_model_narrative_but_keeps_deterministic_trend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_stub(
        monkeypatch,
        {
            "trend_desc": "模型生成的趋势解读。",
            "cross_dimension_note": "模型生成的跨维度说明。",
            "trend_support_evidence_ids": ["E-TEST-1", "E-TEST-2"],
        },
    )
    from app.agents.association_analysis import association_analysis_node

    result = association_analysis_node(_base_state())
    assert result["risk_trend"]["trend_desc"] == "模型生成的趋势解读。"
    assert result["association_result"]["cross_dimension_note"] == "模型生成的跨维度说明。"
    assert result["association_result"]["llm_status"] == "SUCCESS"
    # 趋势类型与跨维度判定仍是确定性的（两条事件分属两个新维度）
    assert result["risk_trend"]["trend_type"] in {"RISING", "STEADY", "FALLING", "SUDDEN_JUMP"}
    assert result["association_result"]["cross_dimension"] is True
    assert result["association_result"]["window_display"] == "近12周"


def test_association_falls_back_on_model_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_broken(monkeypatch)
    from app.agents.association_analysis import association_analysis_node

    result = association_analysis_node(_base_state())
    assert result["association_result"]["llm_status"] == "FALLBACK"
    assert "风险" in result["risk_trend"]["trend_desc"]


def test_association_rejects_invented_evidence_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """模型引用越界证据编号时必须被拦截并降级。"""
    _use_stub(
        monkeypatch,
        {
            "trend_desc": "非法引用。",
            "cross_dimension_note": "非法引用。",
            "trend_support_evidence_ids": ["E-INVENTED"],
        },
    )
    from app.agents.association_analysis import association_analysis_node

    result = association_analysis_node(_base_state())
    assert result["association_result"]["llm_status"] == "FALLBACK"
    assert "E-INVENTED" not in result["risk_trend"]["trend_support_evidence_ids"]


# --------------------------------------------------------------------------- #
# 证据转述
# --------------------------------------------------------------------------- #
def _evidence_state(**overrides) -> dict:
    state = _base_state()
    state["visible_events"] = _events()
    state["risk_trend"] = {"trend_support_evidence_ids": ["E-TEST-1"]}
    state.update(overrides)
    return state


def test_evidence_uses_model_narration(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_stub(
        monkeypatch,
        {
            "narrations": [
                {"evidence_id": "E-TEST-1", "readable_summary": "模型转述：第9周安全事件。"},
                {"evidence_id": "E-TEST-2", "readable_summary": "模型转述：第10周履约延期。"},
            ]
        },
    )
    from app.agents.evidence import evidence_node

    result = evidence_node(_evidence_state())
    items = {
        item["evidence_id"]: item["readable_summary"]
        for item in result["evidence_result"]["evidence_items"]
    }
    assert items["E-TEST-1"] == "模型转述：第9周安全事件。"
    assert items["E-TEST-2"] == "模型转述：第10周履约延期。"
    assert result["evidence_result"]["llm_status"] == "SUCCESS"
    # 归属/时间校验仍确定性通过
    assert result["evidence_result"]["status"] == "PASS"


def test_evidence_falls_back_to_template_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_broken(monkeypatch)
    from app.agents.evidence import evidence_node

    result = evidence_node(_evidence_state())
    assert result["evidence_result"]["llm_status"] == "FALLBACK"
    summaries = [item["readable_summary"] for item in result["evidence_result"]["evidence_items"]]
    assert all(summaries)
    # 模板转述包含来源字段
    assert any("安全公告" in summary for summary in summaries)


def test_evidence_ignores_out_of_scope_narration(monkeypatch: pytest.MonkeyPatch) -> None:
    """模型返回越界编号时应丢弃该条并回退模板，不污染证据链。"""
    _use_stub(
        monkeypatch,
        {"narrations": [{"evidence_id": "E-INVENTED", "readable_summary": "越界转述"}]},
    )
    from app.agents.evidence import evidence_node

    result = evidence_node(_evidence_state())
    summaries = [item["readable_summary"] for item in result["evidence_result"]["evidence_items"]]
    assert all("越界转述" != summary for summary in summaries)


def test_evidence_does_not_call_model_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(settings=None):
        raise AssertionError("disabled 时不应调用模型")

    monkeypatch.setattr("app.llm.factory.build_llm_client", _boom)
    from app.agents.evidence import evidence_node

    result = evidence_node(_evidence_state(enable_live_llm=False))
    assert result["evidence_result"]["llm_status"] == "DISABLED"

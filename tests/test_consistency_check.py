"""多智能体一致性校验节点测试：正常输出 / 越界引用 / 模型失败 / 证据门禁。"""

from __future__ import annotations

from typing import Any

import pytest

from app.config import get_settings


class _StubLLM:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response

    def invoke(self, *, system_prompt: str, user_prompt: str, response_model):
        del system_prompt, user_prompt
        return response_model.model_validate(self.response)


class _BrokenLLM:
    def invoke(self, *, system_prompt: str, user_prompt: str, response_model):
        raise RuntimeError("llm unavailable")


def _state(**overrides) -> dict:
    state = {
        "supplier_id": "S-TEST-01",
        "current_week": 10,
        "enable_live_llm": True,
        "risk_grade": {
            "risk_level": "YELLOW",
            "score": 3.5,
            "grade_basis": "命中事件加权得分",
            "hit_rules": [{"rule_id": "RL-SEV-3"}],
        },
        "risk_identification_result": {"risk_level": "YELLOW", "summary": "识别摘要", "main_risks": []},
        "association_result": {
            "active_dimensions": ["安全"],
            "cross_dimension": False,
            "cross_dimension_note": "单一维度",
        },
        "risk_trend": {"trend_type": "STEADY", "trend_desc": "基本持平"},
        "evidence_result": {
            "status": "PASS",
            "evidence_items": [{"evidence_id": "E-TEST-1"}],
            "reasons": [],
        },
        "llm_advisory": {
            "status": "SUCCESS",
            "risk_summary": "总结",
            "recommendation": "建议观察",
            "rationale": "理由",
            "policy_chunk_ids": ["P-01"],
        },
        "policy_context": [{"chunk_id": "P-01"}],
    }
    state.update(overrides)
    return state


def test_consistency_check_reports_model_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.llm.factory.build_llm_client",
        lambda settings=None: _StubLLM(
            {
                "consistent": False,
                "conflicts": ["风险等级与识别结论不一致"],
                "evidence_sufficient": False,
                "notes": "存在矛盾，建议人工重点复核。",
            }
        ),
    )
    from app.agents.consistency_check import consistency_check_node

    result = consistency_check_node(_state())
    check = result["consistency_check_result"]
    assert check["status"] == "SUCCESS"
    assert check["consistent"] is False
    assert check["conflicts"] == ["风险等级与识别结论不一致"]
    assert check["evidence_sufficient"] is False


def test_consistency_check_falls_back_when_model_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.llm.factory.build_llm_client", lambda settings=None: _BrokenLLM())
    from app.agents.consistency_check import consistency_check_node

    result = consistency_check_node(_state())
    check = result["consistency_check_result"]
    assert check["status"] == "FALLBACK"
    assert check["consistent"] is None
    # 单点失败不阻断主流程
    assert any("Consistency check unavailable" in item for item in result.get("errors", []))


def test_consistency_check_disabled_without_live_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(settings=None):
        raise AssertionError("disabled 时不应调用模型")

    monkeypatch.setattr("app.llm.factory.build_llm_client", _boom)
    from app.agents.consistency_check import consistency_check_node

    result = consistency_check_node(_state(enable_live_llm=False))
    assert result["consistency_check_result"]["status"] == "DISABLED"


def test_consistency_check_skipped_when_evidence_gate_blocked() -> None:
    from app.agents.consistency_check import consistency_check_node

    result = consistency_check_node(_state(evidence_result={"status": "FAIL"}))
    assert result["consistency_check_result"]["status"] == "SKIPPED"


def test_consistency_check_respects_node_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """节点级开关关闭时即使 enable_live_llm=true 也不调用模型。"""
    monkeypatch.setattr(get_settings(), "llm_consistency_check", False, raising=False)

    def _boom(settings=None):
        raise AssertionError("节点开关关闭时不应调用模型")

    monkeypatch.setattr("app.llm.factory.build_llm_client", _boom)
    from app.agents.consistency_check import consistency_check_node

    result = consistency_check_node(_state())
    assert result["consistency_check_result"]["status"] == "DISABLED"

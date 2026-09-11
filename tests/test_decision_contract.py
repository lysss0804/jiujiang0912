"""决策 Agent 的真实链路契约测试。

这些用例针对的是「真调用 LLM 时才会暴露」的缺陷，而不是 mock 下的分支覆盖：

1. **工厂可拦截**：`decision_node` 若用模块级 `from ... import build_llm_client`，
   测试注入的假客户端会被绕过、真实打网。生产代码改走运行时工厂后必须可拦截。
2. **提示词约束落地**：系统提示词禁止越界枚举与自动处置，但此前只靠 Pydantic 字面量
   约束 `suggest_type`（`alert` 本身合法），意味着模型建议「自动停服」会被全程放过。
3. **优先级与风险等级自洽**：模型可能给 RED 供应商标 LOW，误导人工排序。
4. **空 key_factors**：模型返回空占位内容时不应污染报告。
"""

from __future__ import annotations

import pytest

from app.agents.decision import (
    _normalize_key_factors,
    _normalize_priority,
    _validate_candidate_actions,
)
from app.schemas.contracts import AdvisoryResult


def _advisory(**overrides) -> AdvisoryResult:
    payload = {
        "risk_summary": "测试",
        "key_factors": ["命中规则 R-1：测试规则"],
        "recommendation": "建议人工复核",
        "rationale": "测试",
        "evidence_ids": [],
        "policy_chunk_ids": [],
        "candidate_actions": [
            {
                "suggest_type": "observe",
                "suggest_content": "人工复核",
                "suggest_priority": "LOW",
                "execution_status": "PENDING_HUMAN_REVIEW",
            }
        ],
    }
    payload.update(overrides)
    return AdvisoryResult.model_validate(payload)


# --------------------------------------------------------------------------- #
# 1. 工厂可拦截：真实链路必须走运行时工厂，测试注入才能生效
# --------------------------------------------------------------------------- #
def test_decision_node_uses_runtime_factory_so_patch_takes_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """patch `app.llm.factory.build_llm_client` 必须能拦到 decision 节点。

    这是此前 3 个测试失败的根因：模块级绑定让 patch 失效，节点真实打网。
    """
    calls: list[str] = []

    class _Stub:
        def invoke(self, *, system_prompt: str, user_prompt: str, response_model):
            calls.append("hit")
            return response_model.model_validate(
                {
                    "risk_summary": "STUB 风险总结",
                    "key_factors": ["STUB 关键因素"],
                    "recommendation": "STUB 建议",
                    "rationale": "STUB 理由",
                    "evidence_ids": [],
                    "policy_chunk_ids": [],
                    "candidate_actions": [
                        {
                            "suggest_type": "observe",
                            "suggest_content": "STUB",
                            "suggest_priority": "LOW",
                            "execution_status": "PENDING_HUMAN_REVIEW",
                        }
                    ],
                }
            )

    monkeypatch.setattr("app.llm.factory.build_llm_client", lambda settings=None: _Stub())

    from app.agents.decision import decision_node

    state = {
        "supplier_id": "S-TEST-01",
        "current_week": 10,
        "events": [],
        "supplier_profile": {},
        "risk_grade": {"risk_level": "GREEN", "score": 0.5, "grade_basis": "无显著风险"},
        "risk_identification_result": {"summary": "识别", "main_risks": [], "hit_rules": []},
        "association_result": {},
        "risk_trend": {},
        "evidence_result": {
            "status": "PASS",
            "evidence_items": [{"evidence_id": "E-1", "readable_summary": "事件"}],
        },
        "policy_context": [{"chunk_id": "P-1", "text": "制度"}],
        "enable_live_llm": True,
    }

    result = decision_node(state)
    assert calls == ["hit"], "decision 节点未走运行时工厂，patch 被绕过（会真实打网）"
    assert result["llm_advisory"]["status"] == "SUCCESS"
    assert result["llm_advisory"]["risk_summary"] == "STUB 风险总结"


def test_decision_node_falls_back_when_model_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Broken:
        def invoke(self, *, system_prompt: str, user_prompt: str, response_model):
            raise RuntimeError("model unavailable")

    monkeypatch.setattr("app.llm.factory.build_llm_client", lambda settings=None: _Broken())

    from app.agents.decision import decision_node

    result = decision_node(
        {
            "supplier_id": "S-TEST-01",
            "current_week": 10,
            "supplier_profile": {},
            "risk_grade": {"risk_level": "YELLOW", "score": 3.0, "grade_basis": "命中事件"},
            "risk_identification_result": {"main_risks": [], "hit_rules": []},
            "association_result": {},
            "risk_trend": {},
            "evidence_result": {"status": "PASS", "evidence_items": []},
            "policy_context": [{"chunk_id": "P-1", "text": "制度"}],
            "enable_live_llm": True,
        }
    )
    assert result["llm_advisory"]["status"] == "FALLBACK"
    assert result["llm_advisory"]["reason"] == "RuntimeError"
    # 兜底文案仍然可用
    assert result["llm_advisory"]["risk_summary"]
    assert result["candidate_actions"][0]["execution_status"] == "PENDING_HUMAN_REVIEW"


# --------------------------------------------------------------------------- #
# 2. 提示词约束必须真正落地为校验
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "forbidden",
    ["建议自动停服并解约", "对该供应商自动处罚", "直接处罚并自动终止合同"],
)
def test_forbidden_automatic_disposal_is_rejected(forbidden: str) -> None:
    """模型不得越界给出自动处置动作（人工边界），命中即降级。"""
    advisory = _advisory(
        recommendation=forbidden,
        candidate_actions=[
            {
                "suggest_type": "alert",
                "suggest_content": forbidden,
                "suggest_priority": "HIGH",
                "execution_status": "PENDING_HUMAN_REVIEW",
            }
        ],
    )
    with pytest.raises(ValueError, match="human-in-the-loop boundary"):
        _validate_candidate_actions(advisory)


def test_legal_candidate_actions_pass_validation() -> None:
    advisory = _advisory(
        candidate_actions=[
            {
                "suggest_type": "rectify",
                "suggest_content": "建议要求其提交整改计划并跟踪整改结果",
                "suggest_priority": "MEDIUM",
                "execution_status": "PENDING_HUMAN_REVIEW",
            }
        ]
    )
    _validate_candidate_actions(advisory)


# --------------------------------------------------------------------------- #
# 3. 建议优先级必须与风险等级自洽
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("level", "requested", "expected"),
    [
        ("RED", "LOW", "HIGH"),
        ("RED", "MEDIUM", "HIGH"),
        ("YELLOW", "LOW", "MEDIUM"),
        ("GREEN", "LOW", "LOW"),
        ("GREEN", "HIGH", "HIGH"),  # 保守上调允许保留
    ],
)
def test_priority_is_reconciled_with_risk_level(level: str, requested: str, expected: str) -> None:
    advisory = _advisory(
        candidate_actions=[
            {
                "suggest_type": "observe",
                "suggest_content": "建议",
                "suggest_priority": requested,
                "execution_status": "PENDING_HUMAN_REVIEW",
            }
        ]
    )
    _normalize_priority(level, advisory)
    assert advisory.candidate_actions[0].suggest_priority == expected


# --------------------------------------------------------------------------- #
# 4. key_factors 空值 / 占位模板清洗
# --------------------------------------------------------------------------- #
def test_placeholder_key_factors_are_replaced_by_rule_engine_facts() -> None:
    """模型抄回提示词占位模板时，必须换成规则引擎的确定性事实。

    真实数据下观察到的输出：「命中规则 R-XXX：<规则描述>」「XX维度事件增多」，
    对人工研判零信息量。
    """
    advisory = _advisory(key_factors=["命中规则 R-XXX：<规则描述>", "XX维度事件增多"])
    identification = {
        "hit_rules": [{"rule_id": "R-SEC-01", "description": "近期出现安全类风险事件"}],
        "main_risks": [{"dimension": "安全", "event_count": 1, "subtypes": ["重大数据泄露"]}],
    }
    _normalize_key_factors(advisory, identification)
    assert advisory.key_factors == [
        "命中规则 R-SEC-01：近期出现安全类风险事件",
        "安全维度：1条事件（重大数据泄露）",
    ]
    assert all("XXX" not in item and "<" not in item for item in advisory.key_factors)


def test_empty_key_factors_trigger_fallback() -> None:
    advisory = _advisory(key_factors=["   ", ""])
    with pytest.raises(ValueError, match="no usable key_factors"):
        _normalize_key_factors(advisory, {})


def test_key_factors_are_trimmed_and_capped() -> None:
    advisory = _advisory(key_factors=[" 因素A ", "因素B"])
    _normalize_key_factors(advisory, {})
    assert advisory.key_factors == ["因素A", "因素B"]


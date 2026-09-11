import pytest

from app.demo import run_demo
from app.schemas.output_contract import build_public_assessment


def test_workflow_reaches_human_review_with_verified_evidence():
    result = run_demo(current_week=10, include_events=True)
    assert result["evidence_result"]["status"] == "PASS"
    assert result["status"] == "PENDING_HUMAN_REVIEW"
    assert result["candidate_actions"][0]["execution_status"] == "PENDING_HUMAN_REVIEW"
    public = build_public_assessment(result)
    assert public["schema_version"] == "C-DRAFT-V0.2"
    assert "events" not in public
    assert "prediction" not in public


def test_workflow_outputs_risk_grade_and_report():
    result = run_demo(current_week=10, include_events=True)
    grade = result["risk_grade"]
    assert grade["risk_level"] in {"RED", "YELLOW", "GREEN"}
    assert grade["window_weeks"] >= 1
    assert set(grade["dimension_breakdown"]) == {
        "公司背景",
        "司法",
        "失信",
        "经营风险",
        "经营状况",
        "知识产权",
    }
    # 保留分值并给出等级与分值标准
    assert grade["grade_tier"] == grade["risk_level"]
    assert grade["grade_label"]
    assert grade["grade_range"]
    assert grade["window_display"]
    # 供应商重要性为两级，并与风险等级（三级）解耦
    assert grade["importance_tier"] in {"重要", "一般"}
    assert grade["importance_source"] in {"BANK_LIST", "DERIVED"}

    report = result["risk_report"]
    assert report["risk_grade"]["risk_level"] == grade["risk_level"]
    assert report["risk_summary"]
    assert report["recommendation"]
    # 六维度分布完整，且每个维度带数据状态
    assert len(report["dimension_breakdown"]) == 6
    assert all(
        item["data_status"] in {"DATA", "NO_DATA"} for item in report["dimension_breakdown"]
    )
    # 处置提示块存在
    assert report["disposition"]["disposition_state"] in {
        "PENDING_HUMAN_REVIEW",
        "EVIDENCE_INSUFFICIENT",
        "AWAITING_SUPPLIER_REDECLARE",
    }
    # 证据可读化
    assert all(item["readable_summary"] for item in report["evidence_summary"])


def test_evidence_fail_blocks_decision_agent():
    result = run_demo(current_week=10, include_events=False)
    assert result["evidence_result"]["status"] == "FAIL"
    assert result["status"] == "EVIDENCE_INSUFFICIENT"
    assert not result.get("candidate_actions")
    assert all(item["agent_name"] != "DecisionAgent" for item in result["audit_trace"])
    # 证据不足时报告仍可生成（降级可用）
    assert result["risk_report"]["human_review_status"] == "EVIDENCE_INSUFFICIENT"


def test_red_risk_uses_deep_dive_route():
    """命中红线时应走高风险管理链路，体现条件路由协同。"""
    from app.agents.coordinator import HIGH_RISK_ROUTE
    from app.workflow.graph import build_workflow
    from uuid import uuid4

    workflow = build_workflow()
    result = workflow.invoke(
        {
            "run_id": str(uuid4()),
            "supplier_id": "S-TEST-RED",
            "current_week": 10,
            "events": [
                {
                    "evidence_id": "E-TEST-1",
                    "supplier_id": "S-TEST-RED",
                    "event_category": "经营风险",
                    "event_subtype": "内网安全告警",
                    "event_severity": 3,
                    "event_week": 9,
                    "source_type": "安全公告",
                }
            ],
            "rectifies": [],
            "supplier_profile": {"contract_importance": "重要外包", "system_level": "核心"},
            "business_context": {},
            "enable_live_llm": False,
            "status": "CREATED",
            "errors": [],
            "audit_trace": [],
        }
    )
    assert result["risk_grade"]["risk_level"] == "RED"
    assert result["analysis_route"] == HIGH_RISK_ROUTE
    assert result["risk_identification_result"]["analysis_depth"] == "DEEP_DIVE"


def test_disposition_survives_workflow_state_and_public_contract():
    """处置提示必须经由工作流状态传出（而非只存在于报告内部）。

    回归保护：``WorkflowState`` 未声明 ``disposition`` 时，LangGraph 会丢弃该 key，
    导致对外契约（``build_public_assessment``）与报告读取路径恒为 None。
    """
    result = run_demo(current_week=10, include_events=True)

    # 1. 工作流最终状态顶层必须带处置块
    assert "disposition" in result
    assert result["disposition"] is not None
    assert result["disposition"]["disposition_state"] == result["status"]
    assert result["disposition"]["can_redeclare"] in {True, False}

    # 2. 报告内与状态顶层必须一致
    assert result["risk_report"]["disposition"] == result["disposition"]

    # 3. 对外契约读取的是状态顶层，不能为 None
    public = build_public_assessment(result)
    assert public["disposition"] is not None
    assert public["disposition"]["disposition_state"] == result["status"]


def test_report_disposition_is_recoverable_without_state_passthrough():
    """兜底链路：状态里没有 disposition 时，报告仍能从既有报告/判定中恢复处置块。"""
    from app.schemas.report import build_risk_report

    result = run_demo(current_week=10, include_events=True)
    stripped = {key: value for key, value in result.items() if key != "disposition"}

    rebuilt = build_risk_report(stripped)
    assert rebuilt["disposition"] is not None
    assert rebuilt["disposition"]["disposition_state"] == result["status"]


def test_disabled_llm_marks_every_node_disabled():
    """默认关闭真实模型时，各文案节点应明确标记 DISABLED 而非伪造模型输出。"""
    result = run_demo(current_week=10, include_events=True)
    assert result["risk_identification_result"]["llm_status"] == "DISABLED"
    assert result["association_result"]["llm_status"] == "DISABLED"
    assert result["evidence_result"]["llm_status"] == "DISABLED"
    assert result["consistency_check_result"]["status"] == "DISABLED"
    # 报告中的节点状态映射如实反映
    node_status = result["risk_report"]["llm_node_status"]
    assert node_status["risk_identification"] == "DISABLED"
    assert node_status["association_analysis"] == "DISABLED"
    assert node_status["evidence"] == "DISABLED"
    # 候选建议的枚举与执行状态不受影响
    assert result["candidate_actions"][0]["execution_status"] == "PENDING_HUMAN_REVIEW"


def test_live_llm_failure_degrades_to_fallback_gracefully(monkeypatch: pytest.MonkeyPatch):
    """真实模型不可用时全链路仍可跑通，各节点标记 FALLBACK 且报告结构完整。"""

    class _BrokenLLM:
        def invoke(self, *, system_prompt: str, user_prompt: str, response_model):
            raise RuntimeError("model unavailable")

    monkeypatch.setattr("app.llm.factory.build_llm_client", lambda settings=None: _BrokenLLM())

    result = run_demo(current_week=10, include_events=True, enable_live_llm=True)
    assert result["risk_identification_result"]["llm_status"] == "FALLBACK"
    assert result["association_result"]["llm_status"] == "FALLBACK"
    assert result["evidence_result"]["llm_status"] == "FALLBACK"
    assert result["llm_advisory"]["status"] == "FALLBACK"
    assert result["consistency_check_result"]["status"] == "FALLBACK"
    # 降级后报告仍完整可生成
    assert result["status"] == "PENDING_HUMAN_REVIEW"
    report = result["risk_report"]
    assert report["risk_summary"]
    assert report["recommendation"]
    assert all(item["readable_summary"] for item in report["evidence_summary"])


def test_consistency_check_is_wired_between_decision_and_human_review():
    """一致性校验节点应接在决策之后、人工复核之前。"""
    from app.workflow.graph import build_workflow

    graph = build_workflow().get_graph()
    nodes = set(graph.nodes)
    assert {"decision", "consistency_check", "human_review"} <= nodes

    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert ("decision", "consistency_check") in edges
    assert ("consistency_check", "human_review") in edges


def test_dimension_mapping_node_precedes_risk_identification():
    """维度归类节点应位于风险识别之前，保证统计落在新维度键上。"""
    from app.workflow.graph import build_workflow

    graph = build_workflow().get_graph()
    nodes = set(graph.nodes)
    assert "dimension_mapping" in nodes

    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert ("dimension_mapping", "risk_identification") in edges


def test_live_llm_success_uses_model_text(monkeypatch: pytest.MonkeyPatch):
    """启用真实模型且模型可用时，各节点文案应来自模型（SUCCESS）。"""

    class _RouterLLM:
        """按响应模型的字段名分发预置内容，模拟多节点各自的结构化输出。"""

        def invoke(self, *, system_prompt: str, user_prompt: str, response_model):
            fields = set(response_model.model_fields)
            if fields == {"summary"}:
                return response_model.model_validate({"summary": "模型：风险识别摘要"})
            if "trend_desc" in fields:
                return response_model.model_validate(
                    {
                        "trend_desc": "模型：趋势解读",
                        "cross_dimension_note": "模型：跨维度说明",
                        "trend_support_evidence_ids": [],
                    }
                )
            if "narrations" in fields:
                return response_model.model_validate({"narrations": []})
            if "candidate_actions" in fields:
                return response_model.model_validate(
                    {
                        "risk_summary": "模型：风险总结",
                        "key_factors": ["模型：关键因素"],
                        "recommendation": "模型：处置建议",
                        "rationale": "模型：理由",
                        "evidence_ids": [],
                        "policy_chunk_ids": [],
                        "candidate_actions": [
                            {
                                "suggest_type": "observe",
                                "suggest_content": "模型：候选建议",
                                "suggest_priority": "LOW",
                                "execution_status": "PENDING_HUMAN_REVIEW",
                            }
                        ],
                    }
                )
            return response_model.model_validate(
                {
                    "consistent": True,
                    "conflicts": [],
                    "evidence_sufficient": True,
                    "notes": "模型：一致性复核通过",
                }
            )

    monkeypatch.setattr("app.llm.factory.build_llm_client", lambda settings=None: _RouterLLM())

    result = run_demo(current_week=10, include_events=True, enable_live_llm=True)
    assert result["risk_identification_result"]["llm_status"] == "SUCCESS"
    assert result["risk_identification_result"]["summary"] == "模型：风险识别摘要"
    assert result["risk_trend"]["trend_desc"] == "模型：趋势解读"
    assert result["association_result"]["cross_dimension_note"] == "模型：跨维度说明"
    assert result["consistency_check_result"]["status"] == "SUCCESS"
    assert result["consistency_check_result"]["consistent"] is True
    # 报告如实带出校验结论与各节点状态
    report = result["risk_report"]
    assert report["consistency_check"]["notes"] == "模型：一致性复核通过"
    assert report["llm_node_status"]["consistency_check"] == "SUCCESS"

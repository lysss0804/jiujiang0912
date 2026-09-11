"""Multi-Agent 协同工作流（LangGraph）。

链路：Coordinator（装载+分级+路由）
        ↓ 条件路由
   ┌────┴────┐
红色高风险专项   常规分析
   └────┬────┘
        ↓
  维度归类 Agent（旧事件类别 -> 新六维度）
        ↓
  风险识别 Agent
        ↓
  关联分析 Agent（含趋势研判）
        ↓
  Evidence 证据 Agent
        ↓ 条件：证据是否通过
   ┌────┴────┐
政策检索(RAG)  人工复核（证据不足）
        ↓
  决策建议 Agent
        ↓
  一致性校验 Agent（模型级交叉验证）
        ↓
   人工复核（生成报告 + 处置门禁）
"""

from langgraph.graph import END, START, StateGraph

from app.agents.association_analysis import association_analysis_node
from app.agents.consistency_check import consistency_check_node
from app.agents.coordinator import coordinator_node, route_after_grading
from app.agents.decision import decision_node
from app.agents.dimension_mapping import dimension_mapping_node
from app.agents.evidence import evidence_node, route_after_evidence
from app.agents.human_review import human_review_node
from app.agents.policy_retrieval import policy_retrieval_node
from app.agents.risk_identification import risk_identification_node
from app.workflow.state import WorkflowState


def build_workflow():
    graph = StateGraph(WorkflowState)
    graph.add_node("coordinator", coordinator_node)
    graph.add_node("high_risk", _high_risk_entry)
    graph.add_node("standard", _standard_entry)
    graph.add_node("dimension_mapping", dimension_mapping_node)
    graph.add_node("risk_identification", risk_identification_node)
    graph.add_node("association_analysis", association_analysis_node)
    graph.add_node("evidence", evidence_node)
    graph.add_node("policy_retrieval", policy_retrieval_node)
    graph.add_node("decision", decision_node)
    graph.add_node("consistency_check", consistency_check_node)
    graph.add_node("human_review", human_review_node)

    graph.add_edge(START, "coordinator")
    graph.add_conditional_edges(
        "coordinator",
        route_after_grading,
        {"high_risk": "high_risk", "standard": "standard"},
    )
    graph.add_edge("high_risk", "dimension_mapping")
    graph.add_edge("standard", "dimension_mapping")
    graph.add_edge("dimension_mapping", "risk_identification")
    graph.add_edge("risk_identification", "association_analysis")
    graph.add_edge("association_analysis", "evidence")
    graph.add_conditional_edges(
        "evidence",
        route_after_evidence,
        {"policy_retrieval": "policy_retrieval", "human_review": "human_review"},
    )
    graph.add_edge("policy_retrieval", "decision")
    graph.add_edge("decision", "consistency_check")
    graph.add_edge("consistency_check", "human_review")
    graph.add_edge("human_review", END)
    return graph.compile()


def _high_risk_entry(state: dict) -> dict:
    """红色风险专项链路入口：标记深度分析，便于后续 Agent 加深核查要求。"""
    from app.agents.helpers import trace

    return {
        "analysis_route": "HIGH_RISK_DEEP_DIVE",
        "audit_trace": trace("Coordinator", "ROUTE", detail="high-risk deep dive branch selected"),
    }


def _standard_entry(state: dict) -> dict:
    """常规分析链路入口。"""
    from app.agents.helpers import trace

    return {
        "analysis_route": "STANDARD",
        "audit_trace": trace("Coordinator", "ROUTE", detail="standard analysis branch selected"),
    }

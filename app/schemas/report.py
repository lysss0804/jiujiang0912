"""结构化风险报告。

这是交给后端的核心交付物：一次成型、字段稳定、可直接用于 PDF 渲染。
所有字段均来自确定性计算或已校验的 Agent 输出。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field

from app.schemas.contracts import (
    CandidateAction,
    CrossSupplierComparison,
    DimensionDataStatus,
    DispositionHint,
    EvidenceItem,
    EvidenceResult,
    RiskGrade,
    StrictModel,
    TrendResult,
)
from app.schemas.output_contract import OUTPUT_SCHEMA_VERSION

REPORT_SCHEMA_VERSION = "C-DRAFT-V0.3"


class DimensionStat(StrictModel):
    dimension: str
    event_count: int = Field(ge=0)
    score: float = Field(ge=0.0)
    # 数据状态：DATA=本期有事件；NO_DATA=本期无数据来源（不编造，计 0 分）
    data_status: DimensionDataStatus = "DATA"


class KeyFactor(StrictModel):
    dimension: str
    event_count: int = Field(ge=0)
    subtypes: list[str] = Field(default_factory=list)


class PolicyBasis(StrictModel):
    chunk_id: str
    document_name: str
    version: str
    excerpt: str
    # 向量来源：embedding=真实语义检索；hash=无可用 Key 时的本地降级检索。
    # 保留该字段，使政策依据的数据来源可追溯、可审计。
    vector_source: str = ""


class RiskReport(StrictModel):
    """面向后端与 PDF 的完整风险报告对象。"""

    report_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: Literal["C-DRAFT-V0.3"] = REPORT_SCHEMA_VERSION
    output_schema_version: Literal["C-DRAFT-V0.2"] = OUTPUT_SCHEMA_VERSION
    run_id: str
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    supplier_id: str
    supplier_name: str = ""
    current_week: int = Field(ge=1, le=52)

    # ① 供应商基本信息
    supplier_profile: dict[str, Any] = Field(default_factory=dict)

    # ② 当前风险等级（仅展示，不参与自动处置）
    risk_grade: RiskGrade

    # ③ 六维度风险分布
    dimension_breakdown: list[DimensionStat] = Field(default_factory=list)

    # ④ 风险趋势
    risk_trend: TrendResult

    # ⑤ AI 风险总结与关键因素
    risk_summary: str = ""
    key_factors: list[KeyFactor] = Field(default_factory=list)
    association_summary: str = ""

    # ⑥ 风险证据（可追溯）
    evidence_summary: list[EvidenceItem] = Field(default_factory=list)
    evidence_by_dimension: dict[str, list[str]] = Field(default_factory=dict)

    # ⑦ 政策依据
    policy_basis: list[PolicyBasis] = Field(default_factory=list)

    # ⑧ AI 分析与处置建议
    rationale: str = ""
    recommendation: str = ""
    candidate_actions: list[CandidateAction] = Field(default_factory=list)
    llm_status: str = "DISABLED"
    # 各节点的模型调用状态（SUCCESS / FALLBACK / DISABLED），用于审计追溯
    llm_node_status: dict[str, str] = Field(default_factory=dict)

    # ⑧.1 多智能体一致性校验（模型级交叉验证；未启用/失败时为 None）
    consistency_check: dict[str, Any] | None = None

    # ⑨ 人工处置
    human_review_status: Literal[
        "PENDING_HUMAN_REVIEW", "EVIDENCE_INSUFFICIENT", "AWAITING_SUPPLIER_REDECLARE"
    ] = "PENDING_HUMAN_REVIEW"
    # 处置提示（分发对象 / 是否可重新申报 / 原因）；智能体只产出提示，不做自动处置
    disposition: DispositionHint | None = None

    # ⑨.1 多供应商横向对比数据块（批量场景产出；单供应商分析时为 None）
    # 后端据此渲染「横向对比 PDF」，并按角色决定是否下发（普通角色置为 null）
    cross_supplier_comparison: CrossSupplierComparison | None = None

    # ⑩ 运行信息
    analysis_route: str = "STANDARD"
    errors: list[str] = Field(default_factory=list)
    audit_trace: list[dict[str, Any]] = Field(default_factory=list)


def _collect_llm_node_status(state: dict) -> dict[str, str]:
    """汇总各节点的模型调用状态，供报告与审计追溯使用。

    状态含义：SUCCESS=真实模型产出；FALLBACK=模型不可用已降级为模板；
    DISABLED=未启用真实模型；SKIPPED=该节点本次未参与。
    """
    statuses: dict[str, str] = {}

    advisory = state.get("llm_advisory", {}) or {}
    if advisory.get("status"):
        statuses["decision"] = str(advisory["status"])

    identification = state.get("risk_identification_result", {}) or {}
    if identification.get("llm_status"):
        statuses["risk_identification"] = str(identification["llm_status"])

    association = state.get("association_result", {}) or {}
    if association.get("llm_status"):
        statuses["association_analysis"] = str(association["llm_status"])

    evidence = state.get("evidence_result", {}) or {}
    if evidence.get("llm_status"):
        statuses["evidence"] = str(evidence["llm_status"])

    consistency = state.get("consistency_check_result", {}) or {}
    if consistency.get("status"):
        statuses["consistency_check"] = str(consistency["status"])

    return statuses


def _resolve_disposition(state: dict) -> dict[str, Any] | None:
    """解析处置提示块：顶层 -> 已有报告 -> 按状态确定性派生。

    这是「不依赖状态传递」的兜底链路：即使调用方传入的状态里没有
    ``disposition`` 键（例如只保留了部分字段），报告也不会因此丢掉处置结论。
    循环依赖通过函数内导入规避（human_review 模块导入本模块）。
    """
    disposition = state.get("disposition")
    if disposition:
        return disposition if isinstance(disposition, dict) else None

    existing = state.get("risk_report")
    if isinstance(existing, dict) and existing.get("disposition"):
        return existing["disposition"]

    if not state.get("risk_grade"):
        # 无分级结果时无处可派生，保持 None（与历史行为一致）
        return None

    from app.agents.human_review import judge_disposition

    return judge_disposition(state)


def build_risk_report(state: dict) -> dict:

    """从工作流状态构建结构化报告对象。

    处置块解析顺序（保证任何调用路径都能拿到处置结论）：
    1. 状态顶层 ``disposition``（工作流正常传递时走这条）；
    2. 兜底：状态里已有报告（``risk_report``）时取其内 ``disposition``；
    3. 都为 ``None`` 时按状态派生一次确定性判定（懒加载，避免循环依赖）。
    """
    grade = state.get("risk_grade", {})
    identification = state.get("risk_identification_result", {})
    advisory = state.get("llm_advisory", {})
    evidence_result = state.get("evidence_result", {})
    profile = state.get("supplier_profile", {}) or {}
    disposition = _resolve_disposition(state)

    dimension_counts = state.get("dimension_stats", {}).get("counts", {}) or {}
    breakdown_scores = grade.get("dimension_breakdown", {}) or {}
    dimension_breakdown = [
        DimensionStat(
            dimension=name,
            event_count=count,
            score=float(breakdown_scores.get(name, 0.0)),
            data_status="DATA" if count > 0 else "NO_DATA",
        )
        for name, count in dimension_counts.items()
    ]

    key_factors = [
        KeyFactor(
            dimension=item.get("dimension", ""),
            event_count=int(item.get("event_count", 0)),
            subtypes=list(item.get("subtypes", [])),
        )
        for item in identification.get("main_risks", [])
    ]

    policy_basis = [
        PolicyBasis(
            chunk_id=item.get("chunk_id", ""),
            document_name=item.get("document_name", ""),
            version=item.get("version", ""),
            excerpt=item.get("text", ""),
            vector_source=item.get("vector_source", ""),
        )
        for item in state.get("policy_context", [])
    ]

    payload = {
        "run_id": state.get("run_id", ""),
        "supplier_id": state.get("supplier_id", ""),
        "supplier_name": str(profile.get("supplier_name", "")),
        "current_week": int(state.get("current_week", 1)),
        "supplier_profile": profile,
        "risk_grade": grade,
        "dimension_breakdown": [item.model_dump() for item in dimension_breakdown],
        "risk_trend": state.get("risk_trend", {}),
        "risk_summary": advisory.get("risk_summary") or identification.get("summary", ""),
        "key_factors": [item.model_dump() for item in key_factors],
        "association_summary": state.get("association_result", {}).get("cross_dimension_note", ""),
        "evidence_summary": evidence_result.get("evidence_items", []),
        "evidence_by_dimension": evidence_result.get("by_dimension", {}),
        "policy_basis": [item.model_dump() for item in policy_basis],
        "rationale": advisory.get("rationale", ""),
        "recommendation": advisory.get("recommendation", ""),
        "candidate_actions": state.get("candidate_actions", []),
        "llm_status": advisory.get("status", "DISABLED"),
        "llm_node_status": _collect_llm_node_status(state),
        "consistency_check": state.get("consistency_check_result"),
        "human_review_status": state.get("status", "PENDING_HUMAN_REVIEW"),
        "disposition": disposition,
        "cross_supplier_comparison": state.get("cross_supplier_comparison"),
        "analysis_route": state.get("analysis_route", "STANDARD"),
        "errors": state.get("errors", []),
        "audit_trace": state.get("audit_trace", []),
    }
    return RiskReport.model_validate(payload).model_dump(mode="json")


__all__ = [
    "REPORT_SCHEMA_VERSION",
    "DimensionStat",
    "KeyFactor",
    "PolicyBasis",
    "RiskReport",
    "build_risk_report",
]

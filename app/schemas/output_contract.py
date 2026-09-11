from typing import Any, Literal

from pydantic import Field

from app.schemas.contracts import (
    CandidateAction,
    CrossSupplierComparison,
    DispositionHint,
    EvidenceResult,
    RiskGrade,
    StrictModel,
    TrendResult,
)



OUTPUT_SCHEMA_VERSION = "C-DRAFT-V0.2"


class PublicLLMAdvisory(StrictModel):
    status: Literal["SUCCESS", "FALLBACK", "DISABLED"]
    provider: str
    model: str | None = None
    reason: str | None = None
    risk_summary: str | None = None
    key_factors: list[str] = Field(default_factory=list)
    recommendation: str | None = None
    rationale: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    policy_chunk_ids: list[str] = Field(default_factory=list)


class AuditEntry(StrictModel):
    agent_name: str
    status: str
    detail: str
    timestamp: str


class AssessmentOutput(StrictModel):
    """多智能体分析的对外输出契约（不含预测字段）。"""

    schema_version: Literal["C-DRAFT-V0.2"] = OUTPUT_SCHEMA_VERSION
    run_id: str
    supplier_id: str
    current_week: int = Field(ge=1, le=52)

    # 当前风险分级（规则引擎；仅展示，不参与自动处置）
    risk_grade: RiskGrade

    # 风险识别与关联分析
    risk_identification: dict[str, Any] = Field(default_factory=dict)
    association_result: dict[str, Any] = Field(default_factory=dict)
    risk_trend: TrendResult

    # 证据与政策
    evidence_result: EvidenceResult
    policy_context: list[dict[str, Any]] = Field(default_factory=list)

    # AI 分析与建议
    llm_advisory: PublicLLMAdvisory
    candidate_actions: list[CandidateAction] = Field(default_factory=list)

    # 结构化报告
    risk_report: dict[str, Any] = Field(default_factory=dict)

    # 处置提示（分发对象 / 是否可重新申报）；智能体只产出提示，不做自动处置
    disposition: DispositionHint | None = None

    # 多供应商横向对比数据块（批量场景产出；单供应商分析时为 None）
    cross_supplier_comparison: CrossSupplierComparison | None = None

    # 运行信息
    analysis_route: str = "STANDARD"
    status: str
    errors: list[str] = Field(default_factory=list)
    audit_trace: list[AuditEntry] = Field(default_factory=list)


def build_public_assessment(state: dict) -> dict:
    payload = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "run_id": state["run_id"],
        "supplier_id": state["supplier_id"],
        "current_week": state["current_week"],
        "risk_grade": state["risk_grade"],
        "risk_identification": state.get("risk_identification_result", {}),
        "association_result": state.get("association_result", {}),
        "risk_trend": state["risk_trend"],
        "evidence_result": state["evidence_result"],
        "policy_context": state.get("policy_context", []),
        "llm_advisory": state.get("llm_advisory", {"status": "DISABLED", "provider": "none"}),
        "candidate_actions": state.get("candidate_actions", []),
        "risk_report": state.get("risk_report", {}),
        "disposition": state.get("disposition"),
        "cross_supplier_comparison": state.get("cross_supplier_comparison"),
        "analysis_route": state.get("analysis_route", "STANDARD"),
        "status": state["status"],
        "errors": state.get("errors", []),
        "audit_trace": state.get("audit_trace", []),
    }
    return AssessmentOutput.model_validate(payload).model_dump(mode="json")

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


RiskLevel = Literal["RED", "YELLOW", "GREEN"]

# 银行六大风险维度（新版口径）
# 无数据来源的维度（司法 / 失信 / 知识产权）在本期数据下由维度归类节点标记 NO_DATA，分值计 0。
RISK_DIMENSIONS = ("公司背景", "司法", "失信", "经营风险", "经营状况", "知识产权")

# 风险维度数据状态：DATA=本期有事件；NO_DATA=本期无数据来源（不编造，计 0 分）
DimensionDataStatus = Literal["DATA", "NO_DATA"]


class HitRule(StrictModel):
    """规则引擎命中的单条规则，用于向人工解释分级来源。"""

    rule_id: str = Field(min_length=1, max_length=50)
    category: str = Field(min_length=1, max_length=50)
    description: str = Field(min_length=1, max_length=300)
    severity: int = Field(ge=0, le=5)
    weight: float = Field(ge=0.0)


class RiskGrade(StrictModel):
    """当前风险分级结果。仅用于报告展示，不参与任何自动处置。

    注意区分两个"等级"：
    - `risk_level` 风险等级（三级）：RED / YELLOW / GREEN，由分值 + 档位标准判定；
    - `importance_tier` 供应商重要性（两级）：重要 / 一般，由银行名单定义，只作分值系数。
    两者独立：重要性不参与风险等级判定。

    分值保留（`score`），并额外给出等级名称（`grade_label`）与
    该等级对应的明确分值标准（`grade_range`），便于报告直接展示"X 分属于 Y 级"。
    """

    supplier_id: str = Field(min_length=1, max_length=100)
    risk_level: RiskLevel
    score: float = Field(ge=0.0)
    # 风险等级（三级）明细：档位名 / 中文名 / 该档分值标准 / 距上一档差额
    grade_tier: RiskLevel = "GREEN"
    grade_label: str = Field(default="低风险", min_length=1, max_length=20)
    grade_range: str = Field(default="[0, 2.5)", min_length=1, max_length=60)
    next_threshold_gap: float = Field(default=0.0)
    grade_basis: str = Field(min_length=1, max_length=500)
    hit_rules: list[HitRule] = Field(default_factory=list, max_length=50)
    dimension_breakdown: dict[str, float] = Field(default_factory=dict)
    recent_event_count: int = Field(ge=0)
    window_weeks: int = Field(ge=1, le=52)
    # 统计窗口（归一化后可读描述），如「近12周」/「近3个月（第19-30周）」
    window_display: str = Field(default="近12周", min_length=1, max_length=60)
    window_unit: Literal["week", "month"] = "week"
    window_size: int = Field(default=12, ge=1, le=52)
    # 供应商重要性（两级，由银行名单定义）：重要 1.2 / 一般 1.0
    importance_tier: Literal["重要", "一般"] = "一般"
    importance_coefficient: float = Field(default=1.0, gt=0.0)
    # 重要性来源：BANK_LIST=银行名单（权威，只读不算）；DERIVED=名单缺失时系统推算
    importance_source: Literal["BANK_LIST", "DERIVED"] = "DERIVED"
    # 报告周期：按重要性分级（重要→weekly 周报；一般→monthly 月报），与风险等级无关
    report_period: Literal["weekly", "monthly"] = "monthly"
    report_period_label: str = Field(default="月报", min_length=1, max_length=20)
    in_rectify: bool = False


class TrendResult(StrictModel):
    trend_type: Literal["RISING", "STEADY", "FALLING", "SUDDEN_JUMP"]
    trend_desc: str = Field(min_length=1, max_length=500)
    trend_support_evidence_ids: list[str] = Field(default_factory=list, max_length=50)


class EvidenceItem(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=100)
    supplier_id: str = Field(min_length=1, max_length=100)
    event_category: str = Field(min_length=1, max_length=50)
    event_subtype: str = Field(min_length=1, max_length=100)
    event_severity: int = Field(ge=0, le=5)
    event_week: int = Field(ge=1, le=52)
    source_type: str = Field(min_length=1, max_length=100)
    readable_summary: str = Field(default="", max_length=300)
    # 映射后的新维度（六维度之一）；为空时前端/报告回退展示 event_category
    mapped_dimension: str = Field(default="", max_length=50)


class EvidenceResult(StrictModel):
    status: Literal["PASS", "FAIL"]
    evidence_items: list[EvidenceItem] = Field(default_factory=list, max_length=100)
    by_dimension: dict[str, list[str]] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list, max_length=20)
    # 证据转述节点的模型状态（SUCCESS / FALLBACK / DISABLED），向后兼容字段
    llm_status: str = "DISABLED"


class CandidateAction(StrictModel):
    suggest_type: Literal["rectify", "observe", "alert"]
    suggest_content: str = Field(min_length=1, max_length=500)
    suggest_priority: Literal["HIGH", "MEDIUM", "LOW"]
    execution_status: Literal["PENDING_HUMAN_REVIEW"] = "PENDING_HUMAN_REVIEW"


class AdvisoryResult(StrictModel):
    """决策建议 Agent 的结构化输出（LLM 或确定性回退共用）。"""

    risk_summary: str = Field(min_length=1, max_length=500)
    key_factors: list[str] = Field(min_length=1, max_length=10)
    recommendation: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=50)
    policy_chunk_ids: list[str] = Field(default_factory=list, max_length=10)
    candidate_actions: list[CandidateAction] = Field(min_length=1, max_length=5)


# ---- 全链路 LLM 节点结构化输出契约 ----
# 说明：以下模型仅承载"自然语言文案"，数值/枚举/分级仍由规则引擎确定性产出。
# 各节点调用失败（无 Key / 超时 / 输出不合法）时回退模板并标记 FALLBACK。


class RiskIdentificationLLMResult(StrictModel):
    """风险识别 Agent 的文案输出（仅 summary 由模型生成）。"""

    summary: str = Field(min_length=1, max_length=500)


class AssociationLLMResult(StrictModel):
    """关联分析 Agent 的文案输出（趋势类型与阈值判定仍为确定性）。"""

    trend_desc: str = Field(min_length=1, max_length=500)
    cross_dimension_note: str = Field(min_length=1, max_length=500)
    trend_support_evidence_ids: list[str] = Field(default_factory=list, max_length=50)


class EvidenceNarrationItem(StrictModel):
    """单条证据的可读转述（必须一一对应输入中的 evidence_id）。"""

    evidence_id: str = Field(min_length=1, max_length=100)
    readable_summary: str = Field(min_length=1, max_length=300)


class EvidenceNarrationResult(StrictModel):
    """证据转述的批量输出：一次调用转述多条事件，避免逐条请求。"""

    narrations: list[EvidenceNarrationItem] = Field(default_factory=list, max_length=100)


class ConsistencyCheckResult(StrictModel):
    """多智能体一致性校验（模型级交叉验证）结论。"""

    consistent: bool
    conflicts: list[str] = Field(default_factory=list, max_length=20)
    evidence_sufficient: bool = True
    notes: str = Field(default="", max_length=1000)


# ---- 维度归类（旧事件类别 -> 新六大风险维度）----


class DimensionMappingItem(StrictModel):
    """单条事件的维度归类结果。"""

    evidence_id: str = Field(min_length=1, max_length=100)
    new_dimension: str = Field(min_length=1, max_length=50)


class DimensionMappingResult(StrictModel):
    """维度归类批量输出：一次调用返回全部归类，禁止逐条请求。"""

    mappings: list[DimensionMappingItem] = Field(default_factory=list, max_length=200)


# ---- 处置与分发（智能体侧产出，后端据此渲染/裁剪/推送）----

# 分发受众（两层角色）：LEADERSHIP=高级管理人员（领导层）；MANAGER=普通管理人员
DeliveryAudience = Literal["LEADERSHIP", "MANAGER"]


class DispositionHint(StrictModel):
    """处置提示块。

    智能体只产出「提示」，不做任何自动处置，也不直接发送：
    - `delivery_audience`：建议分发对象（后端据此决定下发哪些报告）；
    - `disposition_state`：处置状态（正常复核 / 证据不足 / 待供应商重新申报）；
    - `can_redeclare`：是否允许供应商重新申报（门禁为确定性判定）。
    """

    delivery_audience: list[DeliveryAudience] = Field(default_factory=lambda: ["MANAGER"])
    disposition_state: Literal[
        "PENDING_HUMAN_REVIEW", "EVIDENCE_INSUFFICIENT", "AWAITING_SUPPLIER_REDECLARE"
    ] = "PENDING_HUMAN_REVIEW"
    can_redeclare: bool = False
    reason: str = Field(default="", max_length=500)


# ---- 多供应商横向对比（纯计算，供后端渲染「横向对比 PDF」）----


class ComparisonRankItem(StrictModel):
    """单个供应商在横向对比中的分值与排名。"""

    rank: int = Field(ge=1)
    supplier_id: str = Field(min_length=1, max_length=100)
    supplier_name: str = Field(default="", max_length=200)
    importance_tier: Literal["重要", "一般"] = "一般"
    risk_level: RiskLevel = "GREEN"
    score: float = Field(ge=0.0)
    deviation: float = 0.0  # 相对同批中位数的偏差


class ComparisonDimensionBaseline(StrictModel):
    """单个维度在同批供应商中的基线（中位数）。"""

    dimension: str = Field(min_length=1, max_length=50)
    median_score: float = Field(ge=0.0)
    median_event_count: float = Field(ge=0.0)


class CrossSupplierComparison(StrictModel):
    """多供应商同期横向对比数据块（供后端生成横向对比报告）。"""

    supplier_count: int = Field(ge=0)
    level_distribution: dict[str, int] = Field(default_factory=dict)
    dimension_baselines: list[ComparisonDimensionBaseline] = Field(default_factory=list)
    rankings: list[ComparisonRankItem] = Field(default_factory=list)
    median_score: float = Field(default=0.0, ge=0.0)
    note: str = Field(default="", max_length=500)

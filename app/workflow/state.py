import operator
from typing import Annotated, Any, TypedDict




class WorkflowState(TypedDict, total=False):
    # ---- 输入 ----
    run_id: str
    output_schema_version: str
    supplier_id: str
    current_week: int
    events: list[dict[str, Any]]
    rectifies: list[Any]
    business_context: dict[str, Any]
    enable_live_llm: bool

    # ---- 统计窗口（可按周 / 按月，内部统一归一化为周区间）----
    window_unit: str
    window_size: int
    window_display: str

    # ---- 数据装载与预索引 ----
    supplier_profile: dict[str, Any]
    visible_events: list[dict[str, Any]]
    dimension_stats: dict[str, Any]

    # ---- 规则分级引擎 ----
    risk_grade: dict[str, Any]

    # ---- 维度归类（旧类别 -> 新六维度）----
    dimension_mapping_result: dict[str, Any]

    # ---- 条件路由 ----
    analysis_route: str


    # ---- 四个 Agent 输出 ----
    # 注意：字段名不能与 LangGraph 节点名重名，否则会与 state channel 冲突
    risk_identification_result: dict[str, Any]
    association_result: dict[str, Any]
    risk_trend: dict[str, Any]
    evidence_result: dict[str, Any]
    policy_context: list[dict[str, Any]]
    llm_advisory: dict[str, Any]
    candidate_actions: list[dict[str, Any]]

    # ---- 多智能体一致性校验（模型级交叉验证）----
    # 注意：字段名不能与 LangGraph 节点名（consistency_check）重名，否则会与 state channel 冲突
    consistency_check_result: dict[str, Any]

    # ---- 结构化报告 ----
    risk_report: dict[str, Any]

    # ---- 处置提示（人工复核节点产出）----
    # 必须在此声明：LangGraph 只保留 TypedDict 声明过的 key，
    # 否则 disposition 不会进入工作流最终状态（报告与对外契约都会读不到）。
    disposition: dict[str, Any]
    cross_supplier_comparison: dict[str, Any]

    # ---- 运行状态 ----
    status: str
    errors: Annotated[list[str], operator.add]
    audit_trace: Annotated[list[dict[str, Any]], operator.add]

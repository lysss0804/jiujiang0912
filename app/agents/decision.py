"""决策建议 Agent。

职责：综合「当前风险分级 + 关联分析 + 趋势研判 + 已核验证据 + 政策依据」，
通过大模型形成自然语言的风险总结、关键因素、处置建议与理由。

严格边界：
- 只给候选建议，不代替人工做最终处置（不得建议自动处罚/停服/解约）；
- 不得编造证据编号或政策条款，只能引用输入中提供的 ID；
- 不做任何数值计算（分级与统计均已由规则引擎完成）；
- 大模型不可用时降级为规则引擎生成的确定性文案，保证报告永远可生成。
"""

from __future__ import annotations

import json
import logging

from app.agents.helpers import (
    align_ids,
    allowed_evidence_ids,
    allowed_policy_chunk_ids,
    canonical_id,
    require_grounded,
    trace,
)
from app.config import get_settings
from app.llm import factory as llm_factory
from app.rules.config import get_rules
from app.schemas.contracts import AdvisoryResult, CandidateAction


logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是银行外包供应商风险辅助研判助手。
只输出 JSON 对象，并严格包含 risk_summary、key_factors、recommendation、rationale、
evidence_ids、policy_chunk_ids、candidate_actions。

硬性要求：
1. 风险等级、规则得分、维度分布、事件数量均已由规则引擎计算完成，你只能引用，不得修改或重新计算。
2. 只能引用输入中列出的 evidence_id 和 policy chunk_id，不得编造事实、制度、条款或编号。
3. key_factors 必须来自输入中给出的风险维度、命中规则或事件，不得凭空推测。
4. 所有建议仅为候选建议，execution_status 必须为 PENDING_HUMAN_REVIEW。
5. 不得建议自动处罚、自动停服、自动解约，也不得绕过人工复核。
6. suggest_type 只能取 rectify、observe、alert 之一；suggest_priority 只能取 HIGH、MEDIUM、LOW 之一。
7. 材料不足时必须明确指出，不得猜测；不得做未来风险概率预测。
8. key_factors 每条须是现象描述（如「命中规则 R-XXX：<规则描述>」或「XX维度事件增多」），
   不得输出空字符串或与风险无关的内容；建议优先级须与风险等级匹配
   （RED→HIGH、YELLOW→MEDIUM、GREEN→LOW）。"""


def _canonical_id(value: str) -> str:
    return canonical_id(value)


def _priority_for(level: str, score: float) -> str:
    """由 rules.yaml 的 `grade_tiers` 派生提示优先级，避免与分级规则脱节。

    阈值不再硬编码：RED 档下限 → HIGH，YELLOW 档下限 → MEDIUM，其余 LOW。
    """
    tiers = get_rules().get("grade_tiers") or []
    red_min = next(
        (float(item.get("min_score", 0.0)) for item in tiers if str(item.get("tier")) == "RED"), 6.0
    )
    yellow_min = next(
        (float(item.get("min_score", 0.0)) for item in tiers if str(item.get("tier")) == "YELLOW"), 2.5
    )
    if level == "RED" or score >= red_min:
        return "HIGH"
    if level == "YELLOW" or score >= yellow_min:
        return "MEDIUM"
    return "LOW"


def _normalize_priority(level: str, advisory: AdvisoryResult) -> None:
    """把模型给出的建议优先级与风险等级对齐（等级是权威，优先级只是派生）。

    模型可能把 RED 供应商的建议标成 LOW，或把 GREEN 标成 HIGH，
    这与规则引擎等级自相矛盾，会误导人工排序。这里以等级为准矫正。
    """
    expected = _priority_for(level, 0.0)
    rank = {name: index for index, name in enumerate(SUGGEST_PRIORITIES)}  # HIGH < MEDIUM < LOW
    for action in advisory.candidate_actions:
        requested = action.suggest_priority
        # 只允许在「不弱于等级期望」的方向上保守上调，禁止把高风险降级为低优先级
        if rank.get(requested, 99) > rank.get(expected, 0):
            action.suggest_priority = expected  # type: ignore[assignment]


def _normalize_key_factors(advisory: AdvisoryResult, identification: dict | None = None) -> None:
    """清洗模型输出的 key_factors。

    真实数据下观察到两类问题：
    1. 模型原样抄回提示词里的**占位模板**（如「命中规则 R-XXX：<规则描述>」），
       对人工没有任何信息量；
    2. 输出空串 / 纯空白。
    这里剔除占位与空值；若清洗后为空，则用规则引擎的确定性事实回退，
    保证报告不会出现「关键因素为空」或「关键因素是模板」的情况。
    """
    identification = identification or {}
    placeholder_markers = ("r-xxx", "<规则描述>", "xx维度", "规则描述>")
    cleaned: list[str] = []
    for item in advisory.key_factors:
        text = (item or "").strip()
        if not text:
            continue
        if any(marker in text.lower() for marker in placeholder_markers):
            continue
        cleaned.append(text)

    if not cleaned:
        for hit in identification.get("hit_rules", []):
            cleaned.append(f"命中规则 {hit.get('rule_id')}：{hit.get('description')}")
        for risk in identification.get("main_risks", []):
            cleaned.append(
                f"{risk.get('dimension')}维度：{risk.get('event_count')}条事件"
                f"（{'、'.join(risk.get('subtypes', []))}）"
            )
    if not cleaned:
        raise ValueError("LLM returned no usable key_factors")
    advisory.key_factors = cleaned[:10]




def _fallback_advisory(state: dict, *, reason: str) -> AdvisoryResult:
    """规则引擎驱动的确定性兜底文案；不依赖任何 LLM。"""
    grade = state.get("risk_grade", {})
    # 修正既有键名不一致：工作流实际写入的是 risk_identification_result
    identification = state.get("risk_identification_result") or state.get("risk_identification", {})
    association = state.get("association_result", {})
    trend = state.get("risk_trend", {})
    evidence = state.get("evidence_result", {}).get("evidence_items", [])

    level = str(grade.get("risk_level", "GREEN"))
    score = float(grade.get("score", 0.0))
    main_risks = identification.get("main_risks", [])
    window_display = str(grade.get("window_display") or f"近{int(grade.get('window_weeks', 12))}周")

    risk_summary = identification.get("summary") or (
        f"当前风险等级：{level}（{grade.get('grade_label', '')}，分值标准{grade.get('grade_range', '')}）；"
        f"规则得分{score:.2f}；{window_display}共{grade.get('recent_event_count', 0)}条风险事件。"
    )

    # key_factors 统一用「命中规则 <id>：<描述>」枚举口径，与规则引擎的 hit_rules 一致
    key_factors = [f"命中规则 {hit.get('rule_id')}：{hit.get('description')}" for hit in identification.get("hit_rules", [])]
    if main_risks:
        key_factors.append(
            f"{window_display}主要风险维度："
            + "、".join(item["dimension"] for item in main_risks)
        )
    if not key_factors:
        key_factors = [f"{window_display}未识别到显著风险事件，风险水平处于{level}"]

    if level == "RED":
        suggestion = (
            "建议将该供应商列入重点监测名单，立即开展专项核查，要求其提交书面说明与整改计划，"
            "并启动重新评估流程。"
        )
        suggest_type = "alert"
    elif level == "YELLOW":
        suggestion = (
            "建议将该供应商纳入重点观察范围，提高监测频率，要求其提交整改材料并跟踪整改结果。"
        )
        suggest_type = "rectify"
    else:
        suggestion = "建议维持常规监测频率，如后续出现新的风险事件再行研判。"
        suggest_type = "observe"

    if association.get("cross_dimension"):
        suggestion += f"近期风险跨维度关联（{'、'.join(association.get('active_dimensions', []))}），建议一并核查共性成因。"

    rationale = (
        f"{grade.get('grade_basis', '')}。{trend.get('trend_desc', '')} "
        f"{association.get('cross_dimension_note', '')} "
        f"已核验证据{len(evidence)}条。备注：{reason}，本结论由规则引擎确定性生成。"
    ).strip()

    return AdvisoryResult(
        risk_summary=risk_summary,
        key_factors=key_factors[:10],
        recommendation=suggestion,
        rationale=rationale,
        evidence_ids=[str(item.get("evidence_id")) for item in evidence],
        policy_chunk_ids=[],
        candidate_actions=[
            CandidateAction(
                suggest_type=suggest_type,  # type: ignore[arg-type]
                suggest_content=suggestion,
                suggest_priority=_priority_for(level, score),  # type: ignore[arg-type]
            )
        ],
    )


def _align_identifiers(advisory: AdvisoryResult, state: dict) -> None:
    """把 LLM 输出的视觉等价连字符恢复为源数据中的精确 ID。"""
    advisory.evidence_ids = align_ids(advisory.evidence_ids, allowed_evidence_ids(state))
    advisory.policy_chunk_ids = align_ids(advisory.policy_chunk_ids, allowed_policy_chunk_ids(state))


def _validate_grounding(advisory: AdvisoryResult, state: dict) -> None:
    require_grounded(advisory.evidence_ids, allowed_evidence_ids(state), kind="evidence")
    require_grounded(advisory.policy_chunk_ids, allowed_policy_chunk_ids(state), kind="policy")
    _validate_candidate_actions(advisory)


# 系统提示词规定的候选建议枚举（提示词与校验必须同源，避免「嘴上说的框不住实际输出」）
SUGGEST_TYPES = ("rectify", "observe", "alert")
SUGGEST_PRIORITIES = ("HIGH", "MEDIUM", "LOW")
# 禁止模型越界给出自动处置动作（人工边界）
FORBIDDEN_ACTION_WORDS = ("自动处罚", "自动停服", "自动解约", "直接处罚", "立即停服", "自动终止合同")


def _validate_candidate_actions(advisory: AdvisoryResult) -> None:
    """把系统提示词里的硬性约束真正落到校验上。

    此前 `suggest_type` / `suggest_priority` 仅靠提示词口头约束，
    LLM 一旦输出枚举外的值（或建议自动处置），会绕过校验直接进入报告。
    这里与 grounding 一致：越界即抛错，由上层降级为确定性文案。
    """
    for action in advisory.candidate_actions:
        if action.suggest_type not in SUGGEST_TYPES:
            raise ValueError(f"candidate suggest_type out of enum: {action.suggest_type}")
        if action.suggest_priority not in SUGGEST_PRIORITIES:
            raise ValueError(f"candidate suggest_priority out of enum: {action.suggest_priority}")
        text = f"{action.suggest_content}{advisory.recommendation}"
        hit = [word for word in FORBIDDEN_ACTION_WORDS if word in text]
        if hit:
            raise ValueError(f"candidate action crosses human-in-the-loop boundary: {hit}")



def decision_node(state: dict) -> dict:
    if state.get("evidence_result", {}).get("status") != "PASS":
        return {
            "errors": ["Evidence Gate blocked Decision Agent"],
            "audit_trace": trace("DecisionAgent", "BLOCKED", detail="evidence insufficient"),
        }

    live_llm = bool(state.get("enable_live_llm", False))
    policy_context = state.get("policy_context", [])
    settings = get_settings()

    if not live_llm:
        advisory = _fallback_advisory(state, reason="当前未启用大模型")
        return {
            "candidate_actions": [item.model_dump() for item in advisory.candidate_actions],
            "llm_advisory": {
                "status": "DISABLED",
                "provider": "none",
                **advisory.model_dump(exclude={"candidate_actions"}),
            },
            "audit_trace": trace("DecisionAgent", "PASS", detail="deterministic fallback; live_llm=false"),
        }

    if not policy_context:
        advisory = _fallback_advisory(state, reason="未检索到已批准的制度依据")
        return {
            "candidate_actions": [item.model_dump() for item in advisory.candidate_actions],
            "llm_advisory": {
                "status": "FALLBACK",
                "provider": "none",
                "reason": "NO_APPROVED_POLICY",
                **advisory.model_dump(exclude={"candidate_actions"}),
            },
            "errors": ["No approved policy context; deterministic fallback used"],
            "audit_trace": trace("DecisionAgent", "DEGRADED", detail="no approved policy context"),
        }

    payload = {
        "supplier_id": state["supplier_id"],
        "current_week": state["current_week"],
        "supplier_profile": state.get("supplier_profile", {}),
        "current_risk_grade": state.get("risk_grade", {}),
        "risk_identification": state.get("risk_identification_result", {}),
        "dimension_breakdown": state.get("risk_grade", {}).get("dimension_breakdown", {}),
        "association_analysis": state.get("association_result", {}),
        "trend": state.get("risk_trend", {}),
        "verified_evidence": [
            {"evidence_id": item["evidence_id"], "description": item.get("readable_summary", "")}
            for item in state.get("evidence_result", {}).get("evidence_items", [])
        ],
        "approved_policy_context": policy_context,
    }

    try:
        # 运行时经工厂解析（与 text_nodes / evidence 口径一致），
        # 避免模块级绑定导致测试注入的假客户端被绕过、真实打网
        advisory = llm_factory.build_llm_client(settings).invoke(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=json.dumps(payload, ensure_ascii=False),
            response_model=AdvisoryResult,
        )
        _align_identifiers(advisory, state)
        _validate_grounding(advisory, state)
        _normalize_key_factors(advisory, state.get("risk_identification_result", {}))
        _normalize_priority(str(state.get("risk_grade", {}).get("risk_level", "GREEN")), advisory)
        return {
            "candidate_actions": [item.model_dump() for item in advisory.candidate_actions],
            "llm_advisory": {
                "status": "SUCCESS",
                "provider": settings.llm_provider,
                "model": settings.llm_model,
                **advisory.model_dump(exclude={"candidate_actions"}),
            },
            "audit_trace": trace("DecisionAgent", "PASS", detail=f"grounded_llm={settings.llm_model}"),
        }
    except Exception as exc:
        logger.warning("llm_advisory_fallback error_type=%s", type(exc).__name__)
        advisory = _fallback_advisory(state, reason="大模型暂时不可用或输出未通过校验")
        return {
            "candidate_actions": [item.model_dump() for item in advisory.candidate_actions],
            "llm_advisory": {
                "status": "FALLBACK",
                "provider": settings.llm_provider,
                "model": settings.llm_model,
                "reason": type(exc).__name__,
                **advisory.model_dump(exclude={"candidate_actions"}),
            },
            "errors": ["LLM advisory unavailable; deterministic fallback used"],
            "audit_trace": trace("DecisionAgent", "DEGRADED", detail=f"llm_fallback={type(exc).__name__}"),
        }

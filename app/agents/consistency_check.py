"""多智能体一致性校验节点（模型级交叉验证）。

职责：汇总前面各节点的结论（风险识别 / 关联分析 / 证据 / 决策建议 / 政策依据），
调用真实模型做一次「交叉校验 / 一致性投票」：
- 各节点结论之间是否互相支持、是否存在矛盾；
- 证据是否足以支撑当前结论。

边界：
- 只做一致性复核，不新增事实、不改写任何上游结论、不做处置决定；
- 不参与风险分级，模型也不得推翻规则引擎给出的等级；
- 模型不可用（无 Key / 超时 / 输出不合法）时跳过校验并标注，不阻断报告生成。
"""

from __future__ import annotations

import json
import logging

from app.agents.helpers import trace
from app.config import get_settings
from app.llm.text_nodes import invoke_text_llm
from app.schemas.contracts import ConsistencyCheckResult

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是银行外包供应商风险分析的质量复核员。
只输出 JSON 对象，并严格包含 consistent、conflicts、evidence_sufficient、notes。

硬性要求：
1. 你只做一致性复核，不修改、不推翻任何输入结论，也不做处置建议。
2. 逐项检查：风险识别与规则等级是否一致、关联分析与趋势是否自洽、
   决策建议是否与风险等级和证据相符、政策依据是否支撑结论。
3. 若发现互相矛盾之处，写入 conflicts（每条一句话，客观描述矛盾点）；无矛盾则为空数组。
4. evidence_sufficient 表示已核验证据是否足以支撑当前风险结论。
5. notes 用一句话总结复核结论，不超过 200 字；不得编造输入中不存在的事实或编号。"""


def _build_payload(state: dict) -> dict:
    grade = state.get("risk_grade", {})
    identification = state.get("risk_identification_result", {})
    association = state.get("association_result", {})
    trend = state.get("risk_trend", {})
    evidence = state.get("evidence_result", {})
    advisory = state.get("llm_advisory", {})

    return {
        "supplier_id": state.get("supplier_id", ""),
        "current_week": state.get("current_week"),
        "risk_grade": {
            "risk_level": grade.get("risk_level"),
            "score": grade.get("score"),
            "grade_basis": grade.get("grade_basis", ""),
            "hit_rules": [item.get("rule_id") for item in grade.get("hit_rules", [])],
        },
        "risk_identification": {
            "risk_level": identification.get("risk_level"),
            "summary": identification.get("summary", ""),
            "main_risks": identification.get("main_risks", []),
        },
        "association_analysis": {
            "active_dimensions": association.get("active_dimensions", []),
            "cross_dimension": association.get("cross_dimension"),
            "cross_dimension_note": association.get("cross_dimension_note", ""),
            "trend_type": trend.get("trend_type"),
            "trend_desc": trend.get("trend_desc", ""),
        },
        "evidence": {
            "status": evidence.get("status"),
            "verified_count": len(evidence.get("evidence_items", [])),
            "evidence_ids": [item.get("evidence_id") for item in evidence.get("evidence_items", [])],
            "reasons": evidence.get("reasons", []),
        },
        "decision": {
            "status": advisory.get("status"),
            "risk_summary": advisory.get("risk_summary", ""),
            "recommendation": advisory.get("recommendation", ""),
            "rationale": advisory.get("rationale", ""),
            "policy_chunk_ids": advisory.get("policy_chunk_ids", []),
        },
        "approved_policy_chunk_ids": [item.get("chunk_id") for item in state.get("policy_context", [])],
    }


def consistency_check_node(state: dict) -> dict:
    settings = get_settings()
    enabled = bool(state.get("enable_live_llm", False)) and settings.llm_consistency_check

    # 证据不足时决策 Agent 未参与，无可校验的多智能体结论，跳过校验
    if state.get("evidence_result", {}).get("status") != "PASS":
        return {
            "consistency_check_result": {"status": "SKIPPED", "reason": "EVIDENCE_GATE_BLOCKED"},
            "audit_trace": trace(
                "ConsistencyCheckAgent", "SKIPPED", detail="evidence gate blocked upstream"
            ),
        }

    payload = _build_payload(state)
    result, llm_status = invoke_text_llm(
        node="consistency_check",
        system_prompt=SYSTEM_PROMPT,
        payload=payload,
        response_model=ConsistencyCheckResult,
        enabled=enabled,
        fallback=lambda: ConsistencyCheckResult(
            consistent=True, conflicts=[], evidence_sufficient=True, notes="一致性校验未启用或不可用，已跳过。"
        ),
        settings=settings,
    )

    if llm_status == "DISABLED":
        return {
            "consistency_check_result": {
                "status": "DISABLED",
                "consistent": None,
                "conflicts": [],
                "evidence_sufficient": None,
                "notes": "未启用大模型，跳过模型级一致性校验。",
            },
            "audit_trace": trace("ConsistencyCheckAgent", "SKIPPED", detail="live_llm disabled"),
        }

    if llm_status == "FALLBACK":
        return {
            "consistency_check_result": {
                "status": "FALLBACK",
                "consistent": None,
                "conflicts": [],
                "evidence_sufficient": None,
                "notes": "大模型暂时不可用，跳过模型级一致性校验。",
            },
            "errors": ["Consistency check unavailable; skipped"],
            "audit_trace": trace("ConsistencyCheckAgent", "DEGRADED", detail="llm unavailable; skipped"),
        }

    return {
        "consistency_check_result": {
            "status": "SUCCESS",
            "consistent": result.consistent,
            "conflicts": result.conflicts,
            "evidence_sufficient": result.evidence_sufficient,
            "notes": result.notes,
        },
        "audit_trace": trace(
            "ConsistencyCheckAgent",
            "PASS" if result.consistent else "WARN",
            detail=(
                f"consistent={result.consistent}; evidence_sufficient={result.evidence_sufficient}; "
                f"conflicts={len(result.conflicts)}"
            ),
        ),
    }

"""Evidence 证据 Agent。

职责：把风险结论关联到具体风险事件与证据，形成可追溯的证据链：
- 按六个维度对证据分组；
- 把证据编号（如 E‑S‑NOR001‑1）转成人可读的事件描述；
- 校验证据归属与时间（不得引用未来周、不得跨供应商）。

边界：
- 只做证据核验与转述，不生成处置建议；
- 归属/时间校验逻辑保持确定性；readable_summary 由模型批量转述（一次调用转述多条），
  模型只允许忠实转述给定字段，不得编造；
- 模型不可用或某条转述缺失时，逐条回退确定性模板 readable_event，并标记 FALLBACK。
"""

import json
import logging

from app.agents.helpers import canonical_id, dimension_breakdown, event_dimension, readable_event, trace
from app.config import get_settings
from app.llm import factory as llm_factory
from app.schemas.contracts import EvidenceItem, EvidenceNarrationResult, EvidenceResult

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是银行外包供应商风险证据转述助手。
只输出 JSON 对象，并严格包含 narrations 数组，每个元素含 evidence_id 与 readable_summary。

硬性要求：
1. 只转述输入中给出的事件字段（周次、维度、子类型、严重程度、来源），不得新增或推测事实。
2. evidence_id 必须与输入中的编号完全一致，逐条一一对应，不得编造编号或遗漏。
3. readable_summary 为一句中文客观描述，不超过 120 字。
4. 不得给出判断、建议或未来预测。"""

SEVERITY_LABEL = {0: "提示", 1: "一般", 2: "较重", 3: "严重", 4: "严重", 5: "严重"}


def _requested_evidence_ids(state: dict) -> list[str]:
    """证据来源优先级：分级命中事件 → 趋势支撑证据 → 近期全部事件。"""
    ids: list[str] = []
    for event in state.get("visible_events", []):
        evidence_id = str(event.get("evidence_id", ""))
        if evidence_id and evidence_id not in ids:
            ids.append(evidence_id)
    trend_ids = state.get("risk_trend", {}).get("trend_support_evidence_ids", [])
    for evidence_id in trend_ids:
        if evidence_id not in ids:
            ids.append(evidence_id)
    return ids


def _event_payload(event: dict) -> dict:
    """传给模型的转述输入：只包含可读转述所需的字段，避免 token 膨胀。"""
    return {
        "evidence_id": str(event.get("evidence_id")),
        "event_week": int(event.get("event_week", 1)),
        "event_category": str(event.get("event_category")),
        "mapped_dimension": event_dimension(event),
        "event_subtype": str(event.get("event_subtype")),
        "event_severity": int(event.get("event_severity", 0)),
        "severity_label": SEVERITY_LABEL.get(int(event.get("event_severity", 0)), "未知"),
        "source_type": str(event.get("source_type")),
    }


def _narrate_events(events: list[dict], *, enabled: bool) -> tuple[dict[str, str], str]:
    """一次调用批量转述多条事件，返回 (evidence_id -> 可读描述, 状态)。

    任何异常都降级为确定性模板描述，状态标记 FALLBACK；未启用时标记 DISABLED。
    返回的映射只包含成功转述且编号命中输入集合的条目，其余由调用方回退模板。
    """
    if not enabled:
        return {}, "DISABLED"
    if not events:
        return {}, "SUCCESS"

    settings = get_settings()
    payload = {"events": [_event_payload(event) for event in events]}
    allowed = {canonical_id(payload_item["evidence_id"]) for payload_item in payload["events"]}
    try:
        # 延迟解析工厂，便于测试注入假客户端（与 text_nodes.invoke_text_llm 口径一致）
        result = llm_factory.build_llm_client(settings).invoke(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=json.dumps(payload, ensure_ascii=False),
            response_model=EvidenceNarrationResult,
        )
    except Exception as exc:
        logger.warning("llm_evidence_narration_fallback error_type=%s", type(exc).__name__)
        return {}, "FALLBACK"

    narrations: dict[str, str] = {}
    for item in result.narrations:
        # 只接受输入集合内的编号，越界条目直接丢弃（回退模板，不污染证据链）
        if canonical_id(item.evidence_id) not in allowed:
            continue
        narrations[canonical_id(item.evidence_id)] = item.readable_summary
    return narrations, "SUCCESS"


def evidence_node(state: dict) -> dict:
    settings = get_settings()
    supplier_id = str(state["supplier_id"])
    current_week = int(state["current_week"])
    events = {str(event.get("evidence_id")): event for event in state.get("events", [])}
    requested = _requested_evidence_ids(state)

    reasons: list[str] = []
    items: list[EvidenceItem] = []

    if not requested:
        reasons.append("近期窗口内没有可关联的风险事件，无法形成证据链")

    # 先做确定性归属/时间校验，收集通过校验的事件
    verified: list[dict] = []
    for evidence_id in requested:
        event = events.get(evidence_id)
        if event is None:
            # LLM 可能输出视觉等价但字符不同的连字符，按归一化后重试
            event = next(
                (value for key, value in events.items() if canonical_id(key) == canonical_id(evidence_id)),
                None,
            )
        if event is None:
            reasons.append(f"未知证据编号：{evidence_id}")
            continue
        if str(event.get("supplier_id")) != supplier_id:
            reasons.append(f"证据不属于当前供应商：{evidence_id}")
            continue
        if int(event.get("event_week", 0)) > current_week:
            reasons.append(f"禁止引用未来周证据：{evidence_id}")
            continue
        verified.append(event)

    narrations, llm_status = _narrate_events(
        verified,
        enabled=bool(state.get("enable_live_llm", False)) and settings.llm_text_evidence,
    )

    for event in verified:
        evidence_id = str(event.get("evidence_id"))
        items.append(
            EvidenceItem(
                evidence_id=evidence_id,
                supplier_id=str(event.get("supplier_id")),
                event_category=str(event.get("event_category")),
                event_subtype=str(event.get("event_subtype")),
                event_severity=int(event.get("event_severity", 0)),
                event_week=int(event.get("event_week", 1)),
                source_type=str(event.get("source_type")),
                readable_summary=narrations.get(canonical_id(evidence_id)) or readable_event(event),
                mapped_dimension=event_dimension(event),
            )
        )

    # 证据按「新维度」分组：优先取映射后的维度，回退原始类别（迁移期稳定）
    by_dimension: dict[str, list[str]] = {}
    for item in items:
        by_dimension.setdefault(item.mapped_dimension, []).append(item.evidence_id)

    status = "PASS" if items and not reasons else "FAIL"
    result = EvidenceResult(
        status=status,
        evidence_items=items,
        by_dimension=by_dimension,
        reasons=reasons,
    )
    return {
        "evidence_result": {**result.model_dump(), "llm_status": llm_status},
        "audit_trace": trace(
            "EvidenceAgent",
            result.status,
            detail=(
                f"verified={len(items)}; dimensions={len(by_dimension)}; "
                f"llm={llm_status}; " + ("; ".join(reasons) or "ok")
            ),
        ),
    }


def route_after_evidence(state: dict) -> str:
    """证据不足时跳过高风险专项分析，直接进入人工复核。"""
    return "policy_retrieval" if state.get("evidence_result", {}).get("status") == "PASS" else "human_review"

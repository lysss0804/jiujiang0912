from datetime import datetime, timezone
from typing import Any

from app.schemas.contracts import RISK_DIMENSIONS


def trace(agent_name: str, status: str, *, detail: str = "") -> list[dict]:
    return [
        {
            "agent_name": agent_name,
            "status": status,
            "detail": detail,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    ]


def events_in_window(
    events: list[dict[str, Any]],
    *,
    supplier_id: str,
    week_start: int,
    week_end: int,
) -> list[dict[str, Any]]:
    """按周次窗口过滤指定供应商的事件（闭区间）。"""
    return [
        event
        for event in events
        if str(event.get("supplier_id")) == str(supplier_id)
        and week_start <= int(event.get("event_week", 0)) <= week_end
    ]


def event_dimension(event: dict[str, Any]) -> str:
    """取事件的风险维度：优先使用维度归类节点的 `mapped_dimension`，回退原始类别。

    迁移期保障：维度归类节点上线前或降级时，仍能按旧类别统计而不崩。
    """
    return str(event.get("mapped_dimension") or event.get("event_category", ""))


def dimension_breakdown(events: list[dict[str, Any]]) -> dict[str, int]:
    """统计六个维度的事件数量，保证六大维度全部出现（无事件则为 0）。

    统计键优先使用 `mapped_dimension`（维度归类节点写入），回退 `event_category`。
    """
    counts: dict[str, int] = {name: 0 for name in RISK_DIMENSIONS}
    for event in events:
        dimension = event_dimension(event)
        if dimension not in counts:
            # 别名表可能给出六个固定维度之外的兜底值，这里按兜底维度归并
            dimension = RISK_DIMENSIONS[-1]
        counts[dimension] = counts.get(dimension, 0) + 1
    return counts


def readable_event(event: dict[str, Any]) -> str:
    """把事件编号转成人可读的一句话描述，用于报告与证据摘要。"""
    severity_label = {0: "提示", 1: "一般", 2: "较重", 3: "严重", 4: "严重", 5: "严重"}.get(
        int(event.get("event_severity", 0)), "未知"
    )
    return (
        f"第{event.get('event_week')}周｜{event_dimension(event)}维度｜"
        f"{event.get('event_subtype')}（严重程度：{severity_label}）"
        f"，来源：{event.get('source_type')}"
    )


def canonical_id(value: str) -> str:
    """统一各种视觉等价的连字符变体，用于 ID 对齐。

    源数据使用 U+2011 非断行连字符，LLM 往往输出 ASCII 连字符，
    比较前需要归一化，但返回给外部时仍使用源数据原始 ID。
    """
    return value.translate(str.maketrans({"‑": "-", "–": "-", "—": "-", "−": "-"})).strip()


def allowed_evidence_ids(state: dict[str, Any]) -> list[str]:
    """当前状态下允许被引用的证据编号集合（保持源数据顺序）。"""
    ids: list[str] = []
    for item in state.get("evidence_result", {}).get("evidence_items", []):
        evidence_id = item.get("evidence_id")
        if evidence_id and evidence_id not in ids:
            ids.append(str(evidence_id))
    return ids


def allowed_policy_chunk_ids(state: dict[str, Any]) -> list[str]:
    """当前状态下允许被引用的政策 chunk_id 集合（保持源数据顺序）。"""
    ids: list[str] = []
    for item in state.get("policy_context", []):
        chunk_id = item.get("chunk_id")
        if chunk_id and chunk_id not in ids:
            ids.append(str(chunk_id))
    return ids


def align_ids(references: list[str], allowed: list[str]) -> list[str]:
    """把 LLM 输出的视觉等价 ID 恢复为源数据中的精确 ID（不改变来源集合）。"""
    mapping = {canonical_id(item): item for item in allowed}
    return [mapping.get(canonical_id(item), item) for item in references]


def unknown_ids(references: list[str], allowed: list[str]) -> list[str]:
    """返回越界引用（引用了输入集合之外的 ID），用于 grounding 拦截。"""
    allowed_set = {canonical_id(item) for item in allowed}
    return sorted({item for item in references if canonical_id(item) not in allowed_set})


def require_grounded(references: list[str], allowed: list[str], *, kind: str) -> None:
    """校验引用是否全部来自输入集合，越界即抛错（由上层降级处理）。"""
    violations = unknown_ids(references, allowed)
    if violations:
        raise ValueError(f"LLM cited {kind} identifiers outside supplied context: {violations}")

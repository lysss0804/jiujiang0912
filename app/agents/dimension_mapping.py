"""维度归类节点（旧事件类别 -> 新六大风险维度）。

职责：
- 在风险识别之前，把每条事件归入新的六大风险维度之一；
- 归类由**模型**完成（一次调用批量返回全部归类，禁止逐条请求），
  模型不可用 / 输出越界时退化为确定性别名表（`dimension_alias.yaml`）；
- 为每条事件写入 `mapped_dimension` 字段，供下游统计与报告统一引用；
- 本期数据中无来源的维度标记 NO_DATA，分值计 0，**不编造任何事件或证据**。

边界：
- 不做任何数值计算（分值仍由规则引擎负责），只做语义归类；
- grounding 校验：模型返回的 evidence_id 必须来自输入集合，越界条目直接丢弃。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.agents.helpers import canonical_id, event_dimension, trace
from app.config import get_settings
from app.llm.text_nodes import invoke_text_llm
from app.schemas.contracts import RISK_DIMENSIONS, DimensionMappingResult

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是银行外包供应商风险事件的维度归类助手。
只输出 JSON 对象，并严格包含 mappings 数组，每个元素含 evidence_id 与 new_dimension。

硬性要求：
1. new_dimension 只能取以下六个值之一：公司背景、司法、失信、经营风险、经营状况、知识产权。
2. evidence_id 必须与输入中的编号完全一致，逐条一一对应，不得编造编号或遗漏。
3. 只做归类，不得新增、修改或推测任何事件事实，不得输出解释性文字。
4. 归类依据 event_category、event_subtype、source_type 三个字段的语义。"""

# 进程内缓存：批量场景下避免同一供应商在同一窗口内重复归类
_MAPPING_CACHE: dict[tuple[str, int, str], dict[str, str]] = {}


def clear_mapping_cache() -> None:
    """清空进程内归类缓存（配置变更或测试隔离时使用）。"""
    _MAPPING_CACHE.clear()


_DEFAULT_ALIAS_PATH = Path("app/rules/dimension_alias.yaml")


def load_dimension_alias(path: Path | None = None) -> dict[str, Any]:
    """读取确定性别名表；文件缺失或未安装 PyYAML 时返回空表。"""
    target = path or _DEFAULT_ALIAS_PATH
    if not target.exists():
        return {}
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        return {}
    try:
        with target.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
    except Exception as exc:  # 配置损坏不应阻断工作流
        logger.warning("dimension_alias_load_failed error_type=%s", type(exc).__name__)
        return {}
    return loaded if isinstance(loaded, dict) else {}


def alias_dimension(
    event: dict[str, Any],
    alias: dict[str, Any],
    *,
    categories: tuple[str, ...] = RISK_DIMENSIONS,
) -> str:
    """确定性归类：子类型关键词 > 事件类别 > 数据来源 > 兜底维度。"""
    fallback = str(alias.get("fallback_dimension") or categories[-1])
    subtype = str(event.get("event_subtype", ""))
    category = str(event.get("event_category", ""))
    source_type = str(event.get("source_type", ""))

    for rule in alias.get("subtype_rules") or []:
        dimension = str(rule.get("dimension", ""))
        if dimension not in categories:
            continue
        if any(str(keyword) in subtype for keyword in rule.get("keywords") or []):
            return dimension

    mapped = (alias.get("category_rules") or {}).get(category)
    if mapped in categories:
        return str(mapped)

    mapped = (alias.get("source_rules") or {}).get(source_type)
    if mapped in categories:
        return str(mapped)

    return fallback if fallback in categories else categories[-1]


def _event_payload(event: dict[str, Any]) -> dict[str, str]:
    """传给模型的归类输入：只含语义字段，控制 token。"""
    return {
        "evidence_id": str(event.get("evidence_id")),
        "event_category": str(event.get("event_category", "")),
        "event_subtype": str(event.get("event_subtype", "")),
        "source_type": str(event.get("source_type", "")),
    }


def map_dimensions(
    events: list[dict[str, Any]],
    *,
    enabled: bool,
    supplier_id: str = "",
    current_week: int = 0,
    window_key: str = "",
    alias: dict[str, Any] | None = None,
) -> tuple[dict[str, str], str]:
    """批量归类，返回 ({evidence_id -> new_dimension}, 状态)。

    状态语义：SUCCESS=模型产出；FALLBACK=模型不可用已降级；DISABLED=未启用真实模型。
    返回映射只保留合法维度与输入集合内的编号，其余由调用方回退别名表。
    """
    alias = alias if alias is not None else load_dimension_alias()
    if not events:
        return {}, "SUCCESS" if enabled else "DISABLED"

    cache_key = (str(supplier_id), int(current_week), str(window_key))
    if enabled and cache_key in _MAPPING_CACHE:
        return dict(_MAPPING_CACHE[cache_key]), "SUCCESS"

    if not enabled:
        return {}, "DISABLED"

    settings = get_settings()
    payload = {"events": [_event_payload(event) for event in events]}
    allowed = {canonical_id(item["evidence_id"]) for item in payload["events"]}

    try:
        result = invoke_text_llm(
            node="dimension_mapping",
            system_prompt=SYSTEM_PROMPT,
            payload=payload,
            response_model=DimensionMappingResult,
            enabled=True,
            fallback=lambda: DimensionMappingResult(mappings=[]),
            settings=settings,
            validate=lambda output: _validate_mapping(output, allowed),
        )
        mapping_result, llm_status = result
    except Exception as exc:  # 兜底：任何异常都降级
        logger.warning("dimension_mapping_fallback error_type=%s", type(exc).__name__)
        return {}, "FALLBACK"

    mapping: dict[str, str] = {}
    for item in mapping_result.mappings:
        if item.new_dimension not in RISK_DIMENSIONS:
            continue
        key = canonical_id(item.evidence_id)
        if key not in allowed:
            continue
        mapping[key] = item.new_dimension

    if mapping:
        _MAPPING_CACHE[cache_key] = dict(mapping)
    return mapping, llm_status


def _validate_mapping(output: DimensionMappingResult, allowed: set[str]) -> None:
    """拦截越界证据引用：模型只能引用输入中给出的证据编号。"""
    for item in output.mappings:
        if canonical_id(item.evidence_id) not in allowed:
            raise ValueError(f"dimension mapping cited unknown evidence_id: {item.evidence_id}")
        if item.new_dimension not in RISK_DIMENSIONS:
            raise ValueError(f"dimension mapping produced unknown dimension: {item.new_dimension}")


def dimension_mapping_node(state: dict) -> dict:
    """把统计窗口内的事件归入新六维度，写入 `mapped_dimension`。"""
    from app.rules.window import resolve_window

    settings = get_settings()
    supplier_id = str(state.get("supplier_id", ""))
    current_week = int(state.get("current_week", 1))
    grade = state.get("risk_grade", {}) or {}
    events = list(state.get("events", []))

    spec = resolve_window(
        current_week=current_week,
        unit=state.get("window_unit") or grade.get("window_unit"),
        size=state.get("window_size") or grade.get("window_size"),
        window_weeks=grade.get("window_weeks"),
    )
    window_events = [
        event
        for event in events
        if str(event.get("supplier_id")) == supplier_id
        and spec.start_week <= int(event.get("event_week", 0)) <= spec.end_week
    ]

    enabled = bool(state.get("enable_live_llm", False)) and settings.llm_dimension_mapping
    alias = load_dimension_alias()
    model_mapping, llm_status = map_dimensions(
        window_events,
        enabled=enabled,
        supplier_id=supplier_id,
        current_week=current_week,
        window_key=spec.display,
        alias=alias,
    )
    if llm_status == "FALLBACK":
        alias = alias or {}
    elif llm_status == "SUCCESS" and not model_mapping:
        # 模型返回空映射：视作降级，避免事件全部落到兜底维度
        llm_status = "FALLBACK"

    mapped_by_id: dict[str, str] = {}
    for event in window_events:
        key = canonical_id(str(event.get("evidence_id", "")))
        dimension = model_mapping.get(key)
        if dimension not in RISK_DIMENSIONS:
            dimension = alias_dimension(event, alias)
        mapped_by_id[key] = dimension

    counts: dict[str, int] = {name: 0 for name in RISK_DIMENSIONS}
    for dimension in mapped_by_id.values():
        counts[dimension] = counts.get(dimension, 0) + 1
    no_data_dimensions = [name for name, count in counts.items() if count == 0]
    hit_dimensions = [name for name, count in counts.items() if count > 0]

    # 把归类结果写回事件字典，供下游统计/报告统一引用（迁移期稳定：回退原始类别）
    enriched_events: list[dict[str, Any]] = []
    for event in events:
        key = canonical_id(str(event.get("evidence_id", "")))
        dimension = mapped_by_id.get(key)
        if dimension in RISK_DIMENSIONS:
            event = {**event, "mapped_dimension": dimension}
        enriched_events.append(event)

    # 分级的分维度得分键需与新维度对齐：按新维度重组（分值只做搬家，不重算）
    grade = dict(state.get("risk_grade", {}) or {})
    raw_breakdown = grade.get("dimension_breakdown") or {}
    if raw_breakdown:
        regrouped = {name: 0.0 for name in RISK_DIMENSIONS}
        for event in enriched_events:
            if str(event.get("supplier_id")) != supplier_id:
                continue
            if not (spec.start_week <= int(event.get("event_week", 0)) <= spec.end_week):
                continue
            legacy_key = str(event.get("event_category", ""))
            value = float(raw_breakdown.get(legacy_key, 0.0))
            if value == 0.0:
                continue
            # 同类别多事件时按占比均摊，保证总和不变
            same_category = sum(
                1
                for item in enriched_events
                if str(item.get("event_category", "")) == legacy_key
                and str(item.get("supplier_id")) == supplier_id
                and spec.start_week <= int(item.get("event_week", 0)) <= spec.end_week
            )
            share = value / max(1, same_category)
            regrouped[event_dimension(event)] = round(
                regrouped.get(event_dimension(event), 0.0) + share, 4
            )
        grade["dimension_breakdown"] = regrouped

    return {
        "events": enriched_events,
        "dimension_stats": {
            "counts": counts,
            "window_weeks": spec.window_weeks,
            "window_display": spec.display,
            "start_week": spec.start_week,
            "end_week": spec.end_week,
            "no_data_dimensions": no_data_dimensions,
        },
        "dimension_mapping_result": {
            "status": llm_status,
            "mapped": mapped_by_id,
            "counts": counts,
            "no_data_dimensions": no_data_dimensions,
        },
        "risk_grade": grade,
        "audit_trace": trace(
            "DimensionMappingAgent",
            "PASS",
            detail=(
                f"mapped={len(mapped_by_id)}; hit_dimensions={len(hit_dimensions)}; "
                f"no_data={len(no_data_dimensions)}; llm={llm_status}"
            ),
        ),
    }

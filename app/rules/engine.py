"""风险分级引擎（纯函数、无 LLM、无副作用）。

职责：根据供应商「近期」风险事件 + 重要程度 + 整改状态，按可配置规则
计算当前风险等级（红/黄/绿），并给出命中规则明细。

严格边界：
- 分级结果仅用于报告展示，不参与任何自动处置；
- 所有阈值与权重来自 rules.yaml，便于后续替换为银行正式规则；
- 本模块不做任何自然语言生成。
"""

from __future__ import annotations

from typing import Any

from app.rules.config import get_rules
from app.rules.window import WindowSpec, resolve_window
from app.schemas.contracts import HitRule, RiskGrade

SEVERITY_RED_LINE = 3


def _time_decay_factor(weeks_ago: int, decay_table: list[dict[str, Any]]) -> float:
    for item in decay_table:
        if weeks_ago <= item["max_weeks_ago"]:
            return float(item["factor"])
    return float(decay_table[-1]["factor"]) if decay_table else 1.0


def resolve_importance(
    *,
    importance_level: str | None = None,
    contract_importance: str | None = None,
    system_level: str | None = None,
    rules: dict[str, Any],
) -> tuple[str, float, str]:
    """判定供应商重要性档位（两级）。

    权威来源是银行下发的名单（`suppliers.csv::importance_level`），系统只读不算：
    命中名单直接采信并返回 `source="BANK_LIST"`，**不做 max 覆盖**。
    仅当名单缺失/非法时，才回退到「合同重要性 + 承载系统等级」合成，
    返回值 `source="DERIVED"`，并按两级归并（原「核心」并入「重要」）。
    """
    contract_mapping = rules.get("contract_importance_mapping", {})
    system_mapping = rules.get("system_level_mapping", {})
    coefficients = rules.get("importance_coefficients", {})

    if importance_level and importance_level in coefficients:
        return importance_level, float(coefficients[importance_level]), "BANK_LIST"

    tiers: list[str] = []
    if contract_importance and contract_importance in contract_mapping:
        tiers.append(contract_mapping[contract_importance])
    if system_level and system_level in system_mapping:
        tiers.append(system_mapping[system_level])

    # 合成结果归并为两级：出现「重要/核心」即为「重要」，否则「一般」
    tier = "重要" if any(item in ("重要", "核心") for item in tiers) else "一般"
    return tier, float(coefficients.get(tier, 1.0)), "DERIVED"


def is_minor_issue(
    *,
    risk_level: str,
    score: float,
    red_line_hit: bool,
    max_severity: int,
    rules: dict[str, Any],
) -> tuple[bool, str]:
    """处置门禁（确定性）：判定是否属于「问题较小、可允许供应商重新申报」。

    四个条件同时满足才通过（模型**不参与**该判定，避免门禁被放宽）：
    1. 风险等级为中/低档（YELLOW / GREEN）；
    2. 分值不超过可配上限（`disposition_gate.redeclare_max_score`）；
    3. 未命中红线规则；
    4. 窗口内最高事件严重度 < 门禁上限（`disposition_gate.max_severity_allowed`）。
    """
    gate = rules.get("disposition_gate") or {}
    max_score = float(gate.get("redeclare_max_score", 2.5))
    severity_cap = int(gate.get("max_severity_allowed", 3))

    if risk_level not in ("YELLOW", "GREEN"):
        return False, f"风险等级为{risk_level}，不属于「问题较小」"
    if red_line_hit:
        return False, "命中红线规则，不允许供应商自行重新申报"
    if score > max_score:
        return False, f"规则得分{score:.2f}超过可重新申报上限{max_score:g}"
    if max_severity >= severity_cap:
        return False, f"存在严重程度≥{severity_cap}的事件，需人工复核"
    return True, f"风险等级{risk_level}、得分{score:.2f}且无红线与高严重度事件，问题较小"


def resolve_report_period(*, importance_tier: str, rules: dict[str, Any]) -> tuple[str, str]:
    """按供应商重要性判定报告周期：``重要`` → 周报，``一般`` → 月报。

    返回 ``(unit, label)``，例如 ``("weekly", "周报")``。
    这是纯确定性映射（来自银行名单），与风险等级 RED/YELLOW/GREEN 无关。
    """
    mapping = rules.get("report_period_by_importance", {})
    labels = rules.get("report_period_labels", {})
    unit = str(mapping.get(importance_tier, "monthly"))
    return unit, str(labels.get(unit, "月报"))


def event_dimension(event: dict[str, Any]) -> str:
    """取事件的风险维度：优先使用维度归类节点的 `mapped_dimension`，回退原始类别。

    这样「维度归类节点上线前/降级时」链路仍然可用，逐步迁移不会崩。
    """
    return str(event.get("mapped_dimension") or event.get("event_category", ""))


def resolve_grade_tier(
    *, score: float, red_line_hit: bool, dimensions_hit: int, rules: dict[str, Any]
) -> tuple[str, str, str, float]:
    """按有序 `grade_tiers` 取档，返回 (tier, label, range, next_threshold_gap)。

    - 红线优先于分值：命中红线直接判最高档；
    - 其余按「从高到低取第一个满足 score >= min_score 的档」；
    - `tier_rules.dimensions_hit_min_count` 兜底条件保留为可配开关（默认开启）。
    """
    tiers = rules.get("grade_tiers") or []
    if not tiers:
        tiers = [{"tier": "GREEN", "label": "低风险", "min_score": 0.0, "range": "[0, +∞)"}]

    top = tiers[0]
    if red_line_hit:
        tier, label, value_range = str(top["tier"]), str(top["label"]), str(top.get("range", ""))
    else:
        picked = next((item for item in tiers if score >= float(item["min_score"])), tiers[-1])
        tier, label, value_range = str(picked["tier"]), str(picked["label"]), str(picked.get("range", ""))

    # 多维度兜底：命中维度数达到阈值且当前档位低于 YELLOW 时，至少提到 YELLOW
    tier_rules = rules.get("tier_rules") or {}
    if tier_rules.get("enabled", True):
        threshold = int(tier_rules.get("dimensions_hit_min_count", 3))
        order = {str(item["tier"]): index for index, item in enumerate(tiers)}
        yellow = next((item for item in tiers if str(item["tier"]) == "YELLOW"), None)
        if (
            yellow is not None
            and dimensions_hit >= threshold
            and not red_line_hit
            and order.get(tier, 99) > order.get("YELLOW", 0)
        ):
            tier = "YELLOW"
            label = str(yellow["label"])
            value_range = str(yellow.get("range", ""))

    index = next((i for i, item in enumerate(tiers) if str(item["tier"]) == tier), len(tiers) - 1)
    current = tiers[index]
    upper = tiers[index - 1] if index > 0 else None
    gap = (float(upper["min_score"]) - score) if upper is not None else 0.0
    return tier, label, value_range, round(max(0.0, gap), 4)


def _build_hit_rules(events: list[dict[str, Any]], rules: dict[str, Any]) -> list[HitRule]:
    category_counts: dict[str, int] = {}
    max_severity = 0
    for event in events:
        category = event_dimension(event)
        category_counts[category] = category_counts.get(category, 0) + 1
        max_severity = max(max_severity, int(event.get("event_severity", 0)))

    hits: list[HitRule] = []
    severity_weights = rules.get("severity_weights", {})
    for rule in rules.get("red_line_rules", []) + rules.get("detail_rules", []):
        condition = rule.get("condition", {})
        condition_type = condition.get("type")
        threshold = int(condition.get("value", 0))
        category = str(rule.get("category", "*"))
        matched = False

        if condition_type == "severity_at_least":
            matched = max_severity >= threshold
        elif condition_type == "category_event_count_at_least":
            matched = category_counts.get(category, 0) >= threshold
        elif condition_type == "cross_category_at_least":
            matched = sum(1 for count in category_counts.values() if count > 0) >= threshold

        if not matched:
            continue
        relevant_severity = max_severity if category == "*" else max(
            (int(event.get("event_severity", 0)) for event in events if event_dimension(event) == category),
            default=0,
        )
        hits.append(
            HitRule(
                rule_id=str(rule.get("rule_id", "UNKNOWN")),
                category=category,
                description=str(rule.get("description", "")),
                severity=relevant_severity,
                weight=float(severity_weights.get(relevant_severity, 0.0)),
            )
        )
    return hits


def grade_supplier(
    *,
    supplier_id: str,
    current_week: int,
    events: list[dict[str, Any]],
    importance_level: str | None = None,
    contract_importance: str | None = None,
    system_level: str | None = None,
    in_rectify: bool = False,
    window: WindowSpec | None = None,
    window_weeks: int | None = None,
    window_unit: str | None = None,
    window_size: int | None = None,
    rules: dict[str, Any] | None = None,
) -> RiskGrade:
    """计算单个供应商的当前风险等级与报告周期。

    events 应为该供应商的全部风险事件；本函数按传入的 `WindowSpec`（或由
    `window_weeks` / `window_unit` / `window_size` 解析）过滤「统计窗口」内的事件，
    保证全链路窗口口径唯一。

    兼容性：仍支持旧的 `window_weeks` 参数，不传新参数时与改造前行为一致。
    """
    resolved_rules = rules or get_rules()
    spec = window or resolve_window(
        current_week=current_week,
        unit=window_unit,
        size=window_size,
        window_weeks=window_weeks,
        rules=resolved_rules,
    )
    window_label = spec.display

    recent = [
        event
        for event in events
        if str(event.get("supplier_id")) == str(supplier_id)
        and spec.start_week <= int(event.get("event_week", 0)) <= spec.end_week
    ]

    category_weights = resolved_rules.get("category_weights", {})
    severity_weights = resolved_rules.get("severity_weights", {})
    decay_table = resolved_rules.get("time_decay", [])

    dimension_breakdown: dict[str, float] = {name: 0.0 for name in category_weights}
    for event in recent:
        category = event_dimension(event)
        severity = int(event.get("event_severity", 0))
        weeks_ago = max(0, spec.end_week - int(event.get("event_week", spec.end_week)))
        score = (
            float(severity_weights.get(severity, 0.0))
            * float(category_weights.get(category, 1.0))
            * _time_decay_factor(weeks_ago, decay_table)
        )
        dimension_breakdown[category] = dimension_breakdown.get(category, 0.0) + score

    raw_score = sum(dimension_breakdown.values())
    tier, coefficient, importance_source = resolve_importance(
        importance_level=importance_level,
        contract_importance=contract_importance,
        system_level=system_level,
        rules=resolved_rules,
    )
    report_period_unit, report_period_label = resolve_report_period(
        importance_tier=tier, rules=resolved_rules
    )
    rectify_coefficient = float(resolved_rules.get("rectify_coefficient", 1.0)) if in_rectify else 1.0
    score = raw_score * coefficient * rectify_coefficient

    dimensions_hit = sum(1 for value in dimension_breakdown.values() if value > 0)
    hit_rules = _build_hit_rules(recent, resolved_rules)
    red_line_ids = {str(rule.get("rule_id")) for rule in resolved_rules.get("red_line_rules", [])}
    red_line_hit = any(hit.rule_id in red_line_ids for hit in hit_rules)

    tier_name, tier_label, tier_range, gap = resolve_grade_tier(
        score=score, red_line_hit=red_line_hit, dimensions_hit=dimensions_hit, rules=resolved_rules
    )

    source_note = "银行名单" if importance_source == "BANK_LIST" else "系统推算"
    basis_parts = [
        f"{window_label}内共{len(recent)}条风险事件，覆盖{dimensions_hit}个维度",
        f"规则得分{score:.2f}，等级「{tier_label}」（分值标准：{tier_range}）",
        f"供应商重要性「{tier}」（系数{coefficient:g}，来源：{source_note}）",
    ]
    if in_rectify:
        basis_parts.append(f"处于整改期内（系数{rectify_coefficient:g}）")
    if red_line_hit:
        basis_parts.append("命中红线规则，直接判最高档")

    return RiskGrade(
        supplier_id=str(supplier_id),
        risk_level=tier_name,  # type: ignore[arg-type]
        score=round(score, 4),
        grade_tier=tier_name,  # type: ignore[arg-type]
        grade_label=tier_label,
        grade_range=tier_range,
        next_threshold_gap=gap,
        grade_basis="；".join(basis_parts),
        hit_rules=hit_rules,
        dimension_breakdown={key: round(value, 4) for key, value in dimension_breakdown.items()},
        recent_event_count=len(recent),
        window_weeks=spec.window_weeks,
        window_display=window_label,
        window_unit=spec.unit,
        window_size=spec.size,
        importance_tier=tier,  # type: ignore[arg-type]
        importance_coefficient=coefficient,
        importance_source=importance_source,  # type: ignore[arg-type]
        report_period=report_period_unit,  # type: ignore[arg-type]
        report_period_label=report_period_label,
        in_rectify=in_rectify,
    )

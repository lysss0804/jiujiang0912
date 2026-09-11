"""风险分级规则配置加载。

规则全部外置在 YAML 中，便于后续替换为银行正式规则；
本模块只负责读取、校验与提供默认回退，不包含业务计算逻辑。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from app.common.errors import ConfigurationError
from app.config import get_settings

DEFAULT_RULES: dict[str, Any] = {
    "version": "builtin-fallback",
    "description": "内置兜底规则，未找到 rules.yaml 时使用",
    "window_unit": "week",
    "window_size": 12,
    "weeks_per_month": 4,
    "category_weights": {
        "公司背景": 1.1,
        "司法": 1.3,
        "失信": 1.4,
        "经营风险": 1.2,
        "经营状况": 1.0,
        "知识产权": 0.9,
    },
    "severity_weights": {0: 0.0, 1: 0.6, 2: 1.0, 3: 2.0, 4: 3.0, 5: 4.0},
    "time_decay": [
        {"max_weeks_ago": 2, "factor": 1.0},
        {"max_weeks_ago": 4, "factor": 0.85},
        {"max_weeks_ago": 8, "factor": 0.6},
        {"max_weeks_ago": 12, "factor": 0.4},
        {"max_weeks_ago": 999, "factor": 0.2},
    ],
    "importance_coefficients": {"一般": 1.0, "重要": 1.2},
    "contract_importance_mapping": {"一般外包": "一般", "重要外包": "重要"},
    "system_level_mapping": {"一般": "一般", "重要": "重要", "核心": "重要"},
    "report_period_by_importance": {"重要": "weekly", "一般": "monthly"},
    "report_period_labels": {"weekly": "周报", "monthly": "月报"},
    "rectify_coefficient": 0.85,
    "grade_tiers": [
        {"tier": "RED", "label": "高风险", "min_score": 2.0, "range": "[2.0, +∞)"},
        {"tier": "YELLOW", "label": "中风险", "min_score": 0.8, "range": "[0.8, 2.0)"},
        {"tier": "GREEN", "label": "低风险", "min_score": 0.0, "range": "[0, 0.8)"},
    ],
    "tier_rules": {"dimensions_hit_min_count": 3, "enabled": True},
    "disposition_gate": {"redeclare_max_score": 0.8, "max_severity_allowed": 3},
    "red_line_rules": [
        {
            "rule_id": "RL-SEV-3",
            "category": "*",
            "description": "统计窗口内出现严重程度>=3的极高风险事件",
            "condition": {"type": "severity_at_least", "value": 3},
        }
    ],
    "detail_rules": [
        {
            "rule_id": "R-MUL-01",
            "category": "*",
            "description": "统计窗口内同时出现3个及以上风险维度，存在多维度交叉风险",
            "condition": {"type": "cross_category_at_least", "value": 3},
        }
    ],
}


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    """YAML 中的数字键会被解析为 int/str 混合，这里统一为 int。"""
    severity = raw.get("severity_weights") or {}
    raw["severity_weights"] = {int(key): float(value) for key, value in severity.items()}
    decay = raw.get("time_decay") or []
    raw["time_decay"] = sorted(
        ({"max_weeks_ago": int(item["max_weeks_ago"]), "factor": float(item["factor"])} for item in decay),
        key=lambda item: item["max_weeks_ago"],
    )
    # 兼容旧的 thresholds 配置：自动升级为有序 grade_tiers
    if not raw.get("grade_tiers") and raw.get("thresholds"):
        thresholds = raw["thresholds"]
        red = float(thresholds.get("red", 2.0))
        yellow = float(thresholds.get("yellow", 0.8))
        raw["grade_tiers"] = [
            {"tier": "RED", "label": "高风险", "min_score": red, "range": f"[{red:g}, +∞)"},
            {"tier": "YELLOW", "label": "中风险", "min_score": yellow, "range": f"[{yellow:g}, {red:g})"},
            {"tier": "GREEN", "label": "低风险", "min_score": 0.0, "range": f"[0, {yellow:g})"},
        ]
    tiers = raw.get("grade_tiers") or []
    raw["grade_tiers"] = sorted(
        (
            {
                "tier": str(item.get("tier", "GREEN")),
                "label": str(item.get("label", item.get("tier", "低风险"))),
                "min_score": float(item.get("min_score", 0.0)),
                "range": str(item.get("range", "")),
            }
            for item in tiers
        ),
        key=lambda item: item["min_score"],
        reverse=True,
    )
    return raw


def _parse_yaml(path: Path) -> dict[str, Any]:
    """优先使用 PyYAML；若未安装则退化为内置规则，避免额外硬依赖。"""
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        return {}
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    return loaded if isinstance(loaded, dict) else {}


def load_rules(path: Path | None = None) -> dict[str, Any]:
    target = path or get_settings().rules_config_path
    rules = dict(DEFAULT_RULES)
    if target.exists():
        loaded = _parse_yaml(target)
        if loaded:
            rules.update(loaded)
    required = (
        "category_weights",
        "severity_weights",
        "time_decay",
        "importance_coefficients",
        "grade_tiers",
    )
    missing = [key for key in required if not rules.get(key)]
    if missing:
        raise ConfigurationError(f"Rules config is incomplete: {missing}")
    rules = _normalize(rules)
    tiers = rules["grade_tiers"]
    # 档位单调性校验：从高到低必须严格递减（允许最低档为 0）
    scores = [item["min_score"] for item in tiers]
    if any(scores[index] <= scores[index + 1] for index in range(len(scores) - 1)):
        raise ConfigurationError("Rules config requires grade_tiers min_score strictly descending")
    return rules


@lru_cache
def get_rules(path: str | None = None) -> dict[str, Any]:
    return load_rules(Path(path) if path else None)

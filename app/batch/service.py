"""批量分析服务。

对多个（或全部）供应商逐个运行单供应商工作流，聚合并按风险高低
排序输出「重点监测名单」，同时产出多供应商同期横向对比数据块
（供后端渲染「横向对比 PDF」，并按角色裁剪下发）。

设计要点：
- 单供应商工作流保持纯净可复用，批量只是外层聚合；
- 有界并发（Settings.batch_max_workers）避免触发 LLM 限流；
- 单个供应商失败不影响整批（记录 error 后继续）；
- 横向对比为**纯 Python 计算**（中位数 / 排名），不产生额外模型调用与 I/O。
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

from app.config import get_settings
from app.data.loader import load_supplier_dataset, normalize_supplier_id
from app.schemas.contracts import RISK_DIMENSIONS
from app.schemas.report import build_risk_report
from app.service import analyze_supplier

logger = logging.getLogger(__name__)

LEVEL_RANK = {"RED": 0, "YELLOW": 1, "GREEN": 2}


def _analyze_one(
    supplier_id: str,
    *,
    current_week: int,
    source_dir: Path | str | None,
    enable_live_llm: bool,
    window_unit: str | None = None,
    window_size: int | None = None,
) -> dict[str, Any]:
    try:
        state = analyze_supplier(
            supplier_id=supplier_id,
            current_week=current_week,
            source_dir=source_dir,
            enable_live_llm=enable_live_llm,
            window_unit=window_unit,
            window_size=window_size,
        )
        report = state.get("risk_report") or build_risk_report(state)
        return {"supplier_id": supplier_id, "status": "OK", "report": report}
    except Exception as exc:  # 单点失败不阻断整批
        logger.warning("batch_supplier_failed supplier=%s error=%s", supplier_id, type(exc).__name__)
        return {"supplier_id": supplier_id, "status": "FAILED", "error": type(exc).__name__}


def build_cross_supplier_comparison(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """基于本批已成功报告计算横向对比数据块（纯计算，无模型调用）。

    产出内容：
    - `level_distribution`：本批风险等级分布（RED/YELLOW/GREEN 家数）；
    - `dimension_baselines`：各维度分值/事件数的中位数基线（O(n·6)）；
    - `rankings`：按分值升序排名，含相对中位数偏差；
    - `median_score`：本批分值中位数。
    """
    if not reports:
        return {
            "supplier_count": 0,
            "level_distribution": {"RED": 0, "YELLOW": 0, "GREEN": 0},
            "dimension_baselines": [],
            "rankings": [],
            "median_score": 0.0,
            "note": "本批次无可用报告，未生成横向对比数据",
        }

    level_distribution = {"RED": 0, "YELLOW": 0, "GREEN": 0}
    scores: list[float] = []
    for report in reports:
        grade = report.get("risk_grade", {}) or {}
        level = str(grade.get("risk_level", "GREEN"))
        level_distribution[level] = level_distribution.get(level, 0) + 1
        scores.append(float(grade.get("score", 0.0)))

    median_score = float(median(scores)) if scores else 0.0

    # 逐维度中位数基线：维度分值取报告 dimension_breakdown，事件数取维度分布
    dim_scores: dict[str, list[float]] = {name: [] for name in RISK_DIMENSIONS}
    dim_counts: dict[str, list[float]] = {name: [] for name in RISK_DIMENSIONS}
    for report in reports:
        grade = report.get("risk_grade", {}) or {}
        breakdown = grade.get("dimension_breakdown", {}) or {}
        counts = {
            item.get("dimension"): item.get("event_count", 0)
            for item in report.get("dimension_breakdown", [])
        }
        for name in RISK_DIMENSIONS:
            dim_scores[name].append(float(breakdown.get(name, 0.0)))
            dim_counts[name].append(float(counts.get(name, 0)))

    dimension_baselines = [
        {
            "dimension": name,
            "median_score": round(float(median(dim_scores[name])), 4) if dim_scores[name] else 0.0,
            "median_event_count": round(float(median(dim_counts[name])), 4) if dim_counts[name] else 0.0,
        }
        for name in RISK_DIMENSIONS
    ]

    ordered = sorted(
        reports,
        key=lambda report: (
            float((report.get("risk_grade", {}) or {}).get("score", 0.0)),
            str(report.get("supplier_id", "")),
        ),
    )
    rankings = [
        {
            "rank": index + 1,
            "supplier_id": str(report.get("supplier_id", "")),
            "supplier_name": str(report.get("supplier_name", "")),
            "importance_tier": (report.get("risk_grade", {}) or {}).get("importance_tier", "一般"),
            "risk_level": (report.get("risk_grade", {}) or {}).get("risk_level", "GREEN"),
            "score": float((report.get("risk_grade", {}) or {}).get("score", 0.0)),
            "deviation": round(
                float((report.get("risk_grade", {}) or {}).get("score", 0.0)) - median_score, 4
            ),
        }
        for index, report in enumerate(ordered)
    ]

    return {
        "supplier_count": len(reports),
        "level_distribution": level_distribution,
        "dimension_baselines": dimension_baselines,
        "rankings": rankings,
        "median_score": round(median_score, 4),
        "note": (
            f"本批共{len(reports)}家供应商；分值中位数{median_score:.2f}；"
            "横向对比数据仅供人工研判，不参与任何自动处置"
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def analyze_batch(
    *,
    supplier_ids: list[str] | None = None,
    current_week: int | None = None,
    source_dir: Path | str | None = None,
    enable_live_llm: bool = False,
    max_workers: int | None = None,
    watchlist_size: int = 10,
    window_unit: str | None = None,
    window_size: int | None = None,
) -> dict[str, Any]:
    """批量分析供应商，返回每家报告 + 重点监测名单 + 横向对比数据块。"""
    settings = get_settings()
    dataset = load_supplier_dataset(str(source_dir) if source_dir else None)
    week = int(current_week or dataset["max_week"])

    targets = list(supplier_ids) if supplier_ids else sorted(dataset["suppliers"].keys())
    if supplier_ids:
        # 兼容外部传入的普通连字符（源数据使用 U+2011），映射回真实 ID
        canonical = {normalize_supplier_id(raw): raw for raw in dataset["suppliers"]}
        resolved = [canonical.get(normalize_supplier_id(item), item) for item in targets]
        unknown = [item for item in resolved if item not in dataset["suppliers"]]
        targets = [item for item in resolved if item in dataset["suppliers"]]
    else:
        unknown = []

    workers = min(max_workers or settings.batch_max_workers, max(len(targets), 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(
            pool.map(
                lambda sid: _analyze_one(
                    sid,
                    current_week=week,
                    source_dir=source_dir,
                    enable_live_llm=enable_live_llm,
                    window_unit=window_unit,
                    window_size=window_size,
                ),
                targets,
            )
        )

    succeeded = [item for item in results if item["status"] == "OK"]
    watchlist_source = sorted(
        succeeded,
        key=lambda item: (
            LEVEL_RANK.get(item["report"]["risk_grade"]["risk_level"], 9),
            -float(item["report"]["risk_grade"]["score"]),
            item["supplier_id"],
        ),
    )
    watchlist = [
        {
            "supplier_id": item["report"]["supplier_id"],
            "supplier_name": item["report"].get("supplier_name", ""),
            "risk_level": item["report"]["risk_grade"]["risk_level"],
            "score": item["report"]["risk_grade"]["score"],
            "importance_tier": item["report"]["risk_grade"].get("importance_tier", "一般"),
            "importance_source": item["report"]["risk_grade"].get("importance_source", "DERIVED"),
            "report_period": item["report"]["risk_grade"].get("report_period", "monthly"),
            "report_period_label": item["report"]["risk_grade"].get("report_period_label", "月报"),
            "trend_type": item["report"]["risk_trend"]["trend_type"],
            "key_dimensions": [factor["dimension"] for factor in item["report"].get("key_factors", [])],
            "recommendation": item["report"].get("recommendation", ""),
        }
        for item in watchlist_source[:watchlist_size]
    ]

    level_counts: dict[str, int] = {"RED": 0, "YELLOW": 0, "GREEN": 0}
    for item in succeeded:
        level = item["report"]["risk_grade"]["risk_level"]
        level_counts[level] = level_counts.get(level, 0) + 1

    # 按供应商重要性分级下发：重要供应商出周报，一般供应商出月报
    period_groups: dict[str, list[str]] = {"weekly": [], "monthly": []}
    for item in succeeded:
        period = item["report"]["risk_grade"].get("report_period", "monthly")
        period_groups.setdefault(period, []).append(item["report"]["supplier_id"])
    report_period_counts = {
        "weekly": len(period_groups.get("weekly", [])),
        "monthly": len(period_groups.get("monthly", [])),
    }

    # 待供应商重新申报的名单（处置门禁产出，供后端通知供应商）
    redeclare_candidates = [
        item["report"]["supplier_id"]
        for item in succeeded
        if (item["report"].get("disposition") or {}).get("can_redeclare")
    ]

    return {
        "current_week": week,
        "analyzed_count": len(succeeded),
        "failed_count": len(results) - len(succeeded),
        "unknown_suppliers": unknown,
        "level_counts": level_counts,
        "report_period_counts": report_period_counts,
        "weekly_report_suppliers": sorted(period_groups.get("weekly", [])),
        "monthly_report_suppliers": sorted(period_groups.get("monthly", [])),
        "redeclare_candidates": sorted(redeclare_candidates),
        "cross_supplier_comparison": build_cross_supplier_comparison(
            [item["report"] for item in succeeded]
        ),
        "watchlist": watchlist,
        "reports": [item["report"] for item in succeeded],
        "failures": [item for item in results if item["status"] != "OK"],
    }

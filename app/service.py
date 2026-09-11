"""单供应商分析服务：组合数据装载、预索引与工作流调用。

这是后端对接的主函数入口（纯函数，无网络副作用），
FastAPI 层与批量服务都复用它。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from app.data.loader import is_in_rectify, load_supplier_dataset, resolve_supplier_id
from app.workflow.graph import build_workflow

_WORKFLOW = None


def _workflow():
    global _WORKFLOW
    if _WORKFLOW is None:
        _WORKFLOW = build_workflow()
    return _WORKFLOW


def analyze_supplier(
    *,
    supplier_id: str,
    current_week: int | None = None,
    source_dir: Path | str | None = None,
    enable_live_llm: bool = False,
    run_id: str | None = None,
    window_unit: str | None = None,
    window_size: int | None = None,
) -> dict[str, Any]:
    """分析单个供应商并返回完整工作流状态（含 risk_report）。

    ``window_unit`` / ``window_size`` 可选：统计窗口单位（week/month）与窗口大小。
    不传时与改造前行为完全一致（默认近 12 周）。
    """
    dataset = load_supplier_dataset(str(source_dir) if source_dir else None)
    # 兼容外部传入的普通连字符（源数据使用 U+2011）
    supplier_id = resolve_supplier_id(supplier_id, str(source_dir) if source_dir else None)
    week = int(current_week or dataset["max_week"])
    events = list(dataset["events_by_supplier"].get(supplier_id, []))
    profile = dict(dataset["profile_by_supplier"].get(supplier_id, {}))
    rectifies = list(dataset["rectifies_by_supplier"].get(supplier_id, []))
    weekly = dataset["weekly_by_supplier"].get(supplier_id, {}).get(week, {})

    # 近期窗口内的事件才进入分析（分析 Agent 会再次按窗口过滤，这里先收窄减少负载）
    visible_events = [event for event in events if int(event["event_week"]) <= week]

    return _workflow().invoke(
        {
            "run_id": run_id or str(uuid4()),
            "supplier_id": supplier_id,
            "current_week": week,
            "window_unit": window_unit,
            "window_size": window_size,
            "events": visible_events,
            "rectifies": rectifies,
            "supplier_profile": profile,
            "business_context": {
                "risk_score": float(weekly.get("risk_score", 0.0)),
                "business_exposure": float(weekly.get("business_exposure", 0.0)),
                "event_count": int(weekly.get("event_count", 0)),
                "has_rectify": is_in_rectify(rectifies, week),
            },
            "enable_live_llm": enable_live_llm,
            "status": "CREATED",
            "errors": [],
            "audit_trace": [],
        }
    )

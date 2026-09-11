"""演示脚本：当前风险分级 + 四 Agent 协同分析 + 结构化报告输出。"""

import csv
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.workflow.graph import build_workflow


def load_mock_events(path: Path = Path("data/mock/risk_events.csv")) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["event_severity"] = int(row["event_severity"])
        row["event_week"] = int(row["event_week"])
    return rows


def run_demo(
    *,
    current_week: int = 10,
    include_events: bool = True,
    enable_live_llm: bool = False,
) -> dict[str, Any]:
    """使用内置 mock 数据运行完整工作流（无需外部数据文件）。"""
    workflow = build_workflow()
    return workflow.invoke(
        {
            "run_id": str(uuid4()),
            "supplier_id": "S-MOCK-01",
            "current_week": current_week,
            "events": load_mock_events() if include_events else [],
            "rectifies": [],
            "supplier_profile": {
                "supplier_id": "S-MOCK-01",
                "supplier_name": "模拟供应商",
                "contract_importance": "重要外包",
                "system_level": "重要",
            },
            "business_context": {"risk_score": 5.0, "business_exposure": 0.80},
            "enable_live_llm": enable_live_llm,
            "status": "CREATED",
            "errors": [],
            "audit_trace": [],
        }
    )


def run_real_data_demo(
    *,
    supplier_id: str | None = None,
    current_week: int | None = None,
    source_dir: Path = Path("data/source"),
    enable_live_llm: bool = False,
) -> dict[str, Any]:
    """使用 data/source 下的真实源数据运行完整工作流。"""
    from app.data.loader import load_supplier_dataset
    from app.service import analyze_supplier

    dataset = load_supplier_dataset(str(source_dir))
    targets = [
        sid
        for sid in dataset["suppliers"]
        if dataset["events_by_supplier"].get(sid)
    ]
    if not targets:
        raise RuntimeError("源数据中没有可分析的供应商")
    selected = supplier_id or sorted(targets)[0]
    return analyze_supplier(
        supplier_id=selected,
        current_week=current_week,
        source_dir=source_dir,
        enable_live_llm=enable_live_llm,
    )

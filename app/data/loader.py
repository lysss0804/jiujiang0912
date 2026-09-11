"""源数据装载与预索引。

本模块只做确定性的读取、分组与派生计算，不涉及 LLM。
批量分析场景下先一次性装载并按 supplier 预分组，避免逐供应商重复扫描（消除 N+1）。
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.common.errors import DataValidationError
from app.config import get_settings

SUPPLIER_FILES = (
    "suppliers.csv",
    "contracts.csv",
    "projects.csv",
    "bank_systems.csv",
    "risk_events.csv",
    "rectify_records.csv",
    "weekly_snapshot.csv",
)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_optional_csv(path: Path) -> list[dict[str, str]]:
    return _read_csv(path) if path.exists() else []


# 源数据里的 ID 使用非断行连字符 U+2011（如 "S‑NOR001"），外部调用方常输入普通连字符 "-"。
# 统一做归一化，避免因不可见字符差异导致查不到供应商。
_HYPHEN_VARIANTS = ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015", "\u2212")
_HYPHEN_TRANSLATION = {ord(char): "-" for char in _HYPHEN_VARIANTS}


def normalize_supplier_id(supplier_id: str) -> str:
    """把各种连字符变体统一成 ASCII 连字符，便于跨来源匹配。"""
    return (supplier_id or "").translate(_HYPHEN_TRANSLATION).strip()


def canonical_supplier_ids(source_dir: str | None = None) -> dict[str, str]:
    """返回 {归一化ID: 原始ID}，用于把外部输入映射回源数据的真实 ID。"""
    dataset = load_supplier_dataset(source_dir)
    return {normalize_supplier_id(raw): raw for raw in dataset["suppliers"]}


def resolve_supplier_id(supplier_id: str, source_dir: str | None = None) -> str:
    """把外部传入的 supplier_id 解析为源数据中的真实 ID；找不到时原样返回。"""
    mapping = canonical_supplier_ids(source_dir)
    return mapping.get(normalize_supplier_id(supplier_id), supplier_id)


@lru_cache(maxsize=4)
def load_supplier_dataset(source_dir: str | None = None) -> dict[str, Any]:
    """一次性装载源数据，返回按供应商预索引的视图。

    返回值：
        suppliers        : {supplier_id: {supplier_id, name}}
        events_by_supplier: {supplier_id: [event, ...]}（按周升序）
        profile_by_supplier: {supplier_id: {contract_importance, importance_level, system_level, project_stage, ...}}
        rectifies_by_supplier: {supplier_id: [(start_week, end_week), ...]}
        weekly_by_supplier: {supplier_id: {week: row}}
        max_week         : 数据中出现的最大周次
    """
    base = Path(source_dir) if source_dir else get_settings().source_data_dir
    for filename in SUPPLIER_FILES:
        if not (base / filename).exists():
            raise DataValidationError(f"Missing source file: {filename}")

    suppliers = {row["supplier_id"]: row for row in _read_csv(base / "suppliers.csv")}
    # 银行名单定义的两级重要性（重要/一般）。列缺失时视为空，自动走 DERIVED 合成回退。
    importance_by_supplier = {
        supplier_id: (row.get("importance_level") or "").strip()
        for supplier_id, row in suppliers.items()
    }
    contracts = _read_csv(base / "contracts.csv")
    projects = _read_csv(base / "projects.csv")
    systems = _read_csv(base / "bank_systems.csv")
    events = _read_csv(base / "risk_events.csv")
    rectifies = _read_optional_csv(base / "rectify_records.csv")
    weekly = _read_csv(base / "weekly_snapshot.csv")

    project_by_id = {row["project_id"]: row for row in projects}
    system_by_project: dict[str, dict[str, str]] = {}
    for row in systems:
        # 同一项目可能挂多个系统，取等级最高者
        rank = {"一般": 1, "重要": 2, "核心": 3}
        current = system_by_project.get(row["project_id"])
        if current is None or rank.get(row["system_level"], 0) > rank.get(current["system_level"], 0):
            system_by_project[row["project_id"]] = row

    profile_by_supplier: dict[str, dict[str, Any]] = {}
    for row in contracts:
        supplier_id = row["supplier_id"]
        project = project_by_id.get(f"PJ-{row['contract_id'].replace('CT-', '', 1)}")
        if project is None:
            project = next((item for item in projects if item["contract_id"] == row["contract_id"]), None)
        system = system_by_project.get(project["project_id"]) if project else None
        profile_by_supplier[supplier_id] = {
            "supplier_id": supplier_id,
            "supplier_name": suppliers.get(supplier_id, {}).get("name", supplier_id),
            "contract_id": row["contract_id"],
            "contract_importance": row.get("contract_importance", ""),
            "importance_level": importance_by_supplier.get(supplier_id, ""),
            "project_stage": project.get("project_stage", "") if project else "",
            "system_level": system.get("system_level", "") if system else "",
            "system_name": system.get("system_name", "") if system else "",
        }

    for supplier_id in suppliers:
        profile_by_supplier.setdefault(
            supplier_id,
            {
                "supplier_id": supplier_id,
                "supplier_name": suppliers[supplier_id].get("name", supplier_id),
                "contract_id": "",
                "contract_importance": "",
                "importance_level": importance_by_supplier.get(supplier_id, ""),
                "project_stage": "",
                "system_level": "",
                "system_name": "",
            },
        )

    events_by_supplier: dict[str, list[dict[str, Any]]] = {supplier_id: [] for supplier_id in suppliers}
    max_week = 0
    for row in events:
        normalized = {
            **row,
            "event_severity": int(row["event_severity"]),
            "event_week": int(row["event_week"]),
        }
        events_by_supplier.setdefault(normalized["supplier_id"], []).append(normalized)
        max_week = max(max_week, normalized["event_week"])
    for rows in events_by_supplier.values():
        rows.sort(key=lambda item: item["event_week"])

    rectifies_by_supplier: dict[str, list[tuple[int, int]]] = {supplier_id: [] for supplier_id in suppliers}
    for row in rectifies:
        rectifies_by_supplier.setdefault(row["supplier_id"], []).append(
            (int(row["start_week"]), int(row["end_week"]))
        )

    weekly_by_supplier: dict[str, dict[int, dict[str, Any]]] = {supplier_id: {} for supplier_id in suppliers}
    for row in weekly:
        week = int(row["week"])
        weekly_by_supplier.setdefault(row["supplier_id"], {})[week] = {
            **row,
            "week": week,
            "event_count": int(row["event_count"]),
            "risk_score": float(row["risk_score"]),
            "has_rectify": str(row["has_rectify"]).lower() == "true",
            "business_exposure": float(row["business_exposure"]),
            "upgrade_label": int(row["upgrade_label"]),
        }
        max_week = max(max_week, week)

    return {
        "suppliers": suppliers,
        "events_by_supplier": events_by_supplier,
        "profile_by_supplier": profile_by_supplier,
        "rectifies_by_supplier": rectifies_by_supplier,
        "weekly_by_supplier": weekly_by_supplier,
        "max_week": max_week,
    }


def is_in_rectify(rectifies: list[tuple[int, int]], current_week: int) -> bool:
    return any(start <= current_week <= end for start, end in rectifies)

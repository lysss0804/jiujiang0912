"""Read-only supplier/contract/project/system relationship projection."""

from __future__ import annotations

from typing import Any

from app.data.loader import canonical_supplier_ids, load_supplier_dataset, normalize_supplier_id


def _normal(value: object) -> str:
    return normalize_supplier_id(str(value or ""))


def supplier_relationships(supplier_id: str) -> dict[str, Any]:
    """Build list and graph views from the currently provided source dataset.

    IDs are normalized to ASCII hyphens at the API boundary so Vue routes and
    database identifiers can be used without invisible-character mismatches.
    Missing source relationships are represented by empty arrays, not invented.
    """
    normalized_id = _normal(supplier_id)
    dataset = load_supplier_dataset()
    canonical_id = canonical_supplier_ids().get(normalized_id)
    supplier_row = dataset["suppliers"].get(canonical_id, {}) if canonical_id else {}

    contract_rows = [
        row for row in dataset.get("contracts", [])
        if _normal(row.get("supplier_id")) == normalized_id
    ]
    contract_ids = {_normal(row.get("contract_id")) for row in contract_rows}
    project_rows = [
        row for row in dataset.get("projects", [])
        if _normal(row.get("contract_id")) in contract_ids
    ]
    project_ids = {_normal(row.get("project_id")) for row in project_rows}
    system_rows = [
        row for row in dataset.get("bank_systems", [])
        if _normal(row.get("project_id")) in project_ids
    ]

    contracts = [
        {
            "contract_id": _normal(row.get("contract_id")),
            "supplier_id": normalized_id,
            "start_week": int(row.get("start_week") or 0),
            "end_week": int(row.get("end_week") or 0),
            "contract_importance": str(row.get("contract_importance") or ""),
        }
        for row in contract_rows
    ]
    projects = [
        {
            "project_id": _normal(row.get("project_id")),
            "project_name": str(row.get("project_name") or _normal(row.get("project_id"))),
            "contract_id": _normal(row.get("contract_id")),
            "project_stage": str(row.get("project_stage") or ""),
        }
        for row in project_rows
    ]
    systems = [
        {
            "system_id": _normal(row.get("system_id")),
            "system_name": str(row.get("system_name") or _normal(row.get("system_id"))),
            "system_level": str(row.get("system_level") or ""),
            "project_id": _normal(row.get("project_id")),
        }
        for row in system_rows
    ]

    nodes: list[dict[str, Any]] = [
        {
            "id": normalized_id,
            "label": str(supplier_row.get("name") or supplier_row.get("supplier_name") or normalized_id),
            "type": "SUPPLIER",
            "category": 0,
        }
    ]
    nodes.extend(
        {"id": item["contract_id"], "label": item["contract_id"], "type": "CONTRACT", "category": 1,
         "attributes": {"importance": item["contract_importance"], "start_week": item["start_week"], "end_week": item["end_week"]}}
        for item in contracts
    )
    nodes.extend(
        {"id": item["project_id"], "label": item["project_name"], "type": "PROJECT", "category": 2,
         "attributes": {"stage": item["project_stage"]}}
        for item in projects
    )
    nodes.extend(
        {"id": item["system_id"], "label": item["system_name"], "type": "SYSTEM", "category": 3,
         "attributes": {"level": item["system_level"]}}
        for item in systems
    )

    edges: list[dict[str, str]] = []
    edges.extend(
        {"source": normalized_id, "target": item["contract_id"], "relationship": "HAS_CONTRACT"}
        for item in contracts
    )
    edges.extend(
        {"source": item["contract_id"], "target": item["project_id"], "relationship": "INCLUDES_PROJECT"}
        for item in projects
    )
    edges.extend(
        {"source": item["project_id"], "target": item["system_id"], "relationship": "SUPPORTS_SYSTEM"}
        for item in systems
    )

    return {
        "supplier": {
            "supplier_id": normalized_id,
            "supplier_name": str(supplier_row.get("name") or supplier_row.get("supplier_name") or normalized_id),
            "importance": str(supplier_row.get("importance_level") or supplier_row.get("importance") or ""),
        },
        "contracts": contracts,
        "projects": projects,
        "systems": systems,
        "graph": {
            "categories": ["供应商", "合同", "项目", "系统"],
            "nodes": nodes,
            "edges": edges,
        },
        "data_status": "DATA" if contracts or projects or systems else "NO_DATA",
        "source": "PROVIDED_DATA",
    }

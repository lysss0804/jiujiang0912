"""Explicit, development-only data for frontend contract integration."""

from __future__ import annotations

from typing import Any

from app.backend.repository import RiskRepository


DEMO_SUPPLIERS = [
    {"supplier_id": "DEMO-IMPORTANT", "supplier_name": "演示重要供应商", "credit_code": "91360000DEMO000001", "importance": "IMPORTANT", "responsible_department": "信息科技部"},
    {"supplier_id": "DEMO-GENERAL", "supplier_name": "演示一般供应商", "credit_code": "91360000DEMO000002", "importance": "GENERAL", "responsible_department": "运营管理部"},
]

DEMO_EVENTS = [
    {"supplier_id": "DEMO-IMPORTANT", "risk_dimension": "司法", "risk_type": "合同诉讼", "risk_description": "演示数据：供应商存在合同纠纷案件", "severity": 4, "event_week": 36, "source_type": "MOCK", "source_record_id": "MOCK-JUDICIAL-001", "verified": True},
    {"supplier_id": "DEMO-IMPORTANT", "risk_dimension": "经营风险", "risk_type": "行政处罚", "risk_description": "演示数据：供应商收到行政处罚", "severity": 3, "event_week": 35, "source_type": "MOCK", "source_record_id": "MOCK-OPERATION-001", "verified": True},
    {"supplier_id": "DEMO-GENERAL", "risk_dimension": "知识产权", "risk_type": "商标状态变化", "risk_description": "演示数据：一项商标状态发生变化", "severity": 1, "event_week": 34, "source_type": "MOCK", "source_record_id": "MOCK-IP-001", "verified": True},
]


def bootstrap_frontend_demo(repository: RiskRepository) -> dict[str, Any]:
    repository.import_suppliers(DEMO_SUPPLIERS, configured_by="mock-bootstrap")
    for supplier in DEMO_SUPPLIERS:
        if not repository.list_risk_events(supplier["supplier_id"], status=None):
            repository.import_risk_events(
                [item for item in DEMO_EVENTS if item["supplier_id"] == supplier["supplier_id"]],
                source_type="MOCK",
            )

    manager = repository.upsert_role(
        {
            "role_code": "DEMO_MANAGER",
            "role_name": "演示风险经理",
            "level": 40,
            "permissions": ["supplier.read", "risk_event.read", "monitor.run", "report.read", "report.evidence.read", "review.submit", "file.read"],
        }
    )
    leadership = repository.upsert_role(
        {
            "role_code": "DEMO_LEADERSHIP",
            "role_name": "演示管理层",
            "level": 80,
            "permissions": ["supplier.read", "risk_event.read", "report.read", "report.evidence.read", "report.comparison.read", "report.export", "file.read"],
        }
    )
    users = [
        {"user_id": "demo-admin", "username": "demo_admin", "display_name": "演示管理员", "role_ids": ["system-admin"], "supplier_ids": []},
        {"user_id": "demo-manager", "username": "demo_manager", "display_name": "演示风险经理", "role_ids": [manager["role_id"]], "supplier_ids": ["DEMO-IMPORTANT"]},
        {"user_id": "demo-leadership", "username": "demo_leadership", "display_name": "演示管理层", "role_ids": [leadership["role_id"]], "supplier_ids": []},
    ]
    for user in users:
        repository.upsert_user(user)
        repository.set_user_roles(user["user_id"], user["role_ids"])
        repository.set_user_supplier_scope(user["user_id"], user["supplier_ids"])
    return {
        "mock": True,
        "supplier_ids": [item["supplier_id"] for item in DEMO_SUPPLIERS],
        "users": [{"user_id": item["user_id"], "username": item["username"], "display_name": item["display_name"]} for item in users],
    }


def mock_login(repository: RiskRepository, username: str) -> dict[str, Any]:
    match = next((item for item in repository.list_users() if item["username"] == username and item["status"] == "ACTIVE"), None)
    if match is None:
        raise ValueError("unknown mock username; run /backend/mock/bootstrap first")
    profile = repository.access_profile(match["user_id"])
    return {
        "mock": True,
        "access_token": f"mock::{match['user_id']}",
        "token_type": "mock",
        "request_headers": {"X-User-Id": match["user_id"]},
        "profile": profile,
    }

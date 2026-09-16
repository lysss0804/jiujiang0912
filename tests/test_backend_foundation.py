from __future__ import annotations

from datetime import datetime, timezone

from app.backend.repository import RiskRepository
from app.service import analyze_supplier_snapshot


def _repo(tmp_path) -> RiskRepository:
    return RiskRepository(f"sqlite:///{(tmp_path / 'backend.db').as_posix()}")


def test_import_assigns_default_policies_and_custom_override(tmp_path) -> None:
    repo = _repo(tmp_path)
    repo.import_suppliers(
        [
            {"supplier_id": "SUP-IMPORTANT", "supplier_name": "重要供应商", "credit_code": "913600000000000001", "importance": "重要"},
            {"supplier_id": "SUP-GENERAL", "supplier_name": "一般供应商", "credit_code": "913600000000000002", "importance": "GENERAL"},
        ]
    )
    repo.set_monitor_override(
        "SUP-GENERAL",
        frequency="CUSTOM",
        interval_days=10,
        next_due_at=datetime.now(timezone.utc),
    )

    tasks = repo.dispatch_due_monitoring(now=datetime.now(timezone.utc))
    frequencies = {item["supplier_id"]: item["frequency"] for item in tasks}
    assert frequencies == {"SUP-IMPORTANT": "WEEKLY", "SUP-GENERAL": "CUSTOM"}


def test_database_snapshot_runs_agent_and_persists_result(tmp_path) -> None:
    repo = _repo(tmp_path)
    repo.import_suppliers(
        [{"supplier_id": "SUP-RED", "supplier_name": "红色供应商", "credit_code": "913600000000000003", "importance": "IMPORTANT"}]
    )
    repo.import_risk_events(
        [{"supplier_id": "SUP-RED", "risk_dimension": "经营风险", "risk_type": "重大数据泄露", "severity": 4, "event_week": 36, "evidence_id": "E-SUP-RED-1"}]
    )
    monitor_id = repo.dispatch_due_monitoring(now=datetime.now(timezone.utc))[0]["monitor_id"]
    task, snapshot = repo.snapshot_for_monitor(monitor_id)
    analysis_run_id, run_id = repo.create_analysis_run(monitor_id, snapshot)

    state = analyze_supplier_snapshot(
        supplier_id=task["supplier_id"], snapshot=snapshot, current_week=36, run_id=run_id, enable_live_llm=False
    )
    persisted = repo.persist_analysis(analysis_run_id, state)

    assert state["risk_report"]["risk_grade"]["risk_level"] == "RED"
    assert persisted["report_id"]
    assert repo.list_monitor_tasks()[0]["status"] == "SUCCESS"


def test_custom_role_uses_selected_permissions_and_supplier_scope(tmp_path) -> None:
    repo = _repo(tmp_path)
    repo.import_suppliers(
        [{"supplier_id": "SUP-SCOPE", "supplier_name": "scope supplier", "credit_code": "913600000000000004", "importance": "GENERAL"}]
    )
    role = repo.upsert_role(
        {"role_code": "RISK_VIEWER", "role_name": "risk viewer", "level": 30, "permissions": ["report.read"]}
    )
    repo.upsert_user({"user_id": "user-scope", "username": "scope", "display_name": "scope user"})
    repo.set_user_roles("user-scope", [role["role_id"]])
    repo.set_user_supplier_scope("user-scope", ["SUP-SCOPE"])

    assert repo.authorize("user-scope", "report.read", "SUP-SCOPE")["max_level"] == 30
    try:
        repo.authorize("user-scope", "source.manage")
    except ValueError as exc:
        assert "missing permission" in str(exc)
    else:
        raise AssertionError("unselected permission unexpectedly allowed")

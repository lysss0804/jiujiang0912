from __future__ import annotations

from datetime import datetime, timezone

from app.backend.repository import RiskRepository
from app.backend.api import _provided_data_snapshot
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
    summary = repo.dashboard_summary()
    assert summary["supplier_count_by_risk"] == {"RED": 1, "YELLOW": 0, "GREEN": 0}
    assert summary["supplier_analysis_coverage"] == {"analyzed": 1, "total": 1, "pending": 0}
    assert summary["watchlist"][0]["current_risk_score"] > 0


def test_dashboard_prefers_latest_result_over_stale_supplier_cache(tmp_path) -> None:
    repo = _repo(tmp_path)
    repo.import_suppliers(
        [{"supplier_id": "SUP-STALE", "supplier_name": "缓存过期供应商", "credit_code": "913600000000000005", "importance": "GENERAL"}]
    )
    repo.import_risk_events(
        [{"supplier_id": "SUP-STALE", "risk_dimension": "司法", "risk_type": "重大诉讼", "severity": 5, "event_week": 36}]
    )
    monitor_id = repo.create_manual_monitoring_tasks()[0]["monitor_id"]
    task, snapshot = repo.snapshot_for_monitor(monitor_id)
    analysis_run_id, run_id = repo.create_analysis_run(monitor_id, snapshot)
    state = analyze_supplier_snapshot(
        supplier_id=task["supplier_id"], snapshot=snapshot, current_week=36, run_id=run_id, enable_live_llm=False
    )
    repo.persist_analysis(analysis_run_id, state)

    # Simulate a stale/incorrect cache. Dashboard must still use risk_result.
    from app.backend.database import connect

    with connect(repo.database_url) as db:
        db.execute("UPDATE supplier SET current_risk_level='GREEN' WHERE supplier_id='SUP-STALE'")

    summary = repo.dashboard_summary()
    expected_level = state["risk_report"]["risk_grade"]["risk_level"]
    assert expected_level in {"RED", "YELLOW"}
    assert summary["supplier_count_by_risk"][expected_level] == 1
    assert summary["supplier_count_by_risk"]["GREEN"] == 0
    assert summary["watchlist"][0]["risk_state_source"] == "LATEST_RISK_RESULT"


def test_manual_refresh_tasks_ignore_periodic_due_date(tmp_path) -> None:
    repo = _repo(tmp_path)
    repo.import_suppliers(
        [{"supplier_id": "SUP-MANUAL", "supplier_name": "手工刷新供应商", "credit_code": "913600000000000006", "importance": "GENERAL"}]
    )
    # Normal dispatch advances the periodic schedule into the future.
    repo.dispatch_due_monitoring(now=datetime.now(timezone.utc))
    manual = repo.create_manual_monitoring_tasks(supplier_ids=["SUP-MANUAL"])
    assert len(manual) == 1
    assert manual[0]["monitor_type"] == "MANUAL"


def test_provided_data_snapshot_supports_imported_fixture_supplier() -> None:
    snapshot = _provided_data_snapshot("S-ACC134")
    assert snapshot is not None
    assert snapshot["supplier_profile"]["supplier_name"] != "S-ACC134"
    assert snapshot["supplier_profile"]["importance_level"] in {"重要", "一般"}
    assert snapshot["events"]
    state = analyze_supplier_snapshot(
        supplier_id="S-ACC134",
        snapshot=snapshot,
        current_week=snapshot["max_week"],
        enable_live_llm=False,
    )
    assert state["risk_report"]["risk_grade"]["risk_level"] == "RED"


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

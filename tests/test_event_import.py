from __future__ import annotations

from pathlib import Path

import pytest

from app.backend.database import connect
from app.backend.imports import validate_event_csv
from app.backend.repository import RiskRepository


def test_legacy_source_csv_maps_all_589_rows_to_six_dimensions() -> None:
    content = Path("data/source/risk_events.csv").read_bytes()
    valid, errors = validate_event_csv(content)

    assert errors == []
    assert len(valid) == 589
    assert {row["risk_dimension"] for row in valid} == {
        "公司背景", "司法", "失信", "经营风险", "经营状况", "知识产权"
    }
    assert next(row for row in valid if row["event_category"] == "履约")["risk_dimension"] == "经营状况"
    assert next(row for row in valid if row["event_category"] == "人员")["risk_dimension"] == "经营状况"


def test_repository_accepts_legacy_event_fields(tmp_path) -> None:
    repository = RiskRepository(f"sqlite:///{(tmp_path / 'backend.db').as_posix()}")
    repository.import_suppliers(
        [{"supplier_id": "SUP-1", "supplier_name": "测试", "credit_code": "SOURCE-1", "importance": "一般"}]
    )
    result = repository.import_risk_events(
        [{
            "supplier_id": "SUP-1", "event_category": "人员", "event_subtype": "核心人员离职",
            "event_severity": 2, "event_week": 40, "evidence_id": "E-1", "source_type": "人力台账",
        }],
        source_type="CSV_SOURCE_DATA",
    )

    assert result["imported_count"] == 1
    with connect(repository.database_url) as db:
        row = db.execute("SELECT risk_dimension, risk_type, severity FROM risk_event").fetchone()
    assert dict(row) == {"risk_dimension": "经营状况", "risk_type": "核心人员离职", "severity": 2}


def test_repository_rejects_invalid_explicit_dimension(tmp_path) -> None:
    repository = RiskRepository(f"sqlite:///{(tmp_path / 'backend.db').as_posix()}")
    repository.import_suppliers(
        [{"supplier_id": "SUP-1", "supplier_name": "测试", "credit_code": "SOURCE-1", "importance": "一般"}]
    )
    with pytest.raises(ValueError, match="invalid risk_dimension"):
        repository.import_risk_events(
            [{
                "supplier_id": "SUP-1", "risk_dimension": "未知维度", "risk_type": "测试",
                "severity": 1, "event_week": 1,
            }]
        )

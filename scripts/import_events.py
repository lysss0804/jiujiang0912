"""Import the provided supplier list and legacy risk event CSV into SQLite.

Examples:
    python scripts/import_events.py
    python scripts/import_events.py --replace-all-events
    python scripts/import_events.py --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.backend.database import connect, sqlite_path  # noqa: E402
from app.backend.imports import validate_event_csv  # noqa: E402
from app.backend.repository import RiskRepository, normalize_supplier_id  # noqa: E402
from app.config import get_settings  # noqa: E402


def _read_csv(path: Path) -> bytes:
    if not path.is_file():
        raise FileNotFoundError(f"CSV not found: {path}")
    return path.read_bytes()


def _missing_suppliers(repository: RiskRepository, supplier_csv: Path) -> list[dict[str, str]]:
    import csv
    from io import StringIO

    text = _read_csv(supplier_csv).decode("utf-8-sig")
    rows = list(csv.DictReader(StringIO(text)))
    with connect(repository.database_url) as db:
        known = {row["supplier_id"] for row in db.execute("SELECT supplier_id FROM supplier")}
    records: list[dict[str, str]] = []
    for row in rows:
        supplier_id = normalize_supplier_id(row.get("supplier_id", ""))
        if not supplier_id or supplier_id in known:
            continue
        name = (row.get("supplier_name") or row.get("name") or supplier_id).strip()
        importance = (row.get("importance") or row.get("importance_level") or "一般").strip()
        # 源数据没有统一社会信用代码。使用带 SOURCE-DATA 前缀的稳定占位值，
        # 避免把缺失字段伪装成真实工商信息，后续官方数据可覆盖。
        placeholder = hashlib.sha256(supplier_id.encode("utf-8")).hexdigest()[:20].upper()
        records.append(
            {
                "supplier_id": supplier_id,
                "supplier_name": name,
                "credit_code": f"SOURCE-DATA-{placeholder}",
                "importance": importance,
                "responsible_department": "待补充",
            }
        )
    return records


def _replace_all_event_data(repository: RiskRepository) -> dict[str, int]:
    """Delete event/evidence imports only; analysis cleanup is a separate script."""
    tables = ("risk_event_evidence", "risk_event", "evidence", "ingestion_batch")
    with connect(repository.database_url) as db:
        before = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}
        db.execute("BEGIN")
        try:
            for table in tables:
                db.execute(f"DELETE FROM {table}")
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    return before


def _replace_matching_event_data(repository: RiskRepository, records: list[dict[str, object]]) -> dict[str, int]:
    """Remove prior copies identified by stable evidence IDs, making reruns idempotent."""
    evidence_ids = sorted({str(row.get("evidence_id") or "").strip() for row in records} - {""})
    removed = {"risk_event": 0, "evidence": 0}
    if not evidence_ids:
        return removed
    with connect(repository.database_url) as db:
        db.execute("BEGIN")
        try:
            for start in range(0, len(evidence_ids), 500):
                chunk = evidence_ids[start : start + 500]
                placeholders = ",".join("?" for _ in chunk)
                event_ids = [
                    row["risk_event_id"]
                    for row in db.execute(
                        f"SELECT risk_event_id FROM risk_event_evidence WHERE evidence_id IN ({placeholders})", chunk
                    )
                ]
                if event_ids:
                    event_placeholders = ",".join("?" for _ in event_ids)
                    db.execute(f"DELETE FROM risk_event_evidence WHERE risk_event_id IN ({event_placeholders})", event_ids)
                    removed["risk_event"] += db.execute(
                        f"DELETE FROM risk_event WHERE risk_event_id IN ({event_placeholders})", event_ids
                    ).rowcount
                removed["evidence"] += db.execute(
                    f"DELETE FROM evidence WHERE evidence_id IN ({placeholders})", chunk
                ).rowcount
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Import source risk_events.csv into the backend database")
    parser.add_argument("--csv", type=Path, default=ROOT / "data/source/risk_events.csv")
    parser.add_argument("--suppliers-csv", type=Path, default=ROOT / "data/source/suppliers.csv")
    parser.add_argument("--database-url", default=get_settings().database_url)
    parser.add_argument("--replace-all-events", action="store_true", help="clear all risk events/evidence before import")
    parser.add_argument("--dry-run", action="store_true", help="validate and print counts without changing the database")
    args = parser.parse_args()

    content = _read_csv(args.csv.resolve())
    valid, errors = validate_event_csv(content)
    if errors:
        print(json.dumps({"valid_count": len(valid), "error_count": len(errors), "errors": errors[:50]}, ensure_ascii=False, indent=2))
        return 2

    dimensions: dict[str, int] = {}
    for row in valid:
        dimension = str(row["risk_dimension"])
        dimensions[dimension] = dimensions.get(dimension, 0) + 1
    if args.dry_run:
        print(json.dumps({"valid_count": len(valid), "error_count": 0, "dimensions": dimensions}, ensure_ascii=False, indent=2))
        return 0

    repository = RiskRepository(args.database_url)
    # Resolve once before mutation, and print the exact local target for auditability.
    database_path = sqlite_path(args.database_url).resolve()
    missing = _missing_suppliers(repository, args.suppliers_csv.resolve())
    supplier_result = repository.import_suppliers(missing, configured_by="scripts/import_events.py") if missing else {"imported_count": 0}
    removed = (
        _replace_all_event_data(repository)
        if args.replace_all_events
        else _replace_matching_event_data(repository, valid)
    )
    result = repository.import_risk_events(valid, source_type="CSV_SOURCE_DATA")
    with connect(repository.database_url) as db:
        database_event_count = db.execute("SELECT COUNT(*) FROM risk_event").fetchone()[0]
        imported_event_count = db.execute(
            "SELECT COUNT(*) FROM risk_event WHERE ingestion_batch_id=?", (result["ingestion_batch_id"],)
        ).fetchone()[0]
    print(
        json.dumps(
            {
                "database_path": str(database_path),
                "supplier_imported_count": supplier_result["imported_count"],
                "removed_before_import": removed,
                "imported_event_count": imported_event_count,
                "database_event_count": database_event_count,
                "dimensions": dimensions,
                "ingestion_batch_id": result["ingestion_batch_id"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

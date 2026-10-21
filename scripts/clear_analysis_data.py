"""Safely clear generated monitoring/analysis data while preserving source data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.backend.database import connect, initialize, sqlite_path  # noqa: E402
from app.config import get_settings  # noqa: E402


COUNT_TABLES = (
    "notification",
    "human_review_record",
    "redeclare_request",
    "risk_report",
    "risk_result_rule",
    "agent_step_result",
    "risk_result",
    "analysis_run",
    "monitor_task",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Clear monitor tasks and generated analysis/report rows")
    parser.add_argument("--database-url", default=get_settings().database_url)
    parser.add_argument("--yes", action="store_true", help="confirm the destructive cleanup")
    args = parser.parse_args()
    if not args.yes:
        parser.error("refusing to modify the database without --yes")

    initialize(args.database_url)
    with connect(args.database_url) as db:
        before = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in COUNT_TABLES}
        db.execute("BEGIN")
        try:
            # Preserve unrelated notifications and supplier redeclaration requests.
            # Only their references to reports being removed need adjustment.
            db.execute("DELETE FROM notification WHERE report_id IS NOT NULL")
            db.execute("DELETE FROM human_review_record")
            db.execute("UPDATE redeclare_request SET report_id=NULL, updated_at=CURRENT_TIMESTAMP WHERE report_id IS NOT NULL")
            for table in (
                "risk_report", "risk_result_rule", "agent_step_result", "risk_result", "analysis_run", "monitor_task"
            ):
                db.execute(f"DELETE FROM {table}")
            db.execute("UPDATE supplier SET current_risk_level='GREEN', updated_at=CURRENT_TIMESTAMP")
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        after = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in COUNT_TABLES}
    print(json.dumps({"database_path": str(sqlite_path(args.database_url).resolve()), "before": before, "after": after}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

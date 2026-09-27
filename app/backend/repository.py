"""数据库仓储：供应商/风险事件导入、监控任务与智能体结果持久化。"""

from __future__ import annotations

import json
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.backend.database import connect, initialize


DIMENSIONS = {"公司背景", "司法", "失信", "经营风险", "经营状况", "知识产权"}
IMPORTANCE = {"IMPORTANT": "IMPORTANT", "GENERAL": "GENERAL", "重要": "IMPORTANT", "一般": "GENERAL"}
DEFAULT_PERMISSIONS = (
    ("supplier.read", "查看供应商", "SUPPLIER"), ("supplier.write", "维护供应商", "SUPPLIER"),
    ("risk_event.read", "查看风险事件", "RISK_EVENT"), ("risk_event.import", "导入风险事件", "RISK_EVENT"),
    ("monitor.manage", "配置监控周期", "MONITOR"), ("monitor.run", "执行监控分析", "MONITOR"),
    ("source.manage", "配置官方数据源", "SOURCE"), ("source.sync", "同步官方数据源", "SOURCE"),
    ("report.read", "查看风险报告", "REPORT"), ("report.comparison.read", "查看横向供应商对比", "REPORT"),
    ("report.evidence.read", "查看证据明细", "REPORT"), ("report.export", "导出风险报告", "REPORT"),
    ("review.submit", "提交人工复核", "REVIEW"), ("review.decide", "确认复核结论", "REVIEW"),
    ("notification.manage", "配置通知规则", "NOTIFICATION"), ("user.manage", "维护用户", "RBAC"),
    ("role.manage", "维护角色与权限", "RBAC"), ("audit.read", "查看审计日志", "AUDIT"),
    ("file.read", "下载文件", "FILE"), ("file.convert", "转换文档", "FILE"),
)
_HYPHEN_VARIANTS = ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015", "\u2212")
_HYPHEN_TRANSLATION = {ord(char): "-" for char in _HYPHEN_VARIANTS}


def normalize_supplier_id(supplier_id: str) -> str:
    """后端入库 ID 归一化，不依赖 CSV/算法数据装载模块。"""
    return (supplier_id or "").translate(_HYPHEN_TRANSLATION).strip()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _new_id() -> str:
    return str(uuid4())


class RiskRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        initialize(database_url)
        self._ensure_default_policies()
        self._ensure_default_rbac()

    def _ensure_default_policies(self) -> None:
        rows = (
            ("default-important-weekly", "重要供应商默认周度监控", "IMPORTANT", "WEEKLY"),
            ("default-general-monthly", "一般供应商默认月度监控", "GENERAL", "MONTHLY"),
        )
        with connect(self.database_url) as db:
            db.execute("BEGIN")
            for policy_id, name, level, frequency in rows:
                db.execute(
                    """INSERT INTO monitor_policy(policy_id, policy_name, supplier_level, frequency, is_system_default)
                       VALUES (?, ?, ?, ?, 1)
                       ON CONFLICT(supplier_level, policy_name) DO NOTHING""",
                    (policy_id, name, level, frequency),
                )
            db.execute("COMMIT")

    def _ensure_default_rbac(self) -> None:
        now = _iso(_now())
        with connect(self.database_url) as db:
            db.execute("BEGIN")
            try:
                for code, name, resource in DEFAULT_PERMISSIONS:
                    db.execute("INSERT INTO permission(permission_code, permission_name, resource_type) VALUES (?, ?, ?) ON CONFLICT(permission_code) DO NOTHING", (code, name, resource))
                db.execute("""INSERT INTO app_role(role_id, role_code, role_name, level, is_system, enabled, created_at, updated_at)
                              VALUES ('system-admin', 'SYSTEM_ADMIN', '系统管理员', 100, 1, 1, ?, ?)
                              ON CONFLICT(role_code) DO NOTHING""", (now, now))
                for code, _, _ in DEFAULT_PERMISSIONS:
                    db.execute("INSERT INTO role_permission(role_id, permission_code) VALUES ('system-admin', ?) ON CONFLICT(role_id, permission_code) DO NOTHING", (code,))
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

    def import_suppliers(self, records: list[dict[str, Any]], *, configured_by: str | None = None) -> dict[str, Any]:
        if not records:
            raise ValueError("supplier records must not be empty")
        accepted: list[str] = []
        now = _now()
        with connect(self.database_url) as db:
            db.execute("BEGIN")
            try:
                for record in records:
                    supplier_id = normalize_supplier_id(str(record.get("supplier_id", "")))
                    name = str(record.get("supplier_name") or record.get("name") or "").strip()
                    credit_code = str(record.get("credit_code", "")).strip()
                    importance = IMPORTANCE.get(str(record.get("importance", "")).strip().upper()) or IMPORTANCE.get(str(record.get("importance", "")).strip())
                    if not supplier_id or not name or not credit_code or not importance:
                        raise ValueError("supplier_id, supplier_name, credit_code and importance are required")
                    db.execute(
                        """INSERT INTO supplier(supplier_id, supplier_name, credit_code, importance, responsible_department, status, updated_at)
                           VALUES (?, ?, ?, ?, ?, COALESCE(?, 'ACTIVE'), ?)
                           ON CONFLICT(supplier_id) DO UPDATE SET supplier_name=excluded.supplier_name,
                             credit_code=excluded.credit_code, importance=excluded.importance,
                             responsible_department=excluded.responsible_department, status=excluded.status, updated_at=excluded.updated_at""",
                        (supplier_id, name, credit_code, importance, record.get("responsible_department"), record.get("status"), _iso(now)),
                    )
                    policy_id = "default-important-weekly" if importance == "IMPORTANT" else "default-general-monthly"
                    assignment_id = f"assignment::{supplier_id}::{policy_id}"
                    db.execute(
                        """INSERT INTO supplier_monitor_assignment(assignment_id, supplier_id, policy_id, next_due_at, configured_by)
                           VALUES (?, ?, ?, ?, ?)
                           ON CONFLICT(supplier_id, policy_id) DO NOTHING""",
                        (assignment_id, supplier_id, policy_id, _iso(now), configured_by),
                    )
                    accepted.append(supplier_id)
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        return {"imported_count": len(accepted), "supplier_ids": accepted}

    def import_risk_events(self, records: list[dict[str, Any]], *, source_type: str = "MANUAL") -> dict[str, Any]:
        if not records:
            raise ValueError("risk event records must not be empty")
        batch_id, data_version = _new_id(), f"import-{_now().strftime('%Y%m%d%H%M%S')}"
        event_ids: list[str] = []
        with connect(self.database_url) as db:
            db.execute("BEGIN")
            try:
                db.execute(
                    "INSERT INTO ingestion_batch(ingestion_batch_id, source_type, data_version, status, total_count) VALUES (?, ?, ?, 'RUNNING', ?)",
                    (batch_id, source_type, data_version, len(records)),
                )
                for record in records:
                    supplier_id = normalize_supplier_id(str(record.get("supplier_id", "")))
                    if not db.execute("SELECT 1 FROM supplier WHERE supplier_id = ?", (supplier_id,)).fetchone():
                        raise ValueError(f"unknown supplier_id: {supplier_id}")
                    dimension = str(record.get("risk_dimension") or record.get("event_category") or "").strip()
                    if dimension not in DIMENSIONS:
                        raise ValueError(f"invalid risk_dimension: {dimension}")
                    severity, week = int(record.get("severity", record.get("event_severity", -1))), int(record.get("event_week", -1))
                    risk_type = str(record.get("risk_type") or record.get("event_subtype") or "").strip()
                    description = str(record.get("risk_description") or record.get("description") or risk_type).strip()
                    if severity not in range(6) or not 1 <= week <= 52 or not risk_type or not description:
                        raise ValueError("risk_type, description, severity(0-5) and event_week(1-52) are required")
                    evidence_id = str(record.get("evidence_id") or _new_id())
                    event_id = _new_id()
                    db.execute(
                        """INSERT INTO evidence(evidence_id, supplier_id, evidence_type, source_type, source_record_id, title, content_summary, source_url, event_week, verified)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(evidence_id) DO NOTHING""",
                        (evidence_id, supplier_id, record.get("evidence_type", "RISK_SOURCE"), record.get("source_type", source_type), record.get("source_record_id"), record.get("evidence_title", risk_type), record.get("evidence_content", description), record.get("source_url"), week, int(bool(record.get("verified", True)))),
                    )
                    db.execute(
                        """INSERT INTO risk_event(risk_event_id, supplier_id, ingestion_batch_id, risk_dimension, risk_type, severity, risk_description, amount, event_week, source_type, source_record_id)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (event_id, supplier_id, batch_id, dimension, risk_type, severity, description, record.get("amount"), week, record.get("source_type", source_type), record.get("source_record_id")),
                    )
                    db.execute("INSERT INTO risk_event_evidence(risk_event_id, evidence_id) VALUES (?, ?)", (event_id, evidence_id))
                    event_ids.append(event_id)
                db.execute("UPDATE ingestion_batch SET status='SUCCESS', success_count=?, completed_at=? WHERE ingestion_batch_id=?", (len(event_ids), _iso(_now()), batch_id))
                db.execute("COMMIT")
            except Exception as exc:
                db.execute("ROLLBACK")
                raise ValueError(f"risk event import failed: {exc}") from exc
        return {"ingestion_batch_id": batch_id, "data_version": data_version, "imported_count": len(event_ids), "risk_event_ids": event_ids}

    def set_monitor_override(self, supplier_id: str, *, frequency: str, next_due_at: datetime, interval_days: int | None = None, configured_by: str | None = None) -> dict[str, Any]:
        supplier_id = normalize_supplier_id(supplier_id)
        frequency = frequency.upper()
        if frequency not in {"WEEKLY", "MONTHLY", "QUARTERLY", "CUSTOM"}:
            raise ValueError("frequency must be WEEKLY, MONTHLY, QUARTERLY or CUSTOM")
        if frequency == "CUSTOM" and not interval_days:
            raise ValueError("CUSTOM frequency requires interval_days")
        with connect(self.database_url) as db:
            row = db.execute("SELECT importance FROM supplier WHERE supplier_id=?", (supplier_id,)).fetchone()
            if row is None:
                raise ValueError(f"unknown supplier_id: {supplier_id}")
            policy_id = "default-important-weekly" if row["importance"] == "IMPORTANT" else "default-general-monthly"
            db.execute(
                """UPDATE supplier_monitor_assignment SET frequency_override=?, interval_days_override=?, next_due_at=?, configured_by=?, updated_at=?
                   WHERE supplier_id=? AND policy_id=?""",
                (frequency, interval_days, _iso(next_due_at), configured_by, _iso(_now()), supplier_id, policy_id),
            )
        return {"supplier_id": supplier_id, "frequency": frequency, "interval_days": interval_days, "next_due_at": _iso(next_due_at)}

    def dispatch_due_monitoring(self, *, now: datetime | None = None, limit: int = 100) -> list[dict[str, Any]]:
        now = now or _now()
        created: list[dict[str, Any]] = []
        with connect(self.database_url) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                """SELECT a.*, p.frequency AS policy_frequency, p.interval_days AS policy_interval FROM supplier_monitor_assignment a
                   JOIN monitor_policy p ON p.policy_id=a.policy_id
                   WHERE a.enabled=1 AND p.enabled=1 AND a.next_due_at<=? ORDER BY a.next_due_at LIMIT ?""",
                (_iso(now), limit),
            ).fetchall()
            for row in rows:
                frequency = row["frequency_override"] or row["policy_frequency"]
                interval = row["interval_days_override"] or row["policy_interval"]
                days = {"WEEKLY": 7, "MONTHLY": 30, "QUARTERLY": 90}.get(frequency, interval)
                if not days:
                    raise ValueError(f"no interval configured for assignment {row['assignment_id']}")
                monitor_id = _new_id()
                db.execute(
                    """INSERT INTO monitor_task(monitor_id, supplier_id, assignment_id, monitor_type, status, scheduled_at, data_version)
                       VALUES (?, ?, ?, 'PERIODIC', 'PENDING', ?, ?)""",
                    (monitor_id, row["supplier_id"], row["assignment_id"], _iso(now), f"monitor-{now.strftime('%Y%m%d%H%M%S')}")
                )
                db.execute("UPDATE supplier_monitor_assignment SET next_due_at=?, updated_at=? WHERE assignment_id=?", (_iso(now + timedelta(days=int(days))), _iso(now), row["assignment_id"]))
                created.append({"monitor_id": monitor_id, "supplier_id": row["supplier_id"], "frequency": frequency})
            db.execute("COMMIT")
        return created

    def create_manual_monitoring_tasks(
        self,
        *,
        supplier_ids: list[str] | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Create one-off analysis tasks without changing periodic schedules.

        This is used after a bulk supplier/risk-event import to backfill the
        database risk state.  It deliberately does not advance ``next_due_at``;
        the normal weekly/monthly monitoring cadence remains unchanged.
        """
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        requested = None
        if supplier_ids:
            requested = {normalize_supplier_id(item) for item in supplier_ids}
        now = _now()
        created: list[dict[str, Any]] = []
        with connect(self.database_url) as db:
            rows = db.execute(
                """SELECT s.supplier_id,
                          (SELECT a.assignment_id FROM supplier_monitor_assignment a
                           WHERE a.supplier_id=s.supplier_id AND a.enabled=1
                           ORDER BY a.created_at LIMIT 1) AS assignment_id
                   FROM supplier s WHERE s.status='ACTIVE' ORDER BY s.supplier_id"""
            ).fetchall()
            selected = [row for row in rows if requested is None or row["supplier_id"] in requested]
            if requested is not None:
                found = {row["supplier_id"] for row in selected}
                missing = sorted(requested - found)
                if missing:
                    raise ValueError(f"unknown or inactive supplier_ids: {', '.join(missing)}")
            db.execute("BEGIN")
            try:
                for row in selected[:limit]:
                    monitor_id = _new_id()
                    db.execute(
                        """INSERT INTO monitor_task(monitor_id, supplier_id, assignment_id, monitor_type, status, scheduled_at, data_version)
                           VALUES (?, ?, ?, 'MANUAL', 'PENDING', ?, ?)""",
                        (
                            monitor_id,
                            row["supplier_id"],
                            row["assignment_id"],
                            _iso(now),
                            f"manual-refresh-{now.strftime('%Y%m%d%H%M%S')}",
                        ),
                    )
                    created.append({"monitor_id": monitor_id, "supplier_id": row["supplier_id"], "monitor_type": "MANUAL"})
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        return created

    def snapshot_for_monitor(self, monitor_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        with connect(self.database_url) as db:
            task = db.execute("SELECT * FROM monitor_task WHERE monitor_id=?", (monitor_id,)).fetchone()
            if task is None:
                raise ValueError(f"unknown monitor_id: {monitor_id}")
            profile = db.execute("SELECT * FROM supplier WHERE supplier_id=?", (task["supplier_id"],)).fetchone()
            event_rows = db.execute(
                """SELECT e.*, x.evidence_id FROM risk_event e
                   JOIN risk_event_evidence x ON x.risk_event_id=e.risk_event_id
                   WHERE e.supplier_id=? AND e.event_status='ACTIVE' ORDER BY e.event_week""",
                (task["supplier_id"],),
            ).fetchall()
        events = [
            {"supplier_id": item["supplier_id"], "evidence_id": item["evidence_id"], "event_category": item["risk_dimension"], "event_subtype": item["risk_type"], "event_severity": item["severity"], "event_week": item["event_week"], "source_type": item["source_type"]}
            for item in event_rows
        ]
        snapshot = {"supplier_profile": {"supplier_id": profile["supplier_id"], "supplier_name": profile["supplier_name"], "importance_level": "重要" if profile["importance"] == "IMPORTANT" else "一般"}, "events": events, "rectifies": [], "weekly": {}, "max_week": max((event["event_week"] for event in events), default=1)}
        return dict(task), snapshot

    def create_analysis_run(self, monitor_id: str, snapshot: dict[str, Any]) -> tuple[str, str]:
        analysis_run_id, run_id = _new_id(), _new_id()
        with connect(self.database_url) as db:
            task = db.execute("SELECT supplier_id, data_version FROM monitor_task WHERE monitor_id=?", (monitor_id,)).fetchone()
            if task is None:
                raise ValueError(f"unknown monitor_id: {monitor_id}")
            db.execute("UPDATE monitor_task SET status='RUNNING', started_at=? WHERE monitor_id=?", (_iso(_now()), monitor_id))
            db.execute(
                """INSERT INTO analysis_run(analysis_run_id, run_id, supplier_id, monitor_id, status, agent_version, input_data_version, input_snapshot_json, started_at)
                   VALUES (?, ?, ?, ?, 'RUNNING', '0.3.0', ?, ?, ?)""",
                (analysis_run_id, run_id, task["supplier_id"], monitor_id, task["data_version"], json.dumps(snapshot, ensure_ascii=False), _iso(_now())),
            )
        return analysis_run_id, run_id

    def persist_analysis(self, analysis_run_id: str, state: dict[str, Any]) -> dict[str, str]:
        report = state["risk_report"]
        grade = report["risk_grade"]
        result_id, report_id = _new_id(), str(report.get("report_id") or _new_id())
        with connect(self.database_url) as db:
            db.execute("BEGIN")
            try:
                run = db.execute("SELECT supplier_id, monitor_id FROM analysis_run WHERE analysis_run_id=?", (analysis_run_id,)).fetchone()
                if run is None:
                    raise ValueError(f"unknown analysis_run_id: {analysis_run_id}")
                db.execute("UPDATE analysis_run SET status='SUCCEEDED', analysis_route=?, completed_at=? WHERE analysis_run_id=?", (state.get("analysis_route"), _iso(_now()), analysis_run_id))
                db.execute(
                    """INSERT INTO risk_result(risk_result_id, analysis_run_id, supplier_id, monitor_id, risk_level, risk_score, window_unit, window_size, window_display, importance_tier, importance_source, report_period, dimension_breakdown_json, risk_summary)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (result_id, analysis_run_id, run["supplier_id"], run["monitor_id"], grade["risk_level"], grade["score"], grade["window_unit"], grade["window_size"], grade["window_display"], grade["importance_tier"], grade["importance_source"], grade["report_period"], json.dumps(grade["dimension_breakdown"], ensure_ascii=False), report.get("risk_summary", "")),
                )
                for hit in grade.get("hit_rules", []):
                    db.execute("INSERT INTO risk_result_rule(risk_result_id, rule_id, rule_description, risk_dimension, severity, weight, is_red_line) VALUES (?, ?, ?, ?, ?, ?, ?)", (result_id, hit["rule_id"], hit["description"], hit.get("category"), hit.get("severity"), hit.get("weight"), int(str(hit["rule_id"]).startswith("RL-"))))
                ordered = [("DIMENSION_MAPPING", state.get("dimension_mapping_result")), ("RISK_IDENTIFICATION", state.get("risk_identification_result")), ("ASSOCIATION_ANALYSIS", state.get("association_result")), ("EVIDENCE", state.get("evidence_result")), ("DECISION", state.get("llm_advisory")), ("CONSISTENCY_CHECK", state.get("consistency_check_result")), ("HUMAN_REVIEW", state.get("disposition"))]
                for index, (name, payload) in enumerate(ordered, start=1):
                    if payload is not None:
                        step_status = payload.get("llm_status") or payload.get("status") or "SUCCESS"
                        db.execute("INSERT INTO agent_step_result(agent_step_result_id, analysis_run_id, step_name, execution_order, status, output_json) VALUES (?, ?, ?, ?, ?, ?)", (_new_id(), analysis_run_id, name, index, str(step_status), json.dumps(payload, ensure_ascii=False)))
                db.execute(
                    """INSERT INTO risk_report(report_id, analysis_run_id, supplier_id, risk_result_id, report_version, human_review_status, disposition_json, candidate_actions_json, report_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (report_id, analysis_run_id, run["supplier_id"], result_id, report.get("schema_version", "C-DRAFT-V0.3"), report["human_review_status"], json.dumps(report.get("disposition") or {}, ensure_ascii=False), json.dumps(report.get("candidate_actions") or [], ensure_ascii=False), json.dumps(report, ensure_ascii=False)),
                )
                db.execute("UPDATE supplier SET current_risk_level=?, updated_at=? WHERE supplier_id=?", (grade["risk_level"], _iso(_now()), run["supplier_id"]))
                db.execute("UPDATE monitor_task SET status='SUCCESS', completed_at=? WHERE monitor_id=?", (_iso(_now()), run["monitor_id"]))
                recipients = db.execute(
                    """SELECT DISTINCT ur.user_id, nr.notification_type, nr.channel
                       FROM notification_rule nr JOIN user_role ur ON ur.role_id=nr.role_id
                       JOIN app_user u ON u.user_id=ur.user_id
                       WHERE nr.enabled=1 AND u.status='ACTIVE' AND nr.risk_level=? AND nr.channel='WEB'""",
                    (grade["risk_level"],),
                ).fetchall()
                for recipient in recipients:
                    db.execute(
                        """INSERT INTO notification(notification_id, recipient_user_id, supplier_id, report_id, notification_type, risk_level, title, content, channel)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (_new_id(), recipient["user_id"], run["supplier_id"], report_id, recipient["notification_type"], grade["risk_level"],
                         f"供应商风险报告 {grade['risk_level']}", report.get("risk_summary", "新风险报告已生成"), recipient["channel"]),
                    )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        return {"analysis_run_id": analysis_run_id, "risk_result_id": result_id, "report_id": report_id}

    def mark_analysis_failed(self, analysis_run_id: str, error: Exception) -> None:
        with connect(self.database_url) as db:
            run = db.execute("SELECT monitor_id FROM analysis_run WHERE analysis_run_id=?", (analysis_run_id,)).fetchone()
            db.execute("UPDATE analysis_run SET status='FAILED', error_message=?, completed_at=? WHERE analysis_run_id=?", (str(error), _iso(_now()), analysis_run_id))
            if run:
                db.execute("UPDATE monitor_task SET status='FAILED', error_message=?, completed_at=? WHERE monitor_id=?", (str(error), _iso(_now()), run["monitor_id"]))

    def list_suppliers(self) -> list[dict[str, Any]]:
        with connect(self.database_url) as db:
            return [dict(row) for row in db.execute("SELECT * FROM supplier ORDER BY supplier_id").fetchall()]

    def get_supplier(self, supplier_id: str) -> dict[str, Any]:
        return self.supplier_for_source(supplier_id)

    def list_risk_events(self, supplier_id: str, *, status: str | None = "ACTIVE", limit: int = 200) -> list[dict[str, Any]]:
        supplier_id = normalize_supplier_id(supplier_id)
        self.supplier_for_source(supplier_id)
        with connect(self.database_url) as db:
            if status:
                rows = db.execute("SELECT * FROM risk_event WHERE supplier_id=? AND event_status=? ORDER BY created_at DESC LIMIT ?", (supplier_id, status, limit))
            else:
                rows = db.execute("SELECT * FROM risk_event WHERE supplier_id=? ORDER BY created_at DESC LIMIT ?", (supplier_id, limit))
            return [dict(row) for row in rows]

    def set_risk_event_status(self, risk_event_id: str, status: str) -> dict[str, Any]:
        if status not in {"ACTIVE", "RESOLVED", "REVOKED"}:
            raise ValueError("status must be ACTIVE, RESOLVED or REVOKED")
        with connect(self.database_url) as db:
            row = db.execute("SELECT * FROM risk_event WHERE risk_event_id=?", (risk_event_id,)).fetchone()
            if row is None:
                raise ValueError(f"unknown risk_event_id: {risk_event_id}")
            db.execute("UPDATE risk_event SET event_status=? WHERE risk_event_id=?", (status, risk_event_id))
            updated = db.execute("SELECT * FROM risk_event WHERE risk_event_id=?", (risk_event_id,)).fetchone()
        return dict(updated)

    def get_risk_event(self, risk_event_id: str) -> dict[str, Any]:
        with connect(self.database_url) as db:
            row = db.execute("SELECT * FROM risk_event WHERE risk_event_id=?", (risk_event_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown risk_event_id: {risk_event_id}")
        return dict(row)

    def list_supplier_reports(self, supplier_id: str, limit: int = 50) -> list[dict[str, Any]]:
        supplier_id = normalize_supplier_id(supplier_id)
        self.supplier_for_source(supplier_id)
        with connect(self.database_url) as db:
            rows = db.execute(
                """SELECT report.report_id, report.analysis_run_id, report.supplier_id, report.report_version,
                          report.human_review_status, report.created_at, result.risk_level, result.risk_score, result.report_period
                   FROM risk_report report JOIN risk_result result ON result.risk_result_id=report.risk_result_id
                   WHERE report.supplier_id=? ORDER BY report.created_at DESC LIMIT ?""",
                (supplier_id, limit),
            )
            return [dict(row) for row in rows]

    def list_monitor_tasks(self) -> list[dict[str, Any]]:
        with connect(self.database_url) as db:
            return [dict(row) for row in db.execute("SELECT * FROM monitor_task ORDER BY created_at DESC").fetchall()]

    def register_source(self, source: dict[str, Any]) -> dict[str, Any]:
        """Register API/manual/mock source metadata; never accepts a credential value."""
        source_code = str(source.get("source_code", "")).strip().upper()
        source_name = str(source.get("source_name", "")).strip()
        base_url = str(source.get("base_url", "")).strip().rstrip("/")
        endpoint_path = str(source.get("endpoint_path", "")).strip()
        auth_env_var = str(source.get("auth_env_var", "")).strip()
        access_mode = str(source.get("access_mode") or "API").upper()
        if access_mode not in {"API", "MANUAL_WEB", "MOCK"}:
            raise ValueError("access_mode must be API, MANUAL_WEB or MOCK")
        if not source_code or not source_name or not base_url.startswith("https://") or not endpoint_path.startswith("/"):
            raise ValueError("source_code, source_name, https base_url and endpoint_path are required")
        if access_mode == "API" and not auth_env_var:
            raise ValueError("API source requires auth_env_var")
        now = _iso(_now())
        values = {
            "auth_header": str(source.get("auth_header") or "Authorization"),
            "records_path": source.get("records_path"),
            "query_template_json": json.dumps(source.get("query_template") or {}, ensure_ascii=False),
            "field_mapping_json": json.dumps(source.get("field_mapping") or {}, ensure_ascii=False),
            "access_mode": access_mode,
            "live_http_enabled": int(bool(source.get("live_http_enabled", False))),
            "enabled": int(bool(source.get("enabled", True))),
        }
        with connect(self.database_url) as db:
            db.execute(
                """INSERT INTO source_system(source_code, source_name, base_url, endpoint_path, auth_header, auth_env_var, records_path, query_template_json, field_mapping_json, access_mode, live_http_enabled, enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source_code) DO UPDATE SET source_name=excluded.source_name, base_url=excluded.base_url,
                     endpoint_path=excluded.endpoint_path, auth_header=excluded.auth_header, auth_env_var=excluded.auth_env_var,
                     records_path=excluded.records_path, query_template_json=excluded.query_template_json,
                     field_mapping_json=excluded.field_mapping_json, access_mode=excluded.access_mode,
                     live_http_enabled=excluded.live_http_enabled, enabled=excluded.enabled, updated_at=excluded.updated_at""",
                (source_code, source_name, base_url, endpoint_path, auth_env_var and values["auth_header"], auth_env_var,
                 values["records_path"], values["query_template_json"], values["field_mapping_json"], values["access_mode"],
                 values["live_http_enabled"], values["enabled"], now, now),
            )
        return self.get_source(source_code)

    def get_source(self, source_code: str) -> dict[str, Any]:
        with connect(self.database_url) as db:
            row = db.execute("SELECT * FROM source_system WHERE source_code=?", (source_code.upper(),)).fetchone()
        if row is None:
            raise ValueError(f"unknown source_code: {source_code}")
        result = dict(row)
        result["query_template"] = json.loads(result.pop("query_template_json"))
        result["field_mapping"] = json.loads(result.pop("field_mapping_json"))
        return result

    def list_sources(self) -> list[dict[str, Any]]:
        with connect(self.database_url) as db:
            codes = [row["source_code"] for row in db.execute("SELECT source_code FROM source_system ORDER BY source_code")]
        return [self.get_source(code) for code in codes]

    def set_source_live_http(self, source_code: str, enabled: bool) -> dict[str, Any]:
        source = self.get_source(source_code)
        if source["access_mode"] != "API" and enabled:
            raise ValueError("only API sources can enable live HTTP")
        with connect(self.database_url) as db:
            db.execute("UPDATE source_system SET live_http_enabled=?, updated_at=? WHERE source_code=?", (int(enabled), _iso(_now()), source_code.upper()))
        return self.get_source(source_code)

    def supplier_for_source(self, supplier_id: str) -> dict[str, Any]:
        supplier_id = normalize_supplier_id(supplier_id)
        with connect(self.database_url) as db:
            row = db.execute("SELECT * FROM supplier WHERE supplier_id=?", (supplier_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown supplier_id: {supplier_id}")
        return dict(row)

    def save_raw_source_payload(self, source_code: str, supplier_id: str, payload: Any) -> str:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        raw_record_id = _new_id()
        with connect(self.database_url) as db:
            db.execute(
                "INSERT INTO raw_source_record(raw_record_id, source_code, supplier_id, fetched_at, payload_sha256, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
                (raw_record_id, source_code.upper(), normalize_supplier_id(supplier_id), _iso(_now()), hashlib.sha256(serialized.encode("utf-8")).hexdigest(), serialized),
            )
        return raw_record_id

    def create_collection_task(self, *, source_code: str, supplier_id: str, query: dict[str, Any], assigned_to: str | None = None) -> dict[str, Any]:
        source = self.get_source(source_code)
        if source["access_mode"] != "MANUAL_WEB":
            raise ValueError("collection tasks are only for MANUAL_WEB sources")
        supplier = self.supplier_for_source(supplier_id)
        if assigned_to:
            self.get_user(assigned_to)
        task_id = _new_id()
        with connect(self.database_url) as db:
            db.execute("INSERT INTO collection_task(collection_task_id, source_code, supplier_id, query_json, assigned_to) VALUES (?, ?, ?, ?, ?)", (task_id, source_code.upper(), supplier["supplier_id"], json.dumps(query, ensure_ascii=False), assigned_to))
        return self.get_collection_task(task_id)

    def get_collection_task(self, task_id: str) -> dict[str, Any]:
        with connect(self.database_url) as db:
            row = db.execute("SELECT * FROM collection_task WHERE collection_task_id=?", (task_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown collection_task_id: {task_id}")
        result = dict(row)
        result["query"] = json.loads(result.pop("query_json"))
        result["result"] = json.loads(result.pop("result_json")) if result.get("result_json") else None
        return result

    def list_collection_tasks(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with connect(self.database_url) as db:
            if status:
                ids = [row["collection_task_id"] for row in db.execute("SELECT collection_task_id FROM collection_task WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit))]
            else:
                ids = [row["collection_task_id"] for row in db.execute("SELECT collection_task_id FROM collection_task ORDER BY created_at DESC LIMIT ?", (limit,))]
        return [self.get_collection_task(item) for item in ids]

    def submit_collection_task(self, task_id: str, *, submitted_by: str, source_url: str, result: dict[str, Any], file_id: str | None = None) -> dict[str, Any]:
        task = self.get_collection_task(task_id)
        if task["status"] not in {"PENDING", "PROCESSING"}:
            raise ValueError("collection task is not open for submission")
        self.get_user(submitted_by)
        if file_id:
            self.get_file_object(file_id)
        serialized = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
        with connect(self.database_url) as db:
            db.execute("""UPDATE collection_task SET status='SUBMITTED', submitted_by=?, source_url=?, file_id=?, result_json=?, result_sha256=?, submitted_at=?
                          WHERE collection_task_id=?""", (submitted_by, source_url, file_id, serialized, hashlib.sha256(serialized.encode("utf-8")).hexdigest(), _iso(_now()), task_id))
        return self.get_collection_task(task_id)

    def review_collection_task(self, task_id: str, *, approved: bool, review_comment: str | None = None) -> dict[str, Any]:
        task = self.get_collection_task(task_id)
        if task["status"] != "SUBMITTED":
            raise ValueError("only submitted collection tasks can be reviewed")
        with connect(self.database_url) as db:
            db.execute("UPDATE collection_task SET status=?, review_comment=?, verified_at=? WHERE collection_task_id=?", ("VERIFIED" if approved else "REJECTED", review_comment, _iso(_now()), task_id))
        return self.get_collection_task(task_id)

    def create_file_object(self, *, original_filename: str, stored_path: str, content_type: str | None, file_size: int, sha256: str, purpose: str) -> str:
        file_id = _new_id()
        with connect(self.database_url) as db:
            db.execute(
                "INSERT INTO file_object(file_id, original_filename, stored_path, content_type, file_size, sha256, purpose) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (file_id, original_filename, stored_path, content_type, file_size, sha256, purpose),
            )
        return file_id

    def get_file_object(self, file_id: str) -> dict[str, Any]:
        with connect(self.database_url) as db:
            row = db.execute("SELECT * FROM file_object WHERE file_id=?", (file_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown file_id: {file_id}")
        return dict(row)

    def get_report(self, report_id: str) -> dict[str, Any]:
        with connect(self.database_url) as db:
            row = db.execute("SELECT * FROM risk_report WHERE report_id=?", (report_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown report_id: {report_id}")
        result = dict(row)
        result["report"] = json.loads(result.pop("report_json"))
        return result

    def report_agent_trace(self, report_id: str) -> dict[str, Any]:
        """Return real persisted/model node statuses with report-derived summaries."""
        report_row = self.get_report(report_id)
        report = report_row["report"]
        with connect(self.database_url) as db:
            rows = db.execute(
                """SELECT step_name, execution_order, status, output_json, error_message, created_at
                   FROM agent_step_result WHERE analysis_run_id=? ORDER BY execution_order""",
                (report_row["analysis_run_id"],),
            ).fetchall()
        persisted = {row["step_name"]: dict(row) for row in rows}
        status_map = dict(report.get("llm_node_status") or {})
        evidence_items = report.get("evidence_summary") or []
        evidence_ids = [
            str(item.get("evidence_id")) for item in evidence_items
            if isinstance(item, dict) and item.get("evidence_id")
        ]
        definitions = [
            ("DIMENSION_MAPPING", "dimension_mapping", "维度归类 Agent"),
            ("RISK_IDENTIFICATION", "risk_identification", "风险识别 Agent"),
            ("ASSOCIATION_ANALYSIS", "association_analysis", "关联分析 Agent"),
            ("EVIDENCE", "evidence", "证据 Agent"),
            ("DECISION", "decision", "决策建议 Agent"),
            ("CONSISTENCY_CHECK", "consistency_check", "一致性校验 Agent"),
        ]
        summaries = {
            "DIMENSION_MAPPING": f"六维度归类完成，共处理 {sum(int(item.get('event_count', 0)) for item in report.get('dimension_breakdown', []) if isinstance(item, dict))} 条窗口内事件",
            "RISK_IDENTIFICATION": str(report.get("risk_summary") or "风险识别已完成"),
            "ASSOCIATION_ANALYSIS": str(report.get("association_summary") or (report.get("risk_trend") or {}).get("trend_desc") or "关联分析已完成"),
            "EVIDENCE": f"已关联并校验 {len(evidence_ids)} 条证据",
            "DECISION": str(report.get("recommendation") or "候选处置建议已生成"),
            "CONSISTENCY_CHECK": str((report.get("consistency_check") or {}).get("notes") or "一致性校验结果见节点输出"),
        }
        steps: list[dict[str, Any]] = []
        for order, (step_name, status_key, agent_name) in enumerate(definitions, start=1):
            row = persisted.get(step_name)
            output = json.loads(row["output_json"]) if row and row.get("output_json") else None
            llm_status = status_map.get(status_key) or (row.get("status") if row else None) or "UNKNOWN"
            step_evidence = evidence_ids if step_name in {"EVIDENCE", "DECISION", "CONSISTENCY_CHECK"} else []
            steps.append(
                {
                    "step_name": step_name,
                    "agent_name": agent_name,
                    "execution_order": int(row["execution_order"]) if row else order,
                    "llm_status": str(llm_status),
                    "summary": summaries[step_name],
                    "evidence_ids": step_evidence,
                    "model_provider": output.get("provider") if isinstance(output, dict) else None,
                    "model_name": output.get("model") if isinstance(output, dict) else None,
                    "output": output,
                    "error_message": row.get("error_message") if row else None,
                    "created_at": row.get("created_at") if row else report.get("generated_at"),
                }
            )
        statuses = [item["llm_status"] for item in steps]
        if "FALLBACK" in statuses:
            mode = "DEGRADED"
        elif "SUCCESS" in statuses:
            mode = "LIVE"
        elif all(item in {"DISABLED", "SKIPPED", "UNKNOWN"} for item in statuses):
            mode = "DETERMINISTIC"
        else:
            mode = "MIXED"
        counts = {status: statuses.count(status) for status in sorted(set(statuses))}
        return {
            "report_id": report_id,
            "analysis_run_id": report_row["analysis_run_id"],
            "supplier_id": report_row["supplier_id"],
            "run_id": report.get("run_id"),
            "generated_at": report.get("generated_at"),
            "execution_mode": mode,
            "status_summary": counts,
            "steps": steps,
        }

    def report_visualization(self, report_id: str) -> dict[str, Any]:
        report_row = self.get_report(report_id)
        report = report_row["report"]
        grade = report.get("risk_grade") or {}
        breakdown = grade.get("dimension_breakdown") or {}
        if isinstance(breakdown, dict):
            dimension_items = [{"dimension": name, "score": score} for name, score in breakdown.items()]
        else:
            dimension_items = [item for item in breakdown if isinstance(item, dict)]
        radar_values = [item.get("score", 0) for item in dimension_items]
        with connect(self.database_url) as db:
            history = db.execute(
                """SELECT result.created_at, result.risk_score, result.risk_level FROM risk_result result
                   WHERE result.supplier_id=? ORDER BY result.created_at ASC LIMIT 24""",
                (report_row["supplier_id"],),
            ).fetchall()
        return {
            "report_id": report_id,
            "supplier_id": report_row["supplier_id"],
            "summary_cards": {"risk_level": grade.get("risk_level"), "risk_score": grade.get("score"), "report_period": grade.get("report_period")},
            "radar": {
                "indicators": [{"name": item.get("dimension", "未分类"), "max": 5} for item in dimension_items],
                "series": [{"name": "六维风险分布", "value": radar_values}],
            },
            "trend": {
                "labels": [item["created_at"] for item in history],
                "scores": [item["risk_score"] for item in history],
                "levels": [item["risk_level"] for item in history],
            },
        }

    # RBAC configuration.  Authentication is intentionally outside this
    # repository: the API receives a user identity from SSO/JWT or the gateway.
    def list_permissions(self) -> list[dict[str, Any]]:
        with connect(self.database_url) as db:
            return [dict(row) for row in db.execute("SELECT * FROM permission ORDER BY resource_type, permission_code")]

    def list_roles(self) -> list[dict[str, Any]]:
        with connect(self.database_url) as db:
            rows = db.execute("SELECT * FROM app_role ORDER BY level DESC, role_code").fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["permissions"] = [record["permission_code"] for record in db.execute("SELECT permission_code FROM role_permission WHERE role_id=? ORDER BY permission_code", (row["role_id"],))]
                result.append(item)
            return result

    def upsert_role(self, record: dict[str, Any]) -> dict[str, Any]:
        code = str(record.get("role_code", "")).strip().upper()
        name = str(record.get("role_name", "")).strip()
        level = int(record.get("level", 0))
        permissions = sorted(set(str(item).strip() for item in record.get("permissions", []) if str(item).strip()))
        if not code or not name or not 1 <= level <= 100:
            raise ValueError("role_code, role_name and level(1-100) are required")
        known = {item["permission_code"] for item in self.list_permissions()}
        unknown = set(permissions) - known
        if unknown:
            raise ValueError(f"unknown permissions: {', '.join(sorted(unknown))}")
        now = _iso(_now())
        with connect(self.database_url) as db:
            existing = db.execute("SELECT role_id, is_system FROM app_role WHERE role_code=?", (code,)).fetchone()
            if existing and existing["is_system"]:
                raise ValueError("system role cannot be edited")
            role_id = existing["role_id"] if existing else _new_id()
            if existing:
                db.execute("UPDATE app_role SET role_name=?, level=?, enabled=?, updated_at=? WHERE role_id=?", (name, level, int(bool(record.get("enabled", True))), now, role_id))
                db.execute("DELETE FROM role_permission WHERE role_id=?", (role_id,))
            else:
                db.execute("INSERT INTO app_role(role_id, role_code, role_name, level, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (role_id, code, name, level, int(bool(record.get("enabled", True))), now, now))
            for permission in permissions:
                db.execute("INSERT INTO role_permission(role_id, permission_code) VALUES (?, ?)", (role_id, permission))
        return next(item for item in self.list_roles() if item["role_id"] == role_id)

    def upsert_user(self, record: dict[str, Any]) -> dict[str, Any]:
        user_id = str(record.get("user_id") or _new_id())
        username = str(record.get("username", "")).strip()
        display_name = str(record.get("display_name", "")).strip()
        if not username or not display_name:
            raise ValueError("username and display_name are required")
        now = _iso(_now())
        with connect(self.database_url) as db:
            db.execute("""INSERT INTO app_user(user_id, username, display_name, status, updated_at) VALUES (?, ?, ?, ?, ?)
                          ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, display_name=excluded.display_name, status=excluded.status, updated_at=excluded.updated_at""", (user_id, username, display_name, record.get("status", "ACTIVE"), now))
        return self.get_user(user_id)

    def get_user(self, user_id: str) -> dict[str, Any]:
        with connect(self.database_url) as db:
            row = db.execute("SELECT * FROM app_user WHERE user_id=?", (user_id,)).fetchone()
            if row is None:
                raise ValueError(f"unknown user_id: {user_id}")
            item = dict(row)
            item["role_ids"] = [record["role_id"] for record in db.execute("SELECT role_id FROM user_role WHERE user_id=?", (user_id,))]
            item["supplier_ids"] = [record["supplier_id"] for record in db.execute("SELECT supplier_id FROM user_supplier_scope WHERE user_id=?", (user_id,))]
            return item

    def list_users(self) -> list[dict[str, Any]]:
        with connect(self.database_url) as db:
            user_ids = [row["user_id"] for row in db.execute("SELECT user_id FROM app_user ORDER BY username")]
        return [self.get_user(user_id) for user_id in user_ids]

    def set_user_roles(self, user_id: str, role_ids: list[str]) -> dict[str, Any]:
        self.get_user(user_id)
        selected = sorted(set(role_ids))
        with connect(self.database_url) as db:
            known = {row["role_id"] for row in db.execute("SELECT role_id FROM app_role WHERE enabled=1")}
            if set(selected) - known:
                raise ValueError("unknown or disabled role_id")
            db.execute("DELETE FROM user_role WHERE user_id=?", (user_id,))
            for role_id in selected:
                db.execute("INSERT INTO user_role(user_id, role_id) VALUES (?, ?)", (user_id, role_id))
        return self.get_user(user_id)

    def set_user_supplier_scope(self, user_id: str, supplier_ids: list[str]) -> dict[str, Any]:
        self.get_user(user_id)
        selected = sorted(set(normalize_supplier_id(item) for item in supplier_ids))
        with connect(self.database_url) as db:
            known = {row["supplier_id"] for row in db.execute("SELECT supplier_id FROM supplier")}
            if set(selected) - known:
                raise ValueError("unknown supplier_id in scope")
            db.execute("DELETE FROM user_supplier_scope WHERE user_id=?", (user_id,))
            for supplier_id in selected:
                db.execute("INSERT INTO user_supplier_scope(user_id, supplier_id) VALUES (?, ?)", (user_id, supplier_id))
        return self.get_user(user_id)

    def access_profile(self, user_id: str) -> dict[str, Any]:
        user = self.get_user(user_id)
        if user["status"] != "ACTIVE":
            raise ValueError("user is disabled")
        with connect(self.database_url) as db:
            roles = [dict(row) for row in db.execute("""SELECT r.* FROM app_role r JOIN user_role ur ON ur.role_id=r.role_id
                                                        WHERE ur.user_id=? AND r.enabled=1 ORDER BY r.level DESC""", (user_id,))]
            permissions = sorted({row["permission_code"] for role in roles for row in db.execute("SELECT permission_code FROM role_permission WHERE role_id=?", (role["role_id"],))})
        return {"user": user, "roles": roles, "permissions": permissions, "max_level": max((role["level"] for role in roles), default=0), "supplier_scope": user["supplier_ids"]}

    def authorize(self, user_id: str, permission: str, supplier_id: str | None = None) -> dict[str, Any]:
        profile = self.access_profile(user_id)
        if permission not in profile["permissions"]:
            raise ValueError(f"missing permission: {permission}")
        if supplier_id and profile["supplier_scope"] and normalize_supplier_id(supplier_id) not in profile["supplier_scope"]:
            raise ValueError(f"supplier is outside user scope: {supplier_id}")
        return profile

    def audit(self, *, actor_user_id: str | None, action: str, resource_type: str, resource_id: str, trace_id: str | None = None, before: Any = None, after: Any = None) -> None:
        with connect(self.database_url) as db:
            db.execute("INSERT INTO audit_log(audit_id, actor_user_id, action, resource_type, resource_id, before_json, after_json, trace_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (_new_id(), actor_user_id, action, resource_type, resource_id, json.dumps(before, ensure_ascii=False) if before is not None else None, json.dumps(after, ensure_ascii=False) if after is not None else None, trace_id))

    def list_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        with connect(self.database_url) as db:
            return [dict(row) for row in db.execute("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?", (limit,))]

    def submit_review(self, record: dict[str, Any]) -> dict[str, Any]:
        report_id, reviewer_id = str(record.get("report_id", "")), str(record.get("reviewer_id", ""))
        result = str(record.get("review_result", ""))
        if result not in {"APPROVED", "REJECTED", "NEED_MORE_EVIDENCE", "REDECLARED"}:
            raise ValueError("invalid review_result")
        self.get_report(report_id); self.get_user(reviewer_id)
        review_id = _new_id()
        with connect(self.database_url) as db:
            db.execute("INSERT INTO human_review_record(review_id, report_id, reviewer_id, review_result, final_action, review_comment, confirmed_risk_level) VALUES (?, ?, ?, ?, ?, ?, ?)", (review_id, report_id, reviewer_id, result, record.get("final_action"), record.get("review_comment"), record.get("confirmed_risk_level")))
        return {"review_id": review_id, "report_id": report_id, "reviewer_id": reviewer_id, "review_result": result}

    def list_reviews(self, report_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with connect(self.database_url) as db:
            if report_id:
                rows = db.execute("SELECT * FROM human_review_record WHERE report_id=? ORDER BY created_at DESC LIMIT ?", (report_id, limit))
            else:
                rows = db.execute("SELECT * FROM human_review_record ORDER BY created_at DESC LIMIT ?", (limit,))
            return [dict(row) for row in rows]

    def submit_redeclare(self, record: dict[str, Any]) -> dict[str, Any]:
        supplier_id = normalize_supplier_id(str(record.get("supplier_id", "")))
        self.supplier_for_source(supplier_id)
        report_id = record.get("report_id")
        if report_id and self.get_report(str(report_id))["supplier_id"] != supplier_id:
            raise ValueError("report does not belong to supplier")
        reason = str(record.get("reason", "")).strip()
        submitted_by = str(record.get("submitted_by", "")).strip()
        if not reason or not submitted_by:
            raise ValueError("submitted_by and reason are required")
        request_id = _new_id()
        with connect(self.database_url) as db:
            db.execute("""INSERT INTO redeclare_request(redeclare_request_id, supplier_id, report_id, submitted_by, reason, attachment_refs_json)
                          VALUES (?, ?, ?, ?, ?, ?)""", (request_id, supplier_id, report_id, submitted_by, reason, json.dumps(record.get("attachment_refs") or [], ensure_ascii=False)))
        return self.get_redeclare(request_id)

    def get_redeclare(self, request_id: str) -> dict[str, Any]:
        with connect(self.database_url) as db:
            row = db.execute("SELECT * FROM redeclare_request WHERE redeclare_request_id=?", (request_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown redeclare_request_id: {request_id}")
        result = dict(row)
        result["attachment_refs"] = json.loads(result.pop("attachment_refs_json"))
        return result

    def list_redeclares(self, *, supplier_id: str | None = None, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        clauses, params = [], []
        if supplier_id:
            clauses.append("supplier_id=?"); params.append(normalize_supplier_id(supplier_id))
        if status:
            clauses.append("status=?"); params.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with connect(self.database_url) as db:
            ids = [row["redeclare_request_id"] for row in db.execute(f"SELECT redeclare_request_id FROM redeclare_request{where} ORDER BY created_at DESC LIMIT ?", (*params, limit))]
        return [self.get_redeclare(item) for item in ids]

    def review_redeclare(self, request_id: str, *, status: str, review_comment: str | None = None) -> dict[str, Any]:
        if status not in {"PROCESSING", "APPROVED", "REJECTED", "CANCELLED"}:
            raise ValueError("invalid redeclare status")
        self.get_redeclare(request_id)
        with connect(self.database_url) as db:
            db.execute("UPDATE redeclare_request SET status=?, review_comment=?, updated_at=? WHERE redeclare_request_id=?", (status, review_comment, _iso(_now()), request_id))
        return self.get_redeclare(request_id)

    def dashboard_summary(self, supplier_ids: list[str] | None = None) -> dict[str, Any]:
        allowed = {normalize_supplier_id(item) for item in supplier_ids} if supplier_ids else None
        suppliers = [item for item in self.list_suppliers() if item["status"] == "ACTIVE" and (allowed is None or item["supplier_id"] in allowed)]
        supplier_set = {item["supplier_id"] for item in suppliers}
        # ``supplier.current_risk_level`` is a convenient cache, but imported
        # suppliers start as GREEN.  Prefer the latest persisted rule-engine
        # result so a stale cache can never corrupt dashboard statistics.
        latest_results: dict[str, dict[str, Any]] = {}
        with connect(self.database_url) as db:
            result_rows = db.execute(
                """SELECT supplier_id, risk_level, risk_score, created_at
                   FROM risk_result ORDER BY created_at DESC, rowid DESC"""
            ).fetchall()
            review_rows = db.execute("SELECT supplier_id, human_review_status FROM risk_report").fetchall()
        for row in result_rows:
            latest_results.setdefault(row["supplier_id"], dict(row))
        effective_suppliers: list[dict[str, Any]] = []
        for supplier in suppliers:
            item = dict(supplier)
            latest = latest_results.get(item["supplier_id"])
            if latest:
                item["current_risk_level"] = latest["risk_level"]
                item["current_risk_score"] = latest["risk_score"]
                item["risk_state_source"] = "LATEST_RISK_RESULT"
                item["risk_evaluated_at"] = latest["created_at"]
            else:
                item["current_risk_score"] = 0.0
                item["risk_state_source"] = "SUPPLIER_DEFAULT"
                item["risk_evaluated_at"] = None
            effective_suppliers.append(item)
        levels = {level: sum(item["current_risk_level"] == level for item in effective_suppliers) for level in ("RED", "YELLOW", "GREEN")}
        tasks: dict[str, int] = {}
        for task in self.list_monitor_tasks():
            if task["supplier_id"] in supplier_set:
                tasks[task["status"]] = tasks.get(task["status"], 0) + 1
        pending_states = {"PENDING_HUMAN_REVIEW", "EVIDENCE_INSUFFICIENT", "AWAITING_SUPPLIER_REDECLARE"}
        pending_reviews = sum(row["supplier_id"] in supplier_set and row["human_review_status"] in pending_states for row in review_rows)
        watchlist = sorted(effective_suppliers, key=lambda item: ({"RED": 1, "YELLOW": 2, "GREEN": 3}.get(item["current_risk_level"], 4), -float(item["current_risk_score"]), 0 if item["importance"] == "IMPORTANT" else 1, item["updated_at"]))[:20]
        analyzed_count = sum(item["risk_state_source"] == "LATEST_RISK_RESULT" for item in effective_suppliers)
        return {
            "supplier_count_by_risk": levels,
            "supplier_analysis_coverage": {
                "analyzed": analyzed_count,
                "total": len(effective_suppliers),
                "pending": len(effective_suppliers) - analyzed_count,
            },
            "monitor_task_count_by_status": tasks,
            "pending_review_count": pending_reviews,
            "watchlist": watchlist,
        }

    def upsert_notification_rule(self, record: dict[str, Any]) -> dict[str, Any]:
        risk_level = str(record.get("risk_level", ""))
        notification_type, role_id, channel = (str(record.get("notification_type", "")), str(record.get("role_id", "")), str(record.get("channel", "")))
        if risk_level not in {"RED", "YELLOW", "GREEN"} or notification_type not in {"RISK_ALERT", "REPORT", "TASK_REMINDER"} or channel not in {"WEB", "EMAIL", "WECHAT"}:
            raise ValueError("invalid notification rule")
        with connect(self.database_url) as db:
            if not db.execute("SELECT 1 FROM app_role WHERE role_id=?", (role_id,)).fetchone():
                raise ValueError("unknown role_id")
            row = db.execute("SELECT notification_rule_id FROM notification_rule WHERE risk_level=? AND notification_type=? AND role_id=? AND channel=?", (risk_level, notification_type, role_id, channel)).fetchone()
            rule_id = row["notification_rule_id"] if row else _new_id()
            db.execute("""INSERT INTO notification_rule(notification_rule_id, risk_level, notification_type, role_id, channel, enabled)
                          VALUES (?, ?, ?, ?, ?, ?)
                          ON CONFLICT(risk_level, notification_type, role_id, channel) DO UPDATE SET enabled=excluded.enabled""", (rule_id, risk_level, notification_type, role_id, channel, int(bool(record.get("enabled", True)))))
        return {"notification_rule_id": rule_id, "risk_level": risk_level, "notification_type": notification_type, "role_id": role_id, "channel": channel, "enabled": bool(record.get("enabled", True))}

    def list_notification_rules(self) -> list[dict[str, Any]]:
        with connect(self.database_url) as db:
            return [dict(row) for row in db.execute("SELECT * FROM notification_rule ORDER BY risk_level, notification_type, channel")]

    def list_notifications(self, user_id: str, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        self.get_user(user_id)
        with connect(self.database_url) as db:
            if status:
                rows = db.execute("SELECT * FROM notification WHERE recipient_user_id=? AND status=? ORDER BY created_at DESC LIMIT ?", (user_id, status, limit))
            else:
                rows = db.execute("SELECT * FROM notification WHERE recipient_user_id=? ORDER BY created_at DESC LIMIT ?", (user_id, limit))
            return [dict(row) for row in rows]

    def mark_notification_read(self, notification_id: str, user_id: str) -> dict[str, Any]:
        with connect(self.database_url) as db:
            row = db.execute("SELECT * FROM notification WHERE notification_id=? AND recipient_user_id=?", (notification_id, user_id)).fetchone()
            if row is None:
                raise ValueError("unknown notification or recipient mismatch")
            db.execute("UPDATE notification SET status='READ', read_at=? WHERE notification_id=?", (_iso(_now()), notification_id))
            updated = db.execute("SELECT * FROM notification WHERE notification_id=?", (notification_id,)).fetchone()
        return dict(updated)

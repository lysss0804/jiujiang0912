"""零外部依赖的本地数据库连接与迁移。

生产环境使用 ``db/schema_v1_postgresql.sql`` 的 PostgreSQL 迁移；本模块提供
SQLite 兼容实现，使导入、监控和数据库快照到智能体的联调可以直接运行。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


SQLITE_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS supplier (
  supplier_id TEXT PRIMARY KEY,
  supplier_name TEXT NOT NULL,
  credit_code TEXT NOT NULL UNIQUE,
  importance TEXT NOT NULL CHECK (importance IN ('IMPORTANT', 'GENERAL')),
  responsible_department TEXT,
  status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INACTIVE')),
  current_risk_level TEXT NOT NULL DEFAULT 'GREEN' CHECK (current_risk_level IN ('RED', 'YELLOW', 'GREEN')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS monitor_policy (
  policy_id TEXT PRIMARY KEY,
  policy_name TEXT NOT NULL,
  supplier_level TEXT NOT NULL CHECK (supplier_level IN ('IMPORTANT', 'GENERAL', 'ALL')),
  frequency TEXT NOT NULL CHECK (frequency IN ('WEEKLY', 'MONTHLY', 'QUARTERLY', 'CUSTOM')),
  interval_days INTEGER,
  is_system_default INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (supplier_level, policy_name)
);

CREATE TABLE IF NOT EXISTS supplier_monitor_assignment (
  assignment_id TEXT PRIMARY KEY,
  supplier_id TEXT NOT NULL REFERENCES supplier(supplier_id),
  policy_id TEXT NOT NULL REFERENCES monitor_policy(policy_id),
  frequency_override TEXT CHECK (frequency_override IS NULL OR frequency_override IN ('WEEKLY', 'MONTHLY', 'QUARTERLY', 'CUSTOM')),
  interval_days_override INTEGER,
  next_due_at TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  configured_by TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (supplier_id, policy_id)
);
CREATE INDEX IF NOT EXISTS idx_assignment_due ON supplier_monitor_assignment(next_due_at) WHERE enabled = 1;

CREATE TABLE IF NOT EXISTS monitor_task (
  monitor_id TEXT PRIMARY KEY,
  supplier_id TEXT NOT NULL REFERENCES supplier(supplier_id),
  assignment_id TEXT REFERENCES supplier_monitor_assignment(assignment_id),
  monitor_type TEXT NOT NULL CHECK (monitor_type IN ('PERIODIC', 'EVENT_TRIGGERED', 'MANUAL', 'REASSESSMENT')),
  status TEXT NOT NULL CHECK (status IN ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED')),
  scheduled_at TEXT, started_at TEXT, completed_at TEXT,
  data_version TEXT, error_message TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_monitor_task_supplier ON monitor_task(supplier_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ingestion_batch (
  ingestion_batch_id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL,
  data_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'PARTIAL')),
  total_count INTEGER NOT NULL DEFAULT 0,
  success_count INTEGER NOT NULL DEFAULT 0,
  failed_count INTEGER NOT NULL DEFAULT 0,
  error_message TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS evidence (
  evidence_id TEXT PRIMARY KEY,
  supplier_id TEXT NOT NULL REFERENCES supplier(supplier_id),
  evidence_type TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_record_id TEXT,
  title TEXT,
  content_summary TEXT,
  source_url TEXT,
  event_week INTEGER,
  verified INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS risk_event (
  risk_event_id TEXT PRIMARY KEY,
  supplier_id TEXT NOT NULL REFERENCES supplier(supplier_id),
  ingestion_batch_id TEXT REFERENCES ingestion_batch(ingestion_batch_id),
  risk_dimension TEXT NOT NULL,
  risk_type TEXT NOT NULL,
  severity INTEGER NOT NULL CHECK (severity BETWEEN 0 AND 5),
  risk_description TEXT NOT NULL,
  amount REAL,
  event_week INTEGER NOT NULL CHECK (event_week BETWEEN 1 AND 52),
  source_type TEXT NOT NULL,
  source_record_id TEXT,
  event_status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (event_status IN ('ACTIVE', 'RESOLVED', 'REVOKED')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_risk_event_supplier_week ON risk_event(supplier_id, event_week DESC);

CREATE TABLE IF NOT EXISTS risk_event_evidence (
  risk_event_id TEXT NOT NULL REFERENCES risk_event(risk_event_id),
  evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
  relation_type TEXT NOT NULL DEFAULT 'SUPPORTS',
  PRIMARY KEY (risk_event_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS analysis_run (
  analysis_run_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL UNIQUE,
  supplier_id TEXT NOT NULL REFERENCES supplier(supplier_id),
  monitor_id TEXT REFERENCES monitor_task(monitor_id),
  status TEXT NOT NULL CHECK (status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')),
  agent_version TEXT NOT NULL,
  input_data_version TEXT,
  input_snapshot_json TEXT NOT NULL,
  analysis_route TEXT,
  error_message TEXT,
  started_at TEXT, completed_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS risk_result (
  risk_result_id TEXT PRIMARY KEY,
  analysis_run_id TEXT NOT NULL UNIQUE REFERENCES analysis_run(analysis_run_id),
  supplier_id TEXT NOT NULL REFERENCES supplier(supplier_id),
  monitor_id TEXT REFERENCES monitor_task(monitor_id),
  risk_level TEXT NOT NULL CHECK (risk_level IN ('RED', 'YELLOW', 'GREEN')),
  risk_score REAL NOT NULL,
  window_unit TEXT NOT NULL, window_size INTEGER NOT NULL, window_display TEXT NOT NULL,
  importance_tier TEXT NOT NULL, importance_source TEXT NOT NULL, report_period TEXT NOT NULL,
  dimension_breakdown_json TEXT NOT NULL, risk_summary TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS risk_result_rule (
  risk_result_id TEXT NOT NULL REFERENCES risk_result(risk_result_id),
  rule_id TEXT NOT NULL, rule_description TEXT NOT NULL, risk_dimension TEXT,
  severity INTEGER, weight REAL, is_red_line INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (risk_result_id, rule_id)
);

CREATE TABLE IF NOT EXISTS agent_step_result (
  agent_step_result_id TEXT PRIMARY KEY,
  analysis_run_id TEXT NOT NULL REFERENCES analysis_run(analysis_run_id),
  step_name TEXT NOT NULL, execution_order INTEGER NOT NULL, status TEXT NOT NULL,
  output_json TEXT, error_message TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (analysis_run_id, step_name)
);

CREATE TABLE IF NOT EXISTS risk_report (
  report_id TEXT PRIMARY KEY,
  analysis_run_id TEXT NOT NULL UNIQUE REFERENCES analysis_run(analysis_run_id),
  supplier_id TEXT NOT NULL REFERENCES supplier(supplier_id),
  risk_result_id TEXT NOT NULL REFERENCES risk_result(risk_result_id),
  report_version TEXT NOT NULL, human_review_status TEXT NOT NULL,
  disposition_json TEXT NOT NULL, candidate_actions_json TEXT NOT NULL,
  report_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- External data sources contain configuration only.  Secrets stay in the
-- deployment environment and are referenced by auth_env_var.
CREATE TABLE IF NOT EXISTS source_system (
  source_code TEXT PRIMARY KEY,
  source_name TEXT NOT NULL,
  base_url TEXT NOT NULL,
  endpoint_path TEXT NOT NULL,
  auth_header TEXT NOT NULL DEFAULT 'Authorization',
  auth_env_var TEXT NOT NULL,
  records_path TEXT,
  query_template_json TEXT NOT NULL DEFAULT '{}',
  field_mapping_json TEXT NOT NULL DEFAULT '{}',
  access_mode TEXT NOT NULL DEFAULT 'API' CHECK (access_mode IN ('API','MANUAL_WEB','MOCK')),
  live_http_enabled INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS raw_source_record (
  raw_record_id TEXT PRIMARY KEY,
  source_code TEXT NOT NULL REFERENCES source_system(source_code),
  supplier_id TEXT NOT NULL REFERENCES supplier(supplier_id),
  fetched_at TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_raw_source_supplier ON raw_source_record(supplier_id, fetched_at DESC);

CREATE TABLE IF NOT EXISTS file_object (
  file_id TEXT PRIMARY KEY,
  original_filename TEXT NOT NULL,
  stored_path TEXT NOT NULL,
  content_type TEXT,
  file_size INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  purpose TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_user (
  user_id TEXT PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'DISABLED')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS permission (
  permission_code TEXT PRIMARY KEY,
  permission_name TEXT NOT NULL,
  resource_type TEXT NOT NULL,
  description TEXT
);
CREATE TABLE IF NOT EXISTS app_role (
  role_id TEXT PRIMARY KEY,
  role_code TEXT NOT NULL UNIQUE,
  role_name TEXT NOT NULL,
  level INTEGER NOT NULL CHECK (level BETWEEN 1 AND 100),
  is_system INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS role_permission (
  role_id TEXT NOT NULL REFERENCES app_role(role_id),
  permission_code TEXT NOT NULL REFERENCES permission(permission_code),
  PRIMARY KEY(role_id, permission_code)
);
CREATE TABLE IF NOT EXISTS user_role (
  user_id TEXT NOT NULL REFERENCES app_user(user_id),
  role_id TEXT NOT NULL REFERENCES app_role(role_id),
  PRIMARY KEY(user_id, role_id)
);
CREATE TABLE IF NOT EXISTS user_supplier_scope (
  user_id TEXT NOT NULL REFERENCES app_user(user_id),
  supplier_id TEXT NOT NULL REFERENCES supplier(supplier_id),
  PRIMARY KEY(user_id, supplier_id)
);
CREATE TABLE IF NOT EXISTS human_review_record (
  review_id TEXT PRIMARY KEY,
  report_id TEXT NOT NULL REFERENCES risk_report(report_id),
  reviewer_id TEXT NOT NULL REFERENCES app_user(user_id),
  review_result TEXT NOT NULL CHECK (review_result IN ('APPROVED','REJECTED','NEED_MORE_EVIDENCE','REDECLARED')),
  final_action TEXT, review_comment TEXT, confirmed_risk_level TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS redeclare_request (
  redeclare_request_id TEXT PRIMARY KEY,
  supplier_id TEXT NOT NULL REFERENCES supplier(supplier_id),
  report_id TEXT REFERENCES risk_report(report_id),
  submitted_by TEXT NOT NULL,
  reason TEXT NOT NULL,
  attachment_refs_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','PROCESSING','APPROVED','REJECTED','CANCELLED')),
  review_comment TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS notification_rule (
  notification_rule_id TEXT PRIMARY KEY,
  risk_level TEXT NOT NULL CHECK (risk_level IN ('RED','YELLOW','GREEN')),
  notification_type TEXT NOT NULL CHECK (notification_type IN ('RISK_ALERT','REPORT','TASK_REMINDER')),
  role_id TEXT NOT NULL REFERENCES app_role(role_id), channel TEXT NOT NULL CHECK (channel IN ('WEB','EMAIL','WECHAT')),
  enabled INTEGER NOT NULL DEFAULT 1,
  UNIQUE(risk_level, notification_type, role_id, channel)
);
CREATE TABLE IF NOT EXISTS notification (
  notification_id TEXT PRIMARY KEY,
  recipient_user_id TEXT NOT NULL REFERENCES app_user(user_id),
  supplier_id TEXT REFERENCES supplier(supplier_id),
  report_id TEXT REFERENCES risk_report(report_id),
  notification_type TEXT NOT NULL,
  risk_level TEXT,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  channel TEXT NOT NULL DEFAULT 'WEB',
  status TEXT NOT NULL DEFAULT 'UNREAD' CHECK (status IN ('UNREAD','READ','FAILED')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  read_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_notification_user ON notification(recipient_user_id, status, created_at DESC);
CREATE TABLE IF NOT EXISTS audit_log (
  audit_id TEXT PRIMARY KEY, actor_user_id TEXT, action TEXT NOT NULL, resource_type TEXT NOT NULL,
  resource_id TEXT NOT NULL, before_json TEXT, after_json TEXT, trace_id TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS collection_task (
  collection_task_id TEXT PRIMARY KEY,
  source_code TEXT NOT NULL REFERENCES source_system(source_code),
  supplier_id TEXT NOT NULL REFERENCES supplier(supplier_id),
  query_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','PROCESSING','SUBMITTED','VERIFIED','REJECTED','CANCELLED')),
  assigned_to TEXT REFERENCES app_user(user_id),
  submitted_by TEXT REFERENCES app_user(user_id),
  source_url TEXT,
  file_id TEXT REFERENCES file_object(file_id),
  result_json TEXT,
  result_sha256 TEXT,
  review_comment TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  submitted_at TEXT,
  verified_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_collection_task_status ON collection_task(status, created_at);
"""


def sqlite_path(database_url: str) -> Path:
    if not database_url.startswith("sqlite:///"):
        raise ValueError("local backend supports sqlite:/// URLs only; use PostgreSQL migration in production")
    return Path(database_url.removeprefix("sqlite:///"))


def connect(database_url: str) -> sqlite3.Connection:
    path = sqlite_path(database_url)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(database_url: str) -> None:
    with connect(database_url) as connection:
        connection.executescript(SQLITE_SCHEMA)
        # Additive development migration for databases created before source
        # access modes were introduced. Production uses versioned PostgreSQL
        # migrations instead of runtime ALTER TABLE statements.
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(source_system)")}
        if "access_mode" not in columns:
            connection.execute("ALTER TABLE source_system ADD COLUMN access_mode TEXT NOT NULL DEFAULT 'API'")
        if "live_http_enabled" not in columns:
            connection.execute("ALTER TABLE source_system ADD COLUMN live_http_enabled INTEGER NOT NULL DEFAULT 0")

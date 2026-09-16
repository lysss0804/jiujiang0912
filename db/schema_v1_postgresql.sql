-- 银行外包供应商风险系统：V1 PostgreSQL schema
-- 口径：无预测链路；重要供应商默认每周、一般供应商默认每月；允许单供应商覆盖。

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE app_user (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(50) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    real_name VARCHAR(50) NOT NULL,
    employee_no VARCHAR(50) UNIQUE,
    department VARCHAR(100),
    phone VARCHAR(30),
    email VARCHAR(100),
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','INACTIVE','LOCKED')),
    last_login_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE role (
    role_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role_code VARCHAR(50) NOT NULL UNIQUE,
    role_name VARCHAR(100) NOT NULL,
    description VARCHAR(500),
    level SMALLINT NOT NULL DEFAULT 10 CHECK (level BETWEEN 1 AND 100),
    is_system BOOLEAN NOT NULL DEFAULT FALSE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE permission (
    permission_code VARCHAR(80) PRIMARY KEY,
    permission_name VARCHAR(100) NOT NULL,
    resource_type VARCHAR(50) NOT NULL,
    description VARCHAR(500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE role_permission (
    role_id UUID NOT NULL REFERENCES role(role_id),
    permission_code VARCHAR(80) NOT NULL REFERENCES permission(permission_code),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (role_id, permission_code)
);

CREATE TABLE user_role (
    user_id UUID NOT NULL REFERENCES app_user(user_id),
    role_id UUID NOT NULL REFERENCES role(role_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, role_id)
);

CREATE TABLE supplier (
    supplier_id VARCHAR(32) PRIMARY KEY,
    supplier_name VARCHAR(200) NOT NULL,
    credit_code VARCHAR(32) NOT NULL UNIQUE,
    supplier_type VARCHAR(50),
    importance VARCHAR(20) NOT NULL CHECK (importance IN ('IMPORTANT','GENERAL')),
    responsible_department VARCHAR(100),
    project_owner_id UUID REFERENCES app_user(user_id),
    outsourcing_manager_id UUID REFERENCES app_user(user_id),
    current_risk_level VARCHAR(20) NOT NULL DEFAULT 'GREEN' CHECK (current_risk_level IN ('RED','YELLOW','GREEN')),
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','INACTIVE')),
    address VARCHAR(500), industry VARCHAR(100), legal_representative VARCHAR(100),
    registered_capital NUMERIC(18,2), paid_in_capital NUMERIC(18,2),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 空范围表示可访问所有供应商；写入记录后只可访问列出的供应商。
CREATE TABLE user_supplier_scope (
    user_id UUID NOT NULL REFERENCES app_user(user_id),
    supplier_id VARCHAR(32) NOT NULL REFERENCES supplier(supplier_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, supplier_id)
);

CREATE TABLE monitor_policy (
    policy_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    policy_name VARCHAR(100) NOT NULL,
    supplier_level VARCHAR(20) NOT NULL CHECK (supplier_level IN ('IMPORTANT','GENERAL','ALL')),
    frequency VARCHAR(20) NOT NULL CHECK (frequency IN ('WEEKLY','MONTHLY','QUARTERLY','CUSTOM')),
    interval_days INTEGER CHECK (interval_days IS NULL OR interval_days BETWEEN 1 AND 365),
    is_system_default BOOLEAN NOT NULL DEFAULT FALSE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    effective_from TIMESTAMPTZ NOT NULL DEFAULT now(),
    effective_to TIMESTAMPTZ,
    created_by UUID REFERENCES app_user(user_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((frequency <> 'CUSTOM') OR interval_days IS NOT NULL)
);

CREATE UNIQUE INDEX uq_active_default_monitor_policy
    ON monitor_policy (supplier_level) WHERE is_system_default AND enabled AND effective_to IS NULL;

-- 初始默认策略；用户可新建策略或在 supplier_monitor_assignment 中覆盖周期。
INSERT INTO monitor_policy (policy_name, supplier_level, frequency, is_system_default, enabled)
VALUES
    ('重要供应商默认周度监控', 'IMPORTANT', 'WEEKLY', TRUE, TRUE),
    ('一般供应商默认月度监控', 'GENERAL', 'MONTHLY', TRUE, TRUE);

CREATE TABLE supplier_monitor_assignment (
    assignment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_id VARCHAR(32) NOT NULL REFERENCES supplier(supplier_id),
    policy_id UUID NOT NULL REFERENCES monitor_policy(policy_id),
    frequency_override VARCHAR(20) CHECK (frequency_override IS NULL OR frequency_override IN ('WEEKLY','MONTHLY','QUARTERLY','CUSTOM')),
    interval_days_override INTEGER CHECK (interval_days_override IS NULL OR interval_days_override BETWEEN 1 AND 365),
    next_due_at TIMESTAMPTZ NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    configured_by UUID REFERENCES app_user(user_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (supplier_id, policy_id)
);
CREATE INDEX idx_monitor_assignment_due ON supplier_monitor_assignment (next_due_at) WHERE enabled;

CREATE TABLE monitor_task (
    monitor_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_id VARCHAR(32) NOT NULL REFERENCES supplier(supplier_id),
    assignment_id UUID REFERENCES supplier_monitor_assignment(assignment_id),
    monitor_type VARCHAR(30) NOT NULL CHECK (monitor_type IN ('PERIODIC','EVENT_TRIGGERED','MANUAL','REASSESSMENT')),
    requested_by UUID REFERENCES app_user(user_id),
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','RUNNING','SUCCESS','FAILED','CANCELLED')),
    scheduled_at TIMESTAMPTZ, started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ,
    data_version VARCHAR(80), error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_monitor_task_supplier_created ON monitor_task (supplier_id, created_at DESC);

CREATE TABLE source_system (
    source_system_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_code VARCHAR(50) NOT NULL UNIQUE,
    source_name VARCHAR(100) NOT NULL,
    source_type VARCHAR(30) NOT NULL CHECK (source_type IN ('API','FILE','MANUAL','BANK_SYSTEM','MOCK')),
    access_mode VARCHAR(20) NOT NULL DEFAULT 'API' CHECK (access_mode IN ('API','MANUAL_WEB','MOCK')),
    base_url VARCHAR(500), endpoint_path VARCHAR(500), auth_header VARCHAR(100), auth_env_var VARCHAR(100),
    records_path VARCHAR(300), query_template JSONB NOT NULL DEFAULT '{}'::jsonb, field_mapping JSONB NOT NULL DEFAULT '{}'::jsonb,
    live_http_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ingestion_batch (
    ingestion_batch_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_system_id UUID NOT NULL REFERENCES source_system(source_system_id),
    requested_by UUID REFERENCES app_user(user_id),
    status VARCHAR(20) NOT NULL CHECK (status IN ('PENDING','RUNNING','SUCCESS','FAILED','PARTIAL')),
    data_version VARCHAR(80) NOT NULL,
    started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ,
    total_count INTEGER NOT NULL DEFAULT 0, success_count INTEGER NOT NULL DEFAULT 0, failed_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE file_object (
    file_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    storage_provider VARCHAR(30) NOT NULL,
    object_key VARCHAR(500) NOT NULL UNIQUE,
    original_filename VARCHAR(300), content_type VARCHAR(100), size_bytes BIGINT,
    sha256 CHAR(64) NOT NULL, encryption_key_ref VARCHAR(200),
    uploaded_by UUID REFERENCES app_user(user_id), created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE collection_task (
    collection_task_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_system_id UUID NOT NULL REFERENCES source_system(source_system_id),
    supplier_id VARCHAR(32) NOT NULL REFERENCES supplier(supplier_id),
    query_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','PROCESSING','SUBMITTED','VERIFIED','REJECTED','CANCELLED')),
    assigned_to UUID REFERENCES app_user(user_id), submitted_by UUID REFERENCES app_user(user_id),
    source_url VARCHAR(1000), file_id UUID REFERENCES file_object(file_id), result_json JSONB, result_sha256 CHAR(64),
    review_comment TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), submitted_at TIMESTAMPTZ, verified_at TIMESTAMPTZ
);
CREATE INDEX idx_collection_task_status ON collection_task(status, created_at DESC);

CREATE TABLE raw_source_record (
    raw_record_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ingestion_batch_id UUID NOT NULL REFERENCES ingestion_batch(ingestion_batch_id),
    supplier_id VARCHAR(32) REFERENCES supplier(supplier_id),
    source_record_id VARCHAR(150) NOT NULL,
    raw_payload JSONB, raw_file_id UUID REFERENCES file_object(file_id),
    payload_hash CHAR(64) NOT NULL, fetched_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ingestion_batch_id, source_record_id)
);

CREATE TABLE evidence (
    evidence_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_id VARCHAR(32) NOT NULL REFERENCES supplier(supplier_id),
    raw_record_id UUID REFERENCES raw_source_record(raw_record_id),
    evidence_type VARCHAR(50) NOT NULL,
    source_system_id UUID REFERENCES source_system(source_system_id),
    source_record_id VARCHAR(150), title VARCHAR(300), content_summary TEXT,
    source_url VARCHAR(1000), file_id UUID REFERENCES file_object(file_id),
    event_date DATE, reliability VARCHAR(20) CHECK (reliability IN ('HIGH','MEDIUM','LOW','UNVERIFIED')),
    verified BOOLEAN NOT NULL DEFAULT FALSE, verified_by UUID REFERENCES app_user(user_id), verified_at TIMESTAMPTZ,
    content_hash CHAR(64), created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_evidence_supplier_created ON evidence (supplier_id, created_at DESC);

CREATE TABLE risk_event (
    risk_event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_id VARCHAR(32) NOT NULL REFERENCES supplier(supplier_id),
    ingestion_batch_id UUID REFERENCES ingestion_batch(ingestion_batch_id),
    risk_dimension VARCHAR(30) NOT NULL CHECK (risk_dimension IN ('COMPANY_BACKGROUND','JUDICIAL','DISHONESTY','OPERATING_RISK','OPERATING_STATUS','INTELLECTUAL_PROPERTY')),
    risk_type VARCHAR(100) NOT NULL, severity SMALLINT NOT NULL CHECK (severity BETWEEN 0 AND 5),
    risk_description TEXT NOT NULL, amount NUMERIC(18,2), event_date DATE,
    -- 智能体当前统计窗口按周计算；采集端由 event_date 按银行口径换算并同时保存。
    event_week SMALLINT CHECK (event_week BETWEEN 1 AND 52),
    source_system_id UUID REFERENCES source_system(source_system_id), source_record_id VARCHAR(150),
    is_historical BOOLEAN NOT NULL DEFAULT FALSE, event_status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' CHECK (event_status IN ('ACTIVE','RESOLVED','REVOKED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_risk_event_supplier_date ON risk_event (supplier_id, event_date DESC);
CREATE INDEX idx_risk_event_supplier_dimension ON risk_event (supplier_id, risk_dimension);

CREATE TABLE risk_event_evidence (
    risk_event_id UUID NOT NULL REFERENCES risk_event(risk_event_id),
    evidence_id UUID NOT NULL REFERENCES evidence(evidence_id),
    relation_type VARCHAR(30) NOT NULL DEFAULT 'SUPPORTS' CHECK (relation_type IN ('SUPPORTS','REFUTES','CONTEXT')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (risk_event_id, evidence_id)
);

CREATE TABLE risk_rule_set (
    rule_set_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_version VARCHAR(80) NOT NULL UNIQUE,
    rule_config JSONB NOT NULL, config_hash CHAR(64) NOT NULL,
    effective_from TIMESTAMPTZ NOT NULL, effective_to TIMESTAMPTZ,
    approved_by UUID REFERENCES app_user(user_id), approved_at TIMESTAMPTZ,
    status VARCHAR(20) NOT NULL CHECK (status IN ('DRAFT','APPROVED','RETIRED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE analysis_run (
    analysis_run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id VARCHAR(100) NOT NULL UNIQUE,
    supplier_id VARCHAR(32) NOT NULL REFERENCES supplier(supplier_id),
    monitor_id UUID REFERENCES monitor_task(monitor_id),
    scenario VARCHAR(50) NOT NULL DEFAULT 'MONITORING',
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','RUNNING','SUCCEEDED','FAILED')),
    agent_version VARCHAR(80) NOT NULL, input_data_version VARCHAR(80), input_snapshot JSONB NOT NULL,
    input_snapshot_hash CHAR(64) NOT NULL, analysis_route VARCHAR(40), llm_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    trace_id VARCHAR(100), error_message TEXT, started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_analysis_run_supplier_created ON analysis_run (supplier_id, created_at DESC);

CREATE TABLE risk_result (
    risk_result_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_id VARCHAR(32) NOT NULL REFERENCES supplier(supplier_id),
    monitor_id UUID REFERENCES monitor_task(monitor_id),
    analysis_run_id UUID NOT NULL UNIQUE REFERENCES analysis_run(analysis_run_id),
    rule_set_id UUID NOT NULL REFERENCES risk_rule_set(rule_set_id),
    risk_level VARCHAR(20) NOT NULL CHECK (risk_level IN ('RED','YELLOW','GREEN')),
    risk_score NUMERIC(10,4) NOT NULL, window_unit VARCHAR(10) NOT NULL CHECK (window_unit IN ('week','month')),
    window_size SMALLINT NOT NULL CHECK (window_size BETWEEN 1 AND 52), window_display VARCHAR(100) NOT NULL,
    importance_tier VARCHAR(20) NOT NULL CHECK (importance_tier IN ('IMPORTANT','GENERAL')),
    importance_source VARCHAR(20) NOT NULL CHECK (importance_source IN ('BANK_LIST','DERIVED')),
    report_period VARCHAR(20) NOT NULL CHECK (report_period IN ('WEEKLY','MONTHLY','QUARTERLY')),
    dimension_breakdown JSONB NOT NULL, risk_summary TEXT, evaluated_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_risk_result_supplier_evaluated ON risk_result (supplier_id, evaluated_at DESC);

CREATE TABLE risk_result_rule (
    risk_result_id UUID NOT NULL REFERENCES risk_result(risk_result_id),
    rule_id VARCHAR(100) NOT NULL, rule_description TEXT NOT NULL, risk_dimension VARCHAR(30),
    severity SMALLINT, weight NUMERIC(10,4), is_red_line BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (risk_result_id, rule_id)
);

CREATE TABLE agent_step_result (
    agent_step_result_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_run_id UUID NOT NULL REFERENCES analysis_run(analysis_run_id),
    step_name VARCHAR(50) NOT NULL CHECK (step_name IN ('COORDINATOR','DIMENSION_MAPPING','RISK_IDENTIFICATION','ASSOCIATION_ANALYSIS','EVIDENCE','POLICY_RETRIEVAL','DECISION','CONSISTENCY_CHECK','HUMAN_REVIEW')),
    execution_order SMALLINT NOT NULL, status VARCHAR(20) NOT NULL CHECK (status IN ('SUCCESS','FALLBACK','DISABLED','SKIPPED','FAILED','BLOCKED')),
    model_provider VARCHAR(100), model_name VARCHAR(150), input_json JSONB, output_json JSONB,
    error_message TEXT, started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ,
    UNIQUE (analysis_run_id, step_name)
);

CREATE TABLE policy_document (
    policy_document_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_name VARCHAR(300) NOT NULL, document_version VARCHAR(80) NOT NULL,
    file_id UUID REFERENCES file_object(file_id), status VARCHAR(20) NOT NULL CHECK (status IN ('DRAFT','APPROVED','RETIRED')),
    effective_from DATE, effective_to DATE, approved_by UUID REFERENCES app_user(user_id), approved_at TIMESTAMPTZ,
    content_hash CHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_name, document_version)
);

CREATE TABLE policy_chunk (
    policy_chunk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    policy_document_id UUID NOT NULL REFERENCES policy_document(policy_document_id),
    chunk_no INTEGER NOT NULL, chunk_text TEXT NOT NULL, chunk_hash CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE (policy_document_id, chunk_no)
);

CREATE TABLE policy_index_record (
    policy_chunk_id UUID PRIMARY KEY REFERENCES policy_chunk(policy_chunk_id),
    index_provider VARCHAR(30) NOT NULL CHECK (index_provider IN ('PGVECTOR','EXTERNAL','HASH')),
    index_reference VARCHAR(300) NOT NULL, embedding_model VARCHAR(150), embedding_dimension INTEGER,
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE risk_report (
    report_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_run_id UUID NOT NULL UNIQUE REFERENCES analysis_run(analysis_run_id),
    supplier_id VARCHAR(32) NOT NULL REFERENCES supplier(supplier_id),
    risk_result_id UUID NOT NULL REFERENCES risk_result(risk_result_id),
    report_type VARCHAR(50) NOT NULL DEFAULT 'MONITORING', report_title VARCHAR(300) NOT NULL,
    report_version VARCHAR(80) NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT','FINAL','ARCHIVED')),
    human_review_status VARCHAR(40) NOT NULL CHECK (human_review_status IN ('PENDING_HUMAN_REVIEW','EVIDENCE_INSUFFICIENT','AWAITING_SUPPLIER_REDECLARE')),
    disposition JSONB NOT NULL, candidate_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
    report_json JSONB NOT NULL, rendered_html_file_id UUID REFERENCES file_object(file_id), rendered_pdf_file_id UUID REFERENCES file_object(file_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_risk_report_supplier_created ON risk_report (supplier_id, created_at DESC);

CREATE TABLE report_evidence (
    report_id UUID NOT NULL REFERENCES risk_report(report_id), evidence_id UUID NOT NULL REFERENCES evidence(evidence_id),
    purpose VARCHAR(30) NOT NULL DEFAULT 'RISK_BASIS' CHECK (purpose IN ('RISK_BASIS','TREND_BASIS','DECISION_BASIS')),
    PRIMARY KEY (report_id, evidence_id)
);

CREATE TABLE risk_task (
    risk_task_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_id VARCHAR(32) NOT NULL REFERENCES supplier(supplier_id),
    risk_result_id UUID REFERENCES risk_result(risk_result_id), report_id UUID REFERENCES risk_report(report_id),
    task_type VARCHAR(50) NOT NULL CHECK (task_type IN ('VERIFY','INTERVIEW','RECTIFY','REDECLARE','EXIT_ASSESSMENT','HUMAN_REVIEW')),
    title VARCHAR(300) NOT NULL, description TEXT, assignee_id UUID REFERENCES app_user(user_id), created_by UUID REFERENCES app_user(user_id),
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','PROCESSING','SUBMITTED','COMPLETED','REJECTED','CANCELLED')),
    deadline TIMESTAMPTZ, result TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), completed_at TIMESTAMPTZ
);
CREATE INDEX idx_risk_task_assignee_status ON risk_task (assignee_id, status, deadline);

CREATE TABLE risk_task_history (
    history_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), risk_task_id UUID NOT NULL REFERENCES risk_task(risk_task_id),
    action VARCHAR(50) NOT NULL, from_status VARCHAR(20), to_status VARCHAR(20), comment TEXT,
    operated_by UUID REFERENCES app_user(user_id), created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE task_attachment (
    risk_task_id UUID NOT NULL REFERENCES risk_task(risk_task_id), file_id UUID NOT NULL REFERENCES file_object(file_id),
    attachment_type VARCHAR(40) NOT NULL, uploaded_by UUID REFERENCES app_user(user_id), created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (risk_task_id, file_id)
);

CREATE TABLE human_review_record (
    review_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), report_id UUID NOT NULL REFERENCES risk_report(report_id),
    risk_task_id UUID REFERENCES risk_task(risk_task_id), reviewer_id UUID NOT NULL REFERENCES app_user(user_id),
    review_result VARCHAR(30) NOT NULL CHECK (review_result IN ('APPROVED','REJECTED','NEED_MORE_EVIDENCE','REDECLARED')),
    final_action VARCHAR(30) CHECK (final_action IS NULL OR final_action IN ('OBSERVE','ALERT','RECTIFY')),
    confirmed_risk_level VARCHAR(20) CHECK (confirmed_risk_level IS NULL OR confirmed_risk_level IN ('RED','YELLOW','GREEN')),
    review_comment TEXT, disposition_state VARCHAR(40), created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE redeclare_request (
    redeclare_request_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_id VARCHAR(32) NOT NULL REFERENCES supplier(supplier_id),
    report_id UUID REFERENCES risk_report(report_id), submitted_by VARCHAR(100) NOT NULL,
    reason TEXT NOT NULL, attachment_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','PROCESSING','APPROVED','REJECTED','CANCELLED')),
    review_comment TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE notification_rule (
    notification_rule_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), risk_level VARCHAR(20) NOT NULL CHECK (risk_level IN ('RED','YELLOW','GREEN')),
    notification_type VARCHAR(30) NOT NULL CHECK (notification_type IN ('RISK_ALERT','REPORT','TASK_REMINDER')),
    role_id UUID NOT NULL REFERENCES role(role_id), channel VARCHAR(30) NOT NULL CHECK (channel IN ('WEB','EMAIL','WECHAT')),
    enabled BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (risk_level, notification_type, role_id, channel)
);

CREATE TABLE notification (
    notification_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), supplier_id VARCHAR(32) REFERENCES supplier(supplier_id),
    risk_result_id UUID REFERENCES risk_result(risk_result_id), risk_task_id UUID REFERENCES risk_task(risk_task_id),
    notification_type VARCHAR(30) NOT NULL, risk_level VARCHAR(20), title VARCHAR(300) NOT NULL, content TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','SENDING','SENT','PARTIAL_FAILED','FAILED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), sent_at TIMESTAMPTZ
);

CREATE TABLE notification_recipient_snapshot (
    notification_recipient_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), notification_id UUID NOT NULL REFERENCES notification(notification_id),
    recipient_user_id UUID REFERENCES app_user(user_id), recipient_role_id UUID REFERENCES role(role_id), channel VARCHAR(30) NOT NULL,
    delivery_status VARCHAR(20) NOT NULL DEFAULT 'PENDING' CHECK (delivery_status IN ('PENDING','SENT','FAILED','READ')),
    provider_message_id VARCHAR(150), sent_at TIMESTAMPTZ, read_at TIMESTAMPTZ,
    CHECK (recipient_user_id IS NOT NULL OR recipient_role_id IS NOT NULL)
);

CREATE TABLE outbox_event (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), aggregate_type VARCHAR(50) NOT NULL, aggregate_id VARCHAR(100) NOT NULL,
    event_type VARCHAR(80) NOT NULL, payload JSONB NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','PROCESSING','SENT','FAILED')),
    retry_count INTEGER NOT NULL DEFAULT 0, available_at TIMESTAMPTZ NOT NULL DEFAULT now(), processed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_outbox_dispatch ON outbox_event (status, available_at) WHERE status IN ('PENDING','FAILED');

CREATE TABLE audit_log (
    audit_id UUID PRIMARY KEY DEFAULT gen_random_uuid(), actor_user_id UUID REFERENCES app_user(user_id),
    action VARCHAR(80) NOT NULL, resource_type VARCHAR(80) NOT NULL, resource_id VARCHAR(100) NOT NULL,
    trace_id VARCHAR(100), before_data JSONB, after_data JSONB, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

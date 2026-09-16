# 银行外包供应商风险系统 数据库设计 V1

## 1 设计结论和边界

本设计以 PostgreSQL 为目标，承接当前仓库的规则分级、Multi-Agent、RAG、人工复核和报告输出。当前阶段**不实现未来风险预测**，因此不建 `risk_prediction`、`risk_explanation` 表；后续恢复预测时再单独迁移，不能让未使用的预测字段混入当前报告或处置流程。

默认监控策略为：`重要`供应商按周、`一般`供应商按月。策略由 `monitor_policy` 定义，具体供应商的周期和下次执行时间由 `supplier_monitor_assignment` 保存，允许用户覆盖默认频率。不要把每家供应商的 `next_execute_at` 放在共享策略表中。

系统的权威数据在业务后端：智能体只获得一次不可变的分析快照，返回结构化结论，不能直接连接生产数据库，也不直接写入处置、通知或用户数据。

## 2 核心关系

```text
supplier ──< supplier_monitor_assignment >── monitor_policy
    │                         │
    │                         └──< monitor_task ──< analysis_run ──< agent_step_result
    │                                      │                │
    │                                      │                └── risk_report ──< report_evidence
    │                                      └── risk_result ──< risk_result_rule
    │
    ├──< ingestion_batch ──< raw_source_record ──< evidence
    ├──< risk_event >── risk_event_evidence ──< evidence
    └──< risk_task ──< risk_task_history / task_attachment / human_review_record

policy_document ──< policy_chunk ──< policy_index_record
notification_rule ──< notification ──< notification_recipient_snapshot
```

## 3 表的职责

| 分组 | 表 | 作用 |
| --- | --- | --- |
| 身份与权限 | `app_user`、`role`、`user_role`、`audit_log` | 登录、角色、操作留痕；权限需由服务层按部门及供应商范围判断 |
| 供应商与周期 | `supplier`、`monitor_policy`、`supplier_monitor_assignment`、`monitor_task` | 名单、默认/自定义频率、每次实际监控 |
| 采集与证据 | `source_system`、`ingestion_batch`、`raw_source_record`、`evidence` | 多源导入、原始数据版本、内容哈希、证据核验 |
| 风险事实与分级 | `risk_event`、`risk_event_evidence`、`risk_rule_set`、`risk_result`、`risk_result_rule` | 六维风险事件、证据多对多、规则快照和可复算分级 |
| 智能体与报告 | `analysis_run`、`agent_step_result`、`risk_report`、`report_evidence` | 任务、每个 Agent 输出、完整报告与报告引用的证据 |
| RAG 制度库 | `policy_document`、`policy_chunk`、`policy_index_record` | 制度版本、审批状态、切块、向量索引引用 |
| 人工闭环 | `file_object`、`risk_task`、`risk_task_history`、`task_attachment`、`human_review_record` | 整改、重新申报、附件、审核和再评估依据 |
| 通知与可靠投递 | `notification_rule`、`notification`、`notification_recipient_snapshot`、`outbox_event` | 接收规则、发送记录、收件人快照、事务消息 |

## 4 与智能体契约的落库映射

| 智能体字段 | 落库位置 | 说明 |
| --- | --- | --- |
| `run_id`、`analysis_route`、`audit_trace`、输入快照版本 | `analysis_run` | 一次分析的唯一审计主线 |
| `risk_grade`、分数、窗口、重要性、命中规则 | `risk_result`、`risk_result_rule` | 风险结论是当前风险，不含预测字段 |
| `evidence_summary` | `evidence`、`report_evidence` | 报告仅引用证据，不复制/篡改原始证据 |
| `policy_basis` | `policy_chunk`、报告 JSON | 记录制度版本和 chunk ID；向量索引只作检索，不是权威正文 |
| 各 Agent 结果、LLM 状态 | `agent_step_result` | 保存输入/输出 JSON、耗时、模型和降级状态 |
| `risk_report` | `risk_report` | 保留完整结构化 JSON，常用查询字段拆列 |
| `candidate_actions` | `risk_report.candidate_actions`，人工确认后创建 `risk_task` | AI 建议不等于已执行任务 |
| `disposition`、审核结果 | `risk_report`、`human_review_record`、`risk_task_history` | 不允许直接覆盖历史审核记录 |

## 5 状态与约束

- `monitor_task`: `PENDING` → `RUNNING` → `SUCCESS` / `FAILED` / `CANCELLED`。
- `analysis_run`: `PENDING` → `RUNNING` → `SUCCEEDED` / `FAILED`；重试必须新建 attempt 或记录重试次数。
- `risk_task`: `PENDING` → `PROCESSING` → `SUBMITTED` → `COMPLETED` / `REJECTED` / `CANCELLED`。
- 报告的 `human_review_status` 只允许：`PENDING_HUMAN_REVIEW`、`EVIDENCE_INSUFFICIENT`、`AWAITING_SUPPLIER_REDECLARE`。
- 所有外部来源记录必须保存 `source_system`、`source_record_id`、获取时间和内容哈希；原文或附件保存到对象存储，以 `file_object` 引用。
- 当前智能体以 `event_week` 做窗口计算；生产采集端应同时保存 `event_date` 与按银行口径换算的 `event_week`。数据库枚举采用英文，适配层需把智能体输出的 `重要/一般` 映射为 `IMPORTANT/GENERAL`，把六维度中文名称映射/保存为统一字典值。
- 所有异步通知都通过 `outbox_event` 发送。业务数据和待发送消息在同一事务落库，防止“已更新状态但未通知”或相反的情况。

## 6 建议的后端服务边界

1. 导入/采集服务写入采集、原始记录、证据和风险事件。
2. 调度服务读取到期的 `supplier_monitor_assignment`，创建 `monitor_task` 和 `analysis_run`。
3. 智能体适配器向算法服务传入 `supplier_id`、统计窗口和输入数据版本；返回结构化报告。
4. 结果服务在事务中写入风险结果、报告、审计步骤、待办及 outbox。
5. 审核服务只接受有权限的人工决定；审核通过后才创建或完成整改/核查任务。
6. 通知服务消费 outbox 并记录每位实际收件人的投递与阅读状态。

# 后端基础能力联调说明

本次实现覆盖三个优先项：数据库与导入、周期监控调度、数据库快照驱动的智能体分析。系统暂不实现预测链路。

## 本地启动

默认数据库为 `sqlite:///data/jiujiang_backend.db`，首次调用后自动初始化 SQLite 表。可通过环境变量 `DATABASE_URL` 覆盖。

```powershell
python -m app.main serve
```

生产环境应执行 `db/schema_v1_postgresql.sql` 建立 PostgreSQL 表，不应将 SQLite 用作生产数据库。

## 新增接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| POST | `/backend/suppliers/import` | 批量导入供应商；重要供应商自动分配周度策略，一般供应商自动分配月度策略 |
| POST | `/backend/risk-events/import` | 导入风险事件及对应证据 |
| PUT | `/backend/suppliers/{supplier_id}/monitoring` | 覆盖该供应商默认周期，支持 `WEEKLY`、`MONTHLY`、`QUARTERLY`、`CUSTOM` |
| POST | `/backend/monitoring/dispatch-due` | 创建到期监控任务；`analyze_now=true` 时立即执行智能体分析并持久化结果 |
| GET | `/backend/monitoring/tasks` | 查询监控任务 |
| POST | `/backend/monitoring/tasks/{monitor_id}/analyze` | 基于数据库快照执行一项任务 |
| GET | `/backend/suppliers` | 查询已落库供应商 |

## 最小联调顺序

1. 调用 `/backend/suppliers/import` 导入供应商。
2. 调用 `/backend/risk-events/import` 导入事件和证据。
3. 调用 `/backend/monitoring/dispatch-due`，传 `{ "analyze_now": true, "current_week": 36 }`。
4. 响应中的 `analyses[0].report` 是智能体生成的结构化报告；同一事务已写入 `analysis_run`、`risk_result`、`risk_result_rule`、`agent_step_result`、`risk_report`，并将 `monitor_task` 更新为 `SUCCESS`。

## 数据边界

新增链路使用 `analyze_supplier_snapshot`。后端从数据库构造供应商、风险事件和证据的只读快照，再交给智能体工作流；智能体不连接、不修改业务数据库。

SQLite 仅实现本次联调所需的核心表。完整生产表、对象存储、RAG 制度治理、人工任务、通知与审计要求见 `docs/database_design_v1.md` 和 `db/schema_v1_postgresql.sql`。


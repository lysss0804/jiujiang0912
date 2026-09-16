# SQLite 到 PostgreSQL：这里“切换”是什么意思

当前本地联调使用 SQLite：一个 `data/jiujiang_backend.db` 文件，零安装、适合开发和演示；上传文件也在本地目录。它不适合银行生产环境的并发写入、主从备份、审计保留和权限运维。

切换到 PostgreSQL 不是把文件后缀改成 `.postgresql`，也不能只改 `DATABASE_URL`。它是一次部署迁移：

1. 在生产数据库执行 `db/schema_v1_postgresql.sql`，创建供应商、事件、监控、报告、RBAC、通知和审计表；
2. 为应用配置 PostgreSQL 连接池、TLS、最小权限数据库账号和密钥托管；
3. 把 SQLite 中已确认的供应商、风险事件、监控配置、报告和审核记录校验后导入；
4. 将运行时仓储从当前仅支持 SQLite 的 `RiskRepository` 替换为 PostgreSQL 实现（建议 SQLAlchemy/psycopg + Alembic）；
5. 灰度双写/校验、备份恢复演练后，再切换读写流量。

因此：本仓库已具备 PostgreSQL 的目标表结构，**运行时仓储目前仍只支持 SQLite**。在 PostgreSQL 仓储与迁移作业完成前，不应在线上把 `DATABASE_URL` 直接改成 PostgreSQL 地址。

# 后端新增能力与接入约定

## 真实 LLM 优先、确定性降级

`POST /agent/analyze`、`GET /agent/report/{supplier_id}` 和数据库监控分析接口默认传入 `enable_live_llm=true`。部署需要配置 `.env` 中的模型名、网关地址和密钥；模型不可用时，既有规则引擎仍生成报告，节点状态会标为降级。规则计算、风险等级、处置门槛不能交给模型决定。

## 官方数据源

1. `POST /backend/data-sources` 注册经采购、法务和安全审查批准的 API 配置。密钥字段仅允许填环境变量名（如 `TIANYANCHA_API_TOKEN`），请求体中没有 token 字段。
2. 以 `POST /backend/data-sources/{source_code}/sync/{supplier_id}` 拉取一户数据，原始 JSON 会写入 `raw_source_record`，并计算 SHA-256 供审计。
3. 数据源的 `field_mapping` 将供应商 API 响应字段映射到规范风险事件字段：`risk_dimension`、`risk_type`、`risk_description`、`severity`、`event_week`、`source_record_id` 等。

配置是通用连接器，不会抓取网页。以天眼查为例，实际接口路径、返回路径和映射需根据已购买的开放平台 API 文档和合同配置。

### 无开放 API 网站与零额度 API

数据源有三种运行模式：`API`、`MANUAL_WEB`、`MOCK`。所有新 API 数据源默认 `live_http_enabled=false`，管理员必须通过 `PUT /backend/data-sources/{source_code}/live-http` 显式开启真实 HTTP。

国家企业信用信息公示系统、裁判文书网、商标局、专利局等没有可用开放 API、且可能出现验证码/访问频率限制的网站，登记为 `MANUAL_WEB`。系统通过以下接口形成“建任务—人工查询—提交原始证据—复核”闭环，不提供验证码破解或规避反爬：

- `POST|GET /backend/collection-tasks`
- `POST /backend/collection-tasks/{task_id}/submit`
- `POST /backend/collection-tasks/{task_id}/review`

提交结果保留查询条件、官方页面 URL、操作人、附件引用、提交时间、结构化结果和 SHA-256。只有状态为 `VERIFIED` 的材料才应进一步转换为正式风险事件。

当前天眼查账号无调用额度，因此应登记为 `MOCK`，或保留 `API` 但保持 `live_http_enabled=false`。本地提供的官方字段说明和 JSON 样例只用于 DTO、字段映射和测试，Mock 记录必须显式标注，不能进入生产风险结论。充值并完成采购/合规审批后，才允许配置环境变量密钥并开启真实 HTTP。

## CSV、文件与 Word

- `POST /backend/imports/suppliers/csv?confirm=false|true`
- `POST /backend/imports/risk-events/csv?confirm=false|true`
- `POST /backend/documents/convert?target_format=txt|markdown|html`
- `GET /backend/files/{file_id}`

模板位于 `templates/import/`。上传原文件与转换结果均保存在本地 `UPLOAD_DIR`，其元数据和 SHA-256 记录在 `file_object`。当前内置转换仅为 DOCX；`.doc`、PDF、OFD 等格式应由隔离的 LibreOffice/商业转换服务执行并做恶意文件扫描后接入。

## 可视化与权限边界

`GET /backend/reports/{report_id}/visualization` 返回前端可直接填入 ECharts 的六维雷达指标、风险趋势曲线和汇总卡片数据。图表由前端渲染，后端只提供结构化数据，不输出图片。

补充查询与交付接口：

- `GET /backend/suppliers/{supplier_id}`：供应商详情。
- `GET /backend/suppliers/{supplier_id}/risk-events`：风险事件历史。
- `PATCH /backend/risk-events/{risk_event_id}/status`：关闭、恢复或撤销事件。
- `GET /backend/suppliers/{supplier_id}/reports`：风险报告历史。
- `POST /backend/reports/{report_id}/export?target_format=html|markdown`：按调用人权限裁剪后导出。
- `GET /backend/import-templates/supplier|risk-event`：下载 CSV 模板。
- `GET /backend/notifications`、`PATCH /backend/notifications/{notification_id}/read`：站内通知与已读状态。
- `GET /backend/capabilities`：前端查询 LLM、数据库、RBAC、可视化、导入和转换能力状态，不返回密钥。
- `POST|GET|PATCH /backend/redeclares`：供应商重新申报、查询和审核。
- `GET /backend/dashboard/summary`：首页红黄绿数量、监控任务、待审核数量和重点供应商。

当前 `/agent` 接口只有 `audience=LEADERSHIP|MANAGER` 的**展示裁剪**：普通管理层看不到横向供应商对比。它不是用户身份鉴别或真实 RBAC。生产接入仍必须实现 SSO/JWT、用户-角色-数据范围授权、供应商归属校验和审计日志；在完成前，不应把 `audience` 当成权限依据。

新的 `/backend/reports/{report_id}` 已按真实 RBAC 投影内容：`report.read` 控制报告访问，`report.comparison.read` 控制横向对比，`report.evidence.read` 控制证据明细，`audit.read` 控制运行错误和审计轨迹。前端不能通过传 `audience` 提升权限。

## 可配置 RBAC 与审核、通知接口

新的 RBAC 不再使用前端传入的 `audience` 作为权限。管理员先从 `GET /backend/rbac/permissions` 获取全部可勾选权限，再通过 `POST /backend/rbac/roles` 创建自定义角色（`level` 为 1–100，`permissions` 是勾选结果）；随后创建用户、绑定角色，并按需限定供应商范围：

- `GET|POST /backend/rbac/roles`、`GET /backend/rbac/permissions`
- `GET|POST /backend/rbac/users`
- `PUT /backend/rbac/users/{user_id}/roles`
- `PUT /backend/rbac/users/{user_id}/supplier-scope`
- `GET /backend/rbac/me`、`GET /backend/audit-logs`
- `POST|GET /backend/reviews`、`POST|GET /backend/notification-rules`

本地联调默认 `RBAC_ENFORCED=false`，以便先完成用户和角色初始化。生产环境接入 SSO/JWT 网关并由其验证身份后，将 `RBAC_ENFORCED=true`；届时后端只信任网关注入的 `X-User-Id`，并对已经接入的报告、导入、数据源、监控和上述管理接口校验权限与供应商数据范围。

角色级别用于审批/展示层级排序，**不自动越权**；是否可以调用接口只由已勾选的权限和供应商范围决定。

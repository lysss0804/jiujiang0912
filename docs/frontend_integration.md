# 前端联调说明

当前仓库不包含前端工程。后端提供稳定 HTTP 契约、SQLite 本地数据、Mock 登录和演示账号，前端可直接开始页面联调。

## 启动

```powershell
Copy-Item .env.example .env
python -m pip install -e ".[api,dev]"
python -m app.main serve
```

默认允许 `http://localhost:5173` 和 `http://127.0.0.1:5173` 跨域。Swagger 地址为 `http://127.0.0.1:8000/docs`。

## 初始化联调数据

```http
POST /backend/mock/bootstrap?analyze_now=true&enable_live_llm=true
```

该接口可重复调用，会准备两家演示供应商、三条 Mock 风险事件、三个演示账号，并在首次调用时生成监控任务和报告。`enable_live_llm=true` 会优先调用已配置的真实模型；未配置或调用失败时自动生成确定性报告。

演示账号：

- `demo_admin`：全部权限。
- `demo_manager`：风险经理，只能查看指定供应商，没有横向对比和报告导出权限。
- `demo_leadership`：管理层，可以查看横向对比并导出报告。

## Mock 登录

```http
POST /backend/mock/login
Content-Type: application/json

{"username":"demo_manager"}
```

响应中的 `request_headers.X-User-Id` 是后续请求需要携带的联调身份。`access_token` 只是 Mock 标记，不是 JWT，不能用于生产。

```http
X-User-Id: demo-manager
```

建议前端启动后按以下顺序读取：

1. `GET /backend/capabilities`
2. `POST /backend/mock/login`
3. `GET /backend/rbac/me`
4. `GET /backend/dashboard/summary`
5. `GET /backend/suppliers`
6. `GET /backend/suppliers/{supplier_id}/reports`
7. `GET /backend/reports/{report_id}`
8. `GET /backend/reports/{report_id}/visualization`
9. `GET /backend/reports/{report_id}/agent-trace`
10. `GET /backend/suppliers/{supplier_id}/projects`

## Agent 研判轨迹

```http
GET /backend/reports/{report_id}/agent-trace
X-User-Id: demo-leadership
```

该接口返回六个实际节点：维度归类、风险识别、关联分析、证据、决策建议和一致性校验。
前端应直接使用 `steps[].llm_status`，不要再设置固定默认状态：

- `SUCCESS`：真实模型调用成功；
- `FALLBACK`：模型调用失败或输出不合规，已降级为确定性模板；
- `DISABLED`：本次分析未启用该模型节点；
- `SKIPPED`：前置证据门禁等条件未满足；
- `UNKNOWN`：兼容旧报告，旧记录没有保存该节点状态。

`execution_mode` 取值为 `LIVE / DEGRADED / DETERMINISTIC / MIXED`。每个步骤还返回
真实摘要、证据编号、模型提供方、模型名和持久化节点输出。没有 `audit.read` 权限时
`output/error_message` 会被裁剪；没有 `report.evidence.read` 权限时证据编号会被裁剪。

前端已有供应商 ID 时，先调用 `GET /backend/suppliers/{supplier_id}/reports`，取首条
`report_id`，再请求本接口。若供应商尚未生成持久化报告，应展示“暂无研判轨迹”，
不要使用固定状态补位。

真实模型是否启用由生成该报告时的 `enable_live_llm` 和 `.env` 模型配置共同决定。
已生成的历史报告不会因后来配置模型而变成 `SUCCESS`，需要重新执行分析。

## 供应商项目与关系图

```http
GET /backend/suppliers/{supplier_id}/projects
X-User-Id: demo-leadership
```

响应同时提供：

- `contracts[]`：合同编号、起止周、合同重要性；
- `projects[]`：项目编号、名称、阶段和所属合同；
- `systems[]`：系统编号、名称、等级和所属项目；
- `graph.nodes[] / graph.edges[]`：ECharts graph 可直接使用的节点与关系边。

当前联调数据来自仓库 `data/source`，不会虚构合同金额或日期；数据源没有的字段不返回。
所有 ID 已统一为普通连字符，前端无需再处理 `U+2011`。`data_status=NO_DATA` 时应展示
“暂无项目关系数据”，不要继续显示静态示例。

## 批量导入后的风险状态刷新

供应商刚导入数据库时风险缓存默认是 `GREEN`。风险事件导入完成后，由有
`monitor.run` 权限的用户执行一次：

```http
POST /backend/monitoring/refresh-risk-state
X-User-Id: demo-admin
Content-Type: application/json

{"limit":1000,"enable_live_llm":false,"input_source":"AUTO"}
```

该接口为全部活跃供应商创建一次性 `MANUAL` 分析任务，执行规则引擎并持久化
`risk_result`、报告和供应商最新风险等级，但不会改变原有周/月监控计划。也可以通过
`supplier_ids` 只刷新指定供应商。全量刷新默认不调用真实 LLM，避免批量模型费用；
这不影响规则评分和红黄绿分级。

`input_source=AUTO` 适用于当前联调：数据库已有风险事件时使用数据库快照；如果只有
供应商名单、尚未把事件导入数据库，则对 `S-ACC134` 等仓库样例供应商读取项目提供的
`data/source` 数据。生产环境接入真实风险事件后应传 `input_source=DATABASE`。

`GET /backend/dashboard/summary` 会优先使用每家供应商最新的 `risk_result`，并返回
`supplier_analysis_coverage`。其中 `pending > 0` 表示仍有供应商尚未完成数据库分析，
前端可以展示“统计计算中/数据未覆盖”，不应把它们误解为已确认的低风险。

所有 JSON 业务响应使用统一外壳：

```json
{"code":0,"message":"ok","data":{},"trace_id":"..."}
```

## 联调与生产边界

- `INTEGRATION_MOCK_MODE=true` 只用于开发；`APP_ENV=production` 时 Mock 接口强制拒绝。
- API 适配器保留真实 URL 和字段映射，但 `live_http_enabled=false` 时不会发出真实请求。
- 本地存储为 SQLite 和 `data/uploads`；生产切换 PostgreSQL 和对象存储时保持接口路径与响应结构不变。
- 联调配置同样使用 `RBAC_ENFORCED=true`，以便验证字段裁剪；此时身份来自 Mock 登录。生产环境改为由可信 SSO/JWT 网关注入身份，并删除前端对 Mock 登录的调用。

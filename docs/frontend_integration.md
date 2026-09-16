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

所有 JSON 业务响应使用统一外壳：

```json
{"code":0,"message":"ok","data":{},"trace_id":"..."}
```

## 联调与生产边界

- `INTEGRATION_MOCK_MODE=true` 只用于开发；`APP_ENV=production` 时 Mock 接口强制拒绝。
- API 适配器保留真实 URL 和字段映射，但 `live_http_enabled=false` 时不会发出真实请求。
- 本地存储为 SQLite 和 `data/uploads`；生产切换 PostgreSQL 和对象存储时保持接口路径与响应结构不变。
- 联调配置同样使用 `RBAC_ENFORCED=true`，以便验证字段裁剪；此时身份来自 Mock 登录。生产环境改为由可信 SSO/JWT 网关注入身份，并删除前端对 Mock 登录的调用。

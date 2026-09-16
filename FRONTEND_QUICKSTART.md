# 前端本地联调快速入口

前端人员克隆本仓库后，不需要真实数据库、真实数据源或大模型密钥即可联调。后端默认使用 SQLite、Mock 登录和演示数据；未配置大模型时会自动使用确定性报告模板。

## 1. 环境要求

- Python 3.11 或 3.12
- Git
- 后端默认地址：`http://127.0.0.1:8000`
- Swagger：`http://127.0.0.1:8000/docs`
- 静态接口契约：`openapi.json`

接口发生变化后，后端人员执行 `python scripts/export_openapi.py` 更新静态契约。

## 2. Windows 一键启动

在仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_backend.ps1
```

首次执行会创建 `.venv`、安装依赖、从 `.env.example` 创建 `.env`，随后启动服务。请保持该终端窗口运行。

也可以手动启动：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[api,dev]"
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m app.main serve
```

## 3. 初始化数据与登录

服务启动后，只需初始化一次：

```http
POST /backend/mock/bootstrap?analyze_now=true&enable_live_llm=false
```

Mock 登录：

```http
POST /backend/mock/login
Content-Type: application/json

{"username":"demo_manager"}
```

从响应中读取 `data.request_headers.X-User-Id`，后续请求携带：

```http
X-User-Id: demo-manager
```

可用账号：`demo_admin`、`demo_manager`、`demo_leadership`。建议优先用 `demo_manager` 验证供应商范围和字段脱敏，再用另两个账号验证管理功能。

## 4. 前端环境变量建议

前端工程自行配置，例如 Vite：

```dotenv
VITE_API_BASE_URL=http://127.0.0.1:8000
```

开发服务器默认支持 `localhost:5173` 和 `127.0.0.1:5173` 跨域；如端口不同，请修改后端 `.env` 中的 `API_CORS_ORIGINS` 并重启。

统一响应格式：

```json
{"code":0,"message":"ok","data":{},"trace_id":"..."}
```

## 5. 推荐页面联调顺序

1. `GET /health`
2. `GET /backend/capabilities`
3. `POST /backend/mock/login`
4. `GET /backend/rbac/me`
5. `GET /backend/dashboard/summary`
6. `GET /backend/suppliers`
7. `GET /backend/suppliers/{supplier_id}/reports`
8. `GET /backend/reports/{report_id}`
9. `GET /backend/reports/{report_id}/visualization`

完整接口与权限说明见 `docs/frontend_integration.md` 和 Swagger。

## 6. 当前联调边界

- Mock 登录返回的 token 不是生产 JWT；生产环境需接 SSO/JWT。
- 本地数据存入 `data/jiujiang_backend.db`，上传文件存入 `data/uploads/`，两者均不提交 Git。
- 天眼查和政府平台的真实调用默认关闭，联调使用仓库样例数据。
- PostgreSQL 目标表结构已提供，但当前本地运行仓储仍使用 SQLite。
- 报告可在没有模型密钥时生成；配置合规模型网关后，可切换到真实 LLM 文案生成。

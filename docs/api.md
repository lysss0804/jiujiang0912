# 后端对接接口文档（v0.3.0）

银行外包供应商风险 Multi-Agent 服务 · 供后端联调对接。

- 服务启动：`python -m app.main serve`（等价 `uvicorn app.api.server:app --host 0.0.0.0 --port 8000`）
- 在线调试：启动后访问 `http://127.0.0.1:8000/docs`（Swagger UI）/ `http://127.0.0.1:8000/redoc`
- 本服务**不做未来风险预测**，只输出「当前风险分级 + 六维度归类 + 智能体协同分析 + 结构化报告 + 处置门禁」
- 本服务**不生成 PDF、不推送通知、不管理权限数据库**（属后端职责）；只提供数据与**预留接口**
  （`audience` 角色裁剪、`/agent/redeclare` 重新申报受理）
- **两个「等级」概念区分**：供应商重要性为**两级**（`重要`/`一般`，银行名单定义，仅作分值系数）；
  风险等级为**三级**（`RED`/`YELLOW`/`GREEN`，由分值 + 档位标准判定）。两者互不干扰。

---

## 1. 通用约定

### 1.1 统一响应信封

**所有接口（含错误）** 都返回同一层外壳，body 永远可解析：

```json
{
  "code": 0,
  "message": "ok",
  "data": { },
  "trace_id": "5f2c9a1b8e3d4c7f..."
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `code` | int | 业务错误码，`0` 表示成功 |
| `message` | string | 成功为 `"ok"`，失败为人类可读错误信息 |
| `data` | object \| null | 业务数据；失败时为 `null`（校验失败时为错误明细） |
| `trace_id` | string | 本次调用链 ID，请落到后端日志，便于排查 |

### 1.2 错误码表

| code | HTTP | 含义 | 触发场景 |
| --- | --- | --- | --- |
| `0` | 200 | 成功 | — |
| `40000` | 400 | 请求非法 | 源数据缺失等 `DataValidationError` |
| `40100` | 401 | 未鉴权 | 配置了 `API_KEY` 但未带/带错 `X-API-Key` |
| `40400` | 404 | 资源不存在 | 未知供应商 ID、未知 `task_id` |
| `42200` | 422 | 参数校验失败 | 请求体字段类型/取值不合法，`data.errors` 给出明细 |
| `50000` | 500 | 服务内部错误 | 未预期异常 |

错误响应示例：

```json
{
  "code": 40400,
  "message": "unknown supplier_id: S-NOT-EXIST",
  "data": null,
  "trace_id": "a1b2c3..."
}
```

### 1.3 鉴权

| 配置 `API_KEY` | 行为 |
| --- | --- |
| 空（默认） | **跳过鉴权**，方便本地联调与自动化测试 |
| 非空 | 所有接口必须携带请求头 `X-API-Key: <值>`，否则返回 `40100` |

```http
X-API-Key: your-api-key
```

> 生产环境请务必配置 `API_KEY`，并建议叠加内网网关 / IP 白名单。

### 1.4 供应商 ID 兼容说明（重要）

源数据中的供应商 ID 使用**非断行连字符 U+2011**（形如 `S‑SUD161`），肉眼与普通连字符 `-` 几乎无差别。
服务端已做归一化，**后端直接使用普通 ASCII 连字符即可**：`S-SUD161` 与 `S‑SUD161` 都能命中。

### 1.5 时间与窗口

- `current_week`：业务周次，`1..52`。不传时默认取源数据中的最大周次。
- 风险分级与趋势对照的统计窗口由 `window_unit` / `window_size` 指定；
  不传时等价于**近 12 周**（向后兼容）。
- **按月时 1 月 = 4 周**（`WINDOW_WEEKS_PER_MONTH` 可配），系统内部统一归一化为周区间，
  全链路只有一套口径。

#### 窗口参数（`/agent/analyze`、`/agent/report/{id}`）

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `window_unit` | `week` \| `month` \| null | `null`（取配置） | 统计窗口单位 |
| `window_size` | int \| null | `null`（取配置） | 窗口大小：周数或月数，`1..52` |

归一化结果会写入报告：

| 字段 | 说明 |
| --- | --- |
| `risk_grade.window_unit` | 生效的窗口单位 |
| `risk_grade.window_size` | 生效的窗口大小 |
| `risk_grade.window_weeks` | 归一化后的窗口长度（周） |
| `risk_grade.window_display` | 可读描述，如 `近12周` / `近3个月（第19-30周）` |

> 按月窗口示例：`window_unit=month&window_size=3` → `window_weeks=12`、`window_display="近3个月（第19-30周）"`（假设 `current_week=30`）。

### 1.6 角色下发（两层）

| `audience` | 语义 | 可见内容 |
| --- | --- | --- |
| `LEADERSHIP`（默认） | 高级管理人员 | 汇总报告 + **横向对比数据块** `cross_supplier_comparison` |
| `MANAGER` | 普通管理人员 | 仅汇总报告；`cross_supplier_comparison` 置为 `null`，并返回 `cross_supplier_comparison_withheld` 说明 |

> 本服务只做**数据裁剪**；PDF 生成、通知推送、权限数据库属后端职责。

---

## 2. 接口清单

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查 |
| POST | `/agent/analyze` | 单/批量风险分析（支持异步、窗口、角色） |
| GET | `/agent/task/{task_id}` | 查询异步分析任务结果 |
| GET | `/agent/report/{supplier_id}` | 查询单个供应商结构化报告（支持窗口、角色） |
| GET | `/agent/tools/supplier/{supplier_id}/evidence` | 证据取数（替换为后端真实接口的预留位） |
| GET | `/agent/tools/supplier/{supplier_id}/history` | 历史风险概况（分级 + 趋势 + 维度） |
| POST | `/agent/review` | 人工审核回写 |
| GET | `/agent/reviews` | 查询已回写的人工审核记录（支持处置状态过滤） |
| POST | `/agent/redeclare` | **预留**：供应商重新申报受理 |

---

## 3. 接口详情

### 3.1 `GET /health`

```json
{
  "code": 0,
  "message": "ok",
  "data": { "status": "ok", "service": "jiujiang-multi-agent" },
  "trace_id": "..."
}
```

---

### 3.2 `POST /agent/analyze`

单供应商传 1 个 id；批量传多个或留空（留空 = 全部供应商）。

**请求体**

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `supplier_ids` | string[] | `[]` | 留空则分析全部供应商 |
| `current_week` | int \| null | `null` | 1..52，留空取数据最大周次 |
| `enable_live_llm` | bool | `false` | 是否启用真实大模型（需在线 API Key）；开启后各 LLM 节点按 `LLM_*` 开关逐个调用，失败自动降级 |
| `include_reports` | bool | `true` | 同步批量模式下是否返回全量报告（大数据量建议 `false`） |
| `watchlist_size` | int | `10` | 重点监测名单条数，1..100 |
| `async` | bool | `false` | `true` 且为批量模式时转异步，立即返回 `task_id` |
| `window_unit` | `week` \| `month` \| null | `null` | 统计窗口单位，见 1.5 |
| `window_size` | int \| null | `null` | 统计窗口大小（周数或月数），`1..52` |
| `audience` | `LEADERSHIP` \| `MANAGER` | `LEADERSHIP` | 请求方角色（两层），见 1.6 |

**示例 1｜单供应商（同步）**

```json
{ "supplier_ids": ["S-SUD161"] }
```

响应 `data`：

```json
{
  "mode": "single",
  "report": {
    "report_id": "…",
    "schema_version": "C-DRAFT-V0.3",
    "run_id": "…",
    "supplier_id": "S‑SUD161",
    "supplier_name": "…",
    "current_week": 52,
    "risk_grade": {
      "risk_level": "RED",
      "grade_tier": "RED",
      "grade_label": "高风险",
      "grade_range": "[2.0, +∞)",
      "next_threshold_gap": 0.0,
      "score": 1.989,
      "grade_basis": "…",
      "hit_rules": [ { "rule_id": "RL-SEV-3", "category": "经营风险", "description": "…", "severity": 4, "weight": 1.0 } ],
      "dimension_breakdown": { "经营风险": 1.56, "公司背景": 0.0, "司法": 0.0, "失信": 0.0, "经营状况": 0.0, "知识产权": 0.0 },
      "recent_event_count": 1,
      "window_weeks": 12,
      "window_unit": "week",
      "window_size": 12,
      "window_display": "近12周",
      "importance_tier": "重要",
      "importance_coefficient": 1.2,
      "importance_source": "BANK_LIST",
      "report_period": "weekly",
      "report_period_label": "周报",
      "in_rectify": true
    },
    "dimension_breakdown": [
      { "dimension": "公司背景", "event_count": 0, "score": 0.0, "data_status": "NO_DATA" },
      { "dimension": "司法", "event_count": 0, "score": 0.0, "data_status": "NO_DATA" },
      { "dimension": "失信", "event_count": 0, "score": 0.0, "data_status": "NO_DATA" },
      { "dimension": "经营风险", "event_count": 1, "score": 1.56, "data_status": "DATA" },
      { "dimension": "经营状况", "event_count": 0, "score": 0.0, "data_status": "NO_DATA" },
      { "dimension": "知识产权", "event_count": 0, "score": 0.0, "data_status": "NO_DATA" }
    ],
    "risk_trend": { "trend_type": "SUDDEN_JUMP", "trend_desc": "…" },
    "risk_summary": "…",
    "key_factors": [ { "dimension": "经营风险", "event_count": 1, "subtypes": ["重大数据泄露"] } ],
    "evidence_summary": [ { "evidence_id": "E‑S‑SUD161‑B", "event_category": "经营", "mapped_dimension": "经营风险", "event_subtype": "重大数据泄露", "event_severity": 4, "event_week": 42, "source_type": "安全公告", "readable_summary": "第42周｜经营风险维度｜重大数据泄露（严重程度：严重），来源：安全公告" } ],
    "evidence_by_dimension": { "经营风险": ["E‑S‑SUD161‑B"] },
    "policy_basis": [ { "chunk_id": "…", "document_name": "…", "version": "…", "excerpt": "…" } ],
    "rationale": "…",
    "recommendation": "…",
    "candidate_actions": [ { "suggest_type": "alert", "suggest_content": "…", "suggest_priority": "HIGH", "execution_status": "PENDING_HUMAN_REVIEW" } ],
    "disposition": {
      "delivery_audience": ["LEADERSHIP", "MANAGER"],
      "disposition_state": "AWAITING_SUPPLIER_REDECLARE",
      "can_redeclare": true,
      "reason": "风险等级为中/低档、分值未超上限、无红线且无高严重度事件，允许供应商重新申报"
    },
    "cross_supplier_comparison": null,
    "llm_status": "DISABLED",
    "llm_node_status": {
      "risk_identification": "DISABLED",
      "association_analysis": "DISABLED",
      "evidence": "DISABLED",
      "consistency_check": "DISABLED"
    },
    "consistency_check": {
      "status": "SKIPPED",
      "consistent": true,
      "conflicts": [],
      "evidence_sufficient": true,
      "notes": "…"
    },
    "human_review_status": "PENDING_HUMAN_REVIEW",
    "analysis_route": "HIGH_RISK_DEEP_DIVE",
    "errors": [],
    "audit_trace": [ { "agent_name": "Coordinator", "status": "PASS", "detail": "…", "timestamp": "…" } ]
  }
}
```

> 单供应商响应中 `cross_supplier_comparison` 恒为 `null`（横向对比只在批量结果中产出）。

**示例 2｜同步批量（不返回全量报告）**

```json
{ "supplier_ids": [], "include_reports": false, "watchlist_size": 5 }
```

响应 `data`：

```json
{
  "mode": "batch",
  "current_week": 52,
  "analyzed_count": 200,
  "failed_count": 0,
  "unknown_suppliers": [],
  "level_counts": { "RED": 11, "YELLOW": 14, "GREEN": 175 },
  "report_period_counts": { "weekly": 128, "monthly": 72 },
  "weekly_report_suppliers": ["S‑NOR002", "…"],
  "monthly_report_suppliers": ["S‑NOR001", "…"],
  "redeclare_candidates": ["S‑NOR003", "…"],
  "cross_supplier_comparison": {
    "supplier_count": 200,
    "level_distribution": { "RED": 11, "YELLOW": 14, "GREEN": 175 },
    "dimension_baselines": [
      { "dimension": "公司背景", "median_score": 0.0, "median_event_count": 0.0 },
      { "dimension": "司法", "median_score": 0.0, "median_event_count": 0.0 },
      { "dimension": "失信", "median_score": 0.0, "median_event_count": 0.0 },
      { "dimension": "经营风险", "median_score": 0.0, "median_event_count": 0.0 },
      { "dimension": "经营状况", "median_score": 0.0, "median_event_count": 0.0 },
      { "dimension": "知识产权", "median_score": 0.0, "median_event_count": 0.0 }
    ],
    "rankings": [
      { "rank": 1, "supplier_id": "S‑NOR001", "supplier_name": "…", "importance_tier": "一般", "risk_level": "GREEN", "score": 0.0, "deviation": -0.32 }
    ],
    "median_score": 0.32,
    "note": "本批共200家供应商；分值中位数0.32；横向对比数据仅供人工研判，不参与任何自动处置",
    "generated_at": "2026-09-11T06:25:00+00:00"
  },
  "watchlist": [
    { "supplier_id": "S‑SUD161", "supplier_name": "…", "risk_level": "RED", "score": 1.989, "importance_tier": "重要", "importance_source": "BANK_LIST", "report_period": "weekly", "report_period_label": "周报", "trend_type": "SUDDEN_JUMP", "key_dimensions": ["经营风险"], "recommendation": "…" }
  ],
  "failures": []
}
```

> `include_reports=true` 时额外返回 `reports: [RiskReport, ...]`（全量报告，体积较大）。
> `watchlist` 按 `(风险等级, 得分)` 降序：RED → YELLOW → GREEN，同级内得分高者在前。
> `report_period_counts` 按供应商**重要性**统计下发周期：`weekly`＝重要（周报）、`monthly`＝一般（月报），与风险等级无关。
> `redeclare_candidates` 为处置门禁判定「问题较小、允许重新申报」的供应商名单。
> `cross_supplier_comparison` 为**纯计算**数据块（无模型调用），供后端渲染「横向对比 PDF」；
> `audience=MANAGER` 时该字段为 `null`，并返回 `cross_supplier_comparison_withheld`。

**示例 3｜异步批量**

```json
{ "supplier_ids": [], "async": true, "include_reports": false }
```

响应 `data`：

```json
{
  "task_id": "9c1f7d2a4b8e4f10…",
  "status": "PENDING",
  "polling_url": "/agent/task/9c1f7d2a4b8e4f10…"
}
```

> 建议：全量（数百家以上）或开启 `enable_live_llm` 时使用异步模式，避免 HTTP 超时。

---

### 3.3 `GET /agent/task/{task_id}`

查询异步任务；建议 1~2 秒轮询一次。

响应 `data`：

```json
{
  "task_id": "9c1f7d2a4b8e4f10…",
  "status": "SUCCEEDED",
  "created_at": "2026-09-11T06:25:00+00:00",
  "started_at": "2026-09-11T06:25:00+00:00",
  "finished_at": "2026-09-11T06:25:07+00:00",
  "error": null,
  "result": { "mode": "batch", "analyzed_count": 200, "level_counts": { "RED": 4, "YELLOW": 0, "GREEN": 196 }, "watchlist": [] }
}
```

| `status` | 含义 |
| --- | --- |
| `PENDING` | 已入队，未开始 |
| `RUNNING` | 执行中，`result` 为 `null` |
| `SUCCEEDED` | 成功，`result` 为分析结果（结构同 `/agent/analyze` 的 `data`） |
| `FAILED` | 失败，`error` 含异常摘要 |

> 任务表当前为**进程内内存实现**；服务重启后已触发过的 task_id 会丢失。生产建议换 Redis / DB。

---

### 3.4 `GET /agent/report/{supplier_id}`

**查询参数**

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `current_week` | int \| null | `null` | 1..52，留空取数据最大周次 |
| `window_unit` | `week` \| `month` \| null | `null` | 统计窗口单位，见 1.5 |
| `window_size` | int \| null | `null` | 统计窗口大小，`1..52` |
| `audience` | `LEADERSHIP` \| `MANAGER` | `LEADERSHIP` | 角色，见 1.6 |

响应 `data` 即完整的 `RiskReport` 对象（结构见 3.2 示例 1 的 `report`）。

未知供应商返回 `40400`。

> 报告中的 `disposition` 块给出**分发对象**与**是否可重新申报**：
> - `disposition_state`：`PENDING_HUMAN_REVIEW` / `EVIDENCE_INSUFFICIENT` / `AWAITING_SUPPLIER_REDECLARE`；
> - `can_redeclare`：是否允许供应商重新申报（由确定性门禁判定，模型不参与）；
> - `reason`：判定原因（可直接展示给审核人）。

---

### 3.5 `GET /agent/tools/supplier/{supplier_id}/evidence`

查询参数：`current_week`（可选）。

```json
{
  "supplier_id": "S‑SUD161",
  "current_week": 52,
  "evidence_result": {
    "status": "PASS",
    "evidence_items": [ { "evidence_id": "E‑S‑SUD161‑B", "event_category": "经营", "mapped_dimension": "经营风险", "event_subtype": "重大数据泄露", "event_severity": 4, "event_week": 42, "source_type": "安全公告", "readable_summary": "…" } ],
    "by_dimension": { "经营风险": ["E‑S‑SUD161‑B"] },
    "reasons": [],
    "llm_status": "DISABLED"
  }
}
```

> `status=FAIL` 表示证据不足，此时决策建议 Agent 被强制跳过，报告进入 `EVIDENCE_INSUFFICIENT`。
> `llm_status` 取值 `SUCCESS` / `FALLBACK` / `DISABLED`，表示该节点证据转述文案的来源。

---

### 3.6 `GET /agent/tools/supplier/{supplier_id}/history`

查询参数：`current_week`（可选）。

```json
{
  "supplier_id": "S‑SUD161",
  "current_week": 52,
  "risk_grade": { "risk_level": "RED", "score": 1.989, "…": "…" },
  "risk_trend": { "trend_type": "SUDDEN_JUMP", "trend_desc": "…" },
  "dimension_stats": {
    "counts": { "公司背景": 0, "司法": 0, "失信": 0, "经营风险": 1, "经营状况": 0, "知识产权": 0 },
    "window_weeks": 12,
    "window_display": "近12周",
    "start_week": 41,
    "end_week": 52,
    "no_data_dimensions": ["公司背景", "司法", "失信", "经营状况", "知识产权"]
  }
}
```

---

### 3.7 `POST /agent/review`

人工审核回写。同一 `run_id` 不允许重复提交（重复返回 `40000`）。

**请求体**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `run_id` | string | 是 | 被审核分析的 `run_id`（报告里可拿到） |
| `supplier_id` | string | 是 | 供应商 ID（支持普通连字符） |
| `reviewer_id` | string | 是 | 审核人工号 / 标识 |
| `review_result` | enum | 是 | `APPROVED` / `REJECTED` / `NEED_MORE_EVIDENCE` / `REDECLARED` |
| `final_action` | enum \| null | 条件必填 | `observe` / `alert` / `rectify`；`APPROVED` 时必填 |
| `review_comment` | string | 否 | 审核意见，≤1000 字；`REDECLARED` 时必填 |
| `risk_level` | string \| null | 否 | 审核人确认的风险等级 |
| `disposition_state` | enum \| null | 否 | 处置状态（与报告 `disposition` 对齐），可选 |

```json
{
  "run_id": "0f6c…",
  "supplier_id": "S-SUD161",
  "reviewer_id": "reviewer-001",
  "review_result": "APPROVED",
  "final_action": "rectify",
  "review_comment": "同意列入重点监测并责令整改",
  "risk_level": "RED"
}
```

响应 `data`：

```json
{
  "review_id": "…",
  "schema_version": "C-DRAFT-V0.2",
  "run_id": "0f6c…",
  "supplier_id": "S‑SUD161",
  "reviewer_id": "reviewer-001",
  "review_result": "APPROVED",
  "final_action": "rectify",
  "review_comment": "同意列入重点监测并责令整改",
  "risk_level": "RED",
  "reviewed_at": "2026-09-11T06:30:00+00:00"
}
```

> 当前落盘为本地 JSONL（`REVIEW_OUTPUT_PATH`）；生产替换为事务型数据库适配器（只改 `ReviewFeedbackService`）。

---

### 3.8 `GET /agent/reviews`

查询参数：

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `supplier_id` | string | 无 | 按供应商过滤 |
| `disposition_state` | string | 无 | 按处置状态过滤，如 `AWAITING_SUPPLIER_REDECLARE` |
| `limit` | int | `50` | 返回条数上限，1..500 |

```json
{
  "total": 1,
  "records": [ { "review_id": "…", "supplier_id": "S‑SUD161", "review_result": "APPROVED", "final_action": "rectify", "disposition_state": "AWAITING_SUPPLIER_REDECLARE", "reviewed_at": "…" } ]
}
```

---

### 3.9 `POST /agent/redeclare`（预留）

供应商侧**重新申报**受理端点。

> **是否允许**重新申报由确定性处置门禁在分析与人工复核阶段判定，
> 结果见报告 `disposition.can_redeclare`；本端点只做**登记**：
> 把供应商补充材料写入人工复核队列（`review_result=REDECLARED`），
> 交由后端通知与人工复核处理。

**请求体**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `supplier_id` | string | 是 | 供应商 ID（支持普通连字符） |
| `redeclare_reason` | string | 是 | 重新申报说明，≤1000 字 |
| `attachment_refs` | string[] | 否 | 附件引用（后端上传后的 ID），≤20 个 |
| `submitted_by` | string | 否 | 提交人标识；留空记为 `SUPPLIER` |

```json
{
  "supplier_id": "S-SUD161",
  "redeclare_reason": "已提交补充材料，问题已整改完成",
  "attachment_refs": ["doc-1"],
  "submitted_by": "vendor-contact"
}
```

响应 `data`：落到复核队列的记录（`review_result=REDECLARED`、
`disposition_state=AWAITING_SUPPLIER_REDECLARE`），并回带 `attachment_refs`。

> 重新申报使用独立 `run_id`（`redeclare::<supplier_id>::<时间戳>`），
> 与人工复核记录的去重规则互不干扰。

---

## 4. 对接建议

1. **建表落库**：后端把 `risk_report` 与 `human_review_record` 落库，PDF 由后端模板渲染。
2. **轮询策略**：异步任务建议 1~2 秒轮询，最长不建议超过 10 分钟。
3. **超时设置**：同步批量 200 家约数秒；超过 300 家请改用 `async=true`。
4. **trace 贯通**：把响应 `trace_id` 写入后端日志；也可在请求头传 `X-Trace-Id`，服务端会沿用。
5. **数据来源替换**：`app/data/loader.py` 与 `/agent/tools/supplier/*` 是预留的取数边界，换成调后端真实接口即可，上层 Agent 无需改动。
6. **大模型**：默认 `LLM_PROVIDER=mock` 且 `enable_live_llm=false`，报告文字由规则引擎确定性生成；需真实大模型时配置 `LLM_PROVIDER/LLM_MODEL/LLM_API_KEY/LLM_BASE_URL` 并传 `enable_live_llm=true`。全链路节点行为与降级语义见第 5 节，LLM 不可用时自动降级，报告始终可生成。
7. **PDF 与推送（后端）**：
   - **汇总报告 PDF**：用 `reports[]` 中每家的 `risk_report` 渲染（含等级 + 分值 + 分值标准 + 六维度数据状态 + 处置提示）；
   - **横向对比 PDF**：用批量的 `cross_supplier_comparison` 渲染（等级分布 / 逐维度中位数基线 / 逐家偏差 / 排名）；
   - **按角色分发**：`audience=LEADERSHIP` 时下发两份；`audience=MANAGER` 时只下发汇总报告
     （服务端已把 `cross_supplier_comparison` 置 `null`，后端可直接按字段是否存在来裁剪）；
   - **报告周期**：按供应商重要性下发——`report_period=weekly`（重要）出周报、
     `report_period=monthly`（一般）出月报，批量结果已按 `weekly_report_suppliers` /
     `monthly_report_suppliers` 分组。
8. **重新申报闭环**：报告 `disposition.can_redeclare=true` → 后端通知供应商 →
   供应商调 `POST /agent/redeclare` 提交材料 → 记录进入复核队列
   （`disposition_state=AWAITING_SUPPLIER_REDECLARE`）→ 人工复核。
   是否允许重新申报由**确定性门禁**判定，后端不应自行放宽。

## 5. 大模型行为与降级语义

### 5.1 节点级开关

`enable_live_llm=true` 时，各节点是否调用真实模型由下列开关控制（默认全开）：

| 开关 | 默认 | 作用节点 |
| --- | --- | --- |
| `LLM_DIMENSION_MAPPING` | `true` | 维度归类（旧事件类别 → 新六维度，批量一次请求） |
| `LLM_TEXT_RISK_IDENTIFICATION` | `true` | 风险识别总结 |
| `LLM_TEXT_ASSOCIATION` | `true` | 关联分析趋势解读 / 跨维度说明 |
| `LLM_TEXT_EVIDENCE` | `true` | 证据可读转述（批量一次请求） |
| `LLM_CONSISTENCY_CHECK` | `true` | 多智能体一致性校验 |

> **维度归类降级**：`LLM_DIMENSION_MAPPING=false`、`enable_live_llm=false` 或模型不可用时，
> 全部走确定性 `app/rules/dimension_alias.yaml` 别名表（按事件子类型 → 类别 → 来源 → 兜底优先级）。
> 本地**无数据来源**的维度（司法 / 失信 / 知识产权）在报告中标注 `data_status="NO_DATA"`、
> 分值计 0，**不编造任何事件或证据**。

### 5.2 状态标记

报告顶层 `llm_node_status` 汇总各节点状态；`evidence_result.llm_status` 单独给出证据节点状态；
`consistency_check.status` 给出一致性校验状态（含 `SKIPPED`）。

| 标记 | 含义 |
| --- | --- |
| `SUCCESS` | 模型调用成功并通过结构化校验，报告使用模型文案 |
| `FALLBACK` | 模型不可用/输出不合法/越界引用，已回退确定性模板 |
| `DISABLED` | 节点开关关闭或 `enable_live_llm=false` |
| `SKIPPED` | 前置条件不满足（证据门禁未通过），未调用模型 |

> **契约保证**：任何模型异常都不会使接口失败，最坏情况全部节点为 `FALLBACK`，字段结构与取值域不变。

### 5.3 Grounding 约束

模型只能引用请求 payload 中给出的 `evidence_id` / `chunk_id`。
越界引用会被 `app/agents/helpers.py::require_grounded` 拦截，该节点整体回退模板并标记 `FALLBACK`。

### 5.4 RAG 向量来源

| `vector_source` | 触发条件 |
| --- | --- |
| `HASH` | 未配置 embedding（默认），使用本地 sha256 伪向量，零外部依赖 |
| `EMBEDDING` | 配置了 `EMBEDDING_MODEL`（或复用对话模型且非 mock），调用真实 embedding 接口 |

真实 embedding 调用失败时自动回退 `HASH`，检索不中断。

---

## 6. 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `API_KEY` | 空 | 留空不鉴权；生产必须配置 |
| `API_CORS_ORIGINS` | 空 | 预留（如需浏览器直连可自行开启 CORS） |
| `LLM_PROVIDER` | `mock` | `mock` / `deepseek` / `openai` / `openai-compatible` |
| `LLM_MODEL` | `mock-model` | 模型名 |
| `LLM_API_KEY` | 空 | 在线大模型密钥 |
| `LLM_BASE_URL` | 空 | OpenAI 兼容网关地址 |
| `LLM_TIMEOUT_SECONDS` | `30` | 模型调用超时（秒） |
| `LLM_MAX_RETRIES` | `2` | 模型调用重试次数 |
| `MAX_OUTPUT_CHARS` | `8000` | 单次模型输出字符上限 |
| `LLM_DIMENSION_MAPPING` | `true` | 维度归类节点开关 |
| `LLM_TEXT_RISK_IDENTIFICATION` | `true` | 风险识别节点开关 |
| `LLM_TEXT_ASSOCIATION` | `true` | 关联分析节点开关 |
| `LLM_TEXT_EVIDENCE` | `true` | 证据转述节点开关 |
| `LLM_CONSISTENCY_CHECK` | `true` | 一致性校验节点开关 |
| `WINDOW_UNIT` | `week` | 默认统计窗口单位（`week` / `month`） |
| `WINDOW_SIZE` | `12` | 默认统计窗口大小 |
| `WINDOW_WEEKS_PER_MONTH` | `4` | 1 月 = 4 周（可按银行口径替换） |
| `RECENT_WINDOW_WEEKS` | `12` | 兼容旧口径：未指定窗口时的默认周数 |
| `TREND_BASELINE_WEEKS` | `12` | 趋势对照窗口周数 |
| `REDECLARE_MAX_SCORE` | `0.8` | 重新申报门禁的分值上限（`rules.yaml` 的 `disposition_gate` 优先） |
| `EMBEDDING_PROVIDER` | 空 | 留空则复用 `LLM_PROVIDER` |
| `EMBEDDING_MODEL` | 空 | 留空则复用 `LLM_MODEL`；仍为空则回退哈希向量 |
| `EMBEDDING_BASE_URL` | 空 | 留空则复用 `LLM_BASE_URL` |
| `EMBEDDING_API_KEY` | 空 | 留空则复用 `LLM_API_KEY` |
| `EMBEDDING_DIMENSIONS` | `256` | 哈希向量维度（8..4096） |
| `RAG_TOP_K` | `3` | 政策检索返回片段数 |
| `KNOWLEDGE_DIR` | `data/knowledge` | 制度文档目录 |
| `VECTOR_INDEX_PATH` | `data/index/vector_index.json` | 向量索引落盘路径 |
| `SOURCE_DATA_DIR` | `data/source` | 源数据目录 |
| `REPORT_OUTPUT_DIR` | `artifacts/reports` | Markdown 报告落盘目录 |
| `REVIEW_OUTPUT_PATH` | `artifacts/reviews/review_feedback.jsonl` | 人工审核落盘路径 |
| `BATCH_MAX_WORKERS` | `4` | 批量并发度 |

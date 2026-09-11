# Jiujiang：银行外包供应商风险分级与 Multi-Agent 协同分析

> **作者**：成员 B · 梁雨珊（Algorithm / Multi-Agent 侧）
> **最后更新**：2026-09-11

本仓库是银行外包供应商动态风险监测与决策系统的 **Algorithm / Multi-Agent 侧**实现，
负责把分散的风险数据、规则判定、证据与制度依据组织起来，完成可追溯的风险分析与辅助决策。

> 本服务**不做未来风险预测**（已移除预测模型链路），只做**当前风险分级** + **四智能体协同分析**。
> 输出为结构化报告 JSON 与 Markdown/HTML 模板，PDF 由后端生成。

## 1. 系统在整体项目中的位置

```
供应商数据 → 规则引擎分级 → 维度归类 → Multi-Agent 协同分析 → 结构化报告
        → 处置门禁 →（问题较小：待供应商重新申报 / 其余：人工复核）
        → （后端按角色生成 PDF / 推送 / 权限校验）→ 人工处置
```

本仓库覆盖其中「规则引擎分级 → 维度归类 → Multi-Agent 分析 → 结构化报告 → 处置门禁」诸环。
**PDF 生成、通知推送、权限数据库属传统后端职责，不在本仓库范围**；
本仓库只把所需数据与**预留接口**做好（如 `/agent/redeclare`、`audience` 角色裁剪）。

## 2. 核心能力

| 能力 | 说明 |
| --- | --- |
| 当前风险分级 | 按 `app/rules/rules.yaml` 可配置规则，基于统计窗口内事件、事件严重度、时间衰减、供应商重要性、整改状态，评出 红/黄/绿 |
| 统计窗口可配 | 调用方按**周**或**按月**指定窗口（`window_unit` / `window_size`）；1 月 = 4 周，内部统一归一化为周区间，不传时等价于「近 12 周」 |
| 供应商重要性（两级） | 由银行名单 `suppliers.csv::importance_level` 定义（`重要` / `一般`），系统**只读不算**，仅作风险分系数（1.2 / 1.0） |
| 报告周期分级 | 按重要性下发：**重要 → 周报**，**一般 → 月报**（与风险等级无关） |
| 六维度分析 | 公司背景 / 司法 / 失信 / 经营风险 / 经营状况 / 知识产权（无来源维度标 `NO_DATA`，不编造） |
| 四智能体协同 | 维度归类 → 风险识别 → 关联分析（含趋势研判）→ 证据 → 决策建议 → 一致性校验 |
| 全链路真模型 | 各节点文案可由真实 LLM 生成，异常自动降级为确定性模板（详见第 4 节） |
| 条件路由 | 红色风险走「高风险专项链路」，其余走「常规链路」，体现真正的条件协同 |
| 证据可追溯 | 证据按维度分组，证据编号转人可读描述，禁止引用未来周或跨供应商证据 |
| RAG 政策依据 | 检索已批准制度片段，为结论与建议提供出处 |
| 结构化报告 | 一次成型输出 `risk_report`（10 个分区），并渲染 Markdown / HTML |
| 批量 + 重点名单 | 批量分析全部供应商，按风险高低排序输出「重点监测名单」，并给出周报/月报分组 |
| 横向对比数据块 | 批量产出多供应商同期等级分布、逐维度中位数基线、逐家偏差与排名（**纯计算**，供后端渲染「横向对比 PDF」） |
| 按角色下发 | 两层角色：`LEADERSHIP`（高级管理人员）与 `MANAGER`（普通管理人员）；普通角色不下发横向对比数据 |
| 处置门禁与重新申报 | 确定性判定「问题较小」→ 允许供应商重新申报（`AWAITING_SUPPLIER_REDECLARE`），否则进入人工复核 |
| 人工边界 | Agent 只给候选建议（`PENDING_HUMAN_REVIEW`），不做最终处置 |

> **务必区分两个「等级」**（报告中会同时出现，属正常现象）：
> - **供应商重要性（两级）**：`重要` / `一般`，由银行名单定义，只作风险分系数，**不参与风险等级判定**；
> - **风险等级（三级）**：`RED` / `YELLOW` / `GREEN`，由规则引擎按分值 + 档位标准判定。
>
> 「两级 ≠ 三级」：重要性是**系数**，风险等级是**结论**，两者互不干扰。

## 3. 智能体职责

| Agent | 职责 | 边界 |
| --- | --- | --- |
| Coordinator | 装载校验、解析统计窗口、读取银行名单重要性、调用规则引擎分级、决定分析链路 | 不做自然语言生成 |
| 维度归类 Agent | 把旧事件类别批量归入新六维度（一次模型调用），写入 `mapped_dimension` | 只做语义归类；只做数值搬家，**不重算分值** |
| 风险识别 Agent | 读取分级结果，归纳当前风险等级、主要风险与命中规则 | **不重新判断红黄绿** |
| 关联分析 Agent | 多维度关联分析 + 趋势研判（上升/持平/缓和/突增） | 不做概率预测 |
| 证据 Agent | 关联证据、按维度分组、编号可读化、归属与时间校验 | 不生成建议 |
| 决策建议 Agent | 结合风险/关联/证据/RAG 政策生成总结与建议 | 只给候选建议，不代替人工 |
| 一致性校验 Agent | 交叉复核各智能体结论是否自洽、证据是否充分 | 不改写结论，只标记冲突 |
| 人工复核节点 | 生成报告 + **确定性处置门禁**（分发对象 / 是否可重新申报） | 门禁不调用模型 |

## 4. 大模型的使用边界

### 4.1 确定性优先原则

**不交给 LLM**（全部由规则引擎 / Python 完成）：
阈值判断、风险等级计算（红/黄/绿）、六维度评分与统计、时间窗口过滤、证据归属与时间校验、
趋势类型判定、排序、grounding 校验。

> 一句话：**所有数值、枚举、分级、字段归属都由确定性代码产出；LLM 只负责把这些事实翻译成自然语言。**

### 4.2 全链路模型节点清单

在请求体中传 `enable_live_llm=true` 后，以下节点会调用真实模型生成文案：

| 节点 | 模块 | 模型负责 | 模型不负责（确定性） |
| --- | --- | --- | --- |
| 维度归类 | `app/agents/dimension_mapping.py` | 把每条事件归入新六维度（**一次调用批量返回**） | 归类越界拦截、无来源维度 `NO_DATA` 标注；失败回退 `dimension_alias.yaml` |
| 风险识别 | `app/agents/risk_identification.py` | 风险总结 `summary` | 命中规则、维度统计、主要风险清单、等级 |
| 关联分析 | `app/agents/association_analysis.py` | 趋势解读 `trend_desc`、跨维度说明 `cross_dimension_note` | 趋势类型（上升/持平/缓和/突增）与阈值 |
| 证据转述 | `app/agents/evidence.py` | 证据 `readable_summary`（**一次调用批量返回**） | 证据归属、时间校验、编号合法性 |
| 决策建议 | `app/agents/decision.py` | `rationale`、`recommendation`、候选建议文案 | `suggest_type`、`suggest_priority`、`execution_status` |
| 一致性校验 | `app/agents/consistency_check.py` | 多智能体结论互检 `notes` / `conflicts` | 证据门禁、是否可校验的判定 |
| RAG 检索 | `app/rag/vector_store.py` | 真实 embedding 向量化 | 切分、索引、`approved_only` 过滤、排序 |

> 维度归类节点单节点开关：`LLM_DIMENSION_MAPPING`（默认开启）。关闭或模型不可用时，
> 全部走确定性 `app/rules/dimension_alias.yaml` 别名表，**报告仍可生成**。
>
> 处置门禁（`app/agents/human_review.py::judge_disposition`）**从不调用模型**：
> 「是否问题较小、可否重新申报」完全由 `risk_level` + `score` + 红线 + 最高严重度联合判定。

### 4.3 降级语义（关键）

每个节点都会在报告中写入状态标记，报告顶层汇总在 `llm_node_status`：

| 标记 | 含义 | 触发条件 |
| --- | --- | --- |
| `SUCCESS` | 模型调用成功，报告使用模型文案 | 模型正常返回且通过结构化校验 |
| `FALLBACK` | 已回退确定性模板，报告仍可生成 | 无 Key / 余额不足 / 超时 / 输出不合法 / 越界引用 |
| `DISABLED` | 节点开关关闭或 `enable_live_llm=false` | `LLM_TEXT_*=false` |
| `SKIPPED` | 前置条件不满足，未调用模型 | 证据门禁未通过时的一致性校验 |

**核心保证：任何模型异常都不会让报告生成失败**，最坏情况是全部标记为 `FALLBACK` 并使用确定性模板。

### 4.4 开启真实模型

```powershell
Copy-Item .env.example .env
# 编辑 .env：
#   LLM_PROVIDER=openai-compatible
#   LLM_MODEL=deepseek-chat
#   LLM_API_KEY=<你的密钥>
#   LLM_BASE_URL=https://api.deepseek.com/v1
```

> 上例仅为**通用示例**。本项目实际联调跑通的通道是 **GLM-4-FlashX-250414**
> （`LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4`），详见 `交付演示报告.md` §2。



然后调用接口时传 `"enable_live_llm": true`。也可用单节点开关 `LLM_TEXT_RISK_IDENTIFICATION` /
`LLM_TEXT_ASSOCIATION` / `LLM_TEXT_EVIDENCE` / `LLM_CONSISTENCY_CHECK` 单独控制。

RAG 默认使用本地哈希伪向量（`vector_source="HASH"`）；配置 `EMBEDDING_MODEL`（留空则复用对话模型）
后可切换为真实向量（`vector_source="EMBEDDING"`），失败自动回退哈希。
`EMBEDDING_DIMENSIONS` 同时决定哈希向量维度，由 `build_rag_service` 统一透传。

> **切换 embedding 后务必重建索引**（`python -m app.main rag-demo` 或跑 `RAGService.rebuild`）。
> 索引里的向量维度与查询侧不一致时相似度无法计算，检索质量会退化到「按原顺序返回」。


### 4.5 Grounding 与安全

- 模型输出经 Pydantic 严格校验（`extra="forbid"`），字段不合法直接降级；
- 模型**只能引用输入中提供的** `evidence_id` / `chunk_id`，越界引用被 `require_grounded` 拦截并回退模板；
- 候选建议的 `suggest_type` / `suggest_priority` 枚举被显式校验；**越界给出自动处置动作**
  （如「自动停服」「自动处罚」「自动解约」）会被 `_validate_candidate_actions` 拦截并降级，
  人工边界不只写在提示词里；
- 模型给出的建议优先级会与规则引擎风险等级**对齐**（RED→HIGH、YELLOW→MEDIUM、GREEN→LOW），
  避免高风险供应商标成低优先级误导人工排序；
- `key_factors` 会剔除**提示词占位模板**（如「命中规则 R-XXX：<规则描述>」）与空值，
  并用规则引擎的确定性事实回填，保证报告不出现无信息量的关键因素；
- 密钥仅从环境变量读取，不写入日志与报告。

### 4.6 为什么测试必须打真模型

本项目的测试**故意不全局 mock 大模型**——mock 只能证明「分支写得对不对」，
证明不了「真链路能不能跑通」。真实调用暴露过以下只有真调模型才会出现的问题：

| 问题 | 表现 | 现状态 |
| --- | --- | --- |
| 决策节点用模块级导入工厂 | 测试注入的假客户端被绕过，节点**真实打网**；生产侧也无法替换实现 | 已改为运行时经 `llm_factory.build_llm_client` 解析 |
| 合规约束只写在提示词 | 模型建议「自动停服」时全程无拦截 | 已加 `_validate_candidate_actions` 显式校验 |
| 关键因素抄回占位模板 | 报告出现「命中规则 R-XXX：<规则描述>」 | 已加占位检测 + 规则事实回填 |
| 政策检索恒为空 | 向量维度不一致时 `_dot` 返回 0 分被当作「无结果」过滤，决策永远走 `NO_APPROVED_POLICY` 兜底 | 已改为按分数降序取 top_k |
| `EMBEDDING_DIMENSIONS` 配置失效 | 配置项与文档均存在，但代码写死 256，从未读取 | 已由 `build_rag_service` 透传 |

> 提醒：仓库根目录的 `.env` 会**同时影响测试**（`pydantic-settings` 构造时读取 `.env`，
> 显式传参不覆盖 secret 字段）。因此 `tests/test_llm.py` 中「校验配置解析」的用例
> 在配置了真实 `EMBEDDING_*` 的机器上会与本机配置冲突。这类用例属于
> 「配置解析单测」而非「真链路测试」，两者不要混为一谈。


## 5. 快速启动

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[api,dev]"
Copy-Item .env.example .env

python -m app.main analyze        # 单供应商分析（真实源数据）
python -m app.main batch          # 批量分析 + 重点监测名单 + 横向对比
python -m app.main export-report  # 导出 Markdown 报告
python -m app.main serve          # 启动 FastAPI 联调服务
python -m app.main demo           # mock 数据端到端演示
python -m app.main export-contracts  # 重新导出 JSON Schema（改契约后必跑）
python -m pytest                  # 运行测试
```

## 6. 交付给后端的接口

### 方式一：FastAPI（推荐联调）

```powershell
python -m app.main serve
# 或 uvicorn app.api.server:app --host 0.0.0.0 --port 8000
# 在线调试：http://127.0.0.1:8000/docs
```

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查 |
| POST | `/agent/analyze` | 单/批量分析；`async=true` 时批量转异步返回 `task_id`；支持 `window_unit` / `window_size` / `audience` |
| GET | `/agent/task/{task_id}` | 查询异步分析任务结果 |
| GET | `/agent/report/{supplier_id}` | 查询单个供应商结构化报告；支持 `window_unit` / `window_size` / `audience` 查询参数 |
| GET | `/agent/tools/supplier/{id}/evidence` | 证据取数（便于替换为后端真实接口） |
| GET | `/agent/tools/supplier/{id}/history` | 历史风险概况（分级 + 趋势 + 维度） |
| POST | `/agent/review` | 人工审核回写（可带 `disposition_state`） |
| GET | `/agent/reviews` | 查询已回写的人工审核记录（支持 `disposition_state` 过滤） |
| POST | `/agent/redeclare` | **预留**：供应商重新申报受理（写入复核队列，`review_result=REDECLARED`） |

**统一响应信封**（成功与失败同构，`code=0` 为成功）：

```json
{ "code": 0, "message": "ok", "data": { }, "trace_id": "…" }
```

错误码：`40000` 请求非法 / `40100` 未鉴权 / `40400` 不存在 / `42200` 参数校验失败 / `50000` 内部错误。

**鉴权**：`API_KEY` 未配置时跳过校验（便于联调）；配置后需携带 `X-API-Key` 请求头。

**统计窗口**（不传即保持现状「近 12 周」）：

| 参数 | 取值 | 说明 |
| --- | --- | --- |
| `window_unit` | `week` / `month` | 窗口单位；按月时 1 月 = 4 周（`WINDOW_WEEKS_PER_MONTH` 可配） |
| `window_size` | 1–52 | 窗口大小（周数或月数） |

归一化后会写入报告的 `risk_grade.window_display`（如「近12周」/「近3个月（第19-30周）」）、
`window_weeks`、`window_unit`、`window_size`，全链路统一口径。

**角色下发**（`audience`，两层）：

| 角色 | 可见内容 |
| --- | --- |
| `LEADERSHIP`（高级管理人员，默认） | 汇总报告 + **横向对比数据块**（`cross_supplier_comparison`） |
| `MANAGER`（普通管理人员） | 仅汇总报告；横向对比置为 `null`，并给出 `cross_supplier_comparison_withheld` 说明 |

请求示例：

```json
{
  "supplier_ids": ["S-NOR001", "S-NOR004"],
  "current_week": 52,
  "enable_live_llm": false,
  "include_reports": true,
  "watchlist_size": 10,
  "async": false,
  "window_unit": "month",
  "window_size": 3,
  "audience": "LEADERSHIP"
}
```

批量响应含 `level_counts`、`report_period_counts`（周报/月报家数）、`weekly_report_suppliers`、
`monthly_report_suppliers`、`redeclare_candidates`（待供应商重新申报）、
`cross_supplier_comparison`（等级分布 / 逐维度中位数基线 / 逐家偏差与排名）、
`watchlist`（按风险降序）、`reports`、`failures`。
完整字段说明、示例与错误码见 `docs/api.md`。

> 供应商 ID 兼容：源数据使用非断行连字符 `U+2011`，服务端已归一化，传入普通 `-` 即可命中。

### 方式二：纯函数入口（后端自行包 API）

```python
from app.service import analyze_supplier
from app.batch.service import analyze_batch

state = analyze_supplier(supplier_id="S‑NOR001", current_week=52)
report = state["risk_report"]

batch = analyze_batch(current_week=52)
watchlist = batch["watchlist"]
```

## 7. 结构化报告与 PDF

报告对象 `risk_report` 共 10 个分区：供应商基本信息、当前风险等级、六维度分布、风险趋势、
关键风险因素、风险证据、政策依据、AI 分析结论、AI 处置建议、处置提示与人工审核。

关键展示约定：

- **风险等级区**：同时展示 `score`（规则得分）、`grade_label`（等级名称，三级）与
  `grade_range`（本等级分值标准）；另单列 `window_display`（统计窗口）。
- **供应商重要性（两级）**：单列 `importance_tier`（`重要` / `一般`）与
  `importance_source`（`BANK_LIST` 银行名单 / `DERIVED` 系统推算），与风险等级**分开展示**。
- **六维度分布表**：新增「数据状态」列（`DATA` 有数据 / `NO_DATA` 无数据），
  无数据维度计 0 分并明确标注，报告不编造事件。
- **处置提示块** `disposition`：`delivery_audience`（分发对象）、`disposition_state`
  （处置状态）、`can_redeclare`（是否可重新申报）与 `reason`（原因）。
- **横向对比块** `cross_supplier_comparison`（仅批量、且仅领导层角色）：等级分布、
  逐维度中位数基线、逐家分值与偏差、排名。字段默认 `None`，普通角色返回时置 `null`。

Schema 见 `docs/contracts/risk_report.C-DRAFT-V0.3.schema.json`。

内置渲染器（`app/reporting/renderer.py`）：

```python
from app.reporting.renderer import render_markdown, render_html, save_report
markdown = render_markdown(report)
html = render_html(report)
save_report(report, output_dir="artifacts/reports")  # 落盘 md + html
```

模板文件位于 `templates/report/`（`risk_report.md` / `risk_report.html`），
后端可用任意模板引擎二次开发，或直接用 HTML + 无头浏览器打印 PDF。

## 8. 规则配置（待银行正式规则替换）

所有分级规则外置在 `app/rules/rules.yaml`，可调整：

```yaml
category_weights:      # 六维度权重（公司背景/司法/失信/经营风险/经营状况/知识产权）
severity_weights:      # 严重度分值（0-5）
time_decay:            # 时间衰减（越近权重越高）
importance_coefficients:   # 供应商重要性系数（两级：一般 1.0 / 重要 1.2）
report_period_by_importance:  # 报告周期（重要 → weekly 周报；一般 → monthly 月报）
rectify_coefficient:   # 整改期下调系数
window_unit: week      # 默认统计窗口单位（week / month）
window_size: 12        # 默认窗口大小（12 周 ≈ 近 12 周）
weeks_per_month: 4     # 1 月 = 4 周（可按银行口径替换）
grade_tiers:           # 三级风险等级（从高到低的有序档位）
  - {tier: RED,    label: 高风险, min_score: 2.0, range: "[2.0, +∞)"}
  - {tier: YELLOW, label: 中风险, min_score: 0.8, range: "[0.8, 2.0)"}
  - {tier: GREEN,  label: 低风险, min_score: 0.0, range: "[0, 0.8)"}
tier_rules:            # 多维度兜底开关（dimensions_hit_min_count / enabled）
disposition_gate:      # 重新申报门禁（redeclare_max_score / max_severity_allowed）
red_line_rules:        # 红线规则（命中直接判最高档）
detail_rules:          # 维度细则规则（生成命中明细）
```

**风险等级判定**（`resolve_grade_tier`，三级 + 保留分数）：

1. **红线优先**：命中 `red_line_rules` 直接判 `RED`；
2. **有序取档**：按 `grade_tiers` 顺序取第一个满足 `score >= min_score` 的档；
3. **多维度兜底**（可配）：`tier_rules.enabled=true` 且命中维度数 ≥ `dimensions_hit_min_count` 时提升到中档；
4. 报告同时输出 `score`（原始分值）、`grade_tier`（等级）、`grade_label`（名称）、
   `grade_range`（本等级分值标准）、`next_threshold_gap`（距上一档差额）。

> 档位阈值是**数据不是代码**，随本地数据分布标定（当前 `2.0 / 0.8 / 0.0`），
> 保证三档都能被触发；替换为银行正式规则时只需改本文件。

**供应商重要性判定顺序**（`resolve_importance`）：

1. **银行名单优先**：`suppliers.csv::importance_level` 命中 `重要/一般` 时直接采信，返回 `source=BANK_LIST`，**不做 max 覆盖**；
2. **名单缺失才回退**：用「合同重要性 + 承载系统等级」合成，返回 `source=DERIVED`，合成结果按两级归并（原「核心」并入「重要」）。

**报告周期判定**（`resolve_report_period`）：`重要` → `weekly`（周报）、`一般` → `monthly`（月报）。

**维度归类**（`app/rules/dimension_alias.yaml`）：模型不可用时的确定性降级依据，
把本地旧事件类别（`经营/合规/履约/人员/安全/舆情/知识产权/失信/司法`）与子类型
归入新六维度；未命中的统一回退 `fallback_dimension`。本地**无数据来源**的维度
（司法 / 失信 / 知识产权）在报告中标注 `NO_DATA`、分值计 0，**不编造任何事件或证据**。

**处置门禁**（`disposition_gate`，`is_minor_issue`）：

| 状态 | 触发条件 |
| --- | --- |
| `EVIDENCE_INSUFFICIENT` | 证据门禁未通过（**优先级最高**，不进入重新申报） |
| `AWAITING_SUPPLIER_REDECLARE` | 风险等级为中/低档 **且** `score <= redeclare_max_score` **且** 无红线 **且** 最高严重度 < `max_severity_allowed` |
| `PENDING_HUMAN_REVIEW` | 其余情况，正常进入人工复核 |

拿到银行正式规则后，只需替换该文件，无需修改代码。

## 9. 目录结构

```
app/
├─ agents/       # 四智能体 + 一致性校验 + Coordinator + 人工复核节点
├─ api/          # FastAPI 联调接口
├─ batch/        # 批量分析与重点监测名单
├─ common/       # 日志与通用异常
├─ data/         # 源数据装载与预索引
├─ llm/          # LLM provider、工厂与结构化文案节点（在线 API）
├─ rag/          # 文档加载、切分、索引与检索（支持真实 embedding）
├─ reporting/    # 报告渲染（Markdown / HTML）
├─ review/       # 人工审核服务与 schema
├─ rules/        # 风险分级引擎（engine / config / rules.yaml）
├─ schemas/      # 对外契约与结构化报告
└─ workflow/     # LangGraph 状态与编排（含条件路由）
templates/report/ # 报告模板（供后端套 PDF）
data/source/     # 200 家供应商源数据
docs/contracts/  # 机器可读 JSON Schema
tests/           # 单元与端到端测试
```

## 10. 安全与合规设计

- 分级结果**仅用于报告展示，不参与任何自动处置**；
- Evidence 不通过时 Decision Agent 被强制跳过，流程直接进入人工复核；
- 决策 Agent 输出强制 `execution_status = PENDING_HUMAN_REVIEW`，禁止自动处罚/停服/解约；
- 所有证据必须带 `evidence_id`，且不得引用未来周或跨供应商证据；
- LLM 只能引用输入中提供的 evidence_id / chunk_id，越界引用会被校验拦截并回退模板；
- LLM 输出经结构化严格校验，任何异常都降级为确定性模板（`FALLBACK`），不影响报告生成；
- 日志不记录 API Key；
- 未审核知识条目标记 `is_approved=false`，检索时默认 `approved_only=True`。

## 11. 免责声明

本仓库仅用于校企金融科技专项赛的虚构模拟原型，不包含真实银行客户、合同、账户、密钥或生产业务数据。

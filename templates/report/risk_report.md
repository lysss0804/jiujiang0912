# 供应商风险评估报告：{{ supplier_name }}（{{ supplier_id }}）

- 报告编号：{{ report_id }}
- 评估周次：第 {{ current_week }} 周
- 生成时间：{{ generated_at }}
- 风险等级：**{{ level_label }}**（规则得分 {{ score }}；本等级分值标准 {{ grade_range }}）
- 统计窗口：{{ window_display }}
- 报告周期：**{{ report_period_label }}**（按供应商重要性分级：重要→周报，一般→月报）

> 说明：风险等级仅用于报告展示，不参与任何自动处置；所有处置均需人工确认。
> 注意区分两个「等级」：供应商重要性（两级，由银行名单定义）与风险等级（三级，由分值判定）。

## 一、供应商基本信息

| 项目 | 内容 |
| --- | --- |
| 供应商名称 | {{ supplier_name }} |
| 合同编号 | {{ contract_id }} |
| 外包重要程度 | {{ contract_importance }} |
| 承载系统等级 | {{ system_level }} |
| 承载系统 | {{ system_name }} |
| 项目阶段 | {{ project_stage }} |
| 供应商重要性（两级） | {{ importance_tier }}（来源：{{ importance_source_label }}） |
| 报告周期 | {{ report_period_label }} |
| 是否整改期内 | {{ in_rectify }} |

## 二、当前风险等级

**{{ level_label }}** — 规则得分 {{ score }}　｜　等级名称：{{ grade_label }}　｜　本等级分值标准：{{ grade_range }}

分级依据：{{ grade_basis }}

命中规则：

| 规则编号 | 维度 | 说明 |
| --- | --- | --- |
| {{ rule_id }} | {{ category }} | {{ description }} |

## 三、六维度风险分布

> 六维度为：公司背景 / 司法 / 失信 / 经营风险 / 经营状况 / 知识产权
> （定义见 `app/schemas/contracts.py::RISK_DIMENSIONS`）。
> 「履约 / 人员 / 安全 / 经营 / 合规 / 舆情」为**事件侧旧类别**，
> 由维度归类节点映射到上述六维度，映射表见 `app/rules/dimension_alias.yaml`。

| 维度 | 事件数 | 风险得分 | 数据状态 |
| --- | --- | --- | --- |
| {{ dimension }} | {{ event_count }} | {{ dimension_score }} | {{ data_status }} |

> 「无数据」表示本期该维度无数据来源，计 0 分，系统不编造任何事件或证据。

## 四、风险趋势

趋势：**{{ trend_label }}**

{{ trend_desc }}

关联分析：{{ association_summary }}

## 五、关键风险因素

- **{{ dimension }}**（{{ event_count }}条）：{{ subtypes }}

## 六、风险证据

| 证据编号 | 周次 | 维度 | 事件 | 来源 |
| --- | --- | --- | --- | --- |
| {{ evidence_id }} | {{ event_week }} | {{ mapped_dimension }} | {{ event_subtype }} | {{ source_type }} |

## 七、政策依据

- **{{ document_name }}**（{{ chunk_id }}，版本 {{ version }}）：{{ excerpt }}

## 八、AI 分析结论

{{ risk_summary }}

{{ rationale }}

（{{ llm_status_label }}）

## 九、AI 处置建议

{{ recommendation }}

- [{{ suggest_type }}][{{ suggest_priority }}] {{ suggest_content }}（执行状态：{{ execution_status }}）

## 十、处置提示与人工审核

状态：**{{ human_review_status }}**

- 处置状态：**{{ disposition_state_label }}**
- 建议分发对象：{{ delivery_audience }}
- 是否允许供应商重新申报：{{ can_redeclare }}
- 判定原因：{{ disposition_reason }}

> 处置状态取值：`PENDING_HUMAN_REVIEW`（待人工复核）/ `EVIDENCE_INSUFFICIENT`（证据不足）/
> `AWAITING_SUPPLIER_REDECLARE`（问题较小，待供应商重新申报）。
> 该判定完全确定性（模型不参与），见 `app/agents/human_review.py::judge_disposition`。

本报告所有结论与建议均为辅助研判结果，须经人工复核后方可作为处置依据。

---

<!--
模板占位说明（后端可自行替换渲染引擎，如 Jinja2 / Handlebars）：
本项目已内置 Python 渲染器 app/reporting/renderer.py，可直接调用：
    from app.reporting.renderer import render_markdown, render_html
本模板文件仅作为字段对照与后端二次开发参考。

章节序号以 app/reporting/renderer.py 实际渲染输出为准（共 10 章，Markdown 与 HTML 完全一致）。
-->


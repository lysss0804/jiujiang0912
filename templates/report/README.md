# 报告模板说明

本目录提供风险报告的 Markdown / HTML 模板，供后端直接套用生成 PDF。

## 交付内容

| 文件 | 用途 |
| --- | --- |
| `risk_report.md` | Markdown 模板 + 字段占位对照 |
| `risk_report.html` | HTML 模板（含完整样式，可直接打印为 PDF） |

## 推荐用法

### 方式一：直接调用内置渲染器（推荐）

```python
from app.batch.service import analyze_batch
from app.reporting.renderer import render_html, render_markdown, save_report

result = analyze_batch(supplier_ids=["S‑NOR001"])
report = result["reports"][0]

markdown = render_markdown(report)
html = render_html(report)
save_report(report, output_dir="artifacts/reports")   # 同时落盘 md + html
```

### 方式二：后端自行渲染

后端拿到 `report`（结构化 JSON，schema 见 `docs/contracts/risk_report.C-DRAFT-V0.3.schema.json`），
用任意模板引擎（Jinja2 / Thymeleaf / freemarker / 前端渲染均可）套用本目录模板。

## 报告结构（共 10 个章节）

章节序号与 `app/reporting/renderer.py` 实际渲染输出一一对应，**Markdown 与 HTML 两种输出章节数与序号完全一致**：

1. 供应商基本信息
2. 当前风险等级（红/黄/绿，仅展示，不参与自动处置）
3. 六维度风险分布（公司背景 / 司法 / 失信 / 经营风险 / 经营状况 / 知识产权）
4. 风险趋势（上升/持平/缓和/突增）
5. 关键风险因素
6. 风险证据（可追溯，带证据编号）
7. 政策依据（RAG 检索到的制度条款）
8. AI 分析结论
9. AI 处置建议（仅为候选建议，状态 `PENDING_HUMAN_REVIEW`）
10. 处置提示与人工审核（含 `disposition` 块：处置状态 / 建议分发对象 /
    是否允许供应商重新申报 / 判定原因）

## 与实现的两处口径对齐（重要）

1. **六维度是「公司背景 / 司法 / 失信 / 经营风险 / 经营状况 / 知识产权」**，
   定义在 `app/schemas/contracts.py::RISK_DIMENSIONS`，权重见 `app/rules/rules.yaml::category_weights`。
   旧的「履约 / 人员 / 安全 / 经营 / 合规 / 舆情」只是**事件侧旧类别**，
   由维度归类节点映射到上述六维度（映射表见 `app/rules/dimension_alias.yaml`）。
2. 六个维度中若本期无数据来源，`data_status` 标记为 `NO_DATA` 并计 0 分，报告不编造事件。

## PDF 生成建议

- 用 HTML 模板 + 无头浏览器（Playwright / Puppeteer / wkhtmltopdf）打印为 PDF，样式最可控；
- 或由后端使用 reportlab / iText 直接基于 report JSON 生成。

**注意**：Agent 侧不生成 PDF，PDF 属于后端职责。

"""风险报告渲染：把结构化 report JSON 渲染为 Markdown / HTML。

PDF 由后端基于这两份模板产出，本模块不生成 PDF。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

LEVEL_LABEL = {"RED": "红色（高风险）", "YELLOW": "黄色（中风险）", "GREEN": "绿色（低风险）"}
TREND_LABEL = {
    "RISING": "上升",
    "STEADY": "持平",
    "FALLING": "缓和",
    "SUDDEN_JUMP": "突增",
}
LLM_STATUS_LABEL = {"SUCCESS": "AI 分析已生成", "FALLBACK": "AI 分析降级（规则引擎文案）", "DISABLED": "未启用 AI 分析"}
IMPORTANCE_SOURCE_LABEL = {"BANK_LIST": "银行名单", "DERIVED": "系统推算"}
DATA_STATUS_LABEL = {"DATA": "有数据", "NO_DATA": "无数据"}
DISPOSITION_STATE_LABEL = {
    "PENDING_HUMAN_REVIEW": "待人工复核",
    "EVIDENCE_INSUFFICIENT": "证据不足，需补充材料",
    "AWAITING_SUPPLIER_REDECLARE": "待供应商重新申报",
}
AUDIENCE_LABEL = {"LEADERSHIP": "高级管理人员（领导层）", "MANAGER": "普通管理人员"}


def _level_label(level: str) -> str:
    return LEVEL_LABEL.get(level, level)


def _trend_label(trend: str) -> str:
    return TREND_LABEL.get(trend, trend)


def _importance_source_label(source: str) -> str:
    return IMPORTANCE_SOURCE_LABEL.get(source, source)


def _data_status_label(status: str) -> str:
    return DATA_STATUS_LABEL.get(status, status)


def _disposition_state_label(state: str) -> str:
    return DISPOSITION_STATE_LABEL.get(state, state)


def _audience_label(audience: list[str]) -> str:
    return "、".join(AUDIENCE_LABEL.get(item, item) for item in audience) if audience else "未指定"


def _trend_view(report: dict[str, Any]) -> dict[str, Any]:
    """统一解析「风险趋势」块。

    ``RiskReport`` 是扁平结构，趋势信息在顶层 ``risk_trend`` 子对象里；
    但历史模板与部分下游调用方直接读顶层 ``trend_type`` / ``trend_desc``。
    这里两者都兼容，避免任一口径变化导致趋势正文渲染为空
    （模型生成的 trend_desc 曾被整段丢弃）。
    """
    trend = report.get("risk_trend")
    trend = trend if isinstance(trend, dict) else {}
    return {
        "trend_type": trend.get("trend_type") or report.get("trend_type", ""),
        "trend_desc": trend.get("trend_desc") or report.get("trend_desc", ""),
        "trend_support_evidence_ids": trend.get("trend_support_evidence_ids")
        or report.get("trend_support_evidence_ids", []),
    }


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def _profile_rows(report: dict[str, Any]) -> list[list[Any]]:
    """「供应商基本信息」章节的键值行。

    md 与 html 两个渲染器共用同一份数据源，避免章节内容或字段口径漂移。
    """
    grade = report.get("risk_grade", {})
    profile = report.get("supplier_profile", {})
    return [
        ["供应商名称", profile.get("supplier_name", "")],
        ["合同编号", profile.get("contract_id", "")],
        ["外包重要程度", profile.get("contract_importance", "")],
        ["承载系统等级", profile.get("system_level", "")],
        ["承载系统", profile.get("system_name", "")],
        ["项目阶段", profile.get("project_stage", "")],
        [
            "供应商重要性（两级）",
            f"{grade.get('importance_tier', '一般')}"
            f"（来源：{_importance_source_label(grade.get('importance_source', 'DERIVED'))}）",
        ],
        ["报告周期", grade.get("report_period_label") or "月报"],
        ["是否整改期内", "是" if grade.get("in_rectify") else "否"],
    ]


def render_markdown(report: dict[str, Any]) -> str:
    grade = report.get("risk_grade", {})
    trend = _trend_view(report)
    profile = report.get("supplier_profile", {})

    parts: list[str] = []
    parts.append(f"# 供应商风险评估报告：{report.get('supplier_name') or report.get('supplier_id')}")
    parts.append("")
    parts.append(f"- 报告编号：{report.get('report_id')}")
    parts.append(f"- 供应商编号：{report.get('supplier_id')}")
    parts.append(f"- 评估周次：第 {report.get('current_week')} 周")
    parts.append(f"- 生成时间：{report.get('generated_at')}")
    parts.append(
        f"- 风险等级：**{_level_label(grade.get('risk_level', ''))}**"
        f"（规则得分 {grade.get('score')}；分值标准 {grade.get('grade_range', '')}）"
    )
    parts.append(f"- 统计窗口：{grade.get('window_display', '')}")
    parts.append("")
    parts.append("> 说明：风险等级仅用于报告展示，不参与任何自动处置；所有处置均需人工确认。")
    parts.append("> 注意区分：「风险等级」为三级（RED/YELLOW/GREEN）；「供应商重要性」为两级（重要/一般），仅作分值系数。")
    parts.append("")

    parts.append("## 一、供应商基本信息")
    parts.append("")
    parts.append(_md_table(["项目", "内容"], _profile_rows(report)))
    parts.append("")

    parts.append("## 二、当前风险等级")
    parts.append("")
    parts.append(
        f"**{_level_label(grade.get('risk_level', ''))}** — 规则得分 {grade.get('score')}"
        f"　｜　等级名称：{grade.get('grade_label', '')}"
        f"　｜　本等级分值标准：{grade.get('grade_range', '')}"
    )
    parts.append("")
    if grade.get("next_threshold_gap"):
        parts.append(f"距上一档差额：{grade.get('next_threshold_gap')} 分")
        parts.append("")
    parts.append(f"分级依据：{grade.get('grade_basis', '')}")
    parts.append("")
    hit_rules = grade.get("hit_rules", [])
    if hit_rules:
        parts.append("命中规则：")
        parts.append("")
        parts.append(_md_table(["规则编号", "维度", "说明"], [[r.get("rule_id"), r.get("category"), r.get("description")] for r in hit_rules]))
        parts.append("")

    parts.append("## 三、六维度风险分布")
    parts.append("")
    parts.append(
        _md_table(
            ["维度", "事件数", "风险得分", "数据状态"],
            [
                [
                    item.get("dimension"),
                    item.get("event_count"),
                    item.get("score"),
                    _data_status_label(item.get("data_status", "DATA")),
                ]
                for item in report.get("dimension_breakdown", [])
            ],
        )
    )
    parts.append("")
    parts.append("> 说明：「无数据」表示本期该维度无数据来源，计 0 分，系统不编造任何事件或证据。")
    parts.append("")

    parts.append("## 四、风险趋势")
    parts.append("")
    parts.append(f"趋势：**{_trend_label(trend.get('trend_type', ''))}**")
    parts.append("")
    parts.append(trend.get("trend_desc", ""))
    parts.append("")
    if report.get("association_summary"):
        parts.append(f"关联分析：{report.get('association_summary')}")
        parts.append("")

    parts.append("## 五、关键风险因素")
    parts.append("")
    for factor in report.get("key_factors", []):
        subtypes = "、".join(factor.get("subtypes", []))
        parts.append(f"- **{factor.get('dimension')}**（{factor.get('event_count')}条）：{subtypes}")
    if not report.get("key_factors"):
        parts.append("- 本期未识别到显著关键风险因素")
    parts.append("")

    parts.append("## 六、风险证据")
    parts.append("")
    parts.append(
        _md_table(
            ["证据编号", "周次", "维度", "事件", "来源"],
            [
                [
                    item.get("evidence_id"),
                    item.get("event_week"),
                    item.get("mapped_dimension") or item.get("event_category"),
                    item.get("event_subtype"),
                    item.get("source_type"),
                ]
                for item in report.get("evidence_summary", [])
            ],
        )
        or "未关联到可核验证据。"
    )
    parts.append("")

    parts.append("## 七、政策依据")
    parts.append("")
    policy = report.get("policy_basis", [])
    if policy:
        for item in policy:
            parts.append(f"- **{item.get('document_name')}**（{item.get('chunk_id')}，版本 {item.get('version')}）：{item.get('excerpt')}")
    else:
        parts.append("未检索到已批准的制度依据，建议由业务人员补充。")
    parts.append("")

    parts.append("## 八、AI 分析结论")
    parts.append("")
    parts.append(report.get("risk_summary", ""))
    parts.append("")
    parts.append(report.get("rationale", ""))
    parts.append("")
    parts.append(f"（{LLM_STATUS_LABEL.get(report.get('llm_status', ''), report.get('llm_status', ''))}）")
    parts.append("")

    parts.append("## 九、AI 处置建议")
    parts.append("")
    parts.append(report.get("recommendation", ""))
    parts.append("")
    for action in report.get("candidate_actions", []):
        parts.append(
            f"- [{action.get('suggest_type')}][{action.get('suggest_priority')}] {action.get('suggest_content')}"
            f"（执行状态：{action.get('execution_status')}）"
        )
    parts.append("")

    parts.append("## 十、处置提示与人工审核")
    parts.append("")
    parts.append(f"状态：**{report.get('human_review_status')}**")
    parts.append("")
    disposition = report.get("disposition") or {}
    if disposition:
        parts.append(f"- 处置状态：**{_disposition_state_label(disposition.get('disposition_state', ''))}**")
        parts.append(f"- 建议分发对象：{_audience_label(disposition.get('delivery_audience', []))}")
        parts.append(f"- 是否允许供应商重新申报：{'是' if disposition.get('can_redeclare') else '否'}")
        parts.append(f"- 判定原因：{disposition.get('reason', '')}")
        parts.append("")
    parts.append("本报告所有结论与建议均为辅助研判结果，须经人工复核后方可作为处置依据。")
    return "\n".join(parts)


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>供应商风险评估报告 - {supplier_id}</title>
<style>
  :root {{ --red:#dc2626; --yellow:#d97706; --green:#059669; --ink:#0f172a; --muted:#64748b; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: "Microsoft YaHei", "PingFang SC", system-ui, sans-serif; color: var(--ink);
         margin: 0; padding: 40px; background: #f8fafc; }}
  .sheet {{ max-width: 900px; margin: 0 auto; background: #fff; padding: 48px;
            border-radius: 12px; box-shadow: 0 8px 32px rgba(15,23,42,.08); }}
  h1 {{ font-size: 26px; margin: 0 0 6px; }}
  .sub {{ color: var(--muted); font-size: 13px; margin-bottom: 28px; }}
  h2 {{ font-size: 17px; margin: 32px 0 12px; padding-left: 10px;
        border-left: 4px solid #1d4ed8; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: left; padding: 9px 12px; border-bottom: 1px solid #e2e8f0; vertical-align: top; }}
  th {{ background: #f1f5f9; font-weight: 600; color: #334155; }}
  .badge {{ display: inline-block; padding: 6px 18px; border-radius: 999px;
            color: #fff; font-weight: 700; font-size: 15px; }}
  .badge.RED {{ background: var(--red); }}
  .badge.YELLOW {{ background: var(--yellow); }}
  .badge.GREEN {{ background: var(--green); }}
  .note {{ background: #eff6ff; border-left: 4px solid #3b82f6; padding: 12px 16px;
           font-size: 13px; color: #1e40af; border-radius: 6px; margin: 16px 0; }}
  ul {{ padding-left: 20px; font-size: 14px; line-height: 1.8; }}
  .foot {{ margin-top: 36px; padding-top: 16px; border-top: 1px solid #e2e8f0;
           font-size: 12px; color: var(--muted); }}
</style>
</head>
<body>
<div class="sheet">
  <h1>供应商风险评估报告</h1>
  <div class="sub">{supplier_name}（{supplier_id}）｜评估周次：第 {current_week} 周｜生成时间：{generated_at}</div>

  <h2>一、供应商基本信息</h2>
  {profile_table}

  <h2>二、当前风险等级</h2>
  <p><span class="badge {risk_level}">{level_label}</span>　规则得分：{score}　｜　等级名称：{grade_label}　｜　本等级分值标准：{grade_range}</p>
  <p style="font-size:13px;color:#475569">供应商重要性（两级）：{importance_tier}（{importance_source_label}）　｜　报告周期：{report_period_label}　｜　统计窗口：{window_display}</p>
  <p style="font-size:13px;color:#475569">{grade_basis}</p>
  <div class="note">风险等级为三级（RED/YELLOW/GREEN）；供应商重要性为两级（重要/一般），仅作分值系数。风险等级与所有处置均需人工确认。</div>

  <h2>三、六维度风险分布</h2>
  {dimension_table}

  <h2>四、风险趋势</h2>
  <p><strong>{trend_label}</strong>　{trend_desc}</p>
  <p style="font-size:13px;color:#475569">{association_summary}</p>

  <h2>五、关键风险因素</h2>
  {key_factor_list}

  <h2>六、风险证据</h2>
  {evidence_table}

  <h2>七、政策依据</h2>
  {policy_list}

  <h2>八、AI 分析结论</h2>
  <p>{risk_summary}</p>
  <p style="font-size:13px;color:#475569">{rationale}</p>
  <p style="font-size:12px;color:#94a3b8">{llm_status_label}</p>

  <h2>九、AI 处置建议</h2>
  <p>{recommendation}</p>
  {action_list}

  <h2>十、处置提示与人工审核</h2>
  <p>状态：<strong>{human_review_status}</strong></p>
  {disposition_block}
  <div class="foot">本报告为辅助研判材料，须经人工复核后方可作为处置依据。</div>
</div>
</body>
</html>
"""


def _html_table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{item}</th>" for item in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render_html(report: dict[str, Any]) -> str:
    grade = report.get("risk_grade", {})
    trend = _trend_view(report)

    profile_table = _html_table(["项目", "内容"], _profile_rows(report))
    dimension_table = _html_table(
        ["维度", "事件数", "风险得分", "数据状态"],
        [
            [
                item.get("dimension"),
                item.get("event_count"),
                item.get("score"),
                _data_status_label(item.get("data_status", "DATA")),
            ]
            for item in report.get("dimension_breakdown", [])
        ],
    )
    evidence_table = _html_table(
        ["证据编号", "周次", "维度", "事件", "来源"],
        [
            [
                item.get("evidence_id"),
                item.get("event_week"),
                item.get("mapped_dimension") or item.get("event_category"),
                item.get("event_subtype"),
                item.get("source_type"),
            ]
            for item in report.get("evidence_summary", [])
        ],
    ) or "<p>未关联到可核验证据。</p>"

    key_factor_list = (
        "<ul>"
        + "".join(
            f"<li><strong>{item.get('dimension')}</strong>（{item.get('event_count')}条）：{'、'.join(item.get('subtypes', []))}</li>"
            for item in report.get("key_factors", [])
        )
        + "</ul>"
        if report.get("key_factors")
        else "<p>本期未识别到显著关键风险因素。</p>"
    )

    policy = report.get("policy_basis", [])
    policy_list = (
        "<ul>"
        + "".join(
            f"<li><strong>{item.get('document_name')}</strong>（{item.get('chunk_id')}）：{item.get('excerpt')}</li>"
            for item in policy
        )
        + "</ul>"
        if policy
        else "<p>未检索到已批准的制度依据，建议由业务人员补充。</p>"
    )

    action_list = (
        "<ul>"
        + "".join(
            f"<li>[{item.get('suggest_type')}][{item.get('suggest_priority')}] {item.get('suggest_content')}"
            f"（{item.get('execution_status')}）</li>"
            for item in report.get("candidate_actions", [])
        )
        + "</ul>"
    )

    disposition = report.get("disposition") or {}
    if disposition:
        disposition_block = (
            "<ul>"
            f"<li>处置状态：<strong>{_disposition_state_label(disposition.get('disposition_state', ''))}</strong></li>"
            f"<li>建议分发对象：{_audience_label(disposition.get('delivery_audience', []))}</li>"
            f"<li>是否允许供应商重新申报：{'是' if disposition.get('can_redeclare') else '否'}</li>"
            f"<li>判定原因：{disposition.get('reason', '')}</li>"
            "</ul>"
        )
    else:
        disposition_block = "<p>未生成处置提示。</p>"

    return HTML_TEMPLATE.format(
        supplier_id=report.get("supplier_id", ""),
        supplier_name=report.get("supplier_name", ""),
        current_week=report.get("current_week", ""),
        generated_at=report.get("generated_at", ""),
        risk_level=grade.get("risk_level", "GREEN"),
        level_label=_level_label(grade.get("risk_level", "")),
        score=grade.get("score", 0.0),
        grade_label=grade.get("grade_label", ""),
        grade_range=grade.get("grade_range", ""),
        window_display=grade.get("window_display", ""),
        grade_basis=grade.get("grade_basis", ""),
        importance_tier=grade.get("importance_tier", "一般"),
        importance_source_label=_importance_source_label(grade.get("importance_source", "DERIVED")),
        report_period_label=grade.get("report_period_label") or "月报",
        disposition_block=disposition_block,
        profile_table=profile_table,
        dimension_table=dimension_table,
        trend_label=_trend_label(trend.get("trend_type", "")),
        trend_desc=trend.get("trend_desc", ""),
        association_summary=report.get("association_summary", ""),
        key_factor_list=key_factor_list,
        evidence_table=evidence_table,
        policy_list=policy_list,
        risk_summary=report.get("risk_summary", ""),
        rationale=report.get("rationale", ""),
        llm_status_label=LLM_STATUS_LABEL.get(report.get("llm_status", ""), ""),
        recommendation=report.get("recommendation", ""),
        action_list=action_list,
        human_review_status=report.get("human_review_status", ""),
    )


def save_report(report: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    """同时落盘 Markdown 与 HTML 两种报告模板。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    supplier_id = str(report.get("supplier_id", "unknown"))
    md_path = output_dir / f"risk_report_{supplier_id}.md"
    html_path = output_dir / f"risk_report_{supplier_id}.html"
    md_path.write_text(render_markdown(report), encoding="utf-8")
    html_path.write_text(render_html(report), encoding="utf-8")
    return {"markdown": md_path, "html": html_path}

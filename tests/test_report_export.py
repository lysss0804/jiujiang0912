"""报告生成与模板渲染测试。"""

import re
from pathlib import Path

from app.demo import run_demo
from app.reporting.renderer import render_html, render_markdown, save_report
from app.schemas.report import RiskReport, build_risk_report


def _report() -> dict:
    result = run_demo(current_week=10, include_events=True)
    return result["risk_report"]


def test_report_matches_schema():
    report = _report()
    validated = RiskReport.model_validate(report)
    assert validated.schema_version == "C-DRAFT-V0.3"
    assert validated.risk_grade.risk_level in {"RED", "YELLOW", "GREEN"}


def test_build_report_from_state_is_idempotent():
    result = run_demo(current_week=10, include_events=True)
    rebuilt = build_risk_report(result)
    assert rebuilt["supplier_id"] == result["supplier_id"]
    assert rebuilt["risk_grade"]["risk_level"] == result["risk_grade"]["risk_level"]


def test_markdown_contains_all_sections():
    markdown = render_markdown(_report())
    for section in (
        "一、供应商基本信息",
        "二、当前风险等级",
        "三、六维度风险分布",
        "四、风险趋势",
        "五、关键风险因素",
        "六、风险证据",
        "七、政策依据",
        "八、AI 分析结论",
        "九、AI 处置建议",
        "十、处置提示与人工审核",
    ):
        assert section in markdown


def test_markdown_shows_grade_standard_and_dimension_data_status():
    """报告需同时展示分值、等级与分值标准，并为六维度标注数据状态。"""
    markdown = render_markdown(_report())
    assert "分值标准" in markdown
    assert "数据状态" in markdown
    # 无数据维度应明确标注，不编造事件
    assert "无数据" in markdown or "有数据" in markdown


def test_evidence_uses_mapped_dimension():
    """风险证据表的「维度」列应展示映射后的新维度，而不是原始事件类别。"""
    report = _report()
    markdown = render_markdown(report)
    html = render_html(report)
    for item in report.get("evidence_summary", []):
        mapped = item.get("mapped_dimension")
        if mapped:
            assert mapped in markdown
            assert mapped in html


def test_html_is_well_formed_and_branded():
    html = render_html(_report())
    assert html.startswith("<!DOCTYPE html>")
    assert html.count("<html") == 1
    assert "供应商风险评估报告" in html
    assert "本等级分值标准" in html


def test_save_report_writes_both_formats(tmp_path: Path):
    paths = save_report(_report(), tmp_path)
    assert paths["markdown"].exists()
    assert paths["html"].exists()
    assert paths["markdown"].read_text(encoding="utf-8").strip()


def test_trend_desc_is_rendered_not_dropped():
    """风险趋势正文必须真正渲染出来。

    回归保护：``RiskReport`` 是扁平结构，趋势在顶层 ``risk_trend`` 子对象里；
    渲染器若按错误口径取值（读不存在的顶层 trend_desc），会把模型生成的
    趋势文案整段渲染为空——模型调用成功却白写了。
    """
    report = _report()
    trend_desc = (report.get("risk_trend") or {}).get("trend_desc", "")
    assert trend_desc, "前置条件：报告应包含趋势描述"

    markdown = render_markdown(report)
    html = render_html(report)
    assert trend_desc in markdown, "Markdown 未渲染趋势正文"
    assert trend_desc in html, "HTML 未渲染趋势正文"

    # 章节标题之后应紧跟非空正文，避免「趋势：**缓和**」后面空白
    section = markdown.split("## 四、风险趋势", 1)[1].split("## 五、", 1)[0]
    assert len(section.strip()) > 20


def test_markdown_and_html_section_counts_match():
    """Markdown 与 HTML 的章节数必须一致（当前均为 10 章）。"""
    report = _report()
    md_sections = re.findall(r"^## (.+)$", render_markdown(report), re.M)
    html_sections = re.findall(r"<h2>(.+?)</h2>", render_html(report))

    assert len(md_sections) == 10, md_sections
    assert len(html_sections) == 10, html_sections
    assert md_sections == html_sections, "两种格式章节标题应逐一对齐"


def test_policy_basis_keeps_vector_source():
    """政策依据需保留向量来源，以区分真实语义检索与哈希降级命中。"""
    report = _report()
    for item in report.get("policy_basis", []):
        assert "vector_source" in item
        assert item["vector_source"] in {"embedding", "hash", ""}

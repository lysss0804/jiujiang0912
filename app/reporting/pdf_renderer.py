"""Generate a complete supplier risk report as an in-memory PDF."""

from __future__ import annotations

from html import escape
from io import BytesIO
from math import ceil, cos, pi, sin
from pathlib import Path
from typing import Any, Iterable

from reportlab.graphics.shapes import Circle, Drawing, Line, Polygon, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    LongTable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

SYSTEM_NAME = "银行外包供应商风险监测系统"
DIMENSIONS = ["公司背景", "司法", "失信", "经营风险", "经营状况", "知识产权"]
LEVEL_LABELS = {"RED": "红色（高风险）", "YELLOW": "黄色（中风险）", "GREEN": "绿色（低风险）"}
LEVEL_COLORS = {"RED": colors.HexColor("#DC2626"), "YELLOW": colors.HexColor("#D97706"), "GREEN": colors.HexColor("#059669")}
IMPORTANCE_LABELS = {"IMPORTANT": "重要", "GENERAL": "一般", "重要": "重要", "一般": "一般"}
REVIEW_LABELS = {
    "PENDING_HUMAN_REVIEW": "待人工复核",
    "EVIDENCE_INSUFFICIENT": "证据不足，需补充材料",
    "AWAITING_SUPPLIER_REDECLARE": "待供应商重新申报",
}


def _register_cjk_font() -> str:
    """Use an embedded local CJK font when available, otherwise a PDF CID font."""
    font_name = "JiujiangCJK"
    if font_name in pdfmetrics.getRegisteredFontNames():
        return font_name
    candidates = [
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont(font_name, str(path)))
            return font_name
        except Exception:
            continue
    fallback = "STSong-Light"
    if fallback not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(fallback))
    return fallback


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("‑", "-").replace("–", "-").replace("—", "-")


def _paragraph(value: object, style: ParagraphStyle) -> Paragraph:
    text = escape(_text(value)).replace("\n", "<br/>")
    return Paragraph(text or "-", style)


def _section(title: str, style: ParagraphStyle) -> list[Any]:
    return [Spacer(1, 4 * mm), Paragraph(title, style), Spacer(1, 2.5 * mm)]


def _table(
    rows: list[list[Any]],
    widths: list[float],
    body_style: ParagraphStyle,
    *,
    header: bool = False,
) -> LongTable:
    prepared = [[_paragraph(cell, body_style) for cell in row] for row in rows]
    table = LongTable(prepared, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    commands: list[tuple] = [
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1 if header else 0), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
    ]
    if header:
        commands.extend(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
            ]
        )
    table.setStyle(TableStyle(commands))
    return table


def _radar_chart(dimension_items: Iterable[dict[str, Any]], font_name: str) -> Drawing:
    score_map = {_text(item.get("dimension")): float(item.get("score") or 0) for item in dimension_items}
    scores = [max(0.0, score_map.get(name, 0.0)) for name in DIMENSIONS]
    chart_max = max(3.0, float(ceil(max(scores, default=0.0))))
    width, height = 450, 240
    center_x, center_y, radius = width / 2, height / 2, 78
    drawing = Drawing(width, height)

    for ring in range(1, 5):
        ring_radius = radius * ring / 4
        points: list[float] = []
        for index in range(6):
            angle = pi / 2 - index * 2 * pi / 6
            points.extend([center_x + ring_radius * cos(angle), center_y + ring_radius * sin(angle)])
        drawing.add(Polygon(points, strokeColor=colors.HexColor("#CBD5E1"), fillColor=None, strokeWidth=0.6))

    data_points: list[float] = []
    for index, (label, score) in enumerate(zip(DIMENSIONS, scores)):
        angle = pi / 2 - index * 2 * pi / 6
        axis_x, axis_y = center_x + radius * cos(angle), center_y + radius * sin(angle)
        drawing.add(Line(center_x, center_y, axis_x, axis_y, strokeColor=colors.HexColor("#CBD5E1"), strokeWidth=0.6))
        value_radius = radius * min(score / chart_max, 1.0)
        point_x, point_y = center_x + value_radius * cos(angle), center_y + value_radius * sin(angle)
        data_points.extend([point_x, point_y])
        label_radius = radius + 25
        label_x, label_y = center_x + label_radius * cos(angle), center_y + label_radius * sin(angle)
        drawing.add(String(label_x, label_y, f"{label} {score:g}", fontName=font_name, fontSize=8, textAnchor="middle", fillColor=colors.HexColor("#334155")))

    drawing.add(Polygon(data_points, strokeColor=colors.HexColor("#2563EB"), fillColor=colors.Color(0.15, 0.39, 0.92, alpha=0.22), strokeWidth=1.5))
    for index in range(0, len(data_points), 2):
        drawing.add(Circle(data_points[index], data_points[index + 1], 2.3, fillColor=colors.HexColor("#2563EB"), strokeColor=colors.white, strokeWidth=0.5))
    drawing.add(String(8, 8, f"雷达图刻度上限：{chart_max:g}", fontName=font_name, fontSize=7.5, fillColor=colors.HexColor("#64748B")))
    return drawing


def render_pdf(report: dict[str, Any]) -> bytes:
    """Render one permission-projected report to a complete PDF byte stream."""
    font_name = _register_cjk_font()
    styles = getSampleStyleSheet()
    body = ParagraphStyle("BodyCJK", parent=styles["BodyText"], fontName=font_name, fontSize=9, leading=14, textColor=colors.HexColor("#334155"), wordWrap="CJK")
    small = ParagraphStyle("SmallCJK", parent=body, fontSize=7.8, leading=11)
    title = ParagraphStyle("TitleCJK", parent=styles["Title"], fontName=font_name, fontSize=21, leading=28, alignment=TA_CENTER, textColor=colors.HexColor("#0F172A"))
    subtitle = ParagraphStyle("SubtitleCJK", parent=body, fontSize=9, alignment=TA_CENTER, textColor=colors.HexColor("#64748B"))
    section = ParagraphStyle("SectionCJK", parent=styles["Heading2"], fontName=font_name, fontSize=13, leading=18, textColor=colors.HexColor("#1D4ED8"), spaceBefore=3, spaceAfter=2)
    label = ParagraphStyle("LabelCJK", parent=body, fontSize=8.5, textColor=colors.HexColor("#475569"))
    risk_badge = ParagraphStyle("RiskBadgeCJK", parent=body, fontSize=9, leading=14, textColor=colors.white)

    report_id = _text(report.get("report_id"))
    supplier_id = _text(report.get("supplier_id"))
    supplier_name = _text(report.get("supplier_name") or (report.get("supplier_profile") or {}).get("supplier_name") or supplier_id)
    generated_at = _text(report.get("generated_at"))
    grade = report.get("risk_grade") if isinstance(report.get("risk_grade"), dict) else {}
    profile = report.get("supplier_profile") if isinstance(report.get("supplier_profile"), dict) else {}
    level = _text(grade.get("risk_level") or "GREEN")
    importance = IMPORTANCE_LABELS.get(_text(grade.get("importance_tier") or profile.get("importance_level")), _text(grade.get("importance_tier") or profile.get("importance_level") or "一般"))
    dimensions = report.get("dimension_breakdown") if isinstance(report.get("dimension_breakdown"), list) else []
    evidence = report.get("evidence_summary") if isinstance(report.get("evidence_summary"), list) else []
    actions = report.get("candidate_actions") if isinstance(report.get("candidate_actions"), list) else []

    buffer = BytesIO()

    def footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont(font_name, 7.5)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(18 * mm, 10 * mm, f"{SYSTEM_NAME} · 报告编号 {report_id}")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"第 {doc.page} 页")
        canvas.setStrokeColor(colors.HexColor("#CBD5E1"))
        canvas.line(18 * mm, 14 * mm, A4[0] - 18 * mm, 14 * mm)
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=17 * mm,
        bottomMargin=20 * mm,
        title=f"供应商风险评估报告 - {supplier_id}",
        author=SYSTEM_NAME,
        subject=f"风险报告 {report_id}",
    )
    story: list[Any] = [
        Paragraph(SYSTEM_NAME, subtitle),
        Spacer(1, 2 * mm),
        Paragraph("供应商风险评估报告", title),
        Spacer(1, 2 * mm),
        Paragraph(f"生成时间：{escape(generated_at)}　｜　报告编号：{escape(report_id)}", subtitle),
        Spacer(1, 7 * mm),
    ]

    story += _section("一、供应商基础信息", section)
    story.append(
        _table(
            [
                ["供应商名称", supplier_name, "供应商 ID", supplier_id],
                ["重要程度", importance, "报告周期", grade.get("report_period_label") or grade.get("report_period") or "-"],
            ],
            [28 * mm, 60 * mm, 28 * mm, 58 * mm],
            body,
        )
    )

    story += _section("二、风险定级", section)
    risk_banner = Table(
        [[_paragraph(LEVEL_LABELS.get(level, level), risk_badge), _paragraph(f"风险得分：{grade.get('score', 0)}", body), _paragraph(f"人工复核：{REVIEW_LABELS.get(_text(report.get('human_review_status')), _text(report.get('human_review_status')))}", body)]],
        colWidths=[58 * mm, 48 * mm, 68 * mm],
    )
    risk_banner.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, 0), LEVEL_COLORS.get(level, colors.HexColor("#64748B"))),
                ("TEXTCOLOR", (0, 0), (0, 0), colors.white),
                ("BACKGROUND", (1, 0), (-1, 0), colors.HexColor("#F8FAFC")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.extend([risk_banner, Spacer(1, 2.5 * mm), _paragraph(grade.get("grade_basis") or report.get("risk_summary") or "-", body)])
    hit_rules = grade.get("hit_rules") if isinstance(grade.get("hit_rules"), list) else []
    if hit_rules:
        rule_rows = [["规则编号", "风险维度", "命中说明"]] + [
            [item.get("rule_id"), item.get("category") or item.get("risk_dimension"), item.get("description")]
            for item in hit_rules
        ]
        story.extend([Spacer(1, 2.5 * mm), _table(rule_rows, [32 * mm, 38 * mm, 104 * mm], small, header=True)])
    else:
        story.extend([Spacer(1, 2 * mm), _paragraph("本报告未命中风险规则。", body)])

    story += _section("三、六维度分析", section)
    story.append(KeepTogether([_radar_chart(dimensions, font_name)]))
    dimension_by_name = {_text(item.get("dimension")): item for item in dimensions if isinstance(item, dict)}
    dimension_rows = [["维度", "风险得分", "事件数", "数据状态"]] + [
        [name, dimension_by_name.get(name, {}).get("score", 0), dimension_by_name.get(name, {}).get("event_count", 0), "有数据" if dimension_by_name.get(name, {}).get("data_status") == "DATA" else "无数据"]
        for name in DIMENSIONS
    ]
    story.append(_table(dimension_rows, [50 * mm, 40 * mm, 36 * mm, 48 * mm], body, header=True))

    story += _section("四、证据链汇总", section)
    if evidence:
        evidence_rows = [["证据 ID", "维度", "证据摘要"]] + [
            [item.get("evidence_id"), item.get("mapped_dimension") or item.get("event_category"), item.get("readable_summary") or item.get("event_subtype") or "-"]
            for item in evidence
            if isinstance(item, dict)
        ]
        story.append(_table(evidence_rows, [42 * mm, 32 * mm, 100 * mm], small, header=True))
    else:
        message = "证据明细因权限限制未下发。" if report.get("evidence_withheld") else "本报告暂无可展示的入库证据。"
        story.append(_paragraph(message, body))

    story += _section("五、处置建议", section)
    story.extend([_paragraph(report.get("recommendation") or "暂无总体建议。", body), Spacer(1, 2.5 * mm)])
    if actions:
        action_rows = [["类型", "优先级", "候选建议", "执行状态"]] + [
            [item.get("suggest_type"), item.get("suggest_priority"), item.get("suggest_content"), item.get("execution_status")]
            for item in actions
            if isinstance(item, dict)
        ]
        story.append(_table(action_rows, [28 * mm, 24 * mm, 84 * mm, 38 * mm], small, header=True))
    else:
        story.append(_paragraph("本报告暂无候选处置建议。", body))

    story += _section("六、人工复核状态", section)
    disposition = report.get("disposition") if isinstance(report.get("disposition"), dict) else {}
    review_rows = [
        ["当前状态", REVIEW_LABELS.get(_text(report.get("human_review_status")), _text(report.get("human_review_status")) or "-")],
        ["处置状态", REVIEW_LABELS.get(_text(disposition.get("disposition_state")), _text(disposition.get("disposition_state")) or "-")],
        ["判定原因", disposition.get("reason") or "-"],
    ]
    story.append(_table(review_rows, [35 * mm, 139 * mm], body))
    story.extend(
        [
            Spacer(1, 5 * mm),
            Paragraph("说明：本报告由规则引擎与智能体协同生成，仅作为辅助研判材料；所有处置均须经人工复核后执行。", label),
        ]
    )

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


__all__ = ["render_pdf"]

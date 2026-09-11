"""风险报告渲染（Markdown / HTML，供后端套 PDF）。"""

from app.reporting.renderer import render_html, render_markdown, save_report

__all__ = ["render_html", "render_markdown", "save_report"]

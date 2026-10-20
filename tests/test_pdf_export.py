"""PDF renderer and direct-download API contract tests."""

from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader
from starlette.requests import Request

from app.backend import api as backend_api
from app.config import get_settings
from app.demo import run_demo
from app.reporting.pdf_renderer import render_pdf


def _report() -> dict:
    report = run_demo(current_week=10, include_events=True, enable_live_llm=False)["risk_report"]
    report["report_id"] = "report-pdf-test"
    return report


def test_render_pdf_contains_required_sections() -> None:
    content = render_pdf(_report())
    assert content.startswith(b"%PDF-")
    reader = PdfReader(BytesIO(content))
    assert len(reader.pages) >= 1
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    for expected in (
        "银行外包供应商风险监测系统",
        "报告编号",
        "供应商基础信息",
        "风险定级",
        "六维度分析",
        "证据链汇总",
        "处置建议",
        "人工复核状态",
    ):
        assert expected in text
    assert "公司背景" in text
    assert "知识产权" in text
    assert "report-pdf-test" in text


def test_pdf_endpoint_returns_attachment_and_audits(monkeypatch) -> None:
    report = _report()

    class _Repository:
        def __init__(self) -> None:
            self.audit_calls: list[dict] = []

        def get_report(self, report_id: str) -> dict:
            assert report_id == "report-pdf-test"
            return {
                "report_id": report_id,
                "supplier_id": report["supplier_id"],
                "analysis_run_id": "analysis-pdf-test",
                "report": report,
            }

        def audit(self, **kwargs) -> None:
            self.audit_calls.append(kwargs)

    repository = _Repository()
    monkeypatch.setattr(backend_api, "repository", lambda: repository)
    monkeypatch.setattr(get_settings(), "rbac_enforced", False, raising=False)

    request = Request({"type": "http", "method": "GET", "path": "/backend/reports/report-pdf-test/pdf", "headers": []})
    response = backend_api.download_report_pdf("report-pdf-test", request)

    assert response.status_code == 200
    assert response.media_type == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="risk-report-report-pdf-test.pdf"'
    assert int(response.headers["content-length"]) == len(response.body)
    assert response.body.startswith(b"%PDF-")
    assert repository.audit_calls[0]["action"] == "EXPORT_REPORT_PDF"

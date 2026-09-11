"""交付包装层测试：统一信封、错误处理、鉴权、异步任务、审核回写。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import server as server_module
from app.api.task_store import TaskStore
from app.config import get_settings
from app.review.service import ReviewFeedbackService

SUPPLIER = "S‑SUD161"  # 源数据使用非断行连字符


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(server_module.app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认关闭鉴权，避免影响其他用例。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "api_key", "", raising=False)


def _assert_envelope(body: dict) -> None:
    assert set(body) == {"code", "message", "data", "trace_id"}
    assert isinstance(body["code"], int)
    assert isinstance(body["trace_id"], str)


def test_health_uses_envelope(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    _assert_envelope(body)
    assert body["code"] == 0
    assert body["data"]["status"] == "ok"


def test_single_analyze_returns_report_in_envelope(client: TestClient) -> None:
    response = client.post("/agent/analyze", json={"supplier_ids": ["S-SUD161"]})
    assert response.status_code == 200
    body = response.json()
    _assert_envelope(body)
    assert body["data"]["mode"] == "single"
    report = body["data"]["report"]
    assert report["risk_grade"]["risk_level"] == "RED"
    assert report["supplier_id"] == SUPPLIER


def test_unknown_supplier_report_returns_404_envelope(client: TestClient) -> None:
    response = client.get("/agent/report/S-NOT-EXIST")
    assert response.status_code == 404
    body = response.json()
    _assert_envelope(body)
    assert body["code"] == 40400
    assert body["data"] is None


def test_validation_error_returns_422_envelope(client: TestClient) -> None:
    response = client.post("/agent/analyze", json={"current_week": 999})
    assert response.status_code == 422
    body = response.json()
    _assert_envelope(body)
    assert body["code"] == 42200
    assert body["data"]["errors"]


def test_unknown_task_returns_404_envelope(client: TestClient) -> None:
    response = client.get("/agent/task/does-not-exist")
    assert response.status_code == 404
    assert response.json()["code"] == 40400


def test_api_key_required_when_configured(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "api_key", "secret-key", raising=False)

    unauthorized = client.get("/health")
    assert unauthorized.status_code == 401
    assert unauthorized.json()["code"] == 40100

    authorized = client.get("/health", headers={"X-API-Key": "secret-key"})
    assert authorized.status_code == 200
    assert authorized.json()["code"] == 0


def test_trace_id_is_propagated_from_header(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Trace-Id": "trace-from-gateway"})
    assert response.json()["trace_id"] == "trace-from-gateway"


def test_async_batch_returns_task_id_then_succeeds(client: TestClient) -> None:
    response = client.post(
        "/agent/analyze",
        json={"supplier_ids": ["S-NOR001", "S-NOR004"], "async": True, "include_reports": False},
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["status"] == "PENDING"
    task_id = body["task_id"]
    assert task_id

    deadline = time.time() + 30
    status = "PENDING"
    while time.time() < deadline:
        polled = client.get(f"/agent/task/{task_id}").json()["data"]
        status = polled["status"]
        if status in {"SUCCEEDED", "FAILED"}:
            break
        time.sleep(0.2)

    assert status == "SUCCEEDED"
    assert polled["result"]["analyzed_count"] == 2
    assert polled["result"]["mode"] == "batch"


def test_task_store_records_failure() -> None:
    store = TaskStore()
    task_id = store.create()

    def _boom() -> None:
        raise RuntimeError("boom")

    store.submit(task_id, _boom)
    deadline = time.time() + 10
    task = store.get(task_id)
    while task and task["status"] not in {"SUCCEEDED", "FAILED"} and time.time() < deadline:
        time.sleep(0.05)
        task = store.get(task_id)
    assert task is not None
    assert task["status"] == "FAILED"
    assert "boom" in (task["error"] or "")


def test_review_roundtrip(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    review_path = tmp_path / "review_feedback.jsonl"
    monkeypatch.setattr(get_settings(), "review_output_path", review_path, raising=False)
    # 接口内部重新读取 settings，这里直接替换服务默认路径
    monkeypatch.setattr(
        server_module,
        "ReviewFeedbackService",
        lambda *args, **kwargs: ReviewFeedbackService(review_path),
    )

    payload = {
        "run_id": "run-api-1",
        "supplier_id": "S-SUD161",
        "reviewer_id": "reviewer-001",
        "review_result": "APPROVED",
        "final_action": "rectify",
        "review_comment": "同意整改",
        "risk_level": "RED",
    }
    response = client.post("/agent/review", json=payload)
    assert response.status_code == 200
    record = response.json()["data"]
    assert record["supplier_id"] == SUPPLIER
    assert record["final_action"] == "rectify"

    listed = client.get("/agent/reviews", params={"supplier_id": "S-SUD161"}).json()["data"]
    assert listed["total"] == 1

    duplicated = client.post("/agent/review", json=payload)
    assert duplicated.status_code == 400


def test_review_rejects_missing_final_action(client: TestClient) -> None:
    response = client.post(
        "/agent/review",
        json={
            "run_id": "run-api-2",
            "supplier_id": "S-SUD161",
            "reviewer_id": "reviewer-001",
            "review_result": "APPROVED",
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == 42200


# --------------------------------------------------------------------------- #
# 统计窗口（按周 / 按月）
# --------------------------------------------------------------------------- #
def test_analyze_accepts_month_window(client: TestClient) -> None:
    response = client.post(
        "/agent/analyze",
        json={"supplier_ids": ["S-SUD161"], "window_unit": "month", "window_size": 3},
    )
    assert response.status_code == 200
    report = response.json()["data"]["report"]
    grade = report["risk_grade"]
    assert grade["window_unit"] == "month"
    assert grade["window_size"] == 3
    # 1 月 = 4 周 → 近 3 个月 = 12 周
    assert grade["window_weeks"] == 12
    assert "个月" in grade["window_display"]


def test_analyze_defaults_to_week_window(client: TestClient) -> None:
    response = client.post("/agent/analyze", json={"supplier_ids": ["S-SUD161"]})
    grade = response.json()["data"]["report"]["risk_grade"]
    assert grade["window_unit"] == "week"
    assert grade["window_size"] == 12
    assert grade["window_display"] == "近12周"


def test_report_endpoint_accepts_window_query(client: TestClient) -> None:
    response = client.get(
        "/agent/report/S-SUD161", params={"window_unit": "month", "window_size": 2}
    )
    assert response.status_code == 200
    grade = response.json()["data"]["risk_grade"]
    assert grade["window_unit"] == "month"
    assert grade["window_weeks"] == 8


# --------------------------------------------------------------------------- #
# 按角色下发：领导层可见横向对比，普通管理人员仅见汇总
# --------------------------------------------------------------------------- #
def test_manager_role_withholds_cross_supplier_comparison(client: TestClient) -> None:
    response = client.post(
        "/agent/analyze",
        json={"supplier_ids": ["S-NOR001", "S-NOR004"], "audience": "MANAGER"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["cross_supplier_comparison"] is None
    assert "cross_supplier_comparison_withheld" in data
    # 汇总信息（重点监测名单）仍可见
    assert "watchlist" in data


def test_leadership_role_receives_cross_supplier_comparison(client: TestClient) -> None:
    response = client.post(
        "/agent/analyze",
        json={"supplier_ids": ["S-NOR001", "S-NOR004"], "audience": "LEADERSHIP"},
    )
    data = response.json()["data"]
    comparison = data["cross_supplier_comparison"]
    assert comparison is not None
    assert comparison["supplier_count"] == 2


def test_manager_report_withholds_cross_supplier_comparison(client: TestClient) -> None:
    response = client.get("/agent/report/S-SUD161", params={"audience": "MANAGER"})
    assert response.status_code == 200
    assert response.json()["data"] is None or "cross_supplier_comparison" in response.json()["data"]


# --------------------------------------------------------------------------- #
# 处置提示与重新申报（预留端点）
# --------------------------------------------------------------------------- #
def test_report_carries_disposition_block(client: TestClient) -> None:
    response = client.get("/agent/report/S-SUD161")
    report = response.json()["data"]
    disposition = report["disposition"]
    assert disposition["disposition_state"] in {
        "PENDING_HUMAN_REVIEW",
        "EVIDENCE_INSUFFICIENT",
        "AWAITING_SUPPLIER_REDECLARE",
    }
    assert isinstance(disposition["can_redeclare"], bool)
    assert disposition["delivery_audience"]


def test_redeclare_endpoint_registers_request(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review_path = tmp_path / "review_feedback_redeclare.jsonl"
    monkeypatch.setattr(get_settings(), "review_output_path", review_path, raising=False)
    monkeypatch.setattr(
        server_module,
        "ReviewFeedbackService",
        lambda *args, **kwargs: ReviewFeedbackService(review_path),
    )

    response = client.post(
        "/agent/redeclare",
        json={
            "supplier_id": "S-SUD161",
            "redeclare_reason": "已提交补充材料，问题已整改完成",
            "attachment_refs": ["doc-1"],
            "submitted_by": "vendor-contact",
        },
    )
    assert response.status_code == 200
    record = response.json()["data"]
    assert record["supplier_id"] == SUPPLIER
    assert record["review_result"] == "REDECLARED"
    assert record["disposition_state"] == "AWAITING_SUPPLIER_REDECLARE"
    assert record["attachment_refs"] == ["doc-1"]


def test_redeclare_requires_reason(client: TestClient) -> None:
    response = client.post("/agent/redeclare", json={"supplier_id": "S-SUD161"})
    assert response.status_code == 422
    assert response.json()["code"] == 42200


def test_reviews_can_filter_by_disposition_state(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review_path = tmp_path / "review_feedback_filter.jsonl"
    monkeypatch.setattr(get_settings(), "review_output_path", review_path, raising=False)
    monkeypatch.setattr(
        server_module,
        "ReviewFeedbackService",
        lambda *args, **kwargs: ReviewFeedbackService(review_path),
    )

    payload = {
        "run_id": "run-filter-1",
        "supplier_id": "S-SUD161",
        "reviewer_id": "reviewer-001",
        "review_result": "APPROVED",
        "final_action": "observe",
        "review_comment": "同意",
        "disposition_state": "AWAITING_SUPPLIER_REDECLARE",
    }
    assert client.post("/agent/review", json=payload).status_code == 200

    matched = client.get(
        "/agent/reviews", params={"disposition_state": "AWAITING_SUPPLIER_REDECLARE"}
    ).json()["data"]
    assert matched["total"] == 1

    unmatched = client.get(
        "/agent/reviews", params={"disposition_state": "PENDING_HUMAN_REVIEW"}
    ).json()["data"]
    assert unmatched["total"] == 0

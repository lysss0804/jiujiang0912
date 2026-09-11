"""批量分析与重点监测名单测试。"""

import pytest

from app.batch.service import LEVEL_RANK, analyze_batch


@pytest.fixture(scope="module")
def batch_result() -> dict:
    # 仅取少量供应商，避免测试过慢
    return analyze_batch(supplier_ids=["S‑NOR001", "S‑NOR004", "S‑NOR009", "S‑NOR011"])


def test_batch_returns_report_per_supplier(batch_result: dict):
    assert batch_result["analyzed_count"] == 4
    assert len(batch_result["reports"]) == 4
    assert batch_result["failed_count"] == 0


def test_watchlist_is_sorted_by_risk_descending(batch_result: dict):
    watchlist = batch_result["watchlist"]
    ranks = [LEVEL_RANK[item["risk_level"]] for item in watchlist]
    assert ranks == sorted(ranks)
    # 同等级内按得分降序
    for previous, current in zip(watchlist, watchlist[1:]):
        if previous["risk_level"] == current["risk_level"]:
            assert previous["score"] >= current["score"]


def test_level_counts_match_reports(batch_result: dict):
    counts = batch_result["level_counts"]
    assert sum(counts.values()) == batch_result["analyzed_count"]


def test_unknown_supplier_is_reported_not_silently_dropped():
    result = analyze_batch(supplier_ids=["S‑NOR001", "S-NOT-EXIST"])
    assert "S-NOT-EXIST" in result["unknown_suppliers"]
    assert result["analyzed_count"] == 1


def test_watchlist_respects_size_limit():
    result = analyze_batch(supplier_ids=["S‑NOR001", "S‑NOR004", "S‑NOR009"], watchlist_size=2)
    assert len(result["watchlist"]) <= 2


def test_watchlist_entries_have_required_fields(batch_result: dict):
    for item in batch_result["watchlist"]:
        assert item["supplier_id"]
        assert item["risk_level"] in {"RED", "YELLOW", "GREEN"}
        assert "key_dimensions" in item
        assert "recommendation" in item

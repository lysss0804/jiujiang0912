"""横向对比数据块单测：中位数、偏差、排名正确性；单点失败不影响整批。

横向对比为纯 Python 计算（无模型调用、无 I/O），供后端按角色渲染「横向对比 PDF」。
"""

from __future__ import annotations

from app.batch.service import build_cross_supplier_comparison
from app.schemas.contracts import RISK_DIMENSIONS


def _report(supplier_id: str, level: str, score: float, **overrides) -> dict:
    breakdown = overrides.pop("breakdown", {name: 0.0 for name in RISK_DIMENSIONS})
    counts = overrides.pop("counts", {name: 0 for name in RISK_DIMENSIONS})
    report = {
        "supplier_id": supplier_id,
        "supplier_name": f"供应商{supplier_id}",
        "risk_grade": {
            "risk_level": level,
            "score": score,
            "importance_tier": "重要",
            "dimension_breakdown": breakdown,
        },
        "dimension_breakdown": [
            {"dimension": name, "event_count": counts.get(name, 0), "score": breakdown.get(name, 0.0)}
            for name in RISK_DIMENSIONS
        ],
    }
    report.update(overrides)
    return report


def _reports() -> list[dict]:
    return [
        _report("S-A", "RED", 3.0, breakdown={**{n: 0.0 for n in RISK_DIMENSIONS}, "经营风险": 3.0},
                counts={**{n: 0 for n in RISK_DIMENSIONS}, "经营风险": 2}),
        _report("S-B", "YELLOW", 1.0, breakdown={**{n: 0.0 for n in RISK_DIMENSIONS}, "经营状况": 1.0},
                counts={**{n: 0 for n in RISK_DIMENSIONS}, "经营状况": 1}),
        _report("S-C", "GREEN", 0.0),
    ]


def test_empty_batch_is_safe():
    comparison = build_cross_supplier_comparison([])
    assert comparison["supplier_count"] == 0
    assert comparison["rankings"] == []
    assert comparison["level_distribution"] == {"RED": 0, "YELLOW": 0, "GREEN": 0}
    assert comparison["note"]


def test_level_distribution_counts_each_tier():
    comparison = build_cross_supplier_comparison(_reports())
    assert comparison["level_distribution"] == {"RED": 1, "YELLOW": 1, "GREEN": 1}
    assert comparison["supplier_count"] == 3


def test_median_score_matches_manual_computation():
    comparison = build_cross_supplier_comparison(_reports())
    # scores = [0.0, 1.0, 3.0] -> median 1.0
    assert comparison["median_score"] == 1.0


def test_rankings_sorted_ascending_with_rank_and_deviation():
    comparison = build_cross_supplier_comparison(_reports())
    rankings = comparison["rankings"]
    assert [item["supplier_id"] for item in rankings] == ["S-C", "S-B", "S-A"]
    assert [item["rank"] for item in rankings] == [1, 2, 3]
    # 偏差 = 分值 - 中位数
    assert rankings[0]["deviation"] == -1.0
    assert rankings[1]["deviation"] == 0.0
    assert rankings[2]["deviation"] == 2.0


def test_rankings_carry_level_importance_and_score():
    comparison = build_cross_supplier_comparison(_reports())
    top = comparison["rankings"][-1]
    assert top["risk_level"] == "RED"
    assert top["score"] == 3.0
    assert top["importance_tier"] == "重要"


def test_dimension_baselines_cover_six_dimensions():
    comparison = build_cross_supplier_comparison(_reports())
    baselines = comparison["dimension_baselines"]
    assert [item["dimension"] for item in baselines] == list(RISK_DIMENSIONS)
    by_dim = {item["dimension"]: item for item in baselines}
    # 经营风险分值 [3.0, 0.0, 0.0] -> 中位数 0.0；事件数 [2, 0, 0] -> 中位数 0.0
    assert by_dim["经营风险"]["median_score"] == 0.0
    assert by_dim["经营风险"]["median_event_count"] == 0.0
    # 经营状况分值 [0.0, 1.0, 0.0] -> 中位数 0.0；事件数 [1, 0, 0] -> 中位数 0.0
    assert by_dim["经营状况"]["median_score"] == 0.0
    assert by_dim["经营状况"]["median_event_count"] == 0.0


def test_single_report_batch_is_supported():
    comparison = build_cross_supplier_comparison([_report("S-ONLY", "GREEN", 0.5)])
    assert comparison["supplier_count"] == 1
    assert comparison["median_score"] == 0.5
    assert comparison["rankings"][0]["deviation"] == 0.0


def test_missing_dimension_breakdown_does_not_crash():
    report = {"supplier_id": "S-X", "risk_grade": {"risk_level": "GREEN", "score": 0.0}}
    comparison = build_cross_supplier_comparison([report])
    assert comparison["supplier_count"] == 1
    assert len(comparison["dimension_baselines"]) == len(RISK_DIMENSIONS)


def test_note_mentions_manual_use_only():
    comparison = build_cross_supplier_comparison(_reports())
    assert "人工" in comparison["note"]
    assert "generated_at" in comparison

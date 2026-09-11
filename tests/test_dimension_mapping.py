"""维度归类单测：模型归类、越界拦截、别名表降级、无来源维度 NO_DATA。

要点：
- 归类由模型完成（一次调用批量返回），模型不可用时退化为确定性别名表；
- 越界 evidence_id 必须被丢弃；
- 本地无数据来源的维度输出 NO_DATA，分值计 0，且**不编造任何事件**。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.dimension_mapping import alias_dimension, clear_mapping_cache, load_dimension_alias
from app.schemas.contracts import RISK_DIMENSIONS

SUPPLIER = "S-TEST-01"


@pytest.fixture(autouse=True)
def _isolate_mapping_cache():
    """归类缓存为进程内单例，用例间必须隔离，避免命中其他用例的结果。"""
    clear_mapping_cache()
    yield
    clear_mapping_cache()


class _StubLLM:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response

    def invoke(self, *, system_prompt: str, user_prompt: str, response_model):
        del system_prompt, user_prompt
        return response_model.model_validate(self.response)


class _BrokenLLM:
    def invoke(self, *, system_prompt: str, user_prompt: str, response_model):
        raise RuntimeError("llm unavailable")


def _use_stub(monkeypatch: pytest.MonkeyPatch, response: dict[str, Any]) -> None:
    monkeypatch.setattr(
        "app.llm.factory.build_llm_client", lambda settings=None: _StubLLM(response)
    )


def _use_broken(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.llm.factory.build_llm_client", lambda settings=None: _BrokenLLM()
    )


def _state(**overrides) -> dict:
    state = {
        "supplier_id": SUPPLIER,
        "current_week": 10,
        "window_unit": "week",
        "window_size": 12,
        "enable_live_llm": True,
        "risk_grade": {
            "risk_level": "YELLOW",
            "window_weeks": 12,
            "dimension_breakdown": {"经营": 2.0, "履约": 1.0},
        },
        "events": [
            {
                "evidence_id": "E-1",
                "supplier_id": SUPPLIER,
                "event_category": "经营",
                "event_subtype": "经营异常名录",
                "event_severity": 2,
                "event_week": 9,
                "source_type": "工商登记",
            },
            {
                "evidence_id": "E-2",
                "supplier_id": SUPPLIER,
                "event_category": "履约",
                "event_subtype": "项目延期",
                "event_severity": 1,
                "event_week": 10,
                "source_type": "履约记录",
            },
        ],
    }
    state.update(overrides)
    return state


def test_model_classification_writes_mapped_dimension(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_stub(
        monkeypatch,
        {
            "mappings": [
                {"evidence_id": "E-1", "new_dimension": "公司背景"},
                {"evidence_id": "E-2", "new_dimension": "经营状况"},
            ]
        },
    )
    from app.agents.dimension_mapping import dimension_mapping_node

    result = dimension_mapping_node(_state())
    assert result["dimension_mapping_result"]["status"] == "SUCCESS"
    mapped = {e["evidence_id"]: e.get("mapped_dimension") for e in result["events"]}
    assert mapped["E-1"] == "公司背景"
    assert mapped["E-2"] == "经营状况"
    # 统计键固定为六个新维度
    assert set(result["dimension_stats"]["counts"]) == set(RISK_DIMENSIONS)


def test_out_of_scope_evidence_ids_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """模型返回越界编号时必须被拦截并整体降级，不污染归类结果。"""
    _use_stub(
        monkeypatch,
        {
            "mappings": [
                {"evidence_id": "E-INVENTED", "new_dimension": "司法"},
                {"evidence_id": "E-1", "new_dimension": "公司背景"},
            ]
        },
    )
    from app.agents.dimension_mapping import dimension_mapping_node

    result = dimension_mapping_node(_state())
    # 越界条目导致校验失败 -> 降级到别名表
    assert result["dimension_mapping_result"]["status"] == "FALLBACK"
    mapped = {e["evidence_id"]: e.get("mapped_dimension") for e in result["events"]}
    # 降级仍给出确定性归类
    assert mapped["E-1"] == "公司背景"
    assert mapped["E-2"] == "经营状况"


def test_falls_back_to_alias_table_on_model_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_broken(monkeypatch)
    from app.agents.dimension_mapping import dimension_mapping_node

    result = dimension_mapping_node(_state())
    assert result["dimension_mapping_result"]["status"] == "FALLBACK"
    mapped = {e["evidence_id"]: e.get("mapped_dimension") for e in result["events"]}
    assert mapped["E-1"] == "公司背景"
    assert mapped["E-2"] == "经营状况"


def test_disabled_llm_uses_alias_table_without_calling_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(settings=None):
        raise AssertionError("disabled 时不应调用模型")

    monkeypatch.setattr("app.llm.factory.build_llm_client", _boom)
    from app.agents.dimension_mapping import dimension_mapping_node

    result = dimension_mapping_node(_state(enable_live_llm=False))
    assert result["dimension_mapping_result"]["status"] == "DISABLED"
    mapped = {e["evidence_id"]: e.get("mapped_dimension") for e in result["events"]}
    assert mapped["E-1"] == "公司背景"


def test_no_data_dimensions_are_marked_without_fabrication(monkeypatch: pytest.MonkeyPatch) -> None:
    """无本地来源的维度标 NO_DATA，事件数 0，且不新增任何事件。"""
    _use_broken(monkeypatch)
    from app.agents.dimension_mapping import dimension_mapping_node

    result = dimension_mapping_node(_state())
    counts = result["dimension_stats"]["counts"]
    no_data = result["dimension_stats"]["no_data_dimensions"]
    for dimension in ("司法", "失信", "知识产权"):
        assert counts[dimension] == 0
        assert dimension in no_data
    # 不编造事件：输出事件数量与输入一致
    assert len(result["events"]) == 2


def test_alias_table_covers_real_subtypes() -> None:
    """别名表需把本地真实子类型确定性归入六维度，无越界维度。"""
    alias = load_dimension_alias()
    assert alias, "dimension_alias.yaml 应可用"
    samples = [
        ({"event_category": "经营", "event_subtype": "经营异常名录", "source_type": "工商登记"}, "公司背景"),
        ({"event_category": "履约", "event_subtype": "项目延期", "source_type": "履约记录"}, "经营状况"),
        ({"event_category": "司法", "event_subtype": "诉讼案件", "source_type": "法院公告"}, "司法"),
        ({"event_category": "失信", "event_subtype": "严重违法失信", "source_type": "执行信息"}, "失信"),
        ({"event_category": "知识产权", "event_subtype": "专利侵权", "source_type": "知识产权局"}, "知识产权"),
        ({"event_category": "人员", "event_subtype": "核心人员离职", "source_type": "人员变动"}, "经营状况"),
    ]
    for event, expected in samples:
        assert alias_dimension(event, alias) == expected


def test_alias_fallback_dimension_is_always_valid() -> None:
    alias = load_dimension_alias()
    unknown = {"event_category": "未知类别", "event_subtype": "未知事件", "source_type": "未知"}
    assert alias_dimension(unknown, alias) in RISK_DIMENSIONS

"""政策检索（RAG）节点测试。

重点覆盖真实数据下暴露的既有缺陷：
向量库存在**哈希向量历史记录**时，`_dot` 因维度不一致返回 0 分。
旧实现把 0 分当作「无结果」过滤，导致只要不同时重建索引，
政策依据恒为空 → 决策 Agent 永远走 NO_APPROVED_POLICY 兜底。

测试全部使用临时索引 + 注入式 service，不依赖开发者本机的 data/index。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents import policy_retrieval
from app.agents.policy_retrieval import policy_retrieval_node
from app.rag.models import DocumentChunk


def _state(**overrides) -> dict:
    state = {
        "supplier_id": "S-TEST-01",
        "current_week": 10,
        "risk_grade": {"risk_level": "YELLOW", "score": 3.0},
        "risk_trend": {"trend_type": "STEADY"},
        "evidence_result": {"evidence_items": [{"event_category": "安全"}]},
    }
    state.update(overrides)
    return state


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """清空模块级检索缓存与 service 缓存，避免用例间串味。"""
    policy_retrieval._RETRIEVAL_CACHE.clear()
    policy_retrieval._service.cache_clear()


def _record(chunk_id: str, text: str, vector: list[float], source: str) -> dict:
    chunk = DocumentChunk(
        document_id="DOC-1",
        document_name="外包风险管理办法.md",
        source="data/knowledge/外包风险管理办法.md",
        version="v1.0",
        effective_date=None,
        is_approved=True,
        chunk_id=chunk_id,
        page=None,
        text=text,
    )
    return {"chunk": chunk.to_dict(), "vector": vector, "vector_source": source}


def _install_index(monkeypatch: pytest.MonkeyPatch, records: list[dict], dimensions: int = 8) -> None:
    """把临时索引与哈希维度注入 service 构建路径，隔离本机真实索引。"""
    from app.rag.service import RAGService

    import tempfile

    index_path = Path(tempfile.mkdtemp()) / "vector_index.json"
    index_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        policy_retrieval,
        "_service",
        lambda _path: RAGService(index_path, embedder=None, hash_dimensions=dimensions),
    )


def test_zero_score_match_is_not_discarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """向量维度不一致（历史哈希向量）时得分 0，也不得被当作「无制度依据」丢弃。

    这是真实数据下「政策依据恒为空」的根因：降级为 hash 向量的索引
    （256 维）与 embedding 查询向量（如 1024 维）无法比较，`_dot` 返回 0。
    """
    _install_index(
        monkeypatch,
        [_record("P-0001", "供应商发生安全事件应二十四小时内报告。", [0.0] * 8, "hash")],
    )

    result = policy_retrieval_node(_state())
    assert result["policy_context"], "0 分命中被错误丢弃，导致政策依据恒为空"
    assert result["policy_context"][0]["chunk_id"] == "P-0001"


def test_empty_index_reports_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """制度库确实没有可用条目时，应如实标记 EMPTY 而非伪造依据。"""
    _install_index(monkeypatch, [])

    result = policy_retrieval_node(_state())
    assert result["policy_context"] == []
    assert result["audit_trace"][0]["status"] == "EMPTY"


def test_approved_only_filters_unapproved_records(monkeypatch: pytest.MonkeyPatch) -> None:
    """未审核的制度片段不得作为政策依据（合规红线）。"""
    records = [_record("P-0002", "未审核的制度草稿。", [0.1] * 8, "hash")]
    records[0]["chunk"]["is_approved"] = False
    _install_index(monkeypatch, records)

    result = policy_retrieval_node(_state())
    assert result["policy_context"] == []

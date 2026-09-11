"""政策检索（RAG）。

职责：根据当前风险维度与趋势，检索已批准的银行制度/监管要求片段，
为风险结论和处置建议提供可追溯的政策依据。

边界：RAG 不参与风险分级与预测，只提供依据。

注意（真实数据下的既有缺陷）：向量库在**建索引时**用的是 embedding 向量，
而查询侧若同时存在哈希向量记录，`_dot` 会因维度不一致返回 0 分。
此前的实现把「0 分」当作「检索为空」直接过滤，导致**只要不同时配置
embedding 与索引重建，政策依据就恒为空**，决策 Agent 永远走 NO_APPROVED_POLICY 兜底。
现在改为：按分数降序取 top_k（0 分仍在末位），是否可用交给上层判断，
保证「向量库不一致」与「制度库确实没命中」两种情形可区分。
"""

from functools import lru_cache
from pathlib import Path

from app.agents.helpers import trace
from app.config import get_settings
from app.rag.service import RAGService, build_rag_service

_RETRIEVAL_CACHE: dict[tuple[str, int], list[dict]] = {}


@lru_cache
def _service(index_path: str) -> RAGService:
    # 配置了 embedding 通道时使用真实语义向量，否则由 store 内部回退哈希向量
    # （哈希维度同样从 EMBEDDING_DIMENSIONS 读取，避免配置项形同虚设）
    return build_rag_service(Path(index_path))



def _humanize(text: str, limit: int = 200) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def policy_retrieval_node(state: dict) -> dict:
    settings = get_settings()
    grade = state.get("risk_grade", {})
    association = state.get("association_result", {})
    evidence = state.get("evidence_result", {}).get("evidence_items", [])

    categories = sorted({str(item.get("event_category", "")) for item in evidence if item.get("event_category")})
    query = " ".join(
        [
            "银行外包供应商风险",
            f"风险等级{grade.get('risk_level', '')}",
            *categories,
            str(state.get("risk_trend", {}).get("trend_type", "")),
            "证据核验 人工复核 候选处置",
        ]
    )

    cache_key = (query, settings.rag_top_k)
    if cache_key in _RETRIEVAL_CACHE:
        context = _RETRIEVAL_CACHE[cache_key]
        status = "PASS" if context else "EMPTY"
        return {
            "policy_context": context,
            "audit_trace": trace(
                "PolicyRetrievalAgent", status, detail=f"cached_policy_chunks={len(context)}"
            ),
        }

    matches = _service(str(settings.vector_index_path)).search(
        query, top_k=settings.rag_top_k, approved_only=True
    )
    context = [
        {
            "chunk_id": item["chunk_id"],
            "document_name": item["document_name"],
            "version": item["version"],
            "text": _humanize(item["text"]),
            "score": round(float(item["score"]), 6),
            # 透传向量来源（embedding / hash）：RAG 在无可用 Key 时会降级为
            # 本地哈希伪向量，只有保留该字段才能区分「真实语义检索命中」与
            # 「降级后凑巧命中」，使政策依据可自证数据来源。
            "vector_source": item.get("vector_source", ""),
        }
        for item in matches
    ]
    _RETRIEVAL_CACHE[cache_key] = context
    status = "PASS" if context else "EMPTY"
    return {
        "policy_context": context,
        "audit_trace": trace(
            "PolicyRetrievalAgent",
            status,
            detail=f"approved_policy_chunks={len(context)}",
        ),
    }

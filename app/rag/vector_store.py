"""本地向量索引。

向量化策略（优先真实语义向量，失败降级哈希向量）：
- 配置了可用的 embedding 通道时，调用真实 embedding 模型生成语义向量，
  索引落盘时记录向量来源 vector_source="embedding"；
- 未配置 / 调用失败时回退到本地 sha256 哈希伪向量，vector_source="hash"，
  保证无 Key 时 RAG 仍可用（降级）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from collections import Counter
from pathlib import Path
from typing import Protocol

from app.rag.models import DocumentChunk

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")

VECTOR_SOURCE_EMBEDDING = "embedding"
VECTOR_SOURCE_HASH = "hash"

logger = logging.getLogger(__name__)


class EmbeddingClient(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _vectorize(text: str, dimensions: int = 256) -> list[float]:
    """确定性哈希伪向量：无 embedding 通道时的降级实现。"""
    counts: Counter[int] = Counter()
    for token in TOKEN_PATTERN.findall(text.lower()):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        counts[int.from_bytes(digest[:4], "big") % dimensions] += 1
    norm = math.sqrt(sum(value * value for value in counts.values())) or 1.0
    return [counts[index] / norm for index in range(dimensions)]


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _dot(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        # 向量维度不一致（如切换过模型）时视为不可比，返回最低分
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True))


class LocalVectorStore:
    """Small deterministic local index for skeleton validation, not production scale."""

    def __init__(
        self,
        path: Path,
        *,
        embedder: EmbeddingClient | None = None,
        hash_dimensions: int = 256,
    ) -> None:
        self.path = path
        self.embedder = embedder
        self.hash_dimensions = hash_dimensions
        self.records: list[dict] = []
        # 最近一次写入实际使用的向量来源（embedding / hash）
        self.vector_source: str = VECTOR_SOURCE_HASH
        self._query_cache: dict[str, tuple[list[float], str]] = {}

    # ---- 向量化 ----
    def _embed_texts(self, texts: list[str]) -> tuple[list[list[float]], str]:
        """优先调用真实 embedding，失败或无配置时回退哈希向量。"""
        if self.embedder is not None and texts:
            try:
                vectors = self.embedder.embed(texts)
                if len(vectors) == len(texts) and all(vectors):
                    return [_normalize(list(vector)) for vector in vectors], VECTOR_SOURCE_EMBEDDING
            except Exception as exc:  # 任何模型侧异常都降级，保证 RAG 仍可用
                logger.warning("embedding_fallback error_type=%s", type(exc).__name__)
        return [_vectorize(text, self.hash_dimensions) for text in texts], VECTOR_SOURCE_HASH

    def _vectorize_query(self, query: str) -> list[float]:
        if query in self._query_cache:
            return self._query_cache[query][0]
        vectors, _ = self._embed_texts([query])
        self._query_cache[query] = (vectors[0], self.vector_source)
        return vectors[0]

    # ---- 索引读写 ----
    def add(self, chunks: list[DocumentChunk]) -> None:
        if not chunks:
            return
        vectors, source = self._embed_texts([chunk.text for chunk in chunks])
        self.vector_source = source
        self.records.extend(
            {"chunk": chunk.to_dict(), "vector": vector, "vector_source": source}
            for chunk, vector in zip(chunks, vectors, strict=True)
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.records, ensure_ascii=False), encoding="utf-8")

    def load(self) -> None:
        self.records = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []

    def search(self, query: str, *, top_k: int = 3, approved_only: bool = False) -> list[dict]:
        query_vector = self._vectorize_query(query)
        candidates = []
        for record in self.records:
            chunk = record["chunk"]
            if approved_only and not chunk["is_approved"]:
                continue
            candidates.append(
                {
                    "score": _dot(query_vector, record["vector"]),
                    "vector_source": record.get("vector_source", VECTOR_SOURCE_HASH),
                    **chunk,
                }
            )
        return sorted(candidates, key=lambda item: item["score"], reverse=True)[:top_k]

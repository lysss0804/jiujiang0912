from pathlib import Path

from app.rag.service import RAGService
from app.rag.vector_store import VECTOR_SOURCE_EMBEDDING, VECTOR_SOURCE_HASH


class _FakeEmbedder:
    """确定性假向量：把文本映射为可区分的 4 维向量，用于验证 embedding 通道。"""

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = [0.0, 0.0, 0.0, 0.0]
            for index, char in enumerate(text):
                vector[index % 4] += float(ord(char) % 97)
            vectors.append(vector)
        return vectors


class _BrokenEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedding unavailable")


def test_rag_index_keeps_source_metadata(tmp_path: Path):
    document = tmp_path / "mock.md"
    document.write_text("证据校验失败时不得形成正式处置建议。", encoding="utf-8")
    service = RAGService(tmp_path / "index.json")
    assert service.index_file(document, approved=False) == 1
    result = service.search("证据失败", top_k=1)
    assert result[0]["document_name"] == "mock.md"
    assert result[0]["is_approved"] is False
    # 未配置 embedding 通道时回退哈希向量
    assert result[0]["vector_source"] == VECTOR_SOURCE_HASH


def test_rag_uses_embedding_channel_when_configured(tmp_path: Path):
    document = tmp_path / "mock.md"
    document.write_text("证据校验失败时不得形成正式处置建议。", encoding="utf-8")
    service = RAGService(tmp_path / "index.json", embedder=_FakeEmbedder())
    assert service.index_file(document, approved=True) == 1
    assert service.store.vector_source == VECTOR_SOURCE_EMBEDDING
    result = service.search("证据校验失败", top_k=1)
    assert result[0]["vector_source"] == VECTOR_SOURCE_EMBEDDING
    assert result[0]["score"] > 0
    assert result[0]["is_approved"] is True


def test_rag_falls_back_to_hash_when_embedding_fails(tmp_path: Path):
    """embedding 调用失败时必须回退哈希向量，检索仍可用（降级）。"""
    document = tmp_path / "mock.md"
    document.write_text("证据校验失败时不得形成正式处置建议。", encoding="utf-8")
    service = RAGService(tmp_path / "index.json", embedder=_BrokenEmbedder())
    assert service.index_file(document, approved=True) == 1
    assert service.store.vector_source == VECTOR_SOURCE_HASH
    result = service.search("证据失败", top_k=1)
    assert result[0]["vector_source"] == VECTOR_SOURCE_HASH
    assert result[0]["document_name"] == "mock.md"


def test_rag_approved_only_filters_unapproved(tmp_path: Path):
    document = tmp_path / "mock.md"
    document.write_text("未审核知识条目默认不应被检索到。", encoding="utf-8")
    service = RAGService(tmp_path / "index.json")
    service.index_file(document, approved=False)
    assert service.search("未审核", top_k=3, approved_only=True) == []
    assert len(service.search("未审核", top_k=3, approved_only=False)) == 1

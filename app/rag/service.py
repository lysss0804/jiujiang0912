from pathlib import Path

from app.rag.loaders import load_document, split_document
from app.rag.vector_store import EmbeddingClient, LocalVectorStore


def build_embedder() -> EmbeddingClient | None:
    """按配置构建 embedding 客户端；未配置可用通道时返回 None（回退哈希向量）。"""
    from app.config import get_settings
    from app.llm.factory import build_embedding_client

    settings = get_settings()
    return build_embedding_client(settings)


class RAGService:
    def __init__(
        self,
        index_path: Path,
        *,
        embedder: EmbeddingClient | None = None,
        hash_dimensions: int = 256,
    ) -> None:
        # hash_dimensions 此前从未从配置透传，导致 EMBEDDING_DIMENSIONS 形同虚设；
        # 这里由调用方（build_rag_service）按配置注入，保持构造器无隐式依赖。
        self.store = LocalVectorStore(index_path, embedder=embedder, hash_dimensions=hash_dimensions)


    def index_file(self, path: Path, *, approved: bool = False) -> int:
        self.store.load()
        text, metadata = load_document(path, approved=approved)
        chunks = split_document(text, metadata)
        self.store.add(chunks)
        self.store.save()
        return len(chunks)

    def rebuild(self, documents: list[tuple[Path, bool]]) -> int:
        self.store.records = []
        count = 0
        for path, approved in documents:
            text, metadata = load_document(path, approved=approved)
            chunks = split_document(text, metadata)
            self.store.add(chunks)
            count += len(chunks)
        self.store.save()
        return count

    def search(self, query: str, *, top_k: int = 3, approved_only: bool = False) -> list[dict]:
        self.store.load()
        return self.store.search(query, top_k=top_k, approved_only=approved_only)


def build_rag_service(index_path: Path) -> RAGService:
    """按当前配置构建 RAG 服务（embedding 通道 + 哈希维度均从配置读取）。"""
    from app.config import get_settings

    settings = get_settings()
    return RAGService(
        index_path,
        embedder=build_embedder(),
        hash_dimensions=settings.embedding_dimensions,
    )


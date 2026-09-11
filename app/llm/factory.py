from app.config import Settings, get_settings
from app.llm.client import StructuredLLMClient
from app.llm.providers import EmbeddingProvider, MockProvider, OpenAICompatibleProvider


def build_llm_client(settings: Settings | None = None) -> StructuredLLMClient:
    settings = settings or get_settings()
    provider_name = settings.llm_provider.strip().lower()
    if provider_name == "mock":
        provider = MockProvider()
    elif provider_name in {"deepseek", "openai", "openai-compatible"}:
        provider = OpenAICompatibleProvider(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    else:
        raise ValueError(f"Unsupported LLM_PROVIDER: {settings.llm_provider}")
    return StructuredLLMClient(
        provider,
        max_retries=settings.llm_max_retries,
        max_output_chars=settings.max_output_chars,
    )


def build_embedding_client(settings: Settings | None = None) -> EmbeddingProvider | None:
    """构建向量化客户端。

    复用同一 OpenAI 兼容通道，仅按 embedding_* 配置覆盖模型/地址/密钥。
    未配置任何可用模型时返回 None，由上层（RAG）回退到本地哈希向量（降级）。
    """
    settings = settings or get_settings()
    model = (settings.embedding_model or settings.llm_model).strip()
    provider_name = (settings.embedding_provider or settings.llm_provider).strip().lower()
    api_key = settings.embedding_api_key or settings.llm_api_key
    base_url = settings.embedding_base_url or settings.llm_base_url

    # mock 通道不提供真实向量，交由哈希向量降级，避免伪造语义向量
    if provider_name == "mock" or not model or not api_key:
        return None
    if provider_name not in {"deepseek", "openai", "openai-compatible"}:
        raise ValueError(f"Unsupported EMBEDDING_PROVIDER: {provider_name}")
    return OpenAICompatibleProvider(
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout_seconds=settings.llm_timeout_seconds,
    )

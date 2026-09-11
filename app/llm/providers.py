import json
from typing import Protocol


class LLMProvider(Protocol):
    def complete(self, *, system_prompt: str, user_prompt: str) -> str: ...


class MockProvider:
    """Deterministic provider used by tests and demos."""

    def __init__(self, response: dict | None = None) -> None:
        self.response = response or {
            "status": "MOCK_SUCCESS",
            "summary": "虚构测试输出",
            "evidence_ids": [],
        }

    def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        del system_prompt, user_prompt
        return json.dumps(self.response, ensure_ascii=False)


class OpenAICompatibleProvider:
    """Optional OpenAI-compatible provider; never logs API credentials."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError("LLM_API_KEY is required for a non-mock provider")
        from openai import OpenAI

        self.model = model
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url or None,
            timeout=timeout_seconds,
        )

    def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content or "{}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        """调用同一 OpenAI 兼容通道的 embeddings 接口（不引入新厂商）。"""
        if not texts:
            return []
        response = self.client.embeddings.create(model=self.model, input=texts)
        ordered = sorted(response.data, key=lambda item: item.index)
        return [[float(value) for value in item.embedding] for item in ordered]


class EmbeddingProvider(Protocol):
    """仅暴露向量化能力的轻量协议，便于独立注入与测试。"""

    def embed(self, texts: list[str]) -> list[list[float]]: ...


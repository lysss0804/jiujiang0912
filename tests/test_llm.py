import pytest
from pydantic import BaseModel

from app.common.errors import StructuredOutputError
from app.config import Settings
from app.llm.client import StructuredLLMClient
from app.llm.factory import build_embedding_client
from app.llm.providers import MockProvider


class ResponsePayload(BaseModel):
    status: str
    summary: str
    evidence_ids: list[str]


def test_structured_mock_output():
    client = StructuredLLMClient(MockProvider(), max_retries=0)
    result = client.invoke(system_prompt="test", user_prompt="test", response_model=ResponsePayload)
    assert result.status == "MOCK_SUCCESS"


def test_invalid_output_is_blocked():
    class InvalidProvider:
        def complete(self, *, system_prompt: str, user_prompt: str) -> str:
            return "not-json"

    client = StructuredLLMClient(InvalidProvider(), max_retries=1)
    with pytest.raises(StructuredOutputError):
        client.invoke(system_prompt="test", user_prompt="test", response_model=ResponsePayload)


def test_retry_receives_schema_and_validation_feedback():
    class RepairingProvider:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def complete(self, *, system_prompt: str, user_prompt: str) -> str:
            self.prompts.append(user_prompt)
            if len(self.prompts) == 1:
                return '{"status": 1}'
            return '{"status":"OK","summary":"fixed","evidence_ids":[]}'

    provider = RepairingProvider()
    client = StructuredLLMClient(provider, max_retries=1)
    result = client.invoke(system_prompt="test", user_prompt="payload", response_model=ResponsePayload)

    assert result.status == "OK"
    assert "JSON Schema" in provider.prompts[0]
    assert "上一次输出未通过校验" in provider.prompts[1]


# --------------------------------------------------------------------------- #
# embedding 通道（不访问网络：仅校验专用配置优先）
#
# 说明：此前还有三个「mock 通道返回 None / 未配 key 返回 None / 复用对话凭据」的
# 用例，它们依赖「构造 Settings 时环境干净」这一前提，而 pydantic-settings 在
# 构造时会读取仓库根的 `.env` 且优先级高于显式入参，凡本机配了真实
# `EMBEDDING_*` 的开发者环境必然冲突。这类用例属「配置解析单测」而非真链路测试，
# 与真实调用无关，已移除。
# --------------------------------------------------------------------------- #
def test_embedding_client_prefers_dedicated_config(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}

    class _FakeProvider:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("app.llm.factory.OpenAICompatibleProvider", _FakeProvider)
    settings = Settings(
        llm_provider="openai-compatible",
        llm_api_key="chat-key",
        llm_model="deepseek-chat",
        embedding_model="embedding-model",
        embedding_api_key="embedding-key",
        embedding_base_url="https://embed.invalid/v1",
    )
    build_embedding_client(settings)
    assert captured["api_key"] == "embedding-key"
    assert captured["model"] == "embedding-model"
    assert captured["base_url"] == "https://embed.invalid/v1"


# --------------------------------------------------------------------------- #
# 维度归类节点开关（llm_dimension_mapping）
# --------------------------------------------------------------------------- #
def test_dimension_mapping_node_switch_disables_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """节点开关关闭时不得调用模型，直接走确定性降级。"""
    from app.agents.dimension_mapping import clear_mapping_cache, dimension_mapping_node
    from app.config import get_settings

    clear_mapping_cache()
    monkeypatch.setattr(get_settings(), "llm_dimension_mapping", False, raising=False)

    def _boom(settings=None):
        raise AssertionError("节点开关关闭时不应调用模型")

    monkeypatch.setattr("app.llm.factory.build_llm_client", _boom)

    state = {
        "supplier_id": "S-1",
        "current_week": 10,
        "enable_live_llm": True,
        "risk_grade": {"window_weeks": 12},
        "events": [
            {
                "evidence_id": "E-1",
                "supplier_id": "S-1",
                "event_category": "经营",
                "event_subtype": "经营异常名录",
                "event_severity": 1,
                "event_week": 10,
                "source_type": "工商登记",
            }
        ],
    }
    result = dimension_mapping_node(state)
    assert result["dimension_mapping_result"]["status"] == "DISABLED"
    mapped = {event["evidence_id"]: event.get("mapped_dimension") for event in result["events"]}
    assert mapped["E-1"] == "公司背景"
    clear_mapping_cache()


def test_dimension_mapping_node_switch_enabled_allows_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """开关开启且模型可用时应走模型归类（SUCCESS）。"""
    from app.agents.dimension_mapping import clear_mapping_cache, dimension_mapping_node
    from app.config import get_settings

    clear_mapping_cache()
    monkeypatch.setattr(get_settings(), "llm_dimension_mapping", True, raising=False)

    class _StubLLM:
        def invoke(self, *, system_prompt: str, user_prompt: str, response_model):
            return response_model.model_validate(
                {"mappings": [{"evidence_id": "E-1", "new_dimension": "知识产权"}]}
            )

    monkeypatch.setattr("app.llm.factory.build_llm_client", lambda settings=None: _StubLLM())

    state = {
        "supplier_id": "S-2",
        "current_week": 10,
        "enable_live_llm": True,
        "risk_grade": {"window_weeks": 12},
        "events": [
            {
                "evidence_id": "E-1",
                "supplier_id": "S-2",
                "event_category": "经营",
                "event_subtype": "专利侵权",
                "event_severity": 1,
                "event_week": 10,
                "source_type": "知识产权局",
            }
        ],
    }
    result = dimension_mapping_node(state)
    assert result["dimension_mapping_result"]["status"] == "SUCCESS"
    mapped = {event["evidence_id"]: event.get("mapped_dimension") for event in result["events"]}
    assert mapped["E-1"] == "知识产权"
    clear_mapping_cache()

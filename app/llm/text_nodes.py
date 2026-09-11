"""全链路 LLM 文案节点的公共调用范式。

除决策建议外，风险识别 / 关联分析 / 证据转述等节点也需要让真实模型产出自然语言。
本模块把这套「确定性事实 + 模型文案 + 失败降级」的范式抽成公共函数，避免各节点
各写一套调用与降级逻辑（DRY），并统一日志口径（只记录 error_type，不打印 Key/响应体）。

设计边界：
- 只生成自然语言文案；数值、枚举、分级、阈值判断仍由规则引擎确定性产出；
- 模型不可用（无 Key / 余额不足 / 超时 / 输出不合法）时回退模板，并标记 FALLBACK；
- 未启用真实模型（enable_live_llm=false）或节点开关关闭时标记 DISABLED。
"""

from __future__ import annotations

import json
import logging
from typing import Callable, TypeVar

from pydantic import BaseModel

from app.config import Settings, get_settings
from app.llm import factory as llm_factory

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)


def _default_client(settings: Settings):
    """延迟解析，便于测试通过 patch `app.llm.factory.build_llm_client` 注入假客户端。"""
    return llm_factory.build_llm_client(settings)


def invoke_text_llm(
    *,
    node: str,
    system_prompt: str,
    payload: dict,
    response_model: type[T],
    enabled: bool,
    fallback: Callable[[], T],
    validate: Callable[[T], None] | None = None,
    settings: Settings | None = None,
) -> tuple[T, str]:
    """调用真实模型生成结构化文案，失败时回退确定性模板。

    返回 ``(结果, 状态)``，状态取值：SUCCESS / FALLBACK / DISABLED。
    """
    if not enabled:
        return fallback(), "DISABLED"

    settings = settings or get_settings()
    try:
        result = _default_client(settings).invoke(
            system_prompt=system_prompt,
            user_prompt=json.dumps(payload, ensure_ascii=False),
            response_model=response_model,
        )
        if validate is not None:
            validate(result)
        return result, "SUCCESS"
    except Exception as exc:  # 任何模型侧异常都降级，保证报告可生成
        logger.warning("llm_text_fallback node=%s error_type=%s", node, type(exc).__name__)
        return fallback(), "FALLBACK"


__all__ = ["invoke_text_llm"]

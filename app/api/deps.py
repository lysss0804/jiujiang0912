"""接口依赖：调用链 ID 与 API Key 鉴权。"""

from __future__ import annotations

from uuid import uuid4

from fastapi import Header, Request

from app.common.errors import JiujiangError
from app.config import get_settings


class UnauthorizedError(JiujiangError):
    """缺少或错误的 API Key。"""


def make_trace_id() -> str:
    return uuid4().hex


def get_trace_id(request: Request) -> str:
    """优先沿用上游传入的 X-Trace-Id，保证跨服务链路可追踪。"""
    trace_id = getattr(request.state, "trace_id", "") or request.headers.get("X-Trace-Id", "")
    return trace_id.strip() or make_trace_id()


async def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> str | None:
    """API Key 鉴权。

    - ``API_KEY`` 未配置（默认）：跳过校验，方便本地联调与自动化测试；
    - 配置后：必须携带匹配的 ``X-API-Key``，否则 401。
    """
    request.state.trace_id = get_trace_id(request)
    expected = (get_settings().api_key or "").strip()
    if not expected:
        return None
    if (x_api_key or "").strip() != expected:
        raise UnauthorizedError("invalid or missing X-API-Key")
    return x_api_key


__all__ = ["UnauthorizedError", "make_trace_id", "get_trace_id", "require_api_key"]

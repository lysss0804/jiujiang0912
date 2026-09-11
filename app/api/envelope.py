"""统一响应信封与错误模型。

后端对接约定：所有接口（含错误）都返回同一层外壳，HTTP 状态码与 body 内
``code`` 语义保持一致，body 永远可解析，便于网关/前端统一处理。

    {
      "code": 0,
      "message": "ok",
      "data": { ... },
      "trace_id": "3f2c..."
    }
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorCode:
    """业务错误码（与 HTTP 状态码语义一致）。"""

    OK = 0
    INVALID_REQUEST = 40000
    UNAUTHORIZED = 40100
    NOT_FOUND = 40400
    UNPROCESSABLE = 42200
    INTERNAL_ERROR = 50000


# 业务错误码 -> HTTP 状态码
HTTP_STATUS_BY_CODE: dict[int, int] = {
    ErrorCode.OK: 200,
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.UNPROCESSABLE: 422,
    ErrorCode.INTERNAL_ERROR: 500,
}


class Envelope(BaseModel, Generic[T]):
    """统一响应外壳。``data`` 允许为 null（例如失败响应）。"""

    code: int = ErrorCode.OK
    message: str = "ok"
    data: T | None = Field(default=None)
    trace_id: str = ""


def ok(data: Any, trace_id: str = "") -> dict[str, Any]:
    return {"code": ErrorCode.OK, "message": "ok", "data": data, "trace_id": trace_id}


def fail(code: int, message: str, trace_id: str = "", data: Any = None) -> dict[str, Any]:
    return {"code": code, "message": message, "data": data, "trace_id": trace_id}


def http_status_for(code: int) -> int:
    return HTTP_STATUS_BY_CODE.get(code, 500)


__all__ = ["Envelope", "ErrorCode", "HTTP_STATUS_BY_CODE", "ok", "fail", "http_status_for"]

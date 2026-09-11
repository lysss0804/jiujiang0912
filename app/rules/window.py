"""统计窗口解析与归一化（纯确定性、无 LLM、无副作用）。

设计要点：
- 外部可按「周」或「月」指定统计窗口，系统内部统一归一化为**周区间**
  （`start_week` / `end_week`），保证全链路只有一套口径；
- 1 月 = 4 周（`weeks_per_month` 可配），便于后续按银行口径替换；
- 不传新参数时，行为与改造前完全一致（默认近 12 周）；
- 解析优先级：显式入参 > Settings > rules.yaml > 内置默认。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.contracts import StrictModel

DEFAULT_WINDOW_UNIT: Literal["week", "month"] = "week"
DEFAULT_WINDOW_SIZE = 12
DEFAULT_WEEKS_PER_MONTH = 4
MAX_WEEK = 52


class WindowSpec(StrictModel):
    """归一化后的统计窗口。所有下游节点只认这个对象，不再各自推导。"""

    unit: Literal["week", "month"] = DEFAULT_WINDOW_UNIT
    size: int = Field(default=DEFAULT_WINDOW_SIZE, ge=1, le=52)
    current_week: int = Field(ge=1, le=MAX_WEEK)
    start_week: int = Field(ge=1, le=MAX_WEEK)
    end_week: int = Field(ge=1, le=MAX_WEEK)
    # 归一化后的窗口长度（周），供分级引擎与文档统一引用
    window_weeks: int = Field(default=DEFAULT_WINDOW_SIZE, ge=1, le=MAX_WEEK)
    # 可直接写入报告的可读描述，如「近12周」/「近3个月（第19-30周）」
    display: str = "近12周"

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump()


def _to_window_weeks(*, unit: str, size: int, weeks_per_month: int) -> int:
    """把「月」换算为「周」（1 月 = weeks_per_month 周），并夹到 [1, 52]。"""
    if str(unit).lower() == "month":
        weeks = size * max(1, int(weeks_per_month))
    else:
        weeks = int(size)
    return max(1, min(MAX_WEEK, weeks))


def _build_display(*, unit: str, size: int, window_weeks: int, start_week: int, end_week: int) -> str:
    """生成可读的窗口描述。

    按周时保持与改造前一致的简洁文本（如「近12周」）；
    按月时补充归一化后的周区间，便于人工核对口径（如「近3个月（第41-52周）」）。
    """
    if str(unit).lower() == "month":
        return f"近{size}个月（第{start_week}-{end_week}周）"
    return f"近{window_weeks}周"


def resolve_window(
    *,
    current_week: int,
    unit: str | None = None,
    size: int | None = None,
    weeks_per_month: int | None = None,
    window_weeks: int | None = None,
    rules: dict[str, Any] | None = None,
) -> WindowSpec:
    """解析统计窗口并归一化为周区间。

    优先级（从高到低）：
    1. 显式入参 `unit` / `size`（或兼容旧口径的 `window_weeks`）；
    2. `Settings.window_unit` / `Settings.window_size` / `Settings.window_weeks_per_month`；
    3. `rules.yaml` 的 `window_unit` / `window_size` / `weeks_per_month`；
    4. 内置默认（week / 12 / 4）。

    兼容性：显式传入 `window_weeks`（旧参数）时按「周」处理，行为与改造前一致。
    """
    rules = rules or {}
    settings = None
    if unit is None or size is None or weeks_per_month is None:
        # 延迟导入，避免 rules 配置加载与应用配置互相依赖
        from app.config import get_settings

        settings = get_settings()

    resolved_unit = str(
        unit
        or getattr(settings, "window_unit", None)
        or rules.get("window_unit")
        or DEFAULT_WINDOW_UNIT
    ).lower()
    if resolved_unit not in ("week", "month"):
        resolved_unit = DEFAULT_WINDOW_UNIT

    resolved_weeks_per_month = int(
        weeks_per_month
        or getattr(settings, "window_weeks_per_month", None)
        or rules.get("weeks_per_month")
        or DEFAULT_WEEKS_PER_MONTH
    )

    if size is not None:
        resolved_size = int(size)
    elif window_weeks is not None:
        resolved_size = int(window_weeks)
    else:
        resolved_size = int(
            getattr(settings, "window_size", None)
            or rules.get("window_size")
            or rules.get("recent_window_weeks")
            or DEFAULT_WINDOW_SIZE
        )

    week = max(1, min(MAX_WEEK, int(current_week)))
    resolved_size = max(1, min(MAX_WEEK, resolved_size))
    weeks = _to_window_weeks(
        unit=resolved_unit, size=resolved_size, weeks_per_month=resolved_weeks_per_month
    )
    start_week = max(1, week - weeks + 1)

    return WindowSpec(
        unit=resolved_unit,  # type: ignore[arg-type]
        size=resolved_size,
        current_week=week,
        start_week=start_week,
        end_week=week,
        window_weeks=weeks,
        display=_build_display(
            unit=resolved_unit,
            size=resolved_size,
            window_weeks=weeks,
            start_week=start_week,
            end_week=week,
        ),
    )


def shift_window(spec: WindowSpec, *, offset_weeks: int) -> tuple[int, int]:
    """返回相对 `spec` 平移 `offset_weeks` 的对照窗口区间（闭区间，下限夹到 1）。"""
    end = max(1, spec.start_week - 1) if offset_weeks == 0 else max(1, spec.end_week + offset_weeks)
    start = max(1, end - spec.window_weeks + 1)
    return start, end


__all__ = [
    "WindowSpec",
    "resolve_window",
    "shift_window",
    "DEFAULT_WINDOW_UNIT",
    "DEFAULT_WINDOW_SIZE",
    "DEFAULT_WEEKS_PER_MONTH",
]

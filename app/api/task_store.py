"""异步批量任务的内存任务表（进程内，原型实现）。

生产替换点：改用 Redis / 数据库并加分布式锁，接口签名保持不变。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Callable, Literal
from uuid import uuid4

TaskStatus = Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED"]


class TaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create(self) -> str:
        task_id = uuid4().hex
        with self._lock:
            self._tasks[task_id] = {
                "task_id": task_id,
                "status": "PENDING",
                "created_at": _now(),
                "started_at": None,
                "finished_at": None,
                "result": None,
                "error": None,
            }
        return task_id

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return dict(task) if task else None

    def submit(self, task_id: str, work: Callable[[], Any]) -> None:
        """把长任务丢到后台线程执行，接口立即返回 task_id。"""

        def _runner() -> None:
            self._update(task_id, status="RUNNING", started_at=_now())
            try:
                result = work()
                self._update(task_id, status="SUCCEEDED", result=result, finished_at=_now())
            except Exception as exc:  # 任务失败不影响服务进程
                self._update(
                    task_id,
                    status="FAILED",
                    error=f"{type(exc).__name__}: {exc}",
                    finished_at=_now(),
                )

        threading.Thread(target=_runner, name=f"analyze-{task_id[:8]}", daemon=True).start()

    def _update(self, task_id: str, **fields: Any) -> None:
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id].update(fields)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# 进程内单例，所有请求共享
task_store = TaskStore()

__all__ = ["TaskStore", "task_store", "TaskStatus"]

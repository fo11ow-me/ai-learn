"""内存任务队列（方案文档 3.4/4.4：MVP 零数据库，进程内 dict 存储；服务器重启丢任务可接受）"""
import time
import uuid
from typing import Callable, Literal

from pydantic import BaseModel

TaskStatus = Literal["pending", "running", "completed", "failed"]

DEFAULT_TTL_SECONDS = 1800  # 任务保留 30 分钟（方案文档 4.4）


class TaskError(BaseModel):
    """任务失败信息：错误码 + 用户可读信息（错误码见方案文档 4.4 约定）"""

    code: str
    message: str


class TaskInfo(BaseModel):
    """任务状态快照：GET 轮询接口的响应载体"""

    task_id: str
    status: TaskStatus
    payload: dict | None = None  # completed 时承载结果：{"quiz": ...} 或 {"report": ...}
    error: TaskError | None = None
    created_at: float


class TaskStore:
    """进程内任务状态存储。
    方法均为同步实现（单线程事件循环内无抢占），无需加锁"""

    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS, now_fn: Callable[[], float] = time.time):
        self._ttl = ttl_seconds
        self._now = now_fn
        self._tasks: dict[str, TaskInfo] = {}

    def create(self) -> str:
        """创建 pending 任务并返回 task_id（uuid4 hex）"""
        task_id = uuid.uuid4().hex
        self._tasks[task_id] = TaskInfo(task_id=task_id, status="pending", created_at=self._now())
        return task_id

    def get(self, task_id: str) -> TaskInfo | None:
        """查询任务快照；不存在返回 None（路由层映射为 404）"""
        return self._tasks.get(task_id)

    def count_running(self) -> int:
        """统计 running 状态任务数（WHY：路由提交处打印并发快照，观察任务队列健康）"""
        return sum(1 for task in self._tasks.values() if task.status == "running")

    def update(self, task_id: str, *, status: TaskStatus | None = None,
               payload: dict | None = None, error: TaskError | None = None) -> None:
        """更新任务状态（部分更新：只更新传入的非 None 字段；任务不存在时为 no-op）"""
        info = self._tasks.get(task_id)
        if info is None:
            return
        if status is not None:
            info.status = status
        if payload is not None:
            info.payload = payload
        if error is not None:
            info.error = error

    def cleanup(self) -> int:
        """清理超过 TTL 的任务并返回清理数（供惰性触发调用）"""
        now = self._now()
        expired = [tid for tid, info in self._tasks.items() if now - info.created_at > self._ttl]
        for tid in expired:
            del self._tasks[tid]
        return len(expired)


task_store = TaskStore()  # 模块级单例（路由层共用）

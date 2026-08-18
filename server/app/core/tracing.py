"""调用追踪基础设施（WHY：模型/工具层的日志需带 task_id 归属，但通用服务层不感知任务；
服务层入口用 ContextVar 注入——asyncio.create_task 自动复制 context，并发下归属仍正确）"""
from contextvars import ContextVar

TRACE_TRUNCATE_LIMIT = 2000  # 日志内容字段截断阈值（spec：截断阈值须为常量配置）

current_task_id: ContextVar[str | None] = ContextVar("current_task_id", default=None)


def task_id_kv() -> str:
    """从上下文取 task_id 拼成 key=value 前缀；未设置返回空串（WHY：统一拼进模型/工具层日志，
    与链路事件的 task_id= 字段格式一致，按 task_id 可聚合单次执行的全部调用）"""
    task_id = current_task_id.get()
    return f"task_id={task_id} " if task_id else ""


def truncate_for_log(text: str, limit: int = TRACE_TRUNCATE_LIMIT) -> str:
    """超长日志字段截断（WHY：Prompt/模型输出可达数千字符，整段输出会刷屏且单行过长；
    截断必须带标记，读者知道内容被截断过）"""
    return text if len(text) <= limit else f"{text[:limit]}…[截断]"

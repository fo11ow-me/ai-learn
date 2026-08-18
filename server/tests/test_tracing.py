"""调用追踪基础设施测试（task_id ContextVar 传递 + 日志字段截断）"""
import asyncio

from app.core.tracing import current_task_id, task_id_kv, truncate_for_log


def test_truncate_short_text_unchanged():
    assert truncate_for_log("短文本") == "短文本"


def test_truncate_long_text_with_marker():
    text = "x" * 3000
    out = truncate_for_log(text)
    assert out.startswith("x" * 2000)
    assert out.endswith("…[截断]")
    assert len(out) < len(text)


def test_truncate_custom_limit():
    assert truncate_for_log("abcdef", limit=3) == "abc…[截断]"


def test_task_id_kv_empty_without_context():
    assert task_id_kv() == ""


def test_task_id_kv_with_context():
    token = current_task_id.set("abc123")
    try:
        assert task_id_kv() == "task_id=abc123 "
    finally:
        current_task_id.reset(token)
    assert task_id_kv() == ""  # reset 后恢复


async def test_contextvar_propagates_to_subtask():
    """asyncio.create_task 复制调用方 context → 后台子任务内可见（WHY：run_quiz_task 由 create_task 启动，子任务内模型/工具日志必须带 task_id）"""

    async def child():
        return task_id_kv()

    token = current_task_id.set("t1")
    try:
        result = await asyncio.create_task(child())
        assert result == "task_id=t1 "
    finally:
        current_task_id.reset(token)

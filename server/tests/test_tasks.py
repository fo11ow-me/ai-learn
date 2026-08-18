"""内存任务队列测试（方案文档 4.4：任务状态流转与 30 分钟 TTL 清理）"""
from app.core.tasks import TaskError, TaskStore


class TestTaskStore:
    def test_create_returns_pending(self):
        store = TaskStore()
        task_id = store.create()
        info = store.get(task_id)
        assert info is not None
        assert info.task_id == task_id
        assert info.status == "pending"
        assert info.payload is None
        assert info.error is None

    def test_get_missing_returns_none(self):
        assert TaskStore().get("不存在") is None

    def test_update_transitions(self):
        store = TaskStore()
        task_id = store.create()
        store.update(task_id, status="running")
        assert store.get(task_id).status == "running"

        store.update(task_id, status="completed", payload={"quiz": {"topic": "测试"}})
        info = store.get(task_id)
        assert info.status == "completed"
        assert info.payload == {"quiz": {"topic": "测试"}}
        assert info.error is None

    def test_update_failed_with_error(self):
        store = TaskStore()
        task_id = store.create()
        store.update(task_id, status="failed", error=TaskError(code="LLM_TIMEOUT", message="大模型调用超时"))
        info = store.get(task_id)
        assert info.status == "failed"
        assert info.error.code == "LLM_TIMEOUT"
        assert info.payload is None

    def test_update_missing_is_noop(self):
        TaskStore().update("不存在", status="completed")  # 不应抛异常

    def test_cleanup_expired_only(self):
        now = [1000.0]
        store = TaskStore(ttl_seconds=1800, now_fn=lambda: now[0])
        old_id = store.create()
        now[0] += 1801  # 超过 TTL 1 秒
        fresh_id = store.create()

        removed = store.cleanup()

        assert removed == 1
        assert store.get(old_id) is None
        assert store.get(fresh_id) is not None

    def test_cleanup_keeps_unexpired(self):
        store = TaskStore(ttl_seconds=1800, now_fn=lambda: 1000.0)
        task_id = store.create()
        assert store.cleanup() == 0  # 未超 TTL 不清理
        assert store.get(task_id) is not None

    def test_count_running(self):
        store = TaskStore()
        assert store.count_running() == 0  # 空库为 0
        a, b = store.create(), store.create()
        store.update(a, status="running")
        assert store.count_running() == 1  # 仅 running 计数
        store.update(b, status="completed")
        store.update(a, status="completed")
        assert store.count_running() == 0  # 完成/失败不计入

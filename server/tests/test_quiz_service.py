"""出题任务服务测试（方案文档 4.4：正常 / LLM 失败 / 超时 / 敏感内容路径）"""
import asyncio

import pytest

from app.core.config import Settings
from app.core.tasks import TaskStore
from app.models.schemas import QuizSchema
from app.services.llm import LLMError
from app.services.quiz import run_quiz_task


class FakeLLM:
    """可编程的 LLMClient 替身：generate_quiz 按指定行为返回 / 抛错 / 挂起"""

    def __init__(self, quiz=None, error=None, hang=False):
        self._quiz = quiz
        self._error = error
        self._hang = hang

    async def generate_quiz(self, content):
        if self._hang:
            await asyncio.Event().wait()  # 挂起直至被任务超时取消
        if self._error:
            raise self._error
        return self._quiz


async def test_completed_with_quiz(settings, sensitive, make_valid_quiz):
    store = TaskStore()
    task_id = store.create()
    quiz = QuizSchema.model_validate(make_valid_quiz())

    await run_quiz_task(task_id, "光的波粒二象性", FakeLLM(quiz=quiz), sensitive, settings, store=store)

    info = store.get(task_id)
    assert info.status == "completed"
    assert info.payload == {"quiz": quiz.model_dump()}
    assert info.error is None


async def test_llm_error_marks_failed(settings, sensitive):
    store = TaskStore()
    task_id = store.create()

    await run_quiz_task(
        task_id, "内容", FakeLLM(error=LLMError("LLM_TIMEOUT", "大模型调用超时")), sensitive, settings, store=store
    )

    info = store.get(task_id)
    assert info.status == "failed"
    assert info.error.code == "LLM_TIMEOUT"  # 错误码透传（前端按码提示）


async def test_timeout_marks_task_timeout(sensitive):
    store = TaskStore()
    task_id = store.create()
    short_timeout = Settings(deepseek_api_key="test-key", task_timeout_seconds=0.01)

    await run_quiz_task(task_id, "内容", FakeLLM(hang=True), sensitive, short_timeout, store=store)

    info = store.get(task_id)
    assert info.status == "failed"
    assert info.error.code == "TASK_TIMEOUT"


async def test_sensitive_generated_content_fails(settings, sensitive, make_valid_quiz):
    store = TaskStore()
    task_id = store.create()
    data = make_valid_quiz()
    data["questions"][0]["explanation"] = "这段讲解里混入了赌博相关内容"
    quiz = QuizSchema.model_validate(data)

    await run_quiz_task(task_id, "内容", FakeLLM(quiz=quiz), sensitive, settings, store=store)

    info = store.get(task_id)
    assert info.status == "failed"
    assert info.error.code == "SENSITIVE_CONTENT"  # 生成结果同样受敏感词约束（方案文档 5.4）


async def test_unexpected_error_marks_unavailable(settings, sensitive):
    store = TaskStore()
    task_id = store.create()

    class BrokenLLM:
        async def generate_quiz(self, content):
            raise RuntimeError("boom")

    await run_quiz_task(task_id, "内容", BrokenLLM(), sensitive, settings, store=store)

    info = store.get(task_id)
    assert info.status == "failed"
    assert info.error.code == "LLM_UNAVAILABLE"

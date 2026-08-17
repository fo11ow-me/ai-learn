"""LLM 客户端测试（方案文档 4.4：失败重试 ≤2 次，指数退避 2s/4s；错误码归一化）"""
import asyncio

import pytest
from langchain_core.exceptions import OutputParserException

from app.models.schemas import AIReportSchema, QuizSchema
from app.services.llm import LLMClient, LLMError
from tests.conftest import make_valid_ai_report


class FakeStructuredModel:
    """模拟 with_structured_output 返回的对象：ainvoke 按 behaviors 依次执行，耗尽后复用最后一个"""

    def __init__(self, behaviors):
        self._behaviors = behaviors
        self._index = 0
        self.calls = 0

    async def ainvoke(self, prompt):
        self.calls += 1
        behavior = self._behaviors[min(self._index, len(self._behaviors) - 1)]
        self._index += 1
        result = behavior()
        if isinstance(result, Exception):
            raise result
        return result


class FakeBuilder:
    """模拟模型构建器：记录请求的 temperature，返回可编程行为的结构化模型"""

    def __init__(self, behaviors):
        self._behaviors = behaviors
        self.temperatures = []

    def __call__(self, temperature):
        self.temperatures.append(temperature)
        return self

    def with_structured_output(self, schema_cls, method=None):
        return FakeStructuredModel(self._behaviors)


@pytest.fixture
def fake_sleep(monkeypatch):
    """替换 llm 模块的 sleep：记录退避时长且不真正睡眠"""
    recorded = []

    async def _sleep(seconds):
        recorded.append(seconds)

    monkeypatch.setattr("app.services.llm.sleep", _sleep)
    return recorded


class TestGenerateQuiz:
    async def test_generate_quiz_success(self, settings, fake_sleep, make_valid_quiz):
        quiz = QuizSchema.model_validate(make_valid_quiz())
        builder = FakeBuilder([lambda: quiz])
        client = LLMClient(settings, build_model=builder)

        result = await client.generate_quiz("光的波粒二象性")

        assert result == quiz
        assert builder.temperatures == [0.7]  # 出题温度 0.7（方案文档 5.1）

    async def test_retry_once_then_success(self, settings, fake_sleep, make_valid_quiz):
        quiz = QuizSchema.model_validate(make_valid_quiz())
        builder = FakeBuilder([lambda: asyncio.TimeoutError(), lambda: quiz])
        client = LLMClient(settings, build_model=builder)

        result = await client.generate_quiz("光的波粒二象性")

        assert result == quiz
        assert fake_sleep == [2.0]  # 首次失败退避 2s 后重试

    async def test_retry_twice_then_success(self, settings, fake_sleep, make_valid_quiz):
        quiz = QuizSchema.model_validate(make_valid_quiz())
        builder = FakeBuilder(
            [lambda: asyncio.TimeoutError(), lambda: asyncio.TimeoutError(), lambda: quiz]
        )
        client = LLMClient(settings, build_model=builder)

        result = await client.generate_quiz("光的波粒二象性")

        assert result == quiz
        assert fake_sleep == [2.0, 4.0]  # 指数退避 2s/4s（方案文档 4.4）

    async def test_fail_after_three_attempts_timeout(self, settings, fake_sleep):
        builder = FakeBuilder([lambda: asyncio.TimeoutError()] * 3)
        client = LLMClient(settings, build_model=builder)

        with pytest.raises(LLMError) as exc_info:
            await client.generate_quiz("光的波粒二象性")

        assert exc_info.value.code == "LLM_TIMEOUT"
        assert fake_sleep == [2.0, 4.0]  # 3 次尝试共 2 次退避

    async def test_fail_after_three_attempts_parse(self, settings, fake_sleep):
        builder = FakeBuilder([lambda: OutputParserException("bad output")] * 3)
        client = LLMClient(settings, build_model=builder)

        with pytest.raises(LLMError) as exc_info:
            await client.generate_quiz("光的波粒二象性")

        assert exc_info.value.code == "LLM_PARSE_FAILED"

    async def test_fail_after_three_attempts_unavailable(self, settings, fake_sleep):
        builder = FakeBuilder([lambda: RuntimeError("connection refused")] * 3)
        client = LLMClient(settings, build_model=builder)

        with pytest.raises(LLMError) as exc_info:
            await client.generate_quiz("光的波粒二象性")

        assert exc_info.value.code == "LLM_UNAVAILABLE"


class TestGenerateReport:
    async def test_generate_report_success(self, settings, fake_sleep, make_valid_quiz):
        ai_report = AIReportSchema.model_validate(make_valid_ai_report())
        builder = FakeBuilder([lambda: ai_report])
        client = LLMClient(settings, build_model=builder)

        result = await client.generate_report(QuizSchema.model_validate(make_valid_quiz()), [])

        assert result == ai_report
        assert builder.temperatures == [0.5]  # 报告温度 0.5（方案文档 5.1）

"""LLM 客户端测试（方案文档 4.4：失败重试 ≤2 次，指数退避 2s/4s；错误码归一化）"""
import asyncio
import logging

import pytest
from langchain_core.exceptions import OutputParserException

from app.models.schemas import AIReportSchema, QuizSchema, SearchPlanSchema
from app.services.llm import LLMClient, LLMError
from tests.conftest import make_valid_ai_report


class FakeRawMessage:
    """模拟原始 AIMessage：带 usage_metadata（include_raw 模式返回 {raw, parsed} 中的 raw）"""

    def __init__(self, parsed, usage=None):
        self.parsed = parsed
        self.usage_metadata = usage or {"input_tokens": 100, "output_tokens": 50}


class FakeStructuredModel:
    """模拟 with_structured_output 返回的对象：ainvoke 按 behaviors 依次执行，耗尽后复用最后一个。
    include_raw=True 时返回 {raw, parsed}（与真实 langchain 行为一致）"""

    def __init__(self, behaviors, include_raw=False):
        self._behaviors = behaviors
        self._include_raw = include_raw
        self._index = 0
        self.calls = 0

    async def ainvoke(self, prompt):
        self.calls += 1
        behavior = self._behaviors[min(self._index, len(self._behaviors) - 1)]
        self._index += 1
        result = behavior()
        if isinstance(result, Exception):
            raise result
        if self._include_raw:
            return {"raw": FakeRawMessage(result), "parsed": result}
        return result


class FakeBuilder:
    """模拟模型构建器：记录请求的 temperature，返回可编程行为的结构化模型"""

    def __init__(self, behaviors):
        self._behaviors = behaviors
        self.temperatures = []

    def __call__(self, temperature):
        self.temperatures.append(temperature)
        return self

    def with_structured_output(self, schema_cls, method=None, include_raw=False):
        return FakeStructuredModel(self._behaviors, include_raw=include_raw)


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

    async def test_success_logs_attempt_and_elapsed(self, settings, fake_sleep, make_valid_quiz, caplog):
        """成功路径记录第几次成功 + 耗时（WHY：可观测性——重试 1-2 次才成功时，日志可见抖动）"""
        quiz = QuizSchema.model_validate(make_valid_quiz())
        builder = FakeBuilder([lambda: quiz])

        with caplog.at_level(logging.INFO, logger="app.services.llm"):
            await LLMClient(settings, build_model=builder).generate_quiz("光")

        assert any("LLM 调用成功（第 1/3 次" in r.message for r in caplog.records)

    async def test_success_logs_token_usage(self, settings, fake_sleep, make_valid_quiz, caplog):
        """成功日志附带 token 用量（usage_metadata 输入/输出；缺失时占位符 -）"""
        quiz = QuizSchema.model_validate(make_valid_quiz())

        with caplog.at_level(logging.INFO, logger="app.services.llm"):
            await LLMClient(settings, build_model=FakeBuilder([lambda: quiz])).generate_quiz("光")

        assert any("tokens=in=100 out=50" in r.message for r in caplog.records)

    async def test_debug_logs_prompt_and_output(self, settings, fake_sleep, make_valid_quiz, caplog):
        """DEBUG 级记录完整 Prompt 与结构化输出（WHY：不改代码重建调用现场）"""
        quiz = QuizSchema.model_validate(make_valid_quiz())

        with caplog.at_level(logging.DEBUG, logger="app.services.llm"):
            await LLMClient(settings, build_model=FakeBuilder([lambda: quiz])).generate_quiz("光的波粒二象性")

        messages = [r.message for r in caplog.records]
        assert any("LLM 输入：" in m and "光的波粒二象性" in m for m in messages)
        assert any("LLM 输出（QuizSchema）：" in m and "波粒二象性" in m for m in messages)

    async def test_debug_long_prompt_truncated(self, settings, fake_sleep, make_valid_quiz, caplog):
        """超长 Prompt 在 DEBUG 日志中截断并带标记"""
        quiz = QuizSchema.model_validate(make_valid_quiz())

        with caplog.at_level(logging.DEBUG, logger="app.services.llm"):
            await LLMClient(settings, build_model=FakeBuilder([lambda: quiz])).generate_quiz("光" * 5000)

        assert any("LLM 输入：" in r.message and "…[截断]" in r.message for r in caplog.records)

    async def test_failure_logs_exc_info(self, settings, fake_sleep, make_valid_quiz, caplog):
        """失败重试日志携带完整异常栈（exc_info），不再只打消息"""
        quiz = QuizSchema.model_validate(make_valid_quiz())

        with caplog.at_level(logging.WARNING, logger="app.services.llm"):
            await LLMClient(settings, build_model=FakeBuilder([lambda: asyncio.TimeoutError(), lambda: quiz])).generate_quiz("光")

        failure_records = [r for r in caplog.records if "LLM 调用失败" in r.message]
        assert failure_records and all(r.exc_info is not None for r in failure_records)

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


class TestPlanSearch:
    async def test_plan_search_success_search_mode(self, settings, fake_sleep):
        """plan_search 正常返回 search 模式计划，temperature=0.3"""
        plan = SearchPlanSchema.model_validate(
            {"mode": "search", "keywords": ["Harness Engineering"]}
        )
        builder = FakeBuilder([lambda: plan])
        client = LLMClient(settings, build_model=builder)

        result = await client.plan_search("我想学 Harness Engineering")

        assert result == plan
        assert builder.temperatures == [0.3]

    async def test_plan_search_success_extract_mode(self, settings, fake_sleep):
        """plan_search 正常返回 extract 模式计划"""
        plan = SearchPlanSchema.model_validate(
            {"mode": "extract", "url": "https://docs.tavily.com"}
        )
        client = LLMClient(settings, build_model=FakeBuilder([lambda: plan]))

        result = await client.plan_search("学习 https://docs.tavily.com 的用法")

        assert result.mode == "extract"
        assert result.url == "https://docs.tavily.com"

    async def test_plan_search_failure_raises_llm_error(self, settings, fake_sleep):
        """计划生成失败（三次尝试）→ LLMError 透传（流水线据此降级）"""
        builder = FakeBuilder([lambda: RuntimeError("connection refused")] * 3)
        client = LLMClient(settings, build_model=builder)

        with pytest.raises(LLMError) as exc_info:
            await client.plan_search("词")

        assert exc_info.value.code == "LLM_UNAVAILABLE"


class TestGenerateReport:
    async def test_generate_report_success(self, settings, fake_sleep, make_valid_quiz):
        ai_report = AIReportSchema.model_validate(make_valid_ai_report())
        builder = FakeBuilder([lambda: ai_report])
        client = LLMClient(settings, build_model=builder)

        result = await client.generate_report(QuizSchema.model_validate(make_valid_quiz()), [])

        assert result == ai_report
        assert builder.temperatures == [0.5]  # 报告温度 0.5（方案文档 5.1）

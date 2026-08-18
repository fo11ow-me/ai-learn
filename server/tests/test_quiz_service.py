"""出题任务服务测试（方案文档 4.4：正常 / LLM 失败 / 超时 / 敏感内容路径 + 联网检索段分支与降级）"""
import asyncio
import logging

import pytest

from app.core.config import Settings
from app.core.tasks import TaskStore
from app.core.tracing import current_task_id
from app.models.schemas import QuizSchema, SearchPlanSchema
from app.services.llm import LLMError
from app.services.quiz import is_url, run_quiz_task
from app.services.search import SearchError
from tests.conftest import FakeSearchClient


class FakeLLM:
    """可编程的 LLMClient 替身：generate_quiz / plan_search 按指定行为返回 / 抛错 / 挂起"""

    def __init__(self, quiz=None, error=None, hang=False, plan=None, plan_error=None):
        self._quiz = quiz
        self._error = error
        self._hang = hang
        self._plan = plan
        self._plan_error = plan_error
        self.plan_calls: list[str] = []
        self.search_results_received: list[str | None] = []

    async def generate_quiz(self, content, search_results=None):
        self.search_results_received.append(search_results)
        if self._hang:
            await asyncio.Event().wait()  # 挂起直至被任务超时取消
        if self._error:
            raise self._error
        return self._quiz

    async def plan_search(self, content):
        self.plan_calls.append(content)
        if self._plan_error:
            raise self._plan_error
        if self._plan is None:
            raise RuntimeError("plan 未配置")
        return self._plan


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
        async def generate_quiz(self, content, search_results=None):
            raise RuntimeError("boom")

    await run_quiz_task(task_id, "内容", BrokenLLM(), sensitive, settings, store=store)

    info = store.get(task_id)
    assert info.status == "failed"
    assert info.error.code == "LLM_UNAVAILABLE"


def _search_settings(**overrides) -> Settings:
    """配置了真实 key 的测试配置（联网搜索可走检索段）"""
    return Settings(deepseek_api_key="test-key", tavily_api_key="tvly-test", **overrides)


class TestIsUrl:
    def test_pure_url_matches(self):
        assert is_url("https://docs.tavily.com")
        assert is_url("http://example.com/path?a=1")

    def test_text_with_url_does_not_match(self):
        assert not is_url("帮我学习 https://docs.tavily.com 的用法")

    def test_invalid_format_does_not_match(self):
        assert not is_url("https://")
        assert not is_url("docs.tavily.com")  # 无协议
        assert not is_url("hello world")


class TestSearchEnhancedPipeline:
    async def test_pure_url_extracts_without_plan(self, sensitive, make_valid_quiz):
        """纯 URL 输入 → 直达 extract，且不调用 LLM 检索计划"""
        store = TaskStore()
        task_id = store.create()
        search = FakeSearchClient(extract_result="页面正文资料")
        llm = FakeLLM(quiz=QuizSchema.model_validate(make_valid_quiz()), plan=SearchPlanSchema.model_validate({"mode": "search", "keywords": ["不应被调用"]}))

        await run_quiz_task(
            task_id, "https://docs.tavily.com", llm, sensitive, _search_settings(), store=store, search=search
        )

        assert store.get(task_id).status == "completed"
        assert search.extract_calls == ["https://docs.tavily.com"]
        assert llm.plan_calls == []  # 纯 URL 跳过检索计划（省一次 LLM 调用）
        assert llm.search_results_received == ["页面正文资料"]

    async def test_search_branch_injects_materials(self, sensitive, make_valid_quiz):
        """检索计划 mode=search → 逐关键词搜索，资料注入出题"""
        store = TaskStore()
        task_id = store.create()
        search = FakeSearchClient(search_result="引用 1｜标题｜https://x.com\n正文")
        plan = SearchPlanSchema.model_validate({"mode": "search", "keywords": ["Harness Engineering", "AI 测试"], "count": 4})
        llm = FakeLLM(quiz=QuizSchema.model_validate(make_valid_quiz()), plan=plan)

        await run_quiz_task(
            task_id, "我想学 Harness Engineering", llm, sensitive, _search_settings(), store=store, search=search
        )

        assert store.get(task_id).status == "completed"
        assert search.search_calls == [("Harness Engineering", 4, "basic"), ("AI 测试", 4, "basic")]
        assert "引用 1｜标题" in llm.search_results_received[0]

    async def test_mixed_input_extract_branch(self, sensitive, make_valid_quiz):
        """混合输入（文本+URL）→ 由检索计划判定 mode=extract 并提取"""
        store = TaskStore()
        task_id = store.create()
        search = FakeSearchClient(extract_result="页面正文")
        plan = SearchPlanSchema.model_validate({"mode": "extract", "url": "https://example.com/doc"})
        llm = FakeLLM(quiz=QuizSchema.model_validate(make_valid_quiz()), plan=plan)

        await run_quiz_task(
            task_id, "学习 https://example.com/doc 的原理", llm, sensitive, _search_settings(), store=store, search=search
        )

        assert store.get(task_id).status == "completed"
        assert llm.plan_calls == ["学习 https://example.com/doc 的原理"]
        assert search.extract_calls == ["https://example.com/doc"]

    async def test_plan_extract_invalid_url_degrades(self, sensitive, make_valid_quiz):
        """检索计划 mode=extract 但 url 无效（如 https://）→ 不发起提取，直接一段式出题
        （WHY：无效 URL 进 Tavily 白耗一次调用且丢失搜索增强；实测 LLM 会把「我想学 https:// 的知识」误判为 extract）"""
        store = TaskStore()
        task_id = store.create()
        search = FakeSearchClient(extract_result="不应被调用")
        plan = SearchPlanSchema.model_validate({"mode": "extract", "url": "https://"})
        llm = FakeLLM(quiz=QuizSchema.model_validate(make_valid_quiz()), plan=plan)

        await run_quiz_task(
            task_id, "我想学 https:// 的知识", llm, sensitive, _search_settings(), store=store, search=search
        )

        assert store.get(task_id).status == "completed"
        assert search.extract_calls == []  # 未发起无效提取
        assert llm.search_results_received == [None]  # 无资料，一段式出题

    async def test_missing_key_degrades(self, sensitive, make_valid_quiz):
        """未配置 key → 不发起任何检索，资料为 None（降级路径与现状一致）"""
        store = TaskStore()
        task_id = store.create()
        search = FakeSearchClient()
        llm = FakeLLM(quiz=QuizSchema.model_validate(make_valid_quiz()))

        await run_quiz_task(
            task_id, "内容", llm, sensitive, Settings(deepseek_api_key="test-key"), store=store, search=search
        )

        assert store.get(task_id).status == "completed"
        assert search.search_calls == [] and search.extract_calls == []
        assert llm.search_results_received == [None]

    async def test_disabled_switch_degrades(self, sensitive, make_valid_quiz):
        """SEARCH_ENABLED=false → 不发起任何检索"""
        store = TaskStore()
        task_id = store.create()
        search = FakeSearchClient()
        llm = FakeLLM(quiz=QuizSchema.model_validate(make_valid_quiz()))

        await run_quiz_task(
            task_id, "内容", llm, sensitive, _search_settings(search_enabled=False), store=store, search=search
        )

        assert store.get(task_id).status == "completed"
        assert search.search_calls == [] and search.extract_calls == []

    async def test_plan_failure_degrades(self, sensitive, make_valid_quiz):
        """检索计划生成失败 → 降级出题，不调用搜索"""
        store = TaskStore()
        task_id = store.create()
        search = FakeSearchClient(search_result="不应被使用")
        llm = FakeLLM(
            quiz=QuizSchema.model_validate(make_valid_quiz()),
            plan_error=LLMError("LLM_UNAVAILABLE", "计划失败"),
        )

        await run_quiz_task(task_id, "内容", llm, sensitive, _search_settings(), store=store, search=search)

        assert store.get(task_id).status == "completed"
        assert search.search_calls == [] and search.extract_calls == []
        assert llm.search_results_received == [None]

    async def test_search_failure_degrades(self, sensitive, make_valid_quiz):
        """搜索调用失败 → 降级出题"""
        store = TaskStore()
        task_id = store.create()
        search = FakeSearchClient(search_error=SearchError("搜索失败"))
        plan = SearchPlanSchema.model_validate({"mode": "search", "keywords": ["词"]})
        llm = FakeLLM(quiz=QuizSchema.model_validate(make_valid_quiz()), plan=plan)

        await run_quiz_task(task_id, "内容", llm, sensitive, _search_settings(), store=store, search=search)

        assert store.get(task_id).status == "completed"
        assert llm.search_results_received == [None]

    async def test_extract_failure_degrades(self, sensitive, make_valid_quiz):
        """网页提取失败 → 降级出题"""
        store = TaskStore()
        task_id = store.create()
        search = FakeSearchClient(extract_error=SearchError("提取失败"))
        llm = FakeLLM(quiz=QuizSchema.model_validate(make_valid_quiz()))

        await run_quiz_task(
            task_id, "https://example.com/x", llm, sensitive, _search_settings(), store=store, search=search
        )

        assert store.get(task_id).status == "completed"
        assert llm.search_results_received == [None]

    async def test_search_timeout_degrades(self, sensitive, make_valid_quiz, monkeypatch):
        """检索段超时（挂起）→ 降级出题，不把超时扩散为任务失败"""
        monkeypatch.setattr("app.services.quiz.SEARCH_BUDGET_SECONDS", 0.01)
        store = TaskStore()
        task_id = store.create()

        class HangingSearch:
            async def search(self, query, count, depth):
                await asyncio.Event().wait()

        llm = FakeLLM(quiz=QuizSchema.model_validate(make_valid_quiz()), plan=SearchPlanSchema.model_validate({"mode": "search", "keywords": ["词"]}))

        await run_quiz_task(task_id, "内容", llm, sensitive, _search_settings(), store=store, search=HangingSearch())

        assert store.get(task_id).status == "completed"
        assert llm.search_results_received == [None]

    async def test_sensitive_in_materials_does_not_fail(self, sensitive, make_valid_quiz):
        """检索资料含敏感词不影响出题（敏感检查只作用于生成结果，不作用于输入资料）"""
        store = TaskStore()
        task_id = store.create()
        search = FakeSearchClient(search_result="资料里混入了赌博相关内容")
        llm = FakeLLM(
            quiz=QuizSchema.model_validate(make_valid_quiz()),
            plan=SearchPlanSchema.model_validate({"mode": "search", "keywords": ["词"]}),
        )

        await run_quiz_task(task_id, "内容", llm, sensitive, _search_settings(), store=store, search=search)

        assert store.get(task_id).status == "completed"

    async def test_sensitive_quiz_with_materials_still_fails(self, sensitive, make_valid_quiz):
        """检索资料注入后，生成的题库含敏感词仍返回 SENSITIVE_CONTENT（错误码不变）"""
        store = TaskStore()
        task_id = store.create()
        data = make_valid_quiz()
        data["questions"][0]["explanation"] = "讲解中混入赌博相关内容"
        search = FakeSearchClient(search_result="正常资料")
        llm = FakeLLM(
            quiz=QuizSchema.model_validate(data),
            plan=SearchPlanSchema.model_validate({"mode": "search", "keywords": ["词"]}),
        )

        await run_quiz_task(task_id, "内容", llm, sensitive, _search_settings(), store=store, search=search)

        info = store.get(task_id)
        assert info.status == "failed"
        assert info.error.code == "SENSITIVE_CONTENT"


class TestTraceLogs:
    """结构化链路日志（可观测性：每次执行在日志中完整可重建，task_id 贯穿各步骤）"""

    async def test_success_chain_records_steps(self, settings, sensitive, make_valid_quiz, caplog):
        store = TaskStore()
        task_id = store.create()
        llm = FakeLLM(quiz=QuizSchema.model_validate(make_valid_quiz()))

        with caplog.at_level(logging.INFO, logger="app.services.quiz"):
            await run_quiz_task(task_id, "内容", llm, sensitive, settings, store=store)

        messages = [r.message for r in caplog.records]
        assert any(m.startswith(f"quiz task start task_id={task_id}") for m in messages)
        assert any("quiz llm ok" in m and f"task_id={task_id}" in m for m in messages)
        assert any(
            f"quiz task done task_id={task_id}" in m and "status=completed" in m and "elapsed=" in m
            for m in messages
        )

    async def test_search_degrade_records_reason(self, settings, sensitive, make_valid_quiz, caplog):
        store = TaskStore()
        task_id = store.create()
        search = FakeSearchClient(search_error=SearchError("搜索失败"))
        plan = SearchPlanSchema.model_validate({"mode": "search", "keywords": ["词"]})
        llm = FakeLLM(quiz=QuizSchema.model_validate(make_valid_quiz()), plan=plan)

        with caplog.at_level(logging.INFO, logger="app.services.quiz"):
            await run_quiz_task(task_id, "内容", llm, sensitive, _search_settings(), store=store, search=search)

        assert any(
            f"quiz search degrade task_id={task_id}" in r.message
            and "reason=SearchError" in r.message
            and "error=搜索失败" in r.message  # 错误消息必须随日志输出（WHY：定位 Tavily 失败原因的关键信息）
            for r in caplog.records
        )

    async def test_search_skip_records_reason(self, settings, sensitive, make_valid_quiz, caplog):
        store = TaskStore()
        task_id = store.create()
        llm = FakeLLM(quiz=QuizSchema.model_validate(make_valid_quiz()))

        with caplog.at_level(logging.INFO, logger="app.services.quiz"):
            await run_quiz_task(
                task_id, "内容", llm, sensitive, Settings(deepseek_api_key="test-key"), store=store
            )

        assert any(f"quiz search skip task_id={task_id}" in r.message for r in caplog.records)

    async def test_url_extract_records_branch(self, settings, sensitive, make_valid_quiz, caplog):
        store = TaskStore()
        task_id = store.create()
        search = FakeSearchClient(extract_result="页面资料")
        llm = FakeLLM(quiz=QuizSchema.model_validate(make_valid_quiz()))

        with caplog.at_level(logging.INFO, logger="app.services.quiz"):
            await run_quiz_task(
                task_id, "https://example.com/x", llm, sensitive, _search_settings(), store=store, search=search
            )

        assert any(f"quiz search url_extract task_id={task_id}" in r.message for r in caplog.records)


class TestTaskIdContext:
    """current_task_id ContextVar 注入与释放（WHY：任务执行期间 LLM/搜索追踪日志经 task_id_kv() 自动聚合，
    任务结束后必须释放，否则下一任务日志串上旧 task_id）"""

    class _RecordingLLM:
        """记录 generate_quiz 调用时刻的 ctx task_id，供断言「任务体内已注入」"""

        def __init__(self, quiz):
            self._quiz = quiz
            self.seen: list[str | None] = []

        async def generate_quiz(self, content, search_results=None):
            self.seen.append(current_task_id.get())
            return self._quiz

        async def plan_search(self, content):
            return SearchPlanSchema.model_validate({"mode": "search", "keywords": ["词"]})

    async def test_ctx_injected_during_task_and_restored_after(
        self, settings, sensitive, make_valid_quiz
    ):
        store = TaskStore()
        task_id = store.create()
        llm = self._RecordingLLM(QuizSchema.model_validate(make_valid_quiz()))

        await run_quiz_task(task_id, "内容", llm, sensitive, settings, store=store)

        assert llm.seen == [task_id]  # 任务体内 LLM 调用时 ctx 已注入
        assert current_task_id.get() is None  # 任务结束后恢复（不带旧值）

    async def test_ctx_restored_on_failure(self, settings, sensitive, make_valid_quiz):
        store = TaskStore()
        task_id = store.create()
        llm = FakeLLM(error=LLMError("LLM_TIMEOUT", "超时"))

        await run_quiz_task(task_id, "内容", llm, sensitive, settings, store=store)

        assert current_task_id.get() is None  # 失败路径 finally 同样释放

    async def test_deep_llm_logs_carry_task_id(self, settings, sensitive, make_valid_quiz, caplog):
        """真 LLMClient（FakeBuilder 注入）在任务内产生的追踪日志带 task_id（跨层聚合证据）"""
        from app.services.llm import LLMClient

        store = TaskStore()
        task_id = store.create()
        quiz = QuizSchema.model_validate(make_valid_quiz())
        builder = _QuizFakeBuilder([quiz])

        with caplog.at_level(logging.INFO, logger="app.services.llm"):
            await run_quiz_task(task_id, "内容", LLMClient(settings, build_model=builder), sensitive, settings, store=store)

        assert any(
            f"task_id={task_id}" in r.message and "LLM 调用成功" in r.message for r in caplog.records
        )


class _QuizFakeBuilder:
    """最小 LLM 构建替身：返回可编程结构化模型（include_raw 形态，与真 langchain 一致）"""

    class _StructuredModel:
        def __init__(self, result):
            self._result = result

        async def ainvoke(self, prompt):
            return {"raw": object(), "parsed": self._result}

    def __init__(self, results):
        self._results = results
        self._index = 0

    def __call__(self, temperature):
        return self

    def with_structured_output(self, schema_cls, method=None, include_raw=False):
        result = self._results[min(self._index, len(self._results) - 1)]
        self._index += 1
        return self._StructuredModel(result)

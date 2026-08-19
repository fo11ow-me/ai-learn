"""出题任务服务测试（方案文档 4.4：正常 / LLM 失败 / 超时 / 敏感内容路径 + 联网检索段分支与降级
+ RAG 知识库段：严格模式/判定/缺口补缺/降级矩阵）"""
import asyncio
import logging

import pytest

from app.core.config import Settings
from app.core.tasks import TaskStore
from app.core.tracing import current_task_id
from app.models.schemas import KnowledgeJudgeResult, QuizSchema, SearchPlanSchema
from app.services.knowledge_base import KBError, ChunkHit
from app.services.llm import LLMError
from app.services.quiz import is_url, run_quiz_task
from app.services.search import SearchError
from tests.conftest import FakeKnowledgeBase, FakeSearchClient


class FakeLLM:
    """可编程的 LLMClient 替身：generate_quiz / plan_search / judge_knowledge_sufficient 按指定行为返回 / 抛错 / 挂起"""

    def __init__(self, quiz=None, error=None, hang=False, plan=None, plan_error=None, judge=None, judge_error=None):
        self._quiz = quiz
        self._error = error
        self._hang = hang
        self._plan = plan
        self._plan_error = plan_error
        self._judge = judge
        self._judge_error = judge_error
        self.plan_calls: list[tuple] = []
        self.search_results_received: list[str | None] = []
        self.doc_materials_received: list[str | None] = []
        self.judge_calls: list[tuple] = []

    async def generate_quiz(self, content, search_results=None, doc_materials=None):
        self.search_results_received.append(search_results)
        self.doc_materials_received.append(doc_materials)
        if self._hang:
            await asyncio.Event().wait()  # 挂起直至被任务超时取消
        if self._error:
            raise self._error
        return self._quiz

    async def plan_search(self, content, missing_topics=None):
        self.plan_calls.append((content, missing_topics))
        if self._plan_error:
            raise self._plan_error
        if self._plan is None:
            raise RuntimeError("plan 未配置")
        return self._plan

    async def judge_knowledge_sufficient(self, content, materials):
        self.judge_calls.append((content, materials))
        if self._judge_error:
            raise self._judge_error
        if self._judge is None:
            raise RuntimeError("judge 未配置")
        return self._judge


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
        assert llm.plan_calls == [("学习 https://example.com/doc 的原理", None)]  # 无缺口时 missing_topics=None
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

        async def generate_quiz(self, content, search_results=None, doc_materials=None):
            self.seen.append(current_task_id.get())
            return self._quiz

        async def plan_search(self, content, missing_topics=None):
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


class TestKnowledgeBasePipeline:
    """RAG 知识库段（设计 D4）：严格模式 / 未选库判定 / 缺口定向补缺 / 降级矩阵。
    全部分支零外部依赖：FakeKnowledgeBase 注入命中/异常，FakeLLM 注入判定结果"""

    def _settings(self, **overrides) -> Settings:
        """知识库 + 联网全配置（降级分支按用例覆盖开关）"""
        base = dict(deepseek_api_key="test-key", tavily_api_key="tvly-test", embedding_api_key="test-embed-key")
        base.update(overrides)
        return Settings(**base)

    @staticmethod
    def _hit(text="私有知识库文档的独特内容" * 8, score=0.9, kb_id=1, doc_id=1, filename="doc.txt", chunk_index=0) -> ChunkHit:
        return ChunkHit(doc_id=doc_id, kb_id=kb_id, filename=filename, chunk_index=chunk_index, text=text, score=score)

    async def _run(self, make_valid_quiz, sensitive, *, hits=None, kb_error=None, judge=None, judge_error=None, plan=None,
                   user_id=1, kb_id=None, kb_service=None, search=None, **settings_overrides):
        store = TaskStore()
        task_id = store.create()
        llm = FakeLLM(
            quiz=QuizSchema.model_validate(make_valid_quiz()),
            plan=plan, judge=judge, judge_error=judge_error,
        )
        kb = kb_service if kb_service is not None else FakeKnowledgeBase(hits=hits or [], error=kb_error)
        await run_quiz_task(
            task_id, "想学私有知识内容", llm, sensitive, self._settings(**settings_overrides),
            store=store, search=search, knowledge_base=kb, user_id=user_id, knowledge_base_id=kb_id,
        )
        return store.get(task_id), llm, kb

    async def test_strict_mode_injects_kb_materials_and_never_searches_web(self, sensitive, make_valid_quiz):
        """指定库 + 命中 → 仅文档资料出题；联网检索零调用（严格模式永不联网）"""
        info, llm, kb = await self._run(make_valid_quiz, sensitive, kb_id=1, hits=[self._hit()], search=FakeSearchClient(search_result="不应被使用"))
        assert info.status == "completed"
        assert kb.search_calls == [(1, "想学私有知识内容", 1)]  # filter 带 kb_id
        assert "doc.txt" in llm.doc_materials_received[0] and "私有知识库" in llm.doc_materials_received[0]
        assert llm.search_results_received == [None]  # 无联网资料
        assert len(llm.judge_calls) == 0  # 严格模式不做充分性判定

    async def test_strict_mode_empty_kb_degrades_to_pure_input(self, sensitive, make_valid_quiz):
        """指定库但无命中 → 纯输入降级，仍不联网（严格模式承诺仅库内出题）"""
        info, llm, kb = await self._run(make_valid_quiz, sensitive, kb_id=1, hits=[], search=FakeSearchClient(search_result="不应被使用"))
        assert info.status == "completed"
        assert llm.doc_materials_received == [None]
        assert llm.search_results_received == [None]

    async def test_strict_mode_search_error_degrades_to_pure_input(self, sensitive, make_valid_quiz):
        """指定库但检索异常 → 纯输入降级，绝不落到联网分支"""
        info, llm, kb = await self._run(make_valid_quiz, sensitive, kb_id=1, kb_error=KBError("检索失败"), search=FakeSearchClient(search_result="不应被使用"))
        assert info.status == "completed"
        assert llm.doc_materials_received == [None]
        assert llm.search_results_received == [None]

    async def test_no_kb_sufficient_judge_uses_kb_only(self, sensitive, make_valid_quiz):
        """未选库 + 命中 + 判定足够 → 仅知识库出题，联网零调用"""
        judge = KnowledgeJudgeResult(enough=True, reason="资料覆盖核心概念", missing_topics=[])
        info, llm, kb = await self._run(make_valid_quiz, sensitive, hits=[self._hit()], judge=judge,
                                        search=FakeSearchClient(search_result="不应被使用"))
        assert info.status == "completed"
        assert kb.search_calls == [(1, "想学私有知识内容", None)]  # 全库检索（filter 无 kb_id）
        assert "doc.txt" in llm.doc_materials_received[0]
        assert llm.search_results_received == [None]
        assert llm.judge_calls[0][0] == "想学私有知识内容"  # 判定输入 = 用户内容 + 片段
        assert "私有知识库" in llm.judge_calls[0][1]

    async def test_no_kb_insufficient_judge_triggers_targeted_fill(self, sensitive, make_valid_quiz):
        """判定不足 → missing_topics 注入检索计划（定向补缺），资料 = 文档 + 联网拼接"""
        judge = KnowledgeJudgeResult(enough=False, reason="缺行业案例", missing_topics=["AI 测试行业案例", "落地实践"])
        plan = SearchPlanSchema.model_validate({"mode": "search", "keywords": ["AI 测试行业案例"], "count": 4})
        info, llm, kb = await self._run(make_valid_quiz, sensitive, 
            hits=[self._hit()], judge=judge, plan=plan,
            search=FakeSearchClient(search_result="联网补缺资料"),
        )
        assert info.status == "completed"
        assert llm.plan_calls == [("想学私有知识内容", ["AI 测试行业案例", "落地实践"])]  # 缺口传入检索计划
        assert "联网补缺资料" in llm.search_results_received[0]
        assert "doc.txt" in llm.doc_materials_received[0]  # 文档资料保留参与出题

    async def test_no_kb_judge_failure_keeps_materials_and_goes_online(self, sensitive, make_valid_quiz):
        """判定调用失败 → 视为不足：保留文档资料、联网普通补缺（无定向缺口，不阻塞出题）"""
        plan = SearchPlanSchema.model_validate({"mode": "search", "keywords": ["词"], "count": 4})
        info, llm, kb = await self._run(make_valid_quiz, sensitive, 
            hits=[self._hit()], judge_error=LLMError("LLM_TIMEOUT", "判定超时"), plan=plan,
            search=FakeSearchClient(search_result="联网资料"),
        )
        assert info.status == "completed"
        assert llm.plan_calls == [("想学私有知识内容", None)]  # 判定失败 → 无缺口，普通检索
        assert "联网资料" in llm.search_results_received[0]
        assert "doc.txt" in llm.doc_materials_received[0]

    async def test_no_kb_empty_hits_goes_online(self, sensitive, make_valid_quiz):
        """未选库 + 无命中 → 走既有联网路径（行为与接入知识库前一致）"""
        plan = SearchPlanSchema.model_validate({"mode": "search", "keywords": ["词"], "count": 4})
        info, llm, kb = await self._run(make_valid_quiz, sensitive, hits=[], plan=plan, search=FakeSearchClient(search_result="联网资料"))
        assert info.status == "completed"
        assert llm.doc_materials_received == [None]
        assert "联网资料" in llm.search_results_received[0]
        assert len(llm.judge_calls) == 0  # 无命中不做判定

    async def test_no_kb_search_error_goes_online(self, sensitive, make_valid_quiz):
        """知识库检索异常 → 走既有联网路径，不向用户暴露检索内部错误"""
        plan = SearchPlanSchema.model_validate({"mode": "search", "keywords": ["词"], "count": 4})
        info, llm, kb = await self._run(make_valid_quiz, sensitive, kb_error=KBError("向量库不可用"), plan=plan,
                                        search=FakeSearchClient(search_result="联网资料"))
        assert info.status == "completed"
        assert llm.doc_materials_received == [None]
        assert "联网资料" in llm.search_results_received[0]

    async def test_kb_not_configured_skips(self, sensitive, make_valid_quiz):
        """EMBEDDING_API_KEY 为空 → 知识库段整体跳过（等同现状），联网路径正常"""
        plan = SearchPlanSchema.model_validate({"mode": "search", "keywords": ["词"], "count": 4})
        info, llm, kb = await self._run(make_valid_quiz, sensitive, 
            embedding_api_key="", plan=plan,
            search=FakeSearchClient(search_result="联网资料"),
        )
        assert info.status == "completed"
        assert kb.search_calls == []  # 未发起知识库检索
        assert len(llm.judge_calls) == 0
        assert "联网资料" in llm.search_results_received[0]

    async def test_anonymous_skips_kb(self, sensitive, make_valid_quiz):
        """匿名（游客模式）→ 知识库段跳过（无 user_id 无法 filter），联网路径正常"""
        plan = SearchPlanSchema.model_validate({"mode": "search", "keywords": ["词"], "count": 4})
        info, llm, kb = await self._run(make_valid_quiz, sensitive, user_id=None, plan=plan, search=FakeSearchClient(search_result="联网资料"))
        assert info.status == "completed"
        assert kb.search_calls == []
        assert "联网资料" in llm.search_results_received[0]

    async def test_kb_service_none_skips(self, sensitive, make_valid_quiz):
        """知识库服务未注入（None）→ 知识库段跳过，联网路径正常"""
        plan = SearchPlanSchema.model_validate({"mode": "search", "keywords": ["词"], "count": 4})
        info, llm, _ = await self._run(make_valid_quiz, sensitive, kb_service=None, plan=plan, search=FakeSearchClient(search_result="联网资料"))
        assert info.status == "completed"
        assert "联网资料" in llm.search_results_received[0]


class TestPromptRendering:
    """Prompt 条件渲染（4.5 验证项：无资料时区段不渲染，Prompt 与接入前逐字一致；有资料时正确注入）"""

    @staticmethod
    def _prompt(name: str) -> str:
        from pathlib import Path

        return (Path(__file__).resolve().parent.parent / "app" / "core" / "prompts" / name).read_text(encoding="utf-8")

    def test_quiz_prompt_renders_doc_materials_only_when_provided(self):
        from langchain_core.prompts import ChatPromptTemplate

        tpl = ChatPromptTemplate.from_template(self._prompt("quiz.txt"), template_format="jinja2")
        with_materials = tpl.format(content="内容", search_results="", doc_materials="【来源：doc.txt 第 1 段】\n量子知识")
        assert "【文档资料】" in with_materials and "doc.txt" in with_materials
        assert "（来自联网检索）" not in with_materials  # 无联网资料不渲染联网区段
        without = tpl.format(content="内容", search_results="", doc_materials="")
        assert "（来自用户的私有知识库）" not in without  # 无文档资料不渲染该区段（向后兼容：Prompt 与接入前一致）

    def test_search_plan_prompt_renders_missing_topics_only_when_provided(self):
        from langchain_core.prompts import ChatPromptTemplate

        tpl = ChatPromptTemplate.from_template(self._prompt("search_plan.txt"), template_format="jinja2")
        with_gap = tpl.format(content="内容", missing_topics=["AI 测试行业案例", "落地实践"])
        assert "定向补缺" in with_gap and "AI 测试行业案例" in with_gap
        without = tpl.format(content="内容", missing_topics=[])
        assert "定向补缺" not in without  # 无缺口不渲染补缺指令（既有行为不变）

    def test_knowledge_judge_prompt_renders_materials(self):
        from langchain_core.prompts import ChatPromptTemplate

        tpl = ChatPromptTemplate.from_template(self._prompt("knowledge_judge.txt"), template_format="jinja2")
        rendered = tpl.format(content="想学的内容", materials="【来源：doc.txt 第 1 段】\n片段文本")
        assert "片段文本" in rendered and "想学的内容" in rendered


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

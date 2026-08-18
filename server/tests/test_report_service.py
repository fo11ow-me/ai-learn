"""报告任务服务测试（方案文档 4.3/4.4：正确率确定性计算、多选全对判分、失败与敏感路径）"""
import logging

from app.core.tasks import TaskStore
from app.core.tracing import current_task_id
from app.models.schemas import AIReportSchema, AnswerRecord, QuizSchema
from app.services.llm import LLMError
from app.services.report import run_report_task
from tests.conftest import make_valid_ai_report, make_valid_answers


class FakeLLM:
    """可编程的 LLMClient 替身：generate_report 按指定行为返回 / 抛错"""

    def __init__(self, ai_report=None, error=None):
        self._ai_report = ai_report
        self._error = error

    async def generate_report(self, quiz, answers):
        if self._error:
            raise self._error
        return self._ai_report


def make_quiz(make_valid_quiz):
    return QuizSchema.model_validate(make_valid_quiz())


def make_answers():
    return [AnswerRecord.model_validate(a) for a in make_valid_answers()]


async def test_completed_with_report(settings, sensitive, make_valid_quiz):
    store = TaskStore()
    task_id = store.create()
    ai = AIReportSchema.model_validate(make_valid_ai_report())

    await run_report_task(
        task_id, make_quiz(make_valid_quiz), make_answers(), FakeLLM(ai), sensitive, settings, store=store
    )

    info = store.get(task_id)
    assert info.status == "completed"
    report = info.payload["report"]
    assert report["correct_count"] == 5
    assert report["correct_rate"] == 100
    assert report["total_questions"] == 5
    assert report["summary"] == ai.summary
    assert report["quote"] == ai.quote


async def test_trace_logs_completed(settings, sensitive, make_valid_quiz, caplog):
    """链路日志：报告任务 start → done（task_id 贯穿，与出题链路同格式）"""
    store = TaskStore()
    task_id = store.create()
    ai = AIReportSchema.model_validate(make_valid_ai_report())

    with caplog.at_level(logging.INFO, logger="app.services.report"):
        await run_report_task(
            task_id, make_quiz(make_valid_quiz), make_answers(), FakeLLM(ai), sensitive, settings, store=store
        )

    messages = [r.message for r in caplog.records]
    assert any(f"report task start task_id={task_id}" in m for m in messages)
    assert any(f"report task done task_id={task_id}" in m and "status=completed" in m for m in messages)


async def test_task_id_ctx_restored_after_run(settings, sensitive, make_valid_quiz, caplog):
    """报告任务执行期间 ctx 注入 task_id，任务结束后恢复（WHY：任务池复用协程，残留旧值会污染下一任务日志）"""
    store = TaskStore()
    task_id = store.create()
    ai = AIReportSchema.model_validate(make_valid_ai_report())

    await run_report_task(
        task_id, make_quiz(make_valid_quiz), make_answers(), FakeLLM(ai), sensitive, settings, store=store
    )

    assert current_task_id.get() is None


async def test_task_id_ctx_restored_on_llm_error(settings, sensitive, make_valid_quiz):
    """失败路径同样释放 ctx（finally 生效）"""
    store = TaskStore()
    task_id = store.create()

    await run_report_task(
        task_id,
        make_quiz(make_valid_quiz),
        make_answers(),
        FakeLLM(error=LLMError("LLM_TIMEOUT", "超时")),
        sensitive,
        settings,
        store=store,
    )

    assert current_task_id.get() is None


async def test_correct_rate_computed_not_from_ai(settings, sensitive, make_valid_quiz):
    """正确率由代码计算（4 对 1 错 → 80），不依赖 AI 算术"""
    store = TaskStore()
    task_id = store.create()
    ai = AIReportSchema.model_validate(make_valid_ai_report())
    answers = make_answers()
    answers[3].selected = [0]  # 第 4 题答错（正确为 [1]）

    await run_report_task(
        task_id, make_quiz(make_valid_quiz), answers, FakeLLM(ai), sensitive, settings, store=store
    )

    report = store.get(task_id).payload["report"]
    assert report["correct_count"] == 4
    assert report["correct_rate"] == 80


async def test_multiple_requires_exact_match(settings, sensitive, make_valid_quiz):
    """多选少选一个即判错（第 3 题正确 [0,3]，只选 [0]）"""
    store = TaskStore()
    task_id = store.create()
    ai = AIReportSchema.model_validate(make_valid_ai_report())
    answers = make_answers()
    answers[2].selected = [0]

    await run_report_task(
        task_id, make_quiz(make_valid_quiz), answers, FakeLLM(ai), sensitive, settings, store=store
    )

    report = store.get(task_id).payload["report"]
    assert report["correct_count"] == 4
    assert report["correct_rate"] == 80


async def test_report_llm_error_marks_failed(settings, sensitive, make_valid_quiz):
    store = TaskStore()
    task_id = store.create()

    await run_report_task(
        task_id,
        make_quiz(make_valid_quiz),
        make_answers(),
        FakeLLM(error=LLMError("LLM_PARSE_FAILED", "解析失败")),
        sensitive,
        settings,
        store=store,
    )

    info = store.get(task_id)
    assert info.status == "failed"
    assert info.error.code == "LLM_PARSE_FAILED"


async def test_report_sensitive_fails(settings, sensitive, make_valid_quiz):
    """AI 生成的报告文本同样受敏感词约束（方案文档 5.4）"""
    store = TaskStore()
    task_id = store.create()
    ai_data = make_valid_ai_report()
    ai_data["quote"] = "赌博使人堕落"  # 命中黑名单
    ai = AIReportSchema.model_validate(ai_data)

    await run_report_task(
        task_id, make_quiz(make_valid_quiz), make_answers(), FakeLLM(ai), sensitive, settings, store=store
    )

    info = store.get(task_id)
    assert info.status == "failed"
    assert info.error.code == "SENSITIVE_CONTENT"

"""报告任务流水线（方案文档 3.3/4.3）：代码计算正确率 → AI 生成复盘文本 → 组装完整报告契约"""
import asyncio

from app.core.config import Settings
from app.core.sensitive import SensitiveFilter
from app.core.tasks import TaskError, TaskStore, task_store
from app.models.schemas import AIReportSchema, AnswerRecord, QuizSchema, ReportSchema
from app.services.llm import LLMClient, LLMError


async def run_report_task(
    task_id: str,
    quiz: QuizSchema,
    answers: list[AnswerRecord],
    llm: LLMClient,
    sensitive: SensitiveFilter,
    settings: Settings,
    store: TaskStore = task_store,
) -> None:
    """后台执行报告任务：置 running → 生成（带整体超时）→ 合规检查 → 组装报告 → completed；
    任何失败写 failed + 错误码。store 默认模块级单例，测试可注入独立实例"""
    store.update(task_id, status="running")
    try:
        correct_count = count_correct(quiz, answers)
        async with asyncio.timeout(settings.task_timeout_seconds):
            ai: AIReportSchema = await llm.generate_report(quiz, answers)
            if _report_contains_sensitive(ai, sensitive):
                store.update(
                    task_id,
                    status="failed",
                    error=TaskError(code="SENSITIVE_CONTENT", message="生成内容包含敏感信息，请重试"),
                )
                return
        report = ReportSchema(
            correct_rate=round(correct_count / len(quiz.questions) * 100),
            correct_count=correct_count,
            total_questions=len(quiz.questions),
            summary=ai.summary,
            mastery=ai.mastery,
            suggestions=ai.suggestions,
            quote=ai.quote,
        )
        store.update(task_id, status="completed", payload={"report": report.model_dump()})
    except TimeoutError:
        store.update(
            task_id, status="failed", error=TaskError(code="TASK_TIMEOUT", message="生成超时，请重试")
        )
    except LLMError as exc:
        store.update(task_id, status="failed", error=TaskError(code=exc.code, message=exc.message))
    except Exception:
        store.update(
            task_id, status="failed", error=TaskError(code="LLM_UNAVAILABLE", message="生成失败，请重试")
        )


def count_correct(quiz: QuizSchema, answers: list[AnswerRecord]) -> int:
    """统计答对题数（WHY：正确率必须确定性计算，不能信任 AI 算术；
    多选必须全选正确才判对，与前端判分规则一致）"""
    by_id = {q.id: q for q in quiz.questions}
    correct = 0
    for a in answers:
        q = by_id.get(a.question_id)
        if q is not None and sorted(a.selected) == sorted(q.answer):
            correct += 1
    return correct


def _report_contains_sensitive(ai: AIReportSchema, sensitive: SensitiveFilter) -> bool:
    """拼接报告全部文本做敏感词检查（方案文档 5.4：对生成结果双向过滤）"""
    parts = [ai.summary, ai.quote, *ai.suggestions]
    for m in ai.mastery:
        parts += [m.knowledge_point, m.comment]
    return sensitive.contains("\n".join(parts))

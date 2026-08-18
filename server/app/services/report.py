"""报告任务流水线（方案文档 3.3/4.3）：代码计算正确率 → AI 生成复盘文本 → 组装完整报告契约"""
import asyncio
import logging
import time

from app.core.config import Settings
from app.core.sensitive import SensitiveFilter
from app.core.tasks import TaskError, TaskStore, task_store
from app.core.tracing import current_task_id
from app.models.schemas import AIReportSchema, AnswerRecord, QuizSchema, ReportSchema
from app.services.llm import LLMClient, LLMError

_logger = logging.getLogger(__name__)


async def run_report_task(
    task_id: str,
    quiz: QuizSchema,
    answers: list[AnswerRecord],
    llm: LLMClient,
    sensitive: SensitiveFilter,
    settings: Settings,
    store: TaskStore = task_store,
    session_id: int | None = None,
    db_engine=None,  # DBEngine | None：报告完成后回写 report_json
) -> None:
    """后台执行报告任务：置 running → 生成（带整体超时）→ 合规检查 → 组装报告 → completed；
    任何失败写 failed + 错误码。store 默认模块级单例，测试可注入独立实例"""
    # current_task_id 注入 ContextVar（WHY：LLM 等深层追踪日志经 task_id_kv() 自动携带 task_id）
    token = current_task_id.set(task_id)
    t0 = time.monotonic()
    store.update(task_id, status="running")
    _logger.info("report task start task_id=%s questions=%d", task_id, len(quiz.questions))
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
                _logger.warning("report task done task_id=%s status=failed code=SENSITIVE_CONTENT elapsed=%.1fs", task_id, time.monotonic() - t0)
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
        _logger.info("report task done task_id=%s status=completed elapsed=%.1fs", task_id, time.monotonic() - t0)
        if session_id is not None and db_engine is not None:
            # 回写闯关记录（WHY：历史报告页直接读库，不再调 AI；回写失败仅记日志不置任务失败）
            try:
                from sqlalchemy import update

                from app.models.db_models import QuizSession

                async with db_engine.maker() as db:
                    await db.execute(
                        update(QuizSession)
                        .where(QuizSession.id == session_id)
                        .values(report_json=report.model_dump()))
                    await db.commit()
            except Exception as exc:  # 回写失败不影响已完成任务
                _logger.warning("report backfill fail task_id=%s session_id=%s: %s", task_id, session_id, exc)
    except TimeoutError:
        store.update(
            task_id, status="failed", error=TaskError(code="TASK_TIMEOUT", message="生成超时，请重试")
        )
        _logger.warning("report task done task_id=%s status=failed code=TASK_TIMEOUT elapsed=%.1fs", task_id, time.monotonic() - t0)
    except LLMError as exc:
        store.update(task_id, status="failed", error=TaskError(code=exc.code, message=exc.message))
        _logger.warning("report task done task_id=%s status=failed code=%s elapsed=%.1fs", task_id, exc.code, time.monotonic() - t0)
    except Exception:
        store.update(
            task_id, status="failed", error=TaskError(code="LLM_UNAVAILABLE", message="生成失败，请重试")
        )
        _logger.warning("report task done task_id=%s status=failed code=LLM_UNAVAILABLE elapsed=%.1fs", task_id, time.monotonic() - t0)
    finally:
        # 释放 ContextVar（WHY：任务池复用协程，残留 task_id 会让下一任务的日志串上旧值）
        current_task_id.reset(token)


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

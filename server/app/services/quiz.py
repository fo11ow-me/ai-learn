"""出题任务流水线（方案文档 3.2/5.1 MVP 一段式）：生成题库 → 敏感词检查 → 写入任务状态"""
import asyncio

from app.core.config import Settings
from app.core.sensitive import SensitiveFilter
from app.core.tasks import TaskError, TaskStore, task_store
from app.models.schemas import QuizSchema
from app.services.llm import LLMClient, LLMError


async def run_quiz_task(
    task_id: str,
    content: str,
    llm: LLMClient,
    sensitive: SensitiveFilter,
    settings: Settings,
    store: TaskStore = task_store,
) -> None:
    """后台执行出题任务：置 running → 生成（带整体超时）→ 合规检查 → completed；
    任何失败写 failed + 错误码，绝不把坏数据交给前端。store 默认模块级单例，测试可注入独立实例"""
    store.update(task_id, status="running")
    try:
        async with asyncio.timeout(settings.task_timeout_seconds):
            quiz: QuizSchema = await llm.generate_quiz(content)
            if _quiz_contains_sensitive(quiz, sensitive):
                store.update(
                    task_id,
                    status="failed",
                    error=TaskError(code="SENSITIVE_CONTENT", message="生成内容包含敏感信息，请更换主题"),
                )
                return
        store.update(task_id, status="completed", payload={"quiz": quiz.model_dump()})
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


def _quiz_contains_sensitive(quiz: QuizSchema, sensitive: SensitiveFilter) -> bool:
    """拼接题库全部文本做敏感词检查（方案文档 5.4：对生成结果双向过滤）"""
    parts = [quiz.topic, quiz.source_summary]
    for q in quiz.questions:
        parts += [q.question, q.explanation, q.knowledge_point, *q.options]
    return sensitive.contains("\n".join(parts))

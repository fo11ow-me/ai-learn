"""出题任务流水线（方案文档 3.2/5.1 三段式 + 联网搜索增强 + RAG 知识库段）：
知识库检索（可选，失败自动降级）→ 联网检索资料（可选，失败自动降级）→ 生成题库 → 敏感词检查 → 写入任务状态"""
import asyncio
import logging
import re
import time

from app.core.config import Settings
from app.core.sensitive import SensitiveFilter
from app.core.tasks import TaskError, TaskStore, task_store
from app.core.tracing import current_task_id
from app.models.schemas import QuizSchema, SearchPlanSchema
from app.services.knowledge_base import KBError, KnowledgeBaseService, format_chunks_for_prompt
from app.services.llm import LLMClient, LLMError
from app.services.search import SearchClient, SearchError

SEARCH_BUDGET_SECONDS = 20  # 检索段整体预算（WHY：串行搜索/提取不得超过 20s，防顶穿 120s 任务超时）

_logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"^https?://(?:[\w-]+\.)+[a-zA-Z]{2,}(?::\d+)?(?:/[^\s]*)?$", re.IGNORECASE)


def is_url(text: str) -> bool:
    """判断输入整体是否为网页地址（WHY：纯 URL 输入直达页面提取，跳过 LLM 检索计划，省一次模型调用）"""
    return bool(_URL_RE.match(text.strip()))


async def run_quiz_task(
    task_id: str,
    content: str,
    llm: LLMClient,
    sensitive: SensitiveFilter,
    settings: Settings,
    store: TaskStore = task_store,
    search: SearchClient | None = None,
    knowledge_base: KnowledgeBaseService | None = None,
    user_id: int | None = None,
    knowledge_base_id: int | None = None,
) -> None:
    """后台执行出题任务：置 running → 知识库段（可选，RAG D4）→ 联网检索段（可选，失败自动降级）→ 生成
    （带整体超时）→ 合规检查 → completed；任何失败写 failed + 错误码，绝不把坏数据交给前端。
    knowledge_base_id 非空 = 严格模式（仅库内出题，永不联网，路由层已做归属校验）；
    匿名/未配置时知识库段整体跳过，行为与接入前一致"""
    # 链路日志（WHY：异步任务黑盒，task_id 贯穿各步骤 + 耗时，日志中可重建每次执行全链路）
    # current_task_id 注入 ContextVar（WHY：LLM/搜索等深层追踪日志经 task_id_kv() 自动携带 task_id，
    # 无需逐层传参；asyncio.create_task 自动拷贝上下文，子协程可见）
    token = current_task_id.set(task_id)
    t0 = time.monotonic()
    store.update(task_id, status="running")
    _logger.info("quiz task start task_id=%s content_len=%d kb_id=%s", task_id, len(content), knowledge_base_id)
    try:
        async with asyncio.timeout(settings.task_timeout_seconds):
            doc_materials, missing_topics, kb_only = await _retrieve_kb_materials(
                task_id, user_id, content, knowledge_base_id, llm, settings, knowledge_base
            )
            if kb_only:
                # 知识库资料足够（或严格模式）：不联网，直接基于文档资料出题
                quiz: QuizSchema = await llm.generate_quiz(content, doc_materials=doc_materials)
                search_chars = "-"
            else:
                search_results = await _retrieve_search_materials(
                    task_id, content, llm, settings, search, missing_topics
                )
                quiz = await llm.generate_quiz(content, search_results=search_results, doc_materials=doc_materials)
                search_chars = len(search_results) if search_results else "-"
            _logger.info(
                "quiz llm start task_id=%s search_chars=%s kb_chars=%s",
                task_id, search_chars, len(doc_materials) if doc_materials else "-",
            )
            _logger.info("quiz llm ok task_id=%s questions=%d", task_id, len(quiz.questions))
            if _quiz_contains_sensitive(quiz, sensitive):
                store.update(
                    task_id,
                    status="failed",
                    error=TaskError(code="SENSITIVE_CONTENT", message="生成内容包含敏感信息，请更换主题"),
                )
                _logger.warning("quiz task done task_id=%s status=failed code=SENSITIVE_CONTENT elapsed=%.1fs", task_id, time.monotonic() - t0)
                return
        store.update(task_id, status="completed", payload={"quiz": quiz.model_dump()})
        _logger.info("quiz task done task_id=%s status=completed questions=%d elapsed=%.1fs", task_id, len(quiz.questions), time.monotonic() - t0)
    except TimeoutError:
        store.update(
            task_id, status="failed", error=TaskError(code="TASK_TIMEOUT", message="生成超时，请重试")
        )
        _logger.warning("quiz task done task_id=%s status=failed code=TASK_TIMEOUT elapsed=%.1fs", task_id, time.monotonic() - t0)
    except LLMError as exc:
        store.update(task_id, status="failed", error=TaskError(code=exc.code, message=exc.message))
        _logger.warning("quiz task done task_id=%s status=failed code=%s elapsed=%.1fs", task_id, exc.code, time.monotonic() - t0)
    except Exception:
        store.update(
            task_id, status="failed", error=TaskError(code="LLM_UNAVAILABLE", message="生成失败，请重试")
        )
        _logger.warning("quiz task done task_id=%s status=failed code=LLM_UNAVAILABLE elapsed=%.1fs", task_id, time.monotonic() - t0)
    finally:
        # 释放 ContextVar（WHY：任务池复用线程/协程，残留 task_id 会让下一任务的日志串上旧值）
        current_task_id.reset(token)


async def _retrieve_kb_materials(
    task_id: str,
    user_id: int | None,
    content: str,
    kb_id: int | None,
    llm: LLMClient,
    settings: Settings,
    knowledge_base: KnowledgeBaseService | None,
) -> tuple[str | None, list[str] | None, bool]:
    """知识库段（RAG D4）：返回 (文档资料, 判定缺口主题, 仅知识库标志)。
    降级矩阵：未启用/未注入/匿名/检索异常/未选库无命中 → (None, None, False) 走既有联网路径；
    判定失败 → 保留文档资料但无定向缺口 → 联网普通补缺；判定足够 → 仅知识库出题不联网；
    严格模式（kb_id 非空）→ 永不联网：有命中仅库资料，无命中纯输入。
    WHY：知识库段与联网段同样零故障影响，任何异常都不向出题流程扩散"""
    if not (settings.embedding_enabled and settings.embedding_api_key):
        _logger.info("quiz kb skip task_id=%s reason=disabled_or_no_key", task_id)
        return None, None, False
    if knowledge_base is None:
        _logger.info("quiz kb skip task_id=%s reason=not_injected", task_id)
        return None, None, False
    if user_id is None:
        _logger.info("quiz kb skip task_id=%s reason=anonymous", task_id)
        return None, None, False
    try:
        hits = await knowledge_base.search(user_id, content, kb_id)
    except KBError as exc:
        if kb_id is not None:
            # 严格模式检索失败 → 纯输入降级（WHY：严格模式承诺「仅基于该库出题」，任何情况都不得联网）
            _logger.warning("quiz kb strict degrade task_id=%s error=%s", task_id, exc)
            return None, None, True
        _logger.warning("quiz kb degrade task_id=%s reason=%s error=%s", task_id, type(exc).__name__, exc)
        return None, None, False
    if kb_id is not None:
        # 严格模式：仅库内资料；空命中 → 纯输入（永不联网）
        doc_materials = format_chunks_for_prompt(hits) if hits else None
        _logger.info("quiz kb strict task_id=%s hits=%d kb_chars=%s", task_id, len(hits), len(doc_materials) if doc_materials else "-")
        return doc_materials, None, True
    if not hits:
        _logger.info("quiz kb empty task_id=%s", task_id)
        return None, None, False
    doc_materials = format_chunks_for_prompt(hits)
    try:
        judge = await llm.judge_knowledge_sufficient(content, doc_materials)
    except LLMError as exc:
        # 判定失败视为不足 → 联网（无定向缺口，资料仍保留参与出题）
        _logger.warning("quiz kb judge_fail task_id=%s error=%s", task_id, exc)
        return doc_materials, None, False
    _logger.info(
        "quiz kb judge task_id=%s enough=%s missing=%s", task_id, judge.enough, ",".join(judge.missing_topics) or "-"
    )
    if judge.enough:
        return doc_materials, None, True  # 仅知识库出题，不联网
    return doc_materials, judge.missing_topics or None, False  # 定向补缺：联网围绕缺失知识点检索


async def _retrieve_search_materials(
    task_id: str,
    content: str,
    llm: LLMClient,
    settings: Settings,
    search: SearchClient | None,
    missing_topics: list[str] | None = None,
) -> str | None:
    """检索段（三段式 ①②）：未启用 / 未注入 / 失败 / 超时 / 空资料 → None（降级为一段式）；
    成功 → 拼接后的资料文本。WHY：检索增强必须零故障影响，任何异常都不向出题流程扩散。
    missing_topics 非空时检索计划围绕缺失知识点定向补缺（RAG D4）。
    task_id 仅用于链路日志（可观测性：降级原因必须可见，才能判断「没搜到」还是「没启用」）"""
    if not (settings.search_enabled and settings.tavily_api_key):
        _logger.info("quiz search skip task_id=%s reason=disabled_or_no_key", task_id)
        return None
    if search is None:
        _logger.info("quiz search skip task_id=%s reason=not_injected", task_id)
        return None
    try:
        async with asyncio.timeout(SEARCH_BUDGET_SECONDS):
            if is_url(content):
                _logger.info("quiz search url_extract task_id=%s url=%s", task_id, content.strip())
                text = await search.extract(content)
                if not text:
                    _logger.info("quiz search empty task_id=%s", task_id)
                return text or None
            plan: SearchPlanSchema = await llm.plan_search(content, missing_topics)
            _logger.info(
                "quiz search plan task_id=%s mode=%s keywords=%s count=%d depth=%s",
                task_id, plan.mode, plan.keywords, plan.count, plan.depth,
            )
            if plan.mode == "extract":
                if not is_url(plan.url):
                    # 防御无效 URL（WHY：LLM 可能把「https://」误判为可提取地址，实测 Tavily 返回空
                    # 导致整段检索空跑；无效即弃，降级为一段式出题，不白耗调用）
                    _logger.warning("quiz search invalid_url task_id=%s url=%s", task_id, plan.url)
                    return None
                _logger.info("quiz search url_extract task_id=%s url=%s", task_id, plan.url)
                text = await search.extract(plan.url)
                if not text:
                    _logger.info("quiz search empty task_id=%s", task_id)
                return text or None
            parts = [await search.search(keyword, plan.count, plan.depth) for keyword in plan.keywords]
            joined = "\n\n".join(part for part in parts if part)
            if not joined:
                _logger.info("quiz search empty task_id=%s", task_id)
                return None
            _logger.info("quiz search ok task_id=%s chars=%d", task_id, len(joined))
            return joined[: settings.search_result_max_chars]
    except TimeoutError:
        _logger.warning("quiz search degrade task_id=%s reason=timeout", task_id)
        return None
    except (SearchError, LLMError) as exc:
        _logger.warning("quiz search degrade task_id=%s reason=%s error=%s", task_id, type(exc).__name__, exc)
        return None
    except Exception:
        _logger.warning("quiz search degrade task_id=%s reason=unexpected", task_id)
        return None


def _quiz_contains_sensitive(quiz: QuizSchema, sensitive: SensitiveFilter) -> bool:
    """拼接题库全部文本做敏感词检查（方案文档 5.4：对生成结果双向过滤）"""
    parts = [quiz.topic, quiz.source_summary]
    for q in quiz.questions:
        parts += [q.question, q.explanation, q.knowledge_point, *q.options]
    return sensitive.contains("\n".join(parts))

"""LLM 客户端封装（WHY：供应商可替换、结构化输出、超时重试集中于此，业务代码不感知模型细节）"""
import logging
import time
from asyncio import sleep
from pathlib import Path
from typing import Any, Callable

from langchain.chat_models import init_chat_model
from langchain_core.exceptions import OutputParserException
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.tracing import task_id_kv, truncate_for_log
from app.models.schemas import AIReportSchema, AnswerRecord, KnowledgeJudgeResult, QuizSchema, SearchPlanSchema

QUIZ_TEMPERATURE = 0.7  # 出题温度（方案文档 5.1）
REPORT_TEMPERATURE = 0.5  # 报告温度（方案文档 5.1）
SEARCH_PLAN_TEMPERATURE = 0.3  # 检索计划温度（WHY：计划要稳定，不要多样性）
MAX_ATTEMPTS = 3  # 1 次原始调用 + 2 次重试（方案文档 4.4）
BACKOFF_BASE_SECONDS = 2.0  # 指数退避基数：2s / 4s

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "core" / "prompts"

_logger = logging.getLogger(__name__)


class LLMError(Exception):
    """LLM 调用失败（错误码归一化，供任务层直接透传给前端）"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class LLMClient:
    """模型初始化 + with_structured_output + 指数退避重试。
    失败重试 ≤2 次（2s/4s），解析/校验失败同样重试，绝不把坏数据交给业务层"""

    def __init__(self, settings: Settings, build_model: Callable[[float], Any] | None = None):
        self._settings = settings
        self._build_model = build_model or (lambda temperature: _default_build_model(settings, temperature))

    async def generate_quiz(
        self, content: str, search_results: str | None = None, doc_materials: str | None = None
    ) -> QuizSchema:
        """基于用户输入（+可选联网资料/知识库文档资料）生成题库（WHY：任一资料存在时注入对应段落约束事实来源；
        None 时模板不渲染该段落，Prompt 与接入前逐字一致，向后兼容）"""
        prompt = ChatPromptTemplate.from_template(_load_prompt("quiz.txt"), template_format="jinja2").format(
            content=content, search_results=search_results or "", doc_materials=doc_materials or ""
        )
        return await self._invoke_structured(QuizSchema, prompt, QUIZ_TEMPERATURE)

    async def plan_search(self, content: str, missing_topics: list[str] | None = None) -> SearchPlanSchema:
        """制定联网检索计划（WHY：LLM 只决策 mode/关键词/条数/深度，执行由流水线确定性代码完成）。
        missing_topics 非空 = 知识库判定不足后的定向补缺（RAG D4：检索计划围绕缺失知识点展开而非用户输入泛搜）"""
        prompt = ChatPromptTemplate.from_template(_load_prompt("search_plan.txt"), template_format="jinja2").format(
            content=content, missing_topics=missing_topics or []
        )
        return await self._invoke_structured(SearchPlanSchema, prompt, SEARCH_PLAN_TEMPERATURE)

    async def judge_knowledge_sufficient(self, content: str, materials: str) -> KnowledgeJudgeResult:
        """判定知识库片段是否足以覆盖出题（RAG D4：相关性/覆盖度/总量三信号综合判断；
        判定失败由调用方降级为联网，不阻塞出题）"""
        prompt = ChatPromptTemplate.from_template(_load_prompt("knowledge_judge.txt"), template_format="jinja2").format(
            content=content, materials=materials
        )
        return await self._invoke_structured(KnowledgeJudgeResult, prompt, SEARCH_PLAN_TEMPERATURE)

    async def generate_report(self, quiz: QuizSchema, answers: list[AnswerRecord]) -> AIReportSchema:
        """基于题目与作答生成报告（正确率等统计字段由服务层代码计算，不在本方法范围）"""
        prompt = ChatPromptTemplate.from_template(_load_prompt("report.txt")).format(
            quiz=quiz.model_dump_json(ensure_ascii=False),
            answers=[a.model_dump() for a in answers],
        )
        return await self._invoke_structured(AIReportSchema, prompt, REPORT_TEMPERATURE)

    async def _invoke_structured(self, schema_cls: type[BaseModel], prompt: str, temperature: float) -> BaseModel:
        """结构化生成 + 重试：attempt 1..3，失败退避 2s/4s，最终失败按错误码归类。
        追踪日志（WHY：INFO 记录第几次成功/耗时/token 用量；DEBUG 记录完整 Prompt 与输出，
        不改代码即可重建调用现场——注意 DEBUG 含用户输入原文，生产保持 INFO）"""
        # include_raw=True（WHY：DeepSeek 不支持 json_schema 响应格式，function calling 官方支持且自动解析校验；
        # include_raw 额外返回原始 AIMessage，用于提取 usage_metadata 的 token 用量）
        model = self._build_model(temperature).with_structured_output(schema_cls, method="function_calling", include_raw=True)
        t0 = time.monotonic()
        last_exc: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                result = await model.ainvoke(prompt)
                parsed = result.get("parsed")
                if parsed is None:
                    # include_raw 模式解析失败不抛异常（parsed=None），须显式抛出让重试与错误分类生效
                    raise OutputParserException("结构化解析失败（include_raw 模式下 parsed 为空）")
                raw = result.get("raw")
                usage = getattr(raw, "usage_metadata", None) or {}
                _logger.info(
                    "%sLLM 调用成功（第 %s/%s 次，%.1fs）：%s tokens=in=%s out=%s",
                    task_id_kv(), attempt, MAX_ATTEMPTS, time.monotonic() - t0,
                    schema_cls.__name__, usage.get("input_tokens", "-"), usage.get("output_tokens", "-"),
                )
                _logger.debug("%sLLM 输入：%s", task_id_kv(), truncate_for_log(prompt))
                _logger.debug(
                    "%sLLM 输出（%s）：%s", task_id_kv(), schema_cls.__name__,
                    truncate_for_log(parsed.model_dump_json(ensure_ascii=False)),
                )
                return parsed
            except Exception as exc:  # 网络/超时/限流/解析失败均走统一重试
                last_exc = exc
                _logger.warning("%sLLM 调用失败（第 %s/%s 次）：%s", task_id_kv(), attempt, MAX_ATTEMPTS, exc, exc_info=exc)
                if attempt < MAX_ATTEMPTS:
                    await sleep(BACKOFF_BASE_SECONDS**attempt)
        raise _classify_error(last_exc)


def _default_build_model(settings: Settings, temperature: float):
    """默认模型构建：优先 langchain-deepseek 官方集成；配置 base_url 时走 OpenAI 兼容兜底（方案文档 2.4）。
    统一关闭思考模式（WHY：DeepSeek v4 默认 thinking 模式不支持 tool_choice，结构化输出依赖工具调用）"""
    extra_body = {"thinking": {"type": "disabled"}}
    if settings.deepseek_base_url:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.deepseek_model,
            base_url=settings.deepseek_base_url,
            api_key=settings.deepseek_api_key,
            timeout=settings.llm_timeout,
            max_retries=0,  # 重试由本模块统一控制，避免双层重试
            temperature=temperature,
            extra_body=extra_body,
        )
    return init_chat_model(
        settings.deepseek_model,
        model_provider="deepseek",
        api_key=settings.deepseek_api_key,
        timeout=settings.llm_timeout,
        max_retries=0,
        temperature=temperature,
        extra_body=extra_body,
    )


def _classify_error(exc: Exception | None) -> LLMError:
    """错误码归一化（方案文档 4.4 错误码约定：LLM_TIMEOUT / LLM_PARSE_FAILED / LLM_UNAVAILABLE）"""
    if isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower():
        return LLMError("LLM_TIMEOUT", "大模型调用超时，请重试")
    if isinstance(exc, (OutputParserException, ValidationError)):
        return LLMError("LLM_PARSE_FAILED", "大模型返回内容解析失败，请重试")
    return LLMError("LLM_UNAVAILABLE", "大模型服务暂时不可用，请稍后重试")


def _load_prompt(name: str) -> str:
    """从 core/prompts 读取 Prompt 模板（WHY：Prompt 迭代只改文件不改代码）"""
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")

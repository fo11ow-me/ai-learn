"""LLM 客户端封装（WHY：供应商可替换、结构化输出、超时重试集中于此，业务代码不感知模型细节）"""
import logging
from asyncio import sleep
from pathlib import Path
from typing import Any, Callable

from langchain.chat_models import init_chat_model
from langchain_core.exceptions import OutputParserException
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.models.schemas import AIReportSchema, AnswerRecord, QuizSchema

QUIZ_TEMPERATURE = 0.7  # 出题温度（方案文档 5.1）
REPORT_TEMPERATURE = 0.5  # 报告温度（方案文档 5.1）
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

    async def generate_quiz(self, content: str) -> QuizSchema:
        """基于用户输入生成题库（结构化输出，Pydantic 校验失败会重试）"""
        prompt = ChatPromptTemplate.from_template(_load_prompt("quiz.txt")).format(content=content)
        return await self._invoke_structured(QuizSchema, prompt, QUIZ_TEMPERATURE)

    async def generate_report(self, quiz: QuizSchema, answers: list[AnswerRecord]) -> AIReportSchema:
        """基于题目与作答生成报告（正确率等统计字段由服务层代码计算，不在本方法范围）"""
        prompt = ChatPromptTemplate.from_template(_load_prompt("report.txt")).format(
            quiz=quiz.model_dump_json(ensure_ascii=False),
            answers=[a.model_dump() for a in answers],
        )
        return await self._invoke_structured(AIReportSchema, prompt, REPORT_TEMPERATURE)

    async def _invoke_structured(self, schema_cls: type[BaseModel], prompt: str, temperature: float) -> BaseModel:
        """结构化生成 + 重试：attempt 1..3，失败退避 2s/4s，最终失败按错误码归类"""
        # method="function_calling"（WHY：DeepSeek 不支持 json_schema 响应格式，function calling 官方支持且自动解析校验）
        model = self._build_model(temperature).with_structured_output(schema_cls, method="function_calling")
        last_exc: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return await model.ainvoke(prompt)
            except Exception as exc:  # 网络/超时/限流/解析失败均走统一重试
                last_exc = exc
                _logger.warning("LLM 调用失败（第 %s/%s 次）：%s", attempt, MAX_ATTEMPTS, exc)
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

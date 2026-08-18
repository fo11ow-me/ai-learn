"""Tavily 双模式搜索客户端（WHY：供应商调用细节集中在 search 客户端，出题流水线只按检索计划调用；
依赖注入仿 LLMClient(build_model=...)，测试注入假 build 工厂，不依赖真实 key 与网络）"""
import json
import logging
import time
from typing import Any, Callable

from app.core.config import Settings
from app.core.tracing import task_id_kv, truncate_for_log

_logger = logging.getLogger(__name__)


class SearchError(Exception):
    """搜索/提取失败（WHY：由出题流水线统一捕获并降级为一段式出题，不中断闯关流程）"""


def _parse_json(raw) -> dict:
    """解析工具返回体为 dict；已解析为 str 时兼容 JSON 字符串（WHY：langchain-tavily 0.2.18 实测
    ainvoke 返回 dict——曾假设返回 JSON 字符串，json.loads(dict) 抛 TypeError 误报「非法 JSON」导致检索段全程降级）"""
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        preview = str(raw)[:200] if raw is not None else "None"
        raise SearchError(f"Tavily 返回非法 JSON（类型 {type(raw).__name__}，内容前 200 字：{preview}）") from exc
    if not isinstance(data, dict):
        raise SearchError("Tavily 返回结构异常")
    return data


def _truncate(text: str, limit: int) -> str:
    """截断至上限（WHY：拼接后的资料必须控制在上下文预算内，超出部分直接丢弃）"""
    return text[:limit] if len(text) > limit else text


class SearchClient:
    """搜索 + 页面提取客户端。

    `build_search(max_results, search_depth)` 为工厂（WHY：TavilySearch 的 max_results 仅实例化时
    生效，必须按检索计划动态构建工具实例）；`build_extract()` 返回 TavilyExtract。
    任何解析失败抛 SearchError，空结果返回空串，由流水线统一降级"""

    def __init__(
        self,
        settings: Settings,
        build_search: Callable[[int, str], Any] | None = None,
        build_extract: Callable[[], Any] | None = None,
    ):
        self._result_max_chars = settings.search_result_max_chars
        api_key = settings.tavily_api_key
        self._build_search = build_search or (
            lambda max_results, search_depth: _default_build_search(api_key, max_results, search_depth)
        )
        self._build_extract = build_extract or (lambda: _default_build_extract(api_key))

    async def search(self, query: str, count: int, depth: str) -> str:
        """按关键词搜索并拼接「引用 N｜标题｜正文」，超限截断；空 query 或空结果返回空串。
        追踪日志（WHY：DEBUG 记录入出参——query/count/depth/条数/逐条 URL｜标题/字符数/原始响应，
        排查「为什么搜到的内容不对」不必改代码重跑）"""
        if not query:
            return ""
        t0 = time.monotonic()
        tool = self._build_search(count, depth)
        try:
            raw = await tool.ainvoke({"query": query})
        except Exception as exc:  # 网络/限流/鉴权等一律归一为 SearchError（不区分细节，降级即可）
            _logger.warning("%ssearch fail query=%s count=%d depth=%s error=%s", task_id_kv(), query, count, depth, exc, exc_info=exc)
            raise SearchError(f"Tavily 搜索调用失败：{exc}") from exc
        results = _parse_json(raw).get("results") or []
        text = _truncate(_join_results(results, url_field="url", content_field="content", title_field="title"), self._result_max_chars)
        _logger.debug(
            "%ssearch ok query=%s count=%d depth=%s n=%d chars=%d elapsed=%.1fs urls=[%s] raw=%s",
            task_id_kv(), query, count, depth, len(results), len(text), time.monotonic() - t0,
            _summarize_urls(results), truncate_for_log(repr(raw)),
        )
        return text

    async def extract(self, url: str) -> str:
        """提取网页正文并拼接，超限截断；空 url、全部提取失败或正文为空返回空串。
        追踪日志（WHY：DEBUG 记录目标 URL、成功/失败条目数与耗时）"""
        if not url:
            return ""
        t0 = time.monotonic()
        tool = self._build_extract()
        try:
            raw = await tool.ainvoke({"urls": [url]})
        except Exception as exc:
            _logger.warning("%sextract fail url=%s error=%s", task_id_kv(), url, exc, exc_info=exc)
            raise SearchError(f"Tavily 网页提取失败：{exc}") from exc
        data = _parse_json(raw)
        results = data.get("results") or []
        failed = data.get("failed_results") or []
        text = _truncate(_join_results(results, url_field="url", content_field="raw_content"), self._result_max_chars)
        _logger.debug(
            "%sextract ok url=%s n=%d failed=%d chars=%d elapsed=%.1fs raw=%s",
            task_id_kv(), url, len(results), len(failed), len(text), time.monotonic() - t0, truncate_for_log(repr(raw)),
        )
        return text


def _summarize_urls(results: list[dict]) -> str:
    """结果条目摘要「标题｜URL」列表（WHY：DEBUG 追踪日志用；限制 10 条防刷屏）"""
    return "; ".join(
        f"{item.get('title', '')}｜{item.get('url', '')}" for item in results[:10]
    )


def _join_results(results: list[dict], url_field: str, content_field: str, title_field: str | None = None) -> str:
    """逐条拼接「引用 N｜标题｜正文」，跳过正文为空的条目（WHY：空条目无信息量，且引用编号需与内容对应）"""
    parts: list[str] = []
    for index, item in enumerate(results, start=1):
        content = str(item.get(content_field, "") or "").strip()
        if not content:
            continue
        header = f"引用 {index}"
        if title_field:
            title = str(item.get(title_field, "") or "").strip()
            if title:
                header += f"｜{title}"
        url = str(item.get(url_field, "") or "").strip()
        if url:
            header += f"｜{url}"
        parts.append(f"{header}\n{content}")
    return "\n\n".join(parts)


def _default_build_search(api_key: str, max_results: int, search_depth: str):
    """默认 TavilySearch 构建（WHY：topic=general 覆盖通用知识；不开 include_answer 省 credit，答案由 DeepSeek 生成）"""
    from langchain_tavily import TavilySearch

    return TavilySearch(
        tavily_api_key=api_key,
        max_results=max_results,
        topic="general",
        search_depth=search_depth,
        include_answer=False,
    )


def _default_build_extract(api_key: str):
    """默认 TavilyExtract 构建（extract_depth 默认 basic：advanced 更慢更耗额度，按检索计划需要时再调）"""
    from langchain_tavily import TavilyExtract

    return TavilyExtract(tavily_api_key=api_key)

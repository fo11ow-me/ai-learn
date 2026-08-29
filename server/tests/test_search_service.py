"""Tavily 搜索客户端测试（检索计划执行层：解析、拼接、截断、异常归一化）"""
import json
import logging

import pytest

from app.core.tracing import current_task_id
from app.services.search import SearchClient, SearchError


class FakeTool:
    """模拟 Tavily 工具：ainvoke 返回固定 JSON 字符串或抛异常，记录调用参数"""

    def __init__(self, result, error=None):
        self._result = result
        self._error = error
        self.calls = []

    async def ainvoke(self, args):
        self.calls.append(args)
        if self._error:
            raise self._error
        return self._result


class FakeSearchBuilder:
    """记录构建参数（max_results/search_depth）并返回 FakeTool"""

    def __init__(self, tool):
        self._tool = tool
        self.built: list[tuple] = []

    def __call__(self, max_results, search_depth):
        self.built.append((max_results, search_depth))
        return self._tool


class FakeExtractBuilder:
    """记录是否被构建并返回 FakeTool"""

    def __init__(self, tool):
        self._tool = tool
        self.built = 0

    def __call__(self):
        self.built += 1
        return self._tool


@pytest.fixture
def settings():
    from app.core.config import Settings

    return Settings(deepseek_api_key="test", tavily_api_key="tvly-test")


def _search_payload(results: list[dict]) -> str:
    return json.dumps({"query": "q", "results": results}, ensure_ascii=False)


def _result(title="Tavily 官方文档", url="https://docs.tavily.com", content="Tavily 是搜索 API，支持 basic 与 advanced 深度。") -> dict:
    return {"title": title, "url": url, "content": content}


async def test_search_joins_results_with_citations(settings):
    """正常返回：逐条拼接「引用 N｜标题｜正文」"""
    tool = FakeTool(_search_payload([_result(content="第一条正文"), _result(title="第二条", content="第二条正文")]))
    client = SearchClient(settings, build_search=FakeSearchBuilder(tool))

    text = await client.search("Harness Engineering", 5, "basic")

    assert "引用 1｜Tavily 官方文档｜https://docs.tavily.com\n第一条正文" in text
    assert "引用 2｜第二条｜https://docs.tavily.com\n第二条正文" in text


async def test_search_empty_results_returns_empty_string(settings):
    """空 results → 返回空串（降级信号），不报错"""
    client = SearchClient(settings, build_search=FakeSearchBuilder(FakeTool(_search_payload([]))))

    assert await client.search("词", 5, "basic") == ""


async def test_search_ainvoke_returns_dict_like_real_api(settings):
    """真实 langchain-tavily 0.2.18 的 ainvoke 返回解析后的 dict（非 JSON 字符串）→ 必须能解析。

    曾因假设返回 JSON 字符串，json.loads(dict) 抛 TypeError 误报「非法 JSON」，检索段全程降级。
    """
    tool = FakeTool({"query": "q", "results": [_result()]})
    client = SearchClient(settings, build_search=FakeSearchBuilder(tool), build_extract=FakeExtractBuilder(tool))

    text = await client.search("关键词", 3, "basic")

    assert "Tavily 是搜索 API" in text


async def test_extract_ainvoke_returns_dict_like_real_api(settings):
    """extract 同理：真实返回 dict 而非 JSON 字符串"""
    tool = FakeTool({"results": [{"url": "https://x.com", "raw_content": "页面正文"}]})
    client = SearchClient(settings, build_search=FakeSearchBuilder(tool), build_extract=FakeExtractBuilder(tool))

    text = await client.extract("https://x.com")

    assert "页面正文" in text


async def test_search_invalid_json_raises_search_error(settings):
    """非法 JSON → 抛 SearchError（由流水线统一降级）"""
    client = SearchClient(settings, build_search=FakeSearchBuilder(FakeTool("not json{{{")))

    with pytest.raises(SearchError):
        await client.search("词", 5, "basic")


async def test_search_tool_exception_raises_search_error(settings):
    """工具调用异常（网络/限流）→ 抛 SearchError"""
    client = SearchClient(settings, build_search=FakeSearchBuilder(FakeTool("", error=RuntimeError("boom"))))

    with pytest.raises(SearchError):
        await client.search("词", 5, "basic")


async def test_search_truncates_long_result(settings):
    """超长结果截断至 search_result_max_chars（默认 4000）"""
    long_content = "长" * 5000
    client = SearchClient(settings, build_search=FakeSearchBuilder(FakeTool(_search_payload([_result(content=long_content)]))))

    text = await client.search("词", 5, "basic")

    assert len(text) == 4000


async def test_search_build_receives_plan_count_and_depth(settings):
    """工厂收到检索计划的 count/depth：max_results 仅实例化时生效，须按计划动态构建。"""
    tool = FakeTool(_search_payload([_result()]))
    builder = FakeSearchBuilder(tool)
    client = SearchClient(settings, build_search=builder)

    await client.search("词", 6, "advanced")

    assert builder.built == [(6, "advanced")]


async def test_extract_joins_raw_content(settings):
    """extract 正常返回：raw_content 拼接，标题字段回退为 url"""
    payload = json.dumps(
        {
            "results": [{"url": "https://example.com/a", "raw_content": "页面正文 A"}],
            "failed_results": [{"url": "https://example.com/b", "error": "404"}],
        }
    )
    client = SearchClient(settings, build_extract=FakeExtractBuilder(FakeTool(payload)))

    text = await client.extract("https://example.com/a")

    assert "引用 1｜https://example.com/a\n页面正文 A" in text


async def test_extract_all_failed_returns_empty_string(settings):
    """全部提取失败（failed_results 无 results）→ 返回空串"""
    payload = json.dumps({"results": [], "failed_results": [{"url": "https://x.com", "error": "404"}]})
    client = SearchClient(settings, build_extract=FakeExtractBuilder(FakeTool(payload)))

    assert await client.extract("https://x.com") == ""


async def test_extract_empty_raw_content_returns_empty_string(settings):
    """raw_content 为空 → 跳过该条目，全空则返回空串"""
    payload = json.dumps({"results": [{"url": "https://x.com", "raw_content": ""}]})
    client = SearchClient(settings, build_extract=FakeExtractBuilder(FakeTool(payload)))

    assert await client.extract("https://x.com") == ""


async def test_extract_invalid_json_raises_search_error(settings):
    client = SearchClient(settings, build_extract=FakeExtractBuilder(FakeTool("broken")))

    with pytest.raises(SearchError):
        await client.extract("https://x.com")


async def test_search_logs_inputs_and_outputs_debug(settings, caplog):
    """DEBUG 级追踪：入参（query/count/depth）+ 出参（条数/逐条标题｜URL/字符数/耗时）"""
    tool = FakeTool(_search_payload([_result(), _result(title="第二篇", url="https://b.com")]))
    client = SearchClient(settings, build_search=FakeSearchBuilder(tool))

    with caplog.at_level(logging.DEBUG, logger="app.services.search"):
        await client.search("Harness Engineering", 5, "advanced")

    matches = [r.message for r in caplog.records if "search ok" in r.message]
    assert len(matches) == 1
    assert "query=Harness Engineering" in matches[0]
    assert "count=5 depth=advanced" in matches[0]
    assert "n=2" in matches[0]
    assert "Tavily 官方文档｜https://docs.tavily.com" in matches[0]
    assert "第二篇｜https://b.com" in matches[0]
    assert "chars=" in matches[0] and "elapsed=" in matches[0]


async def test_extract_logs_success_and_failed_counts(settings, caplog):
    """extract DEBUG 追踪：目标 URL、成功条数 n、失败条数 failed"""
    payload = json.dumps(
        {
            "results": [{"url": "https://a.com", "raw_content": "正文"}],
            "failed_results": [{"url": "https://b.com", "error": "404"}],
        }
    )
    client = SearchClient(settings, build_extract=FakeExtractBuilder(FakeTool(payload)))

    with caplog.at_level(logging.DEBUG, logger="app.services.search"):
        await client.extract("https://a.com")

    matches = [r.message for r in caplog.records if "extract ok" in r.message]
    assert len(matches) == 1
    assert "url=https://a.com" in matches[0]
    assert "n=1 failed=1" in matches[0]
    assert "chars=" in matches[0]


async def test_search_failure_logs_exc_info(settings, caplog):
    """失败日志携带完整异常栈（exc_info）与入参，便于定位"""
    client = SearchClient(settings, build_search=FakeSearchBuilder(FakeTool("", error=RuntimeError("boom"))))

    with caplog.at_level(logging.WARNING, logger="app.services.search"):
        with pytest.raises(SearchError):
            await client.search("词", 5, "basic")

    failure_records = [r for r in caplog.records if "search fail" in r.message]
    assert failure_records and all(r.exc_info is not None for r in failure_records)
    assert "query=词 count=5 depth=basic" in failure_records[0].message


async def test_trace_logs_carry_task_id_from_context(settings, caplog):
    """ctx 中设置了 task_id 时，追踪日志带 task_id= 前缀（链路聚合依据）"""
    tool = FakeTool(_search_payload([_result()]))
    client = SearchClient(settings, build_search=FakeSearchBuilder(tool))

    with caplog.at_level(logging.DEBUG, logger="app.services.search"):
        token = current_task_id.set("task-abc123")
        try:
            await client.search("词", 5, "basic")
        finally:
            current_task_id.reset(token)

    assert any("task_id=task-abc123" in r.message and "search ok" in r.message for r in caplog.records)


async def test_empty_query_and_url_do_not_trigger_calls(settings):
    """空 query/空 url → 直接返回空串，不构建工具、不发起调用"""
    tool = FakeTool(_search_payload([_result()]))
    search_builder = FakeSearchBuilder(tool)
    extract_builder = FakeExtractBuilder(tool)
    client = SearchClient(settings, build_search=search_builder, build_extract=extract_builder)

    assert await client.search("", 5, "basic") == ""
    assert await client.extract("") == ""
    assert search_builder.built == [] and extract_builder.built == 0

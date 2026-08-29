"""Tavily 失败诊断脚本：排查「Tavily 返回非法 JSON」——直连真实 API 打印 ainvoke 原始返回，定位返回内容而非猜测。"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_key() -> str:
    for line in open(".env", encoding="utf-8"):
        if line.startswith("TAVILY_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("未找到 TAVILY_API_KEY")


async def main() -> None:
    from langchain_tavily import TavilyExtract, TavilySearch

    api_key = load_key()
    print(f"key 前缀: {api_key[:8]}... 长度: {len(api_key)}")

    # 与线上相同的动态参数（max_results=6, depth=basic）
    search = TavilySearch(tavily_api_key=api_key, max_results=6, topic="general", search_depth="basic", include_answer=False)
    for query in ["落水自救与互救 安全教育", "Harness Engineering"]:
        raw = await search.ainvoke({"query": query})
        print(f"\n=== search query={query} ===")
        print(f"type: {type(raw).__name__}")
        text = raw if isinstance(raw, str) else repr(raw)
        print(f"前 300 字符: {text[:300]}")

    extract = TavilyExtract(tavily_api_key=api_key)
    raw = await extract.ainvoke({"urls": ["https://docs.tavily.com"]})
    print(f"\n=== extract https://docs.tavily.com ===")
    print(f"type: {type(raw).__name__}")
    text = raw if isinstance(raw, str) else repr(raw)
    print(f"前 300 字符: {text[:300]}")


if __name__ == "__main__":
    asyncio.run(main())

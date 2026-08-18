"""联网搜索增强冒烟脚本（真实 Tavily：验证 检索计划→搜索/提取→出题 全链路与任务耗时；任务 6.1 验收必跑）"""
import asyncio
import sys
import time

# 适配 Windows 控制台（WHY：默认 GBK 无法编码 ✓ 等 Unicode 符号，reconfigure 后按 UTF-8 输出）
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx

BASE = "http://127.0.0.1:8000"


async def submit_and_poll(client: httpx.AsyncClient, content: str, label: str) -> dict:
    """提交出题并轮询至结束，报告耗时与题目摘要（WHY：任务 6.1 要实测总耗时，评估 120s/90s 时序余量）"""
    t0 = time.time()
    resp = await client.post("/quiz", json={"content": content})
    assert resp.status_code == 202, resp.text
    task_id = resp.json()["task_id"]
    data: dict = {}
    for _ in range(80):
        data = (await client.get(f"/quiz/{task_id}")).json()
        if data["status"] in ("completed", "failed"):
            break
        await asyncio.sleep(1.5)
    elapsed = time.time() - t0
    if data["status"] == "completed":
        quiz = data["quiz"]
        first = quiz["questions"][0]
        print(f"✓ [{label}] 出题成功  耗时 {elapsed:.1f}s")
        print(f"    topic: {quiz['topic']}")
        print(f"    首题: {first['question'][:70]}")
        print(f"    解析: {first['explanation'][:60]}…")
    else:
        print(f"✗ [{label}] 任务失败: {data.get('error')}  耗时 {elapsed:.1f}s")
    return data


async def main() -> None:
    # trust_env=False：绕过系统代理直连本机（WHY：Windows 系统代理开启时 httpx 默认转发
    # 127.0.0.1 请求，代理返回 502 导致冒烟假失败；本地服务验证不需要代理）
    async with httpx.AsyncClient(base_url=BASE, timeout=120, trust_env=False) as client:
        print("=== 冒烟 1：最新知识（检索计划 + 搜索分支）===")
        await submit_and_poll(client, "Harness Engineering", "搜索分支")
        print()
        print("=== 冒烟 2：纯网页地址（URL 检测 + 提取分支）===")
        await submit_and_poll(client, "https://docs.tavily.com", "提取分支")


if __name__ == "__main__":
    asyncio.run(main())

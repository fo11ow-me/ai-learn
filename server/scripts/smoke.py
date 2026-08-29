"""真实 DeepSeek 冒烟脚本（方案文档 8 章：人工检查出题质量与报告正确性）

用法：
1. 确认 server/.env 已配置 DEEPSEEK_API_KEY
2. 启动服务：.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
3. 运行：.venv/Scripts/python.exe scripts/smoke.py

人工检查清单（出题质量是产品生命线）：
- 5 题 = 2 单选 + 1 多选 + 2 判断，JSON 完全符合方案文档 4.2 契约
- 题目由浅入深、覆盖 3~5 个知识点、讲解深度足够（150 字左右）
- 报告符合 4.3 契约，correct_rate 与作答一致（全对 → 100）
"""
import json
import sys
import time

import httpx

# Windows 控制台默认 GBK 会导致中文乱码，强制 UTF-8 输出便于人工检查题目质量
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:8000"
TOPIC = "光的波粒二象性"


def main() -> None:
    # trust_env=False：httpx 在 Windows 会读取注册表系统代理，本地冒烟必须直连——防止请求被系统代理转发导致 502
    with httpx.Client(timeout=120, trust_env=False) as client:
        # 1. 出题
        resp = client.post(f"{BASE}/quiz", json={"content": TOPIC})
        print(f"POST /quiz -> {resp.status_code}")
        resp.raise_for_status()
        task_id = resp.json()["task_id"]

        quiz = _poll(client, f"{BASE}/quiz/{task_id}", "quiz")
        print(json.dumps(quiz, ensure_ascii=False, indent=2))

        # 2. 按正确答案全对作答，验证正确率与作答一致
        answers = [{"question_id": q["id"], "selected": q["answer"]} for q in quiz["questions"]]
        resp = client.post(f"{BASE}/report", json={"quiz": quiz, "answers": answers})
        print(f"POST /report -> {resp.status_code}")
        resp.raise_for_status()
        report_task_id = resp.json()["task_id"]

        report = _poll(client, f"{BASE}/report/{report_task_id}", "report")
        print(json.dumps(report, ensure_ascii=False, indent=2))

        assert report["correct_rate"] == 100, f"全对作答正确率应为 100，实际 {report['correct_rate']}"
        print("冒烟通过：题库/报告契约完整，正确率与作答一致")


def _poll(client: httpx.Client, url: str, key: str, interval: float = 1.5, timeout: float = 180) -> dict:
    """轮询任务直至 completed / failed（与前端同模式）"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(url).json()
        status = data.get("status")
        print(f"  ... {status}")
        if status == "completed":
            return data[key]
        if status == "failed":
            raise SystemExit(f"任务失败: {data.get('error')}")
        time.sleep(interval)
    raise SystemExit("轮询超时")


if __name__ == "__main__":
    main()

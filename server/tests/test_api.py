"""API 路由集成测试（方案文档 4.1：202+轮询、404、422、错误码透传）"""
import asyncio
import logging

from app.core.sensitive import SensitiveFilter
from app.models.schemas import AIReportSchema, QuizSchema
from app.services.llm import LLMError
from tests.conftest import make_valid_ai_report, make_valid_answers


class FakeLLM:
    """可编程的 LLMClient 替身：generate_quiz / generate_report 按指定行为返回或抛错"""

    def __init__(self, quiz=None, ai_report=None, error=None):
        self._quiz = quiz
        self._ai_report = ai_report
        self._error = error

    async def generate_quiz(self, content, search_results=None):
        if self._error:
            raise self._error
        return self._quiz

    async def generate_report(self, quiz, answers):
        if self._error:
            raise self._error
        return self._ai_report


async def _poll(client, url, max_attempts=20):
    """模拟前端轮询：直到任务 completed / failed"""
    for _ in range(max_attempts):
        resp = await client.get(url)
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] in ("completed", "failed"):
            return data
        await asyncio.sleep(0.01)
    raise AssertionError("任务未在预期轮次内完成")


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_cors_preflight(client):
    """跨域预检：H5 调试（浏览器验证闭环）需要 CORS 支持"""
    resp = await client.options(
        "/quiz",
        headers={
            "Origin": "http://localhost:10086",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert resp.status_code == 200
    assert "access-control-allow-origin" in resp.headers


async def test_cors_header_on_response(client):
    """常规响应携带 CORS 头"""
    resp = await client.get("/health", headers={"Origin": "http://localhost:10086"})
    assert resp.headers.get("access-control-allow-origin") == "*"


async def test_qrcode_returns_png(client):
    """二维码接口返回有效 PNG（海报分享用）"""
    resp = await client.get("/qrcode", params={"text": "https://example.com/share"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"  # PNG 魔数


async def test_qrcode_default_text(client):
    """不传 text 时使用默认分享地址，仍返回 PNG"""
    resp = await client.get("/qrcode")
    assert resp.status_code == 200
    assert resp.content[:4] == b"\x89PNG"


async def test_qrcode_rejects_too_long_text(client):
    """text 超长（>256 字）拒绝"""
    resp = await client.get("/qrcode", params={"text": "x" * 257})
    assert resp.status_code == 422


async def test_quiz_full_flow(client, test_app, make_valid_quiz):
    quiz = QuizSchema.model_validate(make_valid_quiz())
    test_app.state.llm = FakeLLM(quiz=quiz)
    test_app.state.sensitive = SensitiveFilter(["赌博"])

    resp = await client.post("/quiz", json={"content": "光的波粒二象性"})
    assert resp.status_code == 202
    task_id = resp.json()["task_id"]

    data = await _poll(client, f"/quiz/{task_id}")
    assert data["status"] == "completed"
    assert data["quiz"] == quiz.model_dump()


async def test_quiz_empty_content_422(client):
    resp = await client.post("/quiz", json={"content": ""})
    assert resp.status_code == 422


async def test_quiz_too_long_422(client):
    resp = await client.post("/quiz", json={"content": "光" * 2001})
    assert resp.status_code == 422


async def test_quiz_sensitive_422(client, test_app):
    test_app.state.sensitive = SensitiveFilter(["赌博"])
    resp = await client.post("/quiz", json={"content": "我想学赌博"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "SENSITIVE_CONTENT"


async def test_get_missing_task_404(client):
    resp = await client.get("/quiz/不存在")
    assert resp.status_code == 404


async def test_quiz_task_failed_returns_error(client, test_app):
    test_app.state.llm = FakeLLM(error=LLMError("LLM_TIMEOUT", "大模型调用超时"))
    test_app.state.sensitive = SensitiveFilter(["赌博"])

    resp = await client.post("/quiz", json={"content": "内容"})
    task_id = resp.json()["task_id"]

    data = await _poll(client, f"/quiz/{task_id}")
    assert data["status"] == "failed"
    assert data["error"]["code"] == "LLM_TIMEOUT"


async def test_report_full_flow(client, test_app, make_valid_quiz):
    quiz = QuizSchema.model_validate(make_valid_quiz())
    ai = AIReportSchema.model_validate(make_valid_ai_report())
    test_app.state.llm = FakeLLM(quiz=quiz, ai_report=ai)
    test_app.state.sensitive = SensitiveFilter(["赌博"])

    resp = await client.post(
        "/report", json={"quiz": quiz.model_dump(), "answers": make_valid_answers()}
    )
    assert resp.status_code == 202
    task_id = resp.json()["task_id"]

    data = await _poll(client, f"/report/{task_id}")
    assert data["status"] == "completed"
    report = data["report"]
    assert report["correct_rate"] == 100  # 正确率与作答一致
    assert report["quote"] == ai.quote


async def test_report_invalid_answers_422(client, make_valid_quiz):
    resp = await client.post(
        "/report", json={"quiz": make_valid_quiz(), "answers": make_valid_answers()[:4]}
    )
    assert resp.status_code == 422  # 缺一条作答，契约校验拒绝


async def test_quiz_submit_logs_running_count(client, test_app, make_valid_quiz, caplog):
    """提交日志含 running 计数（WHY：并发排查——队列积压一眼可见）"""
    quiz = QuizSchema.model_validate(make_valid_quiz())
    test_app.state.llm = FakeLLM(quiz=quiz)
    test_app.state.sensitive = SensitiveFilter(["赌博"])

    with caplog.at_level(logging.INFO, logger="app.api.routes.quiz"):
        resp = await client.post("/quiz", json={"content": "内容"})

    assert resp.status_code == 202
    task_id = resp.json()["task_id"]
    assert any(
        f"quiz submit task_id={task_id}" in r.message and "running=" in r.message for r in caplog.records
    )


async def test_report_submit_logs_running_count(client, test_app, make_valid_quiz, caplog):
    """报告提交日志含 running 计数"""
    quiz = QuizSchema.model_validate(make_valid_quiz())
    ai = AIReportSchema.model_validate(make_valid_ai_report())
    test_app.state.llm = FakeLLM(quiz=quiz, ai_report=ai)
    test_app.state.sensitive = SensitiveFilter(["赌博"])

    with caplog.at_level(logging.INFO, logger="app.api.routes.report"):
        resp = await client.post(
            "/report", json={"quiz": quiz.model_dump(), "answers": make_valid_answers()}
        )

    assert resp.status_code == 202
    task_id = resp.json()["task_id"]
    assert any(
        f"report submit task_id={task_id}" in r.message and "running=" in r.message for r in caplog.records
    )

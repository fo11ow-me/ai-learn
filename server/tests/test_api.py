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
        self.doc_materials_received: list[str | None] = []

    async def generate_quiz(self, content, search_results=None, doc_materials=None):
        self.doc_materials_received.append(doc_materials)
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


# ── RAG：指定知识库出题（严格模式）的鉴权与归属（设计 D4/D6）──

async def _login(client):
    resp = await client.post("/auth/login", json={"code": "c"})
    assert resp.status_code == 200
    return resp.json()["token"], resp.json()["user"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_quiz_with_kb_id_requires_login(client, test_app, make_valid_quiz):
    """指定知识库但未登录 → 401（WHY：知识库是私有资源，严格模式必须有真实用户归属）"""
    test_app.state.llm = FakeLLM(quiz=QuizSchema.model_validate(make_valid_quiz()))
    resp = await client.post("/quiz", json={"content": "内容", "knowledge_base_id": 1})
    assert resp.status_code == 401


async def test_quiz_with_missing_kb_404(client, test_app, make_valid_quiz):
    """指定不存在的知识库 → 404（与越权一致，防枚举）"""
    test_app.state.llm = FakeLLM(quiz=QuizSchema.model_validate(make_valid_quiz()))
    token, _ = await _login(client)
    resp = await client.post(
        "/quiz", headers=_auth(token), json={"content": "内容", "knowledge_base_id": 999}
    )
    assert resp.status_code == 404


async def test_quiz_with_other_users_kb_404(client, test_app, make_valid_quiz):
    """指定他人知识库 → 404（WHY：归属校验在路由层，他人库与不存在返回一致，不泄露存在性）"""
    from app.core.config import Settings

    test_app.state.llm = FakeLLM(quiz=QuizSchema.model_validate(make_valid_quiz()))
    test_app.state.settings = Settings(deepseek_api_key="test-key", embedding_api_key="test-embed-key",
                                       jwt_secret="test-secret", auth_mock_openid="user-a")
    token_a = (await client.post("/auth/login", json={"code": "c"})).json()["token"]
    kb = (await client.post("/knowledge-base", json={"name": "A的库"}, headers=_auth(token_a))).json()

    test_app.state.settings = Settings(deepseek_api_key="test-key", embedding_api_key="test-embed-key",
                                       jwt_secret="test-secret", auth_mock_openid="user-b")
    token_b = (await client.post("/auth/login", json={"code": "c"})).json()["token"]
    resp = await client.post(
        "/quiz", headers=_auth(token_b), json={"content": "内容", "knowledge_base_id": kb["id"]}
    )
    assert resp.status_code == 404


async def test_quiz_strict_mode_full_flow(client, test_app, make_valid_quiz):
    """严格模式全流程：登录 → 建库 → 上传 → 轮询 ready → 指定库出题 → completed 且注入文档资料。
    WHY：走真实内存 Chroma + FakeEmbeddings（相同文本余弦≈1 命中，query 与文档一致才可命中）"""
    quiz = QuizSchema.model_validate(make_valid_quiz())
    llm = FakeLLM(quiz=quiz)
    test_app.state.llm = llm
    token, _ = await _login(client)

    doc_text = "量子比特与经典比特有本质区别。量子叠加态是量子计算的核心概念。" * 5
    kb = (await client.post("/knowledge-base", json={"name": "量子库"}, headers=_auth(token))).json()
    upload = await client.post(
        f"/knowledge-base/{kb['id']}/document",
        files={"file": ("doc.txt", doc_text, "text/plain")},
        headers=_auth(token),
    )
    assert upload.status_code == 202
    await _poll(client, f"/knowledge-base/task/{upload.json()['task_id']}")

    resp = await client.post(
        "/quiz", headers=_auth(token),
        json={"content": doc_text, "knowledge_base_id": kb["id"]},
    )
    assert resp.status_code == 202
    data = await _poll(client, f"/quiz/{resp.json()['task_id']}")
    assert data["status"] == "completed"
    assert llm.doc_materials_received[0] and "量子比特" in llm.doc_materials_received[0]

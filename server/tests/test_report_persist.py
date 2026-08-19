"""报告回写测试（WHY：报告完成后 report_json 关联到闯关记录；session_id 归属校验）"""
from app.models.schemas import AIReportSchema, QuizSchema
from tests.conftest import make_valid_ai_report, make_valid_answers
from tests.test_api import FakeLLM, _poll


async def _login(client):
    resp = await client.post("/auth/login", json={"code": "c"})
    return resp.json()["token"], resp.json()["user"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_report_writes_back_to_session(client, test_app, make_valid_quiz):
    """报告完成后 report_json 回写对应 session"""
    quiz = QuizSchema.model_validate(make_valid_quiz())
    test_app.state.llm = FakeLLM(quiz=quiz, ai_report=AIReportSchema.model_validate(make_valid_ai_report()))
    token, _ = await _login(client)

    session = (await client.post("/user/session", headers=_auth(token), json={
        "session_key": "a1b2c3d4-101", "content": "内容", "quiz": make_valid_quiz(),
        "answers": make_valid_answers()})).json()

    resp = await client.post("/report", headers=_auth(token), json={
        "quiz": make_valid_quiz(), "answers": make_valid_answers(),
        "session_id": session["session_id"]})
    assert resp.status_code == 202
    data = await _poll(client, f"/report/{resp.json()['task_id']}")
    assert data["status"] == "completed"

    detail = (await client.get(f"/user/session/{session['session_id']}", headers=_auth(token))).json()
    assert detail["report"] is not None
    assert detail["report"]["correct_rate"] == 100


async def test_report_anonymous_without_session_allowed(client, test_app, make_valid_quiz):
    """匿名（无 token、无 session_id）仍可生成报告 → 202（WHY：需求文档 4.6 游客模式——核心闯关可用但不落库）"""
    quiz = QuizSchema.model_validate(make_valid_quiz())
    test_app.state.llm = FakeLLM(quiz=quiz, ai_report=AIReportSchema.model_validate(make_valid_ai_report()))
    resp = await client.post("/report", json={"quiz": make_valid_quiz(), "answers": make_valid_answers()})
    assert resp.status_code == 202
    data = await _poll(client, f"/report/{resp.json()['task_id']}")
    assert data["status"] == "completed"


async def test_report_session_id_requires_login(client, make_valid_quiz):
    """携带 session_id 但未登录 → 401（WHY：session_id 关联记录必须鉴权，匿名不可写他人记录）"""
    token, _ = await _login(client)
    created = (await client.post("/user/session", headers=_auth(token), json={
        "session_key": "a1b2c3d4-103", "content": "内容", "quiz": make_valid_quiz(),
        "answers": make_valid_answers()})).json()
    resp = await client.post("/report", json={
        "quiz": make_valid_quiz(), "answers": make_valid_answers(),
        "session_id": created["session_id"]})
    assert resp.status_code == 401


async def test_report_other_users_session_422(client, test_app, make_valid_quiz):
    """session_id 不属于当前用户 → 422（WHY：防止报告写进他人记录）。
    MOCK 登录所有 code 同一 openid，第二用户直接落库造数据"""
    from app.models.db_models import User
    from app.services.auth import issue_token

    quiz = QuizSchema.model_validate(make_valid_quiz())
    test_app.state.llm = FakeLLM(quiz=quiz, ai_report=AIReportSchema.model_validate(make_valid_ai_report()))
    token_a, _ = await _login(client)
    created = (await client.post("/user/session", headers=_auth(token_a), json={
        "session_key": "a1b2c3d4-102", "content": "内容", "quiz": make_valid_quiz(),
        "answers": make_valid_answers()})).json()

    db_engine = test_app.state.db
    async with db_engine.maker() as db:
        other = User(openid="other-user")
        db.add(other)
        await db.commit()
        await db.refresh(other)
        other_id = other.id
    # 签发与解码同源（WHY：get_current_user 统一从 app.state.settings 解码，测试内签发也必须用同一配置）
    token_b = issue_token(other_id, test_app.state.settings)

    resp = await client.post("/report", headers=_auth(token_b), json={
        "quiz": make_valid_quiz(), "answers": make_valid_answers(),
        "session_id": created["session_id"]})
    assert resp.status_code == 422

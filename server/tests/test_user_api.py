"""用户资料接口测试（WHY：受保护接口 401、个人中心契约、昵称编辑与校验）"""
from app.core.sensitive import SensitiveFilter
from tests.conftest import make_valid_answers


async def _login(client):
    resp = await client.post("/auth/login", json={"code": "c"})
    assert resp.status_code == 200
    data = resp.json()
    return data["token"], data["user"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_get_me_requires_login(client):
    resp = await client.get("/user/me")
    assert resp.status_code == 401


async def test_get_me_contract(client):
    token, user = await _login(client)
    resp = await client.get("/user/me", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["id"] == user["id"]
    assert data["user"]["coins"] == 0
    assert data["stats"] == {"sessions": 0, "correct_rate": 0, "knowledge_points": 0,
                             "total_correct": 0}
    assert len(data["daily_answers"]) == 7
    assert data["knowledge_tree"] == []
    assert data["recent_sessions"] == []


async def test_put_me_updates_nickname_and_avatar(client):
    token, user = await _login(client)
    resp = await client.put("/user/me", json={"nickname": "湖畔诗人"}, headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["nickname"] == "湖畔诗人"
    assert data["avatar_text"] == "湖"  # 头像自动取首字

    resp = await client.get("/user/me", headers=_auth(token))
    assert resp.json()["user"]["nickname"] == "湖畔诗人"


async def test_put_me_sensitive_nickname_422(client, test_app):
    test_app.state.sensitive = SensitiveFilter(["赌博"])
    token, _ = await _login(client)
    resp = await client.put("/user/me", json={"nickname": "赌博俱乐部"}, headers=_auth(token))
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_NICKNAME"


async def test_put_me_empty_nickname_422(client):
    token, _ = await _login(client)
    resp = await client.put("/user/me", json={"nickname": ""}, headers=_auth(token))
    assert resp.status_code == 422


async def test_put_me_blank_nickname_422(client):
    """纯空格昵称 → 422（WHY：strip 后为空若继续走 avatar_text = nickname[0] 会 500）"""
    token, _ = await _login(client)
    resp = await client.put("/user/me", json={"nickname": "   "}, headers=_auth(token))
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_NICKNAME"


async def test_put_me_too_long_nickname_422(client):
    token, _ = await _login(client)
    resp = await client.put("/user/me", json={"nickname": "林" * 17}, headers=_auth(token))
    assert resp.status_code == 422


async def test_submit_session_all_correct(client, make_valid_quiz):
    token, _ = await _login(client)
    resp = await client.post("/user/session", headers=_auth(token), json={
        "session_key": "a1b2c3d4-001", "content": "光的波粒二象性",
        "quiz": make_valid_quiz(), "answers": make_valid_answers(),
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["coins_delta"] == 50
    assert data["coins_counted"] is True
    assert data["coins_total"] == 50


async def test_submit_session_duplicate_key_returns_first(client, make_valid_quiz):
    token, _ = await _login(client)
    payload = {"session_key": "a1b2c3d4-002", "content": "内容",
               "quiz": make_valid_quiz(), "answers": make_valid_answers()}
    first = (await client.post("/user/session", headers=_auth(token), json=payload)).json()
    second = (await client.post("/user/session", headers=_auth(token), json=payload)).json()
    assert first["session_id"] == second["session_id"]
    assert first["coins_delta"] == second["coins_delta"]
    assert second["coins_total"] == 50  # 幂等：不重复入账


async def test_submit_session_antispam(client, make_valid_quiz):
    token, _ = await _login(client)
    base = {"content": "同一主题", "quiz": make_valid_quiz(), "answers": make_valid_answers()}
    first = await client.post("/user/session", headers=_auth(token),
                              json={**base, "session_key": "a1b2c3d4-003"})
    assert first.json()["coins_counted"] is True
    second = await client.post("/user/session", headers=_auth(token),
                               json={**base, "session_key": "a1b2c3d4-004"})
    assert second.json()["coins_counted"] is False
    assert second.json()["coins_delta"] == 0


async def test_submit_session_sensitive_content_422(client, test_app, make_valid_quiz):
    test_app.state.sensitive = SensitiveFilter(["赌博"])
    token, _ = await _login(client)
    resp = await client.post("/user/session", headers=_auth(token), json={
        "session_key": "a1b2c3d4-005", "content": "我想学赌博",
        "quiz": make_valid_quiz(), "answers": make_valid_answers()})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "SENSITIVE_CONTENT"


async def test_submit_session_requires_login(client, make_valid_quiz):
    resp = await client.post("/user/session", json={
        "session_key": "a1b2c3d4-006", "content": "c",
        "quiz": make_valid_quiz(), "answers": make_valid_answers()})
    assert resp.status_code == 401


async def test_get_session_detail(client, make_valid_quiz):
    token, _ = await _login(client)
    quiz = make_valid_quiz()
    created = (await client.post("/user/session", headers=_auth(token), json={
        "session_key": "a1b2c3d4-007", "content": "内容",
        "quiz": quiz, "answers": make_valid_answers()})).json()
    resp = await client.get(f"/user/session/{created['session_id']}", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["quiz"]["topic"] == quiz["topic"]  # 题库快照一致
    assert data["correct_rate"] == 100
    assert data["report"] is None  # 报告未生成前为 null


async def test_get_session_other_user_404(client, test_app, make_valid_quiz):
    """越权访问他人记录 → 404（不泄露存在性）。
    WHY：MOCK 登录所有 code 映射同一 openid，第二个用户直接落库造数据"""
    from app.models.db_models import User
    from app.services.auth import issue_token

    token_a, _ = await _login(client)
    created = (await client.post("/user/session", headers=_auth(token_a), json={
        "session_key": "a1b2c3d4-008", "content": "内容",
        "quiz": make_valid_quiz(), "answers": make_valid_answers()})).json()

    db_engine = test_app.state.db
    async with db_engine.maker() as db:
        other = User(openid="other-user")
        db.add(other)
        await db.commit()
        await db.refresh(other)
        other_id = other.id
    # 签发与解码同源（WHY：get_current_user 统一从 app.state.settings 解码，测试内签发也必须用同一配置）
    token_b = issue_token(other_id, test_app.state.settings)

    resp = await client.get(f"/user/session/{created['session_id']}", headers=_auth(token_b))
    assert resp.status_code == 404

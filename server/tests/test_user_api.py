"""用户资料接口测试（WHY：受保护接口 401、个人中心契约、昵称编辑与校验）"""
from app.core.sensitive import SensitiveFilter


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

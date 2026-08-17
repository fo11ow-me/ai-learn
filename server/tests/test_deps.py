"""JWT 鉴权依赖测试（WHY：无 token/坏 token/过期 token/用户不存在 → 统一 401 与错误码）"""
import jwt
import pytest


@pytest.fixture(autouse=True)
def _fixed_settings(test_app, monkeypatch):
    """固定 JWT 密钥（WHY：测试与真实 .env 解耦；get_current_user 内 get_settings() 按模块属性查找，monkeypatch 生效）"""
    from app.core.config import Settings

    test_app.state.settings = Settings(deepseek_api_key="test", jwt_secret="test-secret")
    monkeypatch.setattr("app.core.config.get_settings", lambda: test_app.state.settings)
    return test_app.state.settings


async def test_me_requires_token(client):
    resp = await client.get("/user/me")
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "UNAUTHORIZED"


async def test_me_with_bad_token(client):
    resp = await client.get("/user/me", headers={"Authorization": "Bearer bad-token"})
    assert resp.status_code == 401


async def test_me_with_expired_token(client, _fixed_settings):
    """过期 token → 401 TOKEN_EXPIRED（前端据此触发重新登录）"""
    token = jwt.encode({"sub": "1", "iat": 0, "exp": 0}, _fixed_settings.jwt_secret,
                       algorithm="HS256")
    resp = await client.get("/user/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "TOKEN_EXPIRED"


async def test_me_with_unknown_user(client, _fixed_settings):
    """token 合法但用户不存在 → 401（WHY：伪造/已删用户场景；用户不存在即视为未登录）"""
    from app.services.auth import issue_token

    token = issue_token(999999, _fixed_settings)
    resp = await client.get("/user/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401

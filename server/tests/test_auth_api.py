"""登录接口测试：MOCK 登录幂等、新用户默认值、code 校验、微信失败 401。"""
import pytest


async def _login(client, code="any-code"):
    return await client.post("/auth/login", json={"code": code})


async def test_login_returns_token_and_user(client):
    resp = await _login(client)
    assert resp.status_code == 200
    data = resp.json()
    assert data["token"]
    assert data["user"]["nickname"] == "林中客"
    assert data["user"]["avatar_text"] == "林"
    assert data["user"]["coins"] == 0


async def test_login_is_idempotent(client):
    """同 openid 重复登录返回同一用户（不重复建号）"""
    first = (await _login(client)).json()["user"]
    second = (await _login(client)).json()["user"]
    assert first["id"] == second["id"]


async def test_login_empty_code_422(client):
    resp = await client.post("/auth/login", json={"code": ""})
    assert resp.status_code == 422


async def test_login_wechat_failure_401(client, test_app, monkeypatch):
    """正式模式（AUTH_MOCK=false）下微信调用失败 → 401 WX_LOGIN_FAILED。
    deps() 惰性装配走 hasattr 检查，请求前预置 app.state.settings 即可切换正式模式"""
    from app.core.config import Settings

    from app.services.auth import AuthError

    test_app.state.settings = Settings(deepseek_api_key="test", jwt_secret="s", auth_mock=False)

    def fake_fetch(code, settings):
        raise AuthError("微信登录失败，请重试", code="WX_LOGIN_FAILED")

    monkeypatch.setattr("app.api.routes.auth.fetch_openid", fake_fetch)
    resp = await _login(client)
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "WX_LOGIN_FAILED"


async def test_login_network_failure_401(client, test_app, monkeypatch):
    """正式模式下 fetch_openid 抛 httpx 网络异常（如连接失败）→ 401 WX_LOGIN_FAILED，不逃逸为 500"""
    import httpx

    from app.core.config import Settings

    test_app.state.settings = Settings(deepseek_api_key="test", jwt_secret="s", auth_mock=False)

    def fake_fetch(code, settings):
        raise httpx.HTTPError("boom")

    monkeypatch.setattr("app.api.routes.auth.fetch_openid", fake_fetch)
    resp = await _login(client)
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "WX_LOGIN_FAILED"

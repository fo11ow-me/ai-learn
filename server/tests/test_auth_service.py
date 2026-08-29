"""认证服务测试：MOCK 登录、JWT 签发/校验、过期/无效 token、code2session 失败兜底。"""
import time

import jwt
import pytest

from app.core.config import Settings
from app.services.auth import AuthError, decode_token, fetch_openid, issue_token

AUTH_SETTINGS = Settings(deepseek_api_key="test", jwt_secret="test-secret")


def test_mock_fetch_openid_returns_mock_openid():
    assert fetch_openid("any-code", AUTH_SETTINGS) == "mock-user"


def test_mock_openid_configurable():
    settings = Settings(deepseek_api_key="test", jwt_secret="s", auth_mock_openid="dev-01")
    assert fetch_openid("c", settings) == "dev-01"


async def test_fetch_openid_wechat_error_raises(monkeypatch):
    """正式模式：微信返回 errcode → AuthError"""
    class FakeResp:
        status_code = 200

        def json(self):
            return {"errcode": 40029, "errmsg": "invalid code"}

    def fake_get(url, **kwargs):
        return FakeResp()

    monkeypatch.setattr("app.services.auth.httpx.get", fake_get)
    settings = Settings(deepseek_api_key="test", jwt_secret="s", auth_mock=False,
                        wechat_appid="app", wechat_secret="sec")
    with pytest.raises(AuthError) as exc:
        fetch_openid("bad-code", settings)
    assert exc.value.code == "WX_LOGIN_FAILED"


async def test_fetch_openid_non_2xx_raises(monkeypatch):
    """正式模式：微信返回非 200（如网关 502 HTML 错误页）→ AuthError，不逃逸为解析异常"""
    class FakeResp:
        status_code = 502

        def json(self):
            raise ValueError("HTML body is not JSON")

    monkeypatch.setattr("app.services.auth.httpx.get", lambda url, **kw: FakeResp())
    settings = Settings(deepseek_api_key="test", jwt_secret="s", auth_mock=False,
                        wechat_appid="app", wechat_secret="sec")
    with pytest.raises(AuthError) as exc:
        fetch_openid("code", settings)
    assert exc.value.code == "WX_LOGIN_FAILED"


async def test_fetch_openid_non_json_body_raises(monkeypatch):
    """正式模式：200 但响应体非 JSON（微信网关异常时可能返回 HTML）→ AuthError"""
    class FakeResp:
        status_code = 200

        def json(self):
            raise ValueError("HTML body is not JSON")

    monkeypatch.setattr("app.services.auth.httpx.get", lambda url, **kw: FakeResp())
    settings = Settings(deepseek_api_key="test", jwt_secret="s", auth_mock=False,
                        wechat_appid="app", wechat_secret="sec")
    with pytest.raises(AuthError) as exc:
        fetch_openid("code", settings)
    assert exc.value.code == "WX_LOGIN_FAILED"


async def test_fetch_openid_success(monkeypatch):
    """正式模式：正常返回 openid"""
    class FakeResp:
        status_code = 200

        def json(self):
            return {"openid": "wx-openid-1", "session_key": "sk"}

    monkeypatch.setattr("app.services.auth.httpx.get", lambda url, **kw: FakeResp())
    settings = Settings(deepseek_api_key="test", jwt_secret="s", auth_mock=False,
                        wechat_appid="app", wechat_secret="sec")
    assert fetch_openid("code", settings) == "wx-openid-1"


def test_issue_and_decode_roundtrip():
    token = issue_token(42, AUTH_SETTINGS)
    assert decode_token(token, AUTH_SETTINGS) == 42


def test_decode_expired_token_raises():
    token = jwt.encode({"sub": "1", "iat": 0, "exp": int(time.time()) - 10},
                       AUTH_SETTINGS.jwt_secret, algorithm="HS256")
    with pytest.raises(AuthError) as exc:
        decode_token(token, AUTH_SETTINGS)
    assert exc.value.code == "TOKEN_EXPIRED"


def test_decode_invalid_token_raises():
    with pytest.raises(AuthError) as exc:
        decode_token("not-a-jwt", AUTH_SETTINGS)
    assert exc.value.code == "UNAUTHORIZED"


def test_decode_wrong_secret_raises():
    token = jwt.encode({"sub": "1", "exp": int(time.time()) + 60}, "other-secret", algorithm="HS256")
    with pytest.raises(AuthError):
        decode_token(token, AUTH_SETTINGS)

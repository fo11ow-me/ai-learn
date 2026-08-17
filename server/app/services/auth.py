"""认证服务（WHY：微信 code2session + JWT 签发/校验集中于此，路由层只依赖本模块；MOCK 开关见配置）"""
import time

import httpx
import jwt

from app.core.config import Settings

WX_CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"


class AuthError(Exception):
    """认证失败：code 供前端映射文案（UNAUTHORIZED / TOKEN_EXPIRED / WX_LOGIN_FAILED）"""

    def __init__(self, message: str, code: str = "UNAUTHORIZED"):
        super().__init__(message)
        self.code = code
        self.message = message


def fetch_openid(code: str, settings: Settings) -> str:
    """code 换 openid（WHY：AUTH_MOCK 时跳过微信调用，开发期无正式 AppID 也能跑通全流程）"""
    if settings.auth_mock:
        return settings.auth_mock_openid
    resp = httpx.get(
        WX_CODE2SESSION_URL,
        params={
            "appid": settings.wechat_appid,
            "secret": settings.wechat_secret,
            "js_code": code,
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    # 非 200 / 非 JSON 响应统一归一化为 AuthError（WHY：微信网关异常时可能返回 HTML
    # 错误页，resp.json() 解析会抛 ValueError，若不捕获则逃逸为 500，路由层只识别 AuthError）
    if resp.status_code != 200:
        raise AuthError("微信登录失败，请重试", code="WX_LOGIN_FAILED")
    try:
        data = resp.json()
    except ValueError as exc:
        raise AuthError("微信登录失败，请重试", code="WX_LOGIN_FAILED") from exc
    if data.get("errcode") or not data.get("openid"):
        raise AuthError("微信登录失败，请重试", code="WX_LOGIN_FAILED")
    return data["openid"]


def issue_token(user_id: int, settings: Settings) -> str:
    """签发 JWT（HS256，有效期 JWT_EXPIRE_DAYS 天）"""
    now = int(time.time())
    payload = {"sub": str(user_id), "iat": now, "exp": now + settings.jwt_expire_days * 86400}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str, settings: Settings) -> int:
    """解析 JWT 返回 user_id；无效/过期抛 AuthError（WHY：路由依赖统一映射为 401）"""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        return int(payload["sub"])
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("登录已过期，请重新登录", code="TOKEN_EXPIRED") from exc
    except (jwt.InvalidTokenError, KeyError, ValueError) as exc:
        raise AuthError("未登录或登录已失效") from exc

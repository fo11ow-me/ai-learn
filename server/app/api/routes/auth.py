"""登录路由：登录接口本身无鉴权——它是签发 token 的入口。"""
import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import deps
from app.models.db_models import User
from app.services.auth import AuthError, fetch_openid, issue_token

router = APIRouter()


class LoginRequest(BaseModel):
    """登录请求：wx.login 的临时 code"""

    code: str = Field(min_length=1, max_length=256)


@router.post("/auth/login")
async def login(body: LoginRequest, request: Request) -> dict:
    """登录：code 换 openid → upsert 用户 → 签发 JWT。

    新用户自动注册，老用户幂等返回。
    """
    settings = deps(request.app)[3]
    try:
        openid = fetch_openid(body.code, settings)
    except (AuthError, httpx.HTTPError, ValueError) as exc:
        # httpx.HTTPError/ValueError 兜底：正式模式网络异常或非 JSON 响应统一为 401，不逃逸为 500
        raise HTTPException(status_code=401, detail={"code": "WX_LOGIN_FAILED", "message": "微信登录失败，请重试"}) from exc
    db_engine = request.app.state.db
    async with db_engine.maker() as db:
        user = await db.scalar(select(User).where(User.openid == openid))
        if user is None:
            user = User(openid=openid)  # 默认昵称「林中客」/头像「林」/金币 0
            db.add(user)
            await db.commit()
            await db.refresh(user)
        token = issue_token(user.id, settings)
        return {
            "token": token,
            "user": {"id": user.id, "nickname": user.nickname,
                     "avatar_text": user.avatar_text, "coins": user.coins},
        }

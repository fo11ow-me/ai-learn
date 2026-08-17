"""用户路由（WHY：全部受 JWT 保护；路由层只做参数校验与组装，业务在 services）"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import deps, get_current_user
from app.models.db_models import User
from app.services.stats import build_profile

router = APIRouter()

MAX_NICKNAME_LENGTH = 16


class UpdateMeRequest(BaseModel):
    """编辑资料请求：仅昵称（头像自动取昵称首字，原型屏 1 文字头像）"""

    nickname: str = Field(min_length=1, max_length=MAX_NICKNAME_LENGTH)


@router.get("/user/me")
async def get_me(request: Request, user: User = Depends(get_current_user)) -> dict:
    """个人中心全量数据（用户信息 + 统计 + 近七日 + 知识树 + 最近复盘，一次加载 YAGNI 不拆分）"""
    db_engine = request.app.state.db
    async with db_engine.maker() as db:
        return await build_profile(db, user)


@router.put("/user/me")
async def update_me(body: UpdateMeRequest, request: Request,
                    user: User = Depends(get_current_user)) -> dict:
    """编辑昵称：敏感词过滤 + 头像同步首字（WHY：文字头像无需文件上传，符合原型）"""
    sensitive = deps(request.app)[1]
    nickname = body.nickname.strip()
    if not nickname:
        # 纯空格昵称 strip 后为空（WHY：Pydantic 只校验原始串长度；不拦截会走到 nickname[0] 抛 IndexError → 500）
        raise HTTPException(status_code=422, detail={"code": "INVALID_NICKNAME", "message": "昵称不能为空"})
    if sensitive.contains(nickname):
        raise HTTPException(status_code=422, detail={"code": "INVALID_NICKNAME", "message": "昵称包含敏感信息，请更换"})
    db_engine = request.app.state.db
    async with db_engine.maker() as db:
        user = await db.scalar(select(User).where(User.id == user.id))
        user.nickname = nickname
        user.avatar_text = nickname[0]
        await db.commit()
        return {"id": user.id, "nickname": user.nickname,
                "avatar_text": user.avatar_text, "coins": user.coins}

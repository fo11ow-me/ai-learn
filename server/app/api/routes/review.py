"""错题重练路由（WHY：全部受 JWT 保护；路由层只做参数校验与组装，业务在 services/review.py）"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.models.db_models import User
from app.services.review import ReviewSubmitError, build_review_board, submit_attempts
from app.services.subscribe import register_quota

router = APIRouter()


class ReviewAttempt(BaseModel):
    """重练作答：item_id + 已选选项索引（与闯关 AnswerRecord 同心智；服务端按快照重判，不信任前端）"""

    item_id: int
    selected: list[int] = Field(min_length=1)


class ReviewSubmitRequest(BaseModel):
    """重练提交请求：全部作答一次性提交（单事务统一更新调度状态）"""

    attempts: list[ReviewAttempt] = Field(min_length=1)


class SubscribeRequest(BaseModel):
    """订阅授权登记请求（前端 wx.requestSubscribeMessage 授权成功后调用）"""

    template_id: str = Field(min_length=1, max_length=64)


@router.get("/user/review")
async def get_review(request: Request, user: User = Depends(get_current_user)) -> dict:
    """错题本：待重温列表 + 统计 + 未来 7 天复习安排（单次加载，错题本页与「我的」页入口卡共用）"""
    db_engine = request.app.state.db
    async with db_engine.maker() as db:
        return await build_review_board(db, user.id)


@router.post("/user/review/submit", status_code=200)
async def submit_review(body: ReviewSubmitRequest, request: Request,
                        user: User = Depends(get_current_user)) -> dict:
    """重练提交：服务端按快照重新判分 + 状态机更新（不计金币、不调 AI）。
    校验失败（item 不存在/非本人/已掌握）按错误码返回"""
    db_engine = request.app.state.db
    async with db_engine.maker() as db:
        try:
            updated = await submit_attempts(db, user.id,
                                            [a.model_dump() for a in body.attempts])
        except ReviewSubmitError as exc:
            raise HTTPException(status_code=exc.status_code,
                                detail={"code": exc.code, "message": exc.message})
        return {"updated": updated}


@router.post("/user/subscribe", status_code=200)
async def register_subscribe(body: SubscribeRequest, request: Request,
                             user: User = Depends(get_current_user)) -> dict:
    """登记一条订阅推送配额（一次性订阅：授权一次推一条，配额持久化）。
    前端仅在 wx.requestSubscribeMessage 授权成功后调用"""
    db_engine = request.app.state.db
    async with db_engine.maker() as db:
        quota = await register_quota(db, user.id, body.template_id)
        return {"quota": quota}

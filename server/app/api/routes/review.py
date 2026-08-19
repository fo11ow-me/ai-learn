"""错题重练路由（WHY：全部受 JWT 保护；路由层只做参数校验与组装，业务在 services/review.py）"""
from fastapi import APIRouter, Depends, Request

from app.api.deps import get_current_user
from app.models.db_models import User
from app.services.review import build_review_board

router = APIRouter()


@router.get("/user/review")
async def get_review(request: Request, user: User = Depends(get_current_user)) -> dict:
    """错题本：待重温列表 + 统计 + 未来 7 天复习安排（单次加载，错题本页与「我的」页入口卡共用）"""
    db_engine = request.app.state.db
    async with db_engine.maker() as db:
        return await build_review_board(db, user.id)

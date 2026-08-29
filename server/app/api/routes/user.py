"""用户路由：全部受 JWT 保护；路由层只做参数校验与组装，业务在 services。"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import deps, get_current_user
from app.models.db_models import QuizSession, User
from app.models.schemas import ReportRequest
from app.services.coins import SessionAlreadyExists, settle_session
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
    """编辑昵称：敏感词过滤 + 头像同步首字。文字头像无需文件上传，符合原型。"""
    sensitive = deps(request.app)[1]
    nickname = body.nickname.strip()
    if not nickname:
        # 纯空格昵称 strip 后为空：Pydantic 只校验原始串长度；不拦截会走到 nickname[0] 抛 IndexError → 500
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


class SessionCreateRequest(ReportRequest):
    """闯关结算请求：复用 ReportRequest 的作答覆盖/索引/单选一致性校验。

    契约与报告接口一致，不重复校验逻辑。
    """

    session_key: str = Field(min_length=8, max_length=36, pattern=r"^[0-9a-fA-F\-]+$")
    content: str = Field(min_length=1, max_length=2000)


@router.post("/user/session", status_code=200)
async def create_session(body: SessionCreateRequest, request: Request,
                         user: User = Depends(get_current_user)) -> dict:
    """闯关结算：服务端判分 + 防刷 + 金币入账（同步；幂等：同 session_key 返回首次结果）"""
    sensitive = deps(request.app)[1]
    if sensitive.contains(body.content):
        raise HTTPException(status_code=422, detail={"code": "SENSITIVE_CONTENT",
                                                     "message": "内容包含敏感信息，请更换内容"})
    db_engine = request.app.state.db
    async with db_engine.maker() as db:
        user_db = await db.scalar(select(User).where(User.id == user.id))
        quiz_dict = body.quiz.model_dump()  # 落库/判分用纯 dict
        answers_dicts = [a.model_dump() for a in body.answers]
        try:
            session = await settle_session(
                db, user=user_db, session_key=body.session_key, content=body.content,
                quiz=quiz_dict, answers=answers_dicts)
        except SessionAlreadyExists as exc:
            session = exc.session  # 幂等：返回首次结算结果
        return {"session_id": session.id, "coins_delta": session.coins_delta,
                "coins_counted": session.coins_counted, "coins_total": user_db.coins}


@router.get("/user/session/{session_id}")
async def get_session(session_id: int, request: Request,
                      user: User = Depends(get_current_user)) -> dict:
    """历史闯关详情：题目快照 + 作答 + 报告。

    报告页历史模式数据源；越权统一 404。
    """
    db_engine = request.app.state.db
    async with db_engine.maker() as db:
        session = await db.get(QuizSession, session_id)
        if session is None or session.user_id != user.id:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "记录不存在"})
        return {"id": session.id, "topic": session.topic, "content": session.content,
                "total_questions": session.total_questions,
                "correct_count": session.correct_count, "correct_rate": session.correct_rate,
                "coins_delta": session.coins_delta, "coins_counted": session.coins_counted,
                "quiz": session.quiz_json, "answers": session.answers_json,
                "report": session.report_json, "created_at": session.created_at.isoformat()}

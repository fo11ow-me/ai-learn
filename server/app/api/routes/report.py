"""报告路由（方案文档 4.1 + 用户系统：匿名可生成（不落库）；带 session_id 必须登录且归属校验）"""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import deps, get_optional_user
from app.models.db_models import QuizSession, User
from app.models.schemas import ReportRequest
from app.services.report import run_report_task

router = APIRouter()
_logger = logging.getLogger(__name__)


@router.post("/report", status_code=202)
async def create_report(body: ReportRequest, request: Request,
                        user: User | None = Depends(get_optional_user)) -> dict:
    """创建报告任务：契约校验 + （可选）session_id 归属校验后异步生成（WHY：游客不关联记录，登录用户回写）"""
    llm, sensitive, store, settings = deps(request.app)
    if body.session_id is not None:
        if user is None:
            raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "未登录"})
        db_engine = request.app.state.db
        async with db_engine.maker() as db:
            session = await db.get(QuizSession, body.session_id)
            if session is None or session.user_id != user.id:
                raise HTTPException(status_code=422, detail={"code": "SESSION_NOT_FOUND",
                                                             "message": "闯关记录不存在"})
    task_id = store.create()
    # 提交日志带 running 计数（WHY：并发排查——多任务并发时一眼看出队列积压）
    _logger.info("report submit task_id=%s questions=%d running=%d", task_id, len(body.quiz.questions), store.count_running())
    asyncio.create_task(run_report_task(
        task_id, body.quiz, body.answers, llm, sensitive, settings,
        store=store, session_id=body.session_id, db_engine=request.app.state.db))
    return {"task_id": task_id}


@router.get("/report/{task_id}")
def get_report(task_id: str, request: Request) -> dict:
    """查询报告任务状态（WHY：轮询接口保持无鉴权，任务 ID 即凭证，向后兼容）"""
    _, _, store, _ = deps(request.app)
    info = store.get(task_id)
    if info is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    resp: dict = {"status": info.status}
    if info.payload is not None:
        resp["report"] = info.payload["report"]
    if info.error is not None:
        resp["error"] = info.error.model_dump()
    return resp

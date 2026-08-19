"""出题路由（方案文档 4.1：POST /quiz 202 + task_id，GET 轮询；内容 ≤2000 字）"""
import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.api.deps import deps, get_optional_user
from app.models.db_models import KnowledgeBase
from app.models.schemas import QuizCreateRequest
from app.services.quiz import run_quiz_task

router = APIRouter()
_logger = logging.getLogger(__name__)


@router.post("/quiz", status_code=202)
async def create_quiz(body: QuizCreateRequest, request: Request) -> dict:
    """创建出题任务：敏感词过滤（输入侧）→ 知识库归属校验 → 异步生成（WHY：立即返回 task_id，前端 1.5s 轮询）。
    knowledge_base_id 非空 = 严格模式（仅库内出题）：必须登录且库归属当前用户，否则 401/404（防枚举）"""
    llm, sensitive, store, settings = deps(request.app)
    if sensitive.contains(body.content):
        raise HTTPException(
            status_code=422, detail={"code": "SENSITIVE_CONTENT", "message": "内容包含敏感信息，请更换内容"}
        )
    user = await get_optional_user(request)
    if body.knowledge_base_id is not None:
        if user is None:
            raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "未登录"})
        async with request.app.state.db.maker() as db:
            kb = await db.scalar(
                select(KnowledgeBase).where(
                    KnowledgeBase.id == body.knowledge_base_id, KnowledgeBase.user_id == user.id
                )
            )
        if kb is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
    task_id = store.create()
    # 提交日志带 running 计数（WHY：并发排查——多任务并发时一眼看出当前队列积压了几个在跑的任务）
    _logger.info(
        "quiz submit task_id=%s content_len=%d running=%d kb_id=%s",
        task_id, len(body.content), store.count_running(), body.knowledge_base_id,
    )
    asyncio.create_task(
        run_quiz_task(
            task_id, body.content, llm, sensitive, settings, store=store,
            search=request.app.state.search,
            knowledge_base=request.app.state.knowledge_base,
            user_id=user.id if user else None,
            knowledge_base_id=body.knowledge_base_id,
        )
    )
    return {"task_id": task_id}


@router.get("/quiz/{task_id}")
def get_quiz(task_id: str, request: Request) -> dict:
    """查询出题任务状态：completed 返回 quiz，failed 返回 error，不存在 404"""
    _, _, store, _ = deps(request.app)
    info = store.get(task_id)
    if info is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    resp: dict = {"status": info.status}
    if info.payload is not None:
        resp["quiz"] = info.payload["quiz"]
    if info.error is not None:
        resp["error"] = info.error.model_dump()
    return resp

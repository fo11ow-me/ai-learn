"""出题路由（方案文档 4.1：POST /quiz 202 + task_id，GET 轮询；内容 ≤2000 字）"""
import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import deps
from app.services.quiz import run_quiz_task

router = APIRouter()

MAX_CONTENT_LENGTH = 2000  # 方案文档 4.1：内容超 2000 字 422


class QuizCreateRequest(BaseModel):
    """出题请求：用户输入的想学内容"""

    content: str = Field(min_length=1, max_length=MAX_CONTENT_LENGTH)


@router.post("/quiz", status_code=202)
async def create_quiz(body: QuizCreateRequest, request: Request) -> dict:
    """创建出题任务：敏感词过滤（输入侧）后异步生成（WHY：立即返回 task_id，前端 1.5s 轮询）"""
    llm, sensitive, store, settings = deps(request.app)
    if sensitive.contains(body.content):
        raise HTTPException(
            status_code=422, detail={"code": "SENSITIVE_CONTENT", "message": "内容包含敏感信息，请更换内容"}
        )
    task_id = store.create()
    asyncio.create_task(run_quiz_task(task_id, body.content, llm, sensitive, settings, store=store))
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

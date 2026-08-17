"""报告路由（方案文档 4.1：POST /report 202 + task_id，GET 轮询；请求体经 ReportRequest 契约校验）"""
import asyncio

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import deps
from app.models.schemas import ReportRequest
from app.services.report import run_report_task

router = APIRouter()


@router.post("/report", status_code=202)
async def create_report(body: ReportRequest, request: Request) -> dict:
    """创建报告任务：契约校验（Pydantic 自动 422）后异步生成（WHY：与出题同构的轮询模式）"""
    llm, sensitive, store, settings = deps(request.app)
    task_id = store.create()
    asyncio.create_task(run_report_task(task_id, body.quiz, body.answers, llm, sensitive, settings, store=store))
    return {"task_id": task_id}


@router.get("/report/{task_id}")
def get_report(task_id: str, request: Request) -> dict:
    """查询报告任务状态：completed 返回 report，failed 返回 error，不存在 404"""
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

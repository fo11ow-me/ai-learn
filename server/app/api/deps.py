"""路由依赖装配（WHY：生产首次请求时按真实配置惰性初始化；测试可预先写入 app.state 注入 fake）"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request

from app.core.config import get_settings
from app.core.db import db_engine
from app.core.sensitive import load_filter
from app.core.tasks import task_store
from app.services.llm import LLMClient


def deps(app: FastAPI):
    """获取依赖四元组 (llm, sensitive, store, settings)。
    逐项惰性装配（WHY：测试可只注入部分依赖，其余仍按真实配置初始化）"""
    if not hasattr(app.state, "settings"):
        app.state.settings = get_settings()
    if not hasattr(app.state, "llm"):
        app.state.llm = LLMClient(app.state.settings)
    if not hasattr(app.state, "sensitive"):
        app.state.sensitive = load_filter(app.state.settings)
    if not hasattr(app.state, "store"):
        app.state.store = task_store
    if not hasattr(app.state, "db"):
        app.state.db = db_engine
    return app.state.llm, app.state.sensitive, app.state.store, app.state.settings


async def get_current_user(request: Request) -> User:
    """JWT 鉴权依赖（WHY：受保护路由声明 Depends(get_current_user) 即完成鉴权；失败统一 401）"""
    from sqlalchemy import select

    from app.core.config import get_settings
    from app.models.db_models import User
    from app.services.auth import AuthError, decode_token

    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "未登录"})
    try:
        user_id = decode_token(header[7:], get_settings())
    except AuthError as exc:
        raise HTTPException(status_code=401, detail={"code": exc.code, "message": exc.message})
    db_engine = request.app.state.db
    async with db_engine.maker() as db:
        user = await db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "未登录"})
    return user


async def get_optional_user(request: Request) -> User | None:
    """可选鉴权（WHY：匿名仍可生成报告（不落库）——需求文档 4.6 游客模式；带 session_id 时路由层再强校验）"""
    from sqlalchemy import select

    from app.core.config import get_settings
    from app.models.db_models import User
    from app.services.auth import AuthError, decode_token

    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    try:
        user_id = decode_token(header[7:], get_settings())
    except AuthError:
        return None  # token 无效视为游客，不阻断匿名生成报告
    db_engine = request.app.state.db
    async with db_engine.maker() as db:
        return await db.scalar(select(User).where(User.id == user_id))

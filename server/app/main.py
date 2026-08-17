"""FastAPI 应用入口：装配路由、CORS 与后台任务；启动时建表（WHY：开发期 create_all，不引入 Alembic）"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.deps import deps
from app.api.routes import auth, health, qrcode, quiz, report, user
from app.core.config import validate_settings

_logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    deps(app)  # 惰性装配（含 app.state.db）
    validate_settings(app.state.settings)  # 正式模式漏配 JWT_SECRET → 启动即失败（fail fast，防空密钥伪造 token）
    try:
        await app.state.db.create_all()
    except Exception as exc:  # MySQL 未启动时记录日志继续启动（WHY：不影响 /health 探活，DB 接口调用时再报错）
        _logger.warning("数据库建表失败，请确认 MySQL 已启动（start-docker.ps1）：%s", exc)
    yield


def create_app() -> FastAPI:
    """应用工厂（WHY：测试可创建独立实例注入 fake 依赖，避免污染全局状态）"""
    app = FastAPI(title="AI 闯关学习", lifespan=lifespan)
    # MVP 无认证，全放开 CORS（WHY：小程序无跨域限制，H5 编译版浏览器验证闭环需要）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth.router)
    app.include_router(user.router)
    app.include_router(health.router)
    app.include_router(quiz.router)
    app.include_router(report.router)
    app.include_router(qrcode.router)
    return app


app = create_app()

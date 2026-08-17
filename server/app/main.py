"""FastAPI 应用入口：装配路由与后台任务"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health, qrcode, quiz, report


def create_app() -> FastAPI:
    """应用工厂（WHY：测试可创建独立实例注入 fake 依赖，避免污染全局状态）"""
    app = FastAPI(title="AI 闯关学习")
    # MVP 无认证，全放开 CORS（WHY：小程序无跨域限制，H5 编译版浏览器验证闭环需要）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(quiz.router)
    app.include_router(report.router)
    app.include_router(qrcode.router)
    return app


app = create_app()

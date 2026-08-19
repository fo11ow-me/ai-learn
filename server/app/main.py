"""FastAPI 应用入口：装配路由、CORS 与后台任务；启动时建表（WHY：开发期 create_all，不引入 Alembic）"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.deps import deps
from app.api.routes import auth, health, knowledge_base, qrcode, quiz, report, review, user
from app.core.config import validate_settings
from app.models.db_models import User
from app.services.subscribe import due_users_for_push, send_review_reminder

_logger = logging.getLogger(__name__)


async def _push_due_reviews(app: FastAPI) -> None:
    """执行一轮复习提醒推送：扫描到期用户 → 逐用户下发（WHY：单个用户失败记日志不影响其他用户；
    send_review_reminder 内部已按配额/降级处理）"""
    db_engine = app.state.db
    async with db_engine.maker() as db:
        due = await due_users_for_push(db)
        for user_id, count in due:
            user = await db.get(User, user_id)
            if user is not None:
                await send_review_reminder(db, user, count, app.state.settings)
        await db.commit()


async def _review_push_loop(app: FastAPI) -> None:
    """每日错题复习提醒循环（WHY：应用内 asyncio 任务——项目无外部调度基础设施，沿用建表降级策略；
    扫描按 next_review_at 天然幂等（漏跑次日补扫），单实例部署下重复执行仅多耗配额不产生数据错误；
    首日启动立即补跑一次）"""
    while True:
        try:
            await _push_due_reviews(app)
        except Exception as exc:  # DB 不可用或单用户失败：记日志继续，不影响主服务
            _logger.warning("复习推送任务执行异常（跳过本轮）：%s", exc)
        now = datetime.now()
        next_run = datetime.combine(now.date() + timedelta(days=1),
                                    datetime.min.time().replace(hour=app.state.settings.review_push_hour))
        await asyncio.sleep(max((next_run - now).total_seconds(), 1))


@asynccontextmanager
async def lifespan(app: FastAPI):
    deps(app)  # 惰性装配（含 app.state.db）
    # 接入日志级别（WHY：uvicorn 不配置 root logger——无 handler 时 INFO 级应用日志被静默丢弃，
    # 「控制台信息少」的根因；LOG_LEVEL 生效是本次追踪方案的前提）
    log_level = getattr(logging, app.state.settings.log_level.upper(), logging.INFO)
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    validate_settings(app.state.settings)  # 正式模式漏配 JWT_SECRET → 启动即失败（fail fast，防空密钥伪造 token）
    # 启动摘要（脱敏，WHY：启动即核对环境配置是否如预期——哪些功能启用、密钥已配置但绝不外泄明文）
    _logger.info("启动配置：%s", app.state.settings.redacted_summary())
    try:
        await app.state.db.create_all()
    except Exception as exc:  # MySQL 未启动时记录日志继续启动（WHY：不影响 /health 探活，DB 接口调用时再报错）
        _logger.warning("数据库建表失败，请确认 MySQL 已启动（start-docker.ps1）：%s", exc)
    # 每日复习提醒定时任务（WHY：任务保存引用防 GC；首日启动补跑一次由循环内首次执行承担）
    app.state.review_push_task = asyncio.create_task(_review_push_loop(app))
    _logger.info("复习推送定时任务已注册（每日 %s 时执行，首日启动补跑）",
                 app.state.settings.review_push_hour)
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
    app.include_router(knowledge_base.router)
    app.include_router(review.router)
    return app


app = create_app()

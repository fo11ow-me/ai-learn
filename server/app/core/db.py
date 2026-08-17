"""SQLAlchemy 异步数据库接入（WHY：统一 engine/session 工厂；bind() 支持测试注入 SQLite 内存库）"""
from collections.abc import AsyncIterator
from urllib.parse import quote_plus

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """ORM 基类：全部模型继承（metadata 供 create_all 使用）"""


class DBEngine:
    """数据库连接管理：lazy 初始化 engine/sessionmaker；bind() 供测试替换"""

    def __init__(self, url: str | None = None, **engine_kwargs):
        self._url = url
        self._engine_kwargs = engine_kwargs
        self._engine = None
        self._maker = None

    def bind(self, url: str, **engine_kwargs) -> None:
        """替换绑定（WHY：测试注入 sqlite+aiosqlite 内存库，不连真实 MySQL）"""
        self._url = url
        self._engine_kwargs = engine_kwargs
        self._engine = None
        self._maker = None

    @property
    def engine(self):
        if self._engine is None:
            self._engine = create_async_engine(self._url or self._default_url(), **self._engine_kwargs)
        return self._engine

    def _default_url(self) -> str:
        """从 Settings 组装 MySQL 连接串（密码 URL 编码，防特殊字符破坏 DSN）"""
        from app.core.config import get_settings

        s = get_settings()
        return (
            f"mysql+aiomysql://{s.mysql_user}:{quote_plus(s.mysql_password)}"
            f"@{s.mysql_host}:{s.mysql_port}/{s.mysql_db}?charset=utf8mb4"
        )

    @property
    def maker(self) -> async_sessionmaker[AsyncSession]:
        if self._maker is None:
            self._maker = async_sessionmaker(self.engine, expire_on_commit=False)
        return self._maker

    async def create_all(self) -> None:
        """建表（WHY：开发期 create_all 足够；表少且稳定，不引入 Alembic）"""
        from app.models import db_models  # noqa: F401  导入即注册模型到 metadata

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def session(self) -> AsyncIterator[AsyncSession]:
        """FastAPI 依赖：每请求一个会话（WHY：统一生命周期，路由无需管理连接）"""
        async with self.maker() as session:
            yield session


db_engine = DBEngine()  # 模块级单例（deps 装配到 app.state，测试可替换）

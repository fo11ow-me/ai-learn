"""数据库接入测试（WHY：验证 ORM 模型可建表可读写；SQLite 内存库，不依赖真实 MySQL）"""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import StaticPool

from app.core.db import DBEngine
from app.models.db_models import CoinTransaction, QuizSession, User


@pytest.fixture
async def db() -> AsyncSession:
    """独立 SQLite 内存库会话（每测试独立）"""
    engine = DBEngine()
    engine.bind("sqlite+aiosqlite://", poolclass=StaticPool)
    await engine.create_all()
    async with engine.maker() as session:
        yield session


async def test_create_user(db):
    user = User(openid="mock-user")
    db.add(user)
    await db.commit()
    got = await db.scalar(select(User).where(User.openid == "mock-user"))
    assert got is not None
    assert got.nickname == "林中客"  # 默认昵称
    assert got.avatar_text == "林"   # 默认文字头像
    assert got.coins == 0


async def test_create_session(db):
    user = User(openid="u1")
    db.add(user)
    await db.flush()
    s = QuizSession(
        session_key="abc-123", user_id=user.id, content="光的波粒二象性",
        content_hash="hash", topic="光的波粒二象性", total_questions=5,
        correct_count=4, correct_rate=80, coins_delta=25, coins_counted=True,
        quiz_json={"topic": "t", "questions": []}, answers_json=[],
    )
    db.add(s)
    await db.commit()
    got = await db.scalar(select(QuizSession).where(QuizSession.session_key == "abc-123"))
    assert got is not None and got.report_json is None


async def test_unique_session_key(db):
    """同一 (user_id, session_key) 唯一约束（幂等键）"""
    user = User(openid="u1")
    db.add(user)
    await db.flush()
    for _ in range(2):
        db.add(QuizSession(session_key="k", user_id=user.id, content="c",
                           content_hash="h", topic="t", total_questions=1,
                           correct_count=1, correct_rate=100, coins_delta=10,
                           coins_counted=True, quiz_json={}, answers_json=[]))
    with pytest.raises(Exception):  # IntegrityError（SQLite 内存下为 IntegrityError）
        await db.commit()


async def test_create_coin_transaction(db):
    user = User(openid="u1")
    db.add(user)
    await db.flush()
    db.add(CoinTransaction(user_id=user.id, session_id=1, delta=10, reason="quiz_correct"))
    await db.commit()
    got = await db.scalar(select(CoinTransaction))
    assert got is not None and got.delta == 10 and got.reason == "quiz_correct"

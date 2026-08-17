"""金币结算服务测试（WHY：判分/封底/防刷/幂等的规则正确性——防作弊关键路径）"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import StaticPool

from app.core.db import DBEngine
from app.models.db_models import CoinTransaction, QuizSession, User
from app.services.coins import (
    SessionAlreadyExists,
    compute_deltas,
    grade_answers,
    settle_session,
)

QUIZ = {
    "topic": "光的波粒二象性",
    "source_summary": "光具有波动性与粒子性。",
    "questions": [
        {"id": 1, "type": "single", "question": "q1", "options": ["a", "b", "c", "d"],
         "answer": [1], "explanation": "e", "knowledge_point": "光学"},
        {"id": 2, "type": "single", "question": "q2", "options": ["a", "b", "c", "d"],
         "answer": [2], "explanation": "e", "knowledge_point": "光学"},
        {"id": 3, "type": "multiple", "question": "q3", "options": ["a", "b", "c", "d"],
         "answer": [0, 2], "explanation": "e", "knowledge_point": "光学"},
        {"id": 4, "type": "judge", "question": "q4", "options": ["正确", "错误"],
         "answer": [1], "explanation": "e", "knowledge_point": "光学"},
        {"id": 5, "type": "judge", "question": "q5", "options": ["正确", "错误"],
         "answer": [0], "explanation": "e", "knowledge_point": "光学"},
    ],
}

ALL_RIGHT = [
    {"question_id": 1, "selected": [1]},
    {"question_id": 2, "selected": [2]},
    {"question_id": 3, "selected": [0, 2]},
    {"question_id": 4, "selected": [1]},
    {"question_id": 5, "selected": [0]},
]


@pytest.fixture
async def db() -> AsyncSession:
    engine = DBEngine()
    engine.bind("sqlite+aiosqlite://", poolclass=StaticPool)
    await engine.create_all()
    async with engine.maker() as session:
        yield session


async def _user(db, coins=0, openid="u1") -> User:
    user = User(openid=openid, coins=coins)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def test_grade_answers_all_correct():
    assert grade_answers(QUIZ, ALL_RIGHT) == (5, 5)


def test_grade_answers_partial():
    answers = [
        {"question_id": 1, "selected": [0]},          # 错
        {"question_id": 2, "selected": [2]},          # 对
        {"question_id": 3, "selected": [0, 1]},       # 错（多选未全中）
        {"question_id": 4, "selected": [1]},          # 对
        {"question_id": 5, "selected": [0]},          # 对
    ]
    assert grade_answers(QUIZ, answers) == (5, 3)


def test_compute_deltas_correct_and_wrong():
    assert compute_deltas(3, 5, coins=0) == [10, 10, 10, -5, -5]


def test_compute_deltas_floor_at_zero():
    """余额不足时封底：扣至 0，不出现负余额"""
    deltas = compute_deltas(0, 3, coins=8)
    assert deltas == [-5, -3, 0]


async def test_settle_all_correct(db):
    user = await _user(db)
    session = await settle_session(db, user=user, session_key="k1",
                                   content="内容", quiz=QUIZ, answers=ALL_RIGHT)
    assert session.correct_count == 5
    assert session.coins_delta == 50
    assert session.coins_counted is True
    assert user.coins == 50
    flows = (await db.scalars(select(CoinTransaction))).all()
    assert len(flows) == 5
    assert all(f.delta == 10 and f.reason == "quiz_correct" for f in flows)


async def test_settle_wrong_deducts(db):
    user = await _user(db, coins=30)
    answers = [{"question_id": i + 1, "selected": [0]} for i in range(4)] + \
              [{"question_id": 5, "selected": [1]}]
    session = await settle_session(db, user=user, session_key="k2",
                                   content="内容", quiz=QUIZ, answers=answers)
    assert session.coins_delta == -25
    assert user.coins == 5


async def test_settle_floor_at_zero(db):
    user = await _user(db, coins=8)
    answers = [{"question_id": i + 1, "selected": [0]} for i in range(4)] + \
              [{"question_id": 5, "selected": [1]}]
    session = await settle_session(db, user=user, session_key="k3",
                                   content="内容", quiz=QUIZ, answers=answers)
    assert session.coins_delta == -8  # 8 → 0，扣 5+3，后 3 题 0
    assert user.coins == 0


async def test_settle_idempotent_same_key(db):
    """同 (user, session_key) 重复提交 → SessionAlreadyExists，不重复入账"""
    user = await _user(db)
    await settle_session(db, user=user, session_key="dup", content="内容", quiz=QUIZ, answers=ALL_RIGHT)
    with pytest.raises(SessionAlreadyExists) as exc:
        await settle_session(db, user=user, session_key="dup", content="内容", quiz=QUIZ, answers=ALL_RIGHT)
    assert exc.value.session.coins_delta == 50
    assert user.coins == 50
    assert len((await db.scalars(select(CoinTransaction))).all()) == 5


async def test_settle_antispam_24h(db):
    """同内容 24h 内二次闯关：记录保存但 coins_counted=false、coins_delta=0"""
    user = await _user(db)
    first = await settle_session(db, user=user, session_key="a1", content="同一主题",
                                 quiz=QUIZ, answers=ALL_RIGHT)
    assert first.coins_counted is True
    second = await settle_session(db, user=user, session_key="a2", content="同一主题",
                                  quiz=QUIZ, answers=ALL_RIGHT)
    assert second.coins_counted is False
    assert second.coins_delta == 0
    assert user.coins == 50  # 二次未入账


async def test_settle_antispam_expired_after_24h(db):
    """24h 后同内容重新计币"""
    user = await _user(db)
    old = await settle_session(db, user=user, session_key="b1", content="主题",
                               quiz=QUIZ, answers=ALL_RIGHT)
    old.created_at = datetime.now() - timedelta(hours=25)
    await db.commit()
    second = await settle_session(db, user=user, session_key="b2", content="主题",
                                  quiz=QUIZ, answers=ALL_RIGHT)
    assert second.coins_counted is True
    assert second.coins_delta == 50

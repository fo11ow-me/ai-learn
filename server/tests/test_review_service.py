"""错题重练服务测试：收录时机/调度状态机/重练判分的规则正确性——遗忘曲线核心逻辑。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import StaticPool

from app.core.db import DBEngine
from app.models.db_models import ReviewItem, User
from app.services.coins import SessionAlreadyExists, settle_session
from app.services.review import (
    MASTERED_STREAK,
    REVIEW_INTERVAL_DAYS,
    STATUS_MASTERED,
    STATUS_PENDING,
    is_due,
    next_plan,
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

# 第 1、3 题答错（3 对 2 错）
PART_WRONG = [
    {"question_id": 1, "selected": [0]},          # 错
    {"question_id": 2, "selected": [2]},          # 对
    {"question_id": 3, "selected": [0, 1]},       # 错（多选未全中）
    {"question_id": 4, "selected": [1]},          # 对
    {"question_id": 5, "selected": [0]},          # 对
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


async def _items(db, user_id: int) -> list[ReviewItem]:
    return list((await db.scalars(select(ReviewItem)
                                  .where(ReviewItem.user_id == user_id))).all())


async def test_settle_captures_wrong_questions(db):
    """结算含 2 答错题 → 收录 2 条错题（快照/知识点/missed_count=1/初始 1 天后复习）"""
    user = await _user(db)
    await settle_session(db, user=user, session_key="k1",
                         content="内容", quiz=QUIZ, answers=PART_WRONG)
    items = await _items(db, user.id)
    assert len(items) == 2
    wrong_ids = sorted(i.question_json["id"] for i in items)
    assert wrong_ids == [1, 3]  # 只收录答错题（快照与题序一致）
    for item in items:
        assert item.user_id == user.id
        assert item.status == "pending"
        assert item.missed_count == 1
        assert item.correct_streak == 0
        assert item.knowledge_point == "光学"
        assert item.question_type == item.question_json["type"]
        assert item.question_json["answer"] is not None  # 快照含正确答案（重练重判分用）
        assert item.question_json["explanation"]         # 快照含讲解（即时反馈用）
        # 初始复习计划 = 1 天后（允许 ±2h 时钟抖动）
        assert datetime.now() + timedelta(hours=22) < item.next_review_at <= datetime.now() + timedelta(hours=26)


async def test_settle_all_correct_no_review_items(db):
    """全对闯关不收录任何错题"""
    user = await _user(db)
    await settle_session(db, user=user, session_key="k2",
                         content="内容", quiz=QUIZ, answers=ALL_RIGHT)
    assert await _items(db, user.id) == []


async def test_settle_antispam_still_captures(db):
    """防刷闯关（coins_counted=false，同内容 24h 内）同样收录错题（答题事实真实，仅金币不计）"""
    user = await _user(db)
    await settle_session(db, user=user, session_key="a1", content="同一主题",
                         quiz=QUIZ, answers=ALL_RIGHT)  # 首次：全对、计币
    second = await settle_session(db, user=user, session_key="a2", content="同一主题",
                                  quiz=QUIZ, answers=PART_WRONG)  # 二次：防刷不计币，但答错 2 题
    assert second.coins_counted is False
    assert second.coins_delta == 0
    items = await _items(db, user.id)
    assert len(items) == 2  # 防刷闯关的答错题照常收录


async def test_settle_idempotent_does_not_duplicate(db):
    """同 (user, session_key) 幂等命中 → 不重复收录（错题状态与首次结算后一致）"""
    user = await _user(db)
    await settle_session(db, user=user, session_key="dup", content="内容",
                         quiz=QUIZ, answers=PART_WRONG)
    with pytest.raises(SessionAlreadyExists):
        await settle_session(db, user=user, session_key="dup", content="内容",
                             quiz=QUIZ, answers=PART_WRONG)
    assert len(await _items(db, user.id)) == 2


# ---------- 调度状态机（SM-2 简化） ----------


def _item(streak=0, missed=1, status=STATUS_PENDING, next_review=None) -> ReviewItem:
    return ReviewItem(
        user_id=1, session_id=1,
        question_json={"id": 1, "type": "single", "question": "q",
                       "options": ["a", "b", "c", "d"], "answer": [1],
                       "explanation": "e", "knowledge_point": "光学"},
        question_type="single", knowledge_point="光学",
        missed_count=missed, correct_streak=streak, status=status,
        next_review_at=next_review or datetime.now())


def test_next_plan_wrong_resets():
    """答错 → streak 清零、missed+1、间隔重置为 1 天"""
    now = datetime(2026, 8, 19, 9, 0, 0)
    status, streak, missed, nxt = next_plan(False, _item(streak=2, missed=2), now=now)
    assert (status, streak, missed) == (STATUS_PENDING, 0, 3)
    assert nxt == now + timedelta(days=1)


def test_next_plan_correct_increases_interval():
    """答对 → 间隔依次 2/4 天，错过次数不变"""
    now = datetime(2026, 8, 19, 9, 0, 0)
    status, streak, missed, nxt = next_plan(True, _item(streak=0, missed=1), now=now)
    assert (status, streak, missed) == (STATUS_PENDING, 1, 1)
    assert nxt == now + timedelta(days=2)

    status, streak, missed, nxt = next_plan(True, _item(streak=1, missed=1), now=now)
    assert (status, streak, missed) == (STATUS_PENDING, 2, 1)
    assert nxt == now + timedelta(days=4)


def test_next_plan_mastered_on_third_correct():
    """连续答对 3 次 → mastered（掌握时刻 = 当前时刻）"""
    now = datetime(2026, 8, 19, 9, 0, 0)
    status, streak, missed, nxt = next_plan(True, _item(streak=2), now=now)
    assert (status, streak, missed) == (STATUS_MASTERED, MASTERED_STREAK, 1)
    assert nxt == now  # mastered 时 next_review_at 记录掌握时刻（提交方据此写 mastered_at）


def test_is_due_boundary():
    """到期判定：next_review_at.date() <= 今天（今天到期算逾期，明天不算）"""
    today = datetime(2026, 8, 19, 12, 0, 0)
    assert is_due(_item(next_review=today - timedelta(days=1)), today=today) is True   # 昨天
    assert is_due(_item(next_review=today.replace(hour=9)), today=today) is True        # 今天
    assert is_due(_item(next_review=today + timedelta(days=1)), today=today) is False   # 明天


async def test_due_items_query(db):
    """due_items 只返回 pending 且到期的条目（mastered 逾期不返回）"""
    user = await _user(db)
    today = datetime.now()
    for i, (status, when) in enumerate([
        (STATUS_PENDING, today - timedelta(days=1)),   # 逾期 pending → 返回
        (STATUS_PENDING, today + timedelta(days=1)),   # 未到期 pending → 不返回
        (STATUS_MASTERED, today - timedelta(days=1)),  # 逾期 mastered → 不返回
    ]):
        db.add(ReviewItem(user_id=user.id, session_id=1, question_json=QUIZ["questions"][0],
                          question_type="single", knowledge_point="光学",
                          missed_count=1, correct_streak=0, status=status,
                          next_review_at=when, created_at=datetime.now()))
    await db.commit()
    from app.services.review import due_items
    due = await due_items(db, user.id)
    assert len(due) == 1
    assert due[0].status == STATUS_PENDING

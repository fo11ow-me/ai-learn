"""个人中心聚合服务测试（WHY：统计卡/七日柱状图/知识树/最近复盘计算正确性）"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy.pool import StaticPool

from app.core.db import DBEngine
from app.models.db_models import QuizSession, User
from app.services.stats import build_profile

RECENT = 5
TREE_LIMIT = 10


@pytest.fixture
async def db():
    engine = DBEngine()
    engine.bind("sqlite+aiosqlite://", poolclass=StaticPool)
    await engine.create_all()
    async with engine.maker() as session:
        yield session


def _session(user_id, *, topic="光的波粒二象性", total=5, correct=4, kps=("光学", "光学", "波动性"),
             days_ago=0):
    """构造一条闯关记录：quiz_json 知识点来自 kps"""
    return QuizSession(
        session_key=f"k-{topic}-{days_ago}-{total}-{correct}",
        user_id=user_id, content=topic, content_hash=f"h-{topic}-{days_ago}",
        topic=topic, total_questions=total, correct_count=correct,
        correct_rate=round(correct / total * 100), coins_delta=correct * 10,
        coins_counted=True,
        quiz_json={"topic": topic, "questions": [
            {"id": i + 1, "knowledge_point": kp} for i, kp in enumerate(kps)]},
        answers_json=[],
        created_at=datetime.now() - timedelta(days=days_ago),
    )


async def _seed(db, *sessions, coins=865, nickname="林中客"):
    user = User(openid="u1", coins=coins, nickname=nickname)
    db.add(user)
    await db.flush()
    for s in sessions:
        db.add(s)
    await db.commit()
    await db.refresh(user)
    return user


async def test_empty_profile(db):
    user = User(openid="u1")
    db.add(user)
    await db.commit()
    profile = await build_profile(db, user)
    assert profile["stats"] == {"sessions": 0, "correct_rate": 0, "knowledge_points": 0,
                                "total_correct": 0}
    assert len(profile["daily_answers"]) == 7
    assert profile["knowledge_tree"] == []
    assert profile["recent_sessions"] == []


async def test_stats_correct(db):
    user = await _seed(db, _session(user_id=1, days_ago=2), _session(user_id=1, total=5, correct=5, days_ago=1))
    profile = await build_profile(db, user)
    assert profile["stats"]["sessions"] == 2
    assert profile["stats"]["correct_rate"] == 90  # (4+5)/(5+5)
    assert profile["stats"]["knowledge_points"] == 2  # 光学、波动性
    assert profile["stats"]["total_correct"] == 9  # 4+5（原型副标题「累计答对 Y 题」）


async def test_knowledge_tree_core_flag(db):
    """出现 ≥2 次的知识点 core=true；最多 10 个"""
    user = await _seed(db, _session(user_id=1), _session(user_id=1, days_ago=1))
    tree = await build_profile(db, user)
    kp = {item["name"]: item["core"] for item in tree["knowledge_tree"]}
    assert kp["光学"] is True
    assert kp["波动性"] is True  # 单条记录内出现 1 次，两条记录共 2 次
    assert len(tree["knowledge_tree"]) <= TREE_LIMIT


async def test_daily_answers_dates(db):
    """近 7 天（含今天）按日答题数；无答题日为 0；今日在最后"""
    user = await _seed(db, _session(user_id=1, days_ago=0))
    daily = (await build_profile(db, user))["daily_answers"]
    assert len(daily) == 7
    assert daily[-1]["count"] == 5  # 今日 5 题
    assert daily[-2]["count"] == 0  # 昨日无
    assert daily[0]["date"] == (datetime.now() - timedelta(days=6)).date().isoformat()


async def test_recent_sessions_newest_first(db):
    user = await _seed(db, _session(user_id=1, topic="A", days_ago=3), _session(user_id=1, topic="B", days_ago=0))
    recent = (await build_profile(db, user))["recent_sessions"]
    assert [r["topic"] for r in recent] == ["B", "A"]  # 新的在前
    assert recent[0]["correct_rate"] == 80

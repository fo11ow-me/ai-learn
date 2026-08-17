"""个人中心聚合服务（WHY：统计/七日/知识树/最近复盘的计算集中一处；接口层只做组装，契约见方案设计文档-用户系统 5.2）"""
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import QuizSession, User

RECENT_LIMIT = 5      # 最近复盘条数（需求文档-用户系统 7 待确认#3：5 条）
TREE_LIMIT = 10       # 知识树气泡上限（需求文档-用户系统 7 待确认#2：最近 10 个）
CORE_THRESHOLD = 2    # 出现 ≥2 次为核心标签（深色样式）


async def build_profile(db: AsyncSession, user: User) -> dict:
    """组装 GET /user/me 全量响应（用户信息 + 统计 + 近七日 + 知识树 + 最近复盘）"""
    rows = (await db.execute(select(QuizSession).where(QuizSession.user_id == user.id)
                             .order_by(QuizSession.created_at.desc()))).all()
    sessions = [r[0] for r in rows]

    total_questions = sum(s.total_questions for s in sessions)
    correct_count = sum(s.correct_count for s in sessions)
    correct_rate = round(correct_count / total_questions * 100) if total_questions else 0

    # 知识树：按出现次数聚合知识点（quiz_json 内逐题 knowledge_point）
    kp_counter: Counter[str] = Counter()
    for s in sessions:
        for q in s.quiz_json.get("questions", []):
            kp = q.get("knowledge_point", "")
            if kp:
                kp_counter[kp] += 1
    tree = [{"name": name, "core": count >= CORE_THRESHOLD}
            for name, count in kp_counter.most_common(TREE_LIMIT)]

    # 近七日（含今日，今日在最后；口径为当日答题数 = total_questions 求和）
    today = datetime.now().date()
    start = datetime.combine(today - timedelta(days=6), datetime.min.time())
    rows_daily = (await db.execute(
        select(func.date(QuizSession.created_at), func.sum(QuizSession.total_questions))
        .where(QuizSession.user_id == user.id, QuizSession.created_at >= start)
        .group_by(func.date(QuizSession.created_at))
    )).all()
    counts = {str(d): int(n) for d, n in rows_daily}
    daily_answers = [
        {"date": (today - timedelta(days=i)).isoformat(),
         "count": counts.get((today - timedelta(days=i)).isoformat(), 0)}
        for i in range(6, -1, -1)
    ]

    recent = [
        {"id": s.id, "topic": s.topic, "correct_rate": s.correct_rate,
         "created_at": s.created_at.isoformat()}
        for s in sessions[:RECENT_LIMIT]
    ]

    return {
        "user": {"id": user.id, "nickname": user.nickname,
                 "avatar_text": user.avatar_text, "coins": user.coins},
        "stats": {"sessions": len(sessions), "correct_rate": correct_rate,
                  "knowledge_points": len(kp_counter), "total_correct": correct_count},
        "daily_answers": daily_answers,
        "knowledge_tree": tree,
        "recent_sessions": recent,
    }

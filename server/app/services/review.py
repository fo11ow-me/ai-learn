"""错题重练服务（WHY：遗忘曲线调度与错题聚合集中一处；纯函数便于单测穷举状态转移）

SM-2 简化规则（方案设计文档-用户系统 §5.x 契约）：错题收录即错过 1 次，初始间隔 1 天；
每次重练答对，下次间隔按序列递增（2/4/7 天），连续答对 3 次标记已掌握；重练答错则
重置回 1 天间隔且累计错过次数 +1。
"""
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import ReviewItem

REVIEW_INTERVAL_DAYS = [1, 2, 4, 7]  # 初始/递增间隔（天）；REVIEW_INTERVAL_DAYS[0] 同时是答错重置间隔
MASTERED_STREAK = 3  # 连续答对 3 次即掌握
STATUS_PENDING = "pending"
STATUS_MASTERED = "mastered"


def next_plan(correct: bool, item: ReviewItem, now: datetime | None = None) -> tuple[str, int, int, datetime]:
    """SM-2 简化状态转移纯函数：返回 (status, correct_streak, missed_count, next_review_at)。
    答错 → 间隔重置 1 天 + 错过次数 +1；答对 → 间隔按递增序列（2/4/7 天），连续 3 次掌握
    （mastered 时 next_review_at=now 记录掌握时刻，提交方据此写 mastered_at）。
    @param correct 本轮回练是否答对（服务端按快照 answer 重新判分）
    @param item    当前错题条目（只读状态）
    @param now     基准时刻（测试可注入，生产默认当前时间）
    """
    now = now or datetime.now()
    if not correct:
        return STATUS_PENDING, 0, item.missed_count + 1, now + timedelta(days=REVIEW_INTERVAL_DAYS[0])
    streak = item.correct_streak + 1
    if streak >= MASTERED_STREAK:
        return STATUS_MASTERED, streak, item.missed_count, now
    return STATUS_PENDING, streak, item.missed_count, now + timedelta(days=REVIEW_INTERVAL_DAYS[streak])


def is_due(item: ReviewItem, today: datetime | None = None) -> bool:
    """到期判定：next_review_at 所在日期 ≤ 今天（跨日即到期，服务端口径，前端只渲染）。
    @param today 基准时刻（测试可注入，生产默认当前时间）
    """
    today = today or datetime.now()
    return item.next_review_at.date() <= today.date()


async def due_items(db: AsyncSession, user_id: int) -> list[ReviewItem]:
    """查询到期错题（pending 且 next_review_at 早于明日 0 点，按到期时间升序）。
    WHY：用严格不等上界等价 next_review_at.date() <= today——日期列不加函数包裹才能走索引"""
    tomorrow = datetime.combine(date.today() + timedelta(days=1), datetime.min.time())
    rows = (await db.execute(
        select(ReviewItem).where(
            ReviewItem.user_id == user_id,
            ReviewItem.status == STATUS_PENDING,
            ReviewItem.next_review_at < tomorrow,
        ).order_by(ReviewItem.next_review_at.asc())
    )).all()
    return [r[0] for r in rows]

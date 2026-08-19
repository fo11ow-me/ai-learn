"""错题重练服务（WHY：遗忘曲线调度与错题聚合集中一处；纯函数便于单测穷举状态转移）

SM-2 简化规则（方案设计文档-用户系统 §5.x 契约）：错题收录即错过 1 次，初始间隔 1 天；
每次重练答对，下次间隔按序列递增（2/4/7 天），连续答对 3 次标记已掌握；重练答错则
重置回 1 天间隔且累计错过次数 +1。
"""
from collections import Counter
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


class ReviewSubmitError(Exception):
    """重练提交校验失败（路由层转译为 HTTP 状态码；WHY：与 coins.SessionAlreadyExists 同模式）"""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


async def submit_attempts(db: AsyncSession, user_id: int, attempts: list[dict]) -> list[dict]:
    """重练提交（单事务）：按快照 answer 重新判分（不信任前端）→ next_plan 逐条应用状态机。
    校验：item 不存在或非本人 → 404（不泄露存在性）；引用已掌握条目 → 422。
    @param attempts [{item_id, selected}]（selected 为作答选项索引，与闯关 AnswerRecord 同心智）
    @returns updated 列表 [{item_id, status, correct_streak, missed_count, next_review_at, mastered, correct}]
    """
    item_ids = [a["item_id"] for a in attempts]
    rows = (await db.execute(select(ReviewItem).where(
        ReviewItem.user_id == user_id, ReviewItem.id.in_(item_ids)))).all()
    items = {r[0].id: r[0] for r in rows}
    if len(items) != len(item_ids):
        raise ReviewSubmitError(404, "NOT_FOUND", "错题不存在")

    updated: list[dict] = []
    now = datetime.now()
    for a in attempts:
        item = items[a["item_id"]]
        if item.status == STATUS_MASTERED:
            raise ReviewSubmitError(422, "INVALID_STATE", "该错题已掌握，无需重练")
        correct = sorted(a["selected"]) == sorted(item.question_json["answer"])
        status, streak, missed, nxt = next_plan(correct, item, now=now)
        item.status, item.correct_streak, item.missed_count, item.next_review_at = \
            status, streak, missed, nxt
        if status == STATUS_MASTERED:
            item.mastered_at = now  # mastered 时 next_review_at 即掌握时刻
        updated.append({
            "item_id": item.id, "status": status, "correct_streak": streak,
            "missed_count": missed, "next_review_at": nxt.isoformat(),
            "mastered": status == STATUS_MASTERED, "correct": correct,
        })
    await db.commit()
    return updated


async def build_review_board(db: AsyncSession, user_id: int) -> dict:
    """聚合 GET /user/review 响应（WHY：列表/统计/安排一次查询组装，接口层只透传。
    due_count = 待重温数（pending 总数，与入口卡徽标/错题本统计口径一致，见 spec 统计场景；
    是否可练由到期判定单独表达——关卡加载 due_items 而非全部 pending）；
    schedule = 明日起的未来 7 天按日到期数）"""
    rows = (await db.execute(
        select(ReviewItem).where(ReviewItem.user_id == user_id)
        .order_by(ReviewItem.next_review_at.asc(), ReviewItem.id.asc())
    )).all()
    items = [r[0] for r in rows]
    pending = [i for i in items if i.status == STATUS_PENDING]
    due_count = len(pending)
    mastered_count = sum(1 for i in items if i.status == STATUS_MASTERED)

    counts: Counter[date] = Counter(i.next_review_at.date() for i in pending)
    tomorrow = date.today() + timedelta(days=1)
    schedule = [
        {"date": (tomorrow + timedelta(days=i)).isoformat(),
         "count": counts.get(tomorrow + timedelta(days=i), 0)}
        for i in range(7)
    ]

    return {
        "summary": {"due_count": due_count, "mastered_count": mastered_count},
        "items": [{
            "id": i.id, "question": i.question_json, "question_type": i.question_type,
            "knowledge_point": i.knowledge_point, "missed_count": i.missed_count,
            "correct_streak": i.correct_streak,
            "next_review_at": i.next_review_at.isoformat(), "status": i.status,
        } for i in pending],
        "schedule": schedule,
    }

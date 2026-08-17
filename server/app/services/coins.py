"""金币结算服务（WHY：判分/防刷/流水/余额在同一事务内完成——防作弊关键路径；路由层只做参数校验与幂等转译）"""
import hashlib
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import CoinTransaction, QuizSession, User

COIN_CORRECT = 10      # 答对 +10（需求文档-用户系统 4.3）
COIN_WRONG = -5        # 答错 −5
ANTISPAM_WINDOW_SECONDS = 86400  # 同内容 24h 内仅首次计币


class SessionAlreadyExists(Exception):
    """幂等命中：同 (user, session_key) 已结算（路由层转译为返回首次结果）"""

    def __init__(self, session: QuizSession):
        super().__init__("session already exists")
        self.session = session


def content_hash(content: str) -> str:
    """内容 MD5（WHY：24h 防刷判定的键；原文已落库可审计）"""
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def grade_answers(quiz: dict, answers: list[dict]) -> tuple[int, int]:
    """服务端判分：返回 (total, correct)。多选必须全中（WHY：不信任前端判分）"""
    qmap = {q["id"]: q for q in quiz["questions"]}
    total = len(quiz["questions"])
    correct = 0
    for a in answers:
        q = qmap.get(a["question_id"])
        if q is not None and sorted(a["selected"]) == sorted(q["answer"]):
            correct += 1
    return total, correct


def compute_deltas(correct: int, total: int, coins: int) -> list[int]:
    """逐题金币变动：答对 +10 / 答错 −5；余额不足时封底（扣至 0，不出现负余额），
    返回的 delta 列表顺序为先答对题后答错题（WHY：仅用于结算，无需与题序对应）"""
    wrong = total - correct
    deltas = [COIN_CORRECT] * correct
    balance = coins + correct * COIN_CORRECT
    for _ in range(wrong):
        if balance <= 0:
            deltas.append(0)
            continue
        delta = max(COIN_WRONG, -balance)
        balance += delta
        deltas.append(delta)
    return deltas


async def settle_session(db: AsyncSession, *, user: User, session_key: str, content: str,
                         quiz: dict, answers: list[dict]) -> QuizSession:
    """结算闯关：判分 → 防刷 → 逐题流水 + 余额更新（单事务）。
    幂等：同 (user, session_key) 已存在时抛 SessionAlreadyExists（WHY：网络重试不重复入账）"""
    existing = await db.scalar(select(QuizSession).where(
        QuizSession.user_id == user.id, QuizSession.session_key == session_key))
    if existing is not None:
        raise SessionAlreadyExists(existing)

    total, correct = grade_answers(quiz, answers)
    correct_rate = round(correct / total * 100) if total else 0
    c_hash = content_hash(content)
    now = datetime.now()

    prev = await db.scalar(select(QuizSession).where(
        QuizSession.user_id == user.id,
        QuizSession.content_hash == c_hash,
        QuizSession.coins_counted.is_(True),
        QuizSession.created_at >= now - timedelta(seconds=ANTISPAM_WINDOW_SECONDS),
    ).limit(1))
    counted = prev is None
    deltas = compute_deltas(correct, total, user.coins) if counted else [0] * total

    session = QuizSession(
        session_key=session_key, user_id=user.id, content=content, content_hash=c_hash,
        topic=quiz.get("topic", ""), total_questions=total, correct_count=correct,
        correct_rate=correct_rate, coins_delta=sum(deltas), coins_counted=counted,
        quiz_json=quiz, answers_json=answers, created_at=now,
    )
    db.add(session)
    await db.flush()  # 取 session.id
    if counted:
        for delta in deltas:
            if delta == 0:  # 封底为 0 的扣减无意义，不记流水
                continue
            db.add(CoinTransaction(
                user_id=user.id, session_id=session.id, delta=delta,
                reason="quiz_correct" if delta > 0 else "quiz_wrong", created_at=now))
        user.coins += sum(deltas)
    await db.commit()
    await db.refresh(session)
    return session

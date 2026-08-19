"""订阅消息推送服务（WHY：微信一次性订阅——授权一次推一条，配额落库；
开发期 AUTH_MOCK=true 或模板 ID 未配置时仅记日志不发真实消息，与 fetch_openid 的 MOCK 降级策略一致）"""
import logging
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.db_models import ReviewItem, SubscribeQuota, User
from app.services.review import STATUS_PENDING

_logger = logging.getLogger(__name__)

WECHAT_API_BASE = "https://api.weixin.qq.com/cgi-bin"


async def register_quota(db: AsyncSession, user_id: int, template_id: str) -> int:
    """登记一条推送配额（一次性订阅授权一次 +1，持久化）。
    @returns 当前剩余配额（历史累计授权 − 已消耗）
    """
    db.add(SubscribeQuota(user_id=user_id, template_id=template_id, remain=1))
    await db.commit()
    return await _quota_sum(db, user_id)


async def _quota_sum(db: AsyncSession, user_id: int) -> int:
    return int(await db.scalar(select(func.sum(SubscribeQuota.remain))
                                .where(SubscribeQuota.user_id == user_id)) or 0)


async def _consume_quota(db: AsyncSession, user_id: int) -> bool:
    """消耗一条配额（任取一条 remain>0 减 1；WHY：配额只计数，不区分授权批次）。
    @returns 是否消耗成功（False = 无剩余配额）"""
    row = await db.scalar(select(SubscribeQuota).where(
        SubscribeQuota.user_id == user_id, SubscribeQuota.remain > 0).limit(1))
    if row is None:
        return False
    row.remain -= 1
    return True


async def due_users_for_push(db: AsyncSession, today: date | None = None) -> list[tuple[int, int]]:
    """扫描有到期错题的用户并聚合条数（WHY：每日推送候选集合；按 next_review_at 上界判定，
    与 review.due_items 同一口径——日期列不加函数包裹以走索引）。
    @returns [(user_id, due_count)]，按 user_id 去重聚合"""
    today = today or date.today()
    tomorrow = datetime.combine(today + timedelta(days=1), datetime.min.time())
    rows = (await db.execute(
        select(ReviewItem.user_id, func.count(ReviewItem.id))
        .where(ReviewItem.status == STATUS_PENDING, ReviewItem.next_review_at < tomorrow)
        .group_by(ReviewItem.user_id)
    )).all()
    return [(int(uid), int(cnt)) for uid, cnt in rows]


async def _get_access_token(settings: Settings) -> str:
    """现取现用微信 access_token（WHY：正式模式每次推送前获取，避免过期缓存管理；
    失败抛异常由调用方记日志，不影响其他用户）"""
    async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
        resp = await client.get(f"{WECHAT_API_BASE}/token", params={
            "grant_type": "client_credential",
            "appid": settings.wechat_appid, "secret": settings.wechat_secret})
        resp.raise_for_status()
        data = resp.json()
        if data.get("errcode", 0) != 0:
            raise RuntimeError(f"微信 access_token 获取失败: {data.get('errmsg')}")
        return data["access_token"]


async def _send_subscribe(access_token: str, openid: str, template_id: str, due_count: int) -> None:
    """调用微信订阅消息下发（失败抛异常由调用方统一处理，配额不消耗）。
    内容：待重温题数 + 今日复习提示（页面跳错题本）"""
    async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
        resp = await client.post(
            f"{WECHAT_API_BASE}/message/subscribe/send?access_token={access_token}",
            json={
                "touser": openid,
                "template_id": template_id,
                "page": "pages/review/index",
                "data": {
                    "thing1": {"value": f"你有 {due_count} 道错题待重温"},
                    "date2": {"value": "今日"},
                },
            })
        resp.raise_for_status()
        data = resp.json()
        if data.get("errcode", 0) != 0:
            raise RuntimeError(f"订阅消息发送失败 errcode={data.get('errcode')} {data.get('errmsg')}")


async def send_review_reminder(db: AsyncSession, user: User, due_count: int,
                               settings: Settings) -> None:
    """向单用户下发复习提醒。
    - AUTH_MOCK=true 或模板 ID 未配置 → 仅记 INFO 日志，不发真实请求（开发期零外部依赖）
    - 正式模式 → 配额 ≥1 时消耗 1 条并调微信接口；失败仅记 WARNING 不消耗配额（WHY：
      失败保留配额让下次有机会重试，且不影响错题数据与重练流程）
    @param user 目标用户（取 openid）
    @param due_count 到期错题数（推送内容）
    """
    if settings.auth_mock or not settings.wechat_tmpl_review:
        _logger.info("订阅推送(MOCK 降级)：user_id=%s openid=%s due=%s 条",
                     user.id, user.openid, due_count)
        return
    if await _quota_sum(db, user.id) < 1:
        _logger.info("订阅推送跳过：user_id=%s 无剩余配额", user.id)
        return
    try:
        access_token = await _get_access_token(settings)
        await _send_subscribe(access_token, user.openid, settings.wechat_tmpl_review, due_count)
    except Exception as exc:
        _logger.warning("订阅推送失败：user_id=%s due=%s err=%s（配额保留）", user.id, due_count, exc)
        return
    await _consume_quota(db, user.id)
    await db.commit()
    _logger.info("订阅推送成功：user_id=%s due=%s 条", user.id, due_count)

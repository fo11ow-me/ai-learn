"""订阅消息服务测试：一次性授权配额登记/消耗、每日扫描聚合、MOCK 降级不发真实消息。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.db import DBEngine
from app.models.db_models import ReviewItem, SubscribeQuota, User
from app.services.review import STATUS_PENDING
from app.services.subscribe import (
    due_users_for_push,
    register_quota,
    send_review_reminder,
)


@pytest.fixture
async def db() -> AsyncSession:
    engine = DBEngine()
    engine.bind("sqlite+aiosqlite://", poolclass=StaticPool)
    await engine.create_all()
    async with engine.maker() as session:
        yield session


async def _user(db, openid="u1", coins=0) -> User:
    user = User(openid=openid, coins=coins)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _add_review(db, user_id: int, next_review_at: datetime) -> None:
    db.add(ReviewItem(
        user_id=user_id, session_id=1,
        question_json={"id": 1, "type": "single", "question": "q",
                       "options": ["a", "b", "c", "d"], "answer": [1],
                       "explanation": "e", "knowledge_point": "光学"},
        question_type="single", knowledge_point="光学",
        missed_count=1, correct_streak=0, status=STATUS_PENDING,
        next_review_at=next_review_at, created_at=datetime.now()))
    await db.commit()


async def _quota_sum(db, user_id: int) -> int:
    return int(await db.scalar(select(func.sum(SubscribeQuota.remain))
                                .where(SubscribeQuota.user_id == user_id)) or 0)


async def test_register_quota_increments(db):
    """授权一次登记一条配额 → 剩余配额累计 +1"""
    user = await _user(db)
    assert await register_quota(db, user.id, "tmpl-1") == 1
    assert await register_quota(db, user.id, "tmpl-1") == 2
    assert await _quota_sum(db, user.id) == 2


async def test_send_reminder_mock_mode_no_request(db, monkeypatch):
    """AUTH_MOCK=true → 仅记日志，不发真实微信请求、不消耗配额"""
    user = await _user(db)
    settings = Settings(deepseek_api_key="k", auth_mock=True, wechat_tmpl_review="tmpl-1")
    calls = []
    monkeypatch.setattr("app.services.subscribe._get_access_token",
                        lambda s: calls.append("token") or "tok")
    monkeypatch.setattr("app.services.subscribe._send_subscribe",
                        lambda *a, **k: calls.append("send"))
    await send_review_reminder(db, user, due_count=3, settings=settings)
    assert calls == []  # MOCK 模式不触达微信
    assert await _quota_sum(db, user.id) == 0


async def test_send_reminder_no_template_no_request(db, monkeypatch):
    """正式模式但模板 ID 未配置 → 同样仅日志不请求（配置缺失视为未启用）"""
    user = await _user(db)
    settings = Settings(deepseek_api_key="k", auth_mock=False, wechat_tmpl_review="")
    calls = []
    monkeypatch.setattr("app.services.subscribe._get_access_token",
                        lambda s: calls.append("token") or "tok")
    monkeypatch.setattr("app.services.subscribe._send_subscribe",
                        lambda *a, **k: calls.append("send"))
    await send_review_reminder(db, user, due_count=1, settings=settings)
    assert calls == []


async def test_send_reminder_real_mode_consumes_quota(db, monkeypatch):
    """正式模式 + 配额 ≥1 → 消耗 1 条配额并调微信接口"""
    user = await _user(db)
    await register_quota(db, user.id, "tmpl-1")
    settings = Settings(deepseek_api_key="k", auth_mock=False, wechat_tmpl_review="tmpl-1",
                        wechat_appid="app", wechat_secret="sec")
    sent = []

    async def _fake_token(s):
        return "tok-abc"

    async def _fake_send(token, openid, template_id, due):
        sent.append((token, openid, template_id, due))

    monkeypatch.setattr("app.services.subscribe._get_access_token", _fake_token)
    monkeypatch.setattr("app.services.subscribe._send_subscribe", _fake_send)
    await send_review_reminder(db, user, due_count=2, settings=settings)
    assert sent == [("tok-abc", "u1", "tmpl-1", 2)]
    assert await _quota_sum(db, user.id) == 0  # 消耗 1 条


async def test_send_reminder_no_quota_no_request(db, monkeypatch):
    """正式模式但配额为 0 → 不推送、不发请求"""
    user = await _user(db)
    settings = Settings(deepseek_api_key="k", auth_mock=False, wechat_tmpl_review="tmpl-1")
    calls = []
    monkeypatch.setattr("app.services.subscribe._get_access_token",
                        lambda s: calls.append("token") or "tok")
    monkeypatch.setattr("app.services.subscribe._send_subscribe",
                        lambda *a, **k: calls.append("send"))
    await send_review_reminder(db, user, due_count=1, settings=settings)
    assert calls == []


async def test_send_reminder_failure_keeps_quota(db, monkeypatch):
    """微信发送失败 → 记 WARNING、不消耗配额（下次有机会重试）"""
    user = await _user(db)
    await register_quota(db, user.id, "tmpl-1")
    settings = Settings(deepseek_api_key="k", auth_mock=False, wechat_tmpl_review="tmpl-1")

    async def _fake_token(s):
        return "tok-abc"

    async def _fail(*args, **kwargs):
        raise RuntimeError("errcode=43101")

    monkeypatch.setattr("app.services.subscribe._get_access_token", _fake_token)
    monkeypatch.setattr("app.services.subscribe._send_subscribe", _fail)
    await send_review_reminder(db, user, due_count=1, settings=settings)  # 不抛异常
    assert await _quota_sum(db, user.id) == 1  # 配额保留


async def test_due_users_for_push(db):
    """扫描聚合：只统计有到期错题的用户（未到期/mastered 不计）"""
    user_a = await _user(db, openid="a")
    user_b = await _user(db, openid="b")
    user_c = await _user(db, openid="c")
    now = datetime.now()
    await _add_review(db, user_a.id, now - timedelta(days=1))  # a：到期 1 条
    await _add_review(db, user_a.id, now + timedelta(days=1))  # a：未到期
    await _add_review(db, user_b.id, now - timedelta(days=2))  # b：到期 1 条
    await _add_review(db, user_b.id, now - timedelta(days=3))  # b：到期第 2 条
    await _add_review(db, user_c.id, now + timedelta(days=5))  # c：未到期

    result = dict(await due_users_for_push(db))
    assert result == {user_a.id: 1, user_b.id: 2}  # c 无到期错题不计入


# ---------- POST /user/subscribe API ----------


async def _login(client):
    resp = await client.post("/auth/login", json={"code": "c"})
    assert resp.status_code == 200
    data = resp.json()
    return data["token"], data["user"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_register_subscribe_api(client):
    """授权登记：配额累计并持久化；未登录 401"""
    token, _ = await _login(client)
    resp = await client.post("/user/subscribe", headers=_auth(token),
                             json={"template_id": "tmpl-api"})
    assert resp.status_code == 200
    assert resp.json()["quota"] == 1
    resp = await client.post("/user/subscribe", headers=_auth(token),
                             json={"template_id": "tmpl-api"})
    assert resp.json()["quota"] == 2  # 授权两次 → 剩余配额 2

    assert (await client.post("/user/subscribe", json={"template_id": "t"})).status_code == 401

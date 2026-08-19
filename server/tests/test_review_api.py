"""错题本接口测试（WHY：GET /user/review 契约——401/空数据/列表排序/统计/复习安排）"""
from datetime import datetime, timedelta

from app.models.db_models import ReviewItem, User
from app.services.review import STATUS_MASTERED, STATUS_PENDING


async def _login(client, code="c"):
    resp = await client.post("/auth/login", json={"code": code})
    assert resp.status_code == 200
    data = resp.json()
    return data["token"], data["user"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _wrong_answers():
    """第 1、3 题答错（其余答对）"""
    return [
        {"question_id": 1, "selected": [0]},
        {"question_id": 2, "selected": [1]},
        {"question_id": 3, "selected": [0, 1]},
        {"question_id": 4, "selected": [1]},
        {"question_id": 5, "selected": [0]},
    ]


async def test_get_review_requires_login(client):
    resp = await client.get("/user/review")
    assert resp.status_code == 401


async def test_get_review_empty_contract(client, make_valid_quiz):
    token, _ = await _login(client)
    resp = await client.get("/user/review", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"] == {"due_count": 0, "mastered_count": 0}
    assert data["items"] == []
    assert len(data["schedule"]) == 7
    assert all(item["count"] == 0 for item in data["schedule"])


async def test_get_review_list_and_stats(client, make_valid_quiz):
    """结算答错 2 题 → 列表含 2 条（按 next_review_at 升序、快照完整）、due_count=2"""
    token, _ = await _login(client)
    await client.post("/user/session", headers=_auth(token), json={
        "session_key": "a1b2c3d4-201", "content": "光的波粒二象性",
        "quiz": make_valid_quiz(), "answers": _wrong_answers()})

    resp = await client.get("/user/review", headers=_auth(token))
    data = resp.json()
    assert data["summary"]["due_count"] == 2
    assert data["summary"]["mastered_count"] == 0
    items = data["items"]
    assert len(items) == 2
    ids = sorted(i["question"]["id"] for i in items)
    assert ids == [1, 3]  # 只收录答错题
    # 到期时间升序（两题同时收录，next_review_at 相同，仍按 id 稳定排序）
    assert items[0]["next_review_at"] <= items[1]["next_review_at"]
    item = items[0]
    assert item["status"] == STATUS_PENDING
    assert item["missed_count"] == 1 and item["correct_streak"] == 0
    assert item["question_type"] == "single"
    assert item["knowledge_point"] == "光的本性"
    # 快照完整（重练前端可直接作答、服务端可重判分）
    assert item["question"]["answer"] == [1]
    assert item["question"]["explanation"]
    assert item["question"]["options"]


async def test_get_review_mastered_and_schedule(client, test_app, make_valid_quiz):
    """已掌握条目计入 mastered_count 且不进列表；schedule 按未来日期聚合到期数"""
    token, user = await _login(client)
    await client.post("/user/session", headers=_auth(token), json={
        "session_key": "a1b2c3d4-202", "content": "光的波粒二象性",
        "quiz": make_valid_quiz(), "answers": _wrong_answers()})

    db_engine = test_app.state.db
    now = datetime.now()
    async with db_engine.maker() as db:
        # 调整两条错题的到期时间：一条昨日已到期、一条明日到期（schedule 明日聚合 1 条）
        from sqlalchemy import select

        from app.models.db_models import ReviewItem
        items = (await db.scalars(select(ReviewItem).where(ReviewItem.user_id == user["id"])
                                  .order_by(ReviewItem.id.asc()))).all()
        items[0].next_review_at = now - timedelta(days=1)
        items[1].next_review_at = now + timedelta(days=1)
        # 新增一条已掌握（mastered_count 计入、不进 items）
        db.add(ReviewItem(
            user_id=user["id"], session_id=999,
            question_json=make_valid_quiz()["questions"][0],
            question_type="single", knowledge_point="光的本性",
            missed_count=1, correct_streak=3, status=STATUS_MASTERED,
            next_review_at=now - timedelta(days=1),
            mastered_at=now, created_at=now))
        await db.commit()

    resp = await client.get("/user/review", headers=_auth(token))
    data = resp.json()
    assert data["summary"]["due_count"] == 2  # 待重温 2 条（pending 总数，与徽标口径一致）
    assert data["summary"]["mastered_count"] == 1
    assert len(data["items"]) == 2  # 全部 pending 都进列表（含未到期），mastered 不进
    tomorrow = (now.date() + timedelta(days=1)).isoformat()
    schedule = {s["date"]: s["count"] for s in data["schedule"]}
    assert schedule[tomorrow] == 1  # 明日到期的 1 条聚合到对应日期
    assert sum(schedule.values()) == 1


async def test_get_review_user_isolation(client, test_app, make_valid_quiz):
    """错题按用户隔离：他人错题不进入我的列表。
    WHY：MOCK 登录所有 code 映射同一 openid，第二个用户直接落库造数据（与 test_report_persist 同模式）"""
    from app.services.auth import issue_token

    token_a, _ = await _login(client)

    db_engine = test_app.state.db
    async with db_engine.maker() as db:
        other = User(openid="other-user")
        db.add(other)
        await db.commit()
        await db.refresh(other)
        other_id = other.id
    token_b = issue_token(other_id, test_app.state.settings)

    await client.post("/user/session", headers=_auth(token_b), json={
        "session_key": "a1b2c3d4-203", "content": "光的波粒二象性",
        "quiz": make_valid_quiz(), "answers": _wrong_answers()})

    resp = await client.get("/user/review", headers=_auth(token_a))
    assert resp.json()["items"] == []
    assert resp.json()["summary"]["due_count"] == 0

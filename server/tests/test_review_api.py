"""错题本接口测试：GET /user/review 契约——401/空数据/列表排序/统计/复习安排。"""
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

    MOCK 登录所有 code 映射同一 openid，第二个用户直接落库造数据（与 test_report_persist 同模式）。
    """
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


# ---------- 重练提交（POST /user/review/submit） ----------


async def _make_wrong_items(client, token, session_key) -> list[int]:
    """结算一次答错 2 题的闯关，返回收录的错题 item_id（按 next_review_at 升序）"""
    await client.post("/user/session", headers=_auth(token), json={
        "session_key": session_key, "content": "光的波粒二象性",
        "quiz": _quiz(), "answers": _wrong_answers()})
    data = (await client.get("/user/review", headers=_auth(token))).json()
    return [i["id"] for i in data["items"]]


def _quiz():
    """与 conftest.make_valid_quiz 同构的最小题库（fixture 无法在模块级直接调用）"""
    return {
        "topic": "光的波粒二象性",
        "source_summary": "光具有波动性与粒子性。",
        "questions": [
            {"id": 1, "type": "single", "question": "q1", "options": ["a", "b", "c", "d"],
             "answer": [1], "explanation": "e", "knowledge_point": "光的本性"},
            {"id": 2, "type": "single", "question": "q2", "options": ["a", "b", "c", "d"],
             "answer": [1], "explanation": "e", "knowledge_point": "光的本性"},
            {"id": 3, "type": "multiple", "question": "q3", "options": ["a", "b", "c", "d"],
             "answer": [0, 3], "explanation": "e", "knowledge_point": "光的本性"},
            {"id": 4, "type": "judge", "question": "q4", "options": ["正确", "错误"],
             "answer": [1], "explanation": "e", "knowledge_point": "光电效应"},
            {"id": 5, "type": "judge", "question": "q5", "options": ["正确", "错误"],
             "answer": [0], "explanation": "e", "knowledge_point": "光的本性"},
        ],
    }


async def test_submit_review_updates_plan(client, make_valid_quiz):
    """重练提交：服务端按快照重判 + 状态机更新（答对递增间隔、答错重置）"""
    token, _ = await _login(client)
    await client.post("/user/session", headers=_auth(token), json={
        "session_key": "a1b2c3d4-301", "content": "光的波粒二象性",
        "quiz": make_valid_quiz(), "answers": _wrong_answers()})
    items = [i["id"] for i in (await client.get("/user/review", headers=_auth(token))).json()["items"]]

    resp = await client.post("/user/review/submit", headers=_auth(token), json={
        "attempts": [
            {"item_id": items[0], "selected": [1]},   # 与快照 answer 一致 → 答对
            {"item_id": items[1], "selected": [0, 0]},  # 与快照 answer [0,3] 不符 → 答错
        ]})
    assert resp.status_code == 200
    updated = resp.json()["updated"]
    assert len(updated) == 2
    by_id = {u["item_id"]: u for u in updated}
    first = by_id[items[0]]
    assert first["correct"] is True and first["mastered"] is False
    assert first["status"] == "pending" and first["correct_streak"] == 1
    assert first["missed_count"] == 1
    assert first["next_review_at"] > datetime.now().isoformat()  # 答对 → 间隔递增（2 天后）
    second = by_id[items[1]]
    assert second["correct"] is False and second["mastered"] is False
    assert second["status"] == "pending" and second["correct_streak"] == 0
    assert second["missed_count"] == 2  # 错过次数 +1
    assert second["next_review_at"] > datetime.now().isoformat()  # 重置 1 天后


async def test_submit_review_mastered_after_three_correct(client, make_valid_quiz):
    """同一错题连续 3 次答对 → 标记已掌握并移出待重温"""
    token, _ = await _login(client)
    await client.post("/user/session", headers=_auth(token), json={
        "session_key": "a1b2c3d4-302", "content": "光的波粒二象性",
        "quiz": make_valid_quiz(), "answers": _wrong_answers()})
    items = [i["id"] for i in (await client.get("/user/review", headers=_auth(token))).json()["items"]]
    item_id = items[0]
    for _ in range(3):
        resp = await client.post("/user/review/submit", headers=_auth(token), json={
            "attempts": [{"item_id": item_id, "selected": [1]}]})
        assert resp.status_code == 200
    last = resp.json()["updated"][0]
    assert last["mastered"] is True
    assert last["status"] == "mastered"
    data = (await client.get("/user/review", headers=_auth(token))).json()
    assert data["summary"]["mastered_count"] == 1
    assert item_id not in [i["id"] for i in data["items"]]


async def test_submit_review_not_found_404(client, make_valid_quiz):
    """item 不存在或非本人 → 404（不泄露存在性）"""
    token, _ = await _login(client)
    resp = await client.post("/user/review/submit", headers=_auth(token), json={
        "attempts": [{"item_id": 99999, "selected": [1]}]})
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "NOT_FOUND"


async def test_submit_review_mastered_item_422(client, test_app, make_valid_quiz):
    """attempts 引用已掌握条目 → 422（已关闭的错题不可重复提交）"""
    token, user = await _login(client)
    item_ids = await _make_wrong_items(client, token, "a1b2c3d4-303")
    db_engine = test_app.state.db
    from sqlalchemy import select

    from app.models.db_models import ReviewItem
    async with db_engine.maker() as db:
        item = await db.get(ReviewItem, item_ids[0])
        item.status = STATUS_MASTERED
        item.correct_streak = 3
        await db.commit()

    resp = await client.post("/user/review/submit", headers=_auth(token), json={
        "attempts": [{"item_id": item_ids[0], "selected": [1]}]})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_STATE"


async def test_submit_review_no_coins(client, make_valid_quiz):
    """重练不计金币：提交后余额不变、无金币流水产生"""
    token, _ = await _login(client)
    item_ids = await _make_wrong_items(client, token, "a1b2c3d4-304")  # 结算已 +20
    before = (await client.get("/user/me", headers=_auth(token))).json()["user"]["coins"]
    resp = await client.post("/user/review/submit", headers=_auth(token), json={
        "attempts": [{"item_id": i, "selected": [1] if i == item_ids[0] else [0]} for i in item_ids]})
    assert resp.status_code == 200
    after = (await client.get("/user/me", headers=_auth(token))).json()["user"]["coins"]
    assert after == before  # 重练不影响余额


async def test_submit_review_requires_login(client):
    resp = await client.post("/user/review/submit", json={"attempts": []})
    assert resp.status_code == 401

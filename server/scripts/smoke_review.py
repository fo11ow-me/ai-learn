"""错题重练冒烟脚本（真实 MySQL + MOCK 登录，验证 收录→列表→重练→状态机→掌握→订阅登记 全链路；
P5 验收必跑。WHY 每次跑用唯一内容——同内容 24h 防刷只防金币，错题照收，断言才精确）"""
import asyncio
import sys
import uuid

# 适配 Windows 控制台（WHY：默认 GBK 无法编码 ✓ 等 Unicode 符号，reconfigure 后按 UTF-8 输出）
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx

BASE = "http://127.0.0.1:8000"


async def main() -> None:
    # trust_env=False：绕过系统代理直连本机（WHY：Windows 系统代理开启时 httpx 默认转发
    # 127.0.0.1 请求，代理返回 502 导致冒烟假失败；本地服务验证不需要代理）
    async with httpx.AsyncClient(base_url=BASE, timeout=60, trust_env=False) as c:
        login = await c.post("/auth/login", json={"code": "smoke"})
        assert login.status_code == 200, login.text
        token = login.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        print("✓ 登录成功，用户：", login.json()["user"])

        # 构造一次含 2 道错题的闯关（q1/q2 答错 → 收录 2 条错题；topic 带随机后缀避开 24h 防刷歧义）
        topic = f"冒烟错题重练-{uuid.uuid4().hex[:6]}"
        quiz = {
            "topic": topic, "source_summary": "冒烟数据。",
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
        answers = [
            {"question_id": 1, "selected": [0]},  # 答错 → 收录
            {"question_id": 2, "selected": [0]},  # 答错 → 收录
            {"question_id": 3, "selected": [0, 2]}, {"question_id": 4, "selected": [1]},
            {"question_id": 5, "selected": [0]},
        ]
        key = uuid.uuid4().hex[:16]
        sess = await c.post("/user/session", headers=headers, json={
            "session_key": key, "content": topic, "quiz": quiz, "answers": answers})
        assert sess.status_code == 200, sess.text
        print("✓ 闯关结算（含 2 道错题）：", sess.json())

        # 列表断言：本次收录的 2 条错题在待重温列表中（按知识点「光学」+ 题干 q1/q2 识别）
        board = await c.get("/user/review", headers=headers)
        assert board.status_code == 200
        data = board.json()
        mine = [i for i in data["items"] if i["question"]["question"] in ("q1", "q2")]
        assert len(mine) == 2, f"应收录 2 条错题，实际 {len(mine)}"
        q1, q2 = mine[0], mine[1]
        print("✓ GET /user/review 收录断言：due_count =", data["summary"]["due_count"],
              "本次收录 =", len(mine), "| 复习安排 =", data["schedule"])
        assert all(i["missed_count"] == 1 and i["status"] == "pending" for i in mine)

        # 重练提交：q1 答对（streak=1 下次 2 天后）、q2 答错（missed=2 下次明日）
        sub = await c.post("/user/review/submit", headers=headers, json={"attempts": [
            {"item_id": q1["id"], "selected": [1]},
            {"item_id": q2["id"], "selected": [0]},
        ]})
        assert sub.status_code == 200, sub.text
        updated = sub.json()["updated"]
        assert len(updated) == 2
        u1 = next(u for u in updated if u["item_id"] == q1["id"])
        u2 = next(u for u in updated if u["item_id"] == q2["id"])
        assert u1["correct"] is True and u1["correct_streak"] == 1 and u1["mastered"] is False
        assert u2["correct"] is False and u2["missed_count"] == 2
        print("✓ 重练提交状态机：q1 答对", u1, "| q2 答错", u2)

        # 连续答对 3 次 → 掌握（不再进入待重温）
        for i in range(2):
            r = await c.post("/user/review/submit", headers=headers, json={"attempts": [
                {"item_id": q1["id"], "selected": [1]}]})
            assert r.status_code == 200, r.text
        u1m = r.json()["updated"][0]
        assert u1m["mastered"] is True, u1m
        print("✓ 连续答对 3 次掌握：", u1m)

        # 再查列表：q1 已掌握不在待重温，mastered_count 增加；q2 仍在（下次明日）
        board2 = (await c.get("/user/review", headers=headers)).json()
        ids = [i["id"] for i in board2["items"]]
        assert q1["id"] not in ids and q2["id"] in ids
        q2_after = next(i for i in board2["items"] if i["id"] == q2["id"])
        assert q2_after["missed_count"] == 2
        print("✓ 列表刷新：q1 已掌握移出待重温，q2 仍待重温（missed_count =", q2_after["missed_count"], "）")

        # 订阅登记（AUTH_MOCK 下推送只记日志；配额真实落库，两次授权累加）
        sub1 = await c.post("/user/subscribe", headers=headers, json={"template_id": "tmpl-smoke"})
        assert sub1.status_code == 200, sub1.text
        sub2 = await c.post("/user/subscribe", headers=headers, json={"template_id": "tmpl-smoke"})
        assert sub2.status_code == 200, sub2.text
        print("✓ 订阅登记：首次配额 =", sub1.json()["quota"], "→ 二次累加 =", sub2.json()["quota"])

        print("\n🎉 错题重练冒烟全链路通过（收录 → 列表 → 重练状态机 → 掌握 → 订阅配额）")


if __name__ == "__main__":
    asyncio.run(main())

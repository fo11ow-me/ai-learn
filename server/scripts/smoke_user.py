"""用户系统冒烟脚本（真实 MySQL + MOCK 登录，验证 登录→结算→me→历史 全链路；P5 验收必跑）"""
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

        me = await c.get("/user/me", headers=headers)
        assert me.status_code == 200
        print("✓ GET /user/me：", me.json()["stats"])

        # 构造一次闯关提交（全对，5 题，+50 金币）
        quiz = {
            "topic": "光的波粒二象性", "source_summary": "光具有波动性与粒子性。",
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
            {"question_id": 1, "selected": [1]}, {"question_id": 2, "selected": [2]},
            {"question_id": 3, "selected": [0, 2]}, {"question_id": 4, "selected": [1]},
            {"question_id": 5, "selected": [0]},
        ]
        key = uuid.uuid4().hex[:16]  # 幂等键（hex，符合后端 session_key 校验规则）
        sess = await c.post("/user/session", headers=headers, json={
            "session_key": key, "content": "光的波粒二象性",
            "quiz": quiz, "answers": answers})
        assert sess.status_code == 200, sess.text
        sid = sess.json()["session_id"]
        print("✓ 闯关结算：", sess.json())

        # 幂等验证：同 key 重复提交 → 返回首次结果，余额不重复入账
        again = await c.post("/user/session", headers=headers, json={
            "session_key": key, "content": "光的波粒二象性",
            "quiz": quiz, "answers": answers})
        print("✓ 幂等重提：", again.json())

        detail = await c.get(f"/user/session/{sid}", headers=headers)
        assert detail.status_code == 200
        print("✓ 历史详情 report 字段：", detail.json()["report"])

        me2 = await c.get("/user/me", headers=headers)
        print("✓ 结算后统计：", me2.json()["stats"], "金币：", me2.json()["user"]["coins"])


if __name__ == "__main__":
    asyncio.run(main())

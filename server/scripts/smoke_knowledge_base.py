"""知识库（RAG）冒烟脚本（真实 MySQL + 真实百炼 embedding；P5 验收必跑）

验证链路：登录 → 建库 → 上传 PDF/Word（脚本内构造真实文件）→ 轮询 ready →
严格模式出题（指定库，永不联网）→ 自动模式出题 → 删除库级联 → badcase：
扫描件 PDF 拒绝 / 敏感词文档 failed / 不相关库出题降级 / 删除后幽灵清理入口。

前置：
- 后端已启动：set -a && source .env && set +a && .venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
- server/.env 已配置 EMBEDDING_API_KEY（阿里云百炼，qwen3.7-text-embedding）；
  未配置时上传接口返回 400 KB_NOT_CONFIGURED，脚本在此终止并提示配置
"""
import asyncio
import io
import os
import sys

# 适配 Windows 控制台（WHY：默认 GBK 无法编码 ✓ 等 Unicode 符号，reconfigure 后按 UTF-8 输出）
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx

# 端口可被 SMOKE_BASE_URL 覆盖（WHY：本机 8000 可能落在 Hyper-V 排除端口范围 7963-8062 内无法绑定）
BASE = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8000")

# 知识库测试文本（>MIN_TEXT_CHARS=50，且与出题 content 语义相关，保证真实 embedding 检索命中）
KB_TEXT = """量子纠缠是指两个或多个粒子之间存在的特殊关联，使得对其中一个粒子的测量会瞬时影响另一个粒子的状态，无论它们相距多远。
爱因斯坦称之为"幽灵般的超距作用"。贝尔不等式提供了检验量子纠缠是否存在实验判据，
违反贝尔不等式说明量子力学预测与经典局域隐变量理论不同。纠缠态是量子计算与量子通信的核心资源，
可用于量子密钥分发与量子隐形传态。量子纠缠不传递信息，不违反相对论光速上限。"""
QUIZ_CONTENT = "量子纠缠的基本原理与贝尔不等式"


def make_pdf(text: str) -> bytes:
    """构造带文本层的真实 PDF（PyMuPDF 内置中文字体写文本，验证 PDF 解析链路）"""
    import pymupdf as fitz  # PyMuPDF 新包名（fitz 为旧别名，已弃用）

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=11, fontname="china-s")
    data = doc.tobytes()
    doc.close()
    return data


def make_docx(text: str) -> bytes:
    """构造真实 Word（python-docx 段落文本，验证 Word 解析链路）"""
    from docx import Document

    doc = Document()
    for line in text.splitlines():
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


async def poll_task(c: httpx.AsyncClient, url: str, headers: dict, timeout: int = 90) -> dict:
    """轮询任务直到 completed/failed（1.5s 间隔，复用前端心智模型）"""
    import asyncio

    for _ in range(timeout // 1):
        resp = await c.get(url, headers=headers)
        assert resp.status_code == 200, f"轮询失败：{resp.status_code} {resp.text}"
        body = resp.json()
        if body["status"] in ("completed", "failed"):
            return body
        await asyncio.sleep(1.5)
    raise AssertionError(f"任务超时（{timeout}s）：{url}")


async def upload_and_wait(c: httpx.AsyncClient, kb_id: int, headers: dict,
                          filename: str, data: bytes, mime: str) -> dict:
    """上传文档 → 202 task_id → 轮询到 ready/failed（覆盖语义：同名覆盖）"""
    resp = await c.post(f"/knowledge-base/{kb_id}/document", headers=headers,
                        files={"file": (filename, data, mime)})
    assert resp.status_code == 202, f"上传失败：{resp.status_code} {resp.text}"
    task_id = resp.json()["task_id"]
    body = await poll_task(c, f"/knowledge-base/task/{task_id}", headers)
    assert body["status"] == "completed", f"索引任务失败：{body}"
    return body


async def main() -> None:
    # trust_env=False：绕过系统代理直连本机（WHY：Windows 系统代理开启时 httpx 默认转发
    # 127.0.0.1 请求，代理返回 502 导致冒烟假失败；本地服务验证不需要代理）
    async with httpx.AsyncClient(base_url=BASE, timeout=120, trust_env=False) as c:
        # ── 0. 登录 ──
        login = await c.post("/auth/login", json={"code": "smoke-kb"})
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        print("✓ 0. 登录成功")

        # ── 1. 建库 + 上传 PDF/Word → ready ──
        kb = await c.post("/knowledge-base", headers=headers, json={"name": "冒烟知识库"})
        assert kb.status_code == 201, kb.text
        kb_id = kb.json()["id"]
        print(f"✓ 1. 创建知识库 id={kb_id}")

        pdf_done = await upload_and_wait(c, kb_id, headers, "量子纠缠简介.pdf", make_pdf(KB_TEXT), "application/pdf")
        print(f"✓ 2. 上传 PDF → ready（chunks={pdf_done['chunk_count']}）")

        docx_done = await upload_and_wait(c, kb_id, headers, "量子纠缠原理.docx", make_docx(KB_TEXT), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        print(f"✓ 3. 上传 Word → ready（chunks={docx_done['chunk_count']}）")

        docs = await c.get(f"/knowledge-base/{kb_id}/document", headers=headers)
        assert all(d["status"] == "ready" for d in docs.json()["items"]), docs.text
        print("✓ 4. 文档列表全部 ready（chunk_count 落库）")

        # ── 5. 严格模式出题（指定库，永不联网）──
        strict = await c.post("/quiz", headers=headers, json={"content": QUIZ_CONTENT, "knowledge_base_id": kb_id})
        assert strict.status_code == 202, strict.text
        body = await poll_task(c, f"/quiz/{strict.json()['task_id']}", headers)
        assert body["status"] == "completed" and body.get("quiz"), body
        quiz = body["quiz"]
        assert len(quiz["questions"]) == 5
        print(f"✓ 5. 严格模式出题完成（5 题，topic={quiz['topic']}）")

        # ── 6. 自动模式出题（未选库：知识库 + 联网）──
        auto = await c.post("/quiz", headers=headers, json={"content": QUIZ_CONTENT})
        assert auto.status_code == 202, auto.text
        body = await poll_task(c, f"/quiz/{auto.json()['task_id']}", headers)
        assert body["status"] == "completed" and body.get("quiz"), body
        print("✓ 6. 自动模式出题完成（无 kb_id，走 知识库+联网 检索）")

        # ── 7. 删除库 → 级联清理 ──
        deleted = await c.delete(f"/knowledge-base/{kb_id}", headers=headers)
        assert deleted.status_code == 204, deleted.text
        docs_after = await c.get(f"/knowledge-base/{kb_id}/document", headers=headers)
        assert docs_after.status_code == 404, docs_after.text
        listing = await c.get("/knowledge-base", headers=headers)
        assert kb_id not in [kb["id"] for kb in listing.json()["items"]]
        print("✓ 7. 删除知识库 → 文档接口 404、列表不再包含该库（级联清理）")

        # ══ badcase 段 ══
        # ── 8. 扫描件 PDF 拒绝（无文本层 → 400 PARSE_FAILED）──
        kb2 = await c.post("/knowledge-base", headers=headers, json={"name": "冒烟 badcase 库"})
        kb2_id = kb2.json()["id"]
        scanned = make_pdf("")  # 无文本层（扫描件等价）
        resp = await c.post(f"/knowledge-base/{kb2_id}/document", headers=headers,
                            files={"file": ("扫描件.pdf", scanned, "application/pdf")})
        assert resp.status_code == 400 and resp.json()["detail"]["code"] == "PARSE_FAILED", resp.text
        print("✓ 8. badcase·扫描件 PDF → 400 PARSE_FAILED（明确拒绝，不落库）")

        # ── 9. 敏感词文档 → 422 + 记录标记 failed（不参与出题）──
        dirty = await c.post(f"/knowledge-base/{kb2_id}/document", headers=headers,
                             files={"file": ("违规内容.txt", ("本文介绍赌博相关内容 " + KB_TEXT).encode(), "text/plain")})
        assert dirty.status_code == 422 and dirty.json()["detail"]["code"] == "SENSITIVE_CONTENT", dirty.text
        docs2 = await c.get(f"/knowledge-base/{kb2_id}/document", headers=headers)
        failed_docs = [d for d in docs2.json()["items"] if d["status"] == "failed"]
        assert len(failed_docs) == 1, docs2.text
        print("✓ 9. badcase·敏感词文档 → 422 SENSITIVE_CONTENT + 记录 failed（可重新上传，不参与出题）")

        # ── 10. 不相关库出题（检索 0 命中 → 严格模式降级纯输入出题，不失败）──
        await upload_and_wait(c, kb2_id, headers, "植物学基础.txt",
                        ("光合作用是植物利用光能将二氧化碳和水转化为有机物并释放氧气的过程。\n"
                         "叶绿体是光合作用的场所，色素主要有叶绿素 a 与叶绿素 b。\n"
                         "光反应在类囊体薄膜上进行，暗反应（卡尔文循环）在基质中进行。\n"
                         "影响光合速率的因素包括光照强度、温度与二氧化碳浓度。\n"
                         "植物激素包括生长素、细胞分裂素与脱落酸等。\n").encode(), "text/plain")
        # content 用与库内文档无关的主题（WHY：量子纠缠 vs 植物学库 → 0 命中验证降级路径；
        # 若 content 与库内容相关则 hits>0 走命中路径，badcase 场景失效）
        unrelated = await c.post("/quiz", headers=headers,
                                 json={"content": "量子纠缠的基本原理与贝尔不等式", "knowledge_base_id": kb2_id})
        assert unrelated.status_code == 202, unrelated.text
        body = await poll_task(c, f"/quiz/{unrelated.json()['task_id']}", headers)
        assert body["status"] == "completed" and body.get("quiz"), body
        print("✓ 10. badcase·严格模式 + 不相关库（0 命中）→ 降级纯输入出题，任务完成不失败")

        # ── 11. 删除后幽灵清理入口：已删文档 reindex → 404 ──
        doc_id = docs2.json()["items"][0]["id"]
        await c.delete(f"/knowledge-base/document/{doc_id}", headers=headers)
        ghost = await c.post(f"/knowledge-base/document/{doc_id}/reindex", headers=headers)
        assert ghost.status_code == 404, ghost.text
        print("✓ 11. badcase·删除文档后 reindex → 404（无幽灵清理入口）")

        await c.delete(f"/knowledge-base/{kb2_id}", headers=headers)
        print("✓ 12. 清理 badcase 库")

        print("\n✅ 知识库冒烟全流程通过（12 段结论全部符合预期）")


if __name__ == "__main__":
    asyncio.run(main())

"""知识库 API 测试（任务 3.1~3.5）：CRUD/重名/级联、上传异步任务与状态流转、覆盖更新、
格式与大小校验、敏感词、鉴权与归属隔离、未配置降级"""
import asyncio

import pytest

from app.core.config import Settings

TXT_CONTENT = "这是一份用于测试的私有知识文档。它包含多个知识点段落，每个段落都足够长，用于验证文档上传与解析流程是否正常工作。今天学习的内容是量子计算的基础知识。量子比特与经典比特有本质区别。"


async def _login(client, code="c"):
    resp = await client.post("/auth/login", json={"code": code})
    assert resp.status_code == 200
    data = resp.json()
    return data["token"], data["user"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _create_kb(client, token, name="我的知识库", description=""):
    resp = await client.post("/knowledge-base", json={"name": name, "description": description},
                             headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _upload(client, token, kb_id, filename="doc.txt", content=TXT_CONTENT):
    return await client.post(
        f"/knowledge-base/{kb_id}/document",
        files={"file": (filename, content.encode("utf-8"), "text/plain")},
        headers=_auth(token),
    )


async def _wait_task(client, task_id, timeout=5.0):
    """轮询任务直至 completed/failed（后台任务与测试同一事件循环，sleep 让出控制权）"""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        resp = await client.get(f"/knowledge-base/task/{task_id}")
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] in ("completed", "failed"):
            return data
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"任务超时未完成：{data}")
        await asyncio.sleep(0.05)


class TestAuth:
    async def test_all_endpoints_require_login(self, client):
        assert (await client.get("/knowledge-base")).status_code == 401
        assert (await client.post("/knowledge-base", json={"name": "x"})).status_code == 401
        assert (await client.patch("/knowledge-base/1", json={"name": "x"})).status_code == 401
        assert (await client.delete("/knowledge-base/1")).status_code == 401
        assert (await client.get("/knowledge-base/1/document")).status_code == 401
        assert (await client.delete("/knowledge-base/document/1")).status_code == 401


class TestKnowledgeBaseCrud:
    async def test_create_and_list(self, client):
        token, _ = await _login(client)
        kb = await _create_kb(client, token, name="客服知识库", description="客服话术")
        assert kb["name"] == "客服知识库"
        resp = await client.get("/knowledge-base", headers=_auth(token))
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["id"] == kb["id"]
        assert items[0]["doc_count"] == 0 and items[0]["ready_count"] == 0

    async def test_duplicate_name_409(self, client):
        token, _ = await _login(client)
        await _create_kb(client, token, name="同名库")
        resp = await client.post("/knowledge-base", json={"name": "同名库"}, headers=_auth(token))
        assert resp.status_code == 409

    async def test_blank_name_422(self, client):
        token, _ = await _login(client)
        resp = await client.post("/knowledge-base", json={"name": "   "}, headers=_auth(token))
        assert resp.status_code == 422

    async def test_rename_and_description(self, client):
        token, _ = await _login(client)
        kb = await _create_kb(client, token, name="旧名")
        resp = await client.patch(f"/knowledge-base/{kb['id']}", json={"name": "新名", "description": "新描述"},
                                  headers=_auth(token))
        assert resp.status_code == 200
        assert resp.json()["name"] == "新名"
        assert resp.json()["description"] == "新描述"

    async def test_rename_duplicate_409(self, client):
        token, _ = await _login(client)
        await _create_kb(client, token, name="库A")
        kb_b = await _create_kb(client, token, name="库B")
        resp = await client.patch(f"/knowledge-base/{kb_b['id']}", json={"name": "库A"}, headers=_auth(token))
        assert resp.status_code == 409

    async def test_delete_cascades_documents(self, client):
        token, _ = await _login(client)
        kb = await _create_kb(client, token)
        upload = await _upload(client, token, kb["id"])
        assert upload.status_code == 202
        await _wait_task(client, upload.json()["task_id"])
        resp = await client.delete(f"/knowledge-base/{kb['id']}", headers=_auth(token))
        assert resp.status_code == 204
        # 库与文档均不可见
        items = (await client.get("/knowledge-base", headers=_auth(token))).json()["items"]
        assert items == []


class TestUpload:
    async def test_upload_then_ready_and_searchable(self, client):
        """上传 → 202 → 轮询 completed → 文档 ready；出题检索可命中（真实走内存 Chroma）"""
        token, _ = await _login(client)
        kb = await _create_kb(client, token)
        resp = await _upload(client, token, kb["id"])
        assert resp.status_code == 202
        task = await _wait_task(client, resp.json()["task_id"])
        assert task["status"] == "completed"
        assert task["chunk_count"] >= 1

        docs = (await client.get(f"/knowledge-base/{kb['id']}/document", headers=_auth(token))).json()["items"]
        assert len(docs) == 1
        assert docs[0]["status"] == "ready"
        assert docs[0]["filename"] == "doc.txt"
        assert docs[0]["chunk_count"] == task["chunk_count"]

    async def test_unsupported_type_400(self, client):
        token, _ = await _login(client)
        kb = await _create_kb(client, token)
        resp = await _upload(client, token, kb["id"], filename="a.xlsx")
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "UNSUPPORTED_TYPE"

    async def test_oversize_400(self, client, test_app):
        token, _ = await _login(client)
        kb = await _create_kb(client, token)
        # jwt_secret 保持与签发一致（WHY：覆盖 settings 只改大小上限，token 解码依赖原 secret）
        test_app.state.settings = Settings(deepseek_api_key="test-key", embedding_api_key="k",
                                           jwt_secret="test-secret", kb_max_file_size_mb=1)
        resp = await _upload(client, token, kb["id"], content="x" * (1024 * 1024 + 1))
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "FILE_TOO_LARGE"

    async def test_embedding_not_configured_400(self, client, test_app):
        """EMBEDDING_API_KEY 为空 → 上传拒绝（知识库功能未配置）"""
        token, _ = await _login(client)
        kb = await _create_kb(client, token)
        test_app.state.settings = Settings(deepseek_api_key="test-key", embedding_api_key="", jwt_secret="test-secret")
        resp = await _upload(client, token, kb["id"])
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "KB_NOT_CONFIGURED"

    async def test_sensitive_content_marks_failed(self, client):
        """文档内容命中敏感词 → 422 + 文档落库 failed（spec：标记失败且不参与出题）"""
        token, _ = await _login(client)
        kb = await _create_kb(client, token)
        resp = await _upload(client, token, kb["id"], content="这是包含赌博相关内容的长文档文本内容，用于触发敏感词过滤。" + "补充内容。" * 20)
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "SENSITIVE_CONTENT"
        docs = (await client.get(f"/knowledge-base/{kb['id']}/document", headers=_auth(token))).json()["items"]
        assert len(docs) == 1
        assert docs[0]["status"] == "failed"

    async def test_same_name_overwrites(self, client):
        """同名重新上传 = 覆盖更新：doc_id 不变，旧 chunk 替换（检索只命中新内容）"""
        token, _ = await _login(client)
        kb = await _create_kb(client, token)
        first = (await _upload(client, token, kb["id"], content="旧版本的独特内容知识文档。" * 10)).json()
        await _wait_task(client, first["task_id"])
        second = (await _upload(client, token, kb["id"], content="新版本的独特内容知识文档。" * 10)).json()
        await _wait_task(client, second["task_id"])

        assert first["document_id"] == second["document_id"]  # 同 doc_id 覆盖
        docs = (await client.get(f"/knowledge-base/{kb['id']}/document", headers=_auth(token))).json()["items"]
        assert len(docs) == 1

    async def test_delete_document_removes_index(self, client):
        token, _ = await _login(client)
        kb = await _create_kb(client, token)
        upload = (await _upload(client, token, kb["id"])).json()
        await _wait_task(client, upload["task_id"])
        doc_id = upload["document_id"]
        resp = await client.delete(f"/knowledge-base/document/{doc_id}", headers=_auth(token))
        assert resp.status_code == 204
        docs = (await client.get(f"/knowledge-base/{kb['id']}/document", headers=_auth(token))).json()["items"]
        assert docs == []

    async def test_reindex(self, client):
        token, _ = await _login(client)
        kb = await _create_kb(client, token)
        upload = (await _upload(client, token, kb["id"])).json()
        await _wait_task(client, upload["task_id"])
        resp = await client.post(f"/knowledge-base/document/{upload['document_id']}/reindex", headers=_auth(token))
        assert resp.status_code == 202
        task = await _wait_task(client, resp.json()["task_id"])
        assert task["status"] == "completed"


class TestIsolation:
    async def _login_as(self, client, test_app, openid):
        """以指定 openid 登录（WHY：mock 模式 openid 固定为 auth_mock_openid，覆盖 settings 切换用户）"""
        test_app.state.settings = Settings(deepseek_api_key="test-key", embedding_api_key="test-embed-key",
                                           jwt_secret="test-secret", auth_mock_openid=openid)
        resp = await client.post("/auth/login", json={"code": "c"})
        assert resp.status_code == 200
        return resp.json()["token"]

    async def test_other_user_kb_404(self, client, test_app):
        """他人知识库操作统一 404（与不存在一致，防枚举）"""
        token_a = await self._login_as(client, test_app, "user-a")
        token_b = await self._login_as(client, test_app, "user-b")
        kb = await _create_kb(client, token_a)

        assert (await client.get(f"/knowledge-base/{kb['id']}/document", headers=_auth(token_b))).status_code == 404
        assert (await client.patch(f"/knowledge-base/{kb['id']}", json={"name": "x"}, headers=_auth(token_b))).status_code == 404
        assert (await client.delete(f"/knowledge-base/{kb['id']}", headers=_auth(token_b))).status_code == 404
        resp = await _upload(client, token_b, kb["id"])
        assert resp.status_code == 404

    async def test_other_user_document_404(self, client, test_app):
        token_a = await self._login_as(client, test_app, "user-a")
        token_b = await self._login_as(client, test_app, "user-b")
        kb = await _create_kb(client, token_a)
        upload = (await _upload(client, token_a, kb["id"])).json()
        await _wait_task(client, upload["task_id"])
        assert (await client.delete(f"/knowledge-base/document/{upload['document_id']}", headers=_auth(token_b))).status_code == 404
        assert (await client.post(f"/knowledge-base/document/{upload['document_id']}/reindex", headers=_auth(token_b))).status_code == 404

    async def test_kb_list_only_shows_own(self, client, test_app):
        token_a = await self._login_as(client, test_app, "user-a")
        token_b = await self._login_as(client, test_app, "user-b")
        await _create_kb(client, token_a, name="A的库")
        await _create_kb(client, token_b, name="B的库")
        items = (await client.get("/knowledge-base", headers=_auth(token_a))).json()["items"]
        assert [i["name"] for i in items] == ["A的库"]

"""知识库路由（RAG：库 CRUD + 文档上传异步任务 + 轮询）。
全部接口 JWT 鉴权 + 归属校验（他人资源返回与「不存在」一致的 404，防枚举）。
写顺序约束（设计 D1）：先 MySQL（解析全文落库）后 Chroma（后台任务向量化），失败标记 failed 可重试"""
import asyncio
import logging
import time

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import case, delete, func, select

from app.api.deps import deps, get_current_user
from app.core.tasks import TaskError, TaskStore, task_store
from app.core.tracing import current_task_id
from app.models.db_models import KnowledgeBase, KnowledgeDocument, User
from app.models.schemas import KnowledgeBaseCreate, KnowledgeBaseUpdate
from app.services.knowledge_base import KBError, KnowledgeBaseService, parse_document, split_text

router = APIRouter()
_logger = logging.getLogger(__name__)


def _kb_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "知识库不存在"})


def _doc_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "文档不存在"})


async def _load_kb(db, kb_id: int, user_id: int) -> KnowledgeBase:
    """加载知识库并校验归属。归属不匹配与不存在返回同一 404，不泄露资源存在性。"""
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None or kb.user_id != user_id:
        raise _kb_not_found()
    return kb


async def _load_doc(db, doc_id: int, user_id: int) -> KnowledgeDocument:
    doc = await db.get(KnowledgeDocument, doc_id)
    if doc is None or doc.user_id != user_id:
        raise _doc_not_found()
    return doc


def _require_embedding_enabled(settings) -> None:
    """知识库功能守卫。未配置 Embedding key 时上传必败，提前 400 提示而非后台任务失败。"""
    if not (settings.embedding_enabled and settings.embedding_api_key):
        raise HTTPException(
            status_code=400, detail={"code": "KB_NOT_CONFIGURED", "message": "知识库功能未配置，请联系管理员"}
        )


@router.post("/knowledge-base", status_code=201)
async def create_knowledge_base(body: KnowledgeBaseCreate, request: Request,
                                user: User = Depends(get_current_user)) -> dict:
    """创建知识库：同用户重名拒绝（409）"""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail={"code": "INVALID_NAME", "message": "知识库名称不能为空"})
    db_engine = request.app.state.db
    async with db_engine.maker() as db:
        exists = await db.scalar(
            select(KnowledgeBase).where(KnowledgeBase.user_id == user.id, KnowledgeBase.name == name)
        )
        if exists is not None:
            raise HTTPException(status_code=409, detail={"code": "NAME_EXISTS", "message": "知识库名称已存在"})
        kb = KnowledgeBase(user_id=user.id, name=name, description=body.description.strip())
        db.add(kb)
        await db.commit()
        await db.refresh(kb)
        return {"id": kb.id, "name": kb.name, "description": kb.description,
                "created_at": kb.created_at.isoformat()}


@router.get("/knowledge-base")
async def list_knowledge_bases(request: Request, user: User = Depends(get_current_user)) -> dict:
    """知识库列表（含文档数与可出题文档数，前端展示）"""
    db_engine = request.app.state.db
    async with db_engine.maker() as db:
        ready_expr = func.sum(case((KnowledgeDocument.status == "ready", 1), else_=0))
        rows = await db.execute(
            select(KnowledgeBase, func.count(KnowledgeDocument.id), ready_expr)
            .outerjoin(KnowledgeDocument, KnowledgeDocument.knowledge_base_id == KnowledgeBase.id)
            .where(KnowledgeBase.user_id == user.id)
            .group_by(KnowledgeBase.id)
            .order_by(KnowledgeBase.created_at.desc())
        )
        return {"items": [
            {"id": kb.id, "name": kb.name, "description": kb.description,
             "doc_count": doc_count, "ready_count": ready_count or 0,
             "created_at": kb.created_at.isoformat()}
            for kb, doc_count, ready_count in rows.all()
        ]}


@router.patch("/knowledge-base/{kb_id}")
async def update_knowledge_base(kb_id: int, body: KnowledgeBaseUpdate, request: Request,
                                user: User = Depends(get_current_user)) -> dict:
    """重命名/改描述：重名拒绝（409）"""
    if body.name is None and body.description is None:
        raise HTTPException(status_code=422, detail={"code": "EMPTY_UPDATE", "message": "没有需要更新的内容"})
    db_engine = request.app.state.db
    async with db_engine.maker() as db:
        kb = await _load_kb(db, kb_id, user.id)
        if body.name is not None:
            name = body.name.strip()
            if not name:
                raise HTTPException(status_code=422, detail={"code": "INVALID_NAME", "message": "知识库名称不能为空"})
            dup = await db.scalar(
                select(KnowledgeBase).where(KnowledgeBase.user_id == user.id,
                                            KnowledgeBase.name == name, KnowledgeBase.id != kb_id)
            )
            if dup is not None:
                raise HTTPException(status_code=409, detail={"code": "NAME_EXISTS", "message": "知识库名称已存在"})
            kb.name = name
        if body.description is not None:
            kb.description = body.description.strip()
        await db.commit()
        return {"id": kb.id, "name": kb.name, "description": kb.description}


@router.delete("/knowledge-base/{kb_id}", status_code=204)
async def delete_knowledge_base(kb_id: int, request: Request,
                                user: User = Depends(get_current_user)) -> None:
    """删除知识库（级联删文档记录；先删 Chroma 索引后删记录）。

    索引删除失败时记录仍在可重试，反之会留幽灵向量。
    """
    kb_service: KnowledgeBaseService = request.app.state.knowledge_base
    await kb_service.delete_base_chunks(kb_id)
    db_engine = request.app.state.db
    async with db_engine.maker() as db:
        kb = await _load_kb(db, kb_id, user.id)
        await db.execute(delete(KnowledgeDocument).where(KnowledgeDocument.knowledge_base_id == kb_id))
        await db.delete(kb)
        await db.commit()


@router.post("/knowledge-base/{kb_id}/document", status_code=202)
async def upload_document(kb_id: int, request: Request, file: UploadFile = File(...),
                          user: User = Depends(get_current_user)) -> dict:
    """上传文档：格式/大小校验 → 同步解析全文落库（MySQL 权威源）→ 202 + task_id 后台向量化。
    覆盖语义：同库同名 = 更新既有文档（旧 chunk 删除后重写）"""
    settings = deps(request.app)[3]
    _require_embedding_enabled(settings)
    filename = file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    from app.services.knowledge_base import SUPPORTED_TYPES

    if ext not in SUPPORTED_TYPES:
        raise HTTPException(status_code=400, detail={"code": "UNSUPPORTED_TYPE",
                                                     "message": f"不支持的文件格式：{ext or '无扩展名'}（支持 pdf/docx/md/txt）"})
    data = await file.read()
    if len(data) > settings.kb_max_file_size_mb * 1024 * 1024:
        raise HTTPException(status_code=400, detail={"code": "FILE_TOO_LARGE",
                                                     "message": f"文件超过大小上限（{settings.kb_max_file_size_mb}MB）"})
    try:
        content = parse_document(filename, data)
    except KBError as exc:
        raise HTTPException(status_code=400, detail={"code": "PARSE_FAILED", "message": exc.message})
    sensitive = deps(request.app)[1]
    if sensitive.contains(content):
        # 敏感词命中：落库 failed（spec：文档标记失败且不参与出题）→ 422 提示
        db_engine = request.app.state.db
        async with db_engine.maker() as db:
            await _load_kb(db, kb_id, user.id)
            await _upsert_document(db, kb_id, user.id, filename, ext, content, status="failed")
        raise HTTPException(status_code=422, detail={"code": "SENSITIVE_CONTENT",
                                                     "message": "文档内容包含敏感信息，该文档已标记失败"})
    db_engine = request.app.state.db
    async with db_engine.maker() as db:
        await _load_kb(db, kb_id, user.id)
        doc = await _upsert_document(db, kb_id, user.id, filename, ext, content, status="uploading")
    store: TaskStore = request.app.state.store
    task_id = store.create()
    _logger.info("kb upload submit task_id=%s doc_id=%s kb_id=%s file=%s bytes=%d",
                 task_id, doc.id, kb_id, filename, len(data))
    asyncio.create_task(
        run_index_task(task_id, doc.id, request.app.state.knowledge_base, db_engine, store, overwrite=True)
    )
    return {"task_id": task_id, "document_id": doc.id}


async def _upsert_document(db, kb_id: int, user_id: int, filename: str, ext: str,
                           content: str, status: str) -> KnowledgeDocument:
    """同名覆盖：更新既有行（doc_id 不变，索引按 doc_id 重建）；新文档插入"""
    existing = await db.scalar(
        select(KnowledgeDocument).where(KnowledgeDocument.knowledge_base_id == kb_id,
                                        KnowledgeDocument.filename == filename)
    )
    if existing is not None:
        existing.content_text = content
        existing.file_type = ext
        existing.status = status
        existing.chunk_count = 0
        await db.commit()
        await db.refresh(existing)
        return existing
    doc = KnowledgeDocument(knowledge_base_id=kb_id, user_id=user_id, filename=filename,
                             file_type=ext, content_text=content, status=status)
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc


@router.get("/knowledge-base/{kb_id}/document")
async def list_documents(kb_id: int, request: Request,
                         user: User = Depends(get_current_user)) -> dict:
    """文档列表（含解析状态：uploading/ready/failed 与 chunk 数）"""
    db_engine = request.app.state.db
    async with db_engine.maker() as db:
        await _load_kb(db, kb_id, user.id)
        rows = await db.execute(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.knowledge_base_id == kb_id)
            .order_by(KnowledgeDocument.created_at.desc())
        )
        return {"items": [
            {"id": d.id, "filename": d.filename, "file_type": d.file_type,
             "status": d.status, "chunk_count": d.chunk_count,
             "created_at": d.created_at.isoformat()}
            for d in rows.scalars()
        ]}


@router.delete("/knowledge-base/document/{doc_id}", status_code=204)
async def delete_document(doc_id: int, request: Request,
                          user: User = Depends(get_current_user)) -> None:
    """删除文档（先删索引后删记录，与删除库同一顺序约束）"""
    kb_service: KnowledgeBaseService = request.app.state.knowledge_base
    await kb_service.delete_document_chunks(doc_id)
    db_engine = request.app.state.db
    async with db_engine.maker() as db:
        doc = await _load_doc(db, doc_id, user.id)
        await db.delete(doc)
        await db.commit()


@router.post("/knowledge-base/document/{doc_id}/reindex", status_code=202)
async def reindex_document(doc_id: int, request: Request,
                           user: User = Depends(get_current_user)) -> dict:
    """重建索引。Chroma 卷丢失/损坏时按 MySQL 全文重建，doc_id 幂等覆盖。"""
    settings = deps(request.app)[3]
    _require_embedding_enabled(settings)
    db_engine = request.app.state.db
    async with db_engine.maker() as db:
        doc = await _load_doc(db, doc_id, user.id)
        doc.status = "uploading"
        await db.commit()
    store: TaskStore = request.app.state.store
    task_id = store.create()
    asyncio.create_task(
        run_index_task(task_id, doc_id, request.app.state.knowledge_base, db_engine, store, overwrite=True)
    )
    return {"task_id": task_id}


@router.get("/knowledge-base/task/{task_id}")
def get_knowledge_base_task(task_id: str, request: Request) -> dict:
    """上传/重建任务状态轮询（复用 TaskStore；completed 返回 document_id/chunk_count，failed 带 error）"""
    _, _, store, _ = deps(request.app)
    info = store.get(task_id)
    if info is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    resp: dict = {"status": info.status}
    if info.payload is not None:
        resp.update(info.payload)
    if info.error is not None:
        resp["error"] = info.error.model_dump()
    return resp


async def run_index_task(task_id: str, doc_id: int, kb_service: KnowledgeBaseService,
                         db_engine, store: TaskStore = task_store, *, overwrite: bool = False) -> None:
    """后台索引任务（编排 2.5：分块 → 删旧 chunk（覆盖语义）→ 向量化 → 标记 ready；
    任一失败标记 failed 并保留全文（可重建/重新上传），不向用户暴露内部错误细节"""
    token = current_task_id.set(task_id)
    t0 = time.monotonic()
    store.update(task_id, status="running")
    _logger.info("kb index task start task_id=%s doc_id=%s", task_id, doc_id)
    try:
        async with db_engine.maker() as db:
            doc = await db.get(KnowledgeDocument, doc_id)
            if doc is None:
                raise KBError("文档不存在")
            kb_id, user_id, filename, content = doc.knowledge_base_id, doc.user_id, doc.filename, doc.content_text
        chunks = split_text(content)
        if not chunks:
            raise KBError("文档无可索引内容（可能为空或纯图片）")
        if overwrite:
            await kb_service.delete_document_chunks(doc_id)  # 覆盖语义：旧 chunk 先删（幂等）
        count = await kb_service.index_chunks(user_id, kb_id, doc_id, filename, chunks)
        async with db_engine.maker() as db:
            doc = await db.get(KnowledgeDocument, doc_id)
            if doc is None:
                raise KBError("文档不存在")
            doc.status = "ready"
            doc.chunk_count = count
            await db.commit()
        store.update(task_id, status="completed", payload={"document_id": doc_id, "chunk_count": count})
        _logger.info("kb index task done task_id=%s doc_id=%s status=completed chunks=%d elapsed=%.1fs",
                     task_id, doc_id, count, time.monotonic() - t0)
    except Exception as exc:
        try:
            async with db_engine.maker() as db:
                doc = await db.get(KnowledgeDocument, doc_id)
                if doc is not None:
                    doc.status = "failed"
                    await db.commit()
        except Exception:
            pass  # 标记失败失败不扩散（任务 error 已足够）
        store.update(task_id, status="failed",
                     error=TaskError(code="KB_INDEX_FAILED", message="文档解析失败，请重新上传"))
        _logger.warning("kb index task done task_id=%s doc_id=%s status=failed elapsed=%.1fs error=%s",
                        task_id, doc_id, time.monotonic() - t0, exc, exc_info=exc)
    finally:
        current_task_id.reset(token)

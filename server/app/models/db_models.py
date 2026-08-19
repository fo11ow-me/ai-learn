"""ORM 模型（WHY：与 Pydantic schema 分离——ORM 面向存储，schema 面向接口/LLM 契约；表结构见方案设计文档-用户系统 4.1~4.3）"""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, JSON, SmallInteger, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class User(Base):
    """用户（含金币余额账本：方案文档 6.7 金币账本与 users 1:1，并入 coins 字段）"""

    __tablename__ = "users"

    # with_variant（WHY：SQLite 仅 INTEGER PRIMARY KEY 自增，测试内存库用 Integer；MySQL 仍为 BIGINT 自增）
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    openid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    nickname: Mapped[str] = mapped_column(String(32), nullable=False, default="林中客")
    avatar_text: Mapped[str] = mapped_column(String(4), nullable=False, default="林")
    coins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class QuizSession(Base):
    """闯关记录（含题目/作答快照与 AI 报告；session_key 为结算幂等键）"""

    __tablename__ = "quiz_sessions"
    __table_args__ = (
        UniqueConstraint("user_id", "session_key", name="uk_session"),
        Index("idx_user_created", "user_id", "created_at"),
        Index("idx_antispam", "user_id", "content_hash", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    session_key: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content: Mapped[str] = mapped_column(String(2000), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(32), nullable=False)
    topic: Mapped[str] = mapped_column(String(64), nullable=False)
    total_questions: Mapped[int] = mapped_column(Integer, nullable=False)
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False)
    correct_rate: Mapped[int] = mapped_column(Integer, nullable=False)
    coins_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    coins_counted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    quiz_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    answers_json: Mapped[list] = mapped_column(JSON, nullable=False)
    report_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class CoinTransaction(Base):
    """金币流水（逐题记录：答对 +10 / 答错 −5，封底时按实际扣减额）"""

    __tablename__ = "coin_transactions"
    # 索引名不能与 quiz_sessions.idx_user_created 同名（WHY：SQLite 测试库中索引名全库唯一，MySQL 下虽按表隔离但需兼容测试环境）
    __table_args__ = (Index("idx_tx_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    session_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class ReviewItem(Base):
    """错题条目（艾宾浩斯调度：独立状态字段落库——next_review_at 到期判定、correct_streak 连续答对、
    missed_count 累计错过（「第 N 次错过」展示）；question_json 为题目快照，重练不调 AI）"""

    __tablename__ = "review_items"
    # 索引名不能与其他表重名（WHY：SQLite 测试库中索引名全库唯一）
    __table_args__ = (Index("idx_review_user_status_next", "user_id", "status", "next_review_at"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    session_id: Mapped[int] = mapped_column(BigInteger, nullable=False)  # 来源闯关（幂等由结算幂等保证，不额外加唯一约束）
    question_json: Mapped[dict] = mapped_column(JSON, nullable=False)    # 题目快照（含 answer/explanation）
    question_type: Mapped[str] = mapped_column(String(8), nullable=False)  # single / multiple / judge
    knowledge_point: Mapped[str] = mapped_column(String(32), nullable=False)
    missed_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    correct_streak: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    next_review_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="pending")  # pending / mastered
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    mastered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SubscribeQuota(Base):
    """订阅消息配额（一次性订阅：授权一次 +1，推送一次 −1；remain=0 不推送）"""

    __tablename__ = "subscribe_quotas"
    __table_args__ = (Index("idx_quota_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    template_id: Mapped[str] = mapped_column(String(64), nullable=False)  # 订阅消息模板 ID
    remain: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class KnowledgeBase(Base):
    """知识库（文件夹，RAG：一库多文档；删除级联删文档与向量，应用层实现无外键）"""

    __tablename__ = "knowledge_bases"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uk_user_name"),
        Index("idx_kb_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class KnowledgeDocument(Base):
    """知识库文档（含解析后全文——MySQL 为权威源，Chroma 为可重建派生索引；
    同名（kb_id, filename）唯一 = 覆盖更新语义；status: uploading/ready/failed）"""

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint("knowledge_base_id", "filename", name="uk_kb_filename"),
        Index("idx_doc_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    knowledge_base_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(16), nullable=False)
    content_text: Mapped[str] = mapped_column(
        MEDIUMTEXT().with_variant(Text, "sqlite"), nullable=False  # MySQL MEDIUMTEXT(4MB) / SQLite 测试用 Text
    )
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="uploading")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

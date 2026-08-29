"""应用配置：API Key 等敏感信息集中从环境变量读取，密钥不散落代码、不入仓库。"""
import os
from dataclasses import dataclass, fields

from dotenv import load_dotenv

# server/.env 相对本文件（app/core/）为上两级目录
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))


@dataclass(frozen=True)
class Settings:
    """运行配置：全部来自环境变量，未设置时使用默认值"""

    deepseek_api_key: str
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_base_url: str = ""  # OpenAI 兼容兜底地址（可选，留空走 langchain-deepseek 官方集成）
    llm_timeout: float = 60  # LLM 单次调用超时（秒）
    task_timeout_seconds: float = 120  # 出题/报告任务整体超时（秒）
    task_ttl_seconds: float = 1800  # 内存任务保留时长（30 分钟）
    sensitive_words_file: str = ""  # 可选自定义敏感词表文件路径
    share_url: str = "https://example.com"  # 海报二维码默认指向（上线时配置为小程序 URL Link）
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "ai_learn"
    mysql_password: str = ""
    mysql_db: str = "ai_learn"
    jwt_secret: str = ""
    jwt_expire_days: int = 7
    wechat_appid: str = ""
    wechat_secret: str = ""
    auth_mock: bool = True  # 开发期跳过 code2session：无正式 AppID 也能跑通全流程
    auth_mock_openid: str = "mock-user"
    search_enabled: bool = True  # 联网搜索总开关；关闭后出题行为与接入前完全一致
    tavily_api_key: str = ""  # Tavily 搜索密钥；空则视同未启用（自动降级为一段式出题）
    search_result_max_chars: int = 4000  # 检索资料拼接上限（防止顶穿 LLM 上下文）
    embedding_enabled: bool = True  # 知识库向量化总开关；关闭后上传拒绝、出题跳过知识库段（行为等同接入前）
    embedding_model: str = "qwen3.7-text-embedding"  # 阿里云百炼文本向量模型（1024 维）
    embedding_api_key: str = ""  # 百炼 API Key（sk- 开头）；空则视同未启用知识库
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"  # OpenAI 兼容端点（可配 WorkspaceId 专属域名）
    embedding_batch_size: int = 20  # 单批向量化条数（对齐百炼批量上限）
    kb_max_file_size_mb: int = 10  # 上传文档大小上限（MB）
    kb_top_k: int = 6  # 知识库检索候选数（检索 k=2*TOP_K 后按阈值过滤）
    kb_min_score: float = 0.3  # 检索片段相似度硬过滤阈值（低于即丢弃）
    kb_chunk_size: int = 500  # 文档分块字数
    kb_chunk_overlap: int = 50  # 分块重叠字数（保跨块语境）
    kb_chroma_dir: str = ""  # Chroma 持久化目录（空则默认 server/chroma/，相对 server 工作目录；不入仓库）
    log_level: str = "INFO"  # 日志级别：INFO=事件摘要；DEBUG 输出模型调用输入输出等完整内容（含用户输入，生产慎用）
    review_interval_days: tuple[int, ...] = (1, 2, 4, 7)  # 错题复习间隔序列（天；当前状态机以代码常量 REVIEW_INTERVAL_DAYS 为权威，此配置为运维预留）
    review_push_hour: int = 9  # 每日订阅提醒执行时点（本地时区 0~23）
    wechat_tmpl_review: str = ""  # 复习提醒订阅消息模板 ID；空则视同未启用（MOCK 降级仅日志）

    @classmethod
    def from_env(cls) -> "Settings":
        """从环境变量构建配置；数值类字段解析失败时回退默认值"""

        def _float(name: str, default: float) -> float:
            raw = os.environ.get(name)
            if not raw:
                return default
            try:
                return float(raw)
            except ValueError:
                return default

        def _int(name: str, default: int) -> int:
            raw = os.environ.get(name)
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                return default

        def _int_list(name: str, default: tuple[int, ...]) -> tuple[int, ...]:
            raw = os.environ.get(name)
            if not raw:
                return default
            try:
                return tuple(int(x) for x in raw.split(",") if x.strip())
            except ValueError:
                return default

        return cls(
            deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            deepseek_model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            deepseek_base_url=os.environ.get("DEEPSEEK_BASE_URL", ""),
            llm_timeout=_float("LLM_TIMEOUT", 60),
            task_timeout_seconds=_float("TASK_TIMEOUT_SECONDS", 120),
            task_ttl_seconds=_float("TASK_TTL_SECONDS", 1800),
            sensitive_words_file=os.environ.get("SENSITIVE_WORDS_FILE", ""),
            share_url=os.environ.get("SHARE_URL", "https://example.com"),
            mysql_host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
            mysql_port=_int("MYSQL_PORT", 3306),
            mysql_user=os.environ.get("MYSQL_USER", "ai_learn"),
            mysql_password=os.environ.get("MYSQL_PASSWORD", ""),
            mysql_db=os.environ.get("MYSQL_DB", "ai_learn"),
            jwt_secret=os.environ.get("JWT_SECRET", ""),
            jwt_expire_days=_int("JWT_EXPIRE_DAYS", 7),
            wechat_appid=os.environ.get("WECHAT_APPID", ""),
            wechat_secret=os.environ.get("WECHAT_SECRET", ""),
            auth_mock=os.environ.get("AUTH_MOCK", "true").lower() != "false",
            auth_mock_openid=os.environ.get("AUTH_MOCK_OPENID", "mock-user"),
            search_enabled=os.environ.get("SEARCH_ENABLED", "true").lower() != "false",
            tavily_api_key=os.environ.get("TAVILY_API_KEY", ""),
            search_result_max_chars=_int("SEARCH_RESULT_MAX_CHARS", 4000),
            embedding_enabled=os.environ.get("EMBEDDING_ENABLED", "true").lower() != "false",
            embedding_model=os.environ.get("EMBEDDING_MODEL", "qwen3.7-text-embedding"),
            embedding_api_key=os.environ.get("EMBEDDING_API_KEY", ""),
            embedding_base_url=os.environ.get("EMBEDDING_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            embedding_batch_size=_int("EMBEDDING_BATCH_SIZE", 20),
            kb_max_file_size_mb=_int("KB_MAX_FILE_SIZE_MB", 10),
            kb_top_k=_int("KB_TOP_K", 6),
            kb_min_score=_float("KB_MIN_SCORE", 0.3),
            kb_chunk_size=_int("KB_CHUNK_SIZE", 500),
            kb_chunk_overlap=_int("KB_CHUNK_OVERLAP", 50),
            kb_chroma_dir=os.environ.get("KB_CHROMA_DIR", ""),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            review_interval_days=_int_list("REVIEW_INTERVAL_DAYS", (1, 2, 4, 7)),
            review_push_hour=_int("REVIEW_PUSH_HOUR", 9),
            wechat_tmpl_review=os.environ.get("WECHAT_TMPL_REVIEW", ""),
        )

    def redacted_summary(self) -> str:
        """启动配置摘要（脱敏）。

        精确字段名集合判定：子串匹配会误伤 jwt_expire_days 等含 hint 的普通字段；
        长度 ≤8 整体打码（前缀超过密钥长度等于完整泄露），其余字段原样。
        """
        _SECRET_FIELDS = {"deepseek_api_key", "mysql_password", "jwt_secret", "wechat_secret", "tavily_api_key", "embedding_api_key"}
        parts = []
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name in _SECRET_FIELDS and value:
                value = f"{str(value)[:8]}***" if len(str(value)) > 8 else "***"
            parts.append(f"{field.name}={value}")
        return " ".join(parts)


def get_settings() -> Settings:
    """从环境变量构建配置（每次重新读取，保证测试可用环境变量注入）"""
    return Settings.from_env()


def validate_settings(settings: Settings) -> None:
    """启动守卫。

    正式模式（auth_mock=False）下 JWT_SECRET 为空会以空密钥签发/校验 JWT，任何人均
    可伪造任意 user_id 的登录态；开发 MOCK 模式跳过 code2session，不受影响。


    @param settings 装配完成的运行配置
    @throws RuntimeError 正式模式且 JWT_SECRET 为空
    """
    if not settings.auth_mock and not settings.jwt_secret:
        raise RuntimeError("正式模式必须配置 JWT_SECRET（当前为空，空密钥签发的 JWT 可被任意伪造，拒绝启动）")

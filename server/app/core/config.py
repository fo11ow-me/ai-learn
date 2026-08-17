"""应用配置（WHY：API Key 等敏感信息集中从环境变量读取，密钥不散落代码、不入仓库）"""
import os
from dataclasses import dataclass

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
    auth_mock: bool = True  # 开发期跳过 code2session（WHY：无正式 AppID 也能跑通全流程）
    auth_mock_openid: str = "mock-user"

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
        )


def get_settings() -> Settings:
    """从环境变量构建配置（每次重新读取，保证测试可用环境变量注入）"""
    return Settings.from_env()


def validate_settings(settings: Settings) -> None:
    """启动守卫（WHY：正式模式（auth_mock=False）下 JWT_SECRET 为空会以空密钥签发/校验 JWT，
    任何人均可伪造任意 user_id 的登录态；开发 MOCK 模式跳过 code2session，不受影响）

    @param settings 装配完成的运行配置
    @throws RuntimeError 正式模式且 JWT_SECRET 为空
    """
    if not settings.auth_mock and not settings.jwt_secret:
        raise RuntimeError("正式模式必须配置 JWT_SECRET（当前为空，空密钥签发的 JWT 可被任意伪造，拒绝启动）")

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

        return cls(
            deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            deepseek_model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            deepseek_base_url=os.environ.get("DEEPSEEK_BASE_URL", ""),
            llm_timeout=_float("LLM_TIMEOUT", 60),
            task_timeout_seconds=_float("TASK_TIMEOUT_SECONDS", 120),
            task_ttl_seconds=_float("TASK_TTL_SECONDS", 1800),
            sensitive_words_file=os.environ.get("SENSITIVE_WORDS_FILE", ""),
            share_url=os.environ.get("SHARE_URL", "https://example.com"),
        )


def get_settings() -> Settings:
    """从环境变量构建配置（每次重新读取，保证测试可用环境变量注入）"""
    return Settings.from_env()

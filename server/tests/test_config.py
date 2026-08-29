"""配置启动守卫测试：正式模式漏配 JWT_SECRET 须 fail fast，防空密钥签发可被任意伪造的 JWT。"""
import pytest

from app.core.config import Settings, validate_settings


def test_search_settings_defaults():
    """联网搜索三项配置默认值：开关开、key 空、资料上限 4000 字"""
    settings = Settings(deepseek_api_key="test")
    assert settings.search_enabled is True
    assert settings.tavily_api_key == ""
    assert settings.search_result_max_chars == 4000


def test_search_settings_from_env(monkeypatch):
    """环境变量注入：SEARCH_ENABLED=false 生效、TAVILY_API_KEY 与长度上限读入"""
    monkeypatch.setenv("SEARCH_ENABLED", "false")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    monkeypatch.setenv("SEARCH_RESULT_MAX_CHARS", "6000")
    settings = Settings.from_env()
    assert settings.search_enabled is False
    assert settings.tavily_api_key == "tvly-test-key"
    assert settings.search_result_max_chars == 6000


def test_validate_settings_rejects_empty_jwt_secret_in_production():
    """auth_mock=False + jwt_secret="" → 抛 RuntimeError（正式模式拒绝以空密钥启动）"""
    settings = Settings(deepseek_api_key="test", jwt_secret="", auth_mock=False)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        validate_settings(settings)


def test_validate_settings_allows_empty_jwt_secret_in_mock_mode():
    """auth_mock=True + jwt_secret="" → 不抛（开发态跳过 code2session，无真实 JWT 签发风险）"""
    settings = Settings(deepseek_api_key="test", jwt_secret="", auth_mock=True)
    validate_settings(settings)


def test_validate_settings_allows_nonempty_jwt_secret_in_production():
    """auth_mock=False + jwt_secret 非空 → 不抛"""
    settings = Settings(deepseek_api_key="test", jwt_secret="secret", auth_mock=False)
    validate_settings(settings)


def test_log_level_default_info():
    settings = Settings(deepseek_api_key="test")
    assert settings.log_level == "INFO"


def test_log_level_from_env(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    assert Settings.from_env().log_level == "DEBUG"


def test_review_settings_defaults():
    """错题重练配置默认值：间隔序列 1/2/4/7、每日推送 9 时、模板 ID 空（降级）"""
    settings = Settings(deepseek_api_key="test")
    assert settings.review_interval_days == (1, 2, 4, 7)
    assert settings.review_push_hour == 9
    assert settings.wechat_tmpl_review == ""


def test_review_settings_from_env(monkeypatch):
    """环境变量注入：间隔序列解析为元组、非法值回退默认、推送时点/模板读入"""
    monkeypatch.setenv("REVIEW_INTERVAL_DAYS", "2,3,5")
    monkeypatch.setenv("REVIEW_PUSH_HOUR", "20")
    monkeypatch.setenv("WECHAT_TMPL_REVIEW", "tmpl-abc")
    settings = Settings.from_env()
    assert settings.review_interval_days == (2, 3, 5)
    assert settings.review_push_hour == 20
    assert settings.wechat_tmpl_review == "tmpl-abc"

    monkeypatch.setenv("REVIEW_INTERVAL_DAYS", "1,x,3")  # 含非法值 → 整体回退默认
    assert Settings.from_env().review_interval_days == (1, 2, 4, 7)


def test_embedding_settings_defaults():
    """知识库 RAG 配置默认值：开关开、key 空、模型/端点/批次/分块参数"""
    settings = Settings(deepseek_api_key="test")
    assert settings.embedding_enabled is True
    assert settings.embedding_api_key == ""
    assert settings.embedding_model == "qwen3.7-text-embedding"
    assert settings.embedding_base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert settings.embedding_batch_size == 20
    assert settings.kb_max_file_size_mb == 10
    assert settings.kb_top_k == 6
    assert settings.kb_min_score == 0.3
    assert settings.kb_chunk_size == 500
    assert settings.kb_chunk_overlap == 50


def test_embedding_settings_from_env(monkeypatch):
    """环境变量注入：开关关闭、key、专属端点、自定义参数全部读入"""
    monkeypatch.setenv("EMBEDDING_ENABLED", "false")
    monkeypatch.setenv("EMBEDDING_API_KEY", "sk-ws-test")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://ws1.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "10")
    monkeypatch.setenv("KB_MIN_SCORE", "0.45")
    settings = Settings.from_env()
    assert settings.embedding_enabled is False
    assert settings.embedding_api_key == "sk-ws-test"
    assert settings.embedding_base_url.startswith("https://ws1.")
    assert settings.embedding_batch_size == 10
    assert settings.kb_min_score == 0.45


def test_redacted_summary_hides_secrets():
    settings = Settings(
        deepseek_api_key="abcdefgh12345678",
        tavily_api_key="tvly-abcdefgh",
        jwt_secret="secret123",
        mysql_password="pw123456",
        embedding_api_key="sk-ws-abcdefgh",
        deepseek_model="deepseek-v4-flash",
    )
    summary = settings.redacted_summary()
    assert "abcdefgh***" in summary  # 保留前缀
    assert "tvly-abc***" in summary
    assert "sk-ws-ab***" in summary  # embedding 密钥同样脱敏（超 8 字符截前 8 + 打码）
    assert "12345678" not in summary  # 任何明文密钥片段不出现
    assert "secret123" not in summary
    assert "pw123456" not in summary
    assert "sk-ws-abcdefgh" not in summary
    assert "deepseek_model=deepseek-v4-flash" in summary  # 非敏感字段原样
    assert "jwt_expire_days=7" in summary  # 含 jwt 子串的普通字段不误伤

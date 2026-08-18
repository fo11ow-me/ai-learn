"""配置启动守卫测试（WHY：正式模式漏配 JWT_SECRET 须 fail fast，防空密钥签发可被任意伪造的 JWT）"""
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


def test_redacted_summary_hides_secrets():
    settings = Settings(
        deepseek_api_key="abcdefgh12345678",
        tavily_api_key="tvly-abcdefgh",
        jwt_secret="secret123",
        mysql_password="pw123456",
        deepseek_model="deepseek-v4-flash",
    )
    summary = settings.redacted_summary()
    assert "abcdefgh***" in summary  # 保留前缀
    assert "tvly-abc***" in summary
    assert "12345678" not in summary  # 任何明文密钥片段不出现
    assert "secret123" not in summary
    assert "pw123456" not in summary
    assert "deepseek_model=deepseek-v4-flash" in summary  # 非敏感字段原样
    assert "jwt_expire_days=7" in summary  # 含 jwt 子串的普通字段不误伤

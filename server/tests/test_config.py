"""配置启动守卫测试（WHY：正式模式漏配 JWT_SECRET 须 fail fast，防空密钥签发可被任意伪造的 JWT）"""
import pytest

from app.core.config import Settings, validate_settings


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

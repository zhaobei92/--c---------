"""P0-10 生产环境启动拦截测试(审查要求)。

env=prod 时必须拒绝启动/拒绝响应:
  * 默认 JWT secret(dev-secret-change-me);
  * 默认管理后台 token(dev-admin);
  * 邮箱验证码不得出现在 API 响应中(dev_code 仅限非 prod);
  * 未配置邮件服务时拒绝启动。
"""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import state
from app.core.config import Settings, settings, validate_production_settings
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_state():
    state.reset()
    yield


def _prod(**overrides):
    base = dict(
        env="prod",
        jwt_secret="a-real-secret-with-enough-entropy-0123456789",
        admin_token="a-real-admin-token-0123456789",
        email_provider_configured=True,
    )
    base.update(overrides)
    return Settings(**base)


def test_prod_ok_with_proper_config():
    validate_production_settings(_prod())  # 不抛异常


def test_prod_rejects_default_jwt_secret():
    with pytest.raises(RuntimeError, match="jwt_secret"):
        validate_production_settings(_prod(jwt_secret="dev-secret-change-me"))


def test_prod_rejects_default_admin_token():
    with pytest.raises(RuntimeError, match="admin_token"):
        validate_production_settings(_prod(admin_token="dev-admin"))


def test_prod_rejects_missing_email_provider():
    with pytest.raises(RuntimeError, match="email"):
        validate_production_settings(_prod(email_provider_configured=False))


def test_dev_env_skips_guards():
    validate_production_settings(Settings(env="dev"))  # dev 默认配置放行


def test_dev_code_not_returned_in_prod(monkeypatch):
    monkeypatch.setattr(settings, "env", "prod")
    resp = client.post("/v1/auth/email/code", json={"email": "x@test.com"}).json()
    assert "dev_code" not in resp
    # 验证码仍然生成并可通过内部通道验证(邮件服务发送)
    assert state.email_codes.get("x@test.com")


def test_dev_code_returned_in_dev():
    resp = client.post("/v1/auth/email/code", json={"email": "x@test.com"}).json()
    assert resp["dev_code"] == state.email_codes["x@test.com"]

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
        smtp_host="smtp.example.com",
        smtp_from="noreply@example.com",
        storage_backend="postgres",
        queue_backend="redis",
        object_backend="s3",
        code_store_backend="redis",
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
    # 布尔标记不算配置:必须有真实 SMTP 主机与发件人,缺一拒绝启动
    with pytest.raises(RuntimeError, match="smtp"):
        validate_production_settings(_prod(smtp_host=""))
    with pytest.raises(RuntimeError, match="smtp"):
        validate_production_settings(_prod(smtp_from=""))


def test_prod_rejects_memory_backends():
    # staging/prod 禁止任何 memory 后端(审查 Phase 2.1 规则)
    with pytest.raises(RuntimeError, match="storage_backend=memory"):
        validate_production_settings(_prod(storage_backend="memory"))
    with pytest.raises(RuntimeError, match="queue_backend=memory"):
        validate_production_settings(_prod(queue_backend="memory"))
    with pytest.raises(RuntimeError, match="object_backend=memory"):
        validate_production_settings(_prod(object_backend="memory"))
    with pytest.raises(RuntimeError, match="code_store_backend=memory"):
        validate_production_settings(_prod(code_store_backend="memory"))


def test_dev_env_skips_guards():
    validate_production_settings(Settings(env="dev"))  # dev 默认配置放行


class _CapturingProvider:
    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to, subject, body):
        self.sent.append((to, subject, body))


def test_dev_code_not_returned_in_prod_and_email_actually_sent(monkeypatch):
    from app.api import auth as auth_module
    provider = _CapturingProvider()
    monkeypatch.setattr(settings, "env", "prod")
    monkeypatch.setattr(auth_module, "get_email_provider", lambda _s: provider)
    resp = client.post("/v1/auth/email/code", json={"email": "x@test.com"}).json()
    assert "dev_code" not in resp
    # 验证码通过邮件 Provider 真实发出(而非仅返回 sent:true)
    assert len(provider.sent) == 1
    assert provider.sent[0][0] == "x@test.com"
    assert state.email_codes.get("x@test.com")


def test_dev_code_returned_in_dev():
    import hashlib
    resp = client.post("/v1/auth/email/code", json={"email": "x@test.com"}).json()
    stored = state.email_codes["x@test.com"]["code_hash"]
    assert hashlib.sha256(resp["dev_code"].encode()).hexdigest() == stored

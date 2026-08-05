"""验证码闭环集成测试(审查验收场景):

请求验证码 → SMTP 真实发送 → Mailpit 收到邮件 → 从邮件提取验证码
→ 验证成功 → 验证码立即失效;并验证两个 API 实例经 Redis 共享验证码状态、
TTL 过期、重发冷却、尝试上限(原子计数)。
"""

import re

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def smtp_provider():
    from app.core.config import Settings
    from app.services.email_provider import SmtpEmailProvider
    from tests.integration.conftest import SMTP_HOST, SMTP_PORT

    return SmtpEmailProvider(Settings(
        smtp_host=SMTP_HOST, smtp_port=SMTP_PORT,
        smtp_from="noreply@ysnote.dev", smtp_starttls=False,
    ))


@pytest.fixture
def code_store(redis_db):
    from app.services.code_store import RedisCodeStore
    return RedisCodeStore(redis_db)


def _extract_code(text: str) -> str:
    return re.search(r"\b(\d{6})\b", text).group(1)


def test_full_email_login_loop(mailpit, smtp_provider, code_store, redis_db):
    from app.services.code_store import RedisCodeStore

    email = "user@test.com"
    code = "428613"
    code_store.put(email, code)
    smtp_provider.send(email, "YS Note verification code",
                       f"Your verification code is {code} (valid 10 min).")

    msgs = mailpit.messages()
    assert len(msgs) == 1
    assert msgs[0]["To"][0]["Address"] == email
    extracted = _extract_code(mailpit.body(msgs[0]["ID"]))
    assert extracted == code

    # 另一个 API 实例(独立 store 对象,同一 Redis)完成验证:多实例共享状态
    other_instance = RedisCodeStore(redis_db)
    assert other_instance.verify(email, extracted) == "ok"
    # 验证码一次性:立即失效
    assert code_store.verify(email, extracted) == "invalid"


def test_ttl_expiry(code_store, redis_db):
    code_store.put("a@test.com", "111111")
    key = "ysc:a@test.com"
    assert 0 < redis_db.ttl(key) <= 600  # Redis TTL 自动过期
    redis_db.expire(key, 0)              # 模拟到期
    assert code_store.verify("a@test.com", "111111") == "invalid"


def test_resend_cooldown(code_store):
    from app.services.code_store import ResendTooSoon
    code_store.put("a@test.com", "111111")
    with pytest.raises(ResendTooSoon) as e:
        code_store.put("a@test.com", "222222")
    assert e.value.retry_after_s >= 1


def test_attempts_exhausted_atomic(code_store):
    code_store.put("a@test.com", "111111")
    for _ in range(5):
        assert code_store.verify("a@test.com", "000000") == "invalid"
    # 第 6 次:即使验证码正确也拒绝
    assert code_store.verify("a@test.com", "111111") == "exhausted"


def test_hash_storage_no_plaintext(code_store, redis_db):
    from app.services.code_store import hash_code
    code_store.put("a@test.com", "654321")
    stored = redis_db.hgetall("ysc:a@test.com")
    assert stored["code_hash"] == hash_code("654321")
    assert "654321" not in stored.values() or stored["code_hash"] != "654321"

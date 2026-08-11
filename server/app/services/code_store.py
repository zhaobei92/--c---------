"""验证码存储抽象:单实例内存(dev)与多实例共享 Redis(prod/staging)。

统一语义(与 tests/test_auth_codes.py 锁定的契约一致):
  * 只存 SHA-256 Hash,不存明文;
  * 有效期 CODE_TTL;重发冷却 RESEND_COOLDOWN;尝试上限 MAX_ATTEMPTS;
  * Redis 实现依赖 TTL 自动过期,尝试计数用 HINCRBY(原子)。
"""

from __future__ import annotations

import hashlib
import time
from typing import Protocol

CODE_TTL_SECONDS = 600
RESEND_COOLDOWN_SECONDS = 60
MAX_VERIFY_ATTEMPTS = 5


def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


class CodeStore(Protocol):
    def put(self, email: str, code: str) -> None:
        """存码;冷却期内重发抛 ResendTooSoon。"""
        ...

    def verify(self, email: str, code: str) -> str:
        """返回 'ok';失败返回 'invalid' / 'expired' / 'exhausted'。成功即删码。"""
        ...


class ResendTooSoon(Exception):
    def __init__(self, retry_after_s: int):
        self.retry_after_s = retry_after_s
        super().__init__(f"resend in {retry_after_s}s")


class MemoryCodeStore:
    """单实例内存实现(dev / 单元测试)。"""

    def __init__(self) -> None:
        self.codes: dict[str, dict] = {}

    def put(self, email: str, code: str) -> None:
        now = time.time()
        existing = self.codes.get(email)
        if existing and now - existing["last_sent"] < RESEND_COOLDOWN_SECONDS:
            raise ResendTooSoon(
                int(RESEND_COOLDOWN_SECONDS - (now - existing["last_sent"])))
        self.codes[email] = {
            "code_hash": hash_code(code),
            "expires_at": now + CODE_TTL_SECONDS,
            "attempts": 0,
            "last_sent": now,
        }

    def verify(self, email: str, code: str) -> str:
        entry = self.codes.get(email)
        if entry is None:
            return "invalid"
        if entry["attempts"] >= MAX_VERIFY_ATTEMPTS:
            return "exhausted"
        if time.time() > entry["expires_at"]:
            del self.codes[email]
            return "expired"
        if hash_code(code) != entry["code_hash"]:
            entry["attempts"] += 1
            return "invalid"
        del self.codes[email]
        return "ok"


class RedisCodeStore:
    """多实例共享实现:两个 API 实例经同一 Redis 共享验证码状态。

    key ysc:{email} = HASH{code_hash, attempts},TTL = CODE_TTL;
    key ysc:{email}:cooldown,TTL = RESEND_COOLDOWN。
    """

    def __init__(self, redis_client, prefix: str = "ysc"):
        self.r = redis_client
        self.prefix = prefix

    def _key(self, email: str) -> str:
        return f"{self.prefix}:{email}"

    def put(self, email: str, code: str) -> None:
        cooldown_key = self._key(email) + ":cooldown"
        # SET NX EX:原子占用冷却窗口
        if not self.r.set(cooldown_key, "1", nx=True, ex=RESEND_COOLDOWN_SECONDS):
            ttl = self.r.ttl(cooldown_key)
            raise ResendTooSoon(max(1, ttl))
        pipe = self.r.pipeline()
        pipe.delete(self._key(email))
        pipe.hset(self._key(email), mapping={"code_hash": hash_code(code), "attempts": 0})
        pipe.expire(self._key(email), CODE_TTL_SECONDS)
        pipe.execute()

    def verify(self, email: str, code: str) -> str:
        key = self._key(email)
        entry = self.r.hgetall(key)
        if not entry:
            return "invalid"  # 不存在或 TTL 已过期(Redis 自动删除)
        attempts = int(entry.get("attempts", 0))
        if attempts >= MAX_VERIFY_ATTEMPTS:
            return "exhausted"
        if hash_code(code) != entry.get("code_hash"):
            self.r.hincrby(key, "attempts", 1)  # 原子计数
            return "invalid"
        self.r.delete(key)
        return "ok"

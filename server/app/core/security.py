"""JWT 签发与校验(HS256,标准库实现,避免首版引入额外依赖)。

生产注意:密钥来自 YS_JWT_SECRET,必须为强随机值;refresh token 轮换,
旧 refresh 使用即作废(AUTH_0004)。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from .config import settings
from .errors import ApiError


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def issue_token(user_id: str, kind: str = "access") -> str:
    ttl = settings.jwt_access_ttl_seconds if kind == "access" else settings.jwt_refresh_ttl_seconds
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(json.dumps({
        "sub": user_id, "kind": kind,
        "iat": int(time.time()), "exp": int(time.time()) + ttl,
    }).encode())
    signing_input = f"{header}.{payload}".encode()
    sig = _b64(hmac.new(settings.jwt_secret.encode(), signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def verify_token(token: str, kind: str = "access") -> str:
    """返回 user_id;过期抛 AUTH_0003,无效抛 AUTH_0004/0005。"""
    try:
        header, payload, sig = token.split(".")
        signing_input = f"{header}.{payload}".encode()
        expected = _b64(hmac.new(settings.jwt_secret.encode(), signing_input, hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            raise ApiError("AUTH_0004")
        claims = json.loads(_unb64(payload))
    except ApiError:
        raise
    except Exception:
        raise ApiError("AUTH_0004") from None
    if claims.get("kind") != kind:
        raise ApiError("AUTH_0004")
    if claims["exp"] < time.time():
        raise ApiError("AUTH_0003" if kind == "access" else "AUTH_0004")
    return claims["sub"]

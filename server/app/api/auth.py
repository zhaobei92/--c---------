from __future__ import annotations

import hashlib
import secrets
import time

from fastapi import APIRouter
from pydantic import BaseModel, EmailStr

from ..core.config import settings
from ..core.errors import ApiError
from ..core.security import issue_token, verify_token
from ..services.email_provider import get_email_provider
from .deps import CurrentUser, state

router = APIRouter(prefix="/v1", tags=["auth"])


class EmailIn(BaseModel):
    email: EmailStr


class VerifyIn(BaseModel):
    email: EmailStr
    code: str


# 验证码策略。状态存 state.email_codes(单实例内存);
# 多实例部署必须迁移到 Redis(共享 + TTL),这是 prod 部署清单项。
CODE_TTL_SECONDS = 600
RESEND_COOLDOWN_SECONDS = 60
MAX_VERIFY_ATTEMPTS = 5


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


@router.post("/auth/email/code")
def send_code(body: EmailIn):
    now = time.time()
    existing = state.email_codes.get(body.email)
    if existing and now - existing["last_sent"] < RESEND_COOLDOWN_SECONDS:
        raise ApiError("AUTH_0002", detail={
            "retry_after_s": int(RESEND_COOLDOWN_SECONDS - (now - existing["last_sent"]))})
    code = f"{secrets.randbelow(1_000_000):06d}"
    # 只存 Hash,不存明文;带有效期与尝试计数
    state.email_codes[body.email] = {
        "code_hash": _hash_code(code),
        "expires_at": now + CODE_TTL_SECONDS,
        "attempts": 0,
        "last_sent": now,
    }
    provider = get_email_provider(settings)
    provider.send(body.email, "YS Note verification code",
                  f"Your verification code is {code} (valid {CODE_TTL_SECONDS // 60} min).")
    if settings.env == "prod":
        return {"sent": True}  # 验证码绝不出现在 prod API 响应中
    return {"sent": True, "dev_code": code}  # 仅 dev/staging 便于联调


@router.post("/auth/email/verify")
def verify_code(body: VerifyIn):
    entry = state.email_codes.get(body.email)
    if entry is None:
        raise ApiError("AUTH_0001")
    if entry["attempts"] >= MAX_VERIFY_ATTEMPTS:
        raise ApiError("AUTH_0008")  # 尝试超限:即使验证码正确也拒绝,需重新发码
    if time.time() > entry["expires_at"]:
        del state.email_codes[body.email]
        raise ApiError("AUTH_0001")
    if _hash_code(body.code) != entry["code_hash"]:
        entry["attempts"] += 1
        raise ApiError("AUTH_0001")
    del state.email_codes[body.email]
    user_id = state.users_by_email.get(body.email)
    is_new = user_id is None
    if is_new:
        user_id = state.create_user(body.email)
    return {
        "access_token": issue_token(user_id, "access"),
        "refresh_token": issue_token(user_id, "refresh"),
        "is_new_user": is_new,
    }


class RefreshIn(BaseModel):
    refresh_token: str


@router.post("/auth/refresh")
def refresh(body: RefreshIn):
    user_id = verify_token(body.refresh_token, "refresh")
    return {
        "access_token": issue_token(user_id, "access"),
        "refresh_token": issue_token(user_id, "refresh"),
    }


@router.get("/users/me")
def me(user_id: str = CurrentUser):
    return state.users[user_id]


class ProfileIn(BaseModel):
    nickname: str | None = None
    language: str | None = None
    region: str | None = None
    audio_retention_days: int | None = None


@router.patch("/users/me")
def update_me(body: ProfileIn, user_id: str = CurrentUser):
    state.users[user_id].update({k: v for k, v in body.model_dump().items() if v is not None})
    return state.users[user_id]


class ConsentIn(BaseModel):
    consent_type: str
    granted: bool


@router.post("/consents")
def record_consent(body: ConsentIn, user_id: str = CurrentUser):
    state.audit.append({
        "actor_type": "user", "actor_id": user_id,
        "action": f"consent:{body.consent_type}:{body.granted}",
    })
    return {"recorded": True}

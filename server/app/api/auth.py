from __future__ import annotations

import secrets

from fastapi import APIRouter
from pydantic import BaseModel, EmailStr

from ..core.config import settings
from ..core.errors import ApiError
from ..core.security import issue_token, verify_token
from ..services.code_store import (
    CODE_TTL_SECONDS, ResendTooSoon,
)
from ..services.email_provider import get_email_provider
from .deps import CurrentUser, state

router = APIRouter(prefix="/v1", tags=["auth"])


class EmailIn(BaseModel):
    email: EmailStr


class VerifyIn(BaseModel):
    email: EmailStr
    code: str


# 验证码状态经 CodeStore 抽象:dev 单实例内存;多实例共享用 RedisCodeStore
# (app/services/code_store.py,Redis TTL 过期 + 原子尝试计数)。


@router.post("/auth/email/code")
def send_code(body: EmailIn):
    code = f"{secrets.randbelow(1_000_000):06d}"
    try:
        state.code_store.put(body.email, code)  # Hash 存储 + 冷却 + 有效期
    except ResendTooSoon as e:
        raise ApiError("AUTH_0002", detail={"retry_after_s": e.retry_after_s})
    provider = get_email_provider(settings)
    provider.send(body.email, "YS Note verification code",
                  f"Your verification code is {code} (valid {CODE_TTL_SECONDS // 60} min).")
    if settings.env == "prod":
        return {"sent": True}  # 验证码绝不出现在 prod API 响应中
    return {"sent": True, "dev_code": code}  # 仅 dev/staging 便于联调


@router.post("/auth/email/verify")
def verify_code(body: VerifyIn):
    result = state.code_store.verify(body.email, body.code)
    if result == "exhausted":
        raise ApiError("AUTH_0008")  # 尝试超限:即使验证码正确也拒绝
    if result != "ok":
        raise ApiError("AUTH_0001")
    if state.db is not None:
        user_id, is_new = state.db.ensure_user(body.email)
    else:
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
    if state.db is not None:
        return state.db.get_user(user_id)
    return state.users[user_id]


class ProfileIn(BaseModel):
    nickname: str | None = None
    language: str | None = None
    region: str | None = None
    audio_retention_days: int | None = None


@router.patch("/users/me")
def update_me(body: ProfileIn, user_id: str = CurrentUser):
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    if state.db is not None:
        return state.db.update_user(user_id, changes)
    state.users[user_id].update(changes)
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

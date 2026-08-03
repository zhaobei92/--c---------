from __future__ import annotations

import random

from fastapi import APIRouter
from pydantic import BaseModel, EmailStr

from ..core.errors import ApiError
from ..core.security import issue_token, verify_token
from .deps import CurrentUser, state

router = APIRouter(prefix="/v1", tags=["auth"])


class EmailIn(BaseModel):
    email: EmailStr


class VerifyIn(BaseModel):
    email: EmailStr
    code: str


@router.post("/auth/email/code")
def send_code(body: EmailIn):
    # 骨架:生产接邮件服务 + 频控(AUTH_0002);dev 环境返回码便于联调
    code = f"{random.randint(0, 999999):06d}"
    state.email_codes[body.email] = code
    return {"sent": True, "dev_code": code}


@router.post("/auth/email/verify")
def verify_code(body: VerifyIn):
    if state.email_codes.get(body.email) != body.code:
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

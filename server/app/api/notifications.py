from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from .deps import CurrentUser, state

router = APIRouter(prefix="/v1", tags=["notifications"])


class TokenIn(BaseModel):
    platform: str  # ios / android
    token: str


@router.post("/push/tokens")
def register_push_token(body: TokenIn, user_id: str = CurrentUser):
    # 骨架:生产存表并接 APNs / FCM
    state.users[user_id]["push_token"] = {"platform": body.platform, "token": body.token}
    return {"registered": True}


@router.get("/notifications")
def list_notifications(user_id: str = CurrentUser):
    if state.db is not None:
        return {"items": state.db.list_notifications(user_id)}
    return {"items": state.notifications.get(user_id, [])}

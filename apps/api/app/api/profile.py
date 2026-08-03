from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.decisions import get_current_user
from app.db import get_db
from app.models import User
from app.services import followup_service
from shared_schemas import PreferencePosteriorOut

router = APIRouter(prefix="/api/v1/profile", tags=["profile"])


@router.get("/preferences", response_model=list[PreferencePosteriorOut])
def list_preference_posteriors(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[PreferencePosteriorOut]:
    return [
        PreferencePosteriorOut.model_validate(p)
        for p in followup_service.list_posteriors(db, user.id)
    ]


@router.delete("/preferences", status_code=200)
def delete_preference_posteriors(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """用户可以删除自己的长期偏好画像（上线前安全清单要求）。"""
    deleted = followup_service.delete_posteriors(db, user.id)
    return {"deleted": deleted}

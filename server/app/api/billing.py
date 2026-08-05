from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..core.errors import ApiError
from ..services.order_verification import OrderError, UnknownProduct, VerificationFailed
from .deps import CurrentUser, state

router = APIRouter(prefix="/v1", tags=["billing"])


@router.get("/entitlements/me")
def my_entitlements(user_id: str = CurrentUser):
    if state.db is not None:
        balances = state.db.balances(user_id)
    else:
        balances = state.entitlements.bucket_balances(user_id)
    return {**balances, "total_minutes": sum(balances.values())}


@router.get("/usage")
def my_usage(user_id: str = CurrentUser, page: int = 1, page_size: int = 50):
    if state.db is not None:
        items = state.db.usage_entries(user_id)
        start = (page - 1) * page_size
        return {"total": len(items), "page": page, "page_size": page_size,
                "items": items[start:start + page_size]}
    entries = state.entitlements.store.entries_for(user_id)
    entries.sort(key=lambda e: e.created_at, reverse=True)
    start = (page - 1) * page_size
    return {
        "total": len(entries), "page": page, "page_size": page_size,
        "items": [
            {"delta_minutes": e.delta_minutes, "reason": e.reason,
             "job_id": e.job_id, "generation": e.generation,
             "order_id": e.order_id,
             "created_at": e.created_at.isoformat()}
            for e in entries[start:start + page_size]
        ],
    }


class VerifyIn(BaseModel):
    credential: str  # apple: signed_transaction / google: purchase_token


@router.post("/orders/{platform}/verify")
def verify_order(platform: str, body: VerifyIn, user_id: str = CurrentUser):
    if platform not in ("apple", "google"):
        raise ApiError("SYS_9004", detail={"platform": platform})
    try:
        order, already = state.orders.verify_and_fulfill(
            user_id=user_id, platform=platform, credential=body.credential)
    except VerificationFailed as e:
        raise ApiError(e.error_code, detail={"reason": str(e)})
    except UnknownProduct as e:
        raise ApiError("ORD_5004", detail={"product_id": str(e)})
    except OrderError as e:
        raise ApiError("ORD_5005", detail={"reason": str(e)})
    return {"order_id": order.id, "status": order.status, "already_processed": already}


class RedeemIn(BaseModel):
    code: str


# 骨架:兑换码批次由管理后台生成;此处内存演示(生产:redeem_codes 表 + 原子核销)
REDEEM_CODES: dict[str, dict] = {
    "WELCOME300": {"minutes": 300, "bucket": "gift", "used_by": None},
}


@router.post("/redeem")
def redeem(body: RedeemIn, user_id: str = CurrentUser):
    rc = REDEEM_CODES.get(body.code.upper())
    if rc is None:
        raise ApiError("ENT_3005")
    if rc["used_by"] is not None:
        raise ApiError("ENT_3006")
    rc["used_by"] = user_id
    state.entitlements.grant(
        user_id, rc["bucket"], rc["minutes"],
        source_type="redeem", idempotency_key=f"redeem:{body.code.upper()}",
    )
    return {"granted_minutes": rc["minutes"]}

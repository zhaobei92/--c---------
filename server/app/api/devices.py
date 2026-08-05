from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..core.errors import ApiError
from .deps import CurrentUser, state

router = APIRouter(prefix="/v1", tags=["devices"])

SUPPORTED_MODELS = {"YS-R1", "YS-R1-PRO", "MOCK-1"}


class BindIn(BaseModel):
    sn: str
    model: str
    firmware_version: str | None = None
    ble_mac: str | None = None


@router.post("/devices/bind")
def bind(body: BindIn, user_id: str = CurrentUser):
    if body.model not in SUPPORTED_MODELS:
        raise ApiError("DEV_1102", detail={"model": body.model})
    owner = state.bindings.get(body.sn)
    if owner is not None and owner != user_id:
        raise ApiError("DEV_1101")  # 验收红线:设备绑定串号 0
    state.bindings[body.sn] = user_id
    state.devices.setdefault(body.sn, {
        "sn": body.sn, "model": body.model,
        "firmware_version": body.firmware_version,
        "battery": None, "storage_free_mb": None,
    })
    return {"bound": True, "sn": body.sn}


@router.get("/devices")
def my_devices(user_id: str = CurrentUser):
    return {"items": [state.devices[sn] for sn, uid in state.bindings.items() if uid == user_id]}


class HeartbeatIn(BaseModel):
    battery: int
    storage_free_mb: int
    firmware_version: str | None = None
    recording_state: str | None = None


@router.post("/devices/{sn}/heartbeat")
def heartbeat(sn: str, body: HeartbeatIn, user_id: str = CurrentUser):
    _require_binding(sn, user_id)
    state.devices[sn].update(body.model_dump(exclude_none=True))
    return {"ok": True}


@router.delete("/devices/{sn}/binding")
def unbind(sn: str, user_id: str = CurrentUser):
    _require_binding(sn, user_id)
    del state.bindings[sn]
    return {"unbound": True}


@router.get("/firmware/check")
def firmware_check(model: str, current: str):
    # 骨架:生产从 firmware_versions 表查最新非黑名单版本
    return {"latest": current, "update_available": False}


def _require_binding(sn: str, user_id: str) -> None:
    if state.bindings.get(sn) != user_id:
        raise ApiError("DEV_1101", message="device not bound to this account")

"""管理后台骨架(/admin,独立鉴权,全部敏感操作写 audit_logs)。

首版由这些 API 驱动 React 后台;RBAC 与真实管理员账号体系在阶段5 完善。
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..services.job_state_machine import JobStatus
from .deps import AdminGuard, state

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[AdminGuard])


@router.get("/users")
def list_users(page: int = 1, page_size: int = 20):
    items = list(state.users.values())
    start = (page - 1) * page_size
    return {"total": len(items), "items": items[start:start + page_size]}


@router.get("/devices")
def list_devices():
    return {"items": [
        {**d, "bound_user_id": state.bindings.get(sn)}
        for sn, d in state.devices.items()
    ]}


@router.get("/jobs")
def list_jobs(status: str | None = None):
    items = []
    for job in state.jobs.values():
        st = job["state"]
        if status and st.status.value != status:
            continue
        items.append({"id": job["id"], "user_id": job["user_id"],
                      "status": st.status.value, "retry_count": st.retry_count,
                      "error_code": st.error_code})
    return {"items": items}


@router.get("/orders")
def list_orders():
    return {"items": [
        {"id": o.id, "user_id": o.user_id, "platform": o.platform,
         "product_id": o.product_id, "status": o.status}
        for o in state.orders.orders_by_txn.values()
    ]}


@router.get("/costs")
def cost_summary():
    """AI 成本汇总骨架:生产按 transcription_jobs 成本列聚合(天/供应商/类型)。"""
    total_jobs = len(state.jobs)
    completed = sum(1 for j in state.jobs.values() if j["state"].status is JobStatus.COMPLETED)
    minutes = sum(j["minutes_charged"] for j in state.jobs.values())
    return {"total_jobs": total_jobs, "completed_jobs": completed, "minutes_charged": minutes}


@router.get("/errors")
def error_summary():
    counts: dict[str, int] = {}
    for job in state.jobs.values():
        code = job["state"].error_code
        if code:
            counts[code] = counts.get(code, 0) + 1
    return {"by_code": counts}


class AdjustIn(BaseModel):
    user_id: str
    minutes: int
    reason: str


@router.post("/adjust-minutes")
def adjust_minutes(body: AdjustIn):
    """人工调整分钟 — 同样必须走 ledger 流水(reason=adjust),留审计。"""
    ent = state.entitlements.grant(
        body.user_id, "gift", body.minutes, source_type="admin",
    )
    state.audit.append({"actor_type": "admin", "action": "adjust_minutes",
                       "target_id": body.user_id, "detail": body.model_dump()})
    return {"entitlement_id": ent.id}


@router.get("/audit-logs")
def audit_logs():
    return {"items": state.audit}

"""权益服务 — usage_ledger 流水核心逻辑(纯 Python,可独立单测)。

铁律(docs/02-architecture.md §7):
  * 所有分钟增减 = 一条 ledger 流水;余额 = 流水聚合;禁止直接改余额字段。
  * 扣费顺序:先到期先扣 —— free_monthly → gift → member → purchased → enterprise;
    同类桶内按 expires_at 升序(NULL 视为最晚)。
  * 幂等:同一 (job_id, reason) 只允许产生一次扣减;退款按原扣减逐桶冲正。

持久化层(SQLAlchemy)只需实现 LedgerStore 协议;单测使用 InMemoryLedgerStore。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Protocol

BUCKET_PRIORITY = ["free_monthly", "gift", "member", "purchased", "enterprise"]


class InsufficientMinutes(Exception):
    """错误码 ENT_3001。"""

    error_code = "ENT_3001"

    def __init__(self, required: int, available: int):
        self.required, self.available = required, available
        super().__init__(f"insufficient minutes: need {required}, have {available}")


class DuplicateOperation(Exception):
    """幂等键冲突:同一操作重复提交(验收红线:重复扣费 = 0)。"""

    error_code = "ORD_5003"


@dataclass
class Entitlement:
    id: str
    user_id: str
    bucket: str
    minutes_granted: int
    expires_at: datetime | None = None
    effective_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class LedgerEntry:
    id: str
    user_id: str
    entitlement_id: str | None
    delta_minutes: int  # 授予为正,消耗为负,冲正为正
    reason: str  # grant / consume / refund / expire / adjust
    job_id: str | None = None
    order_id: str | None = None
    idempotency_key: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class LedgerStore(Protocol):
    def entitlements_for(self, user_id: str) -> Iterable[Entitlement]: ...
    def entries_for(self, user_id: str) -> Iterable[LedgerEntry]: ...
    def has_idempotency_key(self, key: str) -> bool: ...
    def append(self, entry: LedgerEntry) -> None: ...
    def add_entitlement(self, ent: Entitlement) -> None: ...


class InMemoryLedgerStore:
    def __init__(self) -> None:
        self._entitlements: dict[str, list[Entitlement]] = {}
        self._entries: dict[str, list[LedgerEntry]] = {}
        self._idem: set[str] = set()

    def entitlements_for(self, user_id: str) -> list[Entitlement]:
        return list(self._entitlements.get(user_id, []))

    def entries_for(self, user_id: str) -> list[LedgerEntry]:
        return list(self._entries.get(user_id, []))

    def has_idempotency_key(self, key: str) -> bool:
        return key in self._idem

    def append(self, entry: LedgerEntry) -> None:
        if entry.idempotency_key:
            if entry.idempotency_key in self._idem:
                raise DuplicateOperation(entry.idempotency_key)
            self._idem.add(entry.idempotency_key)
        self._entries.setdefault(entry.user_id, []).append(entry)

    def add_entitlement(self, ent: Entitlement) -> None:
        self._entitlements.setdefault(ent.user_id, []).append(ent)


class EntitlementService:
    def __init__(self, store: LedgerStore, now=lambda: datetime.now(timezone.utc)):
        self.store = store
        self.now = now

    # ---------- 查询 ----------

    def bucket_balances(self, user_id: str) -> dict[str, int]:
        """各权益桶余额 = 该桶所有流水之和(过期桶不计入可用)。"""
        ents = {e.id: e for e in self.store.entitlements_for(user_id)}
        balances: dict[str, int] = {}
        for entry in self.store.entries_for(user_id):
            ent = ents.get(entry.entitlement_id or "")
            if ent is None:
                continue
            balances[ent.id] = balances.get(ent.id, 0) + entry.delta_minutes
        result: dict[str, int] = {b: 0 for b in BUCKET_PRIORITY}
        now = self.now()
        for ent_id, bal in balances.items():
            ent = ents[ent_id]
            if ent.expires_at is not None and ent.expires_at <= now:
                continue  # 过期桶:余额不可用(由 expire 批处理落冲销流水)
            result[ent.bucket] = result.get(ent.bucket, 0) + bal
        return result

    def available_minutes(self, user_id: str) -> int:
        return sum(self.bucket_balances(user_id).values())

    # ---------- 授予 ----------

    def grant(
        self,
        user_id: str,
        bucket: str,
        minutes: int,
        *,
        source_type: str,
        order_id: str | None = None,
        expires_at: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> Entitlement:
        assert bucket in BUCKET_PRIORITY, f"unknown bucket {bucket}"
        assert minutes > 0
        ent = Entitlement(
            id=str(uuid.uuid4()), user_id=user_id, bucket=bucket,
            minutes_granted=minutes, expires_at=expires_at,
        )
        # 先落流水,幂等冲突时权益不生效
        self.store.append(LedgerEntry(
            id=str(uuid.uuid4()), user_id=user_id, entitlement_id=ent.id,
            delta_minutes=minutes, reason="grant", order_id=order_id,
            idempotency_key=idempotency_key,
        ))
        self.store.add_entitlement(ent)
        return ent

    # ---------- 消耗 ----------

    def _deduction_plan(self, user_id: str, minutes: int) -> list[tuple[Entitlement, int]]:
        """按扣费顺序生成 (权益桶, 扣减分钟) 列表;余额不足抛 ENT_3001。"""
        now = self.now()
        ents = [
            e for e in self.store.entitlements_for(user_id)
            if e.expires_at is None or e.expires_at > now
        ]
        per_ent: dict[str, int] = {e.id: 0 for e in ents}
        for entry in self.store.entries_for(user_id):
            if entry.entitlement_id in per_ent:
                per_ent[entry.entitlement_id] += entry.delta_minutes

        def sort_key(e: Entitlement):
            far_future = datetime.max.replace(tzinfo=timezone.utc)
            return (BUCKET_PRIORITY.index(e.bucket), e.expires_at or far_future)

        plan: list[tuple[Entitlement, int]] = []
        remaining = minutes
        for ent in sorted(ents, key=sort_key):
            bal = per_ent[ent.id]
            if bal <= 0:
                continue
            take = min(bal, remaining)
            plan.append((ent, take))
            remaining -= take
            if remaining == 0:
                return plan
        raise InsufficientMinutes(required=minutes, available=minutes - remaining)

    def consume(self, user_id: str, minutes: int, *, job_id: str) -> list[LedgerEntry]:
        """为一个 AI 任务扣减分钟。幂等键 = consume:{job_id},同任务只扣一次。"""
        assert minutes > 0
        key = f"consume:{job_id}"
        if self.store.has_idempotency_key(key):
            raise DuplicateOperation(key)
        plan = self._deduction_plan(user_id, minutes)
        entries: list[LedgerEntry] = []
        for i, (ent, take) in enumerate(plan):
            entry = LedgerEntry(
                id=str(uuid.uuid4()), user_id=user_id, entitlement_id=ent.id,
                delta_minutes=-take, reason="consume", job_id=job_id,
                idempotency_key=key if i == 0 else f"{key}:{i}",
            )
            self.store.append(entry)
            entries.append(entry)
        return entries

    # ---------- 冲正 ----------

    def refund_job(self, user_id: str, job_id: str) -> list[LedgerEntry]:
        """任务失败/退款:按原扣减流水逐桶冲正。幂等键 = refund:{job_id}。"""
        key = f"refund:{job_id}"
        if self.store.has_idempotency_key(key):
            raise DuplicateOperation(key)
        consumed = [
            e for e in self.store.entries_for(user_id)
            if e.job_id == job_id and e.reason == "consume"
        ]
        entries: list[LedgerEntry] = []
        for i, orig in enumerate(consumed):
            entry = LedgerEntry(
                id=str(uuid.uuid4()), user_id=user_id, entitlement_id=orig.entitlement_id,
                delta_minutes=-orig.delta_minutes, reason="refund", job_id=job_id,
                idempotency_key=key if i == 0 else f"{key}:{i}",
            )
            self.store.append(entry)
            entries.append(entry)
        return entries

    def expire_entitlement(self, user_id: str, entitlement_id: str) -> LedgerEntry | None:
        """过期批处理:把过期桶剩余余额冲销为 0(留审计流水)。"""
        bal = 0
        for entry in self.store.entries_for(user_id):
            if entry.entitlement_id == entitlement_id:
                bal += entry.delta_minutes
        if bal <= 0:
            return None
        entry = LedgerEntry(
            id=str(uuid.uuid4()), user_id=user_id, entitlement_id=entitlement_id,
            delta_minutes=-bal, reason="expire",
            idempotency_key=f"expire:{entitlement_id}",
        )
        self.store.append(entry)
        return entry

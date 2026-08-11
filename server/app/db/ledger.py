"""SqlLedgerStore — LedgerStore 协议的 PostgreSQL 实现。

绑定到调用方的 Session/事务:扣费顺序与幂等语义完全复用
EntitlementService(纯 Python 核心),并发安全由调用方在同一事务内
先 SELECT ... FOR UPDATE 锁定该用户的权益行保证。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models.tables import Entitlement as EntitlementRow
from ..models.tables import UsageLedger as LedgerRow
from ..services.entitlement_service import (
    DuplicateOperation, Entitlement, LedgerEntry,
)


def _as_dt(value):
    return value


class SqlLedgerStore:
    def __init__(self, session: Session):
        self.s = session

    def lock_user_rows(self, user_id: str) -> None:
        """事务内锁定该用户全部权益行(扣费前调用,串行化并发扣减)。"""
        self.s.execute(
            select(EntitlementRow.id)
            .where(EntitlementRow.user_id == user_id)
            .with_for_update()
        ).all()

    def entitlements_for(self, user_id: str) -> list[Entitlement]:
        rows = self.s.execute(
            select(EntitlementRow).where(EntitlementRow.user_id == user_id)
        ).scalars().all()
        return [
            Entitlement(
                id=str(r.id), user_id=str(r.user_id), bucket=r.bucket,
                minutes_granted=r.minutes_granted,
                expires_at=_as_dt(r.expires_at), effective_at=_as_dt(r.effective_at),
            )
            for r in rows
        ]

    def entries_for(self, user_id: str) -> list[LedgerEntry]:
        rows = self.s.execute(
            select(LedgerRow).where(LedgerRow.user_id == user_id)
            .order_by(LedgerRow.created_at)
        ).scalars().all()
        return [
            LedgerEntry(
                id=str(r.id), user_id=str(r.user_id),
                entitlement_id=str(r.entitlement_id) if r.entitlement_id else None,
                delta_minutes=r.delta_minutes, reason=r.reason,
                job_id=str(r.job_id) if r.job_id else None,
                generation=r.charge_generation,
                order_id=str(r.order_id) if r.order_id else None,
                idempotency_key=r.idempotency_key, created_at=_as_dt(r.created_at),
            )
            for r in rows
        ]

    def has_idempotency_key(self, key: str) -> bool:
        return self.s.execute(
            select(LedgerRow.id).where(LedgerRow.idempotency_key == key)
        ).first() is not None

    def append(self, entry: LedgerEntry) -> None:
        self.s.add(LedgerRow(
            id=entry.id, user_id=entry.user_id,
            entitlement_id=entry.entitlement_id,
            delta_minutes=entry.delta_minutes, reason=entry.reason,
            job_id=entry.job_id, charge_generation=entry.generation,
            order_id=entry.order_id, idempotency_key=entry.idempotency_key,
            created_at=entry.created_at,
        ))
        try:
            self.s.flush()  # 唯一约束立即生效:幂等冲突在此抛出
        except IntegrityError as e:
            raise DuplicateOperation(entry.idempotency_key or "") from e

    def add_entitlement(self, ent: Entitlement) -> None:
        self.s.add(EntitlementRow(
            id=ent.id, user_id=ent.user_id, bucket=ent.bucket,
            minutes_granted=ent.minutes_granted,
            effective_at=ent.effective_at, expires_at=ent.expires_at,
            source_type="system",
        ))
        self.s.flush()

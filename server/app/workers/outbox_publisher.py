"""Outbox Publisher(PostgreSQL 正式实现)。

FOR UPDATE SKIP LOCKED 领取未投递事件 → 投递 Redis Streams → 成功置
published_at;失败记 attempts/last_error 并按指数退避推迟 next_attempt_at。
至少一次投递;消费侧按 job 状态幂等。多实例安全(SKIP LOCKED 天然分片)。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..models.tables import OutboxEvent
from ..services.redis_queue import RedisStreamsQueue


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(2 ** attempts, 300))


def publish_pending(session_factory: sessionmaker[Session],
                    queue: RedisStreamsQueue, batch: int = 100) -> int:
    """领取并投递一批;返回成功投递数。"""
    published = 0
    with session_factory() as s, s.begin():
        rows = s.execute(
            select(OutboxEvent)
            .where(OutboxEvent.published_at.is_(None),
                   OutboxEvent.next_attempt_at <= _now())
            .order_by(OutboxEvent.created_at)
            .with_for_update(skip_locked=True)
            .limit(batch)
        ).scalars().all()
        for row in rows:
            try:
                queue.publish(row.topic, str(row.id), dict(row.payload))
            except Exception as e:
                row.attempts += 1
                row.last_error = str(e)[:500]
                row.next_attempt_at = _now() + _backoff(row.attempts)
                continue
            row.published_at = _now()
            published += 1
    return published

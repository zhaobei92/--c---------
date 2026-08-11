"""Redis Streams 消费者组队列(替代自制 List 队列进入生产路径)。

天然具备:消费者组、ACK、Pending 列表、崩溃后 XAUTOCLAIM 重领、积压可观测。
语义为至少一次投递;消费侧必须以 job_id + 状态机幂等。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

GROUP = "workers"


@dataclass
class StreamMessage:
    id: str          # Redis stream 消息 ID
    event_id: str    # outbox_events.id(端到端追踪)
    payload: dict[str, Any]


class RedisStreamsQueue:
    def __init__(self, redis_client, prefix: str = "ysq"):
        self.r = redis_client
        self.prefix = prefix

    def _stream(self, topic: str) -> str:
        return f"{self.prefix}:{topic}"

    def ensure_group(self, topic: str) -> None:
        try:
            self.r.xgroup_create(self._stream(topic), GROUP, id="0", mkstream=True)
        except Exception as e:  # BUSYGROUP = 已存在
            if "BUSYGROUP" not in str(e):
                raise

    def publish(self, topic: str, event_id: str, payload: dict[str, Any]) -> str:
        self.ensure_group(topic)
        return self.r.xadd(self._stream(topic), {
            "event_id": event_id,
            "payload": json.dumps(payload),
        })

    def read(self, topic: str, consumer: str, count: int = 10,
             block_ms: int = 100) -> list[StreamMessage]:
        self.ensure_group(topic)
        resp = self.r.xreadgroup(GROUP, consumer,
                                 {self._stream(topic): ">"},
                                 count=count, block=block_ms)
        return self._parse(resp)

    def ack(self, topic: str, message_id: str) -> None:
        self.r.xack(self._stream(topic), GROUP, message_id)

    def autoclaim(self, topic: str, consumer: str,
                  min_idle_ms: int = 60_000, count: int = 10) -> list[StreamMessage]:
        """崩溃恢复:接管空闲超过 min_idle_ms 的 pending 消息。"""
        self.ensure_group(topic)
        _, entries, *_ = self.r.xautoclaim(
            self._stream(topic), GROUP, consumer,
            min_idle_time=min_idle_ms, start_id="0-0", count=count)
        return self._parse_entries(entries)

    def delivery_count(self, topic: str, message_id: str) -> int:
        pending = self.r.xpending_range(
            self._stream(topic), GROUP, min=message_id, max=message_id, count=1)
        return pending[0]["times_delivered"] if pending else 0

    def pending_count(self, topic: str) -> int:
        info = self.r.xpending(self._stream(topic), GROUP)
        return info["pending"]

    def move_to_dlq(self, topic: str, msg: StreamMessage, reason: str) -> None:
        """超出重试的毒消息进入死信流并 ACK 原消息(告警接死信流长度)。"""
        self.r.xadd(f"{self._stream(topic)}.dlq", {
            "event_id": msg.event_id,
            "payload": json.dumps(msg.payload),
            "reason": reason,
        })
        self.ack(topic, msg.id)

    def dlq_length(self, topic: str) -> int:
        return self.r.xlen(f"{self._stream(topic)}.dlq")

    def _parse(self, resp) -> list[StreamMessage]:
        out: list[StreamMessage] = []
        for _stream, entries in resp or []:
            out.extend(self._parse_entries(entries))
        return out

    @staticmethod
    def _parse_entries(entries) -> list[StreamMessage]:
        return [
            StreamMessage(
                id=mid,
                event_id=fields.get("event_id", ""),
                payload=json.loads(fields.get("payload", "{}")),
            )
            for mid, fields in entries
        ]

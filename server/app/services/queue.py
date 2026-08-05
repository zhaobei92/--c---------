"""任务队列抽象。

首版:Redis Queue;可替换 RabbitMQ —— 业务代码只依赖 TaskQueue 协议。
单测与本地开发使用 InMemoryQueue。
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class QueueMessage:
    topic: str
    payload: dict[str, Any]
    attempts: int = 0


class TaskQueue(Protocol):
    def enqueue(self, topic: str, payload: dict[str, Any]) -> None: ...
    def dequeue(self, topic: str) -> QueueMessage | None: ...
    def nack(self, message: QueueMessage) -> None:
        """处理失败重新入队(attempts+1);由 worker 决定超限后走状态机 fail_or_retry。"""
        ...


class InMemoryQueue:
    def __init__(self) -> None:
        self._queues: dict[str, deque[QueueMessage]] = {}

    def enqueue(self, topic: str, payload: dict[str, Any]) -> None:
        self._queues.setdefault(topic, deque()).append(QueueMessage(topic, payload))

    def dequeue(self, topic: str) -> QueueMessage | None:
        q = self._queues.get(topic)
        return q.popleft() if q else None

    def nack(self, message: QueueMessage) -> None:
        message.attempts += 1
        self._queues.setdefault(message.topic, deque()).append(message)


class RedisQueue:  # pragma: no cover - 需要 redis 实例,阶段1 部署时启用
    """基于 Redis List 的简单队列(BRPOPLPUSH 保障 at-least-once)。"""

    def __init__(self, redis_client, prefix: str = "ysq"):
        self.r = redis_client
        self.prefix = prefix

    def _key(self, topic: str) -> str:
        return f"{self.prefix}:{topic}"

    def enqueue(self, topic: str, payload: dict[str, Any]) -> None:
        self.r.lpush(self._key(topic), json.dumps({"payload": payload, "attempts": 0}))

    def dequeue(self, topic: str) -> QueueMessage | None:
        raw = self.r.rpoplpush(self._key(topic), self._key(topic) + ":processing")
        if raw is None:
            return None
        data = json.loads(raw)
        return QueueMessage(topic, data["payload"], data.get("attempts", 0))

    def nack(self, message: QueueMessage) -> None:
        message.attempts += 1
        self.r.lpush(
            self._key(message.topic),
            json.dumps({"payload": message.payload, "attempts": message.attempts}),
        )


TOPIC_TRANSCRIBE = "jobs.transcribe"
TOPIC_EXPORT = "jobs.export"
TOPIC_NOTIFY = "jobs.notify"
TOPIC_DELETE = "jobs.delete"  # deletion_requests 驱动的三处同步删除

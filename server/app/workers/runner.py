"""Worker 常驻进程入口(Phase 3 收口)。

单进程内跑两件事:
  1. Outbox Publisher —— 把业务事务写入的 outbox_events 投递到 Redis Streams;
  2. DB Worker —— 消费 Streams,按 ai_provider 推进任务(Demo Mode 用 mock)。

启动:
    python -m app.workers.runner            # 读环境变量(与 API 同一套配置)
    python -m app.workers.runner --once     # 只跑一轮,供脚本/CI 使用

优雅退出:SIGTERM/SIGINT 后不再领新消息,当前轮结束即退出(未 ACK 的消息
由其他实例 XAUTOCLAIM 接管,语义仍是至少一次)。
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from ..core.config import settings
from ..db.engine import make_engine, make_session_factory
from ..services.redis_queue import RedisStreamsQueue
from .db_worker import run_worker_once, stages_for
from .outbox_publisher import publish_pending

logger = logging.getLogger("ysnote.worker")

_stop = False


def _handle_signal(signum, _frame) -> None:
    global _stop
    _stop = True
    logger.info("received signal %s, finishing current cycle", signum)


def build_deps():
    """按配置装配依赖;与 API 的 bootstrap 同源(失败即退出,不静默降级)。"""
    if settings.storage_backend != "postgres":
        raise RuntimeError(
            "worker requires YS_STORAGE_BACKEND=postgres "
            f"(got {settings.storage_backend})")
    if settings.queue_backend != "redis":
        raise RuntimeError(
            "worker requires YS_QUEUE_BACKEND=redis "
            f"(got {settings.queue_backend})")
    import redis as redis_lib

    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    client = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
    client.ping()  # 连接失败直接抛错,进程不启动
    return session_factory, RedisStreamsQueue(client)


def run_cycle(session_factory, queue, consumer: str,
              claim_idle_ms: int = 60_000) -> dict[str, int]:
    """一轮:投递 outbox → 消费队列(含崩溃恢复重领)。"""
    published = publish_pending(session_factory, queue)
    stats = run_worker_once(session_factory, queue, consumer=consumer,
                            stages=stages_for(settings.ai_provider),
                            claim_idle_ms=claim_idle_ms)
    return {"published": published, **stats}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="YS Note background worker")
    parser.add_argument("--once", action="store_true", help="只跑一轮后退出")
    parser.add_argument("--consumer", default="w1", help="消费者名(多实例需唯一)")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="空闲轮询间隔(秒)")
    args = parser.parse_args(argv)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    session_factory, queue = build_deps()
    logger.info("worker started (consumer=%s, ai_provider=%s)",
                args.consumer, settings.ai_provider)

    while not _stop:
        try:
            stats = run_cycle(session_factory, queue, args.consumer)
        except Exception:
            logger.exception("worker cycle failed; retrying after interval")
            stats = {}
        if any(v for v in stats.values()):
            logger.info("cycle: %s", stats)
        if args.once:
            break
        time.sleep(args.interval)

    logger.info("worker stopped")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

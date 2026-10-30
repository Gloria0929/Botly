"""Redis 总线：任务队列 + 事件广播。

设计要点：
- 队列用 Redis List（LPUSH / BRPOP），run_id 即消息体。
- 事件广播用 Pub/Sub，只作为「有新事件了」的唤醒信号；
  真正的数据仍从 events 表按 id 顺序读取，因此不会丢事件、不会乱序，
  并且天然支持 SSE 的 Last-Event-ID 补发。
- Redis 不可用时自动降级为纯 DB 轮询，应用仍可运行（便于本地排查）。
"""
from __future__ import annotations

import asyncio
import json
import os
import time

import redis
import redis.asyncio as aredis

from .config import REDIS_URL

QUEUE_KEY = "botly:queue"
WAKE_PREFIX = "botly:events:"

_sync: redis.Redis | None = None
_async: aredis.Redis | None = None
_available: bool | None = None
_last_probe = 0.0


def _sync_client() -> redis.Redis:
    global _sync
    if _sync is None:
        _sync = redis.Redis.from_url(
            REDIS_URL, decode_responses=True, socket_connect_timeout=1.5, socket_timeout=3
        )
    return _sync


def async_client() -> aredis.Redis:
    global _async
    if _async is None:
        _async = aredis.Redis.from_url(
            REDIS_URL, decode_responses=True, socket_connect_timeout=1.5, socket_timeout=5
        )
    return _async


def available() -> bool:
    """带缓存的可用性探测，避免每次调用都建连。"""
    global _available, _last_probe
    now = time.time()
    if _available is not None and now - _last_probe < 15:
        return _available
    _last_probe = now
    try:
        _sync_client().ping()
        _available = True
    except Exception:  # noqa: BLE001
        _available = False
    return _available


def enqueue(run_id: str) -> bool:
    try:
        _sync_client().lpush(QUEUE_KEY, run_id)
        return True
    except Exception:  # noqa: BLE001
        return False


def publish_event(user_id: str, chat_id: str | None, event_id: int, type_: str) -> None:
    """只发唤醒信号，携带最小信息便于日志排查。"""
    if not available():
        return
    try:
        _sync_client().publish(
            f"{WAKE_PREFIX}{user_id}",
            json.dumps({"id": event_id, "chat_id": chat_id, "type": type_}),
        )
    except Exception:  # noqa: BLE001
        pass


def queue_depth() -> int:
    try:
        return int(_sync_client().llen(QUEUE_KEY))
    except Exception:  # noqa: BLE001
        return 0


async def dequeue(timeout: int = 2) -> str | None:
    """阻塞取一个待执行 run_id。Redis 不可用时返回 None，由调用方走 DB 轮询。"""
    if not available():
        await asyncio.sleep(0.5)
        return None
    try:
        item = await async_client().brpop(QUEUE_KEY, timeout=timeout)
        if not item:
            return None
        return item[1]
    except Exception:  # noqa: BLE001
        await asyncio.sleep(0.5)
        return None


class Waker:
    """SSE 用：订阅用户级频道，有新事件时立即唤醒。"""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.pubsub = None

    async def __aenter__(self):
        if available():
            try:
                self.pubsub = async_client().pubsub()
                await self.pubsub.subscribe(f"{WAKE_PREFIX}{self.user_id}")
            except Exception:  # noqa: BLE001
                self.pubsub = None
        return self

    async def __aexit__(self, *exc):
        if self.pubsub:
            try:
                await self.pubsub.aclose()
            except Exception:  # noqa: BLE001
                pass

    async def wait(self, timeout: float = 1.0) -> None:
        if not self.pubsub:
            await asyncio.sleep(min(timeout, 0.4))
            return
        try:
            await self.pubsub.get_message(ignore_subscribe_messages=True, timeout=timeout)
        except Exception:  # noqa: BLE001
            await asyncio.sleep(min(timeout, 0.4))

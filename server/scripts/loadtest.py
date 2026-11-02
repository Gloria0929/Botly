"""50 用户 / 20 并发压测脚本（核心读写路径）。

用法（先起一个空库的 API，再做压测）：
    cd server
    DATABASE_URL=sqlite:////tmp/botly_lt.db PORT=8200 ./run-api.sh
    ./venv/bin/python scripts/loadtest.py --base http://127.0.0.1:8200 \
        --concurrency 20 --requests 200 --messages 1000

脚本做的事：
  1. 经 HTTP 播种：邀请码、注册用户、绑定 mock 模型；
  2. 直连 DB 往主线程插入 N 条消息（绕开执行队列，快）；
  3. 经 HTTP 压测：首屏消息拉取、群聊列表、发送消息；
  4. 输出 p50 / p95 / p99、吞吐、错误数。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from app.core.db import SessionLocal, init_db  # noqa: E402
from app.core.models import InviteCode, Message  # noqa: E402

INVITE = "LOADTEST-INVITE"
EMAIL = "loadtest@example.dev"
PASSWORD = "loadtest-password-123"
HEADERS = {"X-Botly-Client": "web"}


def _seed(base: str, n_messages: int) -> str:
    init_db()
    with SessionLocal() as db:
        if not db.query(InviteCode).filter(InviteCode.code == INVITE).first():
            db.add(InviteCode(code=INVITE, max_uses=1000, used_count=0))
            db.commit()

    with httpx.Client(base_url=base, timeout=120, headers=HEADERS) as c:
        login = c.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        if login.status_code == 401:
            r = c.post(
                "/api/auth/register",
                json={"email": EMAIL, "password": PASSWORD, "invite_code": INVITE, "name": "压测用户"},
            )
            r.raise_for_status()
            c.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})

        # 绑定 mock 模型，保证发消息能创建执行（不会因未配模型被拒）
        prov = c.post("/api/providers", json={"vendor_id": "mock", "kind": "mock"}).json()
        pid = prov["provider"]["id"]
        for b in c.get("/api/bots").json():
            c.patch(f"/api/bots/{b['id']}", json={"provider_id": pid, "model": "botly-mock"})

        chat = c.get("/api/chats").json()[0]
        chat_id = chat["id"]
        thread_id = c.get(f"/api/chats/{chat_id}").json().get("main_thread_id")

    # 直接插消息，绕开执行队列
    with SessionLocal() as db:
        existing = db.execute(
            text("SELECT COUNT(*) FROM messages WHERE thread_id = :t"), {"t": thread_id}
        ).scalar()
        if existing < n_messages:
            real_uid = db.execute(text("SELECT id FROM users LIMIT 1")).scalar()
            rows = [
                Message(
                    user_id=real_uid,
                    thread_id=thread_id,
                    seq=i + 1,
                    sender_type="user" if i % 2 else "bot",
                    type="text",
                    content=(
                        f"第 {i + 1} 条压测消息。这是一段用于首屏拉取延迟测试的较长正文，"
                        "内容长度会影响序列化与网络传输，用来模拟真实群聊里的一条普通回复。"
                    ),
                )
                for i in range(n_messages)
            ]
            db.add_all(rows)
            db.execute(
                text("UPDATE threads SET seq_counter = :n WHERE id = :t"),
                {"n": n_messages, "t": thread_id},
            )
            db.commit()
    return chat_id


async def _load(base: str, chat_id: str, concurrency: int, requests: int) -> None:
    limits = httpx.Limits(max_connections=concurrency + 5, max_keepalive_connections=concurrency)
    sem = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(base_url=base, timeout=120, limits=limits, headers=HEADERS) as client:
        await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})

        async def timed(fn):
            async with sem:
                t0 = time.perf_counter()
                try:
                    r = await fn()
                    return (time.perf_counter() - t0) * 1000, r.status_code < 400
                except Exception:  # noqa: BLE001
                    return (time.perf_counter() - t0) * 1000, False

        async def run(name: str, fn, n: int):
            results = await asyncio.gather(*(timed(fn) for _ in range(n)))
            lat = sorted(r[0] for r in results)
            ok = sum(1 for r in results if r[1])
            p50 = statistics.median(lat)
            p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))]
            p99 = lat[min(len(lat) - 1, int(len(lat) * 0.99))]
            total = sum(lat) / 1000
            print(
                f"{name}: 请求 {n} 成功 {ok}/{n} | "
                f"p50 {p50:.1f}ms p95 {p95:.1f}ms p99 {p99:.1f}ms | "
                f"{n / max(total, 1e-9):.1f} req/s"
            )

        await run(
            "首屏消息 GET /messages?limit=50",
            lambda: client.get(f"/api/chats/{chat_id}/messages", params={"limit": 50}),
            requests,
        )
        await run("群聊列表 GET /chats", lambda: client.get("/api/chats"), requests // 4)
        # 同群聊 CHT-03 只允许一个执行，并发发送会部分 409，属预期串行化
        await run(
            "发送消息 POST /messages",
            lambda: client.post(f"/api/chats/{chat_id}/messages", json={"content": "压测发送消息"}),
            max(10, requests // 20),
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.getenv("LOADTEST_BASE", "http://127.0.0.1:8200"))
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--requests", type=int, default=200)
    ap.add_argument("--messages", type=int, default=1000)
    args = ap.parse_args()

    print(f"==> 播种 {args.messages} 条消息 ...")
    chat_id = _seed(args.base, args.messages)
    print(f"==> 压测 chat={chat_id} concurrency={args.concurrency} requests={args.requests} ...")
    asyncio.run(_load(args.base, chat_id, args.concurrency, args.requests))


if __name__ == "__main__":
    main()

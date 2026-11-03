"""独立测试环境多用户验收：隔离、首屏性能、真实 Worker 执行。

启动同一测试数据库的 API 和 Worker 后执行：
python -m scripts.loadtest --base http://127.0.0.1:8200 --users 50 --concurrency 20
仅使用 mock 模型；每次创建唯一测试账号，不覆盖已有数据。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import select

from app.core.db import init_db, session_scope
from app.core.models import InviteCode, Message, Run, Thread

HEADERS = {"X-Botly-Client": "web"}
PASSWORD = "loadtest-password-123"


async def verify(args) -> dict:
    init_db()
    prefix = "loadtest-" + uuid4().hex[:10]
    invite = prefix.upper()
    with session_scope() as db:
        db.add(InviteCode(code=invite, max_uses=args.users, used_count=0))
    clients = [httpx.AsyncClient(base_url=args.base, headers=HEADERS, timeout=60) for _ in range(args.users)]
    workspaces = []
    report = {"prefix": prefix, "users": args.users, "concurrency": args.concurrency, "performance": {}}
    sem = asyncio.Semaphore(args.concurrency)
    try:
        for i, client in enumerate(clients):
            r = await client.post("/api/auth/register", json={"email": f"{prefix}-{i}@example.dev",
                "password": PASSWORD, "invite_code": invite, "name": f"压测用户 {i}"})
            r.raise_for_status()
            account = r.json()
            r = await client.post("/api/providers", json={"vendor_id": "mock", "kind": "mock"})
            r.raise_for_status()
            provider = r.json()["provider"]["id"]
            bots = (await client.get("/api/bots")).json()
            for bot in bots:
                r = await client.patch(f"/api/bots/{bot['id']}", json={"provider_id": provider,
                    "model": "botly-mock", "tools": []})
                r.raise_for_status()
            chat = (await client.get(f"/api/chats/{account['chat_id']}")).json()
            workspaces.append({"user": account["id"], "chat": chat["id"], "thread": chat["main_thread_id"],
                "bot": bots[1]["id"], "name": bots[1]["name"], "provider": provider})
        with session_scope() as db:
            for i, w in enumerate(workspaces):
                db.add_all([Message(user_id=w["user"], thread_id=w["thread"], seq=j + 1,
                    sender_type="user", type="text", content=f"{prefix} 用户 {i} 的消息 {j}")
                    for j in range(args.messages)])
                db.get(Thread, w["thread"]).seq_counter = args.messages

        async def timed(i, path):
            async with sem:
                started = time.perf_counter()
                response = await clients[i].get(path)
                response.raise_for_status()
                return (time.perf_counter() - started) * 1000

        for label, make_path in (
            ("messages", lambda w: f"/api/chats/{w['chat']}/messages?limit=50"),
            ("chats", lambda w: "/api/chats"),
        ):
            started = time.perf_counter()
            lat = sorted(await asyncio.gather(*(timed(i % args.users, make_path(workspaces[i % args.users]))
                for i in range(args.requests))))
            elapsed = time.perf_counter() - started
            report["performance"][label] = {"requests": len(lat), "p50_ms": round(statistics.median(lat), 1),
                "p95_ms": round(lat[min(len(lat) - 1, int(len(lat) * .95))], 1),
                "p99_ms": round(lat[min(len(lat) - 1, int(len(lat) * .99))], 1),
                "requests_per_second": round(len(lat) / elapsed, 1)}

        checks = 0
        for i, client in enumerate(clients):
            foreign = workspaces[(i + 1) % args.users]
            if args.users > 1:
                for path in (f"/api/chats/{foreign['chat']}", f"/api/chats/{foreign['chat']}/messages",
                    f"/api/bots/{foreign['bot']}", f"/api/providers/{foreign['provider']}"):
                    r = await client.patch(path, json={"name": "不能修改"}) if "/providers/" in path else await client.get(path)
                    assert r.status_code == 404, (path, r.status_code, r.text)
                    checks += 1
            rows = (await client.get("/api/chats")).json()
            assert {row["id"] for row in rows} == {workspaces[i]["chat"]}
            assert rows[0]["member_count"] == 3
            metrics = (await client.get("/api/metrics")).text
            assert "botly_users_total 1" in metrics
            checks += 2

        async def submit(i):
            async with sem:
                w = workspaces[i]
                r = await clients[i].post(f"/api/chats/{w['chat']}/messages",
                    json={"content": f"@{w['name']} 用户 {i} 并发测试：请直接回答"})
                r.raise_for_status()
                return r.json()["run"]["id"]

        run_ids = await asyncio.gather(*(submit(i) for i in range(args.users)))
        peak, completed = 0, set()
        started = time.monotonic()
        while len(completed) < len(run_ids):
            if time.monotonic() - started > args.timeout:
                raise AssertionError(f"执行超时：{len(completed)}/{len(run_ids)} 完成")
            with session_scope() as db:
                rows = list(db.scalars(select(Run).where(Run.id.in_(run_ids))))
                peak = max(peak, sum(r.status == "running" for r in rows))
                assert peak <= args.concurrency, f"并发上限失效：{peak}"
                failed = [r for r in rows if r.status in ("failed", "stopped")]
                assert not failed, [(r.id, r.end_reason) for r in failed]
                completed = {r.id for r in rows if r.status == "completed"}
            await asyncio.sleep(.1)
        for i, client in enumerate(clients):
            own = (await client.get(f"/api/runs/{run_ids[i]}")).json()
            assert own["status"] == "completed"
            if args.users > 1:
                r = await client.get(f"/api/runs/{run_ids[(i + 1) % args.users]}")
                assert r.status_code == 404
                checks += 1
        report.update({"isolation_checks": checks, "completed_runs": len(completed),
            "peak_running": peak, "run_elapsed_seconds": round(time.monotonic() - started, 1), "passed": True})
        return report
    finally:
        await asyncio.gather(*(c.aclose() for c in clients))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8200")
    parser.add_argument("--users", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--messages", type=int, default=1000)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if min(args.users, args.concurrency, args.requests, args.messages) < 1:
        parser.error("数量必须大于 0")
    report = asyncio.run(verify(args))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

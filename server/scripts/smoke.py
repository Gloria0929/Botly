"""端到端冒烟测试：注册 → 配置模型 → 发消息 → 计划确认 → 派活 → 完成。

用法：python -m scripts.smoke [--base http://127.0.0.1:8100]
"""
from __future__ import annotations

import argparse
import sys
import time

import httpx

INVITE = None


def j(resp: httpx.Response) -> dict:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return {"raw": resp.text}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8100")
    ap.add_argument("--invite", default=None)
    ap.add_argument("--email", default="demo@botly.local")
    ap.add_argument("--password", default="botly-demo-2026")
    args = ap.parse_args()

    c = httpx.Client(base_url=args.base, headers={"X-Botly-Client": "web"}, timeout=30)

    me = c.get("/api/auth/me")
    if me.status_code == 401:
        invite = args.invite or input("邀请码：").strip()
        r = c.post(
            "/api/auth/register",
            json={
                "email": args.email,
                "password": args.password,
                "invite_code": invite,
                "name": "Demo",
            },
        )
        if r.status_code >= 400:
            lr = c.post("/api/auth/login", json={"email": args.email, "password": args.password})
            if lr.status_code >= 400:
                print("注册失败", j(r))
                sys.exit(1)
            print("✓ 登录成功", j(lr)["email"])
        else:
            print("✓ 注册成功", j(r)["email"])
    else:
        print("✓ 已登录", j(me)["email"])

    # 1. 模型接入（mock，无需真实 Key）
    provs = j(c.get("/api/providers"))
    if not provs:
        r = c.post("/api/providers", json={"vendor_id": "mock", "kind": "mock"})
        print("✓ 创建 mock 模型接入", r.status_code)
        provs = j(c.get("/api/providers"))
    pid = provs[0]["id"]

    bots = j(c.get("/api/bots"))
    for b in bots:
        if not b["provider_id"]:
            c.patch(
                f"/api/bots/{b['id']}",
                json={"provider_id": pid, "model": "botly-mock"},
            )
    print(f"✓ 已绑定 {len(bots)} 个 Bot 到模型接入")

    chats = j(c.get("/api/chats"))
    if not chats:
        r = c.post("/api/chats", json={"name": "市场调研", "member_ids": [b["id"] for b in bots]})
        chat_id = j(r)["id"]
    else:
        chat_id = chats[0]["id"]
    print(f"✓ 群聊 {chat_id}")

    # 2. 发消息触发执行
    r = c.post(
        f"/api/chats/{chat_id}/messages",
        json={"content": "调研 3 家国内向量数据库厂商，写一份 1500 字对比报告。"},
    )
    if r.status_code >= 400:
        print("发消息失败", j(r))
        sys.exit(1)
    run_id = j(r)["run"]["id"]
    print(f"✓ 已创建执行 {run_id}")

    seen = set()
    deadline = time.time() + 120
    approved_plan = False
    approved_approval = False
    while time.time() < deadline:
        run = j(c.get(f"/api/runs/{run_id}"))
        status = run.get("status")
        for m in j(c.get(f"/api/chats/{chat_id}/messages?limit=100"))["messages"]:
            if m["id"] in seen:
                continue
            seen.add(m["id"])
            t = m["type"]
            who = m["sender_type"]
            body = (m["content"] or "").replace("\n", " ")[:70]
            if t == "tool_call":
                names = [x["function"]["name"] for x in m["meta"].get("tool_calls", [])]
                print(f"  · [工具] {names}")
            elif t == "tool_result":
                print(f"    ↳ {body}")
            elif t in ("text",):
                print(f"  · [{who}] {body}")
            elif t == "plan":
                print(f"  · [计划卡] {len(m['meta'].get('steps', []))} 步")
            elif t == "task":
                print(f"  · [任务卡] → {m['meta'].get('bot_name')}")
            elif t == "task_done":
                print(f"  · [完成卡] {body}")
            elif t == "approval":
                print(f"  · [审批卡] {m['meta'].get('tool')} ({m['meta'].get('status')})")
            elif t == "system":
                print(f"  · [系统] {body}")
            elif t == "limit":
                print(f"  · [限额] {body}")

        resume = run.get("resume") or {}
        if status == "waiting" and resume.get("kind") == "plan" and not approved_plan:
            r = c.post(f"/api/runs/{run_id}/plan/approve", json={})
            print("  → 批准计划", r.status_code)
            approved_plan = True
        elif status == "waiting" and resume.get("kind") == "approval" and not approved_approval:
            approvals = j(c.get("/api/approvals"))
            pending = [a for a in approvals if a["status"] == "pending"]
            if pending:
                r = c.post(f"/api/approvals/{pending[0]['id']}/approve")
                print("  → 批准工具调用", r.status_code)
                approved_approval = True
        elif status in ("completed", "failed", "stopped"):
            print(f"\n最终状态：{status} — {run.get('end_reason')}")
            break
        time.sleep(0.6)
    else:
        print("\n⚠ 超时，未进入结束状态")

    files = j(c.get(f"/api/chats/{chat_id}/files"))
    print("产物：", [f["path"] for f in files])
    print("token 用量：", j(c.get(f"/api/runs/{run_id}")).get("tokens_used"))


if __name__ == "__main__":
    main()

"""用量与费用统计（LIM-05 / MOD-05）。

数据源是 `usage_logs`（每次模型调用一条）与 `runs`（执行状态、步数）。
单价来自模型接入里每个模型的 `input_price` / `output_price`（每百万 token），
没填单价的模型仍然统计 token，但不计入费用，并在返回里给出未计费的 token 量。
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import Bot, Chat, Provider, Run, UsageLog, User, now

router = APIRouter(prefix="/usage", tags=["usage"])

M = 1_000_000


def _price_map(db: Session, user_id: str) -> dict[tuple[str, str], tuple[float, float] | None]:
    """(provider_id, model) → (输入单价, 输出单价)，未填单价时为 None。"""
    out: dict[tuple[str, str], tuple[float, float] | None] = {}
    for p in db.scalars(select(Provider).where(Provider.user_id == user_id)).all():
        for m in p.models or []:
            pin = m.get("input_price")
            pout = m.get("output_price")
            priced = pin is not None or pout is not None
            out[(p.id, m.get("name") or "")] = (
                (float(pin or 0), float(pout or 0)) if priced else None
            )
    return out


def _cost(tin: int, tout: int, price: tuple[float, float] | None) -> float:
    if not price:
        return 0.0
    return tin / M * price[0] + tout / M * price[1]


@router.get("/summary")
def usage_summary(
    days: int = Query(default=30, ge=1, le=365),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    since = now() - timedelta(days=days)
    prices = _price_map(db, user.id)

    rows = db.execute(
        select(
            UsageLog.bot_id,
            UsageLog.provider_id,
            UsageLog.model,
            func.sum(UsageLog.input_tokens).label("tin"),
            func.sum(UsageLog.output_tokens).label("tout"),
            func.count(UsageLog.id).label("calls"),
            func.sum(case((UsageLog.error.is_not(None), 1), else_=0)).label("errors"),
        )
        .where(UsageLog.user_id == user.id, UsageLog.created_at >= since)
        .group_by(UsageLog.bot_id, UsageLog.provider_id, UsageLog.model)
    ).all()

    providers = {
        p.id: p
        for p in db.scalars(select(Provider).where(Provider.user_id == user.id)).all()
    }
    bot_ids = {r.bot_id for r in rows if r.bot_id}
    bots = {
        b.id: b
        for b in db.scalars(select(Bot).where(Bot.user_id == user.id, Bot.id.in_(bot_ids))).all()
    } if bot_ids else {}

    totals = {"tin": 0, "tout": 0, "calls": 0, "errors": 0, "cost": 0.0, "unpriced": 0}
    by_bot: dict[str, dict] = {}
    by_model: dict[tuple[str, str], dict] = {}

    for r in rows:
        tin, tout = int(r.tin or 0), int(r.tout or 0)
        calls, errors = int(r.calls or 0), int(r.errors or 0)
        price = prices.get((r.provider_id, r.model or ""))
        cost = _cost(tin, tout, price)
        totals["tin"] += tin
        totals["tout"] += tout
        totals["calls"] += calls
        totals["errors"] += errors
        totals["cost"] += cost
        if price is None:
            totals["unpriced"] += tin + tout

        bot = bots.get(r.bot_id or "")
        b = by_bot.setdefault(
            r.bot_id or "",
            {
                "bot_id": r.bot_id,
                "name": bot.name if bot else "（未知 Bot）",
                "avatar": bot.avatar if bot else "bot",
                "input_tokens": 0,
                "output_tokens": 0,
                "tokens": 0,
                "calls": 0,
                "cost": 0.0,
            },
        )
        b["input_tokens"] += tin
        b["output_tokens"] += tout
        b["tokens"] += tin + tout
        b["calls"] += calls
        b["cost"] += cost

        key = (r.provider_id or "", r.model or "")
        m = by_model.setdefault(
            key,
            {
                "provider_id": r.provider_id,
                "provider": providers[r.provider_id].name if r.provider_id in providers else "",
                "model": r.model or "",
                "tokens": 0,
                "calls": 0,
                "errors": 0,
                "cost": 0.0,
                "priced": price is not None,
            },
        )
        m["tokens"] += tin + tout
        m["calls"] += calls
        m["errors"] += errors
        m["cost"] += cost

    # 按群聊：usage_logs → runs → chats
    chat_rows = db.execute(
        select(
            Run.chat_id,
            func.sum(UsageLog.input_tokens),
            func.sum(UsageLog.output_tokens),
        )
        .join(Run, Run.id == UsageLog.run_id)
        .where(UsageLog.user_id == user.id, UsageLog.created_at >= since)
        .group_by(Run.chat_id)
    ).all()
    chat_tokens = {
        cid: (int(tin or 0), int(tout or 0)) for cid, tin, tout in chat_rows if cid
    }
    chat_runs = dict(
        db.execute(
            select(Run.chat_id, func.count(Run.id))
            .where(Run.user_id == user.id, Run.created_at >= since)
            .group_by(Run.chat_id)
        ).all()
    )
    chat_names = {
        c.id: c.name
        for c in db.scalars(select(Chat).where(Chat.user_id == user.id)).all()
    }
    by_chat = []
    for cid in set(chat_tokens) | set(chat_runs):
        tin, tout = chat_tokens.get(cid, (0, 0))
        # 群聊维度的费用按该群聊下各模型的 token 无法精确拆分，这里用总量与总费用比例估算
        by_chat.append(
            {
                "chat_id": cid,
                "name": chat_names.get(cid, "（已删除群聊）"),
                "runs": int(chat_runs.get(cid) or 0),
                "tokens": tin + tout,
                "cost": 0.0,
            }
        )

    # 按日期，补齐没有调用的日期，前端图表不需要自己补空
    day_rows = db.execute(
        select(
            func.date(UsageLog.created_at).label("day"),
            UsageLog.provider_id,
            UsageLog.model,
            func.sum(UsageLog.input_tokens).label("tin"),
            func.sum(UsageLog.output_tokens).label("tout"),
            func.count(UsageLog.id).label("calls"),
        )
        .where(UsageLog.user_id == user.id, UsageLog.created_at >= since)
        .group_by(func.date(UsageLog.created_at), UsageLog.provider_id, UsageLog.model)
    ).all()
    by_day: dict[str, list[int | float]] = {}
    for r in day_rows:
        key = str(r.day)[:10]
        slot = by_day.setdefault(key, [0, 0.0, 0])
        tin, tout = int(r.tin or 0), int(r.tout or 0)
        slot[0] += tin + tout
        slot[1] += _cost(tin, tout, prices.get((r.provider_id, r.model or "")))
        slot[2] += int(r.calls or 0)
    daily = []
    for i in range(days - 1, -1, -1):
        d = (now() - timedelta(days=i)).strftime("%Y-%m-%d")
        tokens, cost, calls = by_day.get(d, [0, 0.0, 0])
        daily.append({"date": d, "tokens": tokens, "cost": round(cost, 6), "calls": calls})

    # 执行维度
    run_rows = db.execute(
        select(
            Run.status,
            func.count(Run.id),
            func.sum(Run.steps_used),
            func.sum(Run.tokens_used),
        )
        .where(Run.user_id == user.id, Run.created_at >= since)
        .group_by(Run.status)
    ).all()
    status_count = {s: int(n or 0) for s, n, _, _ in run_rows}
    run_count = sum(status_count.values())
    steps_total = sum(int(st or 0) for _, _, st, _ in run_rows)
    run_tokens = sum(int(tk or 0) for _, _, _, tk in run_rows)

    run_cost_rows = db.execute(
        select(
            UsageLog.run_id,
            UsageLog.provider_id,
            UsageLog.model,
            func.sum(UsageLog.input_tokens),
            func.sum(UsageLog.output_tokens),
        )
        .where(UsageLog.user_id == user.id, UsageLog.created_at >= since)
        .group_by(UsageLog.run_id, UsageLog.provider_id, UsageLog.model)
    ).all()
    cost_by_run: dict[str, float] = {}
    for rid, pid, model, tin, tout in run_cost_rows:
        if not rid:
            continue
        cost_by_run[rid or ""] = cost_by_run.get(rid or "", 0.0) + _cost(
            int(tin or 0), int(tout or 0), prices.get((pid, model or ""))
        )

    # 费用按群聊分摊：用该群聊的 run 费用汇总
    run_chat = dict(
        db.execute(
            select(Run.id, Run.chat_id).where(Run.user_id == user.id, Run.created_at >= since)
        ).all()
    )
    for cid, cost in [
        (run_chat.get(rid), c) for rid, c in cost_by_run.items() if rid in run_chat
    ]:
        for item in by_chat:
            if item["chat_id"] == cid:
                item["cost"] += cost

    recent = db.scalars(
        select(Run)
        .where(Run.user_id == user.id, Run.created_at >= since)
        .order_by(Run.created_at.desc())
        .limit(12)
    ).all()
    recent_runs = [
        {
            "run_id": r.id,
            "chat_id": r.chat_id,
            "chat_name": chat_names.get(r.chat_id, "（已删除群聊）"),
            "status": r.status,
            "steps_used": r.steps_used,
            "tokens": r.tokens_used,
            "cost": round(cost_by_run.get(r.id, 0.0), 6),
            "trigger": _trigger_preview(db, r),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "ended_at": r.ended_at.isoformat() if r.ended_at else None,
        }
        for r in recent
    ]

    return {
        "days": days,
        "since": since.isoformat(),
        "totals": {
            "input_tokens": totals["tin"],
            "output_tokens": totals["tout"],
            "tokens": totals["tin"] + totals["tout"],
            "cost": round(totals["cost"], 6),
            "calls": totals["calls"],
            "errors": totals["errors"],
            "error_rate": round(totals["errors"] / totals["calls"], 4) if totals["calls"] else 0.0,
            "runs": run_count,
            "completed": status_count.get("completed", 0),
            "failed": status_count.get("failed", 0),
            "stopped": status_count.get("stopped", 0),
            "avg_steps": round(steps_total / run_count, 1) if run_count else 0.0,
            "avg_tokens": int(run_tokens / run_count) if run_count else 0,
            "unpriced_tokens": totals["unpriced"],
        },
        "by_bot": sorted(by_bot.values(), key=lambda x: x["tokens"], reverse=True),
        "by_model": sorted(by_model.values(), key=lambda x: x["tokens"], reverse=True),
        "by_chat": sorted(by_chat, key=lambda x: x["tokens"], reverse=True),
        "daily": daily,
        "recent_runs": recent_runs,
    }


def _trigger_preview(db: Session, run: Run) -> str:
    if not run.trigger_message_id:
        return ""
    from ..models import Message

    msg = db.get(Message, run.trigger_message_id)
    if not msg:
        return ""
    text = (msg.content or "").replace("\n", " ").strip()
    return text[:60] + ("…" if len(text) > 60 else "")

"""删除账号的管理员工具（ACC-07）。

用法：
    python -m scripts.purge_accounts --list
    python -m scripts.purge_accounts --restore user@example.com
    python -m scripts.purge_accounts --purge user@example.com --yes
    python -m scripts.purge_accounts --due

删除账号后数据保留 30 天。保留期内 `--restore` 可恢复；到期后 Worker 会自动
彻底清除，也可以用 `--purge` 立即清除。`--due` 手动触发一次到期清理。
"""
from __future__ import annotations

import argparse

from sqlalchemy import select

from app.accounts import purge_due_accounts, purge_user
from app.db import init_db, session_scope
from app.models import User, now


def _fmt(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC") if value else "-"


def _list(db) -> None:
    rows = db.scalars(
        select(User).where(User.status == "deleted").order_by(User.deleted_at)
    ).all()
    if not rows:
        print("没有待清理的账号")
        return
    print(f"{'邮箱':<32} {'删除时间':<20} {'彻底清除时间':<20} 状态")
    for u in rows:
        due = bool(u.purge_after and u.purge_after <= now())
        print(f"{u.email:<32} {_fmt(u.deleted_at):<20} {_fmt(u.purge_after):<20} {'已到期' if due else '保留中'}")


def _restore(db, email: str, force: bool) -> int:
    user = db.scalar(select(User).where(User.email == email.lower().strip()))
    if not user:
        print("账号不存在")
        return 2
    if user.status != "deleted":
        print(f"账号状态为 {user.status}，不需要恢复")
        return 1
    if user.purge_after and user.purge_after <= now() and not force:
        print("已超过 30 天保留期，拒绝恢复；确需恢复请加 --force（数据可能已被清理）")
        return 3
    user.status = "active"
    user.deleted_at = None
    user.purge_after = None
    print(f"已恢复 {user.email}")
    return 0


def _purge(db, email: str, yes: bool) -> int:
    user = db.scalar(select(User).where(User.email == email.lower().strip()))
    if not user:
        print("账号不存在")
        return 2
    if not yes:
        print(f"将彻底删除 {user.email} 的全部数据与文件，确认后加 --yes")
        return 1
    counts = purge_user(db, user)
    print(f"已彻底清除 {email}：" + "，".join(f"{k}={v}" for k, v in counts.items()))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="已删除账号的恢复与清理")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list", action="store_true", help="列出已删除、等待清理的账号")
    group.add_argument("--restore", metavar="EMAIL", help="恢复保留期内的账号")
    group.add_argument("--purge", metavar="EMAIL", help="立即彻底清除某个账号")
    group.add_argument("--due", action="store_true", help="立即清除所有到期的账号")
    parser.add_argument("--force", action="store_true", help="超过保留期也允许恢复")
    parser.add_argument("--yes", action="store_true", help="确认立即清除")
    args = parser.parse_args()

    init_db()
    with session_scope() as db:
        if args.restore:
            raise SystemExit(_restore(db, args.restore, args.force))
        if args.purge:
            raise SystemExit(_purge(db, args.purge, args.yes))
        if args.due:
            emails = purge_due_accounts(db)
            print(f"已清除 {len(emails)} 个到期账号" + (f"：{', '.join(emails)}" if emails else ""))
            return
        _list(db)


if __name__ == "__main__":
    main()

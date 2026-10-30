"""生成邀请码。

用法：
    python -m scripts.make_invite --uses 3 --days 14 --note "内测第 1 批"
    python -m scripts.make_invite --list
"""
from __future__ import annotations

import argparse
import secrets
import string
from datetime import timedelta

from app.db import init_db, session_scope
from app.models import InviteCode, now
from sqlalchemy import select

ALPHABET = string.ascii_uppercase + string.digits


def gen_code(prefix: str = "BOTLY") -> str:
    body = "".join(secrets.choice(ALPHABET) for _ in range(4))
    body2 = "".join(secrets.choice(ALPHABET) for _ in range(4))
    return f"{prefix}-{body}-{body2}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uses", type=int, default=1, help="可用次数")
    parser.add_argument("--days", type=int, default=30, help="有效天数")
    parser.add_argument("--note", type=str, default="", help="备注")
    parser.add_argument("--prefix", type=str, default="BOTLY")
    parser.add_argument("--list", action="store_true", help="列出已有邀请码")
    args = parser.parse_args()

    init_db()
    with session_scope() as db:
        if args.list:
            rows = db.scalars(select(InviteCode).order_by(InviteCode.created_at.desc())).all()
            if not rows:
                print("暂无邀请码")
            for r in rows:
                exp = r.expires_at.strftime("%Y-%m-%d") if r.expires_at else "永久"
                print(f"{r.code}\t已用 {r.used_count}/{r.max_uses}\t到期 {exp}\t{r.note or ''}")
            return

        code = InviteCode(
            code=gen_code(args.prefix),
            max_uses=args.uses,
            expires_at=now() + timedelta(days=args.days),
            note=args.note,
        )
        db.add(code)
        db.flush()
        print(code.code)


if __name__ == "__main__":
    main()

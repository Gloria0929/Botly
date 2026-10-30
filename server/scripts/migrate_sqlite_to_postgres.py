"""把业务表从 SQLite 迁移到 Postgres。

用法：
    cd server
    ./.venv/bin/python -m scripts.migrate_sqlite_to_postgres
    ./.venv/bin/python -m scripts.migrate_sqlite_to_postgres --force

说明：
- 只迁移业务表（`app.models` 里声明的表）。LangGraph 检查点不迁移：
  它只对「正在进行中」的执行有意义；迁移前请确认没有 queued/running/waiting 的执行。
- 目标库默认取 `DATABASE_URL`，源库默认 `data/botly.db`。
- 目标库已有数据时默认拒绝执行；`--force` 会先清空目标业务表。
"""
from __future__ import annotations

import argparse

from sqlalchemy import create_engine, func, select, text

from app import models  # noqa: F401  注册所有表
from app.config import DATA_DIR, DATABASE_URL
from app.db import Base


def _counts(engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            table.name: int(conn.scalar(select(func.count()).select_from(table)) or 0)
            for table in Base.metadata.sorted_tables
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", default=f"sqlite:///{DATA_DIR / 'botly.db'}")
    parser.add_argument("--postgres", default=DATABASE_URL)
    parser.add_argument("--force", action="store_true", help="清空目标业务表后重新迁移")
    args = parser.parse_args()

    if not args.postgres.startswith("postgresql"):
        raise SystemExit(f"目标不是 Postgres：{args.postgres}")
    source = create_engine(args.sqlite)
    target = create_engine(args.postgres)
    Base.metadata.create_all(target)

    before = _counts(target)
    if any(before.values()):
        if not args.force:
            raise SystemExit(
                "目标库已有数据，已中止。确认要覆盖请加 --force。当前："
                + ", ".join(f"{k}={v}" for k, v in before.items() if v)
            )
        names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        with target.begin() as conn:
            conn.exec_driver_sql(f"TRUNCATE {names} RESTART IDENTITY CASCADE")

    copied: dict[str, int] = {}
    with source.connect() as src, target.begin() as dst:
        for table in Base.metadata.sorted_tables:
            rows = [dict(row) for row in src.execute(select(table)).mappings()]
            if rows:
                dst.execute(table.insert(), rows)
            copied[table.name] = len(rows)

    # events.id 在 Postgres 是自增序列；显式插入 id 后必须把序列推到最大值之后
    with target.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if "id" not in table.c:
                continue
            seq = conn.scalar(text("SELECT pg_get_serial_sequence(:t, 'id')"), {"t": table.name})
            if not seq:
                continue
            max_id = conn.scalar(select(func.max(table.c.id))) or 0
            conn.execute(
                text("SELECT setval(:seq, :value, false)"),
                {"seq": seq, "value": int(max_id) + 1},
            )

    after = _counts(target)
    for name, n in copied.items():
        if after.get(name) != n:
            raise SystemExit(f"{name} 迁移数量不一致：源 {n}，目标 {after.get(name)}")
    print("迁移完成：" + ", ".join(f"{k}={v}" for k, v in copied.items() if v))


if __name__ == "__main__":
    main()

"""Alembic 迁移环境。

连接复用 app.db 的 engine（含 SQLite PRAGMA / 连接池设置），元数据来自
app.models.Base.metadata。用法（在 server/ 目录下）：

    ./venv/bin/alembic revision --autogenerate -m "描述"
    ./venv/bin/alembic upgrade head
    ./venv/bin/alembic downgrade -1
"""
from __future__ import annotations

import os
import sys

from alembic import context
from sqlalchemy import create_engine, pool

# 让 alembic 能 import 到 app 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import models  # noqa: E402,F401  确保所有模型注册到 metadata
from app.core.db import DATABASE_URL, Base, engine as app_engine  # noqa: E402

config = context.config
config.set_main_option("sqlalchemy.url", DATABASE_URL)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with app_engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import DATABASE_URL

IS_SQLITE = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30} if IS_SQLITE else {},
    pool_pre_ping=True,
    future=True,
)

if IS_SQLITE:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - 连接级设置
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


@contextmanager
def session_scope() -> Session:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db():
    """FastAPI 依赖：请求正常结束即提交，异常则回滚。"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401  确保模型已注册

    Base.metadata.create_all(bind=engine)
    _light_migrate()
    _migrate_bot_name_uniqueness()
    _ensure_message_search_index()


def _ensure_message_search_index() -> None:
    """CHT-08：给消息正文建三元组索引，让 ILIKE '%词%' 也能走索引。

    pg_trgm 扩展和索引分两步、各自独立兜底：托管数据库常常不给建扩展的权限，
    但扩展已存在时仍然能建索引，别让前者连累后者。两步都失败也只影响搜索
    速度，不影响正确性；SQLite 用不上，直接返回。
    """
    from sqlalchemy import text

    if IS_SQLITE:
        return
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    except Exception:  # noqa: BLE001
        pass
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_msg_content_trgm "
                    "ON messages USING gin (content gin_trgm_ops)"
                )
            )
    except Exception:  # noqa: BLE001
        pass


def _migrate_bot_name_uniqueness() -> None:
    """把 bots 的「user_id+name 全量唯一」改成「未删除才唯一」。

    之前的表级唯一约束会占住已删除 Bot 的名字，导致删除后无法复用该名字建新 Bot。
    改成部分唯一索引后，软删除依然保留历史消息引用，但名字可以复用。
    """
    from sqlalchemy import text

    if not IS_SQLITE:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE bots DROP CONSTRAINT IF EXISTS uq_bot_user_name"))
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_bot_user_name "
                    "ON bots (user_id, name) WHERE deleted_at IS NULL"
                )
            )
        return

    # SQLite：唯一约束是建表语句里的表级约束，无法单独删除，只能重建表。
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='bots'")
        ).fetchone()
    if not row or "CONSTRAINT uq_bot_user_name UNIQUE" not in (row[0] or ""):
        return
    _rebuild_sqlite_bots()


def _rebuild_sqlite_bots() -> None:
    """重建 bots 表：去掉表级唯一约束，改成「未删除才唯一」的部分唯一索引。"""
    import re

    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute("PRAGMA foreign_keys=OFF")

        cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='bots'")
        create_sql = cur.fetchone()[0]

        # 去掉唯一约束行，其余列定义原样保留（宽度、顺序都不变）
        new_sql = re.sub(r"CREATE TABLE bots\b", "CREATE TABLE bots_new", create_sql, count=1)
        new_sql = re.sub(
            r",\s*CONSTRAINT uq_bot_user_name UNIQUE \([^)]*\)",
            "",
            new_sql,
        )
        new_sql = re.sub(
            r"CONSTRAINT uq_bot_user_name UNIQUE \([^)]*\),?\s*",
            "",
            new_sql,
        )

        cur.execute("PRAGMA table_info(bots)")
        cols = [r[1] for r in cur.fetchall()]
        col_list = ", ".join(cols)

        cur.execute("BEGIN")
        cur.execute(new_sql)
        cur.execute(f"INSERT INTO bots_new ({col_list}) SELECT {col_list} FROM bots")
        cur.execute("DROP TABLE bots")
        cur.execute("ALTER TABLE bots_new RENAME TO bots")
        cur.execute(
            "CREATE UNIQUE INDEX uq_bot_user_name ON bots (user_id, name) "
            "WHERE deleted_at IS NULL"
        )
        cur.execute("CREATE INDEX IF NOT EXISTS ix_bots_user_id ON bots (user_id)")
        cur.execute("COMMIT")

        cur.execute("PRAGMA foreign_keys=ON")
        raw.commit()
    finally:
        raw.close()


def _light_migrate() -> None:
    """极轻量的增量迁移：给已存在的表补新增列。

    第一版没有引入 Alembic；这里只处理「加列」这一种低风险变更，
    避免升级时要求用户删库重建。复杂变更仍应引入 Alembic。
    """
    from sqlalchemy import inspect, text

    wanted = {
        "providers": {"vendor_id": "VARCHAR(40)", "logo": "VARCHAR(500)"},
        "messages": {"reasoning_content": "TEXT"},
        "tasks": {"step_key": "VARCHAR(200)"},
        "runs": {"last_turn_message_id": "VARCHAR(32)", "last_interjection_seq": "INTEGER DEFAULT 0"},
        "users": {"deleted_at": "TIMESTAMP", "purge_after": "TIMESTAMP"},
        "threads": {"summary": "TEXT", "summary_upto_seq": "INTEGER DEFAULT 0"},
    }
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, columns in wanted.items():
            if table not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table)}
            for col, ddl in columns.items():
                if col not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))

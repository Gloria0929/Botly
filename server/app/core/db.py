from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import DATABASE_URL, DB_MAX_OVERFLOW, DB_POOL_SIZE

IS_SQLITE = DATABASE_URL.startswith("sqlite")

engine_options = {
    "connect_args": {"check_same_thread": False, "timeout": 30} if IS_SQLITE else {},
    "pool_pre_ping": True,
    "future": True,
}
if not IS_SQLITE:
    engine_options.update(pool_size=DB_POOL_SIZE, max_overflow=DB_MAX_OVERFLOW)
engine = create_engine(DATABASE_URL, **engine_options)

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
def session_scope():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


from fastapi import Request


def get_db(request: Request):
    """FastAPI 依赖：请求正常结束即提交，异常则回滚。

    注意：本依赖的提交发生在响应发出之后（yield 依赖的清理时机），不能
    单独依靠它保证“下一个请求能读到本次写入”；即时可见性由
    CommitBeforeResponseMiddleware 在响应发出前提交来保证。会话记在
    request.state 上，供该中间件取用。
    """
    db = SessionLocal()
    request.state.db_session = db
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    from app.core.config import APP_ENV

    if APP_ENV in {"production", "prod"}:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from sqlalchemy import text

        base = Path(__file__).resolve().parents[2]
        cfg = Config(str(base / "alembic.ini"))
        cfg.set_main_option("script_location", str(base / "alembic"))
        head = ScriptDirectory.from_config(cfg).get_current_head()
        with engine.connect() as conn:
            if not conn.dialect.has_table(conn, "alembic_version"):
                raise RuntimeError("数据库尚未迁移；请先执行 python -m app.migrate")
            current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        if current != head:
            raise RuntimeError(f"数据库迁移版本不匹配：当前 {current!r}，需要 {head!r}")
        return
    migrate_db()


def migrate_db() -> None:
    """仅部署阶段执行迁移，初始表、兼容旧库和回填均由 Alembic revision 管理。"""
    _upgrade_alembic()


def _upgrade_alembic() -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    base = Path(__file__).resolve().parents[2]
    cfg = Config(str(base / "alembic.ini"))
    cfg.set_main_option("script_location", str(base / "alembic"))
    cfg.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))
    # API、Worker 同时启动时让版本迁移串行；共用连接避免检查与升级间的竞态。
    with engine.begin() as conn:
        if IS_SQLITE:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            conn.execute(text("SELECT pg_advisory_xact_lock(782341993)"))
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")

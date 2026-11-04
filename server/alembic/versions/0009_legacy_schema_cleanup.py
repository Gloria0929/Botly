"""收敛旧版补列、回填和索引改造。"""
from alembic import op
import logging
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def col(name, type_, default=None):
    return sa.Column(name, type_, nullable=True, server_default=default)


COLUMNS = {
    "providers": {"vendor_id": col("vendor_id", sa.String(40)), "logo": col("logo", sa.String(500))},
    "messages": {"reasoning_content": col("reasoning_content", sa.Text())},
    "bots": {"skills": col("skills", sa.JSON()), "enabled": col("enabled", sa.Boolean(), sa.true())},
    "tasks": {"step_key": col("step_key", sa.String(200)), "batch": col("batch", sa.Integer(), "0")},
    "memories": {"source": col("source", sa.String(20), "'user'"), "run_id": col("run_id", sa.String(32)), "category": col("category", sa.String(30), "''")},
    "memory_candidates": {"category": col("category", sa.String(30), "''")},
    "runs": {"last_turn_message_id": col("last_turn_message_id", sa.String(32)), "last_interjection_seq": col("last_interjection_seq", sa.Integer(), "0"), "thread_id": col("thread_id", sa.String(32)), "parent_run_id": col("parent_run_id", sa.String(32)), "parent_task_id": col("parent_task_id", sa.String(32)), "worker_id": col("worker_id", sa.String(64))},
    "threads": {"summary": col("summary", sa.Text()), "summary_upto_seq": col("summary_upto_seq", sa.Integer(), "0"), "anchor_message_id": col("anchor_message_id", sa.String(32)), "seq_counter": col("seq_counter", sa.Integer(), "0")},
    "users": {"deleted_at": col("deleted_at", sa.DateTime()), "purge_after": col("purge_after", sa.DateTime()), "email_verified_at": col("email_verified_at", sa.DateTime()), "session_epoch": col("session_epoch", sa.Integer(), "0")},
    "user_settings": {"delegation_depth": col("delegation_depth", sa.Integer()), "approval_email_enabled": col("approval_email_enabled", sa.Boolean(), sa.true())},
    "notifications": {"dismissed_at": col("dismissed_at", sa.DateTime()), "email_status": col("email_status", sa.String(16)), "email_attempts": col("email_attempts", sa.Integer(), "0"), "email_next_attempt_at": col("email_next_attempt_at", sa.DateTime()), "email_lease_until": col("email_lease_until", sa.DateTime()), "email_last_error": col("email_last_error", sa.String(200))},
    "skills": {"source": col("source", sa.String(20), "'manual'"), "source_url": col("source_url", sa.String(500)), "files": col("files", sa.JSON())},
    "mcp_servers": {"transport": col("transport", sa.String(10), "'http'"), "command": col("command", sa.String(500)), "args": col("args", sa.JSON()), "env": col("env", sa.JSON())},
    "cube_connections": {"last_check_error": col("last_check_error", sa.String(500))},
    "cube_sessions": {"last_executed_at": col("last_executed_at", sa.DateTime()), "last_exit_code": col("last_exit_code", sa.Integer()), "pty_pid": col("pty_pid", sa.Integer()), "skills_manifest": col("skills_manifest", sa.JSON())},
}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table, columns in COLUMNS.items():
        if table not in inspector.get_table_names():
            continue
        existing = {column["name"] for column in inspector.get_columns(table)}
        for name, column in columns.items():
            if name not in existing:
                op.add_column(table, column)
                existing.add(name)

    bind.execute(sa.text(
        "UPDATE threads SET seq_counter = (SELECT COALESCE(MAX(seq), 0) FROM messages "
        "WHERE messages.thread_id = threads.id) WHERE seq_counter IS NULL OR seq_counter < "
        "(SELECT COALESCE(MAX(seq), 0) FROM messages WHERE messages.thread_id = threads.id)"
    ))

    inspector = sa.inspect(bind)
    constraints = inspector.get_unique_constraints("bots")
    indexes = {item["name"] for item in inspector.get_indexes("bots")}
    old_constraint = next((item["name"] for item in constraints if item["name"] == "uq_bot_user_name"), None)
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("ALTER TABLE bots DROP CONSTRAINT IF EXISTS uq_bot_user_name"))
        bind.execute(sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_bot_user_name ON bots (user_id, name) "
            "WHERE deleted_at IS NULL"
        ))
    elif old_constraint:
        with op.batch_alter_table("bots", recreate="always") as batch:
            batch.drop_constraint(old_constraint, type_="unique")
            batch.create_index("uq_bot_user_name", ["user_id", "name"], unique=True,
                               sqlite_where=sa.text("deleted_at IS NULL"))
    elif "uq_bot_user_name" not in indexes:
        op.create_index("uq_bot_user_name", "bots", ["user_id", "name"], unique=True,
                        sqlite_where=sa.text("deleted_at IS NULL"))

    if bind.dialect.name == "postgresql":
        try:
            # 扩展权限不足时回滚到保存点，不能让整个迁移事务进入 aborted 状态。
            with bind.begin_nested():
                bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
                bind.execute(sa.text(
                    "CREATE INDEX IF NOT EXISTS ix_msg_content_trgm ON messages USING gin "
                    "(content gin_trgm_ops)"
                ))
        except Exception as exc:
            logging.getLogger(__name__).warning("pg_trgm 搜索索引未启用：%s", type(exc).__name__)


def downgrade():
    pass

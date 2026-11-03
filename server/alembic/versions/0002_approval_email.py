"""审批邮件持久队列与用户开关。

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    wanted = {
        "user_settings": [sa.Column("approval_email_enabled", sa.Boolean(), server_default=sa.true())],
        "notifications": [
            sa.Column("dismissed_at", sa.DateTime()),
            sa.Column("email_status", sa.String(16)),
            sa.Column("email_attempts", sa.Integer(), server_default="0"),
            sa.Column("email_next_attempt_at", sa.DateTime()),
            sa.Column("email_lease_until", sa.DateTime()),
            sa.Column("email_last_error", sa.String(200)),
        ],
    }
    inspector = sa.inspect(op.get_bind())
    for table, columns in wanted.items():
        have = {column["name"] for column in inspector.get_columns(table)}
        for column in columns:
            if column.name not in have:
                op.add_column(table, column)
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("notifications")}
    if "ix_notifications_email_status" not in indexes:
        op.create_index("ix_notifications_email_status", "notifications", ["email_status"])


def downgrade():
    op.drop_index("ix_notifications_email_status", table_name="notifications")
    with op.batch_alter_table("notifications") as table:
        for column in ("email_last_error", "email_lease_until", "email_next_attempt_at", "email_attempts", "email_status", "dismissed_at"):
            table.drop_column(column)
    with op.batch_alter_table("user_settings") as table:
        table.drop_column("approval_email_enabled")

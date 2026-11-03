"""Cube 连接验证结果与实际执行记录。"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    columns = {
        "cube_connections": [sa.Column("last_check_error", sa.String(500))],
        "cube_sessions": [sa.Column("last_executed_at", sa.DateTime()), sa.Column("last_exit_code", sa.Integer())],
    }
    inspector = sa.inspect(op.get_bind())
    for table, wanted in columns.items():
        existing = {column["name"] for column in inspector.get_columns(table)}
        for column in wanted:
            if column.name not in existing:
                op.add_column(table, column)


def downgrade():
    with op.batch_alter_table("cube_sessions") as table:
        table.drop_column("last_exit_code")
        table.drop_column("last_executed_at")
    with op.batch_alter_table("cube_connections") as table:
        table.drop_column("last_check_error")

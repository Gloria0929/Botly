"""保存 Cube 交互式终端 PID，重连时复用同一个 shell。"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    if "pty_pid" not in {column["name"] for column in sa.inspect(op.get_bind()).get_columns("cube_sessions")}:
        op.add_column("cube_sessions", sa.Column("pty_pid", sa.Integer()))


def downgrade():
    with op.batch_alter_table("cube_sessions") as table:
        table.drop_column("pty_pid")

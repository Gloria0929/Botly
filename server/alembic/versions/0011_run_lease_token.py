"""添加执行租约 fencing token。"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    columns = {row["name"] for row in sa.inspect(op.get_bind()).get_columns("runs")}
    if "lease_token" not in columns:
        op.add_column(
            "runs",
            sa.Column("lease_token", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade():
    columns = {row["name"] for row in sa.inspect(op.get_bind()).get_columns("runs")}
    if "lease_token" in columns:
        with op.batch_alter_table("runs") as batch:
            batch.drop_column("lease_token")

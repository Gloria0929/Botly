"""自由群聊、人设与轻量发言判断。"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

FIELDS = {
    "chats": [("mode", sa.String(8), "task"), ("max_speakers", sa.Integer(), "3"),
              ("max_rounds", sa.Integer(), "2"), ("muted", sa.Boolean(), sa.false())],
    "bots": [("persona", sa.Text(), ""), ("proactivity", sa.Float(), "0.5")],
    "runs": [("kind", sa.String(12), "task"), ("round", sa.Integer(), "0")],
}


def upgrade():
    for table, fields in FIELDS.items():
        columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}
        for name, type_, default in fields:
            if name not in columns:
                op.add_column(table, sa.Column(name, type_, nullable=False, server_default=default))
    indexes = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("runs")}
    if "ix_runs_kind" not in indexes:
        op.create_index("ix_runs_kind", "runs", ["kind"])


def downgrade():
    op.drop_index("ix_runs_kind", table_name="runs")
    for table, fields in reversed(list(FIELDS.items())):
        with op.batch_alter_table(table) as batch:
            for name, _, _ in reversed(fields):
                batch.drop_column(name)

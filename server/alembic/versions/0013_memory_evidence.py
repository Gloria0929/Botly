"""记忆候选保存可核验的用户原话依据，并默认等待足够重复证据。"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("memory_candidates")}
    if "evidence" not in columns:
        op.add_column("memory_candidates", sa.Column("evidence", sa.JSON(), nullable=True))
    # 旧版候选没有用户原话凭证；保留内容及 ID，重新积累足量凭证前不向用户展示。
    op.execute(sa.text(
        "UPDATE memory_candidates SET status = 'observing' "
        "WHERE status = 'pending' AND evidence IS NULL"
    ))


def downgrade():
    op.execute(sa.text(
        "UPDATE memory_candidates SET status = 'pending' WHERE status = 'observing'"
    ))
    op.drop_column("memory_candidates", "evidence")

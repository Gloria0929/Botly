"""索引化 SSE 事件游标查询。"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    indexes = {row["name"] for row in sa.inspect(op.get_bind()).get_indexes("events")}
    if "ix_events_user_chat_id" not in indexes:
        op.create_index("ix_events_user_chat_id", "events", ["user_id", "chat_id", "id"], unique=False)


def downgrade():
    op.drop_index("ix_events_user_chat_id", table_name="events")

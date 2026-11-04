"""强制每条线程消息序号唯一。"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    duplicates = bind.execute(sa.text(
        "SELECT thread_id, seq FROM messages GROUP BY thread_id, seq HAVING COUNT(*) > 1 LIMIT 1"
    )).first()
    if duplicates:
        raise RuntimeError(
            f"消息序号重复，无法添加唯一约束：thread_id={duplicates[0]} seq={duplicates[1]}"
        )
    indexes = {row["name"] for row in sa.inspect(bind).get_indexes("messages")}
    constraints = {row["name"] for row in sa.inspect(bind).get_unique_constraints("messages")}
    if "uq_msg_thread_seq" not in indexes and "uq_msg_thread_seq" not in constraints:
        op.create_index("uq_msg_thread_seq", "messages", ["thread_id", "seq"], unique=True)


def downgrade():
    indexes = {row["name"] for row in sa.inspect(op.get_bind()).get_indexes("messages")}
    if "uq_msg_thread_seq" in indexes:
        op.drop_index("uq_msg_thread_seq", table_name="messages")

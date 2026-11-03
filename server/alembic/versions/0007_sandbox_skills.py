"""记录 Cube 沙箱中已安装的技能文件，按版本增量同步。"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("cube_sessions")}
    if "skills_manifest" not in columns:
        op.add_column("cube_sessions", sa.Column("skills_manifest", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("cube_sessions") as table:
        table.drop_column("skills_manifest")

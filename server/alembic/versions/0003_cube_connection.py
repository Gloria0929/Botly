"""用户 Cube 后端配置及持久沙箱会话。"""
from alembic import op
from app.core.models import CubeConnection, CubeSession

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    CubeConnection.__table__.create(op.get_bind(), checkfirst=True)
    CubeSession.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    CubeSession.__table__.drop(op.get_bind(), checkfirst=True)
    CubeConnection.__table__.drop(op.get_bind(), checkfirst=True)

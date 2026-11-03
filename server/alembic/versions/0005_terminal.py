"""沙箱终端命令记录。"""
from alembic import op
from app.core.models import TerminalCommand

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    TerminalCommand.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    TerminalCommand.__table__.drop(op.get_bind(), checkfirst=True)

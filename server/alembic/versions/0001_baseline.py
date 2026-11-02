"""baseline —— 现有 schema 的版本标记点

Revision ID: 0001
Revises:
Create Date: 2026-11-02

本迁移不执行任何 DDL：当前表结构由 `Base.metadata.create_all` + `_light_migrate`
建立并已就绪。引入 Alembic 后，老库首次启动会执行 `stamp head` 把 alembic_version
标记到本基线；之后的 schema 变更在此之上新增 revision 即可。
"""
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

"""冻结初始表结构；旧库保留已有表，后续 revision 负责补列和回填。"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = set(inspector.get_table_names())
    if 'alerts' not in existing:
        op.create_table('alerts',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('kind', sa.String(length=40), nullable=False),
        sa.Column('level', sa.String(length=12), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('acknowledged_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
    if 'alerts' not in existing:
        op.create_index(op.f('ix_alerts_created_at'), 'alerts', ['created_at'], unique=False)
        op.create_index(op.f('ix_alerts_kind'), 'alerts', ['kind'], unique=False)
    if 'approvals' not in existing:
        op.create_table('approvals',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('run_id', sa.String(length=32), nullable=False),
        sa.Column('task_id', sa.String(length=32), nullable=True),
        sa.Column('bot_id', sa.String(length=32), nullable=False),
        sa.Column('tool', sa.String(length=64), nullable=False),
        sa.Column('args', sa.JSON(), nullable=False),
        sa.Column('args_hash', sa.String(length=64), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('level', sa.String(length=4), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('reject_reason', sa.Text(), nullable=True),
        sa.Column('consumed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
    if 'approvals' not in existing:
        op.create_index(op.f('ix_approvals_run_id'), 'approvals', ['run_id'], unique=False)
        op.create_index(op.f('ix_approvals_status'), 'approvals', ['status'], unique=False)
        op.create_index(op.f('ix_approvals_user_id'), 'approvals', ['user_id'], unique=False)
    if 'cube_sessions' not in existing:
        op.create_table('cube_sessions',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('chat_id', sa.String(length=32), nullable=False),
        sa.Column('sandbox_id', sa.String(length=200), nullable=False),
        sa.Column('connection_hash', sa.String(length=64), nullable=False),
        sa.Column('files_manifest', sa.JSON(), nullable=False),
        sa.Column('skills_manifest', sa.JSON(), nullable=False),
        sa.Column('last_executed_at', sa.DateTime(), nullable=True),
        sa.Column('last_exit_code', sa.Integer(), nullable=True),
        sa.Column('pty_pid', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
    if 'cube_sessions' not in existing:
        op.create_index(op.f('ix_cube_sessions_chat_id'), 'cube_sessions', ['chat_id'], unique=True)
        op.create_index(op.f('ix_cube_sessions_user_id'), 'cube_sessions', ['user_id'], unique=False)
    if 'email_tokens' not in existing:
        op.create_table('email_tokens',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('kind', sa.String(length=24), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('used_at', sa.DateTime(), nullable=True),
        sa.Column('ip', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
    if 'email_tokens' not in existing:
        op.create_index(op.f('ix_email_tokens_email'), 'email_tokens', ['email'], unique=False)
        op.create_index(op.f('ix_email_tokens_kind'), 'email_tokens', ['kind'], unique=False)
        op.create_index(op.f('ix_email_tokens_token_hash'), 'email_tokens', ['token_hash'], unique=True)
        op.create_index(op.f('ix_email_tokens_user_id'), 'email_tokens', ['user_id'], unique=False)
    if 'events' not in existing:
        op.create_table('events',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('chat_id', sa.String(length=32), nullable=True),
        sa.Column('type', sa.String(length=48), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
    if 'events' not in existing:
        op.create_index(op.f('ix_events_chat_id'), 'events', ['chat_id'], unique=False)
        op.create_index('ix_events_user_chat_id', 'events', ['user_id', 'chat_id', 'id'], unique=False)
        op.create_index(op.f('ix_events_user_id'), 'events', ['user_id'], unique=False)
    if 'files' not in existing:
        op.create_table('files',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('chat_id', sa.String(length=32), nullable=False),
        sa.Column('path', sa.String(length=500), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('created_by_bot_id', sa.String(length=32), nullable=True),
        sa.Column('run_id', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
    if 'files' not in existing:
        op.create_index(op.f('ix_files_chat_id'), 'files', ['chat_id'], unique=False)
        op.create_index(op.f('ix_files_user_id'), 'files', ['user_id'], unique=False)
    if 'invite_codes' not in existing:
        op.create_table('invite_codes',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('max_uses', sa.Integer(), nullable=False),
        sa.Column('used_count', sa.Integer(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('note', sa.String(length=200), nullable=True),
        sa.Column('created_by', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
    if 'invite_codes' not in existing:
        op.create_index(op.f('ix_invite_codes_code'), 'invite_codes', ['code'], unique=True)
    if 'login_attempts' not in existing:
        op.create_table('login_attempts',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('ip', sa.String(length=80), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
    if 'login_attempts' not in existing:
        op.create_index(op.f('ix_login_attempts_created_at'), 'login_attempts', ['created_at'], unique=False)
        op.create_index(op.f('ix_login_attempts_email'), 'login_attempts', ['email'], unique=False)
        op.create_index(op.f('ix_login_attempts_ip'), 'login_attempts', ['ip'], unique=False)
    if 'memories' not in existing:
        op.create_table('memories',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('scope', sa.String(length=12), nullable=False),
        sa.Column('bot_id', sa.String(length=32), nullable=True),
        sa.Column('title', sa.String(length=120), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('category', sa.String(length=30), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('run_id', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
    if 'memories' not in existing:
        op.create_index(op.f('ix_memories_run_id'), 'memories', ['run_id'], unique=False)
        op.create_index(op.f('ix_memories_user_id'), 'memories', ['user_id'], unique=False)
    if 'memory_candidates' not in existing:
        op.create_table('memory_candidates',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('run_id', sa.String(length=32), nullable=True),
        sa.Column('chat_id', sa.String(length=32), nullable=True),
        sa.Column('scope', sa.String(length=12), nullable=False),
        sa.Column('bot_id', sa.String(length=32), nullable=True),
        sa.Column('title', sa.String(length=120), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('category', sa.String(length=30), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
    if 'memory_candidates' not in existing:
        op.create_index(op.f('ix_memory_candidates_run_id'), 'memory_candidates', ['run_id'], unique=False)
        op.create_index(op.f('ix_memory_candidates_status'), 'memory_candidates', ['status'], unique=False)
        op.create_index(op.f('ix_memory_candidates_user_id'), 'memory_candidates', ['user_id'], unique=False)
    if 'notifications' not in existing:
        op.create_table('notifications',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('type', sa.String(length=32), nullable=False),
        sa.Column('target_type', sa.String(length=32), nullable=True),
        sa.Column('target_id', sa.String(length=32), nullable=True),
        sa.Column('chat_id', sa.String(length=32), nullable=True),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('read_at', sa.DateTime(), nullable=True),
        sa.Column('dismissed_at', sa.DateTime(), nullable=True),
        sa.Column('email_status', sa.String(length=16), nullable=True),
        sa.Column('email_attempts', sa.Integer(), nullable=False),
        sa.Column('email_next_attempt_at', sa.DateTime(), nullable=True),
        sa.Column('email_lease_until', sa.DateTime(), nullable=True),
        sa.Column('email_last_error', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
    if 'notifications' not in existing:
        op.create_index(op.f('ix_notifications_email_status'), 'notifications', ['email_status'], unique=False)
        op.create_index(op.f('ix_notifications_user_id'), 'notifications', ['user_id'], unique=False)
    if 'push_subscriptions' not in existing:
        op.create_table('push_subscriptions',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('endpoint', sa.Text(), nullable=False),
        sa.Column('p256dh', sa.Text(), nullable=False),
        sa.Column('auth', sa.Text(), nullable=False),
        sa.Column('user_agent', sa.String(length=300), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('endpoint')
        )
    if 'push_subscriptions' not in existing:
        op.create_index(op.f('ix_push_subscriptions_user_id'), 'push_subscriptions', ['user_id'], unique=False)
    if 'questions' not in existing:
        op.create_table('questions',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('run_id', sa.String(length=32), nullable=False),
        sa.Column('task_id', sa.String(length=32), nullable=True),
        sa.Column('bot_id', sa.String(length=32), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('answer', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
    if 'questions' not in existing:
        op.create_index(op.f('ix_questions_run_id'), 'questions', ['run_id'], unique=False)
        op.create_index(op.f('ix_questions_user_id'), 'questions', ['user_id'], unique=False)
    if 'terminal_commands' not in existing:
        op.create_table('terminal_commands',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('chat_id', sa.String(length=32), nullable=False),
        sa.Column('run_id', sa.String(length=32), nullable=True),
        sa.Column('bot_id', sa.String(length=32), nullable=True),
        sa.Column('tool_call_id', sa.String(length=200), nullable=True),
        sa.Column('kind', sa.String(length=12), nullable=False),
        sa.Column('language', sa.String(length=12), nullable=False),
        sa.Column('command', sa.Text(), nullable=False),
        sa.Column('output', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('ended_at', sa.DateTime(), nullable=True),
        sa.Column('lease_until', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
    if 'terminal_commands' not in existing:
        op.create_index(op.f('ix_terminal_commands_chat_id'), 'terminal_commands', ['chat_id'], unique=False)
        op.create_index(op.f('ix_terminal_commands_status'), 'terminal_commands', ['status'], unique=False)
        op.create_index(op.f('ix_terminal_commands_user_id'), 'terminal_commands', ['user_id'], unique=False)
    if 'uploads' not in existing:
        op.create_table('uploads',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('filename', sa.String(length=200), nullable=False),
        sa.Column('mime', sa.String(length=80), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
    if 'uploads' not in existing:
        op.create_index(op.f('ix_uploads_user_id'), 'uploads', ['user_id'], unique=False)
    if 'usage_logs' not in existing:
        op.create_table('usage_logs',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('run_id', sa.String(length=32), nullable=True),
        sa.Column('task_id', sa.String(length=32), nullable=True),
        sa.Column('bot_id', sa.String(length=32), nullable=True),
        sa.Column('provider_id', sa.String(length=32), nullable=True),
        sa.Column('model', sa.String(length=120), nullable=True),
        sa.Column('input_tokens', sa.Integer(), nullable=False),
        sa.Column('output_tokens', sa.Integer(), nullable=False),
        sa.Column('latency_ms', sa.Integer(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
    if 'usage_logs' not in existing:
        op.create_index(op.f('ix_usage_logs_run_id'), 'usage_logs', ['run_id'], unique=False)
        op.create_index(op.f('ix_usage_logs_user_id'), 'usage_logs', ['user_id'], unique=False)
    if 'users' not in existing:
        op.create_table('users',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('invite_code_id', sa.String(length=32), nullable=True),
        sa.Column('name', sa.String(length=80), nullable=True),
        sa.Column('email_verified_at', sa.DateTime(), nullable=True),
        sa.Column('session_epoch', sa.Integer(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.Column('purge_after', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
    if 'users' not in existing:
        op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    if 'bots' not in existing:
        op.create_table('bots',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=40), nullable=False),
        sa.Column('avatar', sa.String(length=500), nullable=False),
        sa.Column('role', sa.String(length=40), nullable=False),
        sa.Column('system_prompt', sa.Text(), nullable=False),
        sa.Column('provider_id', sa.String(length=32), nullable=True),
        sa.Column('model', sa.String(length=120), nullable=True),
        sa.Column('temperature', sa.Double(), nullable=False),
        sa.Column('tools', sa.JSON(), nullable=False),
        sa.Column('skills', sa.JSON(), nullable=False),
        sa.Column('is_supervisor_template', sa.Boolean(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if 'bots' not in existing:
        op.create_index(op.f('ix_bots_user_id'), 'bots', ['user_id'], unique=False)
        op.create_index('uq_bot_user_name', 'bots', ['user_id', 'name'], unique=True, sqlite_where=sa.text('deleted_at IS NULL'), postgresql_where=sa.text('deleted_at IS NULL'))
    if 'chats' not in existing:
        op.create_table('chats',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('supervisor_bot_id', sa.String(length=32), nullable=True),
        sa.Column('run_mode', sa.String(length=20), nullable=False),
        sa.Column('limits_override', sa.JSON(), nullable=False),
        sa.Column('always_allow_tools', sa.JSON(), nullable=False),
        sa.Column('require_approval_for_write', sa.Boolean(), nullable=False),
        sa.Column('last_active_at', sa.DateTime(), nullable=False),
        sa.Column('archived', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if 'chats' not in existing:
        op.create_index(op.f('ix_chats_user_id'), 'chats', ['user_id'], unique=False)
    if 'cube_connections' not in existing:
        op.create_table('cube_connections',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('api_url', sa.String(length=500), nullable=False),
        sa.Column('template_id', sa.String(length=200), nullable=False),
        sa.Column('api_key_encrypted', sa.Text(), nullable=True),
        sa.Column('proxy_node_ip', sa.String(length=200), nullable=True),
        sa.Column('proxy_port', sa.Integer(), nullable=False),
        sa.Column('sandbox_domain', sa.String(length=200), nullable=False),
        sa.Column('last_checked_at', sa.DateTime(), nullable=True),
        sa.Column('last_check_error', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if 'cube_connections' not in existing:
        op.create_index(op.f('ix_cube_connections_user_id'), 'cube_connections', ['user_id'], unique=True)
    if 'mcp_servers' not in existing:
        op.create_table('mcp_servers',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=60), nullable=False),
        sa.Column('slug', sa.String(length=24), nullable=False),
        sa.Column('transport', sa.String(length=10), nullable=False),
        sa.Column('url', sa.String(length=500), nullable=False),
        sa.Column('command', sa.String(length=500), nullable=True),
        sa.Column('args', sa.JSON(), nullable=False),
        sa.Column('env', sa.JSON(), nullable=False),
        sa.Column('token_encrypted', sa.Text(), nullable=True),
        sa.Column('token_last4', sa.String(length=8), nullable=True),
        sa.Column('level', sa.String(length=2), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('tools', sa.JSON(), nullable=False),
        sa.Column('last_checked_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'name', name='uq_mcp_user_name')
        )
    if 'mcp_servers' not in existing:
        op.create_index(op.f('ix_mcp_servers_user_id'), 'mcp_servers', ['user_id'], unique=False)
    if 'oauth_accounts' not in existing:
        op.create_table('oauth_accounts',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('provider', sa.String(length=16), nullable=False),
        sa.Column('provider_uid', sa.String(length=128), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('name', sa.String(length=80), nullable=True),
        sa.Column('avatar_url', sa.String(length=500), nullable=True),
        sa.Column('last_login_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider', 'provider_uid', name='uq_oauth_provider_uid')
        )
    if 'oauth_accounts' not in existing:
        op.create_index(op.f('ix_oauth_accounts_provider'), 'oauth_accounts', ['provider'], unique=False)
        op.create_index(op.f('ix_oauth_accounts_user_id'), 'oauth_accounts', ['user_id'], unique=False)
    if 'providers' not in existing:
        op.create_table('providers',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('vendor_id', sa.String(length=40), nullable=True),
        sa.Column('logo', sa.String(length=500), nullable=True),
        sa.Column('base_url', sa.String(length=400), nullable=False),
        sa.Column('api_key_encrypted', sa.Text(), nullable=True),
        sa.Column('api_key_last4', sa.String(length=8), nullable=True),
        sa.Column('models', sa.JSON(), nullable=False),
        sa.Column('last_checked_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if 'providers' not in existing:
        op.create_index(op.f('ix_providers_user_id'), 'providers', ['user_id'], unique=False)
    if 'skills' not in existing:
        op.create_table('skills',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=60), nullable=False),
        sa.Column('description', sa.String(length=200), nullable=False),
        sa.Column('prompt', sa.Text(), nullable=False),
        sa.Column('tools', sa.JSON(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('source_url', sa.String(length=500), nullable=True),
        sa.Column('files', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'name', name='uq_skill_user_name')
        )
    if 'skills' not in existing:
        op.create_index(op.f('ix_skills_user_id'), 'skills', ['user_id'], unique=False)
    if 'user_settings' not in existing:
        op.create_table('user_settings',
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('max_steps', sa.Integer(), nullable=False),
        sa.Column('token_budget', sa.Integer(), nullable=False),
        sa.Column('delegation_depth', sa.Integer(), nullable=True),
        sa.Column('notifications_enabled', sa.Boolean(), nullable=False),
        sa.Column('approval_email_enabled', sa.Boolean(), nullable=False),
        sa.Column('search_provider', sa.String(length=32), nullable=True),
        sa.Column('search_api_key_encrypted', sa.Text(), nullable=True),
        sa.Column('approval_ttl_hours', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('user_id')
        )
    if 'chat_members' not in existing:
        op.create_table('chat_members',
        sa.Column('chat_id', sa.String(length=32), nullable=False),
        sa.Column('bot_id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(['bot_id'], ['bots.id'], ),
        sa.ForeignKeyConstraint(['chat_id'], ['chats.id'], ),
        sa.PrimaryKeyConstraint('chat_id', 'bot_id')
        )
    if 'chat_members' not in existing:
        op.create_index(op.f('ix_chat_members_user_id'), 'chat_members', ['user_id'], unique=False)
    if 'runs' not in existing:
        op.create_table('runs',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('chat_id', sa.String(length=32), nullable=False),
        sa.Column('trigger_message_id', sa.String(length=32), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('steps_used', sa.Integer(), nullable=False),
        sa.Column('tokens_used', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('ended_at', sa.DateTime(), nullable=True),
        sa.Column('end_reason', sa.Text(), nullable=True),
        sa.Column('config_snapshot', sa.JSON(), nullable=False),
        sa.Column('resume_payload', sa.JSON(), nullable=True),
        sa.Column('stop_requested', sa.Boolean(), nullable=False),
        sa.Column('target_bot_id', sa.String(length=32), nullable=True),
        sa.Column('mode', sa.String(length=20), nullable=False),
        sa.Column('lease_until', sa.DateTime(), nullable=True),
        sa.Column('worker_id', sa.String(length=64), nullable=True),
        sa.Column('lease_token', sa.Integer(), nullable=False),
        sa.Column('last_turn_message_id', sa.String(length=32), nullable=True),
        sa.Column('last_interjection_seq', sa.Integer(), nullable=False),
        sa.Column('thread_id', sa.String(length=32), nullable=True),
        sa.Column('parent_run_id', sa.String(length=32), nullable=True),
        sa.Column('parent_task_id', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['chat_id'], ['chats.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if 'runs' not in existing:
        op.create_index(op.f('ix_runs_chat_id'), 'runs', ['chat_id'], unique=False)
        op.create_index(op.f('ix_runs_parent_run_id'), 'runs', ['parent_run_id'], unique=False)
        op.create_index(op.f('ix_runs_status'), 'runs', ['status'], unique=False)
        op.create_index(op.f('ix_runs_user_id'), 'runs', ['user_id'], unique=False)
    if 'threads' not in existing:
        op.create_table('threads',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('chat_id', sa.String(length=32), nullable=False),
        sa.Column('parent_thread_id', sa.String(length=32), nullable=True),
        sa.Column('type', sa.String(length=20), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=True),
        sa.Column('bot_id', sa.String(length=32), nullable=True),
        sa.Column('anchor_message_id', sa.String(length=32), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('summary_upto_seq', sa.Integer(), nullable=False),
        sa.Column('seq_counter', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['chat_id'], ['chats.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if 'threads' not in existing:
        op.create_index(op.f('ix_threads_chat_id'), 'threads', ['chat_id'], unique=False)
        op.create_index(op.f('ix_threads_user_id'), 'threads', ['user_id'], unique=False)
    if 'messages' not in existing:
        op.create_table('messages',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('thread_id', sa.String(length=32), nullable=False),
        sa.Column('run_id', sa.String(length=32), nullable=True),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('sender_type', sa.String(length=12), nullable=False),
        sa.Column('sender_bot_id', sa.String(length=32), nullable=True),
        sa.Column('type', sa.String(length=24), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('reasoning_content', sa.Text(), nullable=True),
        sa.Column('meta', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['thread_id'], ['threads.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('thread_id', 'seq', name='uq_msg_thread_seq')
        )
    if 'messages' not in existing:
        op.create_index(op.f('ix_messages_run_id'), 'messages', ['run_id'], unique=False)
        op.create_index(op.f('ix_messages_thread_id'), 'messages', ['thread_id'], unique=False)
        op.create_index(op.f('ix_messages_user_id'), 'messages', ['user_id'], unique=False)
    if 'tasks' not in existing:
        op.create_table('tasks',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('run_id', sa.String(length=32), nullable=False),
        sa.Column('thread_id', sa.String(length=32), nullable=False),
        sa.Column('assigned_bot_id', sa.String(length=32), nullable=False),
        sa.Column('instruction', sa.Text(), nullable=False),
        sa.Column('expected_output', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('result', sa.Text(), nullable=True),
        sa.Column('attempt', sa.Integer(), nullable=False),
        sa.Column('step_key', sa.String(length=200), nullable=True),
        sa.Column('batch', sa.Integer(), nullable=False),
        sa.Column('tokens_used', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('ended_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['assigned_bot_id'], ['bots.id'], ),
        sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ),
        sa.ForeignKeyConstraint(['thread_id'], ['threads.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if 'tasks' not in existing:
        op.create_index(op.f('ix_tasks_run_id'), 'tasks', ['run_id'], unique=False)
        op.create_index(op.f('ix_tasks_status'), 'tasks', ['status'], unique=False)
        op.create_index(op.f('ix_tasks_user_id'), 'tasks', ['user_id'], unique=False)


def downgrade() -> None:
    pass

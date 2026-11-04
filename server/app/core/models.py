"""数据模型，对应需求文档 v0.3 第 10 节。

约定：除 users 外，所有表都带 user_id，接口层统一按当前登录用户过滤。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def uid() -> str:
    return uuid.uuid4().hex


def now() -> datetime:
    """统一使用「无时区的 UTC 时间」。

    SQLite 不保存时区信息，混用 aware / naive 会在做时间差时抛 TypeError，
    因此全项目约定：数据库里的时间一律是 UTC，且不带 tzinfo。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now, onupdate=now
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="active")
    invite_code_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # ACC-08：邮箱验证时间；为空表示未验证（第一版不阻断使用，只在设置页提示）
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 会话代数：重置/修改密码时 +1，让所有旧 Cookie 立即失效
    session_epoch: Mapped[int] = mapped_column(Integer, default=0)
    # ACC-07：删除账号先停用、数据保留 30 天供管理员恢复，到期后彻底清除。
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    purge_after: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OAuthAccount(Base, TimestampMixin):
    """第三方登录绑定：一个用户可绑定多个 provider，一个 provider 账号只绑一个用户。

    provider_uid 是对方系统的用户 id（Google 的 sub / GitHub 的数字 id），
    比邮箱稳：对方改邮箱也不会认错人。
    """

    __tablename__ = "oauth_accounts"
    __table_args__ = (UniqueConstraint("provider", "provider_uid", name="uq_oauth_provider_uid"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(16), index=True)  # google | github
    provider_uid: Mapped[str] = mapped_column(String(128))
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class EmailToken(Base, TimestampMixin):
    """ACC-08：一次性邮件令牌（找回密码 / 邮箱验证）。

    只存 SHA-256 摘要，泄库也拿不到可用链接；用完置 used_at，不物理删除，
    便于排查“链接为什么失效”。
    """

    __tablename__ = "email_tokens"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)  # reset_password | verify_email
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)



class LoginAttempt(Base):
    """仅记录失败计数所需的信息，不保存密码或请求正文。"""
    __tablename__ = "login_attempts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(255), index=True)
    ip: Mapped[str] = mapped_column(String(80), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class InviteCode(Base, TimestampMixin):
    __tablename__ = "invite_codes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)


class UserSettings(Base, TimestampMixin):
    __tablename__ = "user_settings"
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), primary_key=True)
    max_steps: Mapped[int] = mapped_column(Integer, default=0)  # 旧字段保留，不再限制步数。
    token_budget: Mapped[int] = mapped_column(Integer, default=300_000)  # 0 表示无限制。
    # RUN-09：委派深度（1 = 只有总指挥可派任务，2 = 允许再向下一层）。
    # 为空表示未设置，按环境变量 BOTLY_MAX_DELEGATION_DEPTH 的默认值执行。
    delegation_depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    approval_email_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    search_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    search_api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_ttl_hours: Mapped[int] = mapped_column(Integer, default=24)


class Provider(Base, TimestampMixin):
    """模型接入：一套 OpenAI 兼容接口。"""

    __tablename__ = "providers"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(20), default="openai")  # openai | mock
    vendor_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # 用户上传的厂商标识；为空时用预置的 logo/color 渲染
    logo: Mapped[str | None] = mapped_column(String(500), nullable=True)
    base_url: Mapped[str] = mapped_column(String(400), default="https://api.openai.com/v1")
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key_last4: Mapped[str | None] = mapped_column(String(8), nullable=True)
    models: Mapped[list] = mapped_column(JSON, default=list)  # [{name, input_price, output_price}]
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="unchecked")


class CubeConnection(Base, TimestampMixin):
    """用户自己的 CubeSandbox 后端连接；密钥加密保存。"""
    __tablename__ = "cube_connections"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), unique=True, index=True)
    api_url: Mapped[str] = mapped_column(String(500))
    template_id: Mapped[str] = mapped_column(String(200))
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    proxy_node_ip: Mapped[str | None] = mapped_column(String(200), nullable=True)
    proxy_port: Mapped[int] = mapped_column(Integer, default=80)
    sandbox_domain: Mapped[str] = mapped_column(String(200), default="cube.app")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_check_error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class CubeSession(Base, TimestampMixin):
    """每账号每群聊的远端实例，跨 API/Worker 和重启复用。"""
    __tablename__ = "cube_sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    chat_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    sandbox_id: Mapped[str] = mapped_column(String(200))
    connection_hash: Mapped[str] = mapped_column(String(64))
    files_manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    skills_manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    last_executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pty_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TerminalCommand(Base, TimestampMixin):
    __tablename__ = "terminal_commands"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    chat_id: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bot_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    kind: Mapped[str] = mapped_column(String(12), default="command")
    language: Mapped[str] = mapped_column(String(12), default="bash")
    command: Mapped[str] = mapped_column(Text)
    output: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Bot(Base, TimestampMixin):
    __tablename__ = "bots"
    # 同一账号下「未删除」的 Bot 名称唯一；软删除后名字可复用（历史消息仍按 id 引用）
    __table_args__ = (
        Index(
            "uq_bot_user_name",
            "user_id",
            "name",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(40))
    # emoji 或上传后的图片地址（/api/uploads/xxx）
    avatar: Mapped[str] = mapped_column(String(500), default="bot")
    role: Mapped[str] = mapped_column(String(40), default="通用助手")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    provider_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    temperature: Mapped[float] = mapped_column(default=0.4)
    tools: Mapped[list] = mapped_column(JSON, default=list)
    # 已启用的技能 id 列表（运行时提供目录，由智能体按任务加载正文）
    skills: Mapped[list] = mapped_column(JSON, default=list)
    is_supervisor_template: Mapped[bool] = mapped_column(Boolean, default=False)
    # BOT-06：停用后不再参与任何新执行（不能被派任务、不能被 @、不能当总指挥），
    # 历史消息与成员关系保留，重新启用即可恢复。
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class McpServer(Base, TimestampMixin):
    """用户接入的 MCP 服务（TOL-05）。

    支持两种传输：Streamable HTTP（url）与 stdio（本地进程 command + args + env）。
    工具在「检测」时通过 tools/list 拉到本地缓存，执行时再 tools/call。
    权限级别在服务级指定（默认 L3），该服务下所有工具继承同一级别。
    """

    __tablename__ = "mcp_servers"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_mcp_user_name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    # 工具名前缀，同一用户下唯一（由名字推导，检测时固化）
    slug: Mapped[str] = mapped_column(String(24))
    transport: Mapped[str] = mapped_column(String(10), default="http")  # http | stdio
    url: Mapped[str] = mapped_column(String(500), default="")
    # stdio：本地进程启动方式（command + args + env）
    command: Mapped[str | None] = mapped_column(String(500), nullable=True)
    args: Mapped[list] = mapped_column(JSON, default=list)
    env: Mapped[dict] = mapped_column(JSON, default=dict)
    token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_last4: Mapped[str | None] = mapped_column(String(8), nullable=True)
    level: Mapped[str] = mapped_column(String(2), default="L3")  # L1 | L2 | L3
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="unchecked")  # unchecked | ok | error
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # [{name, remote_name, description, parameters}]，检测成功后写入
    tools: Mapped[list] = mapped_column(JSON, default=list)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Skill(Base, TimestampMixin):
    """可复用的技能包：一段方法提示词 + 一组工具。

    技能不直接执行，绑定到 Bot 后由编排器提供名称与用途，智能体按需读取正文；
    技能里的工具并入该 Bot 的可用工具（仍受工具自身的权限级别约束）。
    """

    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_skill_user_name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(200), default="")
    prompt: Mapped[str] = mapped_column(Text, default="")
    tools: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 外部技能包：manual（手写）/ url / zip / github
    source: Mapped[str] = mapped_column(String(20), default="manual")
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # 技能包内文件（相对路径），提示词之外的参考资料；用 skill_file_read 读取
    files: Mapped[list] = mapped_column(JSON, default=list)


class Chat(Base, TimestampMixin):
    __tablename__ = "chats"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    supervisor_bot_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    run_mode: Mapped[str] = mapped_column(String(20), default="auto")  # auto | step
    limits_override: Mapped[dict] = mapped_column(JSON, default=dict)
    always_allow_tools: Mapped[list] = mapped_column(JSON, default=list)
    require_approval_for_write: Mapped[bool] = mapped_column(Boolean, default=False)
    last_active_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class ChatMember(Base):
    __tablename__ = "chat_members"
    chat_id: Mapped[str] = mapped_column(String(32), ForeignKey("chats.id"), primary_key=True)
    bot_id: Mapped[str] = mapped_column(String(32), ForeignKey("bots.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)


class Thread(Base, TimestampMixin):
    __tablename__ = "threads"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    chat_id: Mapped[str] = mapped_column(String(32), ForeignKey("chats.id"), index=True)
    parent_thread_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    type: Mapped[str] = mapped_column(String(20), default="main")  # main | task | branch
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    bot_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # CHT-07：分支从哪条消息拉出（用于回到锚点 / 展示来源）
    anchor_message_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # MEM-05：主线程长对话的自动摘要与覆盖到的 seq，避免上下文无限增长。
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_upto_seq: Mapped[int] = mapped_column(Integer, default=0)
    # 消息序号计数器：并行子任务会同时往同一个线程写消息，用数据库自增
    # 分配 seq 才能保证不重号（max(seq)+1 在并发下会算出同一个值）。
    seq_counter: Mapped[int] = mapped_column(Integer, default=0)


class Run(Base, TimestampMixin):
    """一次执行：步数、预算、停止都以执行为单位。"""

    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    chat_id: Mapped[str] = mapped_column(String(32), ForeignKey("chats.id"), index=True)
    trigger_message_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    # queued | running | waiting | waiting_children（等并行子任务）| completed | failed | stopped
    steps_used: Mapped[int] = mapped_column(Integer, default=0)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    resume_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    stop_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    target_bot_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # @Bot 直连
    mode: Mapped[str] = mapped_column(String(20), default="auto")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 最近一轮模型输出对应的消息 id：DB 已提交、检查点未写入时崩溃，重放
    # model 节点据此复用这一轮结果，避免重复调用、重复计步与重复消息（8.3）
    last_turn_message_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # RUN-13：总指挥已读到的最新插话 seq。子 Bot 独立读取补充并继续原任务，
    # 每轮输出的 meta.interjection_seq 记录实际读取位置，用于收尾前检查新补充。
    last_interjection_seq: Mapped[int] = mapped_column(Integer, default=0)
    # CHT-07：分支线程里的执行。为空表示跑在主线程；否则 supervisor 的上下文
    # 与回复都落在该分支线程，不打断主线程。
    thread_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # RUN-12：并行的子任务各自是一条独立执行（有自己的检查点、审批、停止与
    # token 统计），用 parent_run_id 指回总指挥那次执行。为空表示顶层执行。
    parent_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    parent_task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Task(Base, TimestampMixin):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("runs.id"), index=True)
    thread_id: Mapped[str] = mapped_column(String(32), ForeignKey("threads.id"))
    assigned_bot_id: Mapped[str] = mapped_column(String(32), ForeignKey("bots.id"))
    instruction: Mapped[str] = mapped_column(Text)
    expected_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    # queued | running | waiting_approval | waiting_reply | completed | failed | canceled
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    # 同一子任务的稳定标识（计划步骤标题或指令的归一化文本）。改派换个说法
    # 也要算作同一个子任务，所以不能用指令原文做判断（RUN-10）
    step_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # RUN-12：所属计划批次。同一批次（>0）的步骤并行执行；0 表示未按批次调度。
    batch: Mapped[int] = mapped_column(Integer, default=0)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Message(Base, TimestampMixin):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_msg_thread_seq", "thread_id", "seq"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    thread_id: Mapped[str] = mapped_column(String(32), ForeignKey("threads.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    sender_type: Mapped[str] = mapped_column(String(12), default="bot")  # user | bot | system
    sender_bot_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    type: Mapped[str] = mapped_column(String(24), default="text")
    # text | plan | task | task_done | handoff | approval | question | tool_call | tool_result | system | limit
    content: Mapped[str] = mapped_column(Text, default="")
    # 推理模型的思维链（deepseek-reasoner / kimi-k2 / glm 等）：下一轮请求必须原样回传，
    # 否则接口会返回 400。只在服务端用于构造历史，不下发前端。
    reasoning_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="done")  # streaming | done | stopped


class Approval(Base, TimestampMixin):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bot_id: Mapped[str] = mapped_column(String(32))
    tool: Mapped[str] = mapped_column(String(64))
    args: Mapped[dict] = mapped_column(JSON, default=dict)
    args_hash: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    level: Mapped[str] = mapped_column(String(4), default="L3")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # pending | approved | rejected | expired | invalidated
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Question(Base, TimestampMixin):
    __tablename__ = "questions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bot_id: Mapped[str] = mapped_column(String(32))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Memory(Base, TimestampMixin):
    __tablename__ = "memories"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    scope: Mapped[str] = mapped_column(String(12), default="shared")  # shared | bot
    bot_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    title: Mapped[str] = mapped_column(String(120), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    # 主题分类（如 偏好 / 约定 / 项目背景 / 事实），用于记忆页分组与筛选；空表示未分类
    category: Mapped[str] = mapped_column(String(30), default="")
    # user = 用户在记忆页维护；bot = Bot 执行中通过 memory_save 自主写入（MEM-03 修订）
    source: Mapped[str] = mapped_column(String(20), default="user")
    # 写入时的执行 id：追溯来源，也用于「每次执行最多自动保存 N 条」的限额
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)


class MemoryCandidate(Base, TimestampMixin):
    """MEM-04：执行结束后自动提取的记忆候选，用户确认后才写入 memories。

    只从用户请求与总指挥汇总里提取，不采信网页/工具返回的内容，避免提示词注入
    或网页噪声污染长期记忆。
    """

    __tablename__ = "memory_candidates"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    chat_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    scope: Mapped[str] = mapped_column(String(12), default="shared")
    bot_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    title: Mapped[str] = mapped_column(String(120), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(30), default="")
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    # pending | accepted | rejected
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class FileRec(Base, TimestampMixin):
    __tablename__ = "files"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    chat_id: Mapped[str] = mapped_column(String(32), index=True)
    path: Mapped[str] = mapped_column(String(500))
    size: Mapped[int] = mapped_column(Integer, default=0)
    created_by_bot_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Upload(Base, TimestampMixin):
    """用户上传的图片（Bot 头像、厂商标识）。"""

    __tablename__ = "uploads"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    filename: Mapped[str] = mapped_column(String(200))
    mime: Mapped[str] = mapped_column(String(80), default="image/png")
    size: Mapped[int] = mapped_column(Integer, default=0)


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    type: Mapped[str] = mapped_column(String(32))
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    chat_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 审批邮件与通知同事务落库；Worker 认领、投递和重试，不在请求中发信。
    email_status: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    email_attempts: Mapped[int] = mapped_column(Integer, default=0)
    email_next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    email_lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    email_last_error: Mapped[str | None] = mapped_column(String(200), nullable=True)


class PushSubscription(Base, TimestampMixin):
    """NTF-06：Web Push 订阅（Service Worker PushManager 生成）。"""

    __tablename__ = "push_subscriptions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(Text)
    auth: Mapped[str] = mapped_column(Text)
    user_agent: Mapped[str] = mapped_column(String(300), default="")


class Event(Base):
    """SSE 事件表：id 递增作为事件序号，保留 7 天用于断线补发。"""

    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    chat_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    type: Mapped[str] = mapped_column(String(48))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Alert(Base):
    """8.6：系统级告警（队列积压、执行卡死、失败率），供运行状况看板读取。

    不挂 user_id：告警描述的是整个部署的健康度，body 里也不写具体用户/群聊内容。
    """

    __tablename__ = "alerts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String(40), index=True)  # queue_backlog | run_stuck | fail_rate
    level: Mapped[str] = mapped_column(String(12), default="warning")  # warning | critical
    title: Mapped[str] = mapped_column(String(200), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UsageLog(Base, TimestampMixin):
    __tablename__ = "usage_logs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bot_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

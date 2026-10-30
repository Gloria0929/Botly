"""数据模型，对应需求文档 v0.3 第 10 节。

约定：除 users 外，所有表都带 user_id，接口层统一按当前登录用户过滤。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, text)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


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
    # ACC-07：删除账号先停用、数据保留 30 天供管理员恢复，到期后彻底清除。
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    purge_after: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


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
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
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
    is_supervisor_template: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


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


class Run(Base, TimestampMixin):
    """一次执行：步数、预算、停止都以执行为单位。"""

    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    chat_id: Mapped[str] = mapped_column(String(32), ForeignKey("chats.id"), index=True)
    trigger_message_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    # queued | running | waiting | completed | failed | stopped
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
    # RUN-13：总指挥已读到的最新插话 seq。子 Bot 用它判断是否有「尚未处理」
    # 的新插话，有则提前结束子任务交回总指挥，避免插话要等整个子任务跑完才生效。
    last_interjection_seq: Mapped[int] = mapped_column(Integer, default=0)
    # CHT-07：分支线程里的执行。为空表示跑在主线程；否则 supervisor 的上下文
    # 与回复都落在该分支线程，不打断主线程。
    thread_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


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

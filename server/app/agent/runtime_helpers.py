"""消息与任务状态公共操作，以及工具上下文。"""
from __future__ import annotations

import json
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.agent.messages import add_message
from app.sandbox import files as sandbox
from app.sandbox.artifacts import is_output_file
from app.core.events import emit
from app.core.logging_setup import get_logger
from app.core.models import Approval, Bot, Chat, FileRec, Message, Question, Run, Task, Thread, UserSettings, now
from app.core.security import decrypt_secret


def add_compat_message(
    db: Session,
    *,
    thread: Thread,
    run_id: str | None,
    type_: str,
    content: str,
    meta: dict,
    sender_type: str = "bot",
    sender_bot_id: str | None = None,
) -> None:
    """CHT-05 / RUN-08 / 7.1：补发 task / task_done / handoff 结构化消息。

    群聊交互保持现状：派任务是「@成员 指令」的普通消息，子 Bot 的回复是普通
    气泡，任务详情在派任务消息下方的概要行里。这三类消息只作为结构化记录
    存在，供导出、第三方接入与历史渲染消费；前端按 meta.compat 过滤，
    不额外渲染。按 (run_id, task_id, type) 去重，图节点重放不会写第二条。
    """
    task_id = meta.get("task_id")
    rows = (
        db.scalars(
            select(Message).where(
                Message.thread_id == thread.id,
                Message.type == type_,
                Message.run_id == run_id,
            )
        ).all()
        if run_id
        else []
    )
    for row in rows:
        rm = row.meta or {}
        if rm.get("compat") and rm.get("task_id") == task_id:
            return
    add_message(
        db,
        thread=thread,
        run_id=run_id,
        sender_type=sender_type,
        type_=type_,
        content=content,
        sender_bot_id=sender_bot_id,
        meta={**meta, "compat": True},
    )


def emit_run_status(db: Session, run: Run) -> None:
    emit(
        db,
        run.user_id,
        run.chat_id,
        "run.status",
        {
            "run_id": run.id,
            "chat_id": run.chat_id,
            "status": run.status,
            "steps_used": run.steps_used,
            "tokens_used": run.tokens_used,
            "end_reason": run.end_reason,
            "resume": run.resume_payload,
        },
    )


def emit_task_status(db: Session, task: Task) -> None:
    emit(
        db,
        task.user_id,
        db.get(Thread, task.thread_id).chat_id if db.get(Thread, task.thread_id) else None,
        "task.status",
        {
            "task_id": task.id,
            "run_id": task.run_id,
            "thread_id": task.thread_id,
            "status": task.status,
            "assigned_bot_id": task.assigned_bot_id,
            "instruction": task.instruction,
            "expected_output": task.expected_output,
            "result": task.result,
            "tokens_used": task.tokens_used,
            "attempt": task.attempt,
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "ended_at": task.ended_at.isoformat() if task.ended_at else None,
        },
    )


def _parse_json(raw: str) -> dict:
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


EMPTY_OUTPUT = "（无输出）"


log = get_logger("agent")


def _step_key(value: str) -> str:
    """把计划步骤（或指令）归一化成稳定键。

    改派时总指挥常换个说法，所以优先用计划步骤的标题；没给标题才退回指令
    文本。折叠空白 + 小写，避免只差空格或大小写就被当成另一个子任务。
    """
    return " ".join((value or "").split()).lower()[:200]


def settle_run_tasks(db: Session, run: Run, status: str, reason: str) -> None:
    """执行终止时同步任务、交互卡片和流式消息，保留已经完成的产物。"""
    run.resume_payload = None
    run.lease_until = None
    run.worker_id = None
    approvals = {a.id: a for a in db.scalars(select(Approval).where(Approval.run_id == run.id))}
    questions = {q.id: q for q in db.scalars(select(Question).where(Question.run_id == run.id))}
    changed = set()
    for ap in approvals.values():
        if ap.status == "pending":
            ap.status = "invalidated"
            ap.resolved_at = now()
            changed.add(ap.id)
    for question in questions.values():
        if question.status == "pending":
            question.status = "expired"
    for card in db.scalars(select(Message).where(
        Message.run_id == run.id, Message.type.in_(["approval", "question", "plan"]),
    )).all():
        meta = card.meta or {}
        if card.type == "plan":
            if meta.get("status") == "pending":
                card.meta = {**meta, "status": "canceled", "closed_reason": reason}
            continue
        item = approvals.get(meta.get("approval_id")) if card.type == "approval" else questions.get(meta.get("question_id"))
        if not item:
            continue
        patch = {**meta, "status": item.status}
        if item.status in ("invalidated", "expired"):
            patch["closed_reason"] = reason
        if patch != meta:
            card.meta = patch
            if card.type == "approval":
                changed.add(item.id)
    db.flush()
    for approval_id in changed:
        emit(db, run.user_id, run.chat_id, "approval.resolved", {
            "approval_id": approval_id, "run_id": run.id,
            "status": approvals[approval_id].status, "closed_reason": reason,
        })
    for task in db.scalars(select(Task).where(
        Task.run_id == run.id,
        Task.status.in_(["queued", "running", "waiting_approval", "waiting_reply"]),
    )).all():
        task.status = status
        task.result = reason
        task.ended_at = now()
        db.flush()
        emit_task_status(db, task)
    # 已结束的任务也补推一次，确保群聊详情拿到最后的 token 用量
    for task in db.scalars(select(Task).where(Task.run_id == run.id)).all():
        emit_task_status(db, task)
    for msg in db.scalars(select(Message).where(
        Message.run_id == run.id, Message.status == "streaming",
    )).all():
        if not (msg.content or "").strip():
            # 还没输出任何文字的占位消息直接删掉，避免留下空的 Bot 头部
            db.delete(msg)
            db.flush()
            emit(db, run.user_id, run.chat_id, "message.deleted", {"id": msg.id})
            continue
        msg.status = "interrupted"
        emit(db, run.user_id, run.chat_id, "message.completed", {
            "message_id": msg.id, "thread_id": msg.thread_id,
            "content": msg.content, "status": msg.status, "meta": msg.meta or {},
        })


CHILD_ACTIVE_STATUSES = ("queued", "running", "waiting")


def stop_child_runs(db: Session, run: Run, reason: str = "上游执行已停止") -> list[str]:
    """停止某个执行下面的并行子执行。

    子执行是独立的 Run（有自己的队列、审批和停止开关），父执行结束时必须显式
    通知它们，否则用户点了停止，后台的子 Bot 还在继续跑。返回需要入队的 id
    由调用方在事务提交后再处理（这里只负责落状态）。
    """
    stopped: list[str] = []
    children = db.scalars(
        select(Run).where(Run.parent_run_id == run.id, Run.status.in_(CHILD_ACTIVE_STATUSES))
    ).all()
    for child in children:
        child.stop_requested = True
        if child.status in ("queued", "waiting"):
            # 还没跑起来（或停在审批/提问）：直接结束，不用等 Worker 接手
            child.status = "stopped"
            child.ended_at = now()
            child.end_reason = reason
            child.resume_payload = None
            settle_run_tasks(db, child, "canceled", reason)
            _cancel_child_task(db, child, reason)
            db.flush()
            emit_run_status(db, child)
            stopped.append(child.id)
        else:
            # 正在跑：由它的执行器在下一次检查点抛出 StopSignal
            _cancel_child_task(db, child, reason)
            db.flush()
    return stopped


def _cancel_child_task(db: Session, child: Run, reason: str) -> None:
    """把子执行对应的任务标成取消，群聊里的任务概要立刻反映停止。"""
    task = db.get(Task, child.parent_task_id) if child.parent_task_id else None
    if not task or task.status in ("completed", "failed", "canceled"):
        return
    task.status = "canceled"
    task.result = reason
    task.ended_at = now()
    db.flush()
    emit_task_status(db, task)


class ToolCtx:
    """工具执行上下文。"""

    def __init__(self, db: Session, run: Run, chat: Chat, bot: Bot, task: Task | None, lease_check=None):
        self.db = db
        self.run = run
        self.chat = chat
        self.bot = bot
        self.task = task
        self.lease_check = lease_check
        self.user_id = run.user_id
        self.chat_id = run.chat_id
        from app.sandbox.workspace import relative_workspace
        self.workspace = relative_workspace(run)
        settings = db.get(UserSettings, run.user_id)
        key = decrypt_secret(settings.search_api_key_encrypted) if settings else None
        self.search_key = key

    def assert_lease(self) -> None:
        if self.lease_check:
            self.lease_check(self.db)

    def file_path(self, path: str, *, write: bool = False) -> str:
        root = sandbox.chat_root(self.user_id, self.chat_id)
        rel = sandbox.safe_path(self.user_id, self.chat_id, path).relative_to(root).as_posix()
        if not self.workspace or rel == self.workspace or rel.startswith(self.workspace + "/"):
            return rel
        if rel.startswith("output/"):
            from app.sandbox.workspace import output_directory
            output_dir = output_directory(self.workspace)
            if rel == output_dir or rel.startswith(output_dir + "/"):
                return rel
            if write:
                if rel.startswith("output/tasks/"):
                    raise sandbox.SandboxError("不能写入其他并行任务的产物目录")
                return f"{output_dir}/{rel[len('output/'):]}"
            return rel
        if rel.startswith("tasks/"):
            if write:
                raise sandbox.SandboxError("不能写入其他并行任务的目录，请写入当前任务目录")
            return rel
        own = f"{self.workspace}/{rel}"
        if write or sandbox.safe_path(self.user_id, self.chat_id, own).is_file():
            return own
        return rel  # 附件和前置产物仍可从共享根目录读取。

    def record_file(self, path: str, size: int) -> None:
        self.assert_lease()
        if not is_output_file(self.user_id, self.chat_id, path):
            return
        rec = self.db.scalar(
            select(FileRec).where(
                FileRec.chat_id == self.chat_id, FileRec.path == path
            )
        )
        if rec:
            rec.size = size
            rec.created_by_bot_id, rec.run_id = self.bot.id, self.run.id
        else:
            rec = FileRec(
                user_id=self.user_id,
                chat_id=self.chat_id,
                path=path,
                size=size,
                created_by_bot_id=self.bot.id,
                run_id=self.run.id,
            )
            self.db.add(rec)
        self.db.flush()
        emit(
            self.db,
            self.user_id,
            self.chat_id,
            "files.updated",
            {"chat_id": self.chat_id},
        )

"""执行控制：停止、逐步确认继续、计划确认，以及审批与提问的处理。"""
from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, owned_or_404
from ..events import emit
from ..models import Approval, Bot, Chat, Message, Question, Run, Task, Thread, User, now
from ..maintenance import expire_approval, expire_question
from ..orchestrator import emit_run_status, emit_task_status, settle_run_tasks
from ..redis_bus import enqueue
from ..serializers import message_out, run_out, task_out

router = APIRouter(tags=["runs"])

ACTIVE = ("queued", "running", "waiting")


def _load_run(db: Session, run_id: str, user_id: str) -> Run:
    return owned_or_404(db, Run, run_id, user_id)


def _mark_message(
    db: Session, run_id: str, type_: str, meta_patch: dict, match: dict | None = None
) -> None:
    """精确更新卡片状态：有 match 时按 meta 字段匹配，避免多张卡串状态。"""
    rows = db.scalars(
        select(Message).where(Message.run_id == run_id, Message.type == type_).order_by(
            Message.created_at.desc()
        )
    ).all()
    for msg in rows:
        meta = msg.meta or {}
        if match and any(meta.get(k) != v for k, v in match.items()):
            continue
        msg.meta = {**meta, **meta_patch}
        db.flush()
        return


def _invalidate_pending(db: Session, run: Run) -> None:
    settle_run_tasks(db, run, "canceled", run.end_reason or "执行已结束")


@router.get("/runs/{run_id}")
def get_run(run_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return run_out(_load_run(db, run_id, user.id))


@router.get("/tasks/{task_id}")
def get_task(task_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    task = owned_or_404(db, Task, task_id, user.id)
    return task_out(task, db.get(Bot, task.assigned_bot_id))


@router.post("/runs/{run_id}/stop")
def stop_run(run_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    run = _load_run(db, run_id, user.id)
    if run.status not in ACTIVE:
        raise HTTPException(400, "该执行已结束")
    run.stop_requested = True
    run.status = "stopped"
    run.ended_at = now()
    run.end_reason = "用户停止"
    run.resume_payload = None
    db.flush()
    _invalidate_pending(db, run)
    main = db.scalar(select(Thread).where(Thread.chat_id == run.chat_id, Thread.type == "main"))
    if main:
        from ..orchestrator import add_message

        add_message(
            db,
            thread=main,
            run_id=run.id,
            sender_type="system",
            type_="system",
            content="执行已被用户停止，已完成的步骤与产物保留。",
        )
    emit_run_status(db, run)
    return run_out(run)


class ContinueIn(BaseModel):
    note: str | None = None


@router.post("/runs/{run_id}/continue")
def continue_run(
    run_id: str,
    payload: ContinueIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    run = _load_run(db, run_id, user.id)
    if run.status != "waiting":
        raise HTTPException(400, "当前执行不在等待状态")
    resume = run.resume_payload or {}
    if resume.get("kind") != "step":
        raise HTTPException(400, "当前暂停点不是逐步确认")
    if payload.note and payload.note.strip():
        from ..orchestrator import add_message
        main = db.scalar(select(Thread).where(Thread.chat_id == run.chat_id, Thread.type == "main"))
        if main:
            add_message(db, thread=main, run_id=run.id, sender_type="user", type_="text",
                        content=payload.note.strip(), meta={"kind": "interjection"})
    # 保留 task_id：恢复时要把子任务结果作为 delegate 的工具结果回给总指挥
    run.resume_payload = {
        "kind": "step",
        "phase": "supervisor",
        "note": payload.note or "",
        "task_id": resume.get("task_id"),
        "task_title": resume.get("task_title"),
        "thread_id": resume.get("thread_id"),
        "bot_id": resume.get("bot_id"),
    }
    run.status = "queued"
    db.flush()
    emit_run_status(db, run)
    db.commit()
    enqueue(run.id)
    return run_out(run)


class PlanApproveIn(BaseModel):
    steps: list[dict] | None = None


@router.post("/runs/{run_id}/plan/approve")
def approve_plan(
    run_id: str,
    payload: PlanApproveIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    run = _load_run(db, run_id, user.id)
    if run.status != "waiting":
        raise HTTPException(400, "当前执行不在等待状态")
    resume = run.resume_payload or {}
    if resume.get("kind") != "plan":
        raise HTTPException(400, "当前暂停点不是计划确认")
    steps = payload.steps or resume.get("plan") or []
    if not steps:
        raise HTTPException(400, "计划不能为空")
    _mark_message(db, run.id, "plan", {"status": "approved", "steps": steps})
    # 保留 propose_plan 的 tool_call：恢复时要给它补一条 tool_result，
    # 否则模型会收到“assistant tool_calls 后面没有 tool 消息”的 400。
    run.resume_payload = {
        "kind": "plan",
        "phase": "supervisor",
        "plan": steps,
        "thread_id": resume.get("thread_id"),
        "bot_id": resume.get("bot_id"),
        "tool_call": resume.get("tool_call"),
    }
    run.status = "queued"
    db.flush()
    emit_run_status(db, run)
    db.commit()
    enqueue(run.id)
    return run_out(run)


@router.post("/runs/{run_id}/plan/cancel")
def cancel_plan(run_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    run = _load_run(db, run_id, user.id)
    if run.status != "waiting":
        raise HTTPException(400, "当前执行不在等待状态")
    resume = run.resume_payload or {}
    if resume.get("kind") != "plan":
        raise HTTPException(400, "当前暂停点不是计划确认")
    run.status = "stopped"
    run.ended_at = now()
    run.end_reason = "用户取消计划"
    run.resume_payload = None
    db.flush()
    _mark_message(db, run.id, "plan", {"status": "canceled"})
    _invalidate_pending(db, run)
    emit_run_status(db, run)
    return run_out(run)


# --------------------------------------------------------------------------- #
# 审批
# --------------------------------------------------------------------------- #
class RejectIn(BaseModel):
    reason: str | None = None


class ApproveIn(BaseModel):
    # APV-06：勾选后把该工具加入当前群聊的白名单（L3 不支持）
    always_allow: bool = False


def _expire_if_needed(db: Session, ap: Approval) -> None:
    if expire_approval(db, ap):
        # 保留过期处理结果，即使随后对已过期审批返回 400。
        db.commit()


def _resolve_approval(
    db: Session,
    ap: Approval,
    approved: bool,
    reason: str | None,
    always_allow: bool = False,
):
    run = db.get(Run, ap.run_id)
    if not run or run.status not in ACTIVE:
        raise HTTPException(400, "所属执行已结束，无法处理该审批")
    # APV-06：勾选「本群聊始终允许」时写入白名单；L3 工具不可绕过审批
    if approved and always_allow and ap.level != "L3":
        chat = db.get(Chat, run.chat_id)
        if chat:
            tools = list(chat.always_allow_tools or [])
            if ap.tool not in tools:
                tools.append(ap.tool)
                chat.always_allow_tools = tools
                db.flush()
                emit(
                    db,
                    ap.user_id,
                    run.chat_id,
                    "chat.always_allow",
                    {"chat_id": run.chat_id, "tool": ap.tool, "always_allow_tools": tools},
                )
    ap.status = "approved" if approved else "rejected"
    ap.resolved_at = now()
    ap.reject_reason = reason if not approved else None
    db.flush()
    _mark_message(
        db,
        ap.run_id,
        "approval",
        {"status": ap.status, "reject_reason": reason},
        match={"approval_id": ap.id},
    )
    emit(
        db,
        ap.user_id,
        run.chat_id,
        "approval.resolved",
        {"approval_id": ap.id, "run_id": run.id, "status": ap.status},
    )
    if ap.task_id and approved:
        task = db.get(Task, ap.task_id)
        if task and task.status in ("waiting_approval", "waiting_reply"):
            task.status = "running"
            db.flush()
            emit_task_status(db, task)
    run.status = "queued"
    run.resume_payload = {
        "kind": "approval",
        "phase": "task" if ap.task_id else "supervisor",
        "task_id": ap.task_id,
        "approval_id": ap.id,
        "bot_id": ap.bot_id,
        "thread_id": (run.resume_payload or {}).get("thread_id"),
        "tool_call": (run.resume_payload or {}).get("tool_call"),
    }
    db.flush()
    emit_run_status(db, run)
    db.commit()
    enqueue(run.id)
    return run


@router.get("/approvals")
def list_approvals(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Approval).where(Approval.user_id == user.id).order_by(Approval.created_at.desc())
    ).all()
    for ap in rows:
        _expire_if_needed(db, ap)
    return [
        {
            "id": a.id,
            "run_id": a.run_id,
            "tool": a.tool,
            "args": a.args,
            "level": a.level,
            "status": a.status,
            "expires_at": a.expires_at.isoformat() if a.expires_at else None,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in rows
    ]


@router.post("/approvals/{approval_id}/approve")
def approve(
    approval_id: str,
    payload: ApproveIn | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    ap = owned_or_404(db, Approval, approval_id, user.id)
    _expire_if_needed(db, ap)
    if ap.status != "pending":
        raise HTTPException(400, "该审批已失效：所属执行已结束，请在新任务产生的审批中处理。" if ap.status == "invalidated" else f"该审批已处理（{ap.status}）")
    run = _resolve_approval(
        db, ap, True, None, always_allow=bool(payload and payload.always_allow)
    )
    return run_out(run)


@router.post("/approvals/{approval_id}/reject")
def reject(
    approval_id: str,
    payload: RejectIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    ap = owned_or_404(db, Approval, approval_id, user.id)
    _expire_if_needed(db, ap)
    if ap.status != "pending":
        raise HTTPException(400, "该审批已失效：所属执行已结束，请在新任务产生的审批中处理。" if ap.status == "invalidated" else f"该审批已处理（{ap.status}）")
    run = _resolve_approval(db, ap, False, payload.reason or "用户拒绝")
    return run_out(run)


# --------------------------------------------------------------------------- #
# 提问
# --------------------------------------------------------------------------- #
class AnswerIn(BaseModel):
    answer: str


@router.post("/questions/{question_id}/answer")
def answer_question(
    question_id: str,
    payload: AnswerIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    q = owned_or_404(db, Question, question_id, user.id)
    if expire_question(db, q):
        db.commit()
    if q.status != "pending":
        raise HTTPException(400, "该问题已处理")
    if not payload.answer.strip():
        raise HTTPException(400, "回复不能为空")
    run = db.get(Run, q.run_id)
    if not run or run.status not in ACTIVE:
        raise HTTPException(400, "所属执行已结束")
    q.status = "answered"
    q.answer = payload.answer.strip()
    db.flush()
    _mark_message(
        db,
        q.run_id,
        "question",
        {"status": "answered", "answer": q.answer},
        match={"question_id": q.id},
    )
    run.status = "queued"
    run.resume_payload = {
        "kind": "question",
        "phase": "task" if q.task_id else "supervisor",
        "task_id": q.task_id,
        "question_id": q.id,
        "bot_id": q.bot_id,
        "thread_id": (run.resume_payload or {}).get("thread_id"),
        "tool_call": (run.resume_payload or {}).get("tool_call"),
    }
    db.flush()
    emit_run_status(db, run)
    db.commit()
    enqueue(run.id)
    return run_out(run)


@router.get("/runs/{run_id}/tasks")
def run_tasks(run_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    run = _load_run(db, run_id, user.id)
    rows = db.scalars(select(Task).where(Task.run_id == run.id).order_by(Task.created_at)).all()
    return [
        {
            "id": t.id,
            "thread_id": t.thread_id,
            "assigned_bot_id": t.assigned_bot_id,
            "instruction": t.instruction,
            "status": t.status,
            "result": t.result,
            "tokens_used": t.tokens_used,
        }
        for t in rows
    ]

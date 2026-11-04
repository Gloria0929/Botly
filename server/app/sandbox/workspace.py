"""并行任务使用独立目录；重试沿用原目录，共享资料仍可显式读取。"""
from app.sandbox import files


def relative_workspace(run) -> str:
    # 仅新任务显式设置目录，旧执行恢复时继续使用原来的共享工作区。
    return (run.config_snapshot or {}).get("task_workspace") or ""


def workspace_for_run(user_id: str, chat_id: str, run_id: str | None) -> str:
    if not run_id:
        return ""
    from app.core.db import SessionLocal
    from app.core.models import Run

    with SessionLocal() as db:
        run = db.get(Run, run_id)
        if not run or run.user_id != user_id or run.chat_id != chat_id:
            raise files.SandboxError("执行不属于当前账号或群聊")
        relative = relative_workspace(run)
    if relative:
        path = files.safe_path(user_id, chat_id, relative)
        path.mkdir(parents=True, exist_ok=True)
        return path.relative_to(files.chat_root(user_id, chat_id)).as_posix()
    return ""

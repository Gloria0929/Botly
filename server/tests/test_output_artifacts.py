"""文件工具、代码执行和旧脚本的产物统一归档到 output。"""
import asyncio

from sqlalchemy import select

from app.agent.orchestrator import ToolCtx
from app.core.db import SessionLocal
from app.core.models import Bot, Chat, FileRec, Run
from app.sandbox import files, runtime
from app.tools.registry import _file_read, _file_write, _run_command
from tests.conftest import register


def test_file_tools_and_same_size_edits_are_visible_in_output(client, invite):
    account = register(client, invite, "output-tools@test.dev")
    with SessionLocal() as db:
        chat = db.get(Chat, account["chat_id"])
        run = Run(user_id=account["id"], chat_id=chat.id, config_snapshot={})
        db.add(run); db.flush()
        ctx = ToolCtx(db, run, chat, db.get(Bot, chat.supervisor_bot_id), None)
        assert "output/index.html" in asyncio.run(_file_write(ctx, "index.html", "first"))
        rec = db.scalar(select(FileRec).where(FileRec.chat_id == chat.id))
        updated = rec.updated_at
        asyncio.run(_file_write(ctx, "output/index.html", "other"))
        assert rec.updated_at > updated
        assert asyncio.run(_file_read(ctx, "index.html")).endswith("other")
        assert not files.safe_path(account["id"], chat.id, "index.html").exists()
        assert not files.safe_path(account["id"], chat.id, "output/output").exists()


def test_command_collects_new_files_but_not_inputs_dependencies_or_other_tasks(client, invite, monkeypatch):
    account = register(client, invite, "output-command@test.dev")
    user, chat_id = account["id"], account["chat_id"]
    files.write_file(user, chat_id, "input.csv", "input")
    files.write_file(user, chat_id, "old.txt", "old")
    calls = []
    class Backend:
        async def run_command(self, user, chat, command, *, workspace):
            calls.append(workspace)
            files.write_file(user, chat, workspace + "/dist/index.html", "<h1>built</h1>")
            files.write_file(user, chat, "old.txt", "changed")
            files.write_file(user, chat, "exports/report.csv", "result")
            files.write_file(user, chat, "node_modules/pkg/file.js", "dependency")
            files.write_file(user, chat, "tasks/another/report.md", "other task")
            return "退出码 0"
    monkeypatch.setattr(runtime, "user_backend", lambda user: Backend())
    monkeypatch.setattr(runtime, "status", lambda user: {"provider": "docker"})
    with SessionLocal() as db:
        chat = db.get(Chat, chat_id)
        run = Run(user_id=user, chat_id=chat_id, config_snapshot={"task_workspace": "tasks/current"})
        db.add(run); db.commit()
        ctx = ToolCtx(db, run, chat, db.get(Bot, chat.supervisor_bot_id), None)
        assert "退出码 0" in asyncio.run(_run_command(ctx, "build"))
        db.commit()
        records = db.scalars(select(FileRec).where(FileRec.chat_id == chat_id)).all()
        assert {rec.path for rec in records} == {
            "output/tasks/current/dist/index.html", "output/tasks/current/old.txt", "output/tasks/current/exports/report.csv",
        }
        assert all(rec.run_id == run.id for rec in records)
    assert calls == ["output/tasks/current"]
    assert files.read_file(user, chat_id, "output/tasks/current/exports/report.csv") == "result"
    assert files.read_file(user, chat_id, "input.csv") == "input"
    assert not files.safe_path(user, chat_id, "output/tasks/current/input.csv").exists()

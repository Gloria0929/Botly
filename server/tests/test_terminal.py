"""终端手动命令、Agent 记录、账号隔离及产物筛选。"""
import asyncio
from datetime import timedelta

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.models import Bot, Chat, CubeSession, FileRec, Run, TerminalCommand, now
from app.sandbox import cube, files, runtime, terminal
from app.sandbox.artifacts import is_artifact_path
from app.agent.orchestrator import ToolCtx
from app.tools.registry import _file_write, _run_command, UNTRUSTED_PREFIX
from tests.conftest import login, register, setup_workspace
from tests.test_cube import CONFIG, install_fake_sdk


def workspace(client, invite, tag, monkeypatch):
    account = register(client, invite, f"terminal-{tag}@test.dev")
    client.put("/api/sandbox/cube", json=CONFIG)
    state = install_fake_sdk(monkeypatch)
    return account, state


def test_user_terminal_is_owned_executed_immediately_and_not_replayed(client, invite, monkeypatch):
    account, state = workspace(client, invite, "manual", monkeypatch)
    chat_id = account["chat_id"]
    response = client.post(f"/api/chats/{chat_id}/terminal", json={"command": "echo terminal"})
    assert response.status_code == 201, response.text
    command_id = response.json()["id"]
    assert response.json()["status"] == "completed" and state["created"] == 1
    assert command_id not in terminal.pending()
    asyncio.run(terminal.execute(command_id, claim=True))
    history = client.get(f"/api/chats/{chat_id}/terminal").json()
    assert history["sandbox_id"] == "cube-1" and history["provider"] == "cube"
    assert history["items"][0]["status"] == "completed"
    assert "cube output" in history["items"][0]["output"]
    assert history["items"][0]["actor"] == "你"
    asyncio.run(terminal.execute(command_id, claim=True))
    assert len(state["commands"]) == 1
    # 用户手动命令产出的文件不标成 Agent 交付物。
    assert files.safe_path(account["id"], chat_id, "result.png").exists()
    assert client.get(f"/api/chats/{chat_id}/files").json() == []
    pending_id = terminal.create(account["id"], chat_id, "尚未执行的命令")
    assert pending_id in terminal.pending()
    assert client.post(f"/api/chats/{chat_id}/terminal", json={"command": "other"}).status_code == 409
    assert client.post(f"/api/chats/{chat_id}/messages", json={"content": "新任务"}).status_code == 409
    with SessionLocal() as db:
        db.get(TerminalCommand, pending_id).status = "failed"
        db.commit()
    register(client, invite, "terminal-foreign@test.dev")
    assert client.get(f"/api/chats/{chat_id}/terminal").status_code == 404
    assert client.post(f"/api/chats/{chat_id}/terminal", json={"command": "pwd"}).status_code == 404


def test_terminal_rejects_when_agent_is_running(client, invite, monkeypatch):
    account, _ = workspace(client, invite, "agent-busy", monkeypatch)
    with SessionLocal() as db:
        db.add(Run(user_id=account["id"], chat_id=account["chat_id"], status="running", config_snapshot={}))
        db.commit()
    response = client.post(f"/api/chats/{account['chat_id']}/terminal", json={"command": "pwd"})
    assert response.status_code == 409
    assert client.get(f"/api/chats/{account['chat_id']}/terminal").json()["agent_running"]


def test_failed_terminal_keeps_output_and_stale_command_is_not_replayed(client, invite, monkeypatch):
    account, _ = workspace(client, invite, "failure", monkeypatch)
    async def fail(*args, **kwargs):
        return UNTRUSTED_PREFIX + "退出码 2\ncommand failed"
    monkeypatch.setattr(runtime, "run_command", fail)
    command_id = client.post(f"/api/chats/{account['chat_id']}/terminal", json={"command": "bad"}).json()["id"]
    asyncio.run(terminal.execute(command_id, claim=True))
    assert client.get(f"/api/chats/{account['chat_id']}/terminal").json()["items"][0]["status"] == "failed"
    with SessionLocal() as db:
        stale = TerminalCommand(user_id=account["id"], chat_id=account["chat_id"], command="install", status="running",
            lease_until=now()-timedelta(seconds=1))
        db.add(stale); db.commit(); stale_id=stale.id
    assert stale_id not in terminal.pending()
    with SessionLocal() as db:
        assert db.get(TerminalCommand, stale_id).status == "failed"


def test_files_panel_only_shows_agent_deliverables(client, invite):
    account = register(client, invite, "terminal-artifacts@test.dev")
    chat_id, _ = setup_workspace(client)
    uploaded = client.post(f"/api/chats/{chat_id}/files", files={"file": ("input.txt", b"input")})
    assert uploaded.status_code == 201
    with SessionLocal() as db:
        chat = db.get(Chat, chat_id)
        bot = db.get(Bot, chat.supervisor_bot_id)
        run = Run(user_id=account["id"], chat_id=chat_id, status="completed", config_snapshot={})
        db.add(run); db.commit()
        ctx = ToolCtx(db, run, chat, bot, None)
        for path in ("report.md", "skills/reader/SKILL.md", "node_modules/lib/index.js", ".cache/tool.json"):
            asyncio.run(_file_write(ctx, path, "测试内容"))
        # 历史记录中的内部文件也不能进入产物面板。
        db.add(FileRec(user_id=account["id"], chat_id=chat_id, created_by_bot_id=bot.id, path="skills/reader/SKILL.md", size=10))
        db.commit()
    assert {row["path"] for row in client.get(f"/api/chats/{chat_id}/files").json()} == {"report.md"}
    assert {row["path"] for row in client.get(f"/api/chats/{chat_id}").json()["files"]} == {"report.md"}
    assert files.read_file(account["id"], chat_id, "skills/reader/SKILL.md") == "测试内容"
    # 附件仍可下载和供 Agent 读取。
    assert client.get(f"/api/files/{uploaded.json()['id']}/download").content == b"input"


def test_remote_installed_skill_stays_in_cube_and_upload_is_not_reclassified(client, invite, monkeypatch):
    account, state = workspace(client, invite, "remote-files", monkeypatch)
    chat_id = account["chat_id"]
    with SessionLocal() as db:
        bot = db.get(Bot, db.get(Chat, chat_id).supervisor_bot_id)
        run = Run(user_id=account["id"], chat_id=chat_id, status="completed", config_snapshot={})
        db.add(run); db.commit()
        bot_id, run_id = bot.id, run.id
    backend = cube.CubeBackend(cube.connection_for(account["id"]))
    box, _ = backend._open(account["id"], chat_id)
    box.data.update({"/workspace/skills/reader/SKILL.md": b"installed", "/workspace/reader/SKILL.md": b"installed",
        "/workspace/reader/references/notes.txt": b"reference", "/workspace/node_modules/lib/index.js": b"library",
        "/workspace/template.txt": b"template"})
    cube.close_client(box)
    uploaded = client.post(f"/api/chats/{chat_id}/files", files={"file": ("input.txt", b"input")}).json()
    with SessionLocal() as db:
        ctx = ToolCtx(db, db.get(Run, run_id), db.get(Chat, chat_id), db.get(Bot, bot_id), None)
        result = asyncio.run(_run_command(ctx, "echo report"))
        assert "cube output" in result
        db.commit()
    assert {row["path"] for row in client.get(f"/api/chats/{chat_id}/files").json()} == {"result.png"}
    assert not files.safe_path(account["id"], chat_id, "skills/reader/SKILL.md").exists()
    assert not files.safe_path(account["id"], chat_id, "reader/references/notes.txt").exists()
    assert box.data["/workspace/skills/reader/SKILL.md"] == b"installed"
    assert client.get(f"/api/files/{uploaded['id']}/download").content == b"input"
    history = client.get(f"/api/chats/{chat_id}/terminal").json()["items"]
    assert history[0]["bot_id"] == bot_id and history[0]["command"] == "echo report"


def test_image_preview_stays_authenticated(client, invite, monkeypatch):
    account, _ = workspace(client, invite, "image-preview", monkeypatch)
    with SessionLocal() as db:
        bot_id = db.get(Chat, account["chat_id"]).supervisor_bot_id
        files.write_bytes(account["id"], account["chat_id"], "plot.png", b"\x89PNG-test")
        rec = FileRec(user_id=account["id"], chat_id=account["chat_id"], path="plot.png", size=9, created_by_bot_id=bot_id)
        db.add(rec); db.commit(); file_id=rec.id
    response = client.get(f"/api/files/{file_id}/preview")
    assert response.content == b"\x89PNG-test" and response.headers["content-type"].startswith("image/png")
    register(client, invite, "terminal-image-foreign@test.dev")
    assert client.get(f"/api/files/{file_id}/preview").status_code == 404

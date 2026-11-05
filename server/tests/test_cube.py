"""Cube 后端配置、账号隔离、官方 SDK 参数与文件同步。"""
import asyncio
from pathlib import PurePosixPath
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.sandbox import cube, files, runtime
from app.core.db import SessionLocal
from app.core.models import CubeConnection, CubeSession
from app.core.security import decrypt_secret
from tests.conftest import register


CONFIG = {"api_url": "http://127.0.0.1:3000", "template_id": "tpl-code", "api_key": "cube-secret-test", "proxy_node_ip": "127.0.0.1"}


@pytest.mark.parametrize("api_url,expected", [
    (" 127.0.0.1:3000/ ", "http://127.0.0.1:3000"),
    ("localhost:3000", "http://localhost:3000"),
    ("https://cube.test/api/", "https://cube.test/api"),
])
def test_cube_backend_address_accepts_omitted_protocol(client, invite, api_url, expected):
    register(client, invite, "cube-url-" + str(abs(hash(api_url))) + "@test.dev")
    response = client.put("/api/sandbox/cube", json={**CONFIG, "api_url": api_url})
    assert response.status_code == 200, response.text
    assert response.json()["api_url"] == expected


def mock_template_backend(monkeypatch, handler):
    client_class = httpx.AsyncClient
    monkeypatch.setattr(cube.httpx, "AsyncClient", lambda **kwargs: client_class(
        transport=httpx.MockTransport(handler), **kwargs))


@pytest.mark.parametrize("wrapper", [None, "templates", "items"])
def test_cube_templates_detect_without_template_id_or_saving(client, invite, monkeypatch, wrapper):
    register(client, invite, f"cube-discover-{wrapper}@test.dev")
    def respond(request):
        assert str(request.url) == "http://127.0.0.1:3000/api/templates"
        assert request.headers["X-API-Key"] == CONFIG["api_key"]
        assert request.extensions["timeout"]["read"] == 15
        templates = [{"templateID": "tpl-code", "aliases": ["python"], "status": "ready"},
            {"template_id": "tpl-node", "name": "Node", "status": "building"}]
        return httpx.Response(200, json={wrapper: templates} if wrapper else templates)
    mock_template_backend(monkeypatch, respond)
    response = client.post("/api/sandbox/cube/templates", json={
        "api_url": "127.0.0.1:3000/api", "api_key": CONFIG["api_key"]})
    assert response.status_code == 200, response.text
    assert response.json()["templates"] == [
        {"id": "tpl-code", "name": "python", "status": "ready"},
        {"id": "tpl-node", "name": "Node", "status": "building"}]
    assert CONFIG["api_key"] not in response.text
    assert client.get("/api/sandbox").json()["provider"] != "cube"


def test_cube_template_discovery_reuses_only_current_users_key_and_does_not_save(client, invite, monkeypatch):
    register(client, invite, "cube-discover-key@test.dev")
    client.put("/api/sandbox/cube", json=CONFIG)
    captured = []
    def respond(request):
        captured.append(request.headers.get("X-API-Key"))
        return httpx.Response(200, json=[])
    mock_template_backend(monkeypatch, respond)
    assert client.post("/api/sandbox/cube/templates", json={"api_url": "localhost:4000"}).json() == {"templates": []}
    saved = client.get("/api/sandbox").json()
    assert saved["api_url"] == CONFIG["api_url"] and saved["verification"] == "untested"
    assert client.post("/api/sandbox/cube/templates", json={"api_url": CONFIG["api_url"], "api_key": ""}).status_code == 200
    assert client.get("/api/sandbox").json()["api_key_configured"] is True
    register(client, invite, "cube-discover-isolated@test.dev")
    assert client.post("/api/sandbox/cube/templates", json={"api_url": CONFIG["api_url"]}).status_code == 200
    assert captured == [CONFIG["api_key"], None, None]


@pytest.mark.parametrize("failure", ["auth", "timeout", "invalid-json", "invalid-list", "secret"])
def test_cube_template_discovery_errors_are_reported_and_redacted(client, invite, monkeypatch, failure):
    register(client, invite, f"cube-discover-error-{failure}@test.dev")
    def respond(request):
        if failure == "auth":
            return httpx.Response(401, json={"message": "unauthorized"})
        if failure == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if failure == "secret":
            raise RuntimeError("backend rejected " + CONFIG["api_key"])
        if failure == "invalid-json":
            return httpx.Response(200, text="not JSON")
        return httpx.Response(200, json={"message": "not a template list"})
    mock_template_backend(monkeypatch, respond)
    response = client.post("/api/sandbox/cube/templates", json={
        "api_url": CONFIG["api_url"], "api_key": CONFIG["api_key"]})
    assert response.status_code == 400
    assert "Cube 模板加载失败" in response.json()["detail"]
    assert CONFIG["api_key"] not in response.text


@pytest.mark.parametrize("proxy", ["http://203.0.113.10", "https://203.0.113.10/", " 203.0.113.10 "])
def test_cube_proxy_url_normalizes_ip_and_keeps_separate_port(client, invite, proxy):
    register(client, invite, "cube-proxy-" + str(abs(hash(proxy))) + "@test.dev")
    response = client.put("/api/sandbox/cube", json={**CONFIG, "proxy_node_ip": proxy, "proxy_port": 18080})
    assert response.status_code == 200, response.text
    assert response.json()["proxy_node_ip"] == "203.0.113.10"
    assert response.json()["proxy_port"] == 18080


def test_cube_proxy_url_normalizes_before_connection_test(client, invite, monkeypatch):
    register(client, invite, "cube-proxy-test@test.dev")
    captured = []
    def probe(connection):
        captured.append((connection.proxy_node_ip, connection.proxy_port))
        return {"ok": True, "message": "ready"}
    monkeypatch.setattr(cube, "probe", probe)
    response = client.post("/api/sandbox/cube/test", json={**CONFIG,
        "proxy_node_ip": "http://203.0.113.10", "proxy_port": 18080})
    assert response.status_code == 200, response.text
    assert captured == [("203.0.113.10", 18080)]


@pytest.mark.parametrize("proxy", ["http://203.0.113.10/admin", "http://user:password@203.0.113.10",
    "http://203.0.113.10?key=secret", "http://203.0.113.10:18081", "http://203.0.113.10:0"])
def test_cube_invalid_proxy_urls_are_rejected(client, invite, proxy):
    register(client, invite, "cube-proxy-invalid-" + str(abs(hash(proxy))) + "@test.dev")
    response = client.put("/api/sandbox/cube", json={**CONFIG, "proxy_node_ip": proxy, "proxy_port": 18080})
    assert response.status_code == 400


def test_cube_config_is_owned_and_key_is_encrypted(client, invite):
    alice = register(client, invite, "cube-alice@test.dev")
    response = client.put("/api/sandbox/cube", json=CONFIG)
    assert response.status_code == 200, response.text
    assert response.json()["provider"] == "cube"
    assert response.json()["api_key_configured"] is True
    assert CONFIG["api_key"] not in response.text
    with SessionLocal() as db:
        saved = db.scalar(select(CubeConnection).where(CubeConnection.user_id == alice["id"]))
        assert saved.api_key_encrypted != CONFIG["api_key"]
        assert decrypt_secret(saved.api_key_encrypted) == CONFIG["api_key"]
    keep = {k: v for k, v in CONFIG.items() if k != "api_key"}
    assert client.put("/api/sandbox/cube", json=keep).json()["api_key_configured"] is True
    assert client.put("/api/sandbox/cube", json={**keep, "api_key": ""}).json()["api_key_configured"] is False
    register(client, invite, "cube-bob@test.dev")
    assert client.get("/api/sandbox").json()["provider"] != "cube"
    assert client.get("/api/settings").json()["sandbox"]["provider"] != "cube"
    assert client.delete("/api/sandbox/cube").status_code == 200
    assert cube.connection_for(alice["id"]) is not None


@pytest.mark.parametrize("patch", [{"api_url": "file:///etc/passwd"}, {"api_url": "http://u:p@localhost:3000"},
    {"api_url": "http://[invalid"}, {"api_url": "http://localhost:0"}, {"api_url": "http://bad host:3000"},
    {"proxy_node_ip": "127.0.0.1:80"}, {"sandbox_domain": "http://cube.app"}, {"template_id": " "}])
def test_cube_invalid_configuration_is_rejected(client, invite, patch):
    register(client, invite, "cube-invalid-" + str(abs(hash(str(patch)))) + "@test.dev")
    assert client.put("/api/sandbox/cube", json={**CONFIG, **patch}).status_code == 400


def install_fake_sdk(monkeypatch):
    state = {"sandboxes": {}, "created": 0, "connected": [], "commands": [], "killed": []}
    class FS:
        def __init__(self, box):
            self.box = box
            self.directories = {"/", "/tmp", "/workspace"}
        def _base_headers(self): return {"X-Access-Token": "envd-test-token"}
        def exists(self, path): return path in self.directories or path in self.box.data
        def make_dir(self, path):
            if path in self.directories:
                raise OSError("Filesystem MakeDir failed: already_exists")
            self.directories.add(path)
            return {}
        def write(self, path, data): self.box.data[path] = data.encode() if isinstance(data, str) else data
        def read(self, path): return self.box.data[path].decode()
        def remove(self, path): self.box.data.pop(path, None)
        def list(self, directory):
            entries = {}
            root = PurePosixPath(directory)
            for path, data in self.box.data.items():
                try: rel = PurePosixPath(path).relative_to(root)
                except ValueError: continue
                if len(rel.parts) == 1:
                    entries[rel.name] = {"name": rel.name, "type": "file", "size": str(len(data))}
                else: entries[rel.parts[0]] = {"name": rel.parts[0], "type": "directory"}
            return list(entries.values())
    class Commands:
        def __init__(self, box): self.box = box
        def run(self, command, **kwargs):
            state["commands"].append((self.box.sandbox_id, command, kwargs))
            if command == "printf BOTLY_CUBE_READY":
                return SimpleNamespace(exit_code=0, stdout="BOTLY_CUBE_READY", stderr="")
            self.box.data["/workspace/result.png"] = b"\x89PNG\x00\xff"
            return SimpleNamespace(exit_code=0, stdout="cube output", stderr="")
    class Box:
        def __init__(self, config):
            self.sandbox_id = f"cube-{state['created']}"
            self._config = config
            self._client = None
            self._session = SimpleNamespace(close=lambda: None)
            self.data = {}
            self.files = FS(self)
            self.commands = Commands(self)
        def _build_data_client(self):
            def handle(request):
                assert request.headers["X-API-Key"] == CONFIG["api_key"]
                path = request.url.params["path"]
                return httpx.Response(200, content=self.data[path], request=request)
            return httpx.Client(transport=httpx.MockTransport(handle))
        def get_host(self, port): return f"{port}-{self.sandbox_id}.cube.app"
        def kill(self): state["killed"].append(self.sandbox_id); state["sandboxes"].pop(self.sandbox_id, None)
    class SDK:
        @staticmethod
        def create(config, **kwargs):
            state["created"] += 1
            box = Box(config)
            state["sandboxes"][box.sandbox_id] = box
            state["create_args"] = kwargs
            return box
        @staticmethod
        def connect(sandbox_id, **kwargs):
            state["connected"].append(sandbox_id)
            box = state["sandboxes"].get(sandbox_id)
            if not box: raise cube.SandboxNotFoundError("gone", 404)
            box._client = None
            return box
    monkeypatch.setattr(cube, "Sandbox", SDK)
    return state


def test_cube_test_creates_and_releases_temporary_sandbox(client, invite, monkeypatch):
    register(client, invite, "cube-probe@test.dev")
    state = install_fake_sdk(monkeypatch)
    r = client.post("/api/sandbox/cube/test", json=CONFIG)
    assert r.status_code == 200, r.text
    assert r.json()["ok"]
    assert state["created"] == 1 and state["killed"] == ["cube-1"]
    assert client.get("/api/sandbox").json()["provider"] != "cube"  # 测试不隐式保存。


def test_cube_reuses_chat_sandbox_and_syncs_binary_files(client, invite, monkeypatch):
    account = register(client, invite, "cube-execute@test.dev")
    assert client.put("/api/sandbox/cube", json=CONFIG).status_code == 200
    state = install_fake_sdk(monkeypatch)
    files.write_file(account["id"], account["chat_id"], "input.txt", "用户附件")
    out = asyncio.run(runtime.run_command(account["id"], account["chat_id"], "echo hi"))
    assert "cube output" in out
    assert "运行环境：Cube · 沙箱 cube-1" in out
    assert state["created"] == 1
    assert state["sandboxes"]["cube-1"].data["/workspace/input.txt"] == "用户附件".encode()
    assert files.safe_path(account["id"], account["chat_id"], "result.png").read_bytes() == b"\x89PNG\x00\xff"
    with SessionLocal() as db:
        session = db.scalar(select(CubeSession).where(CubeSession.user_id == account["id"]))
        assert session.sandbox_id == "cube-1"
        assert session.last_executed_at is not None and session.last_exit_code == 0
    status = client.get("/api/sandbox").json()
    assert status["session_count"] == 1
    assert status["sessions"][0]["sandbox_id"] == "cube-1"
    assert status["sessions"][0]["chat_id"] == account["chat_id"]
    assert status["sessions"][0]["last_exit_code"] == 0
    out2 = asyncio.run(runtime.run_code(account["id"], account["chat_id"], "print(42)", "python"))
    assert "cube output" in out2 and state["created"] == 1 and state["connected"] == ["cube-1"]
    assert "python3" in state["commands"][-1][1]
    assert state["commands"][-1][2]["cwd"] == "/workspace"
    assert client.put("/api/sandbox/cube", json={**CONFIG, "template_id": "other"}).status_code == 409
    assert client.delete("/api/sandbox/cube").status_code == 200
    assert state["killed"] == ["cube-1"]
    assert files.safe_path(account["id"], account["chat_id"], "input.txt").exists()


def test_cube_probe_error_masks_api_key(client, invite, monkeypatch):
    register(client, invite, "cube-error@test.dev")
    def fail(connection): raise RuntimeError("bad token " + CONFIG["api_key"])
    monkeypatch.setattr(cube, "probe", fail)
    response = client.post("/api/sandbox/cube/test", json=CONFIG)
    assert response.status_code == 400
    assert CONFIG["api_key"] not in response.text


def test_cube_verification_tracks_saved_config_and_preserves_on_unchanged_save(client, invite, monkeypatch):
    register(client, invite, "cube-verification@test.dev")
    assert client.put("/api/sandbox/cube", json=CONFIG).json()["verification"] == "untested"
    install_fake_sdk(monkeypatch)
    tested = client.post("/api/sandbox/cube/test", json=CONFIG)
    assert tested.status_code == 200, tested.text
    assert tested.json()["sandbox"]["verification"] == "passed"
    checked = tested.json()["sandbox"]["last_checked_at"]
    # 同一个明文密钥每次加密密文不同，不能因此丢掉验证记录。
    saved = client.put("/api/sandbox/cube", json=CONFIG).json()
    assert saved["last_checked_at"] == checked and saved["verification"] == "passed"
    changed = client.put("/api/sandbox/cube", json={**CONFIG, "proxy_port": 8080}).json()
    assert changed["verification"] == "untested" and changed["last_checked_at"] is None


def test_agent_code_tool_uses_owned_cube_instead_of_local_docker(client, invite, monkeypatch):
    import json
    from app.agent.orchestrator import RunExecutor
    from app.core.models import Chat, Run, Thread
    from app.sandbox import linux
    from tests.conftest import setup_workspace

    account = register(client, invite, "cube-agent-tool@test.dev")
    chat_id, bots = setup_workspace(client)
    bot = next(bot for bot in bots if bot["name"] == "研究员")
    client.patch(f"/api/bots/{bot['id']}", json={"tools": ["code_exec"]})
    client.put("/api/sandbox/cube", json=CONFIG)
    state = install_fake_sdk(monkeypatch)
    async def no_docker(*args, **kwargs):
        raise AssertionError("Cube 配置存在时不能调用本机 Docker")
    monkeypatch.setattr(linux, "run_code", no_docker)
    run_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": "@研究员 执行代码"}).json()["run"]["id"]
    with SessionLocal() as db:
        thread_id = db.scalar(select(Thread.id).where(Thread.chat_id == chat_id, Thread.type == "main"))
    call = {"id": "cube-agent-code", "type": "function", "function": {
        "name": "code_exec", "arguments": json.dumps({"code": "print(42)", "language": "python"})}}
    result = asyncio.run(RunExecutor(run_id).execute_tool(thread_id=thread_id, bot_id=bot["id"],
        task_id=None, call=call, role="worker"))
    assert "运行环境：Cube · 沙箱 cube-1" in result
    assert state["created"] == 1 and "python3" in state["commands"][-1][1]
    assert client.get("/api/sandbox").json()["session_count"] == 1
    client.post(f"/api/runs/{run_id}/stop")


def test_cube_failed_recheck_is_saved_and_redacted(client, invite, monkeypatch):
    register(client, invite, "cube-recheck-failed@test.dev")
    client.put("/api/sandbox/cube", json=CONFIG)
    install_fake_sdk(monkeypatch)
    assert client.post("/api/sandbox/cube/test", json=CONFIG).status_code == 200
    def fail(connection): raise RuntimeError("server unreachable " + CONFIG["api_key"])
    monkeypatch.setattr(cube, "probe", fail)
    response = client.post("/api/sandbox/cube/test", json=CONFIG)
    assert response.status_code == 400
    status = client.get("/api/sandbox").json()
    assert status["verification"] == "failed" and "server unreachable" in status["last_check_error"]
    assert CONFIG["api_key"] not in str(status)


def test_unsaved_cube_test_does_not_change_saved_verification(client, invite, monkeypatch):
    register(client, invite, "cube-unsaved-check@test.dev")
    client.put("/api/sandbox/cube", json=CONFIG)
    install_fake_sdk(monkeypatch)
    response = client.post("/api/sandbox/cube/test", json={**CONFIG, "template_id": "different"})
    assert response.status_code == 200
    assert client.get("/api/sandbox").json()["verification"] == "untested"


def test_cube_tool_registers_and_removes_file_panel_records(client, invite, monkeypatch):
    from app.agent.orchestrator import ToolCtx
    from app.core.models import Bot, Chat, FileRec, Run
    from app.tools.registry import _run_command, _record_execution_files

    account = register(client, invite, "cube-file-panel@test.dev")
    assert client.put("/api/sandbox/cube", json=CONFIG).status_code == 200
    install_fake_sdk(monkeypatch)
    with SessionLocal() as db:
        run = Run(user_id=account["id"], chat_id=account["chat_id"], status="completed", config_snapshot={})
        db.add(run)
        db.commit()
        chat = db.get(Chat, account["chat_id"])
        bot = db.get(Bot, chat.supervisor_bot_id)
        ctx = ToolCtx(db, run, chat, bot, None)
        assert "cube output" in asyncio.run(_run_command(ctx, "echo test"))
        db.commit()
        rec = db.scalar(select(FileRec).where(FileRec.chat_id == chat.id, FileRec.path == "result.png"))
        assert rec is not None
        response = client.get(f"/api/files/{rec.id}/download")
        assert response.content == b"\x89PNG\x00\xff"
        files.delete_file(account["id"], chat.id, "result.png")
        _record_execution_files(ctx)
        db.commit()
    assert client.get(f"/api/chats/{account['chat_id']}/files").json() == []


def test_purge_releases_owned_cube_instances(client, invite, monkeypatch):
    from app.services.accounts import purge_user
    from app.core.models import User
    account = register(client, invite, "cube-purge@test.dev")
    assert client.put("/api/sandbox/cube", json=CONFIG).status_code == 200
    state = install_fake_sdk(monkeypatch)
    asyncio.run(runtime.run_command(account["id"], account["chat_id"], "true"))
    with SessionLocal() as db:
        purge_user(db, db.get(User, account["id"]))
        db.commit()
        assert db.scalar(select(CubeConnection).where(CubeConnection.user_id == account["id"])) is None
        assert db.scalar(select(CubeSession).where(CubeSession.user_id == account["id"])) is None
    assert state["killed"] == ["cube-1"]

import base64
import asyncio
import json
import shlex
import subprocess
from types import SimpleNamespace

import pytest

from app.api import preview
from app.sandbox import files, preview_service, preview_runner
from tests.test_site_preview import make_site


def configured_site(client, invite, label):
    account, file_id = make_site(client, invite, label)
    config = {"backend": {"cwd": "backend", "command": "npm start", "port": 3000, "api_prefix": "/api"}}
    files.write_file(account["id"], account["chat_id"], "output/site/preview.json", json.dumps(config))
    files.write_file(account["id"], account["chat_id"], "output/site/backend/server.js", "// fixture")
    return account, file_id


def test_backend_start_proxy_methods_body_cors_and_host_isolation(client, invite, monkeypatch):
    account, file_id = configured_site(client, invite, "backend-http")
    calls = []
    async def invoke(user, chat, config, action, **kwargs):
        calls.append((user, chat, config, action, kwargs))
        if action != "request":
            return {"running": action != "stop", "ready": action != "stop", "port": 3000, "log": ""}
        return {"status": 201 if kwargs["method"] == "POST" else 200,
                "headers": {"Content-Type": "application/json", "Set-Cookie": "botly_session=bad", "Location": "/api/auth/logout"},
                "body": base64.b64encode(b'{"ok":true}').decode()}
    monkeypatch.setattr(preview_service, "invoke", invoke)
    info = client.get(f"/api/files/{file_id}/site").json()
    assert info["backend"]["port"] == 3000
    html = client.get(info["url"]).text
    assert html.index("window.fetch =") < html.index("assets/main.js")
    assert "XMLHttpRequest.prototype.open" in html
    for action in ("start", "status", "stop"):
        assert client.post(f"/api/files/{file_id}/backend", json={"action": action}).status_code == 200
        assert calls[-1][3] == action
    assert calls[-1][2]["cwd"] == "/workspace/output/site/backend"
    url = info["url"].rsplit("/", 1)[0] + "/__backend/api/users?name=test"
    preflight = client.options(url, headers={"Origin": "null", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
    assert preflight.status_code == 204
    assert preflight.headers["Access-Control-Allow-Origin"] == "*"
    # 主站 API 的 null-origin 预检仍被拒绝。
    assert client.options("/api/auth/logout", headers={"Origin": "null", "Access-Control-Request-Method": "POST"}).status_code == 400
    for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        response = client.request(method, url, content=b'{"name":"test"}', headers={
            "Origin": "null", "Content-Type": "application/json", "Authorization": "Bearer app-token"})
        assert response.status_code == (201 if method == "POST" else 200)
        assert response.json() == {"ok": True}
        assert "set-cookie" not in response.headers and "location" not in response.headers
        forwarded = calls[-1][-1]
        assert forwarded["method"] == method and forwarded["path"] == "/api/users?name=test"
        assert base64.b64decode(forwarded["body"]) == b'{"name":"test"}'
        assert forwarded["headers"] == {"content-type": "application/json", "authorization": "Bearer app-token", "accept": "*/*"}
    client.cookies.clear()
    assert client.post(f"/api/files/{file_id}/backend", json={"action": "start"}).status_code == 401
    assert client.get(url).status_code == 200  # iframe 仅使用限时、项目范围的凭证。


def test_backend_proxy_rejects_configuration_changes_and_reports_unavailable_service(client, invite, monkeypatch):
    account, file_id = configured_site(client, invite, "backend-errors")
    info = client.get(f"/api/files/{file_id}/site").json()
    base = info["url"].rsplit("/", 1)[0] + "/__backend/"
    assert client.get(base + "admin").status_code == 404
    async def fail(*args, **kwargs):
        raise ValueError("private backend error")
    monkeypatch.setattr(preview_service, "invoke", fail)
    response = client.get(base + "api/users")
    assert response.status_code == 502 and "private backend error" not in response.text
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    assert client.post(base + "api/users", content=b"x" * (2 * 1024 * 1024 + 1)).status_code == 413
    files.write_file(account["id"], account["chat_id"], "output/site/preview.json", json.dumps({
        "backend": {"cwd": "backend", "command": "npm start", "port": 3001}}))
    assert client.get(base + "api/users").status_code == 409


@pytest.mark.parametrize("changes", [{"port": 49983}, {"port": 80}, {"port": "3000"},
    {"cwd": "../../../../outside"}, {"cwd": "/etc"}, {"api_prefix": "/"}, {"command": ""}])
def test_invalid_backend_configuration_is_rejected(client, invite, changes):
    account, file_id = configured_site(client, invite, "backend-invalid-" + str(abs(hash(str(changes)))))
    config = {"cwd": "backend", "command": "npm start", "port": 3000, **changes}
    files.write_file(account["id"], account["chat_id"], "output/site/preview.json", json.dumps({"backend": config}))
    assert client.get(f"/api/files/{file_id}/site").status_code == 400


def test_runner_does_not_stop_a_reused_pid_and_refuses_an_occupied_port(tmp_path, monkeypatch):
    config = {"cwd": str(tmp_path), "state_dir": str(tmp_path / "state"), "command": "node server.js", "port": 3000}
    directory = tmp_path / "state"
    directory.mkdir()
    (directory / "service.json").write_text(json.dumps({"pid": 123, "identity": "old"}))
    monkeypatch.setattr(preview_runner, "identity", lambda pid: "reused")
    def forbidden(*args):
        raise AssertionError("must not kill another process")
    monkeypatch.setattr(preview_runner.os, "killpg", forbidden)
    assert not preview_runner.run({"config": config, "action": "stop"})["running"]
    monkeypatch.setattr(preview_runner, "listening", lambda port: True)
    with pytest.raises(ValueError, match="已被占用"):
        preview_runner.run({"config": config, "action": "start"})


def test_browser_bridge_routes_dynamic_fetch_request_and_xhr_without_host_credentials():
    bridge = preview.backend_bridge("/api/preview/token/", {"api_prefix": "/api", "port": 3000})
    script = """
const assert = require('node:assert/strict');
global.location = new URL('https://botly.test/api/preview/token/index.html');
let calls = [];
global.window = {fetch: async (input, init) => { calls.push({input, init}); return {ok:true}; }};
global.XMLHttpRequest = function(){};
XMLHttpRequest.prototype.open = function(...args) { this.opened = args; };
""" + bridge + """
(async () => {
  for (const url of ['/api/users', 'api/users', 'http://localhost:3000/api/users', new URL('http://127.0.0.1:3000/api/users')]) {
    await window.fetch(url, {method:'POST', body:'data', credentials:'include'});
    const call=calls.pop();
    assert.equal(call.input, '/api/preview/token/__backend/api/users');
    assert.equal(call.init.credentials, 'omit');
    assert.equal(call.init.body, 'data');
  }
  await window.fetch(new Request('https://botly.test/api/users', {method:'POST',body:'payload'}));
  let call=calls.pop();
  assert.equal(call.input.url, 'https://botly.test/api/preview/token/__backend/api/users');
  assert.equal(await call.input.text(), 'payload');
  await window.fetch('./data.json');
  assert.equal(calls.pop().input, 'https://botly.test/api/preview/token/data.json');
  const xhr=new XMLHttpRequest(); xhr.open('PUT','/api/users/1?edit=1',true);
  assert.deepEqual(xhr.opened,['PUT','/api/preview/token/__backend/api/users/1?edit=1',true]);
})().catch(e => { console.error(e); process.exitCode=1; });
"""
    # Node 的 Request 使用绝对 URL，浏览器会自动相对于 document.baseURI 解析。
    script = script.replace("global.location =", "const NativeRequest = Request; global.Request = class extends NativeRequest { constructor(url, init) { super(new URL(url, 'https://botly.test'), init); } };\nglobal.location =")
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_cube_transport_keeps_request_body_out_of_shell_and_cleans_temporary_files(client, invite, monkeypatch):
    from app.sandbox import cube
    from tests.test_cube import CONFIG, install_fake_sdk
    account, file_id = configured_site(client, invite, "backend-cube")
    install_fake_sdk(monkeypatch)
    assert client.put("/api/sandbox/cube", json=CONFIG).status_code == 200
    connection = cube.connection_for(account["id"])
    box, _ = cube.CubeBackend(connection)._open(account["id"], account["chat_id"])
    seen = []
    def command_run(command, **kwargs):
        args = shlex.split(command)
        assert args[:2] == ["python3", "-c"]
        payload = json.loads(box.data[args[-1]])
        seen.append(payload)
        assert "user-controlled" not in command
        return SimpleNamespace(exit_code=0, stdout=json.dumps({"ready": True, "port": 3000}), stderr="")
    box.commands.run = command_run
    config = preview_service.configuration(account["id"], account["chat_id"], "output/site/dist/index.html")
    result = asyncio.run(preview_service.invoke(account["id"], account["chat_id"], config, "request",
        method="POST", path="/api/users", body="user-controlled $(never-run)", headers={"Content-Type": "application/json"}))
    assert result["ready"]
    assert seen[0]["body"] == "user-controlled $(never-run)"
    assert seen[0]["config"]["cwd"] == "/workspace/output/site/backend"
    assert not any(path.startswith("/workspace/output/.preview/requests/") for path in box.data)

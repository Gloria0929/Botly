"""TOL-05 MCP 工具 + 技能管理。

用一个本机假 MCP 服务（Streamable HTTP）验证：
  1. 接入、检测、工具暴露与 Bot 勾选；
  2. L3 审批 → 批准 → 真正调用远端工具；
  3. L1 直接执行；
  4. 删除服务时从 Bot / 技能里摘掉工具；
  5. 技能注入提示词、工具并入 Bot，以及校验与删除清理。
"""
from __future__ import annotations

import asyncio
import io
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.worker import process_one
from tests.conftest import register, setup_workspace


def run_worker(run_id: str) -> None:
    asyncio.run(process_one(run_id))


def wait_status(client, run_id: str, expect: set[str]) -> dict:
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] in expect, f"期望 {expect}，实际 {run['status']}（{run.get('end_reason')}）"
    return run


class _McpHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D102
        pass

    def _send(self, code: int, payload: dict | None = None, session: str | None = None):
        body = json.dumps(payload or {}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if session:
            self.send_header("Mcp-Session-Id", session)
        self.end_headers()
        if payload is not None:
            self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/skill.md"):
            body = (
                "---\nname: 外部写作规范\ndescription: 来自 URL 的技能\n---\n\n"
                "# 步骤\n\n先写提纲，再补事实，最后自检来源。\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        req = json.loads(self.rfile.read(length) or b"{}")
        method = req.get("method")
        if method == "initialize":
            self._send(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": req.get("id"),
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fake-mcp", "version": "1.0"},
                    },
                },
                session="test-session",
            )
        elif method == "notifications/initialized":
            self._send(202)
        elif method == "tools/list":
            self._send(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": req.get("id"),
                    "result": {
                        "tools": [
                            {
                                "name": "echo",
                                "description": "回显输入文本",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"text": {"type": "string"}},
                                    "required": ["text"],
                                },
                            }
                        ]
                    },
                },
            )
        elif method == "tools/call":
            args = (req.get("params") or {}).get("arguments") or {}
            self._send(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": req.get("id"),
                    "result": {
                        "content": [{"type": "text", "text": f"ECHO: {args.get('text', '')}"}],
                        "isError": False,
                    },
                },
            )
        else:
            self._send(400, {"jsonrpc": "2.0", "id": req.get("id"), "error": {"message": "bad method"}})


@pytest.fixture()
def fake_mcp(monkeypatch):
    from app.tools import mcp

    monkeypatch.setattr(mcp, "MCP_ALLOW_LOCAL", True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _McpHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/mcp"
    finally:
        server.shutdown()
        thread.join(timeout=3)


def _supervisor(bots: list[dict]) -> dict:
    return next(b for b in bots if b["role"] == "总指挥")


def _fake_tool_model(monkeypatch, tool_name: str, expected: str):
    """总指挥第一轮调用工具，拿到结果后给最终回答。"""
    from app.agent import orchestrator

    state = {"turn": 0}

    async def fake_stream_chat(**kw):
        state["turn"] += 1
        if state["turn"] == 1:
            assert tool_name in {t["function"]["name"] for t in kw["tools"]}
            yield {
                "type": "done",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-mcp-1",
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps({"text": "hi"}, ensure_ascii=False),
                        },
                    }
                ],
            }
            return
        assert any(
            m["role"] == "tool" and expected in m["content"] for m in kw["messages"]
        ), "工具结果要回到模型上下文"
        yield {"type": "done", "content": "工具调用完成", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", fake_stream_chat)


def _create_server(client, url: str, level: str = "L3") -> dict:
    r = client.post(
        "/api/mcp/servers",
        json={"name": "Fake Tools", "url": url, "level": level},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_mcp_create_probe_and_expose_tools(client, invite, fake_mcp):
    register(client, invite, "mcp-crud@test.dev")
    data = _create_server(client, fake_mcp)
    assert data["status"] == "ok"
    assert data["tool_count"] == 1
    exposed = data["tools"][0]["name"]
    assert exposed == "mcp__faketools__echo"

    tools = {t["name"]: t for t in client.get("/api/bots/tools").json()}
    assert tools[exposed]["level"] == "L3"
    assert tools[exposed]["source"] == "mcp"

    # 未允许本机地址时拒绝接入（SSRF 防护）
    from app.tools import mcp

    original = mcp.MCP_ALLOW_LOCAL
    mcp.MCP_ALLOW_LOCAL = False
    try:
        bad = client.post(
            "/api/mcp/servers",
            json={"name": "Blocked", "url": "http://127.0.0.1:9/mcp"},
        )
        assert bad.status_code == 400
        assert "内网" in bad.json()["detail"] or "本机" in bad.json()["detail"]
    finally:
        mcp.MCP_ALLOW_LOCAL = original


def test_mcp_tool_requires_approval_then_executes(client, invite, fake_mcp, monkeypatch):
    register(client, invite, "mcp-run@test.dev")
    chat_id, bots = setup_workspace(client)
    sup = _supervisor(bots)
    data = _create_server(client, fake_mcp, level="L3")
    exposed = data["tools"][0]["name"]
    assert client.patch(
        f"/api/bots/{sup['id']}", json={"tools": [exposed]}
    ).status_code == 200

    _fake_tool_model(monkeypatch, exposed, "ECHO: hi")
    run_id = client.post(
        f"/api/chats/{chat_id}/messages", json={"content": "调用一下 MCP 工具"}
    ).json()["run"]["id"]
    run_worker(run_id)
    run = wait_status(client, run_id, {"waiting"})
    assert run["resume"]["kind"] == "approval"

    pending = [a for a in client.get("/api/approvals").json() if a["status"] == "pending"]
    assert pending and pending[0]["tool"] == exposed
    assert pending[0]["level"] == "L3"
    assert client.post(f"/api/approvals/{pending[0]['id']}/approve", json={}).status_code == 200

    run_worker(run_id)
    wait_status(client, run_id, {"completed"})
    msgs = client.get(f"/api/chats/{chat_id}/messages?limit=100").json()["messages"]
    assert any(m["content"] == "工具调用完成" for m in msgs)


def test_mcp_l1_tool_runs_without_approval(client, invite, fake_mcp, monkeypatch):
    register(client, invite, "mcp-l1@test.dev")
    chat_id, bots = setup_workspace(client)
    sup = _supervisor(bots)
    data = _create_server(client, fake_mcp, level="L1")
    exposed = data["tools"][0]["name"]
    client.patch(f"/api/bots/{sup['id']}", json={"tools": [exposed]})

    _fake_tool_model(monkeypatch, exposed, "ECHO: hi")
    run_id = client.post(
        f"/api/chats/{chat_id}/messages", json={"content": "调用 MCP"}
    ).json()["run"]["id"]
    run_worker(run_id)
    wait_status(client, run_id, {"completed"})


def test_delete_mcp_server_strips_tools(client, invite, fake_mcp):
    register(client, invite, "mcp-delete@test.dev")
    _, bots = setup_workspace(client)
    sup = _supervisor(bots)
    data = _create_server(client, fake_mcp)
    exposed = data["tools"][0]["name"]
    client.patch(f"/api/bots/{sup['id']}", json={"tools": [exposed]})
    skill = client.post(
        "/api/skills", json={"name": "MCP 技能", "tools": [exposed]}
    ).json()
    assert exposed in skill["tools"]

    assert client.delete(f"/api/mcp/servers/{data['id']}").status_code == 200
    bot = client.get(f"/api/bots/{sup['id']}").json()
    assert exposed not in bot["tools"]
    skills = {s["id"]: s for s in client.get("/api/skills").json()}
    assert exposed not in skills[skill["id"]]["tools"]


def test_skill_prompt_and_tools_merge(client, invite, monkeypatch):
    from app.agent import orchestrator

    register(client, invite, "skill-run@test.dev")
    chat_id, bots = setup_workspace(client)
    sup = _supervisor(bots)
    # Bot 自己不勾 file_delete，只靠技能带入
    client.patch(f"/api/bots/{sup['id']}", json={"tools": [], "skills": []})
    skill = client.post(
        "/api/skills",
        json={
            "name": "清理规范",
            "description": "删除文件前先确认",
            "prompt": "先列出文件，再删除不再需要的产物，并说明原因。",
            "tools": ["file_delete"],
        },
    ).json()
    assert client.patch(
        f"/api/bots/{sup['id']}", json={"skills": [skill["id"]]}
    ).status_code == 200

    seen: dict = {}

    async def fake_stream_chat(**kw):
        seen["system"] = kw["messages"][0]["content"]
        seen["tools"] = {t["function"]["name"] for t in kw["tools"]}
        results = [m["content"] for m in kw["messages"] if m["role"] == "tool"]
        if not results:
            yield {"type": "done", "content": "", "tool_calls": [{
                "id": "read-cleanup-skill", "type": "function",
                "function": {"name": "skill_file_read", "arguments": json.dumps(
                    {"path": "清理规范/SKILL.md"}, ensure_ascii=False
                )},
            }]}
            return
        assert skill["prompt"] in results[-1]
        yield {"type": "done", "content": "好的", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", fake_stream_chat)
    run_id = client.post(
        f"/api/chats/{chat_id}/messages", json={"content": "聊聊清理规范"}
    ).json()["run"]["id"]
    run_worker(run_id)
    wait_status(client, run_id, {"completed"})
    assert "已启用的技能" in seen["system"]
    assert "清理规范" in seen["system"]
    assert "清理规范/SKILL.md" in seen["system"]
    assert skill["prompt"] not in seen["system"]
    assert "file_delete" in seen["tools"], "技能里的工具要并入 Bot 可用工具"

    assert client.delete(f"/api/skills/{skill['id']}").status_code == 200
    assert client.get(f"/api/bots/{sup['id']}").json()["skills"] == []


def test_skill_validation(client, invite):
    register(client, invite, "skill-validate@test.dev")
    _, bots = setup_workspace(client)
    sup = _supervisor(bots)
    r = client.post("/api/skills", json={"name": "坏技能", "tools": ["nope"]})
    assert r.status_code == 400
    assert "未知工具" in r.json()["detail"]

    assert client.patch(
        f"/api/bots/{sup['id']}", json={"skills": ["not-a-skill"]}
    ).status_code == 400


def _zip_package() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "demo-skill/SKILL.md",
            "---\nname: 数据清洗\ndescription: 清理 CSV 的固定流程\ntools:\n  - file_read\n---\n\n"
            "# 步骤\n\n先看表头，再处理缺失值，最后抽样核对。\n",
        )
        zf.writestr("demo-skill/reference/checklist.md", "缺失值检查清单：空值、重复行、类型不一致。")
        zf.writestr("demo-skill/examples/sample.csv", "a,b\n1,2\n")
    return buf.getvalue()


def test_import_zip_skill_and_read_reference(client, invite, monkeypatch):
    from app.agent import orchestrator

    register(client, invite, "skill-import@test.dev")
    chat_id, bots = setup_workspace(client)
    sup = _supervisor(bots)
    client.patch(f"/api/bots/{sup['id']}", json={"tools": []})

    r = client.post(
        "/api/skills/import",
        files={"file": ("demo.zip", _zip_package(), "application/zip")},
    )
    assert r.status_code == 201, r.text
    skill = r.json()
    assert skill["name"] == "数据清洗"
    assert skill["source"] == "zip"
    assert "reference/checklist.md" in skill["files"]
    assert "examples/sample.csv" in skill["files"]
    assert skill["tools"] == ["file_read"]

    content = client.get(f"/api/skills/{skill['id']}/files/reference/checklist.md")
    assert content.status_code == 200
    assert "缺失值检查清单" in content.text

    client.patch(f"/api/bots/{sup['id']}", json={"skills": [skill["id"]]})
    turn = {"n": 0}

    async def fake_stream_chat(**kw):
        turn["n"] += 1
        names = {t["function"]["name"] for t in kw["tools"]}
        assert "skill_file_read" in names, "导入技能带参考文件时要自动提供读取工具"
        assert "先看表头" not in kw["messages"][0]["content"]
        if turn["n"] <= 2:
            path = "数据清洗/SKILL.md" if turn["n"] == 1 else "数据清洗/reference/checklist.md"
            if turn["n"] == 2:
                assert any(
                    m["role"] == "tool" and "先看表头" in m["content"] for m in kw["messages"]
                )
            yield {
                "type": "done",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"call-skill-file-{turn['n']}",
                        "type": "function",
                        "function": {
                            "name": "skill_file_read",
                            "arguments": json.dumps(
                                {"path": path}, ensure_ascii=False
                            ),
                        },
                    }
                ],
            }
            return
        assert any(
            m["role"] == "tool" and "缺失值检查清单" in m["content"] for m in kw["messages"]
        )
        yield {"type": "done", "content": "按技能完成", "tool_calls": []}

    monkeypatch.setattr(orchestrator, "stream_chat", fake_stream_chat)
    run_id = client.post(
        f"/api/chats/{chat_id}/messages", json={"content": "按技能清理数据"}
    ).json()["run"]["id"]
    run_worker(run_id)
    wait_status(client, run_id, {"completed"})


def test_import_skill_from_url(client, invite, fake_mcp, monkeypatch):
    register(client, invite, "skill-url@test.dev")
    base = fake_mcp.rsplit("/", 1)[0]
    r = client.post("/api/skills/import-url", json={"url": f"{base}/skill.md"})
    assert r.status_code == 201, r.text
    skill = r.json()
    assert skill["name"] == "外部写作规范"
    assert skill["source"] == "url"
    assert skill["source_url"] == f"{base}/skill.md"
    assert "先写提纲" in skill["prompt"]
    assert skill["files"] == ["SKILL.md"]


def test_skill_package_rejects_path_traversal_and_missing_manifest(client, invite, monkeypatch):
    register(client, invite, "skill-bad-zip@test.dev")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("README.md", "没有 SKILL.md")
    r = client.post(
        "/api/skills/import", files={"file": ("bad.zip", buf.getvalue(), "application/zip")}
    )
    assert r.status_code == 400
    assert "SKILL.md" in r.json()["detail"]


def test_import_skill_from_clawhub(client, invite, monkeypatch):
    from app.tools import skills as skillmod

    register(client, invite, "skill-clawhub@test.dev")
    seen = {}

    async def fake_fetch_clawhub(slug: str):
        seen["slug"] = slug
        return skillmod.SkillPackage(
            name="Notion 操作",
            description="按规范操作 Notion",
            prompt="先读 SKILL.md 再调用接口。",
            source="clawhub",
            source_url=f"https://clawhub.ai/api/v1/download?slug={slug}",
        )

    monkeypatch.setattr(skillmod, "fetch_clawhub", fake_fetch_clawhub)
    r = client.post("/api/skills/import-clawhub", json={"slug": "fangkelvin/find-skills-skill"})
    assert r.status_code == 201, r.text
    assert seen["slug"] == "fangkelvin/find-skills-skill"
    skill = r.json()
    assert skill["name"] == "Notion 操作"
    assert skill["source"] == "clawhub"
    assert skill["prompt"] == "先读 SKILL.md 再调用接口。"


def test_clawhub_owner_slug_parse():
    from app.tools import skills as skillmod

    assert skillmod._clawhub_owner_slug("fangkelvin/find-skills-skill") == (
        "fangkelvin",
        "find-skills-skill",
    )
    assert skillmod._clawhub_owner_slug(
        "https://clawhub.ai/fangkelvin/skills/find-skills-skill"
    ) == ("fangkelvin", "find-skills-skill")
    assert skillmod._clawhub_owner_slug("find-skills") is None
    assert skillmod._clawhub_owner_slug("https://clawhub.ai/api/v1/download?slug=x") is None


def test_fetch_github_precise_lookup(monkeypatch):
    from app.tools import skills as skillmod

    tree = [
        "README.md",
        "src/main.py",
        "skills/coding-agent/SKILL.md",
        "skills/coding-agent/reference.md",
        "skills/coding-agent/scripts/run.py",
    ]

    async def fake_tree(owner, repo, ref):
        assert owner == "zhaoxinghua09-cell"
        assert repo == "coding-agent"
        return tree

    downloaded = {}

    async def fake_download(url):
        downloaded[url] = url
        if url.endswith("SKILL.md"):
            return b"---\nname: Coding Agent\ndescription: coding\n---\n\n# \xe6\xad\xa5\xe9\xaa\xa4\n"
        return b"reference content"

    monkeypatch.setattr(skillmod, "_github_tree_paths", fake_tree)
    monkeypatch.setattr(skillmod, "_download_bytes", fake_download)

    import asyncio

    pkg = asyncio.run(skillmod.fetch_github("zhaoxinghua09-cell/coding-agent"))
    assert pkg.name == "Coding Agent"
    assert pkg.source == "github"
    # 精确命中 skills/coding-agent/SKILL.md（路径最浅），而不是 other/SKILL.md 或根目录兜底
    assert any("skills/coding-agent/SKILL.md" in u for u in downloaded)
    assert "reference.md" in pkg.files
    assert "scripts/run.py" in pkg.files  # 子目录脚本也要随技能安装到沙箱。

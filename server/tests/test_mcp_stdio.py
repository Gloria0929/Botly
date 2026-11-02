"""TOL-05 扩展：MCP stdio 传输（本地进程 + 换行分隔 JSON-RPC）。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from app.core.config import DATA_DIR
from tests.conftest import register, setup_workspace

FAKE_STDIO = r'''
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except Exception:
        continue
    m = msg.get("method"); rid = msg.get("id")
    if m == "initialize":
        sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":rid,"result":{
            "protocolVersion":"2024-11-05","capabilities":{"tools":{}},
            "serverInfo":{"name":"fake-stdio","version":"1"}}})+"\n"); sys.stdout.flush()
    elif m == "tools/list":
        sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":rid,"result":{"tools":[
            {"name":"echo","description":"回显","inputSchema":{"type":"object",
             "properties":{"text":{"type":"string"}},"required":["text"]}}]}})+"\n"); sys.stdout.flush()
    elif m == "tools/call":
        args = (msg.get("params") or {}).get("arguments") or {}
        sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":rid,"result":{"content":[
            {"type":"text","text":"ECHO:" + str(args.get("text",""))}],"isError":False}})+"\n"); sys.stdout.flush()
    # notifications/initialized 等无 id 的通知不回包
'''


def _fake_script() -> str:
    # 写到测试自己的 DATA_DIR（conftest 已建），避免 pytest tmp_path 触发沙箱文件钩子
    path = Path(DATA_DIR) / "fake_mcp_stdio.py"
    path.write_text(FAKE_STDIO, encoding="utf-8")
    return str(path)


def test_stdio_list_and_call():
    from app.tools import mcp

    script = _fake_script()
    tools = asyncio.run(mcp._stdio_list_tools(sys.executable, [script], {}))
    assert tools and tools[0]["remote_name"] == "echo"

    out = asyncio.run(mcp._stdio_call(sys.executable, [script], {}, "echo", {"text": "hi"}))
    assert "ECHO:hi" in out


def test_mcp_stdio_server_create_and_detect(client, invite):
    register(client, invite, "mcp-stdio@test.dev")
    setup_workspace(client)
    script = _fake_script()

    r = client.post(
        "/api/mcp/servers",
        json={
            "name": "Stdio Tool",
            "transport": "stdio",
            "command": sys.executable,
            "args": [script],
            "level": "L1",
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["status"] == "ok"
    assert data["tool_count"] == 1
    assert data["tools"][0]["name"].startswith("mcp__")

    bad = client.post(
        "/api/mcp/servers",
        json={"name": "Bad Stdio", "transport": "stdio", "command": ""},
    )
    assert bad.status_code == 400


def test_mcp_import_json(client, invite):
    register(client, invite, "mcp-import@test.dev")
    setup_workspace(client)
    script = _fake_script()

    r = client.post(
        "/api/mcp/import",
        json={
            "mcpServers": {
                "从JSON导入的": {"command": sys.executable, "args": [script], "level": "L1"},
                "坏配置": {"nothing": True},
            }
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["created"]) == 1
    assert data["created"][0]["name"] == "从JSON导入的"
    assert data["created"][0]["status"] == "ok"
    assert data["errors"][0]["name"] == "坏配置"

    # 重名导入 → skipped
    r2 = client.post(
        "/api/mcp/import",
        json={"mcpServers": {"从JSON导入的": {"command": sys.executable, "args": [script]}}},
    )
    assert r2.json()["skipped"][0]["name"] == "从JSON导入的"

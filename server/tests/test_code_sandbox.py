"""TOL-06：代码执行（跑在群聊那台常驻 Ubuntu 容器里）。

这里只做环境无关的单元验证：工具注册、语言映射、缺解释器时的兜底。
真实容器执行依赖本机 Docker，不在 CI 里跑。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.sandbox import linux as linux_sandbox
from app.tools import registry as tools


def test_code_exec_registered():
    tool = tools.BUILTIN_TOOLS.get("code_exec")
    assert tool is not None
    assert tool.kind == "builtin"
    assert tool.level in ("L1", "L2", "L3")
    # 容器里首次跑可能要装解释器，超时必须比默认 30 秒宽
    assert tool.timeout and tool.timeout > 30


def test_code_exec_disabled(monkeypatch):
    monkeypatch.setattr(linux_sandbox, "CODE_SANDBOX_ENABLED", False)
    ctx = SimpleNamespace(user_id="u1", chat_id="c1")
    out = asyncio.run(tools._code_exec(ctx, "print(1)"))
    assert "未启用" in out


def test_code_exec_rejects_unknown_language():
    out = asyncio.run(linux_sandbox.run_code("u1", "c1", "puts 1", "ruby"))
    assert "不支持的语言" in out


def test_code_exec_docker_missing(monkeypatch):
    async def no_docker(*args, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_docker)
    out = asyncio.run(linux_sandbox.run_code("u1", "c1", "print(1)"))
    assert "未找到 Docker" in out


def test_run_code_feeds_stdin_to_container(monkeypatch):
    calls: list[tuple] = []

    async def fake_ensure(user_id, chat_id):
        return "botly-linux-c1"

    async def fake_docker(*args, timeout=30, stdin=None):
        calls.append((args, stdin))
        if "command -v" in str(args[-1]):
            return "", "", 0  # 解释器已存在
        return "42\n", "", 0

    monkeypatch.setattr(linux_sandbox, "ensure_container", fake_ensure)
    monkeypatch.setattr(linux_sandbox, "_docker", fake_docker)

    out = asyncio.run(linux_sandbox.run_code("u1", "c1", "print(42)"))
    assert "42" in out
    run = next(c for c in calls if c[0][-1] == "-")
    assert run[0][:5] == ("exec", "-i", "-w", "/workspace", "botly-linux-c1")
    assert run[0][-2] == "python3"
    assert run[1] == b"print(42)"


def test_run_code_installs_missing_interpreter(monkeypatch):
    seen: list[str] = []

    async def fake_ensure(user_id, chat_id):
        return "botly-linux-c1"

    async def fake_docker(*args, timeout=30, stdin=None):
        if "command -v" in str(args[-1]):
            return "", "", 1  # 没有 python3
        if "apt-get install" in str(args[-1]):
            seen.append(str(args[-1]))
            return "", "", 0
        return "ok\n", "", 0

    monkeypatch.setattr(linux_sandbox, "ensure_container", fake_ensure)
    monkeypatch.setattr(linux_sandbox, "_docker", fake_docker)

    out = asyncio.run(linux_sandbox.run_code("u1", "c1", "print(1)"))
    assert "ok" in out
    assert seen and "python3" in seen[0]


def test_run_code_install_disabled(monkeypatch):
    async def fake_ensure(user_id, chat_id):
        return "botly-linux-c1"

    async def fake_docker(*args, timeout=30, stdin=None):
        if "command -v" in str(args[-1]):
            return "", "", 1
        return "", "", 0

    monkeypatch.setattr(linux_sandbox, "ensure_container", fake_ensure)
    monkeypatch.setattr(linux_sandbox, "_docker", fake_docker)
    monkeypatch.setattr(linux_sandbox, "LINUX_SANDBOX_AUTO_INSTALL", False)

    out = asyncio.run(linux_sandbox.run_code("u1", "c1", "print(1)"))
    assert "run_command" in out


def test_strip_fence():
    assert tools._strip_fence("```python\nprint(1)\n```") == "print(1)"
    assert tools._strip_fence("print(1)") == "print(1)"

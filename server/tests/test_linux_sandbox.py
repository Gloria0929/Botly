"""常驻 Linux 状态环境（run_command）的单元测试（不依赖真实 Docker）。"""
from __future__ import annotations

import asyncio

from app.tools import registry as tools


def test_run_command_registered():
    tool = tools.BUILTIN_TOOLS.get("run_command")
    assert tool is not None
    assert tool.kind == "builtin"
    assert tool.level == "L3"  # root + 联网 = 默认审批，支持显式群聊授权
    # 容器内命令自身 60 秒超时，外层必须更宽，否则永远取不到结果
    assert tool.timeout and tool.timeout > 60


def test_ensure_container_docker_args(monkeypatch):
    from app.sandbox import linux as linux_sandbox

    calls = []

    async def fake_docker(*args, timeout=30):
        calls.append(list(args))
        if args[0] == "inspect":
            return "", "", 1  # 不存在
        return "", "", 0

    monkeypatch.setattr(linux_sandbox, "_docker", fake_docker)
    name = asyncio.run(linux_sandbox.ensure_container("u1", "c1"))
    assert name == "botly-linux-c1"
    run_args = next(a for a in calls if a[0] == "run")
    assert linux_sandbox.LINUX_SANDBOX_IMAGE in run_args
    assert any("/workspace" in a for a in run_args)
    assert "sleep" in run_args and "infinity" in run_args


def test_run_command_output(monkeypatch):
    from app.sandbox import linux as linux_sandbox

    calls = []

    async def fake_ensure(user_id, chat_id):
        return "botly-linux-c1"

    async def fake_docker(*args, timeout=30):
        calls.append(list(args))
        return "hello from linux\n", "", 0

    monkeypatch.setattr(linux_sandbox, "ensure_container", fake_ensure)
    monkeypatch.setattr(linux_sandbox, "_docker", fake_docker)

    out = asyncio.run(linux_sandbox.run_command("u1", "c1", "echo hi"))
    assert "hello from linux" in out
    exec_args = next(a for a in calls if a[0] == "exec")
    workdir = exec_args.index("-w")
    assert exec_args[workdir:workdir + 3] == ["-w", "/workspace", "botly-linux-c1"]
    assert exec_args[-1] == "echo hi"


def test_run_command_timeout_message(monkeypatch):
    from app.sandbox import linux as linux_sandbox

    async def fake_ensure(user_id, chat_id):
        return "botly-linux-c1"

    async def fake_docker(*args, timeout=30):
        raise linux_sandbox.LinuxSandboxError("Docker 命令超时（70 秒）")

    monkeypatch.setattr(linux_sandbox, "ensure_container", fake_ensure)
    monkeypatch.setattr(linux_sandbox, "_docker", fake_docker)

    out = asyncio.run(linux_sandbox.run_command("u1", "c1", "sleep 999"))
    assert "超时" in out


def test_run_command_disabled(monkeypatch):
    from app.sandbox import linux as linux_sandbox

    monkeypatch.setattr(linux_sandbox, "LINUX_SANDBOX_ENABLED", False)
    out = asyncio.run(linux_sandbox.run_command("u1", "c1", "echo hi"))
    assert "未启用" in out


def test_image_missing_falls_back_to_baseline(monkeypatch):
    """预装镜像没构建时退回基础镜像，不能直接把建群/执行卡死。"""
    from app.sandbox import linux as linux_sandbox

    runs: list[list[str]] = []

    async def fake_docker(*args, timeout=30):
        if args[0] == "inspect":
            return "", "", 1
        if args[0] == "run":
            runs.append(list(args))
            if linux_sandbox.LINUX_SANDBOX_IMAGE in args:
                return "", "Unable to find image 'botly-sandbox:24.04' locally", 125
            return "", "", 0
        return "", "", 0

    monkeypatch.setattr(linux_sandbox, "_docker", fake_docker)
    name = asyncio.run(linux_sandbox.ensure_container("u1", "c1"))
    assert name == "botly-linux-c1"
    assert len(runs) == 2
    assert linux_sandbox.LINUX_SANDBOX_IMAGE in runs[0]
    assert linux_sandbox.LINUX_SANDBOX_BASELINE_IMAGE in runs[1]


def test_image_missing_matcher():
    from app.sandbox import linux as linux_sandbox

    assert linux_sandbox._image_missing("Unable to find image 'x' locally")
    assert linux_sandbox._image_missing("pull access denied for botly-sandbox")
    assert not linux_sandbox._image_missing("port is already allocated")

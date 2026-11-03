"""沙箱入口默认不启动 Docker；切换适配器后执行和清理使用同一上下文。"""
import asyncio
import sys
from types import ModuleType

from app.sandbox import runtime


def test_unconfigured_sandbox_never_uses_docker(client, monkeypatch):
    monkeypatch.setattr(runtime, "SANDBOX_PROVIDER", "none")
    runtime.backend.cache_clear()
    try:
        assert runtime.status()["available"] is False
        assert "尚未接入" in asyncio.run(runtime.run_code("user", "chat", "print(1)"))
        asyncio.run(runtime.warm("user", "chat"))
        asyncio.run(runtime.release("user", "chat"))
    finally:
        runtime.backend.cache_clear()


def test_external_backend_receives_user_and_chat(client, monkeypatch):
    calls = []
    class Cloud:
        async def warm(self, user, chat): calls.append(("warm", user, chat))
        async def release(self, user, chat): calls.append(("release", user, chat))
        async def run_command(self, user, chat, command):
            calls.append(("command", user, chat, command))
            return "remote output"
        async def run_code(self, user, chat, code, language):
            calls.append(("code", user, chat, code, language))
            return "remote code output"
    module = ModuleType("test_cloud_sandbox")
    module.Cloud = Cloud
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(runtime, "SANDBOX_PROVIDER", "external")
    monkeypatch.setattr(runtime, "SANDBOX_ADAPTER", "test_cloud_sandbox:Cloud")
    runtime.backend.cache_clear()
    try:
        assert runtime.status()["available"]
        async def check():
            await runtime.warm("alice", "chat1")
            assert await runtime.run_command("alice", "chat1", "pwd") == "remote output"
            assert await runtime.run_code("bob", "chat2", "1+1", "python") == "remote code output"
            await runtime.release("alice", "chat1")
        asyncio.run(check())
        assert calls == [("warm", "alice", "chat1"), ("command", "alice", "chat1", "pwd"),
            ("code", "bob", "chat2", "1+1", "python"), ("release", "alice", "chat1")]
    finally:
        runtime.backend.cache_clear()


def test_invalid_adapter_is_unavailable(monkeypatch):
    monkeypatch.setattr(runtime, "SANDBOX_PROVIDER", "external")
    monkeypatch.setattr(runtime, "SANDBOX_ADAPTER", "")
    runtime.backend.cache_clear()
    try:
        assert runtime.status()["available"] is False
    finally:
        runtime.backend.cache_clear()

"""web_read 的 SSRF 防护：公网可读，内网/本机/元数据被拒，且不误杀代理 fake-ip。"""
from __future__ import annotations

import asyncio

import pytest

from app.tools import registry as tools


def test_literal_private_addresses_rejected():
    for url in (
        "http://127.0.0.1/",
        "http://192.168.1.1/",
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://169.254.169.254/",
        "http://[::1]/",
        "http://0.0.0.0/",
    ):
        with pytest.raises(tools.ToolError):
            tools._check_url(url)


def test_literal_public_address_allowed():
    tools._check_url("http://8.8.8.8/")
    tools._check_url("https://1.1.1.1/")


def test_metadata_and_bad_scheme_rejected():
    with pytest.raises(tools.ToolError):
        tools._check_url("http://metadata.google.internal/")
    with pytest.raises(tools.ToolError):
        tools._check_url("ftp://example.com/")
    with pytest.raises(tools.ToolError):
        tools._check_url("file:///etc/passwd")


def test_domain_resolving_private_is_rejected(monkeypatch):
    monkeypatch.setattr(
        tools.socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("10.0.0.8", 0))]
    )
    with pytest.raises(tools.ToolError):
        tools._check_url("http://internal.corp.example/")


def test_domain_resolving_fake_ip_is_allowed(monkeypatch):
    """本地代理（Clash/Surge 等）常把公网域名解析到 reserved 段 198.18.x.x，不应误杀。"""
    monkeypatch.setattr(
        tools.socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("198.18.1.1", 0))]
    )
    tools._check_url("http://example.com/")


def test_domain_resolving_public_is_allowed(monkeypatch):
    monkeypatch.setattr(
        tools.socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))]
    )
    tools._check_url("http://example.com/")


# --------------------------------------------------------------------------- #
# 读取流程：重定向 / 429 退避 / 空壳页面
# --------------------------------------------------------------------------- #
_HTML = "text/html; charset=utf-8"
_LONG_BODY = ("<html><body><p>" + "redirected ok " * 30 + "</p></body></html>").encode()


class _FakeResp:
    def __init__(self, code: int, headers: dict | None = None, body: bytes = b""):
        self.status_code = code
        self.headers = headers or {}
        self.encoding = "utf-8"
        self._body = body

    async def aiter_bytes(self):
        yield self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _install_fake_client(monkeypatch, handler):
    """把 httpx.AsyncClient 换成按 URL 返回响应的假客户端，返回请求过的 URL 列表。"""
    seen: list[str] = []

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def stream(self, method, url, **kwargs):
            seen.append(url)
            return handler(url)

    monkeypatch.setattr(tools.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(tools, "_check_url", lambda url: None)
    return seen


def test_web_read_follows_redirect(monkeypatch):
    """重定向要把 location 拼到当前 URL 上继续读（曾误用 yarl 的 human_repr 直接报错）。"""

    def handler(url: str):
        if url.endswith("/start"):
            return _FakeResp(302, {"location": "/final"})
        return _FakeResp(200, {"content-type": _HTML}, _LONG_BODY)

    seen = _install_fake_client(monkeypatch, handler)
    out = asyncio.run(tools._web_read(None, "https://example.com/start"))
    assert "redirected ok" in out
    assert seen[-1] == "https://example.com/final"
    assert "human_repr" not in out


def test_web_read_retries_once_on_429(monkeypatch):
    """429 是公共站点限速，等 Retry-After 后再试一次，不该第一次就判失败。"""
    calls: list[str] = []

    def handler(url: str):
        calls.append(url)
        if len(calls) == 1:
            return _FakeResp(429, {"retry-after": "0"})
        return _FakeResp(200, {"content-type": _HTML}, _LONG_BODY)

    _install_fake_client(monkeypatch, handler)
    out = asyncio.run(tools._web_read(None, "https://example.com/"))
    assert len(calls) == 2
    assert "redirected ok" in out


def test_web_read_429_twice_reports_limit(monkeypatch):
    """重试后仍是 429，就明确告诉调用方被限速，而不是抛异常。"""
    calls: list[str] = []

    def handler(url: str):
        calls.append(url)
        return _FakeResp(429, {"retry-after": "0"})

    _install_fake_client(monkeypatch, handler)
    out = asyncio.run(tools._web_read(None, "https://example.com/"))
    assert len(calls) == 2
    assert "429" in out


def test_web_read_empty_shell_is_not_success(monkeypatch):
    """只有站名、没有正文的脚本外壳不能让模型误以为读到了内容（Reddit 就是这样）。"""
    shell = b"<html><head><title>Reddit</title></head><body><div id='root'></div></body></html>"

    def handler(url: str):
        return _FakeResp(200, {"content-type": _HTML}, shell)

    _install_fake_client(monkeypatch, handler)
    out = asyncio.run(tools._web_read(None, "https://www.reddit.com/r/DeepSeek/"))
    assert out.startswith("读取失败")
    assert "页面框架" in out
    # 空内容不能被当成可信资料交给模型
    assert tools.UNTRUSTED_PREFIX not in out


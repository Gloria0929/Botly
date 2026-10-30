"""web_read 的 SSRF 防护：公网可读，内网/本机/元数据被拒，且不误杀代理 fake-ip。"""
from __future__ import annotations

import pytest

from app import tools


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

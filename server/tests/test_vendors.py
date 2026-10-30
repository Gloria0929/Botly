"""预置厂商目录：图标直链使用 LobeHub Icons。"""
from __future__ import annotations

from tests.conftest import register


def test_vendor_logos_use_lobehub_icons(client, invite):
    register(client, invite, "vendors@test.dev")
    vendors = {v["id"]: v for v in client.get("/api/providers/vendors").json()}

    assert vendors["deepseek"]["logo"].endswith("/deepseek-color.svg")
    assert vendors["moonshot"]["logo"].endswith("/kimi-color.svg")
    assert vendors["anthropic"]["logo"].endswith("/claude-color.svg")
    assert vendors["custom"]["logo"] == "＋"

    # 本身是单色的品牌用 mono 版，由前端按主题反色
    for vid in ("openai", "xai", "groq"):
        assert vendors[vid]["logo"].endswith(f"/{vid}.svg"), vendors[vid]["logo"]
        assert "-color.svg" not in vendors[vid]["logo"]

    # 除自定义外，其余厂商都指向 LobeHub 静态 CDN
    for vid, v in vendors.items():
        if vid == "custom":
            continue
        assert v["logo"].startswith(
            "https://registry.npmmirror.com/@lobehub/icons-static-svg/"
        ), (vid, v["logo"])
        assert v["logo"].endswith(".svg"), (vid, v["logo"])

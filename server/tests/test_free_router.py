"""自由群聊路由：按内容挑选接话成员；失败时回退到原有轮换规则。"""
import asyncio

import pytest

from app.agent import free_chat, router as router_mod
from app.agent.llm import LLMError
from app.core.db import SessionLocal
from app.core.models import Run
from tests.test_free_chat import answers, model, room, send  # noqa: F401  (room 是 fixture)


def patch_router(monkeypatch, picks):
    calls = []

    async def fake(**kwargs):
        calls.append(kwargs)
        result = picks(kwargs) if callable(picks) else picks
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(free_chat, "route_members", fake)
    return calls


def free_state(run_id):
    with SessionLocal() as db:
        return db.get(Run, run_id).config_snapshot["free"]


def test_router_picks_by_content_instead_of_rotation(client, room, monkeypatch):
    cid, bots = room
    picks = [{"bot_id": bots[2]["id"], "reason": "话题最对口"}]
    router_calls = patch_router(monkeypatch, picks)
    calls = model(monkeypatch)
    result = send(client, cid, "这个成本怎么控制")
    assert [m["sender_bot_id"] for m in answers(client, cid)] == [bots[2]["id"]]
    assert [c["ctx"]["bot_name"] for c in calls] == [bots[2]["name"]]
    assert len(router_calls) == 1
    assert {m["id"] for m in router_calls[0]["members"]} == {b["id"] for b in bots}
    state = free_state(result["run"]["id"])
    assert state["route_done"] and state["primary_bot_id"] == bots[2]["id"]
    assert state["route_reasons"] == {bots[2]["id"]: "话题最对口"}


def test_two_picks_both_reply_and_reason_reaches_the_prompt(client, room, monkeypatch):
    cid, bots = room
    patch_router(monkeypatch, [{"bot_id": bots[1]["id"], "reason": "有实际经验"},
                               {"bot_id": bots[0]["id"], "reason": "补充风险"}])
    calls = model(monkeypatch, {bots[0]["name"]: "我补充一个风险"})
    send(client, cid, "我们聊聊这个方案")
    assert {m["sender_bot_id"] for m in answers(client, cid)} == {bots[0]["id"], bots[1]["id"]}
    systems = {c["ctx"]["bot_name"]: c["messages"][0]["content"] for c in calls}
    assert "有实际经验" in systems[bots[1]["name"]]
    assert "补充风险" in systems[bots[0]["name"]]


@pytest.mark.parametrize("outcome", [None, [], RuntimeError("模型挂了")])
def test_router_failure_falls_back_to_rotation(client, room, monkeypatch, outcome):
    cid, bots = room
    patch_router(monkeypatch, outcome)
    calls = model(monkeypatch)
    send(client, cid, "聊聊这个方案")
    assert len(answers(client, cid)) == 1
    assert len(calls) == len(bots)  # 与没有路由时一致：一位主回应，其余成员获得补充机会


def test_router_cannot_select_a_member_with_zero_proactivity(client, room, monkeypatch):
    cid, bots = room
    client.patch(f"/api/bots/{bots[1]['id']}", json={"proactivity": 0})
    router_calls = patch_router(monkeypatch, [{"bot_id": bots[1]["id"], "reason": "不该被选中"}])
    model(monkeypatch)
    send(client, cid, "聊聊这个方案")
    assert bots[1]["id"] not in {m["id"] for m in router_calls[0]["members"]}
    assert bots[1]["id"] not in {m["sender_bot_id"] for m in answers(client, cid)}


def test_mentions_and_private_greetings_skip_the_router(client, room, monkeypatch):
    cid, bots = room
    router_calls = patch_router(monkeypatch, [{"bot_id": bots[2]["id"], "reason": "x"}])
    model(monkeypatch)
    send(client, cid, f"@{bots[1]['name']} 你怎么看")
    send(client, cid, "谢谢")
    send(client, cid, "@全员 今天下午开会")
    assert router_calls == []


def test_user_naming_a_member_keeps_them_first_even_if_the_router_disagrees(client, room, monkeypatch):
    cid, bots = room
    patch_router(monkeypatch, [{"bot_id": bots[0]["id"], "reason": "a"}, {"bot_id": bots[2]["id"], "reason": "b"}])
    model(monkeypatch)
    result = send(client, cid, f"{bots[1]['name']}，你觉得呢")
    state = free_state(result["run"]["id"])
    assert state["primary_bot_id"] == bots[1]["id"]
    assert state["initial_bot_ids"][0] == bots[1]["id"]


def test_router_runs_only_once_per_message(client, room, monkeypatch):
    cid, bots = room
    router_calls = patch_router(monkeypatch, [{"bot_id": bots[2]["id"], "reason": "x"},
                                              {"bot_id": bots[0]["id"], "reason": "y"}])
    model(monkeypatch)
    send(client, cid, "聊聊")
    assert len(router_calls) == 1


MEMBERS = [{"id": "a", "name": "研究员"}, {"id": "b", "name": "程序员"}, {"id": "c", "name": "设计师"}]


def test_parse_decision_accepts_tokens_names_fences_and_drops_invalid():
    text = '```json\n{"speakers":[{"id":"m2","reason":"写代码"},{"id":"研究员"},{"id":"m2"},{"id":"m9"}]}\n```'
    assert router_mod.parse_decision(text, MEMBERS, 3) == [
        {"bot_id": "b", "reason": "写代码"}, {"bot_id": "a", "reason": ""}]
    assert router_mod.parse_decision(text, MEMBERS, 1) == [{"bot_id": "b", "reason": "写代码"}]
    for bad in ("", "没有 JSON", '{"speakers": "m1"}', '{"speakers": [{"id": "m9"}]}', "{坏的"):
        assert router_mod.parse_decision(bad, MEMBERS, 3) == []


def run_route(monkeypatch, stream, **overrides):
    seen = {}

    async def fake_stream(**kwargs):
        seen.update(kwargs)
        async for chunk in stream(**kwargs):
            yield chunk

    monkeypatch.setattr(router_mod, "stream_chat", fake_stream)
    args = dict(kind="openai", base_url="https://example.test/v1", api_key="k", model="small", content="成本怎么控制",
                history=[{"who": "用户", "text": "先看看预算"}], max_pick=2, timeout=1.0,
                members=[{**m, "role": "成员", "persona": "", "proactivity": 0.5, "recent": 0} for m in MEMBERS])
    args.update(overrides)
    return asyncio.run(router_mod.route_members(**args)), seen


def test_route_members_calls_the_hosted_model_with_a_short_json_request(monkeypatch):
    async def stream(**kwargs):
        yield {"type": "done", "content": '{"speakers":[{"id":"m3","reason":"懂预算"}]}', "tool_calls": [], "usage": {}}

    picks, seen = run_route(monkeypatch, stream)
    assert picks == [{"bot_id": "c", "reason": "懂预算"}]
    assert seen["model"] == "small" and seen["temperature"] == 0 and seen["max_retries"] == 0
    assert seen["max_output_tokens"] == router_mod.ROUTER_MAX_OUTPUT_TOKENS and not seen.get("tools")
    prompt = "\n".join(m["content"] for m in seen["messages"])
    assert "[m1] 研究员" in prompt and "成本怎么控制" in prompt and "先看看预算" in prompt


def test_route_members_returns_none_on_timeout_error_mock_or_garbage(monkeypatch):
    async def slow(**kwargs):
        await asyncio.sleep(5)
        yield {"type": "done", "content": "{}"}

    async def broken(**kwargs):
        raise LLMError("模型服务暂时繁忙")
        yield  # pragma: no cover

    async def garbage(**kwargs):
        yield {"type": "delta", "text": "我觉得都可以"}
        yield {"type": "done", "content": "我觉得都可以"}

    assert run_route(monkeypatch, slow, timeout=0.05)[0] is None
    assert run_route(monkeypatch, broken)[0] is None
    assert run_route(monkeypatch, garbage)[0] is None
    assert run_route(monkeypatch, garbage, kind="mock")[0] is None

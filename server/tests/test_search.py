"""CHT-08：群聊主线程消息搜索。"""
from __future__ import annotations

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Chat, Message, Thread
from tests.conftest import register


def _seed(chat_id: str, texts: list[str], *, thread_type: str = "main") -> str:
    """往指定群聊的线程里直接写消息，返回 thread_id。"""
    with SessionLocal() as db:
        chat = db.get(Chat, chat_id)
        thread = db.scalar(
            select(Thread).where(Thread.chat_id == chat_id, Thread.type == thread_type)
        )
        if thread is None:
            thread = Thread(user_id=chat.user_id, chat_id=chat_id, type=thread_type, title="分支")
            db.add(thread)
            db.flush()
        base = db.scalar(
            select(func.max(Message.seq)).where(Message.thread_id == thread.id)
        ) or 0
        for i, text in enumerate(texts, start=1):
            db.add(
                Message(
                    user_id=chat.user_id,
                    thread_id=thread.id,
                    seq=base + i,
                    sender_type="user",
                    type="text",
                    content=text,
                    status="done",
                )
            )
        db.commit()
        return thread.id


def test_search_matches_main_thread_newest_first(client, invite):
    data = register(client, invite, "search-basic@test.dev")
    chat_id = data["chat_id"]
    _seed(
        chat_id,
        [
            "帮我查一下向量数据库的选型",  # 1
            "第二轮的讨论与向量数据库无关",  # 2
            "最终结论：先用 pgvector",  # 3
        ],
    )
    _seed(chat_id, ["子线程里也提到向量数据库"], thread_type="branch")

    r = client.get(f"/api/chats/{chat_id}/messages/search?q=向量数据库")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["query"] == "向量数据库"
    # 子线程内容不进结果
    assert body["total"] == 2
    assert [hit["seq"] for hit in body["results"]] == [2, 1]
    assert "向量数据库" in body["results"][0]["snippet"]
    assert body["has_more"] is False


def test_search_snippet_is_centered_on_match(client, invite):
    data = register(client, invite, "search-snippet@test.dev")
    chat_id = data["chat_id"]
    filler = "前面无关内容" * 30
    _seed(chat_id, [f"{filler}关键结论在这里{filler}"])

    body = client.get(f"/api/chats/{chat_id}/messages/search?q=关键结论").json()
    snippet = body["results"][0]["snippet"]
    assert "关键结论" in snippet
    assert len(snippet) < 150
    assert snippet.startswith("…") and snippet.endswith("…")


def test_search_escapes_wildcards(client, invite):
    """`%` 和 `_` 要当成普通字符，不能变成通配符把整库都搜出来。"""
    data = register(client, invite, "search-escape@test.dev")
    chat_id = data["chat_id"]
    _seed(chat_id, ["进度 90% 了", "普通消息"])

    body = client.get(f"/api/chats/{chat_id}/messages/search?q=%25").json()
    assert body["total"] == 1
    assert "90%" in body["results"][0]["snippet"]


def test_search_paginates_with_before(client, invite):
    data = register(client, invite, "search-page@test.dev")
    chat_id = data["chat_id"]
    _seed(chat_id, [f"重复关键词 {i}" for i in range(1, 6)])

    first = client.get(f"/api/chats/{chat_id}/messages/search?q=重复关键词&limit=2").json()
    assert first["total"] == 5
    assert first["has_more"] is True
    cursor = first["results"][-1]["seq"]

    second = client.get(
        f"/api/chats/{chat_id}/messages/search?q=重复关键词&limit=2&before={cursor}"
    ).json()
    assert [hit["seq"] for hit in second["results"]] == [3, 2]
    assert second["total"] == 5  # 翻页不影响总数


def test_search_rejects_other_users_chat(client, invite):
    mine = register(client, invite, "search-mine@test.dev")
    _seed(mine["chat_id"], ["私密内容 abcdef"])

    # 当前会话切到另一个账号：别人的群聊一律 404，不泄露是否存在
    register(client, invite, "search-other@test.dev")
    r = client.get(f"/api/chats/{mine['chat_id']}/messages/search?q=私密内容")
    assert r.status_code == 404


def test_search_empty_query_returns_nothing(client, invite):
    data = register(client, invite, "search-empty@test.dev")
    r = client.get(f"/api/chats/{data['chat_id']}/messages/search?q=%20%20")
    assert r.status_code == 200
    assert r.json()["results"] == []

    bad = client.get(f"/api/chats/{data['chat_id']}/messages/search?q=")
    assert bad.status_code == 422

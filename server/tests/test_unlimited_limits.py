import asyncio
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from app.db import SessionLocal
from app.graph import build_graph, run_config
from app.models import Bot, Chat, Message, Run, Thread, UserSettings
from app.orchestrator import LimitExceeded, RunExecutor
from tests.conftest import register, setup_workspace


def test_unlimited_budget_and_legacy_step_values(client, invite):
    register(client, invite, "unlimited-budget@test.dev")
    chat_id, _ = setup_workspace(client)
    assert client.patch('/api/settings', json={'token_budget': 0}).json()['token_budget'] == 0
    assert client.get('/api/settings').json()['token_budget'] == 0
    assert client.get('/api/auth/me').json()['settings']['token_budget'] == 0
    assert client.get(f'/api/chats/{chat_id}').json()['limits']['token_budget'] == 0
    rid = client.post(f'/api/chats/{chat_id}/messages', json={'content': '继续执行'}).json()['run']['id']
    with SessionLocal() as db:
        run = db.get(Run, rid)
        run.steps_used = 100000
        run.tokens_used = 1000000000
        db.get(UserSettings, run.user_id).max_steps = 1
        db.get(Chat, chat_id).limits_override = {'max_steps': 1}
        db.commit()
    ex = RunExecutor(rid)
    ex._check_limits()
    with SessionLocal() as db:
        run = db.get(Run, rid);chat = db.get(Chat, chat_id);bot = db.get(Bot, chat.supervisor_bot_id)
        assert ex._limits(db, run)['max_steps'] == 0
        assert 'token 预算为 无限制' in ex._build_system_prompt(db, run, chat, bot, True, [bot])
        bot_id, provider_id, model = bot.id, bot.provider_id, bot.model
    ex._record_usage(run_id=rid, task_id=None, bot_id=bot_id, provider_id=provider_id, model=model,
                     usage={'input_tokens': 5000}, latency_ms=1)
    with SessionLocal() as db:
        assert db.get(Run, rid).tokens_used == 1000005000
        assert not db.scalars(select(Message).where(Message.run_id == rid, Message.content.like('已使用约%'))).all()


def test_budget_switching_and_chat_zero_override(client, invite):
    register(client, invite, "finite-budget-switch@test.dev")
    chat_id, _ = setup_workspace(client)
    rid = client.post(f'/api/chats/{chat_id}/messages', json={'content': '执行任务'}).json()['run']['id']
    assert client.patch('/api/settings', json={'token_budget': 1000}).status_code == 200
    with SessionLocal() as db:
        db.get(Run, rid).tokens_used = 1000
        db.commit()
    ex = RunExecutor(rid)
    with pytest.raises(LimitExceeded, match='token 预算超限'):
        ex._check_limits()
    assert client.patch(f'/api/chats/{chat_id}', json={'limits_override': {'token_budget': 0}}).status_code == 200
    assert client.get(f'/api/chats/{chat_id}').json()['limits']['token_budget'] == 0
    ex._check_limits()
    client.patch('/api/settings', json={'token_budget': 0})
    client.patch(f'/api/chats/{chat_id}', json={'limits_override': {'token_budget': 1000}})
    with pytest.raises(LimitExceeded, match='token 预算超限'):
        ex._check_limits()
    for value in [-1, 1, 999]:
        assert client.patch('/api/settings', json={'token_budget': value}).status_code == 400
        assert client.patch(f'/api/chats/{chat_id}', json={'limits_override': {'token_budget': value}}).status_code == 400


def test_graph_has_no_old_node_jump_limit():
    class Executor:
        run_id = 'unlimited-graph'
        models = 0
        tools = 0
        pending = False
        completed = False

        async def model_turn(self, **kw):
            self.models += 1
            self.pending = self.models <= 400
            return self.pending, '完成'

        def next_open_call(self, thread_id):
            return SimpleNamespace(id='call', meta={'tool_calls': [{'id': 'call', 'function': {'name': 'file_list', 'arguments': '{}'}}]}) if self.pending else None

        def begin_tool(self, message_id):
            self.tools += 1

        async def execute_tool(self, **kw):
            return 'ok'

        def write_tool_result(self, thread_id, call, content):
            self.pending = False

        def finish_run(self, content):
            self.completed = True

    ex = Executor()
    app = build_graph(ex, InMemorySaver())
    asyncio.run(app.ainvoke({'role': 'supervisor', 'bot_id': 'bot', 'thread_id': 'main', 'task_id': None}, run_config(ex.run_id)))
    assert ex.completed and ex.models == 401 and ex.tools == 400

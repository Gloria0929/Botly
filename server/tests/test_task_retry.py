"""只重试失败子任务，沿用产物和补充要求，不重新执行其他成员。"""
import json

from sqlalchemy import func, select

from app.agent import orchestrator
from app.core.db import SessionLocal
from app.core.models import Notification, Run, Task, Thread
from app.sandbox import files
from tests.conftest import register
from tests.test_parallel import run_worker
from tests.test_task_workspace import parallel_workspace


def failed_workspace(client, invite, label):
    state = parallel_workspace(client, invite, label)
    with SessionLocal() as db:
        db.get(Run, state["root_id"]).status = "completed"
        db.get(Run, state["a"]["run_id"]).status = "failed"
        db.get(Task, state["a"]["task_id"]).status = "failed"
        db.get(Task, state["a"]["task_id"]).result = "原模型请求失败"
        db.get(Run, state["b"]["run_id"]).status = "completed"
        db.get(Task, state["b"]["task_id"]).status = "completed"
        db.get(Task, state["b"]["task_id"]).result = "B 已完成，不应重做"
        main = db.scalar(select(Thread).where(Thread.chat_id == state["chat_id"], Thread.type == "main"))
        orchestrator.add_message(db, thread=main, run_id=state["root_id"], sender_type="user", type_="text",
                                 content="重点只看国内厂商", meta={"kind": "interjection"})
        db.commit()
    files.write_file(state["user_id"], state["chat_id"], state["a"]["workspace"] + "/partial.md", "已收集的资料")
    return state


def retry(client, state, **body):
    return client.post(f"/api/chats/{state['chat_id']}/tasks/{state['a']['task_id']}/retry", json=body)


def test_retry_only_failed_task_with_previous_files_corrections_and_current_model(client, invite, monkeypatch):
    state = failed_workspace(client, invite, "retry-success")
    client.patch(f"/api/bots/{state['a']['bot_id']}", json={"model": "corrected-model"})
    response = retry(client, state, note="不要重新检索，继续完成报告")
    assert response.status_code == 201, response.text
    body = response.json()
    run_id, task_id = body["run"]["id"], body["task"]["id"]
    assert body["task"]["attempt"] == 2
    assert body["run"]["parent_run_id"] is None
    assert retry(client, state).status_code == 409
    turns = 0
    async def respond(**kwargs):
        nonlocal turns
        turns += 1
        assert kwargs["ctx"]["role"] == "worker" and kwargs["ctx"]["bot_name"] == "研究员"
        assert kwargs["model"] == "corrected-model"
        assert "重点只看国内厂商" in kwargs["messages"][1]["content"]
        assert "不要重新检索" in kwargs["messages"][1]["content"]
        with SessionLocal() as db:
            assert db.get(Task, task_id).status == "running"
        if turns == 1:
            calls = [{"id": "read-progress", "type": "function", "function": {
                "name": "file_read", "arguments": json.dumps({"path": "partial.md"})}}]
            yield {"type": "done", "content": "读取已有进度", "tool_calls": calls}
        else:
            assert any(message["role"] == "tool" and "已收集的资料" in message["content"] for message in kwargs["messages"])
            yield {"type": "done", "content": "报告已完成，复用了已有资料", "tool_calls": []}
    monkeypatch.setattr(orchestrator, "stream_chat", respond)
    run_worker(run_id)
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "completed"
    assert turns == 2
    with SessionLocal() as db:
        assert db.get(Run, run_id).config_snapshot["task_workspace"] == state["a"]["workspace"]
        assert db.get(Task, task_id).status == "completed"
        assert db.get(Task, state["a"]["task_id"]).status == "failed"
        assert db.get(Task, state["b"]["task_id"]).result == "B 已完成，不应重做"
        assert db.scalar(select(func.count(Task.id)).where(Task.run_id == run_id)) == 1
    assert files.read_file(state["user_id"], state["chat_id"], state["a"]["workspace"] + "/partial.md") == "已收集的资料"


def test_retry_failure_stays_failed_without_redelegating_to_supervisor(client, invite, monkeypatch):
    state = failed_workspace(client, invite, "retry-failure")
    body = retry(client, state).json()
    async def fail(**kwargs):
        assert kwargs["ctx"]["role"] == "worker"
        raise orchestrator.LLMError("新模型也不可用")
        yield
    monkeypatch.setattr(orchestrator, "stream_chat", fail)
    run_worker(body["run"]["id"])
    assert client.get(f"/api/runs/{body['run']['id']}").json()["status"] == "failed"
    with SessionLocal() as db:
        assert db.get(Task, body["task"]["id"]).status == "failed"
        assert db.scalar(select(Notification.id).where(Notification.target_id == body["run"]["id"], Notification.type == "run_failed"))


def test_retry_respects_ownership_and_does_not_retry_completed_task(client, invite):
    state = failed_workspace(client, invite, "retry-owner")
    path = f"/api/chats/{state['chat_id']}/tasks/{state['b']['task_id']}/retry"
    assert client.post(path, json={}).status_code == 409
    register(client, invite, "retry-foreign@test.dev")
    assert retry(client, state).status_code == 404


def test_retry_rejects_disabled_member_and_running_group(client, invite):
    state = failed_workspace(client, invite, "retry-busy")
    client.patch(f"/api/bots/{state['a']['bot_id']}", json={"enabled": False})
    assert retry(client, state).status_code == 409
    client.patch(f"/api/bots/{state['a']['bot_id']}", json={"enabled": True})
    with SessionLocal() as db:
        db.get(Run, state["root_id"]).status = "running"
        db.commit()
    assert retry(client, state).status_code == 409


def test_retry_keeps_interjections_in_main_thread_and_processes_late_updates(client, invite, monkeypatch):
    state = failed_workspace(client, invite, "retry-late")
    body = retry(client, state).json()
    turns = 0
    async def respond(**kwargs):
        nonlocal turns
        turns += 1
        if turns == 1:
            message = client.post(f"/api/chats/{state['chat_id']}/messages", json={"content": "补充：结尾加价格说明", "interject": True}).json()["message"]
            with SessionLocal() as db:
                assert db.get(Thread, message["thread_id"]).type == "main"
            yield {"type": "done", "content": "初稿", "tool_calls": []}
        else:
            assert turns == 2
            assert kwargs["messages"][-1]["content"] == "补充：结尾加价格说明"
            yield {"type": "done", "content": "补上价格说明", "tool_calls": []}
    monkeypatch.setattr(orchestrator, "stream_chat", respond)
    run_worker(body["run"]["id"])
    assert client.get(f"/api/runs/{body['run']['id']}").json()["status"] == "completed"
    assert turns == 2


def test_stopping_retry_only_cancels_the_new_task(client, invite):
    state = failed_workspace(client, invite, "retry-stop")
    body = retry(client, state).json()
    assert client.post(f"/api/runs/{body['run']['id']}/stop").status_code == 200
    with SessionLocal() as db:
        assert db.get(Task, body["task"]["id"]).status == "canceled"
        assert db.get(Task, state["a"]["task_id"]).status == "failed"
        assert db.get(Task, state["b"]["task_id"]).status == "completed"

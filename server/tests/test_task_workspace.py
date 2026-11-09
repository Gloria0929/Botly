"""独立工作目录、共享资料、原子写入及 Cube 同步的并行安全性。"""
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.agent.orchestrator import ToolCtx
from app.agent import orchestrator
from app.core.db import SessionLocal
from app.core.models import Bot, Chat, FileRec, Run, Task, Thread
from app.sandbox import cube, files, linux, runtime
from app.sandbox.workspace import workspace_for_run
from app.tools.registry import _file_delete, _file_read, _file_write
from tests.conftest import register, setup_workspace
from tests.test_cube import CONFIG, install_fake_sdk
from tests.test_parallel import drain_run, run_worker


@pytest.fixture(autouse=True)
def _settle_synthetic_runs():
    """本文件手工创建 running 子执行；用例结束后归还它们占用的并发名额。"""
    with SessionLocal() as db:
        existing = set(db.scalars(select(Run.id)))
    yield
    with SessionLocal() as db:
        runs = list(db.scalars(select(Run).where(Run.id.not_in(existing),
            Run.status.in_(("queued", "running", "waiting", "waiting_children")))))
        ids = [run.id for run in runs]
        for run in runs:
            run.status, run.stop_requested = "stopped", True
            run.lease_until, run.worker_id = None, None
        for task in db.scalars(select(Task).where(Task.run_id.in_(ids),
            Task.status.in_(("queued", "running", "waiting_approval", "waiting_reply")))):
            task.status = "canceled"
        db.commit()


def parallel_workspace(client, invite, label):
    account = register(client, invite, f"workspace-{label}@test.dev")
    chat_id, bots = setup_workspace(client)
    root_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": "整理两份报告"}).json()["run"]["id"]
    result = {"user_id": account["id"], "chat_id": chat_id, "root_id": root_id, "bots": bots}
    with SessionLocal() as db:
        root = db.get(Run, root_id)
        main = db.scalar(select(Thread).where(Thread.chat_id == chat_id, Thread.type == "main"))
        for key, name in (("a", "研究员"), ("b", "架构师")):
            bot = next(b for b in bots if b["name"] == name)
            thread = Thread(user_id=root.user_id, chat_id=chat_id, parent_thread_id=main.id, type="task", bot_id=bot["id"])
            db.add(thread); db.flush()
            task = Task(user_id=root.user_id, run_id=root.id, thread_id=thread.id, assigned_bot_id=bot["id"],
                        instruction=f"整理{name}报告", status="running", batch=1)
            db.add(task); db.flush()
            child = Run(user_id=root.user_id, chat_id=chat_id, parent_run_id=root.id, parent_task_id=task.id,
                        target_bot_id=bot["id"], thread_id=thread.id, status="running",
                        config_snapshot={**root.config_snapshot, "task_workspace": f"tasks/{task.id}"})
            db.add(child); db.flush()
            result[key] = {"run_id": child.id, "task_id": task.id, "bot_id": bot["id"], "workspace": f"output/tasks/{task.id}"}
        db.commit()
    return result


def context(db, state, key):
    item = state[key]
    return ToolCtx(db, db.get(Run, item["run_id"]), db.get(Chat, state["chat_id"]),
                   db.get(Bot, item["bot_id"]), db.get(Task, item["task_id"]))


def test_parallel_same_name_outputs_do_not_overwrite_and_shared_inputs_are_readable(client, invite):
    state = parallel_workspace(client, invite, "separate")
    files.write_file(state["user_id"], state["chat_id"], "input.txt", "共享输入")
    with SessionLocal() as db:
        a, b = context(db, state, "a"), context(db, state, "b")
        assert state["a"]["workspace"] in asyncio.run(_file_write(a, "report.md", "报告 A"))
        asyncio.run(_file_write(b, "report.md", "报告 B"))
        assert asyncio.run(_file_read(a, "report.md")).endswith("报告 A")
        assert asyncio.run(_file_read(b, "report.md")).endswith("报告 B")
        assert asyncio.run(_file_read(a, "input.txt")).endswith("共享输入")
        assert asyncio.run(_file_read(b, state["a"]["workspace"] + "/report.md")).endswith("报告 A")
        a.task.status, a.task.result = "completed", "报告已交付 report.md"
        summary = orchestrator.RunExecutor(state["root_id"])._delegate_summary(db, a.task)
        assert "相关产物记录：" + state["a"]["workspace"] + "/report.md" in summary
        with pytest.raises(files.SandboxError, match="其他并行任务"):
            asyncio.run(_file_write(b, state["a"]["workspace"] + "/report.md", "不能覆盖"))
        with pytest.raises(files.SandboxError):
            asyncio.run(_file_delete(b, state["a"]["workspace"] + "/report.md"))
        with pytest.raises(files.SandboxError):
            asyncio.run(_file_write(a, "../escape.txt", "不能越界"))
        db.commit()
        records = db.scalars(select(FileRec).where(FileRec.chat_id == state["chat_id"])).all()
        assert {record.path: record.run_id for record in records} == {
            state["a"]["workspace"] + "/report.md": state["a"]["run_id"],
            state["b"]["workspace"] + "/report.md": state["b"]["run_id"],
        }


def test_old_execution_also_defaults_to_output(client, invite):
    state = parallel_workspace(client, invite, "legacy")
    with SessionLocal() as db:
        run = db.get(Run, state["a"]["run_id"])
        run.config_snapshot = {}
        db.commit()
        a = context(db, state, "a")
        asyncio.run(_file_write(a, "report.md", "旧任务继续"))
        assert files.read_file(state["user_id"], state["chat_id"], "output/report.md") == "旧任务继续"
        assert a.workspace == "output"


def test_workspace_lookup_rejects_foreign_execution(client, invite):
    state = parallel_workspace(client, invite, "owner")
    with pytest.raises(files.SandboxError, match="不属于"):
        workspace_for_run("foreign", state["chat_id"], state["a"]["run_id"])


def test_docker_code_runs_in_task_directory(client, invite, monkeypatch):
    state = parallel_workspace(client, invite, "docker")
    calls = []
    async def ensure(*args): return "container"
    async def docker(*args, **kwargs):
        calls.append(args)
        return "ok", "", 0
    monkeypatch.setattr(linux, "ensure_container", ensure)
    monkeypatch.setattr(linux, "_docker", docker)
    asyncio.run(linux.run_code(state["user_id"], state["chat_id"], "print(1)", workspace=state["a"]["workspace"]))
    execution = next(args for args in calls if "-i" in args)
    assert execution[execution.index("-w") + 1] == "/workspace/" + state["a"]["workspace"]


def test_atomic_write_never_exposes_partial_content_and_preserves_mode():
    left, right = b"A" * 65536, b"B" * 65536
    files.write_bytes("atomic", "chat", "report.txt", left)
    path = files.safe_path("atomic", "chat", "report.txt")
    path.chmod(0o755)
    done = threading.Event()
    observed = set()
    def write():
        try:
            for index in range(30):
                files.write_bytes("atomic", "chat", "report.txt", left if index % 2 else right)
        finally:
            done.set()
    def read():
        while not done.is_set():
            observed.add(path.read_bytes())
    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(read)
        pool.submit(write).result()
        reader.result()
    assert observed and observed <= {left, right}
    assert path.stat().st_mode & 0o777 == 0o755


def test_cube_uses_task_directory_and_does_not_erase_another_tasks_new_file(client, invite, monkeypatch):
    state = parallel_workspace(client, invite, "cube")
    fake = install_fake_sdk(monkeypatch)
    client.put("/api/sandbox/cube", json=CONFIG)
    backend = cube.CubeBackend(cube.connection_for(state["user_id"]))
    box, _ = backend._open(state["user_id"], state["chat_id"])
    seen = []
    original_command = box.commands.run
    def execute(command, **kwargs):
        if command.startswith("sha256sum -z -- "):
            return original_command(command, **kwargs)
        seen.append(kwargs)
        box.data["/workspace/" + state["a"]["workspace"] + "/report.md"] = b"remote A"
        box.data["/workspace/" + state["b"]["workspace"] + "/remote.md"] = b"other task"
        with SessionLocal() as db:
            asyncio.run(_file_write(context(db, state, "b"), "later.md", "B 的新成果"))
            db.commit()
        return SimpleNamespace(exit_code=0, stdout="remote A", stderr="")
    box.commands.run = execute
    result = asyncio.run(backend.run_command(state["user_id"], state["chat_id"], "write report",
                                            bot_id=state["a"]["bot_id"], run_id=state["a"]["run_id"]))
    assert "退出码 0" in result, result
    assert seen[0]["cwd"] == "/workspace/" + state["a"]["workspace"]
    assert files.read_file(state["user_id"], state["chat_id"], state["b"]["workspace"] + "/later.md") == "B 的新成果"
    assert not files.safe_path(state["user_id"], state["chat_id"], state["b"]["workspace"] + "/remote.md").exists()
    with SessionLocal() as db:
        records = db.scalars(select(FileRec).where(FileRec.chat_id == state["chat_id"])).all()
        assert {record.path: record.run_id for record in records} == {
            state["a"]["workspace"] + "/report.md": state["a"]["run_id"],
            state["b"]["workspace"] + "/later.md": state["b"]["run_id"],
        }
    assert fake["created"] == 1


def test_cube_independent_task_commands_overlap_and_keep_both_results(client, invite, monkeypatch):
    state = parallel_workspace(client, invite, "cube-real-parallel")
    install_fake_sdk(monkeypatch)
    client.put("/api/sandbox/cube", json=CONFIG)
    backend = cube.CubeBackend(cube.connection_for(state["user_id"]))
    box, _ = backend._open(state["user_id"], state["chat_id"])
    original = box.commands.run
    together = threading.Barrier(2, timeout=4)
    entered = []

    def command(command, **kwargs):
        if command.startswith("sha256sum -z -- "):
            return original(command, **kwargs)
        cwd = kwargs["cwd"]
        entered.append(cwd)
        together.wait()  # 第一条命令必须在第二条进入后才能完成，计时假并行无法通过。
        box.data[cwd + "/result.txt"] = cwd.encode()
        return SimpleNamespace(exit_code=0, stdout="completed", stderr="")

    box.commands.run = command

    async def run():
        return await asyncio.gather(*(backend.run_command(state["user_id"], state["chat_id"], "work",
            bot_id=state[key]["bot_id"], run_id=state[key]["run_id"]) for key in ("a", "b")))

    results = asyncio.run(run())
    assert all("退出码 0" in result for result in results), results
    assert len(entered) == 2
    for key in ("a", "b"):
        path = state[key]["workspace"] + "/result.txt"
        assert files.read_file(state["user_id"], state["chat_id"], path) == "/workspace/" + state[key]["workspace"]
    with SessionLocal() as db:
        from app.core.models import CubeSession
        session = db.scalar(select(CubeSession).where(CubeSession.chat_id == state["chat_id"]))
        assert all(state[key]["workspace"] + "/result.txt" in session.files_manifest for key in ("a", "b"))


@pytest.mark.parametrize("shared", [False, True])
def test_cube_same_task_and_shared_directory_commands_wait_for_active_task(client, invite, monkeypatch, shared):
    state = parallel_workspace(client, invite, f"cube-command-lock-{shared}")
    install_fake_sdk(monkeypatch)
    client.put("/api/sandbox/cube", json=CONFIG)
    backend = cube.CubeBackend(cube.connection_for(state["user_id"]))
    box, _ = backend._open(state["user_id"], state["chat_id"])
    original = box.commands.run
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def command(command, **kwargs):
        if command.startswith("sha256sum -z -- "):
            return original(command, **kwargs)
        calls.append(kwargs["cwd"])
        if len(calls) == 1:
            entered.set()
            assert release.wait(3)
        return SimpleNamespace(exit_code=0, stdout="completed", stderr="")

    box.commands.run = command

    async def run():
        first = asyncio.create_task(backend.run_command(state["user_id"], state["chat_id"], "first",
            run_id=state["a"]["run_id"]))
        second = None
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            second = asyncio.create_task(backend.run_command(state["user_id"], state["chat_id"], "second",
                run_id=None if shared else state["a"]["run_id"]))
            await asyncio.sleep(.1)
            assert len(calls) == 1, "共享目录或同一任务的命令不能与当前命令重叠"
        finally:
            release.set()
            results = await asyncio.gather(*(item for item in (first, second) if item is not None))
        assert all("退出码 0" in result for result in results), results

    asyncio.run(run())
    assert len(calls) == 2


def test_parallel_writes_cannot_exceed_account_quota(monkeypatch):
    monkeypatch.setattr(files, "SANDBOX_QUOTA_MB", 1)
    def write(name):
        try:
            return files.write_bytes("quota-lock", "chat", name, b"X" * 700000)
        except files.SandboxError:
            return "quota"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ["a.txt", "b.txt"]))
    assert sorted(str(result) for result in results) == ["700000", "quota"]
    assert files.quota_used("quota-lock") == 700000


@pytest.mark.parametrize("supported", [False, True])
def test_custom_adapter_receives_workspace_or_returns_explicit_failure(client, invite, monkeypatch, supported):
    state = parallel_workspace(client, invite, f"adapter-{supported}")
    calls = []
    class OldAdapter:
        async def run_command(self, user, chat, command): calls.append(command); return "ok"
        async def run_code(self, user, chat, code, language): calls.append(code); return "ok"
    class Adapter:
        async def run_command(self, user, chat, command, *, workspace=""):
            calls.append(workspace); return "ok"
        async def run_code(self, user, chat, code, language, *, workspace=""):
            calls.append(workspace); return "ok"
    monkeypatch.setattr(runtime, "user_backend", lambda user: Adapter() if supported else OldAdapter())
    async def execute():
        return [await runtime.run_command(state["user_id"], state["chat_id"], "pwd", run_id=state["a"]["run_id"]),
                await runtime.run_code(state["user_id"], state["chat_id"], "print(1)", run_id=state["a"]["run_id"])]
    result = asyncio.run(execute())
    if supported:
        assert result == ["ok", "ok"] and calls == [state["a"]["workspace"]] * 2
    else:
        assert all("沙箱执行失败" in output for output in result) and not calls


def test_nested_parallel_task_returns_to_its_parent_before_child_run_finishes(client, invite, monkeypatch):
    import json
    register(client, invite, "workspace-nested-parallel@test.dev")
    chat_id, bots = setup_workspace(client)
    researcher = next(bot for bot in client.get("/api/bots").json() if bot["name"] == "研究员")
    programmer = client.post("/api/bots", json={"name": "程序员", "role": "程序员",
        "provider_id": researcher["provider_id"], "model": researcher["model"]}).json()
    assert client.post(f"/api/chats/{chat_id}/members", json={"bot_ids": [programmer["id"]]}).status_code == 200
    turns = {"supervisor": 0, "researcher": 0}
    def call(name, args, identifier):
        return {"id": identifier, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
    async def respond(**kwargs):
        if kwargs["ctx"]["role"] == "supervisor":
            turns["supervisor"] += 1
            if turns["supervisor"] == 1:
                calls = [call("propose_plan", {"steps": [
                    {"title": "研究", "bot": "研究员", "depends_on": []},
                    {"title": "复核", "bot": "架构师", "depends_on": []},
                ]}, "nested-plan")]
            elif turns["supervisor"] == 2:
                calls = [call("delegate", {"bot": "研究员", "instruction": "完成研究并汇总", "step": "研究"}, "outer-delegate")]
            else:
                assert "完整研究结果" in str(kwargs["messages"])
                assert "复核结果" in str(kwargs["messages"])
                yield {"type": "done", "content": "研究与复核已汇总", "tool_calls": []}
                return
            yield {"type": "done", "content": "", "tool_calls": calls}
        elif kwargs["ctx"]["bot_name"] == "研究员":
            turns["researcher"] += 1
            if turns["researcher"] == 1:
                yield {"type": "done", "content": "", "tool_calls": [call("delegate", {
                    "bot": "程序员", "instruction": "只整理价格字段", "step": "价格字段",
                }, "inner-delegate")]}
            else:
                assert turns["researcher"] == 2
                assert any(message["role"] == "tool" and "价格字段完整" in message["content"] for message in kwargs["messages"])
                yield {"type": "done", "content": "完整研究结果", "tool_calls": []}
        elif kwargs["ctx"]["bot_name"] == "程序员":
            assert kwargs["messages"][1]["content"] == "只整理价格字段", "嵌套任务必须读取自己的指令"
            yield {"type": "done", "content": "价格字段完整", "tool_calls": []}
        else:
            yield {"type": "done", "content": "复核结果", "tool_calls": []}
    monkeypatch.setattr(orchestrator, "stream_chat", respond)
    root_id = client.post(f"/api/chats/{chat_id}/messages", json={"content": "并行研究和复核"}).json()["run"]["id"]
    run_worker(root_id)
    client.post(f"/api/runs/{root_id}/plan/approve", json={})
    assert drain_run(client, root_id)["status"] == "completed"
    with SessionLocal() as db:
        children = db.scalars(select(Run).where(Run.parent_run_id == root_id)).all()
        tasks = db.scalars(select(Task).where(Task.run_id.in_([root_id, *(child.id for child in children)]))).all()
        assert len(tasks) == 3 and all(task.status == "completed" for task in tasks)
        assert all(child.status == "completed" for child in children)

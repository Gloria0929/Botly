"""使用独立临时进程验证本地停止脚本及项目边界。"""
from contextlib import contextmanager
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import time

import pytest


STOP_SCRIPT = Path(__file__).resolve().parents[2] / "stop.sh"
LISTENER = """
import signal, socket, sys, time
if sys.argv[1] == 'ignore':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
server = socket.socket()
server.bind(('127.0.0.1', 0))
server.listen()
print(server.getsockname()[1], flush=True)
while True:
    time.sleep(1)
"""
WORKER = """
import signal, sys, time
def shutdown(*args):
    time.sleep(0.5)
    sys.exit(0)
signal.signal(signal.SIGTERM, signal.SIG_IGN if sys.argv[1] == 'ignore' else shutdown)
print('ready', flush=True)
while True:
    time.sleep(1)
"""


@pytest.fixture
def stop_project(tmp_path):
    project = tmp_path / "project with spaces"
    project.mkdir()
    shutil.copy2(STOP_SCRIPT, project / "stop.sh")
    (project / "web").mkdir()
    return project


@contextmanager
def process(args, cwd):
    child = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True)
    try:
        ready, _, _ = select.select([child.stdout], [], [], 5)
        assert ready, "测试进程未就绪"
        line = child.stdout.readline().strip()
        assert line, child.stderr.read()
        yield child, line
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        child.stdout.close()
        child.stderr.close()


def worker_command(directory, mode):
    package = directory / "app"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "worker.py").write_text(WORKER)
    return [sys.executable, "-m", "app.worker", mode]


def stop(project, port=None, timeout=2):
    environment = {**os.environ, "BOTLY_STOP_TIMEOUT": str(timeout)}
    environment.pop("BOTLY_STOP_PORTS", None)
    if port is not None:
        environment["BOTLY_STOP_PORTS"] = str(port)
    return subprocess.run([str(project / "stop.sh")], cwd=project.parent, env=environment,
        capture_output=True, text=True, timeout=15)


def test_stop_finds_project_listener_from_another_directory(stop_project):
    with process([sys.executable, "-c", LISTENER, "normal"], stop_project / "web") as (child, port):
        result = stop(stop_project, port)
        assert result.returncode == 0, result.stdout + result.stderr
        assert child.wait(timeout=3) < 0
        assert "已停止" in result.stdout
        assert "没有发现" not in result.stdout


def test_stop_keeps_listener_in_neighboring_project(stop_project):
    other = stop_project.with_name(stop_project.name + "-other")
    other.mkdir()
    with process([sys.executable, "-c", LISTENER, "normal"], other) as (child, port):
        result = stop(stop_project, port)
        assert result.returncode == 0, result.stdout + result.stderr
        assert child.poll() is None
        assert "没有发现" in result.stdout


def test_stop_forces_listener_that_ignores_term(stop_project):
    with process([sys.executable, "-c", LISTENER, "ignore"], stop_project / "web") as (child, port):
        result = stop(stop_project, port, timeout=0)
        assert result.returncode == 0, result.stdout + result.stderr
        assert child.wait(timeout=3) < 0
        assert "强制结束" in result.stdout


def test_stop_forces_owned_worker_but_keeps_other_projects_worker(stop_project):
    other = stop_project.with_name(stop_project.name + "-other")
    other.mkdir()
    with process(worker_command(other, "ignore"), other) as (foreign, _):
        with process(worker_command(stop_project, "ignore"), stop_project) as (owned, _):
            result = stop(stop_project, timeout=0)
            assert result.returncode == 0, result.stdout + result.stderr
            assert owned.wait(timeout=3) < 0
            assert foreign.poll() is None
            assert "强制结束" in result.stdout


def test_stop_waits_for_workers_graceful_shutdown(stop_project):
    with process(worker_command(stop_project, "normal"), stop_project) as (child, _):
        started = time.monotonic()
        result = stop(stop_project)
        assert result.returncode == 0, result.stdout + result.stderr
        assert child.wait(timeout=3) == 0
        assert time.monotonic() - started >= 0.5
        assert "强制结束" not in result.stdout

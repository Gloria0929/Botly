import hashlib
import json
import os
from types import SimpleNamespace

from app.sandbox import preview_runner as runner


def environment(tmp_path, monkeypatch):
    clock = {"now": 1000.0, "alive": True, "listening": False}
    launches = []
    config = {"cwd": str(tmp_path), "state_dir": str(tmp_path / "state"),
              "command": "npm install && npm start", "port": 3000}
    monkeypatch.setattr(runner, "time", SimpleNamespace(time=lambda: clock["now"], monotonic=lambda: clock["now"],
        sleep=lambda duration: clock.update(now=clock["now"] + duration)))
    monkeypatch.setattr(runner, "identity", lambda pid: "birth" if clock["alive"] else None)
    monkeypatch.setattr(runner, "listening", lambda port: clock["listening"])
    def popen(args, **kwargs):
        launches.append((args, kwargs))
        # 即使项目启动命令完全静默，也必须在启动前写好诊断上下文。
        assert "npm install && npm start" in (tmp_path / "state/backend.log").read_text()
        return SimpleNamespace(pid=123)
    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    return config, clock, launches


def test_slow_installation_is_starting_and_reuses_process_until_port_is_ready(tmp_path, monkeypatch):
    config, clock, launches = environment(tmp_path, monkeypatch)
    result = runner.run({"config": config, "action": "start"})
    assert result["running"] and not result["ready"] and result["phase"] == "starting"
    assert clock["now"] < 1002  # 启动请求快速返回；不占用生命周期锁等待安装完成。
    assert "工作目录" in result["log"] and "3000" in result["log"]
    assert launches[0][0] == ["bash", "-c", "npm install && npm start"]
    assert launches[0][1]["start_new_session"] is True
    clock["now"] += 25
    waiting = runner.run({"config": config, "action": "start"})
    assert waiting["phase"] == "starting" and waiting["elapsed_seconds"] >= 25
    assert len(launches) == 1
    clock["listening"] = True
    assert runner.run({"config": config, "action": "status"})["phase"] == "ready"


def test_silent_legacy_process_has_diagnostics_and_eventually_times_out(tmp_path, monkeypatch):
    config, clock, launches = environment(tmp_path, monkeypatch)
    directory = tmp_path / "state"
    directory.mkdir()
    path = directory / "service.json"
    path.write_text(json.dumps({"pid": 123, "identity": "birth",
        "signature": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()}))
    os.utime(path, (975, 975))
    result = runner.run({"config": config, "action": "status"})
    assert result["running"] and result["phase"] == "starting" and result["elapsed_seconds"] == 25
    assert "暂未输出日志" in result["message"] and "3000" in result["message"]
    assert result["log_path"] == str(directory / "backend.log")
    clock["now"] += 300
    timeout = runner.run({"config": config, "action": "status"})
    assert timeout["running"] and not timeout["ready"] and timeout["phase"] == "timeout"
    assert "325" in timeout["message"] and "启动命令" in timeout["message"]
    assert not launches  # 诊断和超时不能擅自重启或终止进程。
    clock["listening"] = True
    assert runner.run({"config": config, "action": "status"})["ready"]


def test_exited_process_is_failed_and_different_configuration_is_identified(tmp_path, monkeypatch):
    config, clock, _ = environment(tmp_path, monkeypatch)
    runner.run({"config": config, "action": "start"})
    different = runner.run({"config": {**config, "command": "npm run other"}, "action": "status"})
    assert different["phase"] == "config_changed"
    (tmp_path / "state/backend.log").write_text("Cannot find module express")
    clock["alive"] = False
    failed = runner.run({"config": config, "action": "status"})
    assert not failed["running"] and failed["phase"] == "failed"
    assert "进程已退出" in failed["message"] and "Cannot find module" in failed["log"]

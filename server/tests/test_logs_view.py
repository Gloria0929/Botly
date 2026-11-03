"""日志页的数据口径：要和 data/logs/app.log 里看到的一致。

后端读取时会做三件影响展示的事：
  1. 保留 pid（前端要和日志文件对得上）；
  2. 把堆栈等多行续行拼回父日志，否则报错只剩第一行；
  3. 继续折叠 sqlalchemy 的 SQL 回显。
"""
import pytest

from app.api import logs


def _page(p, monkeypatch, **kw):
    monkeypatch.setattr(logs, "_log_files", lambda: p if isinstance(p, list) else [p])
    params = dict(user=None, level=None, module=None, q=None, request_id=None, run_id=None, limit=10, offset=0)
    params.update(kw)
    return logs.list_logs(**params)


def test_logs_keep_pid_and_multiline_trace(tmp_path, monkeypatch):
    p = tmp_path / "app.log"
    p.write_text(
        "2026-11-02 10:00:00 INFO    pid=111 [access] req=r1 run=- task=- GET /api/bots -> 200 12ms size=34 ip=127.0.0.1 uid=u1 ua='Chrome'\n"
        "2026-11-02 10:00:01 ERROR   pid=111 [agent] req=r1 run=run1 task=t1 执行失败\n"
        "Traceback (most recent call last):\n"
        '  File "x.py", line 1, in <module>\n'
        "ValueError: boom\n"
        "2026-11-02 10:00:02 INFO    pid=111 [sqlalchemy.engine.Engine] req=- run=- task=- SELECT 1\n"
        "2026-11-02 10:00:03 INFO    pid=111 [worker] req=- run=- task=- Worker 启动\n",
        encoding="utf-8",
    )

    page = _page(p, monkeypatch)

    assert [i["module"] for i in page["items"]] == ["worker", "agent", "access"]
    assert page["hidden_sql"] == 1
    assert all(i["pid"] == "111" for i in page["items"])

    err = page["items"][1]
    assert err["message"].startswith("执行失败\nTraceback")
    assert err["message"].endswith("ValueError: boom")

    # 关键字能命中堆栈里的内容（HTML 页面里搜 "valueerror" 能找到这条）
    hit = _page(p, monkeypatch, q="valueerror")
    assert [i["time"] for i in hit["items"]] == ["2026-11-02 10:00:01"]


@pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "warning", None])
def test_level_filter_matches_exact_severity(tmp_path, monkeypatch, level):
    p = tmp_path / "app.log"
    levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    p.write_text("".join(
        f"2026-11-02 10:00:0{i} {severity:<7} pid=111 [agent] req=r1 run=run1 task=t1 {severity} message\n"
        + ("Traceback: ERROR-only detail\n" if severity == "ERROR" else "")
        for i, severity in enumerate(levels)
    ), encoding="utf-8")

    page = _page(p, monkeypatch, level=level)

    expected = [level.upper()] if level else list(reversed(levels))
    assert [row["level"] for row in page["items"]] == expected
    if level and level.upper() != "ERROR":
        assert all("ERROR-only detail" not in row["message"] for row in page["items"])
    if level and level.upper() == "ERROR":
        assert "ERROR-only detail" in page["items"][0]["message"]


@pytest.mark.parametrize("limit,offset", [(1, 0), (2, 0), (2, 1), (10, 0)])
def test_rotated_logs_return_current_file_first(tmp_path, monkeypatch, limit, offset):
    paths = [tmp_path / name for name in ("app.log", "app.log.1", "app.log.2")]
    times = ["2026-11-03 10:00:00", "2026-10-30 10:00:00", "2026-10-29 10:00:00"]
    for path, time in zip(paths, times):
        path.write_text(f"{time} INFO    pid=111 [worker] req=- run=- task=- {path.name}\n", encoding="utf-8")

    page = _page(paths, monkeypatch, limit=limit, offset=offset)

    assert [row["time"] for row in page["items"]] == times[offset:offset + limit]
    assert page["files"] == [path.name for path in paths]


def test_refresh_reads_new_entries_before_archives(tmp_path, monkeypatch):
    current = tmp_path / "app.log"
    archive = tmp_path / "app.log.1"
    current.write_text("2026-11-03 10:00:00 INFO    pid=111 [worker] req=- run=- task=- before refresh\n", encoding="utf-8")
    archive.write_text("2026-10-30 10:00:00 INFO    pid=111 [worker] req=- run=- task=- archived\n", encoding="utf-8")
    before = _page([current, archive], monkeypatch, limit=1)
    with current.open("a", encoding="utf-8") as file:
        file.write("2026-11-03 10:00:05 INFO    pid=111 [worker] req=- run=- task=- after refresh\n")
    after = _page([current, archive], monkeypatch, limit=1)

    assert before["items"][0]["message"] == "before refresh"
    assert after["items"][0]["message"] == "after refresh"


def test_log_file_rotation_order(tmp_path, monkeypatch):
    for name in ("app.log.10", "app.log.2", "app.log", "app.log.1"):
        (tmp_path / name).touch()
    monkeypatch.setattr(logs, "LOG_DIR", tmp_path)
    assert [path.name for path in logs._log_files()] == ["app.log", "app.log.1", "app.log.2", "app.log.10"]

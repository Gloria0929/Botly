"""日志页的数据口径：要和 data/logs/app.log 里看到的一致。

后端读取时会做三件影响展示的事：
  1. 保留 pid（前端要和日志文件对得上）；
  2. 把堆栈等多行续行拼回父日志，否则报错只剩第一行；
  3. 继续折叠 sqlalchemy 的 SQL 回显。
"""
from app.api import logs


def _page(p, monkeypatch, **kw):
    monkeypatch.setattr(logs, "_log_files", lambda: [p])
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

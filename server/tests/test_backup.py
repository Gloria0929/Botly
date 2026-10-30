"""M5 备份：SQLite 备份、保留策略与恢复提示。"""
from __future__ import annotations

import sqlite3

from scripts import backup_db


def test_backup_list_prune_and_restore_hint(client):
    # client fixture 触发 init_db，确保测试库有表结构
    for _ in range(3):
        backup_db.run_backup()

    backups = backup_db._backups()
    assert len(backups) == 3
    # 备份是合法的 SQLite 文件
    conn = sqlite3.connect(backups[-1])
    try:
        assert conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] > 0
    finally:
        conn.close()

    removed = backup_db.prune(keep=1)
    assert len(removed) == 2
    assert len(backup_db._backups()) == 1

    # 恢复提示（SQLite 走复制）
    name = backup_db._backups()[0].stem
    assert "cp" in backup_db._restore_hint(name)


def test_pg_url_conversion(monkeypatch):
    monkeypatch.setattr(
        backup_db, "DATABASE_URL", "postgresql+psycopg://botly:botly@127.0.0.1:5432/botly"
    )
    assert backup_db._pg_url() == "postgresql://botly:botly@127.0.0.1:5432/botly"


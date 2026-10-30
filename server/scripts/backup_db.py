"""数据库备份（M5 备份）。

Postgres 用 `pg_dump` 自定义格式（已压缩），SQLite 用 `sqlite3` 在线 `.backup`
（WAL 下也安全，无需停服）。备份写到 `server/data/backups/`，默认保留最近 7 份，
旧的自动清理（可用 `--keep` 调整）。

用法：
    python -m scripts.backup_db              # 备份一次
    python -m scripts.backup_db --keep 14    # 备份并保留 14 份
    python -m scripts.backup_db --list       # 列出已有备份
    python -m scripts.backup_db --restore 20261030-120000  # 打印恢复命令（不自动执行）

恢复（Postgres，需停机或另开新库，避免覆盖在线数据）：
    pg_restore --clean --if-exists --dbname=postgresql://botly:botly@127.0.0.1:5432/botly \
        server/data/backups/backup-<ts>.dump
恢复（SQLite）：
    停止 API 与 Worker 后，把备份文件复制回 `DATABASE_URL` 指向的 .db 文件即可。
"""
from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.config import DATA_DIR, DATABASE_URL
from app.db import IS_SQLITE

BACKUP_DIR = DATA_DIR / "backups"
KEEP_DEFAULT = 7


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")


def _sqlite_path() -> Path:
    prefix = "sqlite:///"
    raw = DATABASE_URL[len(prefix):]
    path = Path(raw)
    return path if path.is_absolute() else (DATA_DIR / path)


def _backup_sqlite(dst: Path) -> None:
    src = sqlite3.connect(_sqlite_path())
    dest = sqlite3.connect(dst)
    try:
        with dest:
            src.backup(dest)
    finally:
        src.close()
        dest.close()


def _pg_url() -> str:
    """把 SQLAlchemy 的 URL 换成 libpq 能识别的 URI。"""
    url = DATABASE_URL
    for scheme in ("postgresql+psycopg://", "postgresql+psycopg2://", "postgresql+asyncpg://"):
        if url.startswith(scheme):
            return "postgresql://" + url[len(scheme):]
    return url


def _backup_postgres(dst: Path) -> None:
    # DATABASE_URL 已含密码；作为进程参数会有短暂暴露，脚本运行在受信主机上可接受。
    cmd = ["pg_dump", "-Fc", "-Z6", "--no-owner", "--no-privileges", "-f", str(dst), _pg_url()]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        dst.unlink(missing_ok=True)
        raise RuntimeError(f"pg_dump 失败：{proc.stderr.strip() or '未知错误'}")



def run_backup() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if IS_SQLITE:
        dst = BACKUP_DIR / f"backup-{_ts()}.sqlite"
        _backup_sqlite(dst)
    else:
        dst = BACKUP_DIR / f"backup-{_ts()}.dump"
        _backup_postgres(dst)
    return dst


def _backups() -> list[Path]:
    if not BACKUP_DIR.exists():
        return []
    return sorted(BACKUP_DIR.glob("backup-*"), key=lambda p: p.name)


def prune(keep: int) -> list[Path]:
    """删除超出保留份数的旧备份，返回被删除的文件。"""
    items = _backups()
    removed = items[:-keep] if keep > 0 and len(items) > keep else []
    for p in removed:
        p.unlink(missing_ok=True)
    return removed


def _restore_hint(name: str) -> str:
    matches = [p for p in _backups() if p.name.startswith(name) or p.stem == name]
    if not matches:
        return f"找不到备份 {name}"
    p = matches[0]
    if p.suffix == ".dump":
        return (
            "Postgres 恢复（建议先停 API/Worker，或恢复到新库）：\n"
            f"  pg_restore --clean --if-exists --dbname={DATABASE_URL} {p}"
        )
    return (
        "SQLite 恢复：停止 API 与 Worker 后执行：\n"
        f"  cp {p} {_sqlite_path()}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="数据库备份与恢复提示")
    parser.add_argument("--keep", type=int, default=KEEP_DEFAULT, help="保留的备份份数（默认 7）")
    parser.add_argument("--list", action="store_true", help="列出已有备份")
    parser.add_argument("--restore", metavar="NAME", help="打印恢复命令，不自动执行")
    args = parser.parse_args()

    if args.list:
        items = _backups()
        if not items:
            print("暂无备份")
        for p in items:
            print(f"{p.name}\t{p.stat().st_size / 1024:.0f} KB")
        return

    if args.restore:
        print(_restore_hint(args.restore))
        return

    dst = run_backup()
    print(f"已备份：{dst}")
    removed = prune(args.keep)
    if removed:
        print(f"已清理 {len(removed)} 份旧备份")


if __name__ == "__main__":
    sys.exit(main())

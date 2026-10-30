"""只读日志查看。

日志由 logging_setup 写入 `data/logs/app.log`；这里按行读取并做过滤，
供前端「日志」页展示。只读，不提供删除或清空；也只会读固定目录下的
轮转文件，不接收任意路径。
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, Query

from ..config import LOG_FILE_MAX_MB
from ..deps import current_user
from ..logging_setup import LOG_DIR
from ..models import User

router = APIRouter(prefix="/logs", tags=["logs"])

# 单次最多读取的字节数，避免大文件一次性读进内存
_MAX_BYTES = 8 * 1024 * 1024
# 行首格式：2026-10-29 18:40:00 LEVEL [module] ...（与 logging_setup 的 FILE_FMT 对应）
_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) "
    r"(?P<level>[A-Z]+)\s+pid=\d+ \[(?P<module>[^\]]+)\] "
    r"req=(?P<request_id>\S+) run=(?P<run_id>\S+) task=(?P<task_id>\S+) (?P<message>.*)$"
)


def _log_files() -> list[Path]:
    """当前日志 + 轮转出来的历史文件，越新的文件排在越前。"""
    if not LOG_DIR.exists():
        return []
    files = [p for p in LOG_DIR.glob("app.log*") if p.is_file()]
    # app.log 是最新文件；轮转文件是 app.log.1（最新备份）…，序号越小越新
    def key(p: Path):
        if p.name == "app.log":
            return (0, 0)
        try:
            return (1, int(p.name.rsplit(".", 1)[-1]))
        except ValueError:
            return (2, 0)
    return sorted(files, key=key)


def _read_lines(path: Path) -> list[str]:
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > _MAX_BYTES:
            fh.seek(size - _MAX_BYTES)
            fh.readline()  # 丢掉可能被截断的首行
        data = fh.read()
    return data.decode("utf-8", errors="replace").splitlines()


@router.get("")
def list_logs(
    user: User = Depends(current_user),
    level: str | None = Query(default=None, description="只看某个级别及以上：ERROR / WARNING / INFO / DEBUG"),
    module: str | None = Query(default=None, description="模块名，如 access、agent、security"),
    q: str | None = Query(default=None, description="关键字（不区分大小写）"),
    request_id: str | None = Query(default=None, description="按请求编号串联一条请求的全部日志"),
    run_id: str | None = Query(default=None, description="按执行编号串联一次执行"),
    limit: int = Query(default=300, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
):
    """返回最新的 limit 条（过滤后），倒序：第一页就是最近发生的事。"""
    level_rank = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
    want_level = (level or "").upper()
    min_rank = level_rank.get(want_level, 0)
    needle = (q or "").strip().lower()

    matched: list[dict] = []
    for path in reversed(_log_files()):
        for raw in reversed(_read_lines(path)):
            m = _LINE.match(raw)
            if not m:
                # 多行堆栈的续行：如果上一条命中过滤，本行也属于它；这里简单跳过
                continue
            rank = level_rank.get(m["level"], 0)
            if min_rank and rank < min_rank:
                continue
            if module and m["module"] != module:
                continue
            if request_id and m["request_id"] != request_id:
                continue
            if run_id and m["run_id"] != run_id:
                continue
            if needle and needle not in raw.lower():
                continue
            matched.append(
                {
                    "time": m["ts"],
                    "level": m["level"],
                    "module": m["module"],
                    "request_id": m["request_id"],
                    "run_id": m["run_id"],
                    "task_id": m["task_id"],
                    "message": m["message"],
                }
            )
            if len(matched) >= offset + limit:
                break
        if len(matched) >= offset + limit:
            break

    total_window = len(matched)
    page = matched[offset : offset + limit]
    files = [p.name for p in reversed(_log_files())]
    return {
        "items": page,
        "has_more": total_window >= offset + limit,
        "offset": offset,
        "limit": limit,
        "files": files,
        "log_dir": str(LOG_DIR),
        "max_file_mb": LOG_FILE_MAX_MB,
    }


@router.get("/modules")
def list_modules(user: User = Depends(current_user)):
    """日志里出现过的模块名，用于前端下拉筛选。"""
    seen: dict[str, int] = {}
    for path in reversed(_log_files()):
        for raw in _read_lines(path):
            m = _LINE.match(raw)
            if m:
                seen[m["module"]] = seen.get(m["module"], 0) + 1
    return [{"module": k, "count": v} for k, v in sorted(seen.items(), key=lambda kv: -kv[1])]

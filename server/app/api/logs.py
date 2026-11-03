"""只读日志查看。

日志由 logging_setup 写入 `data/logs/app.log`；这里按行读取并做过滤，
供前端「日志」页展示。只读，不提供删除或清空；也只会读固定目录下的
轮转文件，不接收任意路径。

数据库引擎的 SQL 回显（sqlalchemy.engine）属于实现细节，历史文件里还
残留着大量多行 SQL，读取时一律跳过——否则日志页会被 SQL 刷屏。
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, Query

from app.core.config import LOG_FILE_MAX_MB
from app.core.deps import current_user
from app.core.logging_setup import LOG_DIR
from app.core.models import User

router = APIRouter(prefix="/logs", tags=["logs"])

# 单次最多读取的字节数，避免大文件一次性读进内存
_MAX_BYTES = 8 * 1024 * 1024
# 数据库引擎的 SQL 回显（sqlalchemy.engine.Engine）是实现细节，不属于
# 「API 请求 / 执行链路 / 安全事件」这三类，历史日志文件里还残留着大量
# 多行 SQL，读取时统一跳过，避免把日志页刷成 SQL 编辑器。
_DB_LOGGER = re.compile(r"^sqlalchemy(\.|$)")
# 行首格式：2026-10-29 18:40:00 LEVEL [module] ...（与 logging_setup 的 FILE_FMT 对应）
_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) "
    r"(?P<level>[A-Z]+)\s+pid=(?P<pid>\d+) \[(?P<module>[^\]]+)\] "
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
    level: str | None = Query(default=None, description="精确匹配日志级别：CRITICAL / ERROR / WARNING / INFO / DEBUG"),
    module: str | None = Query(default=None, description="模块名，如 access、agent、security"),
    q: str | None = Query(default=None, description="关键字（不区分大小写）"),
    request_id: str | None = Query(default=None, description="按请求编号串联一条请求的全部日志"),
    run_id: str | None = Query(default=None, description="按执行编号串联一次执行"),
    limit: int = Query(default=300, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
):
    """返回最新的 limit 条（过滤后），倒序：第一页就是最近发生的事。"""
    want_level = (level or "").strip().upper()
    needle = (q or "").strip().lower()

    matched: list[dict] = []
    hidden_sql = 0
    # _log_files 已按新到旧排序；只反转各文件内的行，不能再反转文件顺序。
    for path in _log_files():
        # 反向扫描时，堆栈/多行消息的续行出现在父行之前，先攒着，
        # 等匹配到父行再拼回去——否则前端看到的报错只有第一行。
        pending: list[str] = []
        for raw in reversed(_read_lines(path)):
            m = _LINE.match(raw)
            if not m:
                if raw.strip():
                    pending.append(raw)
                continue
            if _DB_LOGGER.match(m["module"]):
                hidden_sql += 1
                pending.clear()
                continue
            if want_level and m["level"] != want_level:
                pending.clear()
                continue
            if module and m["module"] != module:
                pending.clear()
                continue
            if request_id and m["request_id"] != request_id:
                pending.clear()
                continue
            if run_id and m["run_id"] != run_id:
                pending.clear()
                continue

            message = m["message"]
            if pending:
                message += "\n" + "\n".join(reversed(pending))
            pending.clear()

            haystack = (
                f'{m["ts"]} {m["level"]} pid={m["pid"]} [{m["module"]}] '
                f'req={m["request_id"]} run={m["run_id"]} task={m["task_id"]} {message}'
            )
            if needle and needle not in haystack.lower():
                continue
            matched.append(
                {
                    "time": m["ts"],
                    "level": m["level"],
                    "module": m["module"],
                    "pid": m["pid"],
                    "request_id": m["request_id"],
                    "run_id": m["run_id"],
                    "task_id": m["task_id"],
                    "message": message,
                }
            )
            if len(matched) >= offset + limit:
                break
        if len(matched) >= offset + limit:
            break

    total_window = len(matched)
    page = matched[offset : offset + limit]
    files = [p.name for p in _log_files()]
    return {
        "items": page,
        "has_more": total_window >= offset + limit,
        "offset": offset,
        "limit": limit,
        "files": files,
        "log_dir": str(LOG_DIR),
        "max_file_mb": LOG_FILE_MAX_MB,
        # 本次读取时跳过的数据库语句条数（填满一页就停，所以是下限而非总数）
        "hidden_sql": hidden_sql,
    }


@router.get("/modules")
def list_modules(user: User = Depends(current_user)):
    """日志里出现过的模块名，用于前端下拉筛选。"""
    seen: dict[str, int] = {}
    for path in reversed(_log_files()):
        for raw in _read_lines(path):
            m = _LINE.match(raw)
            if m and not _DB_LOGGER.match(m["module"]):
                seen[m["module"]] = seen.get(m["module"], 0) + 1
    return [{"module": k, "count": v} for k, v in sorted(seen.items(), key=lambda kv: -kv[1])]

"""统一日志：控制台 + 按天轮转文件，带请求追踪 id 与敏感字段脱敏。

API 与 Worker 两个进程共用这一套配置：
- 控制台：易读行格式（开发时直接看）
- 文件：server/data/logs/app.log，按天轮转，保留 LOG_RETENTION_DAYS 天
- 两者都带 request_id（API 请求）/ run_id、task_id（执行链路），便于串联
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import logging.handlers
import re
import time

from .config import DATA_DIR, LOG_FILE_MAX_MB, LOG_LEVEL_NUM, LOG_RETENTION_DAYS

LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "app.log"

# 当前请求 / 执行上下文的追踪 id，由中间件和编排器写入，日志格式自动带上
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
run_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("run_id", default="-")
task_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("task_id", default="-")

# 响应/请求体里出现这些键时一律打码，避免密码、API Key、会话 cookie 进日志
_SENSITIVE = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|authorization|cookie|invite_code|session)",
    re.IGNORECASE,
)
# 请求体超过这个长度只截断记录，防止大文件上传把日志撑爆
_BODY_LIMIT = 2000


def mask_sensitive(value, depth: int = 0):
    """递归把敏感键的值替换成 ***，并对字符串里的密钥做部分遮蔽。"""
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        return {
            k: ("***" if _SENSITIVE.search(str(k)) else mask_sensitive(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [mask_sensitive(v, depth + 1) for v in value]
    return value


def body_preview(raw: bytes | str | None) -> str:
    """请求体摘要：JSON 脱敏后压缩成一行，其他内容截断。"""
    if not raw:
        return ""
    if isinstance(raw, str):
        text = raw
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return f"<binary {len(raw)} bytes>"
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return text[:_BODY_LIMIT] + ("…" if len(text) > _BODY_LIMIT else "")
    return json.dumps(mask_sensitive(data), ensure_ascii=False)[:_BODY_LIMIT]


def fingerprint(user_id: str | None) -> str:
    """日志里的用户标识用短哈希，不直接落原始 id 以外的东西；没有就返回 '-'。"""
    if not user_id:
        return "-"
    return hashlib.sha256(user_id.encode()).hexdigest()[:8]


class ContextFilter(logging.Filter):
    """把当前上下文的 request_id / run_id / task_id 附加到每条记录。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.run_id = run_id_var.get()
        record.task_id = task_id_var.get()
        return True


class _Fmt(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.name_short = record.name.removeprefix("botly.")
        return super().format(record)


CONSOLE_FMT = "%(asctime)s %(levelname)-7s [%(name_short)s] req=%(request_id)s run=%(run_id)s task=%(task_id)s %(message)s"
FILE_FMT = "%(asctime)s %(levelname)-7s pid=%(process)d [%(name_short)s] req=%(request_id)s run=%(run_id)s task=%(task_id)s %(message)s"
DATE_FMT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(service: str) -> None:
    """幂等初始化；API 与 Worker 各自启动时调用一次。"""
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger()
    root.setLevel(LOG_LEVEL_NUM)
    for h in list(root.handlers):
        root.removeHandler(h)

    ctx = ContextFilter()

    console = logging.StreamHandler()
    console.setFormatter(_Fmt(CONSOLE_FMT.replace("%(asctime)s", f"%(asctime)s {service}"), DATE_FMT))
    console.addFilter(ctx)
    root.addHandler(console)

    # 按大小轮转：每天一个 app.log-YYYY-MM-DD 不够时也不会让单文件无限增长
    rotating = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=LOG_FILE_MAX_MB * 1024 * 1024, backupCount=LOG_RETENTION_DAYS, encoding="utf-8"
    )
    rotating.setFormatter(_Fmt(FILE_FMT, DATE_FMT))
    rotating.addFilter(ctx)
    root.addHandler(rotating)

    # uvicorn 用自己的输出通道，统一并入 root（同时保留 uvloop/asyncio 的 warning）
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    # 访问日志由 botly.access 记录（带耗时、用户、请求体摘要），关掉 uvicorn 的重复一条
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    # 这些库在 DEBUG 级别会打大量噪声
    for noisy in ("httpx", "httpcore", "urllib3", "redis", "asyncio", "multipart"):
        logging.getLogger(noisy).setLevel(max(logging.INFO, LOG_LEVEL_NUM))
    # SQL 语句默认不打进应用日志；需要排查 SQL 时单独把 sqlalchemy.engine 调到 INFO
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """业务模块统一用 botly.<模块名> 取得 logger。"""
    return logging.getLogger(name if name.startswith("botly.") else f"botly.{name}")


def new_request_id() -> str:
    # 16 位十六进制够用且短；不依赖外部 uuid 库开销
    return f"{time.time_ns():x}"[-16:]

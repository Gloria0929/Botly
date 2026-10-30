"""全局配置。

第一版默认使用 SQLite（零依赖、单机可跑），通过 DATABASE_URL 可切换到 Postgres。
所有可调参数集中在这里，对应需求文档附录 A。
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env(path: Path) -> None:
    """极简 .env 加载：不引入额外依赖，已存在的环境变量优先。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_env(BASE_DIR / ".env")

DATA_DIR = Path(os.getenv("BOTLY_DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
SANDBOX_DIR = Path(os.getenv("BOTLY_SANDBOX_DIR", str(DATA_DIR / "sandbox")))
SANDBOX_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "botly.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")

# 会话签名密钥
SECRET_KEY = os.getenv("BOTLY_SECRET_KEY", "dev-insecure-secret-key-please-change")
# API Key 对称加密主密钥：优先使用独立环境变量，否则从 SECRET_KEY 派生
_raw_enc = os.getenv("BOTLY_ENCRYPTION_KEY")
if _raw_enc:
    ENCRYPTION_KEY = _raw_enc.encode()
else:
    ENCRYPTION_KEY = base64.urlsafe_b64encode(hashlib.sha256(SECRET_KEY.encode()).digest())

SESSION_COOKIE = "botly_session"
SESSION_MAX_AGE = 7 * 24 * 3600  # 7 天
# 部署在反向代理/HTTPS 后面时，通过环境变量强制 Secure，不依赖代理头是否被信任
COOKIE_SECURE = os.getenv("BOTLY_COOKIE_SECURE", "").lower() in ("1", "true", "yes")

# ---- 附录 A 默认参数 ----
DEFAULT_TOKEN_BUDGET = 300_000
MAX_CONCURRENT_RUNS_PER_USER = max(1, int(os.getenv("BOTLY_MAX_CONCURRENT_RUNS_PER_USER", "2")))
MAX_CONCURRENT_RUNS = max(1, int(os.getenv("BOTLY_MAX_CONCURRENT_RUNS", "20")))
WORKER_CONCURRENCY = max(1, int(os.getenv("BOTLY_WORKER_CONCURRENCY", "20")))
LLM_TIMEOUT_SECONDS = 120
LLM_MAX_RETRIES = 3
LLM_RETRY_BACKOFF = (2, 4, 8)
MAX_REASSIGN = 1
APPROVAL_TTL_HOURS = 24
MEMORY_INJECT_LIMIT = 4000
# MEM-05：主线程历史超过该 token 估算值时，用总指挥模型生成摘要替换更早的历史。
SUMMARY_THRESHOLD_TOKENS = max(1000, int(os.getenv("BOTLY_SUMMARY_THRESHOLD_TOKENS", "8000")))
SUMMARY_MAX_ITEMS = max(10, int(os.getenv("BOTLY_SUMMARY_MAX_ITEMS", "80")))
HISTORY_KEEP_RECENT = 40  # 摘要后仍保留在上下文里的最近消息条数
WEB_READ_TRUNCATE = 20_000
# 网页读取先按字节截断，避免恶意/超大响应把进程内存打满（需求 8.4）
WEB_READ_MAX_BYTES = 5 * 1024 * 1024
SANDBOX_QUOTA_MB = 200
SANDBOX_FILE_MAX_MB = 10
LOG_RETENTION_DAYS = 30
EVENT_RETENTION_DAYS = 7  # 需求第 10 节：events 保留 7 天用于断线补发
# 应用日志：级别、单文件上限（MB）、是否记录请求/响应体与查询参数
LOG_LEVEL = os.getenv("BOTLY_LOG_LEVEL", "INFO").upper()
LOG_LEVEL_NUM = getattr(logging, LOG_LEVEL, logging.INFO) if LOG_LEVEL.isalpha() else logging.INFO
LOG_FILE_MAX_MB = max(1, int(os.getenv("BOTLY_LOG_FILE_MAX_MB", "20")))
LOG_BODY = os.getenv("BOTLY_LOG_BODY", "1").lower() in ("1", "true", "yes")
DELEGATION_DEPTH = 1

# 8.6 告警阈值：Worker 每分钟检查一次，冷却期内同一类告警不重复写
ALERT_QUEUE_BACKLOG = max(1, int(os.getenv("BOTLY_ALERT_QUEUE_BACKLOG", "10")))
ALERT_RUN_STUCK_MINUTES = max(1, int(os.getenv("BOTLY_ALERT_RUN_STUCK_MINUTES", "20")))
ALERT_FAIL_WINDOW_MINUTES = max(5, int(os.getenv("BOTLY_ALERT_FAIL_WINDOW_MINUTES", "60")))
ALERT_FAIL_MIN_RUNS = max(1, int(os.getenv("BOTLY_ALERT_FAIL_MIN_RUNS", "5")))
ALERT_FAIL_RATE = float(os.getenv("BOTLY_ALERT_FAIL_RATE", "0.3"))
ALERT_COOLDOWN_MINUTES = max(1, int(os.getenv("BOTLY_ALERT_COOLDOWN_MINUTES", "30")))
ALERT_CHECK_INTERVAL = max(10, int(os.getenv("BOTLY_ALERT_CHECK_INTERVAL", "60")))

# Worker
WORKER_POLL_INTERVAL = 0.5
WORKER_LEASE_SECONDS = 180

# Redis：队列 + 事件广播；不可用时自动降级为 DB 轮询
REDIS_URL = os.getenv("BOTLY_REDIS_URL", "redis://127.0.0.1:6379/0")

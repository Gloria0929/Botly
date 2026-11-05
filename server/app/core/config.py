"""全局配置。

第一版默认使用 SQLite（零依赖、单机可跑），通过 DATABASE_URL 可切换到 Postgres。
所有可调参数集中在这里，对应需求文档附录 A。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

# server/ 目录：本文件在 app/core/ 下，往上三层才是工程根
BASE_DIR = Path(__file__).resolve().parents[2]


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
APP_ENV = os.getenv("BOTLY_ENV", "development").strip().lower()
DEVELOPMENT_ENVS = {"development", "dev", "test", "testing"}

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
# OAuth 回调时校验 state 用的一次性 nonce（只活 10 分钟）
OAUTH_STATE_COOKIE = "botly_oauth_nonce"
OAUTH_STATE_MAX_AGE = 600
# 部署在反向代理/HTTPS 后面时，通过环境变量强制 Secure，不依赖代理头是否被信任
COOKIE_SECURE = (
    os.getenv("BOTLY_COOKIE_SECURE", "").lower() in ("1", "true", "yes")
    or os.getenv("BOTLY_BASE_URL", "").lower().startswith("https://")
)

# ---- 附录 A 默认参数 ----
DEFAULT_TOKEN_BUDGET = 300_000
MAX_CONCURRENT_RUNS_PER_USER = max(1, int(os.getenv("BOTLY_MAX_CONCURRENT_RUNS_PER_USER", "2")))
MAX_CONCURRENT_RUNS = max(1, int(os.getenv("BOTLY_MAX_CONCURRENT_RUNS", "20")))
WORKER_CONCURRENCY = max(1, int(os.getenv("BOTLY_WORKER_CONCURRENCY", "20")))
LLM_TIMEOUT_SECONDS = 120
LLM_MAX_RETRIES = 5
LLM_RETRY_BACKOFF = (2, 4, 8, 16, 32)
MAX_REASSIGN = 1
# RUN-12：一个计划批次最多并行几个子任务。超过上限的批次会被拆成多轮，
# 避免一次把 Worker 的并发名额占满。
MAX_PARALLEL_SUBTASKS = max(2, int(os.getenv("BOTLY_MAX_PARALLEL_SUBTASKS", "4")))
APPROVAL_TTL_HOURS = 24
MEMORY_INJECT_LIMIT = 4000
# MEM-05：主线程历史超过该 token 估算值时，用总指挥模型生成摘要替换更早的历史。
SUMMARY_THRESHOLD_TOKENS = max(1000, int(os.getenv("BOTLY_SUMMARY_THRESHOLD_TOKENS", "8000")))
SUMMARY_MAX_ITEMS = max(10, int(os.getenv("BOTLY_SUMMARY_MAX_ITEMS", "80")))
HISTORY_KEEP_RECENT = 40  # 摘要后仍保留在上下文里的最近消息条数
WEB_READ_TRUNCATE = 20_000
# 网页读取先按字节截断，避免恶意/超大响应把进程内存打满（需求 8.4）
WEB_READ_MAX_BYTES = 5 * 1024 * 1024
# 转成纯文本后短于该长度，视为只拿到页面框架（脚本渲染或反爬拦截），不算读取成功
WEB_READ_MIN_TEXT = 120

# ---- ACC-08 邮件（找回密码、邮箱验证）----
SMTP_HOST = os.getenv("BOTLY_SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("BOTLY_SMTP_PORT", "587"))
SMTP_USER = os.getenv("BOTLY_SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("BOTLY_SMTP_PASSWORD", "")
# starttls（587，默认）/ ssl（465）/ none（内网中继）
SMTP_SECURITY = os.getenv("BOTLY_SMTP_SECURITY", "starttls").strip().lower()
MAIL_FROM = os.getenv("BOTLY_MAIL_FROM", "").strip() or (SMTP_USER or "no-reply@botly.local")
# 没配 SMTP 时：1 = 把邮件写进日志并在接口里回一个可点的 dev 链接（本地/测试），0 = 直接报“邮件服务未配置”
MAIL_DEV = APP_ENV in DEVELOPMENT_ENVS and os.getenv("BOTLY_MAIL_DEV", "1").lower() in ("1", "true", "yes")
# 邮件里的链接前缀；仅开发/测试环境允许留空并按请求推导。
PUBLIC_BASE_URL = os.getenv("BOTLY_BASE_URL", "").rstrip("/")
if APP_ENV in {"production", "prod"}:
    if SECRET_KEY == "dev-insecure-secret-key-please-change":
        raise RuntimeError("生产环境必须配置 BOTLY_SECRET_KEY")
    if len(SECRET_KEY) < 32:
        raise RuntimeError("生产环境 BOTLY_SECRET_KEY 至少需要 32 个字符")
    if not _raw_enc:
        raise RuntimeError("生产环境必须单独配置 BOTLY_ENCRYPTION_KEY")
ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("BOTLY_ADMIN_EMAILS", "").split(",") if e.strip()}
RESET_TOKEN_TTL_MINUTES = max(5, int(os.getenv("BOTLY_RESET_TOKEN_TTL_MINUTES", "10")))
VERIFY_TOKEN_TTL_HOURS = max(1, int(os.getenv("BOTLY_VERIFY_TOKEN_TTL_HOURS", "24")))
SANDBOX_QUOTA_MB = 200

# ---- Google / GitHub 第三方登录 ----
# 没配 client id/secret 时该登录方式自动隐藏，不影响邮箱密码登录。
GOOGLE_CLIENT_ID = os.getenv("BOTLY_GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("BOTLY_GOOGLE_CLIENT_SECRET", "").strip()
GITHUB_CLIENT_ID = os.getenv("BOTLY_GITHUB_CLIENT_ID", "").strip()
GITHUB_CLIENT_SECRET = os.getenv("BOTLY_GITHUB_CLIENT_SECRET", "").strip()
# 回调地址前缀。留空按请求头推导（反代下也是对的）；必须与 OAuth 后台登记的
# Redirect URI 完全一致，所以对外域名和内部不一致时要显式配这个。
OAUTH_REDIRECT_BASE = os.getenv("BOTLY_OAUTH_REDIRECT_BASE", "").rstrip("/")
# 陌生账号（邮箱没注册过）能不能直接用 Google/GitHub 登录：1 = 允许并自动建号，
# 0 = 只允许已注册用户绑定登录。邀请制部署建议设 0。
OAUTH_ALLOW_SIGNUP = os.getenv("BOTLY_OAUTH_ALLOW_SIGNUP", "1").lower() in ("1", "true", "yes")
OAUTH_TIMEOUT = max(5, int(os.getenv("BOTLY_OAUTH_TIMEOUT", "15")))

# ---- NTF-06 Web Push（Service Worker + VAPID）----
# VAPID 私钥：PEM 文本或 32 字节原始私钥的 base64url。不配置时首次状态自动生成，
# 持久化到 DATA_DIR/vapid.json（多实例部署请显式配置，保证各实例用同一把公钥）。
VAPID_PRIVATE_KEY = os.getenv("BOTLY_VAPID_PRIVATE_KEY", "").strip()
# VAPID sub 声明，要求是 mailto: 或 https: 开头
VAPID_SUBJECT = os.getenv("BOTLY_VAPID_SUBJECT", "mailto:no-reply@botly.local")
# 推送消息在推送服务里的存活秒数；页面关闭期间的消息超时后不再投递
PUSH_TTL = max(60, int(os.getenv("BOTLY_PUSH_TTL", str(24 * 3600))))
SANDBOX_FILE_MAX_MB = 10
LOG_RETENTION_DAYS = 30
EVENT_RETENTION_DAYS = 7  # 需求第 10 节：events 保留 7 天用于断线补发
# 应用日志：级别、单文件上限（MB）、是否记录请求/响应体与查询参数
LOG_LEVEL = os.getenv("BOTLY_LOG_LEVEL", "INFO").upper()
LOG_LEVEL_NUM = getattr(logging, LOG_LEVEL, logging.INFO) if LOG_LEVEL.isalpha() else logging.INFO
LOG_FILE_MAX_MB = max(1, int(os.getenv("BOTLY_LOG_FILE_MAX_MB", "20")))
LOG_BODY = os.getenv("BOTLY_LOG_BODY", "1").lower() in ("1", "true", "yes")
# RUN-09 / LIM-01：委派深度（总指挥 → 子 Bot 记为第 1 层）。
# 1 = 只有总指挥能派任务（旧行为）；2 = 允许子 Bot 再向下派一层，以此类推。
# 默认值可被用户设置与单个群聊的 limits_override 覆盖（见 orchestrator._limits）。
DELEGATION_DEPTH = max(1, min(5, int(os.getenv("BOTLY_MAX_DELEGATION_DEPTH", "2"))))

# ---- TOL-05 MCP 工具 ----
# 默认只允许公网地址（SSRF 防护）。自建/本机 MCP 服务把该开关设为 1。
MCP_ALLOW_LOCAL = os.getenv("BOTLY_MCP_ALLOW_LOCAL", "0").lower() in ("1", "true", "yes")
MCP_STDIO_ENABLED = os.getenv("BOTLY_MCP_STDIO_ENABLED", "0").lower() in ("1", "true", "yes")
# 每项是完整 argv（绝对命令路径 + 固定参数），不允许任意解释器参数。
MCP_STDIO_ALLOWED_COMMANDS = json.loads(os.getenv("BOTLY_MCP_STDIO_ALLOWED_COMMANDS", "[]"))
if not isinstance(MCP_STDIO_ALLOWED_COMMANDS, list) or any(
    not isinstance(argv, list) or not argv or any(not isinstance(arg, str) for arg in argv)
    or not os.path.isabs(argv[0]) for argv in MCP_STDIO_ALLOWED_COMMANDS
):
    raise RuntimeError("BOTLY_MCP_STDIO_ALLOWED_COMMANDS 必须是完整命令参数数组的 JSON 列表，命令使用绝对路径")
ALLOW_PRIVATE_BASE_URL = os.getenv("BOTLY_ALLOW_PRIVATE_BASE_URL", "0").lower() in ("1", "true", "yes")
MCP_TIMEOUT = max(5, int(os.getenv("BOTLY_MCP_TIMEOUT", "30")))
MCP_MAX_TOOLS = max(1, int(os.getenv("BOTLY_MCP_MAX_TOOLS", "80")))
MCP_MAX_RESULT_CHARS = max(1000, int(os.getenv("BOTLY_MCP_MAX_RESULT_CHARS", "20000")))

# ---- TOL-06 代码执行（跑在群聊的常驻 Ubuntu 容器里）----
CODE_SANDBOX_ENABLED = os.getenv("BOTLY_CODE_SANDBOX_ENABLED", "1").lower() not in ("0", "false", "no")
CODE_SANDBOX_TIMEOUT = max(5, int(os.getenv("BOTLY_CODE_SANDBOX_TIMEOUT", "30")))
CODE_SANDBOX_MAX_OUTPUT = max(1000, int(os.getenv("BOTLY_CODE_SANDBOX_MAX_OUTPUT", "20000")))
# 代码执行默认按 L2（沙箱内写入、免审批）；容器是 root + 联网，想让它和
# run_command 一样默认需要审批就设成 L3，仍支持当前群聊的工具白名单。
CODE_EXEC_LEVEL = os.getenv("BOTLY_CODE_EXEC_LEVEL", "L2").upper()
if CODE_EXEC_LEVEL not in ("L1", "L2", "L3"):
    CODE_EXEC_LEVEL = "L2"

# ---- 常驻 Linux 状态环境（每个群聊一个容器，root + 联网，可装包）----
LINUX_SANDBOX_ENABLED = os.getenv("BOTLY_LINUX_SANDBOX_ENABLED", "1").lower() not in ("0", "false", "no")
# Docker 仅供本地测试；云沙箱通过受信任的服务端适配器接入，未配置时不启动容器。
SANDBOX_PROVIDER = os.getenv("BOTLY_SANDBOX_PROVIDER", "none").strip().lower()
SANDBOX_ADAPTER = os.getenv("BOTLY_SANDBOX_ADAPTER", "").strip()
# 预装镜像（deploy/sandbox.Dockerfile 构建）；镜像不存在时自动回退到 BASELINE
LINUX_SANDBOX_IMAGE = os.getenv("BOTLY_LINUX_SANDBOX_IMAGE", "botly-sandbox:24.04")
LINUX_SANDBOX_BASELINE_IMAGE = os.getenv("BOTLY_LINUX_SANDBOX_BASELINE_IMAGE", "ubuntu:24.04")
LINUX_SANDBOX_TIMEOUT = max(10, int(os.getenv("BOTLY_LINUX_SANDBOX_TIMEOUT", "60")))
LINUX_SANDBOX_MEMORY = os.getenv("BOTLY_LINUX_SANDBOX_MEMORY", "1g")
LINUX_SANDBOX_CPUS = os.getenv("BOTLY_LINUX_SANDBOX_CPUS", "1.0")
LINUX_SANDBOX_MAX_OUTPUT = max(1000, int(os.getenv("BOTLY_LINUX_SANDBOX_MAX_OUTPUT", "20000")))
# run_command 首次执行时容器里缺这些包会自动 apt 安装（留空则完全不装）
LINUX_SANDBOX_AUTO_INSTALL = os.getenv("BOTLY_LINUX_SANDBOX_AUTO_INSTALL", "1").lower() not in ("0", "false", "no")

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
WORKER_SHUTDOWN_TIMEOUT = max(1, int(os.getenv("BOTLY_WORKER_SHUTDOWN_TIMEOUT", "8")))
DB_POOL_SIZE = max(1, int(os.getenv("BOTLY_DB_POOL_SIZE", "10")))
DB_MAX_OVERFLOW = max(0, int(os.getenv("BOTLY_DB_MAX_OVERFLOW", "20")))

# Redis：队列 + 事件广播；不可用时自动降级为 DB 轮询
REDIS_URL = os.getenv("BOTLY_REDIS_URL", "redis://127.0.0.1:6379/0")
if APP_ENV in {"production", "prod"}:
    for label, url in (("DATABASE_URL", DATABASE_URL), ("BOTLY_REDIS_URL", REDIS_URL)):
        parsed = urlsplit(url)
        if parsed.scheme.startswith(("postgres", "redis", "rediss")):
            password = unquote(parsed.password or "")
            if len(password) < 16:
                raise RuntimeError(f"生产环境 {label} 的密码至少需要 16 个字符")

"""测试邮件配置：打印当前生效的 SMTP 参数，并可选发一封测试邮件。

用法：
  ./venv/bin/python scripts/test_mail.py                 # 只看当前配置
  ./venv/bin/python scripts/test_mail.py you@example.com # 真发一封

注意：配置来自 server/.env（或同名环境变量），改完 .env 不需要重启本脚本；
但正在跑的 API / Worker 是在启动时读的，改完要重启才会生效。
"""
from __future__ import annotations

import smtplib
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import mail  # noqa: E402
from app.core.config import (
    MAIL_DEV,
    MAIL_FROM,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_SECURITY,
    SMTP_USER,
)


def _mask(value: str) -> str:
    if not value:
        return "（未设置）"
    return value if len(value) <= 2 else f"{value[:1]}***{value[-1:]}（{len(value)} 位）"


def _print_config() -> None:
    print("当前邮件配置：")
    print(f"  BOTLY_SMTP_HOST      {SMTP_HOST or '（未配置）'}")
    print(f"  BOTLY_SMTP_PORT      {SMTP_PORT}")
    print(f"  BOTLY_SMTP_SECURITY  {SMTP_SECURITY}")
    print(f"  BOTLY_SMTP_USER      {SMTP_USER or '（未配置，不登录）'}")
    print(f"  BOTLY_SMTP_PASSWORD  {_mask(SMTP_PASSWORD)}")
    print(f"  BOTLY_MAIL_FROM      {MAIL_FROM}")
    print(f"  BOTLY_MAIL_DEV       {'1（开发模式：只写日志，不发信）' if MAIL_DEV else '0（真发信）'}")
    if not SMTP_HOST:
        print("\n→ 没有配置 SMTP_HOST：接口会把邮件内容写进日志，并回一个 dev_link。")


def _hints(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return (
            "认证失败（535）。多数邮箱要求用「授权码 / 应用专用密码」，不是登录密码；"
            "确认账号已开启 SMTP 服务，且 USER 与 MAIL_FROM 是同一个邮箱。"
        )
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return "服务器主动断开。端口与安全方式不匹配（587 配 starttls / 465 配 ssl），或 IP 被限制。"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "连接超时。检查网络/防火墙是否放行该端口，或换成 465 + ssl。"
    if isinstance(exc, ConnectionRefusedError) or getattr(exc, "errno", None) == 61:
        return "连接被拒绝。HOST/PORT 填错，或本机网络挡住了 SMTP 端口。"
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "收件人被拒。检查收件地址是否存在，或发件服务限制了收件域。"
    return text


def main() -> int:
    _print_config()
    to = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    if not to:
        print("\n未指定收件人，未发送。用法：")
        print("  ./venv/bin/python scripts/test_mail.py you@example.com")
        return 0

    print(f"\n正在发送测试邮件到 {to} …")
    try:
        delivered = mail.send(
            to,
            "Botly 邮件测试",
            "这是一封来自 Botly 的测试邮件。\n\n"
            "如果你收到了这封信，说明 SMTP 配置正确，找回密码与邮箱验证都能正常发信。",
        )
    except Exception as exc:  # noqa: BLE001 - 命令行工具，直接给出可读原因
        print(f"\n发送失败：{_hints(exc)}")
        return 1

    if delivered:
        print("已交给 SMTP 服务器投递，请检查收件箱（含垃圾邮件）。")
    else:
        print("当前是开发模式（未配 SMTP 或 BOTLY_MAIL_DEV=1）：邮件只写进了日志，没有真的发出。")
        print("要真发信：配好 SMTP 并把 BOTLY_MAIL_DEV 设为 0，然后重跑本脚本。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

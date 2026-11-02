"""邮件发送（ACC-08：找回密码、邮箱验证）。

只做最朴素的一件事：SMTP 发纯文本 + 可选 HTML。没有引入第三方 SDK，
因为第一版只需要发两类事务邮件，多一个依赖就多一份部署/安全负担。

未配置 SMTP 时有两种行为：
- `MAIL_DEV=1`（默认）：不真的发信，把邮件内容写进 WARNING 日志，并在接口里
  回一个可点的链接，方便本地和测试环境走完整流程；
- `MAIL_DEV=0`：抛 `MailNotConfigured`，由接口返回 503「邮件服务未配置」，
  避免生产环境出现「看起来发了、其实没人收到」的静默失败。
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from .config import MAIL_DEV, MAIL_FROM, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_SECURITY, SMTP_USER
from .logging_setup import get_logger

log = get_logger("mail")


class MailNotConfigured(RuntimeError):
    """没有可用的 SMTP 配置，且不允许落日志兜底。"""


def configured() -> bool:
    return bool(SMTP_HOST)


def send(to: str, subject: str, text: str, html: str | None = None) -> bool:
    """发一封邮件。返回 True 表示真的投递到了 SMTP，False 表示开发模式只写了日志。

    失败会抛异常（SMTP 认证错误、超时等），由调用方决定是否暴露给用户。
    """
    if not configured():
        if not MAIL_DEV:
            raise MailNotConfigured("邮件服务未配置")
        log.warning("邮件未发送（未配置 SMTP，开发模式）\n收件人：%s\n主题：%s\n%s", to, subject, text)
        return False

    msg = EmailMessage()
    msg["From"] = formataddr(("Botly", MAIL_FROM))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=MAIL_FROM.split("@")[-1] or "botly.local")
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")

    # 超时留 10 秒：不能让一封邮件把 Web 请求拖到网关超时
    if SMTP_SECURITY == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10,
                                                context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10)
    try:
        server.ehlo()
        if SMTP_SECURITY == "starttls":
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        if SMTP_USER and SMTP_PASSWORD:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001 - 关闭失败不影响已投递的邮件
            pass
    log.info("邮件已发送 to=%s subject=%s", to, subject)
    return True


# --------------------------------------------------------------------------- #
# 两类事务邮件的文案
# --------------------------------------------------------------------------- #
def password_reset_mail(link: str, ttl_minutes: int) -> tuple[str, str, str]:
    subject = "重置你的 Botly 密码"
    text = (
        "有人（或你自己）在 Botly 请求重置这个邮箱对应的密码。\n\n"
        f"点下面的链接设置新密码，{ttl_minutes} 分钟内有效，且只能使用一次：\n{link}\n\n"
        "链接打开后需要输入两次新密码。重置成功后，其他设备上的登录状态会立即失效。\n\n"
        "如果这不是你本人操作，忽略这封邮件即可，密码不会被修改。\n"
    )
    html = f"""\
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;line-height:1.7;color:#1f2328">
  <p>有人（或你自己）在 Botly 请求重置这个邮箱对应的密码。</p>
  <p>点下面的按钮设置新密码，<b>{ttl_minutes} 分钟</b>内有效，且只能使用一次：</p>
  <p style="margin:20px 0">
    <a href="{link}" style="display:inline-block;padding:10px 18px;border-radius:8px;background:#4f63ff;color:#fff;text-decoration:none">设置新密码</a>
  </p>
  <p style="color:#6b7280;font-size:12.5px;word-break:break-all">{link}</p>
  <p>重置成功后，其他设备上的登录状态会立即失效。</p>
  <p style="color:#6b7280;font-size:12.5px">如果这不是你本人操作，忽略这封邮件即可，密码不会被修改。</p>
</div>"""
    return subject, text, html


def verify_email_mail(link: str, ttl_hours: int) -> tuple[str, str, str]:
    subject = "验证你的 Botly 邮箱"
    text = (
        "请在 Botly 中验证这个邮箱地址。\n\n"
        f"点下面的链接完成验证，{ttl_hours} 小时内有效，且只能使用一次：\n{link}\n\n"
        "验证后，找回密码等功能才能正常使用。如果这不是你本人操作，忽略这封邮件即可。\n"
    )
    html = f"""\
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;line-height:1.7;color:#1f2328">
  <p>请在 Botly 中验证这个邮箱地址。</p>
  <p style="margin:20px 0">
    <a href="{link}" style="display:inline-block;padding:10px 18px;border-radius:8px;background:#4f63ff;color:#fff;text-decoration:none">验证邮箱</a>
  </p>
  <p style="color:#6b7280;font-size:12.5px;word-break:break-all">{link}</p>
  <p style="color:#6b7280;font-size:12.5px">链接 {ttl_hours} 小时内有效，且只能使用一次。</p>
</div>"""
    return subject, text, html

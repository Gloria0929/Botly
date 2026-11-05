"""从正式邮件模板生成可离线打开的静态预览，不发送邮件。"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

from app.services.mail import password_reset_mail  # noqa: E402


def main() -> None:
    public = SERVER.parent / "web" / "public"
    link = "https://botly.example/reset-password?token=preview_7e9b42c8d15f6a30_example_only"
    _, _, html = password_reset_mail(link, 60)
    logo = base64.b64encode((public / "email-logo.png").read_bytes()).decode("ascii")
    html = html.replace("https://botly.example/email-logo.png", "data:image/png;base64," + logo)
    html = html.replace("<title>重置你的 Botly 密码</title>", "<title>邮件预览 · 重置你的 Botly 密码</title>")
    html = html.replace("</head>", f'<link rel="icon" type="image/png" href="data:image/png;base64,{logo}">\n</head>')
    html = html.replace("</body>", """<script>
document.addEventListener('click', function (event) {
  if (!(event.target instanceof Element) || !event.target.closest('[data-reset-link]')) return;
  event.preventDefault();
  let note = document.getElementById('preview-feedback');
  if (!note) {
    note = document.createElement('p');
    note.id = 'preview-feedback';
    note.setAttribute('role', 'status');
    note.style.cssText = 'margin:16px 0 0;font-size:12px;line-height:1.8;color:#62626d;';
    document.querySelector('[data-reset-link]').closest('table').after(note);
  }
  note.textContent = '这是静态预览。正式邮件中的链接会打开设置新密码页面。';
});
</script>
</body>""")
    output = public / "previews" / "password-reset-email.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()

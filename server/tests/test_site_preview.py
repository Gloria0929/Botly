"""完整静态构建、资源隔离及限时预览凭证。"""
from app.api import preview
from app.core.db import SessionLocal
from app.core.models import FileRec, User
from app.sandbox import files
from tests.conftest import register


def make_site(client, invite, label):
    account = register(client, invite, f"preview-{label}@test.dev")
    assets = {
        "index.html": '<html><head><link rel="stylesheet" href="/assets/main.css"></head><body><h1>Preview</h1><script type="module" src="/assets/main.js"></script></body></html>',
        "assets/main.js": 'import {value} from "./chunk.js"; document.querySelector("h1").textContent=value; fetch("/data.json");',
        "assets/chunk.js": 'export const value="Built app";',
        "assets/main.css": 'h1{color:rgb(0, 128, 0);background-image:url(/logo.svg)}',
        "logo.svg": '<svg xmlns="http://www.w3.org/2000/svg"/>',
        "data.json": '{"ready":true}',
        "about.html": '<h1>About</h1>',
    }
    with SessionLocal() as db:
        for name, content in assets.items():
            path = f"output/site/dist/{name}"
            size = files.write_file(account["id"], account["chat_id"], path, content)
            rec = FileRec(user_id=account["id"], chat_id=account["chat_id"], path=path, size=size)
            db.add(rec)
            if name == "index.html":
                db.flush()
                file_id = rec.id
        db.commit()
    return account, file_id


def test_built_site_loads_html_modules_css_and_binary_assets_without_cookies(client, invite):
    account, file_id = make_site(client, invite, "assets")
    response = client.get(f"/api/files/{file_id}/site")
    assert response.status_code == 200
    url = response.json()["url"]
    prefix = url.rsplit("/", 1)[0] + "/"
    # 模块使用 anonymous CORS；opaque iframe 不依赖 Cookie。
    client.cookies.clear()
    html = client.get(url)
    assert html.status_code == 200 and prefix + "assets/main.js" in html.text
    assert prefix + "assets/main.css" in html.text
    assert 'crossorigin="anonymous"' in html.text
    assert "sandbox allow-scripts" in html.headers["content-security-policy"]
    assert "allow-same-origin" not in html.headers["content-security-policy"]
    js = client.get(prefix + "assets/main.js", headers={"Origin": "null"})
    assert js.status_code == 200 and 'from "./chunk.js"' in js.text
    assert prefix + "data.json" in js.text
    assert js.headers["access-control-allow-origin"] == "*"
    assert js.headers["content-type"].startswith("text/javascript")
    assert "value=" in client.get(prefix + "assets/chunk.js").text
    css = client.get(prefix + "assets/main.css")
    assert prefix + "logo.svg" in css.text
    assert client.get(prefix + "logo.svg").headers["content-type"].startswith("image/svg+xml")
    assert client.get(prefix + "data.json").json() == {"ready": True}
    assert "About" in client.get(prefix + "about.html").text
    assert client.get(f"/api/files/{file_id}/site").status_code == 401


def test_preview_is_scoped_to_entry_directory_and_rejects_symlinks(client, invite):
    account, file_id = make_site(client, invite, "scope")
    url = client.get(f"/api/files/{file_id}/site").json()["url"]
    prefix = url.rsplit("/", 1)[0] + "/"
    files.write_file(account["id"], account["chat_id"], "output/site/private.txt", "private")
    files.write_file(account["id"], account["chat_id"], "output/site/dist/.env", "secret")
    root = files.safe_path(account["id"], account["chat_id"], "output/site/dist")
    (root / "leak.txt").symlink_to(root.parent / "private.txt")
    for path in ["%2e%2e/private.txt", "%2e%2e%2fprivate.txt", "%2fetc/passwd", ".env", "leak.txt", "missing.js", "assets/%5c..%5c.env"]:
        assert client.get(prefix + path).status_code == 404, path
    register(client, invite, "preview-other@test.dev")
    assert client.get(f"/api/files/{file_id}/site").status_code == 404


def test_preview_token_tamper_expiry_revocation_and_deleted_entry(client, invite, monkeypatch):
    account, file_id = make_site(client, invite, "token")
    url = client.get(f"/api/files/{file_id}/site").json()["url"]
    assert client.get(url.replace("/preview/", "/preview/bad")).status_code == 403
    monkeypatch.setattr(preview, "MAX_AGE", -1)
    assert client.get(url).status_code == 403
    monkeypatch.setattr(preview, "MAX_AGE", 3600)
    with SessionLocal() as db:
        db.get(User, account["id"]).session_epoch += 1
        db.commit()
    assert client.get(url).status_code == 404
    with SessionLocal() as db:
        db.get(User, account["id"]).session_epoch -= 1
        db.commit()
    files.delete_file(account["id"], account["chat_id"], "output/site/dist/index.html")
    assert client.get(url).status_code == 404


def test_preview_requires_html_in_output(client, invite):
    account, file_id = make_site(client, invite, "entry")
    with SessionLocal() as db:
        for path in ("outside.html", "output/program.py"):
            files.write_file(account["id"], account["chat_id"], path, "test")
            rec = FileRec(user_id=account["id"], chat_id=account["chat_id"], path=path, size=4)
            db.add(rec); db.commit()
            assert client.get(f"/api/files/{rec.id}/site").status_code == 400

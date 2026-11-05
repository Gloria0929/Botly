"""以只读、限时凭证加载完整静态站点；iframe 保持独立的 opaque origin。"""
from pathlib import Path, PurePosixPath
import base64
import hashlib
import json
import mimetypes
import re
from typing import Literal
from urllib.parse import quote, unquote, urlsplit

from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.core.config import SECRET_KEY
from app.core.db import get_db
from app.core.deps import current_user, owned_or_404
from app.core.models import FileRec, User
from app.sandbox import files
from app.sandbox.artifacts import is_output_file
from app.sandbox import preview_service

router = APIRouter(tags=["preview"])
_tokens = URLSafeTimedSerializer(SECRET_KEY, salt="botly-site-preview")
MAX_AGE = 3600
HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    # ES modules、字体和 fetch 来自沙箱的 null origin，不携带会话凭证。
    "Access-Control-Allow-Origin": "*",
    "Content-Security-Policy": "default-src 'none'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
        "media-src 'self' data: blob:; connect-src 'self'; base-uri 'self'; "
        "form-action 'none'; frame-ancestors 'self'; sandbox allow-scripts",
}


class PreviewCorsMiddleware:
    """仅预览后端接受 opaque iframe 的预检，不放宽 Botly 自身 API。"""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope["type"] == "http" and scope["method"] == "OPTIONS"
                and scope["path"].startswith("/api/preview/") and "/__backend/" in scope["path"]):
            response = Response(status_code=204, headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Requested-With",
                "Access-Control-Max-Age": "600",
            })
            return await response(scope, receive, send)
        await self.app(scope, receive, send)


def backend_config(rec):
    try:
        return preview_service.configuration(rec.user_id, rec.chat_id, rec.path)
    except (ValueError, OSError, TypeError, AttributeError, files.SandboxError) as exc:
        raise HTTPException(400, f"后端预览配置无效：{exc}")


def backend_signature(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest() if config else None


def token_context(token, db):
    try:
        data = _tokens.loads(token, max_age=MAX_AGE)
    except BadSignature:
        raise HTTPException(403, "预览已过期，请刷新预览")
    rec = db.get(FileRec, data.get("file"))
    user = db.get(User, rec.user_id) if rec else None
    if not user or user.status != "active" or data.get("epoch") != (user.session_epoch or 0) or data.get("path") != rec.path:
        raise HTTPException(404, "预览不存在")
    return rec, data


def entry_path(rec: FileRec) -> Path:
    if not is_output_file(rec.user_id, rec.chat_id, rec.path) or Path(rec.path).suffix.lower() not in {".html", ".htm"}:
        raise HTTPException(400, "请选择 output 中构建完成的 HTML 入口")
    try:
        path = files.safe_path(rec.user_id, rec.chat_id, rec.path)
    except files.SandboxError:
        raise HTTPException(404, "页面不存在")
    if not path.is_file():
        raise HTTPException(404, "页面已不存在，请重新构建")
    return path


@router.get("/files/{file_id}/site")
def site(file_id: str, response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rec = owned_or_404(db, FileRec, file_id, user.id)
    path = entry_path(rec)
    config = backend_config(rec)
    token = _tokens.dumps({"file": rec.id, "path": rec.path, "epoch": user.session_epoch or 0,
                          "backend": backend_signature(config)})
    response.headers["Cache-Control"] = "no-store"
    return {"url": f"/api/preview/{token}/{quote(path.name)}", "expires_in": MAX_AGE,
            "backend": {"port": config["port"], "manifest": config["manifest"]} if config else None}


class BackendAction(BaseModel):
    action: Literal["start", "stop", "status"] = "start"


@router.post("/files/{file_id}/backend")
async def backend_action(file_id: str, body: BackendAction, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rec = owned_or_404(db, FileRec, file_id, user.id)
    entry_path(rec)
    config = backend_config(rec)
    if not config:
        raise HTTPException(400, "项目尚未配置 preview.json 的 backend 启动信息")
    user_id, chat_id = rec.user_id, rec.chat_id
    db.commit()
    try:
        return await preview_service.invoke(user_id, chat_id, config, body.action)
    except Exception as exc:
        from app.sandbox.cube import connection_for, safe_error
        connection = connection_for(user_id)
        detail = safe_error(exc, connection) if connection else str(exc)
        raise HTTPException(502, f"后端预览失败：{detail}")


@router.api_route("/preview/{token}/__backend/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"])
async def backend_proxy(token: str, path: str, request: Request, db: Session = Depends(get_db)):
    rec, data = token_context(token, db)
    entry_path(rec)
    config = backend_config(rec)
    if not config or data.get("backend") != backend_signature(config):
        raise HTTPException(409, "后端配置已变化，请刷新预览")
    upstream_path = "/" + path
    if (not (upstream_path == config["api_prefix"] or upstream_path.startswith(config["api_prefix"] + "/"))
            or ".." in PurePosixPath(path).parts or "\\" in path or any(ord(c) < 32 for c in path)):
        raise HTTPException(404, "接口不在配置的 API 范围内")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 2 * 1024 * 1024:
            raise HTTPException(413, "请求体超过 2 MB 限制")
    if request.url.query:
        upstream_path += "?" + request.url.query
    headers = {key: value for key, value in request.headers.items() if key.lower() in {"accept", "content-type", "authorization"}}
    user_id, chat_id = rec.user_id, rec.chat_id
    db.commit()
    try:
        result = await preview_service.invoke(user_id, chat_id, config, "request", method=request.method,
            path=upstream_path, headers=headers, body=base64.b64encode(body).decode())
    except Exception:
        return Response(json.dumps({"message": "后端未就绪或请求失败，请刷新预览重新启动后端"}),
                        status_code=502, media_type="application/json", headers=HEADERS)
    # 不转发宿主 Cookie、沙箱管理密钥、后端 Set-Cookie 或重定向到宿主的 Location。
    content_type = next((v for k, v in result["headers"].items() if k.lower() == "content-type"), "application/octet-stream")
    return Response(base64.b64decode(result["body"]), status_code=result["status"],
                    headers={**HEADERS, "Content-Type": content_type})


def rewrite_assets(content: str, suffix: str, root: Path, prefix: str, backend: dict | None = None) -> str:
    """相对 URL 原样交给浏览器；常见构建器的根路径资源映射到本站点。"""
    def rooted(url: str) -> str:
        if not url.startswith("/") or url.startswith("//"):
            return url
        parsed = urlsplit(url)
        relative = unquote(parsed.path).lstrip("/")
        candidate = root / relative
        if ".." in PurePosixPath(relative).parts or not candidate.is_file():
            return url
        return prefix + url[1:]

    def quoted(match):
        return match[1] + rooted(match[2]) + match[1]

    def css(text):
        return re.sub(r"url\(\s*(['\"]?)(/[^\s)'\"]+)\1\s*\)",
                      lambda m: "url(" + m[1] + rooted(m[2]) + m[1] + ")", text)

    def script(text):
        return re.sub(r"(['\"])(/[^\s'\"\\]*)\1", quoted, text)

    if suffix in {".js", ".mjs"}:
        return script(content)
    if suffix == ".css":
        return css(script(content))
    if suffix not in {".html", ".htm"}:
        return content
    soup = BeautifulSoup(content, "html.parser")
    for base in soup.find_all("base"):
        base.decompose()
    for tag in soup.find_all(True):
        for attr in ("src", "href", "poster", "data"):
            if isinstance(tag.get(attr), str):
                tag[attr] = rooted(tag[attr])
        if tag.get("srcset"):
            tag["srcset"] = re.sub(r"(^|,\s*)(/[^\s,]+)", lambda m: m[1] + rooted(m[2]), tag["srcset"])
        if tag.get("style"):
            tag["style"] = css(tag["style"])
        # 重写后的 JS/CSS 字节发生变化，旧构建校验值不再适用。
        if tag.name in {"script", "link"}:
            tag.attrs.pop("integrity", None)
            tag["crossorigin"] = "anonymous"
        if tag.string and tag.name in {"script", "style"}:
            tag.string.replace_with(script(str(tag.string)) if tag.name == "script" else css(str(tag.string)))
    if backend:
        bridge = soup.new_tag("script")
        bridge.string = backend_bridge(prefix, backend)
        if soup.head:
            soup.head.insert(0, bridge)
        else:
            soup.insert(0, bridge)
    return str(soup)


def backend_bridge(prefix: str, config: dict) -> str:
    # 包括运行时拼接的 /api 路径、Request 对象以及 axios 的 XHR 适配器。
    settings = json.dumps({"base": prefix, "proxy": prefix.rstrip("/") + "/__backend", "api": config["api_prefix"], "port": config["port"]})
    return "(() => { const {base, proxy, api, port} = " + settings + ";\n" + """
function rewrite(value) {
  const url = new URL(value, location.href);
  const local = url.origin === location.origin ||
    (["localhost", "127.0.0.1", "0.0.0.0", "[::1]"].includes(url.hostname) && url.port === String(port));
  const path = url.pathname.startsWith(base) ? "/" + url.pathname.slice(base.length) : url.pathname;
  return local && (path === api || path.startsWith(api + "/"))
    ? proxy + path + url.search : url.href;
}
const originalFetch = window.fetch.bind(window);
window.fetch = (input, init) => originalFetch(input instanceof Request
  ? new Request(rewrite(input.url), input) : rewrite(input), {...init, credentials: "omit"});
const originalOpen = XMLHttpRequest.prototype.open;
XMLHttpRequest.prototype.open = function(method, url, ...args) {
  return originalOpen.call(this, method, rewrite(url), ...args);
};
})();"""


@router.get("/preview/{token}/{asset:path}")
def asset(token: str, asset: str, db: Session = Depends(get_db)):
    rec, data = token_context(token, db)
    root = entry_path(rec).parent
    relative = PurePosixPath(asset)
    if relative.is_absolute() or ".." in relative.parts or "\\" in asset:
        raise HTTPException(404, "资源不存在")
    rel = (root / str(relative)).relative_to(files.chat_root(rec.user_id, rec.chat_id)).as_posix()
    try:
        path = files.safe_path(rec.user_id, rec.chat_id, rel)
        path.relative_to(root)
    except (files.SandboxError, ValueError):
        raise HTTPException(404, "资源不存在")
    if path.is_dir():
        return asset_response(rec, path / "index.html", root, token)
    return asset_response(rec, path, root, token)


def asset_response(rec: FileRec, path: Path, root: Path, token: str):
    rel = path.relative_to(files.chat_root(rec.user_id, rec.chat_id)).as_posix()
    # index.html 补全后也再检查符号链接和产物过滤规则。
    try:
        path = files.safe_path(rec.user_id, rec.chat_id, rel)
    except files.SandboxError:
        raise HTTPException(404, "资源不存在")
    if not is_output_file(rec.user_id, rec.chat_id, rel) or not path.is_file():
        raise HTTPException(404, "资源不存在")
    suffix = path.suffix.lower()
    media = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".html": "text/html", ".htm": "text/html"}.get(suffix)
    if media:
        content = rewrite_assets(path.read_text(encoding="utf-8", errors="replace"), suffix, root, f"/api/preview/{token}/", backend_config(rec))
        return Response(content, media_type=media, headers=HEADERS)
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream", headers=HEADERS)

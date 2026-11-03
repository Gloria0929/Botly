"""文件与产物。"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.sandbox import files as sandbox
from app.core.db import get_db
from app.core.deps import current_user, owned_or_404
from app.core.events import emit
from app.core.models import CubeConnection, CubeSession, FileRec, User
from app.sandbox.artifacts import is_artifact_file

router = APIRouter(prefix="/files", tags=["files"])

PREVIEW_EXT = {".md", ".txt", ".json", ".csv", ".py", ".js", ".ts", ".html", ".yaml", ".yml", ".log"}
PREVIEW_MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".avif": "image/avif", ".pdf": "application/pdf"}


@router.get("/{file_id}/preview")
def preview(file_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rec = owned_or_404(db, FileRec, file_id, user.id)
    media = PREVIEW_MEDIA.get(Path(rec.path).suffix.lower())
    if not media:
        raise HTTPException(400, "该类型不支持预览，请下载")
    try:
        path = sandbox.safe_path(rec.user_id, rec.chat_id, rec.path)
    except sandbox.SandboxError:
        raise HTTPException(404, "文件不存在")
    if not path.is_file():
        raise HTTPException(404, "文件已不存在")
    return FileResponse(path, media_type=media, headers={"X-Content-Type-Options": "nosniff"})


@router.get("/{file_id}/content", response_class=PlainTextResponse)
def file_content(file_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rec = owned_or_404(db, FileRec, file_id, user.id)
    if Path(rec.path).suffix.lower() not in PREVIEW_EXT:
        raise HTTPException(400, "该类型不支持在线预览，请下载")
    try:
        return sandbox.read_file(rec.user_id, rec.chat_id, rec.path)
    except sandbox.SandboxError as exc:
        raise HTTPException(404, str(exc))


@router.get("/{file_id}/download")
def download(file_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rec = owned_or_404(db, FileRec, file_id, user.id)
    try:
        path = sandbox.safe_path(rec.user_id, rec.chat_id, rec.path)
    except sandbox.SandboxError as exc:
        raise HTTPException(404, str(exc))
    if not path.exists():
        raise HTTPException(404, "文件已不存在")
    return FileResponse(path, filename=Path(rec.path).name)


@router.delete("/{file_id}")
async def delete_artifact(file_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from app.sandbox.cube import chat_lock, delete_remote_file, fingerprint, safe_error

    rec = owned_or_404(db, FileRec, file_id, user.id)
    user_id, chat_id = user.id, rec.chat_id
    # 等待同步锁和连接远端前结束读事务，避免 SQLite 快照与 Worker 写入冲突。
    db.commit()
    async with chat_lock(user_id, chat_id):
        db.expire_all()
        rec = owned_or_404(db, FileRec, file_id, user_id)
        path = rec.path
        if not rec.created_by_bot_id or not is_artifact_file(user_id, chat_id, path):
            raise HTTPException(400, "仅支持删除 Agent 产物")
        try:
            local = sandbox.safe_path(user_id, chat_id, path)
            if local.exists() and not local.is_file():
                raise sandbox.SandboxError("目标已不是文件，请在终端中处理")
        except sandbox.SandboxError as exc:
            raise HTTPException(400, str(exc))

        session = db.scalar(select(CubeSession).where(CubeSession.user_id == user_id, CubeSession.chat_id == chat_id))
        if session:
            connection = db.scalar(select(CubeConnection).where(CubeConnection.user_id == user_id))
            if not connection or session.connection_hash != fingerprint(connection):
                raise HTTPException(409, "此产物所属的 Cube 配置已变更，请恢复原连接后删除")
            sandbox_id = session.sandbox_id
            db.commit()
            try:
                await asyncio.to_thread(delete_remote_file, connection, sandbox_id, path)
            except Exception as exc:
                raise HTTPException(502, f"Cube 文件删除失败：{safe_error(exc, connection)}")
            db.expire_all()

        try:
            sandbox.delete_file(user_id, chat_id, path)
        except (sandbox.SandboxError, OSError) as exc:
            raise HTTPException(400, f"本地文件删除失败：{exc}")
        db.execute(delete(FileRec).where(FileRec.user_id == user_id, FileRec.chat_id == chat_id, FileRec.path == path))
        session = db.scalar(select(CubeSession).where(CubeSession.user_id == user_id, CubeSession.chat_id == chat_id))
        if session:
            manifest = dict(session.files_manifest or {})
            manifest.pop(path, None)
            session.files_manifest = manifest
        emit(db, user_id, chat_id, "files.updated", {"chat_id": chat_id})
        # 锁释放前提交，后续同步只能看到删除后的文件及清单。
        db.commit()
    return {"ok": True}

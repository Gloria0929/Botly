"""文件与产物。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy.orm import Session

from .. import sandbox
from ..db import get_db
from ..deps import current_user, owned_or_404
from ..models import FileRec, User

router = APIRouter(prefix="/files", tags=["files"])

PREVIEW_EXT = {".md", ".txt", ".json", ".csv", ".py", ".js", ".ts", ".html", ".yaml", ".yml", ".log"}


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

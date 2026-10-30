"""图片上传：Bot 头像、厂商标识。

文件落在 data/uploads/{user_id}/ 下，通过带鉴权的 /api/uploads/{id} 读取，
因此不同账号之间互相看不到（ACC-05）。
"""
from __future__ import annotations


from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..config import DATA_DIR
from ..db import get_db
from ..deps import current_user, owned_or_404
from ..models import Upload, User, uid

router = APIRouter(prefix="/uploads", tags=["uploads"])

MAX_BYTES = 2 * 1024 * 1024
UPLOAD_DIR = DATA_DIR / "uploads"


def sniff_image(raw: bytes) -> str | None:
    """按魔数判断图片类型（Python 3.13 已移除 imghdr）。"""
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    return None


def _path(user_id: str, filename: str):
    d = UPLOAD_DIR / user_id
    d.mkdir(parents=True, exist_ok=True)
    return d / filename


@router.post("", status_code=201)
async def upload_image(
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    raw = await file.read()
    if len(raw) > MAX_BYTES:
        raise HTTPException(400, "图片不能超过 2 MB")
    if not raw:
        raise HTTPException(400, "文件为空")

    kind = sniff_image(raw)
    if not kind:
        raise HTTPException(400, "只支持 PNG / JPEG / GIF / WebP")

    name = f"{uid()}.{'jpg' if kind == 'jpeg' else kind}"
    path = _path(user.id, name)
    path.write_bytes(raw)

    rec = Upload(
        user_id=user.id,
        filename=name,
        mime=f"image/{kind}",
        size=len(raw),
    )
    db.add(rec)
    db.flush()
    return {"id": rec.id, "url": f"/api/uploads/{rec.id}"}


@router.get("/{upload_id}")
def get_image(
    upload_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    rec = owned_or_404(db, Upload, upload_id, user.id)
    path = _path(rec.user_id, rec.filename)
    if not path.exists():
        raise HTTPException(404, "图片不存在")
    return FileResponse(
        path,
        media_type=rec.mime,
        headers={"Cache-Control": "private, max-age=86400"},
    )

"""技能管理：可复用的提示词 + 工具包，支持导入外部技能包。"""
from __future__ import annotations

import mimetypes

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.tools import skills as skillmod
from app.core.db import get_db
from app.core.deps import current_user, owned_or_404
from app.core.logging_setup import get_logger
from app.core.models import Bot, Skill, User

router = APIRouter(prefix="/skills", tags=["skills"])
log = get_logger("skills")


class SkillIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = ""
    prompt: str = ""
    tools: list[str] = []
    enabled: bool = True


class SkillPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    description: str | None = None
    prompt: str | None = None
    tools: list[str] | None = None
    enabled: bool | None = None


class SkillUrlIn(BaseModel):
    url: str = Field(min_length=1, max_length=500)


class SkillClawhubIn(BaseModel):
    slug: str = Field(min_length=1, max_length=200)


class SkillGithubIn(BaseModel):
    repo: str = Field(min_length=1, max_length=300)


def _out(s: Skill) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "prompt": s.prompt,
        "tools": s.tools or [],
        "enabled": s.enabled,
        "source": s.source or "manual",
        "source_url": s.source_url,
        "files": s.files or [],
        "sandbox_path": skillmod.sandbox_path(s),
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


def _validate(db: Session, user_id: str, payload: dict) -> None:
    allowed = skillmod.valid_tool_names(db, user_id)
    unknown = [t for t in (payload.get("tools") or []) if t not in allowed]
    if unknown:
        raise HTTPException(400, f"未知工具：{'、'.join(unknown)}")
    name = payload.get("name")
    if name:
        q = select(Skill).where(Skill.user_id == user_id, Skill.name == name)
        if payload.get("id"):
            q = q.where(Skill.id != payload["id"])
        if db.scalar(q):
            raise HTTPException(400, f"已存在名为「{name}」的技能")


def _unique_name(db: Session, user_id: str, name: str) -> str:
    base = (name or "外部技能").strip()[:55] or "外部技能"
    candidate = base
    i = 2
    while db.scalar(select(Skill).where(Skill.user_id == user_id, Skill.name == candidate)):
        candidate = f"{base} {i}"
        i += 1
    return candidate


async def _installation(user_id: str) -> dict:
    from app.services.skill_installation import sync_user
    try:
        return await sync_user(user_id)
    except Exception as exc:
        log.exception("技能沙箱安装失败")
        return {"provider": "none", "synced": 0, "pending": True,
                "errors": [{"message": f"沙箱安装失败：{type(exc).__name__}"}]}


async def _create_from_package(db: Session, user: User, pkg: skillmod.SkillPackage) -> dict:
    allowed = skillmod.valid_tool_names(db, user.id)
    skill = Skill(
        user_id=user.id,
        name=_unique_name(db, user.id, pkg.name),
        description=(pkg.description or "")[:200],
        prompt=pkg.prompt or "",
        tools=[t for t in pkg.tools if t in allowed],
        source=pkg.source,
        source_url=pkg.source_url,
    )
    db.add(skill)
    db.flush()
    try:
        skill.files = skillmod.save_package(user.id, skill.id, pkg.files)
    except skillmod.SkillPackageError as exc:
        raise HTTPException(400, str(exc))
    db.flush()
    result = _out(skill)
    # 先提交技能源文件，再连接远端，避免 SQLite 写锁与沙箱同步互相等待。
    db.commit()
    result["installation"] = await _installation(user.id)
    return result


@router.post("/import", status_code=201)
async def import_skill_file(
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """上传外部技能包：.zip（含 SKILL.md）或单个 .md 文件。"""
    raw = await file.read()
    try:
        pkg = skillmod.parse_package(raw, file.filename or "SKILL.md")
    except skillmod.SkillPackageError as exc:
        raise HTTPException(400, str(exc))
    return await _create_from_package(db, user, pkg)


@router.post("/import-url", status_code=201)
async def import_skill_url(
    payload: SkillUrlIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """从 URL 导入：SKILL.md 直链、GitHub 仓库链接或 ZIP 地址。"""
    try:
        pkg = await skillmod.fetch_package(payload.url)
    except skillmod.SkillPackageError as exc:
        raise HTTPException(400, str(exc))
    return await _create_from_package(db, user, pkg)


@router.post("/import-clawhub", status_code=201)
async def import_skill_clawhub(
    payload: SkillClawhubIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """按技能名（slug）从 ClawHub 下载并导入。"""
    try:
        pkg = await skillmod.fetch_clawhub(payload.slug)
    except skillmod.SkillPackageError as exc:
        raise HTTPException(400, str(exc))
    return await _create_from_package(db, user, pkg)


@router.post("/import-github", status_code=201)
async def import_skill_github(
    payload: SkillGithubIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """按 GitHub owner/repo（或仓库链接）精准定位并导入。"""
    try:
        pkg = await skillmod.fetch_github(payload.repo)
    except skillmod.SkillPackageError as exc:
        raise HTTPException(400, str(exc))
    return await _create_from_package(db, user, pkg)


@router.get("")
def list_skills(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Skill).where(Skill.user_id == user.id).order_by(Skill.created_at)
    ).all()
    used = {}
    for bot in db.scalars(select(Bot).where(Bot.user_id == user.id)).all():
        for sid in bot.skills or []:
            used[sid] = used.get(sid, 0) + 1
    return [{**_out(s), "bot_count": used.get(s.id, 0)} for s in rows]


@router.post("", status_code=201)
async def create_skill(payload: SkillIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    data = payload.model_dump()
    data["name"] = data["name"].strip()
    _validate(db, user.id, data)
    skill = Skill(user_id=user.id, **data)
    db.add(skill)
    db.flush()
    result = _out(skill)
    db.commit()
    result["installation"] = await _installation(user.id)
    return result


@router.patch("/{skill_id}")
async def update_skill(
    skill_id: str,
    payload: SkillPatch,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    skill = owned_or_404(db, Skill, skill_id, user.id)
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        data["name"] = data["name"].strip()
    _validate(db, user.id, {**data, "id": skill.id})
    for key, value in data.items():
        setattr(skill, key, value)
    db.flush()
    result = _out(skill)
    db.commit()
    result["installation"] = await _installation(user.id)
    return result


@router.get("/{skill_id}/files")
def list_skill_files(
    skill_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    skill = owned_or_404(db, Skill, skill_id, user.id)
    return [{"path": f, "name": str(f).rsplit("/", 1)[-1]} for f in (skill.files or [])]


@router.get("/{skill_id}/files/{path:path}")
def download_skill_file(
    skill_id: str,
    path: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    skill = owned_or_404(db, Skill, skill_id, user.id)
    if path not in (skill.files or []):
        raise HTTPException(404, "技能包里没有这个文件")
    try:
        target = skillmod.file_abs_path(user.id, skill.id, path)
    except skillmod.SkillPackageError as exc:
        raise HTTPException(404, str(exc))
    media = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return FileResponse(target, media_type=media, filename=path.rsplit("/", 1)[-1])


@router.delete("/{skill_id}")
async def delete_skill(
    skill_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    skill = owned_or_404(db, Skill, skill_id, user.id)
    for bot in db.scalars(select(Bot).where(Bot.user_id == user.id)).all():
        kept = [sid for sid in (bot.skills or []) if sid != skill.id]
        if kept != (bot.skills or []):
            bot.skills = kept
    skillmod.delete_package(user.id, skill.id)
    db.delete(skill)
    db.flush()
    db.commit()
    return {"ok": True, "installation": await _installation(user.id)}

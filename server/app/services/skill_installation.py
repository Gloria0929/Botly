"""把账号技能库安装到会话沙箱，源文件缓存只用于管理和恢复。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from pathlib import PurePosixPath

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.models import Chat, CubeSession, Skill
from app.sandbox import files
from app.tools import skills


def library(user_id: str):
    with SessionLocal() as db:
        rows = db.scalars(select(Skill).where(Skill.user_id == user_id).order_by(Skill.created_at)).all()
    return [(row.id, skills.sandbox_path(row), skills.sandbox_files(row)) for row in rows]


def managed_directory(skill_id: str, path: str) -> str:
    target = PurePosixPath(path)
    if target.parent != PurePosixPath(skills.SANDBOX_SKILL_ROOT) or not target.name.endswith("-" + skill_id):
        raise ValueError("技能安装目录不属于当前技能")
    return str(target.relative_to("/workspace"))


def _sync_local_skills(user_id: str, chat_id: str) -> None:
    """Docker 使用同一个挂载工作区；技能目录不登记为产物。"""
    manifest_path = files.safe_path(user_id, chat_id, ".botly/skills-manifest.json")
    previous = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    current = {}
    for skill_id, path, bundle in library(user_id):
        relative = managed_directory(skill_id, path)
        old = previous.get(skill_id, {})
        digests = {rel: hashlib.sha256(data).hexdigest() for rel, data in bundle.items()}
        current[skill_id] = {"path": path, "files": digests}
        for rel, data in bundle.items():
            target = files.safe_path(user_id, chat_id, f"{relative}/{rel}")
            if old.get("path") != path or old.get("files", {}).get(rel) != digests[rel] or not target.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        if old.get("path") == path:
            for rel in old.get("files", {}).keys() - bundle.keys():
                if skills._safe_rel(rel) != rel:
                    raise ValueError("非法技能文件路径")
                files.delete_file(user_id, chat_id, f"{relative}/{rel}")
    for skill_id, old in previous.items():
        if current.get(skill_id, {}).get("path") != old["path"]:
            relative = managed_directory(skill_id, old["path"])
            target = files.safe_path(user_id, chat_id, relative)
            if target.is_dir():
                shutil.rmtree(target)
    if current != previous:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")


async def sync_local_skills(user_id: str, chat_id: str) -> None:
    from app.sandbox.cube import chat_lock
    async with chat_lock(user_id, chat_id):
        task = asyncio.create_task(asyncio.to_thread(_sync_local_skills, user_id, chat_id))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.gather(task, return_exceptions=True)
            raise


async def sync_user(user_id: str) -> dict:
    """更新已有沙箱；尚未创建的会话由首次打开沙箱时安装。"""
    from app.sandbox import runtime
    from app.sandbox.cube import CubeBackend, fingerprint, safe_error

    driver = runtime.user_backend(user_id)
    result = {"provider": "none", "synced": 0, "pending": True, "errors": []}
    chat_ids = []
    if driver is None:
        return result
    if isinstance(driver, CubeBackend):
        result["provider"] = "cube"
        with SessionLocal() as db:
            chat_ids = list(db.scalars(select(CubeSession.chat_id).where(
                CubeSession.user_id == user_id,
                CubeSession.connection_hash == fingerprint(driver.connection),
            )))
        limit = asyncio.Semaphore(3)

        async def install(chat_id):
            async with limit:
                try:
                    return await driver.sync_skills(user_id, chat_id), None
                except Exception as exc:
                    return False, {"chat_id": chat_id, "message": safe_error(exc, driver.connection)}

        for installed, error in await asyncio.gather(*(install(chat_id) for chat_id in chat_ids)):
            result["synced"] += int(installed)
            if error:
                result["errors"].append(error)
    elif isinstance(driver, runtime.DockerBackend):
        result["provider"] = "docker"
        with SessionLocal() as db:
            chat_ids = list(db.scalars(select(Chat.id).where(Chat.user_id == user_id)))
        for chat_id in chat_ids:
            try:
                await sync_local_skills(user_id, chat_id)
                result["synced"] += 1
            except Exception as exc:
                result["errors"].append({"chat_id": chat_id, "message": str(exc)})
    result["pending"] = not result["synced"] or result["synced"] < len(chat_ids) or bool(result["errors"])
    return result

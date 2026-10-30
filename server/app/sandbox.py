"""沙箱文件系统：每个用户独立目录，同一用户的不同群聊各自子目录。"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from .config import SANDBOX_DIR, SANDBOX_FILE_MAX_MB, SANDBOX_QUOTA_MB


class SandboxError(Exception):
    pass


def chat_root(user_id: str, chat_id: str) -> Path:
    root = (SANDBOX_DIR / user_id / chat_id).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_path(user_id: str, chat_id: str, rel: str) -> Path:
    """禁止路径穿越与符号链接逃逸。只接受相对路径。"""
    root = chat_root(user_id, chat_id)
    raw = (rel or "").strip()
    if not raw:
        raise SandboxError("路径不能为空")
    if raw.startswith("/") or raw.startswith("~") or (len(raw) > 1 and raw[1] == ":"):
        raise SandboxError("只接受相对于沙箱根目录的路径，例如 research.md")
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise SandboxError("路径越界：只能访问当前群聊的沙箱目录")
    # 逐级检查符号链接
    cur = root
    for part in Path(rel).parts:
        cur = cur / part
        if cur.exists() and cur.is_symlink():
            raise SandboxError("不允许访问符号链接")
    return candidate


def quota_used(user_id: str, chat_id: str | None = None) -> int:
    root = chat_root(user_id, chat_id) if chat_id else (SANDBOX_DIR / user_id).resolve()
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def write_file(user_id: str, chat_id: str, rel: str, content: str) -> int:
    return write_bytes(user_id, chat_id, rel, content.encode("utf-8"))


def write_bytes(user_id: str, chat_id: str, rel: str, content: bytes) -> int:
    path = safe_path(user_id, chat_id, rel)
    size = len(content)
    if size > SANDBOX_FILE_MAX_MB * 1024 * 1024:
        raise SandboxError(f"单文件超过 {SANDBOX_FILE_MAX_MB} MB 限制")
    used = quota_used(user_id)
    previous_size = path.stat().st_size if path.is_file() else 0
    if used - previous_size + size > SANDBOX_QUOTA_MB * 1024 * 1024:
        raise SandboxError(f"沙箱容量超过 {SANDBOX_QUOTA_MB} MB 限制")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return size


def read_file(user_id: str, chat_id: str, rel: str) -> str:
    path = safe_path(user_id, chat_id, rel)
    if not path.exists() or not path.is_file():
        raise SandboxError(f"文件不存在：{rel}")
    if path.stat().st_size > 2 * 1024 * 1024:
        raise SandboxError("文件过大，无法直接读取")
    return path.read_text(encoding="utf-8", errors="replace")


def list_files(user_id: str, chat_id: str) -> list[dict]:
    root = chat_root(user_id, chat_id)
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = Path(dirpath) / f
            out.append(
                {
                    "path": str(p.relative_to(root)),
                    "size": p.stat().st_size,
                    "updated_at": p.stat().st_mtime,
                }
            )
    return sorted(out, key=lambda x: x["path"])


def delete_chat_dir(user_id: str, chat_id: str) -> None:
    """解散群聊时清空该群聊的沙箱目录（需求 8.5）。"""
    base = (SANDBOX_DIR / user_id).resolve()
    root = (base / chat_id).resolve()
    if base not in root.parents:
        raise SandboxError("非法的群聊目录")
    shutil.rmtree(root, ignore_errors=True)


def delete_file(user_id: str, chat_id: str, rel: str) -> None:
    path = safe_path(user_id, chat_id, rel)
    if not path.exists():
        return  # 相同删除调用在检查点恢复时重放，目标已不存在即视为完成。
    path.unlink()

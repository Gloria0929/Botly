"""交付产物过滤：环境文件仍可在沙箱中访问，不进入下载面板。"""
from pathlib import PurePosixPath

INTERNAL_DIRS = {
    "skills", "skill", "node_modules", "venv", "env", "site-packages", "dist-packages",
    "__pycache__", ".git", ".svn", ".hg", ".venv", ".env", ".agents", ".codex", ".claude",
    ".cache", ".npm", ".pnpm-store", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".next",
    ".botly", ".local", ".config", ".ssh", ".idea", ".vscode",
}
INTERNAL_NAMES = {"skill.md", "agents.md", "pyvenv.cfg", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", ".ds_store"}


def is_artifact_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if not parts or ".." in parts or PurePosixPath(path).is_absolute():
        return False
    if any(part.lower() in INTERNAL_DIRS or part.startswith(".") for part in parts):
        return False
    return parts[-1].lower() not in INTERNAL_NAMES and not parts[-1].lower().endswith((".pyc", ".pyo"))


def is_skill_directory(names) -> bool:
    return any(name.lower() in ("skill.md", "pyvenv.cfg") for name in names)


def is_artifact_file(user_id: str, chat_id: str, path: str) -> bool:
    if not is_artifact_path(path):
        return False
    from app.sandbox.files import chat_root
    root = chat_root(user_id, chat_id)
    current = root
    for part in PurePosixPath(path).parts[:-1]:
        current = current / part
        if current.is_dir() and is_skill_directory(p.name for p in current.iterdir()):
            return False
    return True


def visible_files(rows):
    return [row for row in rows if row.created_by_bot_id and is_artifact_file(row.user_id, row.chat_id, row.path)]

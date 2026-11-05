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
    return [row for row in rows if is_output_file(row.user_id, row.chat_id, row.path)]


def is_output_file(user_id: str, chat_id: str, path: str) -> bool:
    parts = PurePosixPath(path).parts
    if len(parts) < 2 or parts[0] != "output":
        return False
    return is_artifact_file(user_id, chat_id, path)


def in_execution_scope(path: str, workspace: str) -> bool:
    from app.sandbox.workspace import is_task_path, output_directory
    if not is_task_path(path):
        return True
    own = output_directory(workspace)
    legacy = own.removeprefix("output/")
    return own != "output" and (path.startswith(own + "/") or path.startswith(legacy + "/"))


def collect_generated_files(user_id: str, chat_id: str, workspace: str, before: dict, after: dict) -> dict[str, bytes]:
    """脚本显式写到旧目录时，将本次新增/修改的产物归档，不搬走其工作文件。"""
    from app.sandbox import files
    from app.sandbox.workspace import output_directory
    output = output_directory(workspace)
    legacy = output.removeprefix("output/")
    collected = {}
    for path, digest in after.items():
        if path.startswith("output/") or before.get(path) == digest or not in_execution_scope(path, workspace):
            continue
        relative = path.removeprefix(legacy + "/") if legacy != "output" else path
        target = f"{output}/{relative}"
        # 显式写入 output 的同名交付文件优先，不被工作区的中间版本覆盖。
        if target in after and before.get(target) != after[target]:
            continue
        data = files.safe_path(user_id, chat_id, path).read_bytes()
        files.write_bytes(user_id, chat_id, target, data)
        collected[target] = data
    return collected

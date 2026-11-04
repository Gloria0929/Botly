"""群聊的常驻 Linux 环境：一个群聊一个 Docker 容器（Ubuntu，root + 联网，可装包）。

这里同时承载两个工具，容器是同一个：
- `run_command`：执行 shell 命令，L3（默认审批，可按群聊设为始终允许）
- `code_exec`：执行一段 python / node / bash / sh 代码，代码走 stdin

工作区挂载为群聊沙箱目录（`sandbox.chat_root`），因此容器里 `/workspace` 与
`file_write / file_read` 操作的是同一份文件，命令产出的文件也能用文件工具读回。

建群聊时就会后台预热（`warm_container`），此后 `docker exec` 复用；万一预热没赶上，
第一次用到时 `ensure_container` 也会兜底创建。解散群聊时 `stop_container` 删除。
"""
from __future__ import annotations

import asyncio

from app.sandbox import files as sandbox
from app.core.config import (
    CODE_SANDBOX_ENABLED,
    CODE_SANDBOX_MAX_OUTPUT,
    CODE_SANDBOX_TIMEOUT,
    LINUX_SANDBOX_AUTO_INSTALL,
    LINUX_SANDBOX_BASELINE_IMAGE,
    LINUX_SANDBOX_CPUS,
    LINUX_SANDBOX_ENABLED,
    LINUX_SANDBOX_IMAGE,
    LINUX_SANDBOX_MAX_OUTPUT,
    LINUX_SANDBOX_MEMORY,
    LINUX_SANDBOX_TIMEOUT,
)
from app.core.logging_setup import get_logger
from app.tools.registry import UNTRUSTED_PREFIX

log = get_logger("sandbox")

# 首次装包可能要几分钟（apt update + 下载），单独给足时间
_INSTALL_TIMEOUT = 300

# language -> (可执行文件, 参数, 缺包时要装的 apt 包)
_RUNNERS: dict[str, tuple[str, list[str], str | None]] = {
    "python": ("python3", ["-"], "python3"),
    "node": ("node", ["-"], "nodejs"),
    "bash": ("bash", ["-s"], None),
    "sh": ("sh", ["-s"], None),
}


class LinuxSandboxError(Exception):
    pass


# 每个容器一把锁：串行创建/启动，避免并发 docker run 撞名
_locks: dict[str, asyncio.Lock] = {}


def container_name(chat_id: str) -> str:
    return f"botly-linux-{chat_id}"


async def _kill(proc) -> None:
    """超时/取消时把 docker 客户端进程杀掉，否则容器里会留下游离进程。"""
    try:
        proc.kill()
        await proc.wait()
    except Exception:  # noqa: BLE001
        pass


async def _docker(*args: str, timeout: int = 30, stdin: bytes | None = None) -> tuple[str, str, int]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        raise LinuxSandboxError("未找到 Docker。请先安装 Docker，并确认 docker 命令可用。")
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout)
    except asyncio.TimeoutError:
        await _kill(proc)
        raise LinuxSandboxError(f"Docker 命令超时（{timeout} 秒）")
    except asyncio.CancelledError:
        # 上层工具超时会取消协程；这里必须杀掉子进程再抛，避免容器里进程泄漏
        await _kill(proc)
        raise
    return out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace"), proc.returncode


async def _state(name: str) -> str | None:
    out, _, code = await _docker("inspect", "-f", "{{.State.Running}}", name)
    if code != 0:
        return None
    return "running" if out.strip() == "true" else "stopped"


async def ensure_container(user_id: str, chat_id: str) -> str:
    """确保容器在跑：不存在就创建，存在但停了就拉起。"""
    name = container_name(chat_id)
    # 同一个群聊串行创建：建群预热和第一次工具调用可能同时触发，
    # 并发 docker run 会撞 "container name already in use"。
    async with _lock_for(name):
        return await _ensure_container(user_id, chat_id, name)


def _lock_for(name: str) -> asyncio.Lock:
    lock = _locks.get(name)
    if lock is None:
        lock = asyncio.Lock()
        _locks[name] = lock
    return lock


async def _run_container(name: str, workdir: str, image: str) -> tuple[int, str]:
    _, err, code = await _docker(
        "run", "-d", "--name", name,
        "--init",  # tini 当 1 号进程，回收 docker exec 留下的僵尸
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--memory", LINUX_SANDBOX_MEMORY,
        "--cpus", LINUX_SANDBOX_CPUS,
        "--pids-limit", "512",
        "-v", f"{workdir}:/workspace",
        "-w", "/workspace",
        image, "sleep", "infinity",
        timeout=300,  # 首次创建可能要现场拉镜像，给足时间
    )
    return code, err


def _image_missing(err: str) -> bool:
    """docker run 的报错是不是「镜像不存在」。"""
    low = (err or "").lower()
    return any(
        s in low
        for s in (
            "pull access denied",
            "repository does not exist",
            "unable to find image",
            "manifest unknown",
            "no such image",
        )
    )


async def _ensure_container(user_id: str, chat_id: str, name: str) -> str:
    state = await _state(name)
    if state == "running":
        return name
    if state == "stopped":
        _, _, code = await _docker("start", name)
        if code == 0:
            return name
        # start 失败（容器损坏）：删掉重建
        await _docker("rm", "-f", name)

    workdir = str(sandbox.chat_root(user_id, chat_id))
    image = LINUX_SANDBOX_IMAGE
    code, err = await _run_container(name, workdir, image)
    if code != 0 and _image_missing(err) and image != LINUX_SANDBOX_BASELINE_IMAGE:
        # 预装镜像没构建（见 deploy/sandbox.Dockerfile）：退回基础镜像，功能不受影响，
        # 只是 python/node 第一次用到时要现场 apt 装。
        log.warning(
            "沙箱镜像 %s 不存在，回退到 %s（可先跑 scripts/build_sandbox.sh 预装常用工具）",
            image, LINUX_SANDBOX_BASELINE_IMAGE,
        )
        image = LINUX_SANDBOX_BASELINE_IMAGE
        code, err = await _run_container(name, workdir, image)
    if code != 0:
        # API 和 Worker 是两个进程，进程内的锁管不到对方：谁先建成用谁的
        if "already in use" in err or "Conflict" in err:
            state = await _state(name)
            if state == "running":
                return name
            if state == "stopped":
                _, _, code = await _docker("start", name)
                if code == 0:
                    return name
        raise LinuxSandboxError(
            f"启动 Linux 环境失败：{err.strip()[:300]}"
            f"（确认已安装 Docker，镜像 {image} 可拉取）"
        )
    log.info("沙箱容器已创建 chat=%s name=%s image=%s", chat_id, name, image)
    return name


async def warm_container(user_id: str, chat_id: str) -> None:
    """建群聊时后台预热容器；失败只记日志，不能影响建群本身。"""
    if not LINUX_SANDBOX_ENABLED:
        return
    try:
        await ensure_container(user_id, chat_id)
    except Exception as exc:  # noqa: BLE001 - 预热失败仍可懒加载重试
        log.warning("群聊沙箱预热失败 chat=%s err=%s", chat_id, exc)


def _format(out: bytes | str, err: bytes | str, code: int, limit: int) -> str:
    stdout = out.decode("utf-8", errors="replace") if isinstance(out, bytes) else out
    stderr = err.decode("utf-8", errors="replace") if isinstance(err, bytes) else err
    combined = "\n".join(x for x in [stdout.strip(), stderr.strip()] if x).strip()
    if len(combined) > limit:
        combined = combined[:limit] + "\n\n[输出已截断]"
    header = f"退出码 {code}。\n" if code != 0 else ""
    return UNTRUSTED_PREFIX + header + (combined or "（无输出）")


async def run_command(user_id: str, chat_id: str, command: str, *, workspace: str = "") -> str:
    if not LINUX_SANDBOX_ENABLED:
        return "Linux 运行环境未启用（管理员未开启 BOTLY_LINUX_SANDBOX_ENABLED）。"
    command = (command or "").strip()
    if not command:
        return "命令为空。"
    try:
        name = await ensure_container(user_id, chat_id)
        workdir = "/workspace"
        if workspace:
            from app.sandbox import files
            local = files.safe_path(user_id, chat_id, workspace)
            local.mkdir(parents=True, exist_ok=True)
            workdir += "/" + local.relative_to(files.chat_root(user_id, chat_id)).as_posix()
        # 容器内自负其责地限时：即使这边把 docker 客户端杀掉，容器里的
        # `timeout` 仍会在 N 秒后 KILL 掉命令，不会留下跑飞的进程。
        out, err, code = await _docker(
            "exec", "-e", "TERM=xterm-256color", "-e", "LANG=C.UTF-8", "-e", "LC_ALL=C.UTF-8", "-w", workdir, name,
            "timeout", "-s", "KILL", str(LINUX_SANDBOX_TIMEOUT), "bash", "-lc", command,
            timeout=LINUX_SANDBOX_TIMEOUT + 15,
        )
    except LinuxSandboxError as exc:
        return f"命令执行失败：{exc}"
    if code in (124, 137):
        return f"命令执行超时（{LINUX_SANDBOX_TIMEOUT} 秒），已终止。"
    return _format(out, err, code, LINUX_SANDBOX_MAX_OUTPUT)


async def _has_binary(name: str, binary: str) -> bool:
    try:
        _, _, code = await _docker("exec", name, "bash", "-lc", f"command -v {binary} >/dev/null 2>&1")
    except LinuxSandboxError:
        return False
    return code == 0


async def _install(name: str, package: str) -> str:
    """容器里缺解释器时补装（一次装好，容器在就一直有效）。返回空串表示成功。"""
    if not LINUX_SANDBOX_AUTO_INSTALL:
        return f"环境里没有 {package}。可以用 run_command 安装：apt-get update && apt-get install -y {package}"
    log.info("沙箱补装依赖 package=%s", package)
    cmd = (
        "apt-get update -qq && "
        f"DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends {package}"
    )
    try:
        _, err, code = await _docker(
            "exec", name,
            "timeout", "-s", "KILL", str(_INSTALL_TIMEOUT - 20), "bash", "-lc", cmd,
            timeout=_INSTALL_TIMEOUT,
        )
    except LinuxSandboxError as exc:
        return f"安装 {package} 失败：{exc}"
    if code != 0:
        return (
            f"安装 {package} 失败：{err.strip()[:300]}"
            f"（容器需要联网；也可以用 run_command 手动安装）"
        )
    return ""


async def run_code(user_id: str, chat_id: str, code: str, language: str = "python", *, workspace: str = "") -> str:
    """在群聊的 Ubuntu 容器里跑一段代码，代码从 stdin 喂给解释器。"""
    if not CODE_SANDBOX_ENABLED:
        return "代码执行未启用（管理员未开启 BOTLY_CODE_SANDBOX_ENABLED）。"
    if not LINUX_SANDBOX_ENABLED:
        return "Linux 运行环境未启用（管理员未开启 BOTLY_LINUX_SANDBOX_ENABLED）。"
    runner = _RUNNERS.get((language or "python").lower())
    if not runner:
        return f"不支持的语言：{language}。可选：{'、'.join(_RUNNERS)}。"
    if not (code or "").strip():
        return "代码为空，请提供要执行的代码。"
    binary, argv, package = runner

    try:
        name = await ensure_container(user_id, chat_id)
        workdir = "/workspace"
        if workspace:
            from app.sandbox import files
            local = files.safe_path(user_id, chat_id, workspace)
            local.mkdir(parents=True, exist_ok=True)
            workdir += "/" + local.relative_to(files.chat_root(user_id, chat_id)).as_posix()
        if not await _has_binary(name, binary):
            if not package:
                return f"环境里没有 {binary}。"
            failure = await _install(name, package)
            if failure:
                return failure
        out, err, code_ret = await _docker(
            "exec", "-i", "-e", "TERM=xterm-256color", "-e", "LANG=C.UTF-8", "-e", "LC_ALL=C.UTF-8", "-w", workdir, name,
            "timeout", "-s", "KILL", str(CODE_SANDBOX_TIMEOUT), binary, *argv,
            stdin=code.encode("utf-8"),
            timeout=CODE_SANDBOX_TIMEOUT + 15,
        )
    except LinuxSandboxError as exc:
        msg = str(exc)
        if "超时" in msg:
            return f"代码执行超时（{CODE_SANDBOX_TIMEOUT} 秒），已终止。"
        return f"代码执行失败：{msg}"

    if code_ret in (124, 137):
        return f"代码执行超时（{CODE_SANDBOX_TIMEOUT} 秒），已终止。"
    return _format(out, err, code_ret, min(CODE_SANDBOX_MAX_OUTPUT, LINUX_SANDBOX_MAX_OUTPUT))


async def stop_container(chat_id: str) -> None:
    """解散群聊 / 清理时停掉并删除对应容器。"""
    name = container_name(chat_id)
    await _docker("rm", "-f", name)


def stop_container_sync(chat_id: str) -> None:
    """同步版清理（供同步 API 在解散群聊时调用，best-effort）。"""
    import subprocess

    try:
        subprocess.run(
            ["docker", "rm", "-f", container_name(chat_id)],
            capture_output=True,
            timeout=30,
        )
    except Exception:  # noqa: BLE001 - 清理失败不影响解散
        pass

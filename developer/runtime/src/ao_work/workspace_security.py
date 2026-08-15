from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult

GitRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]
MAX_OUTBOUND_FILE_BYTES = 1024 * 1024
SECRET_FILE_NAME_PATTERN = re.compile(
    r"(?:^|[._-])(env|secret|secrets|credential|credentials|password|passwd|token|"
    r"private[_-]?key|id_rsa|id_ed25519)(?:$|[._-])",
    re.IGNORECASE,
)
SECRET_FILE_SUFFIXES = frozenset({".key", ".pem", ".p12", ".pfx", ".jks", ".keystore"})
SECRET_CONTENT_PATTERN = re.compile(
    r"(?im)^(?:export\s+)?[A-Z0-9_]*(?:TOKEN|PASSWORD|PASSWD|SECRET|PRIVATE_KEY|"
    r"API_KEY|ACCESS_KEY)[A-Z0-9_]*\s*[:=]\s*\S+"
)
PRIVATE_KEY_PATTERN = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
TOKEN_FAMILY_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"gh[pousr]_[A-Za-z0-9]{30,255}|github_pat_[A-Za-z0-9_]{50,255}|"
    r"glpat-[A-Za-z0-9_-]{20,255}|ATATT3xFfGF0[A-Za-z0-9_-]{20,255}|"
    r"AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|"
    r"sk_(?:live|test)_[A-Za-z0-9]{16,255}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,255}"
    r")(?![A-Za-z0-9])"
)


def _atomic_write_text(path: Path, content: str) -> None:
    """Write Git metadata without importing the eager task_state package."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            if content and not content.endswith("\n"):
                stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def validate_workspace_state_root(workspace_root: Path) -> Path:
    """Return the managed state path only when it is a real directory path."""
    root = workspace_root.expanduser().resolve()
    state_root = root / ".agentic-ops"
    if state_root.is_symlink():
        raise _blocked(
            "workspace_state_symlink_forbidden",
            "业务项目工作空间的 .agentic-ops 不能是符号链接",
            "请移除符号链接，并在当前工作空间内使用真实的 .agentic-ops 目录",
        )
    try:
        state_root.resolve(strict=False).relative_to(root)
    except (OSError, ValueError) as error:
        raise _blocked(
            "workspace_managed_path_unsafe",
            "业务项目工作空间的 .agentic-ops 越出工作空间",
            "请移除越界路径，并在当前工作空间内重新初始化",
        ) from error
    for name in (
        "agent.json",
        "profiles",
        "connections",
        "runs",
        "audit",
        "feedback",
        "handoff",
        "locks",
        "tasks",
    ):
        child = state_root / name
        if child.is_symlink():
            raise _blocked(
                "workspace_managed_path_unsafe",
                f"工作空间受管路径不能是符号链接：{child}",
                "请移除受管路径符号链接，并核对是否发生身份或状态篡改",
            )
    return state_root


def validate_managed_path(
    managed_root: Path,
    path: Path,
    *,
    code: str = "workspace_managed_path_unsafe",
) -> Path:
    """Validate a managed path lexically and physically without following symlinks."""
    root = managed_root.expanduser().absolute()
    candidate = path.expanduser().absolute()
    try:
        relative = candidate.relative_to(root)
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
    except (OSError, ValueError) as error:
        raise _blocked(
            code,
            f"受管路径越出允许目录：{candidate}",
            "请移除越界路径或符号链接，并重新初始化受管配置",
        ) from error
    current = root
    if current.is_symlink():
        raise _blocked(
            code,
            f"受管目录不能是符号链接：{current}",
            "请移除受管目录符号链接，并重新初始化受管配置",
        )
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise _blocked(
                code,
                f"受管路径不能包含符号链接：{current}",
                "请移除受管路径符号链接，并核对是否发生身份或状态篡改",
            )
    return candidate


def validate_workspace_managed_path(workspace_root: Path, path: Path) -> Path:
    root = workspace_root.expanduser().absolute()
    state_root = validate_workspace_state_root(root)
    candidate = validate_managed_path(root, path)
    try:
        candidate.relative_to(state_root)
    except ValueError as error:
        raise _blocked(
            "workspace_managed_path_unsafe",
            f"工作空间受管路径不在 .agentic-ops 中：{candidate}",
            "请只在当前工作空间 .agentic-ops 内读写受管配置与状态",
        ) from error
    return candidate


def validate_workspace_root_file(
    workspace_root: Path,
    path: Path,
    *,
    label: str,
) -> Path:
    root = workspace_root.expanduser().resolve()
    candidate = validate_managed_path(root, path)
    if candidate.exists() and not candidate.is_file():
        raise _blocked(
            "workspace_managed_path_unsafe",
            f"{label} 若存在必须是当前工作空间内的普通文件：{candidate}",
            f"请移除异常的 {label} 路径，并核对是否发生工作空间边界篡改",
        )
    return candidate


def read_workspace_root_file(
    workspace_root: Path,
    path: Path,
    *,
    label: str,
) -> str:
    candidate = validate_workspace_root_file(
        workspace_root,
        path,
        label=label,
    )
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(candidate, flags)
    except FileNotFoundError:
        return ""
    except OSError as error:
        raise _blocked(
            "workspace_managed_path_unsafe",
            f"无法安全读取 {label}：{type(error).__name__}",
            f"请移除异常的 {label} 路径，并核对是否发生工作空间边界篡改",
        ) from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise _blocked(
                "workspace_managed_path_unsafe",
                f"{label} 必须是当前工作空间内的普通文件",
                f"请移除异常的 {label} 路径，并核对是否发生工作空间边界篡改",
            )
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = -1
            return stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def read_workspace_outbound_file(
    workspace_root: Path,
    value: str | Path,
    *,
    label: str,
) -> str:
    """Read user-authored outbound content without exposing managed or Git state."""
    root = workspace_root.expanduser().resolve()
    supplied = Path(value).expanduser()
    candidate = supplied if supplied.is_absolute() else root / supplied
    candidate = candidate.absolute()
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise _blocked(
            "workspace_path_escape",
            f"{label}越出项目工作空间：{value}",
            f"请把{label}放在项目 AI 工作空间的非受管内容目录内",
        ) from error
    if any(part in {".agentic-ops", ".git"} for part in relative.parts):
        raise _blocked(
            "workspace_outbound_file_forbidden",
            f"{label}不能来自 .agentic-ops、.git 或 Git 元数据：{value}",
            f"请把人工编写的{label}放在工作空间普通内容目录内",
        )
    if any(
        part.startswith(".")
        or SECRET_FILE_NAME_PATTERN.search(part)
        or Path(part).suffix.lower() in SECRET_FILE_SUFFIXES
        for part in relative.parts
    ):
        raise _blocked(
            "workspace_outbound_file_forbidden",
            f"{label}路径看起来属于隐藏文件、凭证、密钥或 secret 配置：{value}",
            f"请只使用专门编写的普通{label}，不要直接引用配置或凭证文件",
        )
    validate_managed_path(root, candidate, code="workspace_outbound_file_unsafe")

    try:
        initial = os.stat(candidate, follow_symlinks=False)
    except FileNotFoundError as error:
        raise _blocked(
            "workspace_file_not_found",
            f"{label}不存在：{value}",
            f"请检查{label}路径后重试",
        ) from error
    except OSError as error:
        raise _blocked(
            "workspace_outbound_file_unsafe",
            f"无法安全检查{label}：{type(error).__name__}",
            f"请移除{label}路径中的符号链接或特殊文件后重试",
        ) from error
    if not stat.S_ISREG(initial.st_mode):
        raise _blocked(
            "workspace_outbound_file_unsafe",
            f"{label}必须是工作空间内的普通文件",
            f"请移除符号链接或特殊文件后重试",
        )

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(candidate, flags)
    except FileNotFoundError as error:
        raise _blocked(
            "workspace_file_not_found",
            f"{label}不存在：{value}",
            f"请检查{label}路径后重试",
        ) from error
    except OSError as error:
        raise _blocked(
            "workspace_outbound_file_unsafe",
            f"无法安全读取{label}：{type(error).__name__}",
            f"请移除{label}路径中的符号链接或特殊文件后重试",
        ) from error
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size > MAX_OUTBOUND_FILE_BYTES
        ):
            raise _blocked(
                "workspace_outbound_file_unsafe",
                f"{label}必须是工作空间内不超过 1 MiB 的单链接普通文件",
                f"请移除符号链接、硬链接或特殊文件，并缩小{label}后重试",
            )
        validate_managed_path(root, candidate, code="workspace_outbound_file_unsafe")
        current = os.stat(candidate, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            raise _blocked(
                "workspace_outbound_file_unsafe",
                f"{label}在安全校验期间发生变化",
                f"请停止并核对{label}路径后重试",
            )
        try:
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                descriptor = -1
                content = stream.read()
                if (
                    PRIVATE_KEY_PATTERN.search(content)
                    or SECRET_CONTENT_PATTERN.search(content)
                    or TOKEN_FAMILY_PATTERN.search(content)
                ):
                    raise _blocked(
                        "workspace_outbound_secret_forbidden",
                        f"{label}包含疑似凭证、token、password 或 private key，已阻断外发",
                        f"请移除{label}中的敏感值后重试",
                    )
                return content
        except UnicodeDecodeError as error:
            raise _blocked(
                "workspace_outbound_file_invalid",
                f"{label}必须使用 UTF-8 编码",
                f"请把{label}转换为 UTF-8 后重试",
            ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def validate_workspace_env_path(workspace_root: Path) -> Path:
    state_root = validate_workspace_state_root(workspace_root)
    env_path = state_root / ".env"
    if env_path.is_symlink():
        raise _blocked(
            "workspace_env_symlink_forbidden",
            "业务项目工作空间的 .agentic-ops/.env 不能是符号链接",
            "请移除符号链接后，通过 ao-work auth jira set 重新配置当前工作空间凭证",
        )
    return env_path


def protect_workspace_env_from_git(
    workspace_root: Path,
    *,
    run_git: GitRunner | None = None,
) -> str:
    """Fail closed for tracked credentials, then ensure local Git exclusion."""
    root = workspace_root.expanduser().resolve()
    env_path = validate_workspace_env_path(root)
    runner = run_git or _run_git

    top_level = runner(["-C", str(root), "rev-parse", "--show-toplevel"])
    if top_level.returncode != 0:
        if _git_metadata_ancestor(root) is not None:
            raise _blocked(
                "workspace_git_boundary_invalid",
                "当前目录位于 Git 元数据目录树中，但无法确认仓库顶层",
                "请修复 Git 仓库权限或安全配置后重试；在确认前不得写入凭证",
            )
        return "hard_separation"
    try:
        repository_root = Path(top_level.stdout.strip()).expanduser().resolve()
        relative_workspace = root.relative_to(repository_root)
    except (OSError, ValueError) as error:
        raise _blocked(
            "workspace_git_boundary_invalid",
            "无法确认业务工作空间所属 Git 仓库边界",
            "请把业务项目 AI 工作空间移到独立目录后重试",
        ) from error

    relative_env = relative_workspace / ".agentic-ops" / ".env"
    tracked = runner(
        [
            "-C",
            str(repository_root),
            "ls-files",
            "--error-unmatch",
            "--",
            relative_env.as_posix(),
        ]
    )
    if tracked.returncode == 0:
        raise _blocked(
            "workspace_env_tracked",
            "业务项目工作空间的 .agentic-ops/.env 已被 Git 跟踪，禁止写入凭证",
            "请先从 Git 索引和历史中安全移除该文件，确认凭证未泄露并完成必要轮换后再重试",
        )
    if tracked.returncode not in {1}:
        raise _blocked(
            "workspace_git_tracking_check_failed",
            "无法确认 .agentic-ops/.env 是否已被 Git 跟踪",
            "请检查 Git 仓库状态后重试；在确认前不得写入凭证",
        )

    git_path = runner(["-C", str(root), "rev-parse", "--git-path", "info/exclude"])
    if git_path.returncode != 0 or not git_path.stdout.strip():
        raise _blocked(
            "workspace_git_exclude_failed",
            "无法定位业务工作空间 Git 本地 exclude",
            "请把业务项目 AI 工作空间移到独立目录后重试",
        )
    exclude_path = Path(git_path.stdout.strip()).expanduser()
    if not exclude_path.is_absolute():
        exclude_path = (root / exclude_path).resolve()
    if exclude_path.is_symlink():
        raise _blocked(
            "workspace_git_exclude_failed",
            "Git 本地 exclude 不能是符号链接",
            "请修复仓库 .git/info/exclude 后重试",
        )

    state_path = relative_workspace / ".agentic-ops"
    pattern = f"/{state_path.as_posix().strip('/')}/"
    try:
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude_path.read_text(encoding="utf-8") if exclude_path.is_file() else ""
        if pattern not in existing.splitlines():
            content = existing
            if content and not content.endswith("\n"):
                content += "\n"
            _atomic_write_text(exclude_path, content + pattern + "\n")
    except OSError as error:
        raise _blocked(
            "workspace_git_exclude_failed",
            "无法写入业务工作空间 Git 本地 exclude",
            "请修复 .git/info/exclude 权限，或把 AI 工作空间移到独立目录",
        ) from error

    ignored = runner(
        [
            "-C",
            str(repository_root),
            "check-ignore",
            "--quiet",
            "--no-index",
            "--",
            relative_env.as_posix(),
        ]
    )
    if ignored.returncode != 0:
        raise _blocked(
            "workspace_git_exclude_failed",
            "Git 本地 exclude 未能保护 .agentic-ops/.env",
            "请修复仓库本地 exclude，确认 git status 不会显示凭证路径后重试",
        )
    # Re-check the exact target after touching Git metadata, before the caller writes a secret.
    if env_path.is_symlink():
        raise _blocked(
            "workspace_env_symlink_forbidden",
            "业务项目工作空间的 .agentic-ops/.env 不能是符号链接",
            "请移除符号链接后，通过 ao-work auth jira set 重新配置当前工作空间凭证",
        )
    tracked_after_protection = runner(
        [
            "-C",
            str(repository_root),
            "ls-files",
            "--error-unmatch",
            "--",
            relative_env.as_posix(),
        ]
    )
    if tracked_after_protection.returncode == 0:
        raise _blocked(
            "workspace_env_tracked",
            "业务项目工作空间的 .agentic-ops/.env 已被 Git 跟踪，禁止写入凭证",
            "请先从 Git 索引和历史中安全移除该文件，确认凭证未泄露并完成必要轮换后再重试",
        )
    if tracked_after_protection.returncode != 1:
        raise _blocked(
            "workspace_git_tracking_check_failed",
            "无法在凭证写入前复核 .agentic-ops/.env 的 Git 跟踪状态",
            "请检查 Git 仓库状态后重试；在确认前不得写入凭证",
        )
    return "git_local_exclude"


def _run_git(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *arguments],
            capture_output=True,
            text=True,
            check=False,
            timeout=20.0,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise _blocked(
            "git_check_failed",
            f"Git 安全检查失败：{type(error).__name__}",
            "请检查 Git 安装和仓库状态后重试；在确认前不得写入凭证",
        ) from error


def _git_metadata_ancestor(path: Path) -> Path | None:
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists() or (candidate / ".git").is_symlink():
            return candidate
    return None


def _blocked(code: str, message: str, action: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=True,
        required_human_action=action,
    )

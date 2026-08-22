from __future__ import annotations

import json
import re
import stat
import subprocess
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult


_INSTALLED_AT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def inspect_version(install_root: Path) -> dict[str, object]:
    """Return only verified, non-sensitive facts about a developer installation."""
    head = _run_git(install_root, "rev-parse", "--verify", "HEAD")
    branch = _run_git(install_root, "symbolic-ref", "--quiet", "--short", "HEAD")
    git_describe = _run_git(install_root, "describe", "--long", "HEAD")
    git_tag, commit_count, describe_hash = _parse_git_describe(git_describe)
    return {
        "version": f"{branch}-{git_describe}",
        "runtime_version": _runtime_version(install_root),
        "install_root": str(install_root),
        "installed_at": _installed_at(install_root),
        "git_head": head,
        "git_short_sha": _run_git(install_root, "rev-parse", "--short=12", "HEAD"),
        "git_describe": git_describe,
        "git_tag": git_tag,
        "git_commit_count": commit_count,
        "git_describe_hash": describe_hash,
    }


def _parse_git_describe(git_describe: str) -> tuple[str, int, str]:
    """Validate the stable `<tag>-<count>-g<hash>` shape from Git."""
    try:
        tag, raw_count, describe_hash = git_describe.rsplit("-", 2)
        commit_count = int(raw_count)
    except ValueError as error:
        raise _blocked(
            "install_git_metadata_invalid",
            "受管安装的 Git 描述版本格式无效",
            "请检查 Git Tag 与 developer 安装后重试",
        ) from error
    if not tag or commit_count < 0 or not re.fullmatch(r"g[0-9a-f]+", describe_hash):
        raise _blocked(
            "install_git_metadata_invalid",
            "受管安装的 Git 描述版本格式无效",
            "请检查 Git Tag 与 developer 安装后重试",
        )
    return tag, commit_count, describe_hash


def _runtime_version(install_root: Path) -> str:
    path = install_root / "developer" / "pyproject.toml"
    _require_regular_file(path, "install_version_metadata_invalid", "版本元数据")
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise _blocked(
            "install_version_metadata_invalid",
            "无法读取 developer Runtime 的发行版本元数据",
            "请通过 developer/bootstrap/install.sh 重新安装",
        ) from error
    project = payload.get("project")
    version = project.get("version") if isinstance(project, dict) else None
    if not isinstance(version, str) or not version.strip() or len(version) > 128:
        raise _blocked(
            "install_version_metadata_invalid",
            "developer Runtime 的发行版本元数据无效",
            "请通过 developer/bootstrap/install.sh 重新安装",
        )
    return version.strip()


def _installed_at(install_root: Path) -> str:
    path = install_root / ".local" / "installation.json"
    _require_regular_file(path, "install_metadata_missing", "安装元数据")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise _blocked(
            "install_metadata_invalid",
            "安装时间元数据无法解析",
            "请通过 developer/bootstrap/install.sh 重新安装；Runtime 不会猜测安装时间",
        ) from error
    installed_at = payload.get("installed_at") if isinstance(payload, dict) else None
    if (
        not isinstance(installed_at, str)
        or payload.get("schema_version") != 1
        or not _INSTALLED_AT.fullmatch(installed_at)
    ):
        raise _blocked(
            "install_metadata_invalid",
            "安装时间元数据格式无效",
            "请通过 developer/bootstrap/install.sh 重新安装；Runtime 不会猜测安装时间",
        )
    try:
        datetime.strptime(installed_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise _blocked(
            "install_metadata_invalid",
            "安装时间元数据包含无效时间",
            "请通过 developer/bootstrap/install.sh 重新安装；Runtime 不会猜测安装时间",
        ) from error
    return installed_at


def _require_regular_file(path: Path, code: str, label: str) -> None:
    try:
        info = path.lstat()
    except OSError as error:
        raise _blocked(
            code,
            f"缺少可验证的{label}",
            "请通过 developer/bootstrap/install.sh 重新安装；Runtime 不会猜测版本或安装时间",
        ) from error
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600 and path.name == "installation.json"
    ):
        raise _blocked(
            code,
            f"{label}不是安全的受管普通文件",
            "请通过 developer/bootstrap/install.sh 重新安装；Runtime 不会猜测版本或安装时间",
        )


def _run_git(install_root: Path, *arguments: str, allow_empty: bool = False) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(install_root), *arguments],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise _blocked(
            "install_git_metadata_invalid",
            "无法读取受管安装的 Git 版本信息",
            "请检查 Git 与 developer 安装后重试",
        ) from error
    if result.returncode != 0 or (not allow_empty and not result.stdout.strip()):
        raise _blocked(
            "install_git_metadata_invalid",
            "受管安装的 Git 版本信息无效",
            "请通过 developer/bootstrap/install.sh 重新安装",
        )
    return result.stdout.strip()


def _blocked(code: str, message: str, action: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=True,
        required_human_action=action,
    )

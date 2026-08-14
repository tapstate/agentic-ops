from __future__ import annotations

import os
import re
from pathlib import Path

from ao_work.managed_io import read_managed_text
from ao_work.task_state.io import atomic_write_text
from ao_work.workspace_security import (
    GitRunner,
    protect_workspace_env_from_git,
    validate_workspace_env_path,
)

ENV_NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def resolve_secret_pair_with_source(
    first_name: str,
    second_name: str,
    path: Path,
    *,
    allow_process_environment: bool = False,
) -> tuple[str | None, str | None, str]:
    """Resolve one credential pair without combining values from different accounts."""
    if allow_process_environment:
        process_first = os.environ.get(first_name, "").strip()
        process_second = os.environ.get(second_name, "").strip()
        if process_first or process_second:
            return process_first or None, process_second or None, "explicit_process_environment"

    values = read_env_file(path)
    file_first = values.get(first_name, "").strip()
    file_second = values.get(second_name, "").strip()
    if file_first or file_second:
        return file_first or None, file_second or None, "workspace"
    return None, None, "missing"


def read_env_file(path: Path) -> dict[str, str]:
    _validate_managed_env_path(path)
    content = read_managed_text(
        path,
        label="工作空间 .agentic-ops/.env",
        allow_missing=True,
        max_bytes=1024 * 1024,
    )
    if content is None:
        return {}
    result: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        if not separator or not name.strip():
            continue
        cleaned = value.strip()
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
            cleaned = cleaned[1:-1]
        result[name.strip()] = cleaned
    return result


def update_env_file(path: Path, updates: dict[str, str | None]) -> None:
    _validate_managed_env_path(path)
    for name, value in updates.items():
        if not ENV_NAME_PATTERN.fullmatch(name):
            raise ValueError(f"invalid environment variable name: {name}")
        if value is not None and any(character in value for character in ("\n", "\r", "\x00")):
            raise ValueError(f"environment variable {name} contains an invalid control character")

    existing = read_managed_text(
        path,
        label="工作空间 .agentic-ops/.env",
        allow_missing=True,
        max_bytes=1024 * 1024,
    )
    existing_lines = existing.splitlines() if existing is not None else []
    remaining = dict(updates)
    output: list[str] = []
    for raw_line in existing_lines:
        stripped = raw_line.strip()
        candidate = stripped[7:].lstrip() if stripped.startswith("export ") else stripped
        name, separator, _ = candidate.partition("=")
        normalized_name = name.strip()
        if separator and normalized_name in remaining:
            value = remaining.pop(normalized_name)
            if value is not None:
                output.append(f"{normalized_name}={value}")
            continue
        output.append(raw_line)
    for name, value in remaining.items():
        if value is not None:
            output.append(f"{name}={value}")
    while output and not output[-1].strip():
        output.pop()
    _validate_managed_env_path(path)
    atomic_write_text(path, "\n".join(output))


def update_workspace_env_file(
    workspace_root: Path,
    updates: dict[str, str | None],
    *,
    run_git: GitRunner | None = None,
) -> str:
    protection = protect_workspace_env_from_git(workspace_root, run_git=run_git)
    env_path = validate_workspace_env_path(workspace_root)
    update_env_file(env_path, updates)
    return protection


def _validate_managed_env_path(path: Path) -> None:
    if path.name == ".env" and path.parent.name == ".agentic-ops":
        validate_workspace_env_path(path.parent.parent)

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult

MAX_MANAGED_FILE_BYTES = 16 * 1024 * 1024


def read_managed_text(
    path: Path,
    *,
    label: str,
    allow_missing: bool = False,
    max_bytes: int = MAX_MANAGED_FILE_BYTES,
) -> str | None:
    """Read one managed leaf through a verified descriptor, never through a link."""
    descriptor = open_managed_regular(
        path,
        os.O_RDONLY,
        label=label,
        allow_missing=allow_missing,
        max_bytes=max_bytes,
    )
    if descriptor is None:
        return None
    try:
        content = bytearray()
        while True:
            chunk = os.read(descriptor, min(65_536, max_bytes + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > max_bytes:
                raise _unsafe(path, label, "读取期间大小超过上限")
        _verify_open_descriptor(path, descriptor, label=label, max_bytes=max_bytes)
        return bytes(content).decode("utf-8")
    except UnicodeDecodeError as error:
        raise _unsafe(path, label, "不是 UTF-8 文本") from error
    finally:
        os.close(descriptor)


def read_managed_json(path: Path, *, label: str) -> dict[str, Any]:
    content = read_managed_text(path, label=label)
    assert content is not None

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number: {value}")

    payload = json.loads(
        content,
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
    )
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def open_managed_regular(
    path: Path,
    flags: int,
    *,
    label: str,
    allow_missing: bool = False,
    mode: int = 0o600,
    max_bytes: int = MAX_MANAGED_FILE_BYTES,
) -> int | None:
    safe_flags = flags
    if hasattr(os, "O_NOFOLLOW"):
        safe_flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        safe_flags |= os.O_CLOEXEC
    try:
        descriptor = os.open(path, safe_flags, mode)
    except FileNotFoundError:
        if allow_missing:
            return None
        raise _unsafe(path, label, "文件不存在")
    except OSError as error:
        raise _unsafe(path, label, type(error).__name__) from error
    try:
        _verify_open_descriptor(path, descriptor, label=label, max_bytes=max_bytes)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _verify_open_descriptor(
    path: Path,
    descriptor: int,
    *,
    label: str,
    max_bytes: int,
) -> None:
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
        or opened.st_size > max_bytes
    ):
        raise _unsafe(path, label, "不是单链接普通文件或大小超限")
    current = os.stat(path, follow_symlinks=False)
    if (
        current.st_dev,
        current.st_ino,
        current.st_mode,
        current.st_nlink,
    ) != (
        opened.st_dev,
        opened.st_ino,
        opened.st_mode,
        opened.st_nlink,
    ):
        raise _unsafe(path, label, "安全检查期间文件发生变化")


def _unsafe(path: Path, label: str, detail: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code="managed_file_unsafe",
        message=f"{label}必须是当前工作空间内的单链接普通文件：{path.name}（{detail}）",
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=False,
        required_human_action="请移除符号链接、硬链接或特殊文件，并核对是否发生跨工作空间读取或写入",
    )

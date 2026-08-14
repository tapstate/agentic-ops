from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Mapping

from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult

MAX_STATE_FILE_BYTES = 16 * 1024 * 1024


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    require_safe_regular_file(path, allow_missing=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                payload,
                stream,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        require_safe_regular_file(path, allow_missing=True)
        os.replace(temporary_path, path)
        _fsync_directory(path.parent)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    require_safe_regular_file(path, allow_missing=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            if content and not content.endswith("\n"):
                stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        require_safe_regular_file(path, allow_missing=True)
        os.replace(temporary_path, path)
        _fsync_directory(path.parent)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def append_ndjson(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    encoded_bytes = (encoded + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    descriptor = _open_safe_regular(path, flags, mode=0o600)
    if os.fstat(descriptor).st_size + len(encoded_bytes) > MAX_STATE_FILE_BYTES:
        os.close(descriptor)
        raise _unsafe_leaf(path, "追加后大小将超过上限")
    with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
        stream.write(encoded)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
        _verify_open_descriptor(path, stream.fileno())
    _fsync_directory(path.parent)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(read_text(path))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_text(path: Path) -> str:
    descriptor = _open_safe_regular(path, os.O_RDONLY)
    try:
        content = bytearray()
        while True:
            chunk = os.read(
                descriptor,
                min(65_536, MAX_STATE_FILE_BYTES + 1 - len(content)),
            )
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > MAX_STATE_FILE_BYTES:
                raise _unsafe_leaf(path, "读取期间大小超过上限")
        _verify_open_descriptor(path, descriptor)
        return bytes(content).decode("utf-8")
    except UnicodeDecodeError as error:
        raise _unsafe_leaf(path, "不是 UTF-8 文本") from error
    finally:
        os.close(descriptor)


def require_safe_regular_file(path: Path, *, allow_missing: bool = False) -> None:
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        if allow_missing:
            return
        raise _unsafe_leaf(path, "文件不存在")
    except OSError as error:
        raise _unsafe_leaf(path, type(error).__name__) from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size > MAX_STATE_FILE_BYTES
    ):
        raise _unsafe_leaf(path, "不是单链接普通文件或大小超限")


def _open_safe_regular(path: Path, flags: int, *, mode: int = 0o600) -> int:
    safe_flags = flags
    if hasattr(os, "O_NOFOLLOW"):
        safe_flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        safe_flags |= os.O_CLOEXEC
    try:
        descriptor = os.open(path, safe_flags, mode)
    except OSError as error:
        raise _unsafe_leaf(path, type(error).__name__) from error
    try:
        _verify_open_descriptor(path, descriptor)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _verify_open_descriptor(path: Path, descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size > MAX_STATE_FILE_BYTES
    ):
        raise _unsafe_leaf(path, "不是单链接普通文件或大小超限")
    current = os.stat(path, follow_symlinks=False)
    if (
        current.st_dev,
        current.st_ino,
        current.st_mode,
        current.st_nlink,
    ) != (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
    ):
        raise _unsafe_leaf(path, "安全检查期间文件发生变化")


def _unsafe_leaf(path: Path, detail: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code="task_state_leaf_unsafe",
        message=f"任务状态叶子必须是当前工作空间内的单链接普通文件：{path.name}（{detail}）",
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=False,
        required_human_action="请移除符号链接、硬链接或特殊文件，并核对是否发生跨工作空间状态篡改",
    )


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

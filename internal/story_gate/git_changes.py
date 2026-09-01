from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from internal.story_gate.model import ChangeSet


def collect_changes(
    root: Path,
    source: str,
    *,
    base: str | None = None,
    head: str | None = None,
) -> ChangeSet:
    _git(root, "rev-parse", "--show-toplevel")
    if source == "staged":
        names = _nul_paths(_git_bytes(root, "diff", "--cached", "--name-only", "-z"))
        material = _git_bytes(root, "diff", "--cached", "--binary", "--no-ext-diff")
    elif source == "worktree":
        names = _nul_paths(_git_bytes(root, "diff", "HEAD", "--name-only", "-z"))
        untracked = _nul_paths(
            _git_bytes(root, "ls-files", "--others", "--exclude-standard", "-z")
        )
        names = tuple(sorted(set(names) | set(untracked)))
        material = _git_bytes(root, "diff", "HEAD", "--binary", "--no-ext-diff")
        for path in untracked:
            material += b"\0UNTRACKED\0" + path.encode("utf-8") + b"\0"
            candidate = root / path
            if candidate.is_file():
                material += candidate.read_bytes()
    elif source == "range":
        if not base:
            raise ValueError("range 变更来源要求 --base")
        target = head or "HEAD"
        revision_range = f"{base}...{target}"
        names = _nul_paths(_git_bytes(root, "diff", revision_range, "--name-only", "-z"))
        material = _git_bytes(root, "diff", revision_range, "--binary", "--no-ext-diff")
    else:
        raise ValueError(f"不支持的变更来源：{source}")
    return ChangeSet(
        source=source,
        paths=tuple(sorted(set(names))),
        fingerprint=hashlib.sha256(material).hexdigest(),
    )


def _git(root: Path, *arguments: str) -> str:
    return _git_bytes(root, *arguments).decode("utf-8", errors="replace").strip()


def _git_bytes(root: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )
    if completed.returncode != 0:
        diagnostic = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"Git 变更读取失败：{diagnostic or 'unknown error'}")
    return completed.stdout


def _nul_paths(payload: bytes) -> tuple[str, ...]:
    return tuple(
        item.decode("utf-8", errors="surrogateescape")
        for item in payload.split(b"\0")
        if item
    )

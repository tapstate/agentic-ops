from __future__ import annotations

import os
from pathlib import Path


def resolve_secret(name: str, paths: list[Path]) -> str | None:
    process_value = os.environ.get(name, "").strip()
    if process_value:
        return process_value
    for path in paths:
        values = read_env_file(path)
        value = values.get(name, "").strip()
        if value:
            return value
    return None


def read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
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

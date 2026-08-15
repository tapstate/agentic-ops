from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit


_COMPONENT = re.compile(r"^[0-9A-Za-z_.-]+$")
_SCP = re.compile(r"^git@github\.com:([^/]+)/([^/]+)$")


def parse_github_repository_url(value: str) -> str:
    """Return exact owner/repository for the narrowly supported GitHub URL forms."""
    raw = value.strip()
    match = _SCP.fullmatch(raw)
    if match:
        return _slug(match.group(1), match.group(2))

    parsed = urlsplit(raw)
    if parsed.scheme not in {"https", "ssh"}:
        return ""
    if parsed.hostname != "github.com" or parsed.query or parsed.fragment:
        return ""
    try:
        if parsed.port is not None:
            return ""
    except ValueError:
        return ""
    if parsed.scheme == "https":
        if parsed.username is not None or parsed.password is not None or parsed.netloc != "github.com":
            return ""
    elif parsed.username != "git" or parsed.password is not None or parsed.netloc != "git@github.com":
        return ""
    if unquote(parsed.path) != parsed.path:
        return ""
    parts = parsed.path.split("/")
    if len(parts) != 3 or parts[0] != "":
        return ""
    return _slug(parts[1], parts[2])


def github_repository_url_matches(value: str, expected_slug: str) -> bool:
    return parse_github_repository_url(value) == expected_slug


def _slug(owner: str, repository: str) -> str:
    name = repository[:-4] if repository.endswith(".git") else repository
    if (
        not owner
        or not name
        or owner in {".", ".."}
        or name in {".", ".."}
        or not _COMPONENT.fullmatch(owner)
        or not _COMPONENT.fullmatch(name)
    ):
        return ""
    return f"{owner}/{name}"

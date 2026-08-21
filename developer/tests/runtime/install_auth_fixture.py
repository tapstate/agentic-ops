from __future__ import annotations

from pathlib import Path
from typing import Any

from ao_work.config.loader import _install_identity_ref, install_entry_sha256
from ao_work.installation import (
    load_install_identity,
    save_install_credentials,
    save_install_identity,
)


def configure_install_authorization(
    install: Path,
    *,
    agent_id: str = "harsen-mini-test-bot",
    jira_email: str = "harsen@example.test",
    token: str = "test-token-secret",
    git_email: str | None = None,
) -> str:
    entry = install / "bin" / "ao-work"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    entry.chmod(0o700)
    effective_git_email = git_email or jira_email
    save_install_identity(
        install,
        {
            "agent_id": agent_id,
            "jira_email": jira_email,
            "execution_identity": {
                "git_author_name": "Harsen Test Bot",
                "git_author_email": effective_git_email,
                "git_committer_name": "Harsen Test Bot",
                "git_committer_email": effective_git_email,
                "github_actor_login": agent_id,
            },
        },
    )
    save_install_credentials(install, jira_email, token)
    return _install_identity_ref(install, load_install_identity(install))


def v5_agent(
    install: Path,
    **values: Any,
) -> dict[str, Any]:
    return {
        "schema_version": 5,
        "workplane": "developer",
        "install_identity_ref": _install_identity_ref(
            install,
            load_install_identity(install),
        ),
        "workspace_entry": ".agentic-ops/bin/ao-work",
        "install_entry_sha256": install_entry_sha256(install),
        **values,
    }


# 兼容测试导入名；产出已是 schema v5，不保留旧 schema 行为。
v4_agent = v5_agent

from __future__ import annotations

from pathlib import Path
from typing import Any

from ao_work.config.loader import _install_identity_ref
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


def v4_agent(
    install: Path,
    **values: Any,
) -> dict[str, Any]:
    return {
        "schema_version": 4,
        "workplane": "developer",
        "install_identity_ref": _install_identity_ref(
            install,
            load_install_identity(install),
        ),
        **values,
    }

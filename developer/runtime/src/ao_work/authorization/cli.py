from __future__ import annotations

import argparse
import getpass
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from ao_work.authorization.execution import (
    AUTHORIZATION_MODES,
    GLOBAL,
    INSTALLATION,
    authorization_change_digest,
    authorization_existing_summary,
    authorization_paths,
    installation_managed_configuration_differs,
    installation_ssh_command,
    operational_environment,
    prepare_installation_authorization,
    public_key_fingerprint,
)

from ao_work.installation import (
    build_execution_identity,
    install_user_dir,
    load_install_credentials,
    load_install_identity,
    mask_email,
    save_install_credentials,
    save_install_identity,
    validate_agent_id,
    validate_jira_email,
)
from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult
from ao_work.task_state.locking import TaskLock


def configure_authorization_parser(
    subparsers: argparse._SubParsersAction[Any],
) -> None:
    auth = subparsers.add_parser("auth")
    auth.add_argument("--show", action="store_true")
    auth.add_argument("--agent-id")
    auth.add_argument("--jira-email")
    auth.add_argument("--git-name")
    auth.add_argument("--git-email")
    auth.add_argument("--github-login")
    auth.add_argument("--execution-auth-mode", choices=AUTHORIZATION_MODES)
    auth.add_argument("--confirm-replace-authorization")
    auth.add_argument("--token-stdin", action="store_true")
    auth.add_argument("--non-interactive", action="store_true")


def execute_authorization(
    args: argparse.Namespace,
    install_root: Path,
) -> dict[str, Any]:
    install_user_dir(install_root)
    if args.show:
        if any(
            (
                args.agent_id,
                args.jira_email,
                args.git_name,
                args.git_email,
                args.github_login,
                args.execution_auth_mode,
                args.confirm_replace_authorization,
                args.token_stdin,
                args.non_interactive,
            )
        ):
            raise _blocked(
                "authorization_show_arguments_invalid",
                "--show 不能与授权写入参数同时使用",
                "请单独执行 ao-work auth --show",
            )
        return _show(install_root)
    return _set(args, install_root)


def _show(install_root: Path) -> dict[str, Any]:
    try:
        identity = load_install_identity(install_root)
    except RuntimeErrorResult as error:
        if error.code != "install_identity_missing":
            raise
        return {
            "configured": False,
            "identity_configured": False,
            "jira_credentials_configured": False,
            "user_dir": str(install_user_dir(install_root)),
        }
    credentials = load_install_credentials(install_root)
    return {
        "configured": credentials is not None,
        "identity_configured": True,
        "agent_id": identity["agent_id"],
        "jira_email": mask_email(identity["jira_email"]),
        "execution_identity": {
            "git_author_name": identity["execution_identity"]["git_author_name"],
            "git_author_email": mask_email(
                identity["execution_identity"]["git_author_email"]
            ),
            "github_actor_login": identity["execution_identity"][
                "github_actor_login"
            ],
        },
        "execution_authorization": identity["execution_authorization"],
        "existing_authorization": authorization_existing_summary(
            install_root, identity
        ),
        "jira_credentials_configured": credentials is not None,
        "authorization_scope": "installation",
    }


def _set(args: argparse.Namespace, install_root: Path) -> dict[str, Any]:
    interactive = not args.non_interactive
    if interactive and not sys.stdin.isatty():
        raise _blocked(
            "interactive_terminal_required",
            "零参数授权配置需要终端输入",
            "请直接在终端运行 ao-work auth，或提供完整非交互参数",
        )

    existing: dict[str, Any] = {}
    try:
        existing = load_install_identity(install_root)
    except RuntimeErrorResult as error:
        if error.code == "install_execution_authorization_upgrade_required":
            existing = _load_legacy_identity_for_upgrade(install_root)
        elif error.code != "install_identity_missing":
            raise

    agent_id = args.agent_id or existing.get("agent_id") or ""
    jira_email = args.jira_email or existing.get("jira_email") or ""
    existing_execution = existing.get("execution_identity", {})
    git_name = args.git_name or existing_execution.get("git_author_name") or ""
    git_email = args.git_email or existing_execution.get("git_author_email") or ""
    github_login = (
        args.github_login or existing_execution.get("github_actor_login") or ""
    )
    existing_authorization = existing.get("execution_authorization", {})
    execution_auth_mode = (
        args.execution_auth_mode
        or existing_authorization.get("mode")
        or ""
    )
    if interactive:
        if not agent_id:
            agent_id = _prompt_required("agent_id（研发员标识，安装级唯一）")
        if not jira_email:
            jira_email = _prompt_required("Jira email")
        if not git_name:
            git_name = _prompt_required("Git author/committer name")
        if not git_email:
            git_email = _prompt_required("Git email")
        if not github_login:
            github_login = _prompt_required("GitHub login")
        if not execution_auth_mode:
            execution_auth_mode = _prompt_choice(
                "Git/SSH/gh 授权模式",
                AUTHORIZATION_MODES,
            )
    if not all(
        (agent_id, jira_email, git_name, git_email, github_login, execution_auth_mode)
    ):
        raise _blocked(
            "install_identity_incomplete",
            "安装级授权缺少必填身份字段",
            "请补齐 agent_id、Jira email、Git 身份、GitHub login 与 execution auth mode",
        )
    if execution_auth_mode not in AUTHORIZATION_MODES:
        raise _blocked(
            "install_execution_authorization_invalid",
            "Git/SSH/gh 授权模式无效",
            "请选择 global 或 installation",
        )

    normalized_agent_id = validate_agent_id(agent_id)
    normalized_jira_email = validate_jira_email(jira_email)
    execution_identity = build_execution_identity(
        git_name,
        git_email,
        github_login,
    )
    proposed_change = {
        "agent_id": normalized_agent_id,
        "jira_email": normalized_jira_email,
        "execution_identity": execution_identity,
        "execution_auth_mode": execution_auth_mode,
        "existing": authorization_existing_summary(
            install_root, existing or None
        ),
    }
    change_digest = authorization_change_digest(proposed_change)
    managed_config_changed = (
        execution_auth_mode == INSTALLATION
        and installation_managed_configuration_differs(install_root)
    )
    identity_changed = bool(existing) and any(
        (
            existing.get("agent_id") != normalized_agent_id,
            existing.get("jira_email") != normalized_jira_email,
            existing.get("execution_identity") != execution_identity,
            existing_authorization.get("mode") != execution_auth_mode,
        )
    )
    if (identity_changed or managed_config_changed) and (
        args.confirm_replace_authorization != change_digest
    ):
        raise _blocked(
            "existing_authorization_change_confirmation_required",
            "检测到已有安装身份或授权模式与候选配置不同",
            "请审查输出中的脱敏 existing/candidate/change_digest；只有精确绑定当前差异摘要的确认才能更新",
            details={
                "existing": authorization_existing_summary(install_root, existing),
                "candidate": {
                    "agent_id": normalized_agent_id,
                    "jira_email": mask_email(normalized_jira_email),
                    "git_author_name": execution_identity["git_author_name"],
                    "git_author_email": mask_email(
                        execution_identity["git_author_email"]
                    ),
                    "github_login": execution_identity["github_actor_login"],
                    "mode": execution_auth_mode,
                },
                "change_digest": change_digest,
            },
        )

    _preflight_managed_clone_ssh(install_root, mode=execution_auth_mode)

    global_readback: dict[str, Any] | None = None
    if execution_auth_mode == GLOBAL:
        global_readback = _validate_global_authorization(execution_identity)

    if interactive:
        _confirm_authorization_selection(
            execution_auth_mode,
            normalized_agent_id,
            execution_identity,
            install_root,
        )

    token: str | None = None
    if args.token_stdin:
        token = sys.stdin.readline().rstrip("\r\n")
    elif interactive:
        token = getpass.getpass(
            "AgenticOps：Jira API token：",
            stream=sys.stderr,
        ).strip()
    if not token:
        raise _blocked(
            "authorization_token_empty",
            "Jira API token 不能为空",
            "请重新运行 ao-work auth 并通过隐藏输入或安全标准输入提供 token",
        )
    if len(token.strip()) < 8:
        raise _blocked(
            "authorization_token_invalid",
            "Jira token 长度明显不合理",
            "请重新运行 ao-work auth 并输入当前 Jira 账户的 API token",
        )

    if execution_auth_mode == GLOBAL:
        execution_authorization = {
            "mode": GLOBAL,
            "ssh_key_fingerprint": "",
        }
        authorization_readback = dict(global_readback or {})
    else:
        paths = authorization_paths(install_root)
        if not interactive and not paths["private_key"].is_file():
            raise _blocked(
                "installation_github_interactive_authorization_required",
                "首次安装级 GitHub/SSH 授权需要交互终端",
                "请在终端运行 ao-work auth --execution-auth-mode installation；Runtime 不会复用 Jira token 或全局账户静默完成 GitHub 登录",
            )
        authorization_readback = prepare_installation_authorization(
            install_root,
            github_login=execution_identity["github_actor_login"],
            allow_managed_update=args.confirm_replace_authorization == change_digest,
        )
        authorization_readback["ssh_connection_login"] = (
            _validate_or_login_installation_github(
                install_root,
                execution_identity["github_actor_login"],
                interactive=interactive,
            )
        )
        execution_authorization = {
            "mode": INSTALLATION,
            "ssh_key_fingerprint": authorization_readback[
                "ssh_key_fingerprint"
            ],
        }
    _configure_managed_clone_ssh(
        install_root,
        mode=execution_auth_mode,
    )

    user_dir = install_user_dir(install_root)
    user_dir.mkdir(parents=True, exist_ok=True)
    with TaskLock(user_dir / ".authorization.lock", timeout=5):
        save_install_identity(
            install_root,
            {
                "agent_id": normalized_agent_id,
                "jira_email": normalized_jira_email,
                "execution_identity": execution_identity,
                "execution_authorization": execution_authorization,
            },
        )
        save_install_credentials(install_root, normalized_jira_email, token.strip())
    return {
        "configured": True,
        "identity_configured": True,
        "agent_id": normalized_agent_id,
        "jira_email": mask_email(normalized_jira_email),
        "execution_identity": {
            "git_author_name": execution_identity["git_author_name"],
            "git_author_email": mask_email(execution_identity["git_author_email"]),
            "github_actor_login": execution_identity["github_actor_login"],
        },
        "execution_authorization": execution_authorization,
        "authorization_readback": authorization_readback,
        "change_digest": change_digest,
        "jira_credentials_configured": True,
        "authorization_scope": "installation",
    }


def _prompt_required(label: str, default: str = "") -> str:
    if default:
        value = input(f"AgenticOps：{label} [{default}]：").strip() or default
    else:
        value = input(f"AgenticOps：{label}：").strip()
    if not value:
        raise _blocked(
            "install_identity_incomplete",
            f"缺少 {label}",
            "请重新运行 ao-work auth 并补齐必填字段",
        )
    return value


def _prompt_choice(label: str, choices: tuple[str, ...]) -> str:
    value = input(
        f"AgenticOps：{label}（{'/'.join(choices)}）："
    ).strip()
    if value not in choices:
        raise _blocked(
            "install_execution_authorization_invalid",
            f"{label}必须是 {' 或 '.join(choices)}",
            "请重新运行 ao-work auth 并明确选择授权模式",
        )
    return value


def _confirm_authorization_selection(
    mode: str,
    agent_id: str,
    execution_identity: dict[str, str],
    install_root: Path,
) -> None:
    if mode == GLOBAL:
        prompt = (
            "仅复用机器现有 Git/SSH/gh 授权，不修改全局配置。"
            f" agent_id={agent_id}，Git={execution_identity['git_author_name']}"
            f" <{mask_email(execution_identity['git_author_email'])}>，"
            f"GitHub={execution_identity['github_actor_login']}。"
            "输入 REUSE-GLOBAL 确认："
        )
        expected = "REUSE-GLOBAL"
    else:
        prompt = (
            "机器全局 Git/SSH/gh 授权保持不变；将在当前安装 user/ 下创建或复用"
            f"专属授权：{install_root}。输入 USE-INSTALLATION 确认："
        )
        expected = "USE-INSTALLATION"
    if input(f"AgenticOps：{prompt}").strip() != expected:
        raise _blocked(
            "authorization_selection_not_confirmed",
            "未确认当前 Git/SSH/gh 授权选择",
            "Runtime 未修改已有授权；请核对提示后重新运行",
        )


def _validate_global_authorization(
    execution_identity: dict[str, str],
) -> dict[str, Any]:
    git_name = _command_stdout(
        ["git", "config", "--global", "--get", "user.name"],
        "global_git_identity_unavailable",
        "无法读取机器全局 Git name",
    )
    git_email = _command_stdout(
        ["git", "config", "--global", "--get", "user.email"],
        "global_git_identity_unavailable",
        "无法读取机器全局 Git email",
    )
    github_login = _command_stdout(
        ["gh", "api", "user", "--jq", ".login"],
        "global_github_authorization_unavailable",
        "无法读取机器全局 gh 登录账户",
    )
    if (
        git_name != execution_identity["git_author_name"]
        or git_email != execution_identity["git_author_email"]
        or github_login != execution_identity["github_actor_login"]
    ):
        raise _blocked(
            "global_authorization_identity_mismatch",
            "机器全局 Git/gh 身份与候选安装身份不一致",
            "请切换机器现有账户，或选择 installation 模式；Runtime 未修改全局配置",
            details={
                "observed": {
                    "git_name": git_name,
                    "git_email": mask_email(git_email),
                    "github_login": github_login,
                },
                "expected": {
                    "git_name": execution_identity["git_author_name"],
                    "git_email": mask_email(
                        execution_identity["git_author_email"]
                    ),
                    "github_login": execution_identity["github_actor_login"],
                },
            },
        )
    ssh_config = Path.home() / ".ssh" / "config"
    if ssh_config.is_symlink():
        ssh_config_state = "present_symlink"
    elif ssh_config.is_file():
        ssh_config_state = "present_file"
    else:
        ssh_config_state = "absent"
    return {
        "mode": GLOBAL,
        "global_authorization_modified": False,
        "git_author_name": git_name,
        "git_author_email": mask_email(git_email),
        "github_login": github_login,
        "global_ssh_config": ssh_config_state,
        "global_ssh_agent": "configured" if os.environ.get("SSH_AUTH_SOCK") else "absent",
        "ssh_push_actor_independently_verified": False,
    }


def _validate_or_login_installation_github(
    install_root: Path,
    github_login: str,
    *,
    interactive: bool,
) -> str:
    paths = authorization_paths(install_root)
    identity = {
        "execution_authorization": {
            "mode": INSTALLATION,
            "ssh_key_fingerprint": public_key_fingerprint(paths["public_key"]),
        }
    }
    environment = operational_environment(install_root, identity)
    observed = _optional_command_stdout(
        ["gh", "api", "user", "--jq", ".login"],
        environment,
    )
    if observed and observed != github_login:
        raise _blocked(
            "existing_authorization_change_confirmation_required",
            "安装目录已有不同 gh 登录账户",
            "请先审查并单独确认账户切换；Runtime 未执行 gh logout 或覆盖现有登录",
            details={"observed_github_login": observed, "expected": github_login},
        )
    if not observed:
        if not interactive:
            raise _blocked(
                "installation_github_interactive_authorization_required",
                "安装目录尚未完成 gh 登录",
                "请在终端运行 ao-work auth 完成官方设备登录；Runtime 不会回退到全局 gh 账户",
            )
        login = subprocess.run(
            [
                "gh",
                "auth",
                "login",
                "--hostname",
                "github.com",
                "--git-protocol",
                "ssh",
                "--skip-ssh-key",
                "--web",
            ],
            env=environment,
            check=False,
        )
        if login.returncode != 0:
            raise _blocked(
                "installation_github_login_failed",
                "安装目录 gh 官方登录未完成",
                "可安全重试 ao-work auth；Runtime 未修改全局 gh 配置",
            )
        observed = _optional_command_stdout(
            ["gh", "api", "user", "--jq", ".login"],
            environment,
        )
    if observed != github_login:
        raise _blocked(
            "installation_github_identity_mismatch",
            "安装目录 gh 登录账户与候选身份不一致",
            "请停止使用当前安装授权并核对 GitHub 账户",
        )

    public_key_text = paths["public_key"].read_text(encoding="utf-8").strip()
    key_material = " ".join(public_key_text.split()[:2])
    keys = _optional_command_stdout(
        ["gh", "api", "user/keys", "--paginate", "--jq", ".[].key"],
        environment,
    )
    if key_material not in {line.strip() for line in keys.splitlines()}:
        title = (
            "AgenticOps "
            + public_key_fingerprint(paths["public_key"]).removeprefix("SHA256:")[:12]
        )
        added = subprocess.run(
            ["gh", "ssh-key", "add", str(paths["public_key"]), "--title", title],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if added.returncode != 0:
            raise _blocked(
                "installation_github_ssh_key_add_failed",
                "无法把安装 SSH 公钥登记到当前 GitHub 账户",
                "请核对 GitHub OAuth scope/SSO；可重试且不会覆盖现有 SSH 私钥",
            )
    return _verify_installation_ssh_actor(
        install_root,
        github_login,
        environment,
    )


def _verify_installation_ssh_actor(
    install_root: Path,
    github_login: str,
    environment: dict[str, str],
) -> str:
    result = subprocess.run(
        [
            "ssh",
            "-F",
            str(authorization_paths(install_root)["ssh_config"]),
            "-T",
            "git@github.com",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    output = "\n".join((result.stdout, result.stderr))
    match = re.search(r"Hi ([^!\r\n]+)!", output)
    observed = match.group(1).strip() if match else ""
    if observed != github_login:
        raise _blocked(
            "installation_github_ssh_identity_mismatch",
            "安装专属 SSH 连接未回读到候选 GitHub 账户",
            "请核对 SSH 公钥登记、组织 SSO 和 GitHub 账户；Runtime 不会回退到全局 SSH Agent",
            details={
                "observed_github_login": observed or None,
                "expected": github_login,
            },
        )
    return observed


def _command_stdout(argv: list[str], code: str, message: str) -> str:
    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    value = result.stdout.strip()
    if result.returncode != 0 or not value:
        raise _blocked(code, message, "请先完成机器现有授权，或选择 installation 模式")
    return value


def _optional_command_stdout(argv: list[str], environment: dict[str, str]) -> str:
    result = subprocess.run(
        argv,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _load_legacy_identity_for_upgrade(install_root: Path) -> dict[str, Any]:
    path = install_user_dir(install_root) / "identity.yaml"
    if path.is_symlink() or not path.is_file():
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        raise _blocked(
            "install_identity_invalid",
            "已有安装身份无法安全读取",
            "请人工核对 identity.yaml；Runtime 不会覆盖无法识别的已有授权",
        ) from error
    if not isinstance(payload, dict):
        raise _blocked(
            "install_identity_invalid",
            "已有安装身份结构无效",
            "请人工核对 identity.yaml；Runtime 不会覆盖无法识别的已有授权",
        )
    return payload


def _configure_managed_clone_ssh(install_root: Path, *, mode: str) -> None:
    if not (install_root / ".git").exists():
        return
    expected = installation_ssh_command(install_root)
    read = subprocess.run(
        [
            "git",
            "-C",
            str(install_root),
            "config",
            "--local",
            "--get",
            "core.sshCommand",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    current = read.stdout.strip() if read.returncode == 0 else ""
    if mode == INSTALLATION:
        if current and current != expected:
            raise _blocked(
                "existing_authorization_unmanaged_conflict",
                "managed clone 已有不同的 core.sshCommand",
                "请人工核对仓库本地 SSH 配置；Runtime 不会无提示覆盖",
                details={"current": current, "candidate": expected},
            )
        if current == expected:
            return
        write = subprocess.run(
            [
                "git",
                "-C",
                str(install_root),
                "config",
                "--local",
                "core.sshCommand",
                expected,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if write.returncode != 0:
            raise _blocked(
                "installation_managed_clone_ssh_config_failed",
                "无法为 developer managed clone 固化安装专属 SSH",
                "请检查安装仓库本地配置权限；Runtime 未修改全局 Git/SSH 配置",
            )
        return
    if current == expected:
        remove = subprocess.run(
            [
                "git",
                "-C",
                str(install_root),
                "config",
                "--local",
                "--unset",
                "core.sshCommand",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if remove.returncode != 0:
            raise _blocked(
                "installation_managed_clone_ssh_config_failed",
                "无法移除 Runtime 先前管理的安装级 core.sshCommand",
                "请核对 managed clone 本地配置；Runtime 未修改机器全局授权",
            )


def _preflight_managed_clone_ssh(install_root: Path, *, mode: str) -> None:
    if mode != INSTALLATION or not (install_root / ".git").exists():
        return
    expected = installation_ssh_command(install_root)
    read = subprocess.run(
        [
            "git",
            "-C",
            str(install_root),
            "config",
            "--local",
            "--get",
            "core.sshCommand",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    current = read.stdout.strip() if read.returncode == 0 else ""
    if current and current != expected:
        raise _blocked(
            "existing_authorization_unmanaged_conflict",
            "managed clone 已有不同的 core.sshCommand",
            "请人工核对仓库本地 SSH 配置；Runtime 不会无提示覆盖",
            details={"current": current, "candidate": expected},
        )


def _blocked(
    code: str,
    message: str,
    action: str,
    *,
    details: dict[str, Any] | None = None,
) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=True,
        required_human_action=action,
        details=details or {},
    )

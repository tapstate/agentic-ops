from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
from typing import Any, Mapping

from ao_work.installation import install_user_dir
from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult


GLOBAL = "global"
INSTALLATION = "installation"
AUTHORIZATION_MODES = (GLOBAL, INSTALLATION)
MANAGED_HEADER = "# Managed by AgenticOps developer authorization."


def authorization_paths(install_root: Path) -> dict[str, Path]:
    user = install_user_dir(install_root)
    ssh = user / "ssh"
    return {
        "user": user,
        "ssh": ssh,
        "private_key": ssh / "id_ed25519",
        "public_key": ssh / "id_ed25519.pub",
        "ssh_config": ssh / "config",
        "known_hosts": ssh / "known_hosts",
        "gh_config": user / "gh",
        "state": user / "execution-authorization.json",
        "trusted_known_hosts": install_root
        / "developer"
        / "standards"
        / "security"
        / "github-known-hosts",
    }


def authorization_change_digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def installation_ssh_command(install_root: Path) -> str:
    config = authorization_paths(install_root)["ssh_config"]
    return f"ssh -F {shlex.quote(str(config))}"


def operational_environment(
    install_root: Path,
    identity: Mapping[str, Any],
    *,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = dict(base if base is not None else os.environ)
    authorization = identity.get("execution_authorization", {})
    mode = str(authorization.get("mode", ""))
    if mode == GLOBAL:
        return environment
    if mode != INSTALLATION:
        raise _blocked(
            "install_execution_authorization_invalid",
            "安装身份中的 Git/SSH/gh 授权模式无效",
            "请运行 ao-work auth 重新配置授权模式",
        )
    paths = authorization_paths(install_root)
    validate_installation_authorization_paths(paths)
    environment.pop("SSH_AUTH_SOCK", None)
    environment["GH_CONFIG_DIR"] = str(paths["gh_config"])
    environment["GIT_SSH_COMMAND"] = installation_ssh_command(install_root)
    environment["GIT_SSH_VARIANT"] = "ssh"
    return environment


def validate_installation_authorization_paths(paths: Mapping[str, Path]) -> None:
    _require_directory(paths["user"], 0o700, "安装用户目录")
    _require_directory(paths["ssh"], 0o700, "安装 SSH 目录")
    _require_directory(paths["gh_config"], 0o700, "安装 gh 配置目录")
    _require_private_file(paths["private_key"], 0o600, "安装 SSH 私钥")
    _require_regular_file(paths["public_key"], "安装 SSH 公钥")
    _require_private_file(paths["ssh_config"], 0o600, "安装 SSH 配置")
    _require_private_file(paths["known_hosts"], 0o600, "安装 SSH known_hosts")
    if not paths["ssh_config"].read_text(encoding="utf-8").startswith(MANAGED_HEADER):
        raise _unmanaged(paths["ssh_config"])
    if not paths["known_hosts"].read_text(encoding="utf-8").startswith(MANAGED_HEADER):
        raise _unmanaged(paths["known_hosts"])


def prepare_installation_authorization(
    install_root: Path,
    *,
    github_login: str,
    allow_managed_update: bool = False,
    run_command: Any = subprocess.run,
) -> dict[str, Any]:
    """创建或幂等恢复安装级 SSH/gh 本地资产；不触碰全局授权。"""
    paths = authorization_paths(install_root)
    _preflight_existing_installation_paths(paths)
    trusted = paths["trusted_known_hosts"]
    if trusted.is_symlink() or not trusted.is_file():
        raise _blocked(
            "github_known_hosts_asset_missing",
            "缺少版本化 GitHub SSH 主机密钥资产",
            "请停止授权并修复 developer 标准资产；不得使用未校验 ssh-keyscan 结果",
        )
    _prepare_directory(paths["user"], 0o700, "安装用户目录")
    _prepare_directory(paths["ssh"], 0o700, "安装 SSH 目录")
    _prepare_directory(paths["gh_config"], 0o700, "安装 gh 配置目录")

    private_exists = paths["private_key"].exists()
    public_exists = paths["public_key"].exists()
    if private_exists != public_exists:
        raise _unmanaged(paths["private_key"] if private_exists else paths["public_key"])
    if not private_exists:
        result = run_command(
            [
                "ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                f"agentic-ops:{github_login}",
                "-f",
                str(paths["private_key"]),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            raise _blocked(
                "installation_ssh_key_generation_failed",
                "无法生成安装专属 SSH 密钥",
                "请检查 ssh-keygen 和安装目录权限；Runtime 未修改全局 SSH 配置",
            )
    _chmod(paths["private_key"], 0o600)
    _chmod(paths["public_key"], 0o600)

    known_hosts_content, ssh_config_content = _managed_contents(paths)
    _write_managed_or_same(
        paths["known_hosts"],
        known_hosts_content,
        allow_managed_update=allow_managed_update,
    )
    _write_managed_or_same(
        paths["ssh_config"],
        ssh_config_content,
        allow_managed_update=allow_managed_update,
    )
    fingerprint = public_key_fingerprint(paths["public_key"], run_command=run_command)
    return {
        "mode": INSTALLATION,
        "ssh_key_fingerprint": fingerprint,
        "ssh_config": str(paths["ssh_config"]),
        "gh_config_dir": str(paths["gh_config"]),
        "global_authorization_modified": False,
    }


def public_key_fingerprint(
    public_key: Path,
    *,
    run_command: Any = subprocess.run,
) -> str:
    result = run_command(
        ["ssh-keygen", "-l", "-E", "sha256", "-f", str(public_key)],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if result.returncode != 0:
        raise _blocked(
            "installation_ssh_key_invalid",
            "无法读取安装 SSH 公钥指纹",
            "请核对安装 SSH 公钥；Runtime 不会覆盖现有密钥",
        )
    fields = result.stdout.strip().split()
    if len(fields) < 2 or not fields[1].startswith("SHA256:"):
        raise _blocked(
            "installation_ssh_key_invalid",
            "安装 SSH 公钥指纹输出无效",
            "请核对 ssh-keygen 版本和安装 SSH 公钥",
        )
    return fields[1]


def authorization_existing_summary(
    install_root: Path,
    existing_identity: Mapping[str, Any] | None,
) -> dict[str, Any]:
    paths = authorization_paths(install_root)
    authorization = (
        dict(existing_identity.get("execution_authorization", {}))
        if existing_identity
        else {}
    )
    summary = {
        "identity": "configured" if existing_identity else "absent",
        "mode": authorization.get("mode"),
        "github_login": (
            existing_identity.get("execution_identity", {}).get("github_actor_login")
            if existing_identity
            else None
        ),
        "ssh_key_fingerprint": authorization.get("ssh_key_fingerprint") or None,
        "ssh_private_key": _path_state(paths["private_key"]),
        "ssh_config": _path_state(paths["ssh_config"]),
        "gh_config": _path_state(paths["gh_config"]),
        "global_authorization_modified": False,
    }
    for key in ("ssh_config", "known_hosts"):
        digest = _managed_file_digest(paths[key])
        if digest:
            summary[f"{key}_sha256"] = digest
    if paths["public_key"].is_file() and not paths["public_key"].is_symlink():
        try:
            summary["observed_ssh_key_fingerprint"] = public_key_fingerprint(
                paths["public_key"]
            )
        except RuntimeErrorResult:
            summary["observed_ssh_key_fingerprint"] = "invalid"
    return summary


def installation_managed_configuration_differs(install_root: Path) -> bool:
    paths = authorization_paths(install_root)
    if not paths["trusted_known_hosts"].is_file():
        return False
    known_hosts_content, ssh_config_content = _managed_contents(paths)
    return any(
        path.is_file()
        and not path.is_symlink()
        and path.read_text(encoding="utf-8").startswith(MANAGED_HEADER)
        and path.read_text(encoding="utf-8") != expected
        for path, expected in (
            (paths["known_hosts"], known_hosts_content),
            (paths["ssh_config"], ssh_config_content),
        )
    )


def _managed_contents(paths: Mapping[str, Path]) -> tuple[str, str]:
    known_hosts_content = (
        MANAGED_HEADER
        + "\n"
        + paths["trusted_known_hosts"].read_text(encoding="utf-8")
    )
    ssh_config_content = "\n".join(
        [
            MANAGED_HEADER,
            "Host github.com",
            "  HostName ssh.github.com",
            "  Port 443",
            "  User git",
            f"  IdentityFile {paths['private_key']}",
            "  IdentitiesOnly yes",
            "  IdentityAgent none",
            f"  UserKnownHostsFile {paths['known_hosts']}",
            "  StrictHostKeyChecking yes",
            "  PasswordAuthentication no",
            "  KbdInteractiveAuthentication no",
            "",
        ]
    )
    return known_hosts_content, ssh_config_content


def _managed_file_digest(path: Path) -> str | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        content = path.read_bytes()
    except OSError:
        return None
    if not content.startswith(MANAGED_HEADER.encode("utf-8")):
        return None
    return hashlib.sha256(content).hexdigest()


def _preflight_existing_installation_paths(paths: Mapping[str, Path]) -> None:
    for key, mode, label in (
        ("user", 0o700, "安装用户目录"),
        ("ssh", 0o700, "安装 SSH 目录"),
        ("gh_config", 0o700, "安装 gh 配置目录"),
    ):
        path = paths[key]
        if path.exists():
            _require_directory(path, mode, label)

    private_exists = paths["private_key"].exists()
    public_exists = paths["public_key"].exists()
    if private_exists != public_exists:
        raise _unmanaged(
            paths["private_key"] if private_exists else paths["public_key"]
        )
    if private_exists:
        _require_private_file(paths["private_key"], 0o600, "安装 SSH 私钥")
        _require_private_file(paths["public_key"], 0o600, "安装 SSH 公钥")
    for key, label in (
        ("ssh_config", "安装 SSH 配置"),
        ("known_hosts", "安装 SSH known_hosts"),
    ):
        path = paths[key]
        if not path.exists():
            continue
        _require_private_file(path, 0o600, label)
        if not path.read_text(encoding="utf-8").startswith(MANAGED_HEADER):
            raise _unmanaged(path)


def _path_state(path: Path) -> str:
    if path.is_symlink():
        return "unmanaged_conflict"
    if not path.exists():
        return "absent"
    if path.is_dir():
        return "present_directory"
    if path.is_file():
        if path.name in {"config", "known_hosts"}:
            try:
                if path.read_text(encoding="utf-8").startswith(MANAGED_HEADER):
                    return "managed"
            except OSError:
                pass
        return "present_file"
    return "unmanaged_conflict"


def _prepare_directory(path: Path, mode: int, label: str) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise _unmanaged(path)
    if not path.exists():
        path.mkdir(parents=False, mode=mode)
        os.chmod(path, mode)
        return
    actual = stat.S_IMODE(path.stat().st_mode)
    if actual != mode:
        raise _blocked(
            "existing_authorization_permissions_unsafe",
            f"{label}权限不是 {mode:o}：{path}",
            "请人工核对并收紧权限；Runtime 不会无提示修改机器已有授权目录",
        )


def _require_directory(path: Path, mode: int, label: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise _unmanaged(path)
    actual = stat.S_IMODE(path.stat().st_mode)
    if actual != mode:
        raise _blocked(
            "existing_authorization_permissions_unsafe",
            f"{label}权限不是 {mode:o}：{path}",
            "请人工核对并收紧权限；Runtime 不会无提示修改机器已有授权目录",
        )


def _require_regular_file(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise _blocked(
            "installation_authorization_incomplete",
            f"{label}缺失或不是普通文件：{path}",
            "请恢复当前安装授权；Runtime 不会回退到全局账户",
        )


def _require_private_file(path: Path, mode: int, label: str) -> None:
    _require_regular_file(path, label)
    actual = stat.S_IMODE(path.stat().st_mode)
    if actual != mode:
        raise _blocked(
            "existing_authorization_permissions_unsafe",
            f"{label}权限不是 {mode:o}：{path}",
            "请人工核对并收紧权限；Runtime 不会无提示改写凭证权限",
        )


def _write_managed_or_same(
    path: Path,
    content: str,
    *,
    allow_managed_update: bool = False,
) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise _unmanaged(path)
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing == content:
            _chmod(path, 0o600)
            return
        if not existing.startswith(MANAGED_HEADER):
            raise _unmanaged(path)
        if not allow_managed_update:
            raise _blocked(
                "existing_authorization_change_confirmation_required",
                f"已有受管授权配置与当前候选不同：{path}",
                "请先审查脱敏差异并使用绑定当前差异摘要的精确确认；Runtime 未覆盖现有配置",
            )
    temporary = path.parent / f".{path.name}.tmp"
    temporary.write_text(content, encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _chmod(path: Path, mode: int) -> None:
    if path.is_symlink() or not path.is_file():
        raise _unmanaged(path)
    os.chmod(path, mode)


def _unmanaged(path: Path) -> RuntimeErrorResult:
    return _blocked(
        "existing_authorization_unmanaged_conflict",
        f"检测到非本 Runtime 管理的已有授权路径：{path}",
        "请人工核对现有授权；普通安装和 ao-work auth 不会覆盖该路径",
    )


def _blocked(code: str, message: str, action: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=True,
        required_human_action=action,
    )

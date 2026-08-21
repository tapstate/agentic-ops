from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any

import yaml

from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult

AGENT_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]+$")
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
GITHUB_LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
EXECUTION_AUTHORIZATION_MODES = frozenset({"global", "installation"})
SSH_FINGERPRINT_PATTERN = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")

DEFAULT_REPOSITORY = "tapstate/agentic-ops"
IDENTITY_OVERRIDE_ENVS = (
    "AGENTIC_OPS_TEST_MODE",
    "AGENTIC_OPS_TEST_LAUNCHER",
    "AGENTIC_OPS_TEST_EXPECTED_REPOSITORY",
    "AGENTIC_OPS_REPO_URL",
    "AGENTIC_OPS_GITHUB_REPOSITORY",
    "AGENTIC_OPS_BRANCH",
)
SHARED_SOURCE_ASSETS = {
    "shared/README.md": ("100644", "blob"),
    "shared/integration/README.md": ("100644", "blob"),
    "shared/integration/task-to-pr-event.schema.json": ("100644", "blob"),
    "shared/integration/task-to-pr-manifest.schema.json": ("100644", "blob"),
    "shared/integration/task-to-pr-result.schema.json": ("100644", "blob"),
    "shared/standards/jira-comment-template.schema.json": ("100644", "blob"),
}
SHARED_DISTRIBUTION_ASSETS = {
    "integration/README.md",
    "integration/task-to-pr-event.schema.json",
    "integration/task-to-pr-manifest.schema.json",
    "integration/task-to-pr-result.schema.json",
    "standards/jira-comment-template.schema.json",
}
DEVELOPER_SPARSE_PATHS = {
    ".python-version",
    "developer/AGENTS.md",
    "developer/bootstrap",
    "developer/pyproject.toml",
    "developer/rules",
    "developer/runtime",
    "developer/skills",
    "developer/standards",
    "developer/uv.lock",
    "shared/integration",
    "shared/standards",
}
DEVELOPER_TOP_LEVEL_ASSETS = {
    "AGENTS.md",
    "bootstrap",
    "pyproject.toml",
    "rules",
    "runtime",
    "skills",
    "standards",
    "uv.lock",
}
DEVELOPER_FORBIDDEN_DISTRIBUTION_NAMES = {
    "test",
    "tests",
    "fixtures",
    "__pycache__",
    "task_to_pr_producer.py",
}


def default_install_root() -> Path:
    # installation/ 是包：__file__ = .../ao_work/installation/__init__.py
    # parents[0]=installation, [1]=ao_work, [2]=src, [3]=runtime, [4]=developer, [5]=root
    # 安装根 = managed clone 根（developer 的上级）。
    return Path(__file__).resolve().parents[5]


def install_user_dir(install_root: Path) -> Path:
    """研发员级配置目录：~/.agentic-ops/user/（D-048 阶段二身份/凭证承载）。"""
    user_dir = install_root / "user"
    if user_dir.is_symlink() or (user_dir.exists() and not user_dir.is_dir()):
        raise _blocked(
            "install_user_dir_invalid",
            f"研发员级配置目录被符号链接或非目录占用：{user_dir}",
            "请修复 ~/.agentic-ops/user 后重试",
        )
    return user_dir


def validate_agent_id(agent_id: str) -> str:
    value = agent_id.strip()
    if not value or not AGENT_ID_PATTERN.fullmatch(value):
        raise _blocked(
            "agent_id_invalid",
            "agent_id 只能包含字符 [0-9A-Za-z_-]",
            "请修正 agent_id 后重新执行 ao-work auth",
        )
    return value


def validate_jira_email(jira_email: str) -> str:
    value = jira_email.strip()
    if not EMAIL_PATTERN.fullmatch(value):
        raise _blocked(
            "authorization_email_invalid",
            "Jira email 格式无效",
            "请重新运行 ao-work auth 并输入当前 Jira 账户 email",
        )
    return value


def build_execution_identity(
    git_name: str,
    git_email: str,
    github_actor_login: str,
) -> dict[str, str]:
    name = git_name.strip()
    email = git_email.strip()
    login = github_actor_login.strip()
    if not name or "\x00" in name or len(name) > 256:
        raise _blocked(
            "execution_identity_invalid",
            "Git author/committer name 无效",
            "请通过 ao-work auth 确认当前研发员的 Git 姓名",
        )
    if not EMAIL_PATTERN.fullmatch(email):
        raise _blocked(
            "execution_identity_invalid",
            "Git author/committer email 格式无效",
            "请通过 ao-work auth 确认当前研发员的 Git email",
        )
    if not GITHUB_LOGIN_PATTERN.fullmatch(login):
        raise _blocked(
            "execution_identity_invalid",
            "GitHub actor login 格式无效",
            "请通过 ao-work auth 确认当前研发员的 GitHub login",
        )
    return {
        "git_author_name": name,
        "git_author_email": email,
        "git_committer_name": name,
        "git_committer_email": email,
        "github_actor_login": login,
    }


def mask_email(value: str | None) -> str | None:
    if not value or "@" not in value:
        return None
    local, domain = value.split("@", 1)
    visible = local[:2] if len(local) > 2 else local[:1]
    return f"{visible}{'*' * max(len(local) - len(visible), 1)}@{domain}"


def load_install_identity(install_root: Path) -> dict[str, Any]:
    """读取研发员级身份：~/.agentic-ops/user/identity.yaml。

    返回 {agent_id, execution_identity, jira_email}；缺失时抛 install_identity_missing。
    """
    user_dir = install_user_dir(install_root)
    identity_path = user_dir / "identity.yaml"
    if identity_path.is_symlink() or not identity_path.is_file():
        raise _blocked(
            "install_identity_missing",
            "安装目录缺少研发员身份配置",
            "请运行 ao-work auth 配置 agent_id、Git 执行身份与 Jira 账户",
        )
    try:
        payload = yaml.safe_load(identity_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        raise _blocked(
            "install_identity_invalid",
            f"研发员身份配置无法解析：{identity_path}",
            "请修复 ~/.agentic-ops/user/identity.yaml 后重试",
        ) from error
    agent_id = payload.get("agent_id")
    execution_identity = payload.get("execution_identity")
    execution_authorization = payload.get("execution_authorization")
    jira_email = payload.get("jira_email")
    if not agent_id or not isinstance(execution_identity, dict) or not jira_email:
        raise _blocked(
            "install_identity_invalid",
            "研发员身份配置缺少 agent_id、execution_identity 或 jira_email",
            "请运行 ao-work auth 重新配置",
        )
    if not isinstance(execution_authorization, dict):
        raise _blocked(
            "install_execution_authorization_upgrade_required",
            "安装身份缺少 Git/SSH/gh 授权模式",
            "请运行 ao-work auth，明确选择 global 或 installation 授权模式；Runtime 不会静默沿用或覆盖机器现有授权",
        )
    mode = str(execution_authorization.get("mode", "")).strip()
    if mode not in EXECUTION_AUTHORIZATION_MODES:
        raise _blocked(
            "install_execution_authorization_invalid",
            "安装身份中的 Git/SSH/gh 授权模式无效",
            "请运行 ao-work auth 重新配置授权模式",
        )
    ssh_key_fingerprint = str(
        execution_authorization.get("ssh_key_fingerprint", "")
    ).strip()
    if mode == "installation" and not SSH_FINGERPRINT_PATTERN.fullmatch(
        ssh_key_fingerprint
    ):
        raise _blocked(
            "install_execution_authorization_invalid",
            "安装级授权缺少有效 SSH 公钥指纹",
            "请恢复当前安装的 SSH 授权，或经明确确认后重新授权",
        )
    if mode == "global" and ssh_key_fingerprint:
        raise _blocked(
            "install_execution_authorization_invalid",
            "全局授权模式不能绑定安装级 SSH 公钥指纹",
            "请核对授权模式，不要混用全局和安装级 SSH 身份",
        )
    required_execution = {
        "git_author_name",
        "git_author_email",
        "git_committer_name",
        "git_committer_email",
        "github_actor_login",
    }
    if not required_execution.issubset(execution_identity):
        raise _blocked(
            "install_identity_invalid",
            "研发员身份 execution_identity 缺少 Git 身份四字段或 github_actor_login",
            "请运行 ao-work auth 重新配置",
        )
    return {
        "agent_id": agent_id,
        "execution_identity": execution_identity,
        "execution_authorization": {
            "mode": mode,
            "ssh_key_fingerprint": ssh_key_fingerprint,
        },
        "jira_email": jira_email,
    }


def install_identity_ref(identity: dict[str, Any]) -> str:
    """生成工作空间绑定使用的唯一安装身份摘要。"""
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "agent_id": identity["agent_id"],
                "jira_email": identity["jira_email"],
                "execution_identity": identity["execution_identity"],
                "execution_authorization": identity["execution_authorization"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"install:{fingerprint}"


def save_install_identity(install_root: Path, identity: dict[str, Any]) -> None:
    """原子写入研发员级身份：~/.agentic-ops/user/identity.yaml（0600）。"""
    user_dir = install_user_dir(install_root)
    user_dir.mkdir(parents=True, exist_ok=True)
    identity_path = user_dir / "identity.yaml"
    _atomic_write_private(identity_path, yaml.safe_dump(identity, allow_unicode=True, sort_keys=False))


def load_install_credentials(install_root: Path) -> tuple[str, str] | None:
    """读取研发员级 Jira 凭证：~/.agentic-ops/user/.env（email 已在 identity.yaml）。

    返回 (email, token) 或 None（未配置）。
    """
    user_dir = install_user_dir(install_root)
    env_path = user_dir / ".env"
    if env_path.is_symlink() or not env_path.is_file():
        return None
    try:
        content = env_path.read_text(encoding="utf-8")
    except OSError:
        return None
    values: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    email = values.get("TAPDATA_JIRA_EMAIL")
    token = values.get("TAPDATA_JIRA_API_TOKEN")
    if not email or not token:
        return None
    return email, token


def save_install_credentials(install_root: Path, email: str, token: str) -> None:
    """原子写入研发员级 Jira 凭证：~/.agentic-ops/user/.env（0600）。"""
    user_dir = install_user_dir(install_root)
    user_dir.mkdir(parents=True, exist_ok=True)
    env_path = user_dir / ".env"
    _atomic_write_private(
        env_path,
        f"TAPDATA_JIRA_EMAIL={email}\nTAPDATA_JIRA_API_TOKEN={token}\n",
    )


def _atomic_write_private(path: Path, content: str) -> None:
    """原子写 + 0600 权限（复用既有 update_env_file 思路，独立实现避免跨面）。"""
    directory = path.parent
    temporary = directory / f".{path.name}.tmp"
    try:
        temporary.write_text(content, encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise _blocked(
            "install_identity_write_failed",
            f"无法写入研发员级配置：{path}",
            "请检查 ~/.agentic-ops/user 目录权限后重试",
        ) from error


def validate_install_root() -> Path:
    _reject_identity_overrides()
    root = default_install_root()
    if not root.is_dir():
        raise _blocked(
            "install_root_not_found",
            f"AgenticOps developer 安装根目录不存在：{root}",
            "请通过 developer/bootstrap/install.sh 重新安装 AgenticOps",
        )
    if (root / ".agentic-ops-source").exists() or (root / "maintainer").exists():
        raise _blocked(
            "install_root_source_rejected",
            f"当前 ao_work 模块位于 AgenticOps 源头或包含 maintainer 资产：{root}",
            "请使用 developer-only managed clone 中安装的 ao-work",
        )
    if not (root / ".git").exists():
        raise _blocked(
            "install_root_identity_invalid",
            f"AgenticOps developer 安装缺少 managed clone 身份：{root}",
            "请通过 developer/bootstrap/install.sh 重新安装，不得使用仿造目录",
        )
    for required in (
        root / "developer" / "AGENTS.md",
        root / "developer" / "rules" / "ai-execution.md",
        root / "developer" / "runtime" / "src" / "ao_work" / "__init__.py",
        root / "shared" / "integration" / "README.md",
        root / "shared" / "integration" / "task-to-pr-manifest.schema.json",
        root / "shared" / "integration" / "task-to-pr-event.schema.json",
        root / "shared" / "integration" / "task-to-pr-result.schema.json",
    ):
        if required.is_symlink() or not required.is_file():
            raise _blocked(
                "install_root_identity_invalid",
                f"AgenticOps developer 安装资产不完整：{required}",
                "请通过 developer/bootstrap/install.sh 重新安装",
            )

    origins = _run_git(root, "config", "--get-all", "remote.origin.url").splitlines()
    effective_origins = _run_git(root, "remote", "get-url", "--all", "origin").splitlines()
    effective_pushes = _run_git(
        root, "remote", "get-url", "--push", "--all", "origin"
    ).splitlines()
    origin = origins[0] if len(origins) == 1 else ""
    if (
        len(origins) != 1
        or len(effective_origins) != 1
        or len(effective_pushes) != 1
        or not _repository_matches(origin)
    ):
        raise _blocked(
            "install_origin_mismatch",
            f"AgenticOps managed clone origin 不是受信仓库：{origin or '未配置'}",
            f"请重新安装并确保 origin 指向 {DEFAULT_REPOSITORY}",
        )
    if (
        not _repository_matches(effective_origins[0])
        or not _repository_matches(effective_pushes[0])
        or _normalize_repository_url(origin)
        != _normalize_repository_url(effective_origins[0])
        or _normalize_repository_url(origin)
        != _normalize_repository_url(effective_pushes[0])
    ):
        raise _blocked(
            "install_transport_rewrite_forbidden",
            "AgenticOps managed clone 的实际 fetch 或 push 地址被 Git 配置改写",
            "请移除 url.*.insteadOf、pushInsteadOf 或 remote pushurl 后重新安装",
        )

    sparse = _run_git(root, "sparse-checkout", "list")
    sparse_paths = {
        line.strip().strip("/")
        for line in sparse.splitlines()
        if line.strip().strip("/")
    }
    if sparse_paths != DEVELOPER_SPARSE_PATHS:
        raise _blocked(
            "developer_sparse_checkout_invalid",
            "AgenticOps managed clone 不是固定的 developer-only sparse checkout",
            "请通过 developer/bootstrap/install.sh 重新安装",
        )
    _validate_shared_source_tree(root, "HEAD")
    _validate_shared_distribution(root)
    _validate_developer_distribution(root)
    if _is_verification_install(root):
        _validate_verification_checkout_integrity(root)
    else:
        _validate_checkout_integrity(root)
    return root


def _reject_identity_overrides() -> None:
    configured = sorted(name for name in IDENTITY_OVERRIDE_ENVS if os.environ.get(name))
    if configured:
        raise _blocked(
            "install_identity_override_forbidden",
            "AgenticOps 安装身份固定为 tapstate/agentic-ops 的 main，"
            f"不能通过环境变量覆盖：{', '.join(configured)}",
            "请移除安装身份覆盖环境变量后重试",
        )


def _run_git(root: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise _blocked(
            "install_identity_check_failed",
            f"无法校验 AgenticOps managed clone：{type(error).__name__}",
            "请检查 Git 安装和 ~/.agentic-ops 后重试",
        ) from error
    if result.returncode != 0:
        raise _blocked(
            "install_identity_check_failed",
            "无法读取 AgenticOps managed clone 的 Git 身份",
            "请通过 developer/bootstrap/install.sh 重新安装",
        )
    return result.stdout.strip()


def _repository_matches(origin: str) -> bool:
    normalized = _normalize_repository_url(origin)
    return normalized in {
        f"git@github.com:{DEFAULT_REPOSITORY}",
        f"ssh://git@github.com/{DEFAULT_REPOSITORY}",
        f"https://github.com/{DEFAULT_REPOSITORY}",
    }


def _normalize_repository_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    return normalized


def _validate_checkout_integrity(root: Path) -> None:
    head = _run_git(root, "rev-parse", "--verify", "HEAD")
    current_ref = root / ".local" / "current-ref"
    try:
        recorded = current_ref.read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, IndexError) as error:
        raise _blocked(
            "install_ref_integrity_invalid",
            "AgenticOps developer 安装缺少有效的 .local/current-ref",
            "请通过 developer/bootstrap/install.sh 重新安装",
        ) from error
    if recorded != head:
        raise _blocked(
            "install_ref_integrity_invalid",
            "AgenticOps checkout 的 HEAD 与 .local/current-ref 不一致",
            "请停止使用该目录，并通过正式 Bootstrap 重新安装或完成受控回滚",
        )
    _run_git(root, "cat-file", "-e", "refs/remotes/origin/main^{commit}")
    _run_git(root, "merge-base", "--is-ancestor", head, "refs/remotes/origin/main")
    tracked = _run_git(root, "status", "--porcelain=v1", "--untracked-files=no")
    if tracked:
        raise _blocked(
            "install_tracked_changes_forbidden",
            "AgenticOps developer 安装中的受管文件存在本地修改",
            "请不要修改 ~/.agentic-ops；通过业务反馈流程改进后更新稳定 main",
        )
    for asset in (
        "developer/AGENTS.md",
        "developer/bootstrap/ao-work",
        "developer/runtime/src/ao_work/__init__.py",
        "shared/integration/README.md",
        "shared/integration/task-to-pr-manifest.schema.json",
        "shared/integration/task-to-pr-event.schema.json",
        "shared/integration/task-to-pr-result.schema.json",
    ):
        _run_git(root, "cat-file", "-e", f"HEAD:{asset}")


VERIFICATION_MARKER = ".agentic-ops/verification-only"


def _is_verification_install(root: Path) -> bool:
    marker = root / VERIFICATION_MARKER
    return not marker.is_symlink() and marker.is_file()


def _validate_verification_checkout_integrity(root: Path) -> None:
    head = _run_git(root, "rev-parse", "--verify", "HEAD")
    current_ref = root / ".local" / "current-ref"
    try:
        recorded = current_ref.read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, IndexError) as error:
        raise _blocked(
            "install_ref_integrity_invalid",
            "AgenticOps 验证安装缺少有效的 .local/current-ref",
            "请重新执行 developer/bootstrap/install-verify-branch.sh 验证安装",
        ) from error
    if recorded != head:
        raise _blocked(
            "install_ref_integrity_invalid",
            "AgenticOps 验证安装 checkout 的 HEAD 与 .local/current-ref 不一致",
            "请停止使用该目录并重新执行验证安装",
        )
    if not _verification_head_reachable(root, head):
        raise _blocked(
            "verification_branch_unreachable",
            "AgenticOps 验证安装的 HEAD 不可达于任一 origin 远端分支或 tag",
            "请确认指定分支已推送到 tapstate/agentic-ops 后重新执行验证安装",
        )
    tracked = _run_git(root, "status", "--porcelain=v1", "--untracked-files=no")
    if tracked:
        raise _blocked(
            "install_tracked_changes_forbidden",
            "AgenticOps 验证安装中的受管文件存在本地修改",
            "请不要修改验证安装目录；通过业务反馈流程改进后重新安装",
        )
    for asset in (
        "developer/AGENTS.md",
        "developer/bootstrap/ao-work",
        "developer/runtime/src/ao_work/__init__.py",
        "shared/integration/README.md",
        "shared/integration/task-to-pr-manifest.schema.json",
        "shared/integration/task-to-pr-event.schema.json",
        "shared/integration/task-to-pr-result.schema.json",
    ):
        _run_git(root, "cat-file", "-e", f"HEAD:{asset}")


def _verification_head_reachable(root: Path, head: str) -> bool:
    refs = _run_git(
        root, "for-each-ref", "--format=%(refname)", "refs/remotes/origin"
    ).splitlines()
    for ref in refs:
        if not ref:
            continue
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "merge-base", "--is-ancestor", head, ref],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise _blocked(
                "install_identity_check_failed",
                f"无法校验 AgenticOps 验证安装远端可达性：{type(error).__name__}",
                "请检查 Git 安装后重试",
            ) from error
        if result.returncode == 0:
            return True
    return False


def _validate_shared_source_tree(root: Path, ref: str) -> None:
    actual: dict[str, tuple[str, str]] = {}
    for entry in _run_git(root, "ls-tree", "-r", ref, "--", "shared").splitlines():
        metadata, separator, path = entry.partition("\t")
        fields = metadata.split()
        if separator != "\t" or len(fields) != 3 or path in actual:
            raise _shared_source_invalid()
        mode, object_type, _object_id = fields
        actual[path] = (mode, object_type)
    if actual != SHARED_SOURCE_ASSETS:
        raise _shared_source_invalid()


def _validate_shared_distribution(root: Path) -> None:
    shared = root / "shared"
    if shared.is_symlink() or not shared.is_dir():
        raise _shared_distribution_invalid()

    actual_directories: set[str] = set()
    actual_files: set[str] = set()
    for current, directories, files in os.walk(shared, followlinks=False):
        current_path = Path(current)
        for name in directories:
            path = current_path / name
            if path.is_symlink() or not path.is_dir():
                raise _shared_distribution_invalid()
            actual_directories.add(path.relative_to(shared).as_posix())
        for name in files:
            path = current_path / name
            if path.is_symlink() or not path.is_file():
                raise _shared_distribution_invalid()
            try:
                mode = path.stat().st_mode
            except OSError as error:
                raise _shared_distribution_invalid() from error
            if mode & 0o111:
                raise _shared_distribution_invalid()
            actual_files.add(path.relative_to(shared).as_posix())

    expected_directories = {
        str(Path(item).parent) for item in SHARED_DISTRIBUTION_ASSETS
    }
    if actual_directories != expected_directories or actual_files != SHARED_DISTRIBUTION_ASSETS:
        raise _shared_distribution_invalid()


def _validate_developer_distribution(root: Path) -> None:
    developer = root / "developer"
    if developer.is_symlink() or not developer.is_dir():
        raise _developer_distribution_invalid()

    actual_top_level = {
        path.name for path in developer.iterdir() if path.name != ".venv"
    }
    if actual_top_level != DEVELOPER_TOP_LEVEL_ASSETS:
        raise _developer_distribution_invalid(contaminated=True)

    for required in DEVELOPER_TOP_LEVEL_ASSETS:
        path = developer / required
        if path.is_symlink() or not path.exists():
            raise _developer_distribution_invalid()

    for current, directories, files in os.walk(developer, followlinks=False):
        current_path = Path(current)
        if current_path == developer:
            directories[:] = [name for name in directories if name != ".venv"]
        for name in (*directories, *files):
            path = current_path / name
            lowered = name.lower()
            if (
                path.is_symlink()
                or lowered in DEVELOPER_FORBIDDEN_DISTRIBUTION_NAMES
                or lowered.endswith((".pyc", ".pyo"))
                or ("fake" in lowered and "producer" in lowered)
            ):
                raise _developer_distribution_invalid(contaminated=True)


def _developer_distribution_invalid(
    *, contaminated: bool = False
) -> RuntimeErrorResult:
    if contaminated:
        return _blocked(
            "developer_distribution_contaminated",
            "AgenticOps developer 安装混入测试、fixture、fake producer、缓存、符号链接或非生产资产",
            "请停止使用该安装目录并重新安装；测试资产只能保留在源头仓库",
        )
    return _blocked(
        "developer_distribution_invalid",
        "AgenticOps developer 安装的生产资产不完整或类型不安全",
        "请通过 developer/bootstrap/install.sh 重新安装",
    )


def _shared_source_invalid() -> RuntimeErrorResult:
    return _blocked(
        "developer_shared_source_invalid",
        "AgenticOps 提交中的 shared 资产超出固定只读协议白名单，或包含不安全文件类型/权限",
        "请停止使用该版本；由项目维护者移除非准入路径、可执行位、脚本或 AI 入口",
    )


def _shared_distribution_invalid() -> RuntimeErrorResult:
    return _blocked(
        "developer_shared_distribution_invalid",
        "AgenticOps developer 安装中的 shared 可见树超出固定只读协议白名单",
        "请停止使用该安装目录并通过 developer/bootstrap/install.sh 重新安装",
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

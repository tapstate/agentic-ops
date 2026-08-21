from __future__ import annotations

import hashlib
import json
import shlex
import shutil
import ssl
import subprocess
import sys
import threading
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from ao_maint.integration.model import IntegrationManifest
from ao_maint.output import EXIT_BLOCKED, RuntimeErrorResult


@dataclass
class FakeJiraState:
    issue_key: str
    project_key: str
    comments: list[dict[str, Any]] = field(default_factory=list)


class FakeJiraServer(AbstractContextManager["FakeJiraServer"]):
    def __init__(
        self,
        issue_key: str,
        project_key: str,
        certificate: Path,
        private_key: Path,
    ) -> None:
        self.state = FakeJiraState(issue_key, project_key)
        handler = _handler(self.state)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, private_key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address
        return f"https://{host}:{port}"

    def __enter__(self) -> "FakeJiraServer":
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class OfflineFakeRunner:
    def __init__(self, manifest: IntegrationManifest, sandbox: Path) -> None:
        self.manifest = manifest
        self.sandbox = sandbox
        self.home = sandbox / "home"
        self.install_root = sandbox / "install"
        self.workspace = sandbox / "business-workspace"
        self.source_checkout = sandbox / "business-source"
        self.tool_bin = sandbox / "tools"
        self.certificate = sandbox / "offline-jira-ca.pem"
        self.private_key = sandbox / "offline-jira-key.pem"
        self.steps: list[dict[str, Any]] = []
        digest = hashlib.sha256(manifest.path.read_bytes()).hexdigest()[:20]
        self.run_id = f"integration-{digest}"

    def run(self) -> dict[str, Any]:
        self._prepare_isolation()
        self._prepare_loopback_tls()
        with FakeJiraServer(
            self.manifest.issue_key,
            self.manifest.jira_project_key,
            self.certificate,
            self.private_key,
        ) as jira:
            # 离线连接必须在 distribution 提交前固化。若安装完成后再改
            # managed clone，生产完整性门禁会（正确地）拒绝受污染安装。
            distribution = self._create_distribution_repository(jira.base_url)
            self._configure_isolated_git(distribution)
            self._deploy(distribution)
            self._configure_pool_root()
            self._configure_authorization()
            self._initialize_workspace()
            self._initialize_fixture_task_state()
            self._write_task_reports()
            self._run_verification()
            completion_plan = self._complete_fixture_task()
            comment_readback = self._ao_work(
                "jira",
                "comment",
                "readback",
                "--issue-key",
                self.manifest.issue_key,
                "--idempotency-key",
                f"{self.run_id}:completion",
                "--plan-file",
                completion_plan["plan_file"],
                "--confirm-plan-id",
                completion_plan["plan_id"],
            )
            final_issue = self._ao_work("jira", "inspect", "--issue-key", self.manifest.issue_key)
            issue_status = str(dict(final_issue["issue"])["status"])
            if issue_status != "正在进行":
                raise self._step_error(
                    "offline_fixture_state_invalid",
                    "Offline Fake Jira 状态被意外修改；烟测不得冒充 transition",
                    "fixture_evidence_readback",
                )
            final_task = self._ao_work("task", "inspect", "--issue-key", self.manifest.issue_key)
            external_writes = dict(final_task["sync"]).get("external_writes", {})
            if not external_writes:
                raise self._step_error(
                    "offline_fixture_readback_failed",
                    "本地任务状态没有记录离线证据评论写入回读",
                    "fixture_evidence_readback",
                )
            self._record(
                "fixture_evidence_readback",
                issue_status=issue_status,
                comment_external_id=str(comment_readback["external_id"]),
                transition_attempted=False,
                synced=True,
            )

        return {
            "adapter": "offline_fake",
            "adapter_scope": "隔离的离线 Fake Jira 合同测试，不代表真实 Jira 任务已完成",
            "issue_key": self.manifest.issue_key,
            "agentic_run_id": self.run_id,
            "task_completion": "offline_fixture_completed",
            "production_jira_completed": False,
            "steps": self.steps,
            "isolation": {
                "home": "temporary_and_removed",
                "install_root": "temporary_and_removed",
                "business_workspace": "temporary_and_removed",
                "host_credentials_inherited": False,
            },
        }

    def _prepare_isolation(self) -> None:
        for directory in (self.home, self.workspace, self.tool_bin, self.sandbox / "tmp"):
            directory.mkdir(parents=True, exist_ok=True)
        fake_uv = self.tool_bin / "uv"
        python = shlex.quote(sys.executable)
        fake_uv.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "project=\n"
            "while [ \"$#\" -gt 0 ]; do\n"
            "  if [ \"$1\" = \"--project\" ]; then project=$2; shift 2; else shift; fi\n"
            "done\n"
            "test -n \"$project\"\n"
            "mkdir -p \"$project/.venv/bin\"\n"
            "cat > \"$project/.venv/bin/python\" <<'PYTHON_WRAPPER'\n"
            "#!/bin/sh\n"
            f"exec {python} \"$@\"\n"
            "PYTHON_WRAPPER\n"
            "chmod 700 \"$project/.venv/bin/python\"\n",
            encoding="utf-8",
        )
        fake_uv.chmod(0o700)
        (self.home / ".gitconfig").write_text(
            "[user]\n"
            "\tname = Offline Integration Agent\n"
            "\temail = offline-agent@example.invalid\n",
            encoding="utf-8",
        )
        fake_gh = self.tool_bin / "gh"
        github_login = self.manifest.agent_id.replace("_", "-")
        fake_gh.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "if [ \"${1:-}\" = api ] && [ \"${2:-}\" = user ]; then\n"
            f"  printf '%s\\n' {shlex.quote(github_login)}\n"
            "  exit 0\n"
            "fi\n"
            "printf 'offline gh only supports api user\\n' >&2\n"
            "exit 2\n",
            encoding="utf-8",
        )
        fake_gh.chmod(0o700)
        self._record("isolation_prepared", inherited_environment=False)

    def _prepare_loopback_tls(self) -> None:
        openssl = shutil.which("openssl")
        if openssl is None:
            raise self._step_error(
                "integration_tls_dependency_missing",
                "Offline Fake HTTPS 夹具缺少 openssl",
                "offline_tls_prepare",
            )
        self._process(
            "offline_tls_prepare",
            [
                openssl,
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(self.private_key),
                "-out",
                str(self.certificate),
                "-days",
                "1",
                "-subj",
                "/CN=AgenticOps Offline Jira",
                "-addext",
                "subjectAltName=IP:127.0.0.1",
                "-addext",
                "basicConstraints=critical,CA:TRUE",
                "-addext",
                "keyUsage=critical,keyCertSign,digitalSignature,keyEncipherment",
            ],
            cwd=self.sandbox,
        )
        self.private_key.chmod(0o600)

    def _create_distribution_repository(self, jira_base_url: str) -> Path:
        source = self.manifest.agentic_ops.repository
        developer = source / "developer"
        shared = source / "shared"
        required_protocols = (
            "task-to-pr-manifest.schema.json",
            "task-to-pr-event.schema.json",
            "task-to-pr-result.schema.json",
        )
        if not (developer / "AGENTS.md").is_file() or not (
            developer / "bootstrap" / "install.sh"
        ).is_file() or any(
            not (shared / "integration" / name).is_file()
            for name in required_protocols
        ):
            raise self._step_error(
                "integration_source_invalid",
                "agentic_ops.repository 缺少 developer 工作面或 shared 集成协议",
                "distribution_prepare",
            )
        distribution = self.sandbox / "distribution-source"
        shutil.copytree(
            developer,
            distribution / "developer",
            ignore=shutil.ignore_patterns(".venv", "__pycache__", "*.pyc"),
        )
        shutil.copytree(
            shared,
            distribution / "shared",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        version = source / ".python-version"
        (distribution / ".python-version").write_text(
            version.read_text(encoding="utf-8") if version.is_file() else "3.12\n",
            encoding="utf-8",
        )
        self._configure_offline_distribution(distribution, jira_base_url)
        self._process(
            "distribution_git_init",
            ["/usr/bin/git", "init", "--initial-branch", "main", str(distribution)],
            cwd=self.sandbox,
        )
        self._process(
            "distribution_git_add",
            ["/usr/bin/git", "-C", str(distribution), "add", "."],
            cwd=self.sandbox,
        )
        self._process(
            "distribution_git_commit",
            [
                "/usr/bin/git",
                "-C",
                str(distribution),
                "-c",
                "user.name=AgenticOps Offline Integration",
                "-c",
                "user.email=offline-integration@example.invalid",
                "commit",
                "-m",
                "offline integration distribution",
            ],
            cwd=self.sandbox,
        )
        return distribution

    def _configure_isolated_git(self, distribution: Path) -> None:
        repository = self.manifest.task_repository.repository
        if not repository.is_dir():
            raise self._step_error(
                "integration_task_repository_invalid",
                "task_repository.repository 不存在或不是目录",
                "task_repository_preflight",
            )
        self._process(
            "task_repository_preflight",
            ["/usr/bin/git", "-C", str(repository), "rev-parse", "--is-inside-work-tree"],
            cwd=self.sandbox,
        )
        self._process(
            "task_ref_preflight",
            [
                "/usr/bin/git",
                "-C",
                str(repository),
                "rev-parse",
                "--verify",
                f"{self.manifest.task_repository.ref}^{{commit}}",
            ],
            cwd=self.sandbox,
        )
        slug = self.manifest.task_repository.slug
        assert slug is not None
        mirror = self.sandbox / "github.com" / slug
        mirror.parent.mkdir(parents=True, exist_ok=True)
        self._process(
            "task_repository_mirror",
            ["/usr/bin/git", "clone", "--mirror", str(repository), str(mirror)],
            cwd=self.sandbox,
        )
        self._write_git_transport_wrapper(distribution, mirror, slug)
        self._record(
            "isolated_git_transport_prepared",
            persistent_git_rewrite=False,
            product_identity_override=False,
        )

    def _write_git_transport_wrapper(
        self, distribution: Path, mirror: Path, slug: str
    ) -> None:
        wrapper = self.tool_bin / "git"
        real_git = shutil.which("git", path="/usr/bin:/bin:/usr/sbin:/sbin")
        if real_git is None:
            raise self._step_error(
                "integration_git_dependency_missing",
                "Offline Fake 找不到受信 Git 可执行文件",
                "isolated_git_transport_prepare",
            )
        agentic_mapping = (
            f"url.{distribution}.insteadOf=git@github.com:tapstate/agentic-ops.git"
        )
        task_mapping = f"url.{mirror}.insteadOf=git@github.com:{slug}.git"
        wrapper.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "subcommand=\n"
            "skip_next=0\n"
            "for argument in \"$@\"; do\n"
            "  if [ \"$skip_next\" = 1 ]; then skip_next=0; continue; fi\n"
            "  case \"$argument\" in\n"
            "    -C|-c|--git-dir|--work-tree) skip_next=1 ;;\n"
            "    --git-dir=*|--work-tree=*|-c*|-*) ;;\n"
            "    *) subcommand=$argument; break ;;\n"
            "  esac\n"
            "done\n"
            "if [ \"$subcommand\" = ls-remote ] && [ \"${2:-}\" = --get-url ]; then\n"
            f"  exec {shlex.quote(real_git)} \"$@\"\n"
            "fi\n"
            "case \"$subcommand\" in\n"
            "  clone|fetch|ls-remote|push)\n"
            f"    exec {shlex.quote(real_git)} "
            f"-c {shlex.quote(agentic_mapping)} -c {shlex.quote(task_mapping)} \"$@\"\n"
            "    ;;\n"
            f"  *) exec {shlex.quote(real_git)} \"$@\" ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o700)

    def _deploy(self, distribution: Path) -> None:
        script = distribution / "developer" / "bootstrap" / "install.sh"
        environment = self._environment(
            {
                "AGENTIC_OPS_HOME": str(self.install_root),
                "AGENTIC_OPS_UV": str(self.tool_bin / "uv"),
            }
        )
        self._process(
            "developer_bootstrap_install",
            ["/bin/bash", str(script)],
            cwd=distribution,
            env=environment,
            json_output=True,
        )
        if (self.install_root / "maintainer").exists():
            raise self._step_error(
                "integration_workplane_contaminated",
                "隔离 developer 安装中出现 maintainer 资产",
                "developer_bootstrap_install",
            )

    def _configure_offline_distribution(self, distribution: Path, base_url: str) -> None:
        profile_path = (
            distribution
            / "developer"
            / "standards"
            / "projects"
            / self.manifest.project_profile
            / "profile.yaml"
        )
        connection_path = (
            distribution
            / "developer"
            / "standards"
            / "connections"
            / "offline-jira.yaml"
        )
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        connection_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "profile_id": self.manifest.project_profile,
                    "connection_id": "offline-jira",
                    "jira": {
                        "project_key": self.manifest.jira_project_key,
                        "issue_types": ["Task"],
                        "task_query": f"project = {self.manifest.jira_project_key}",
                    },
                    "repositories": {"default": self.manifest.task_repository.slug},
                    "fields": {
                        "owner": {
                            "source": "jira_field",
                            "jira_field": "assignee",
                            "state": "read_only",
                            "required": True,
                        }
                    },
                    "statuses": {"正在进行": "implementation", "完成": "completed"},
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        connection_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "connection_id": "offline-jira",
                    "base_url": base_url,
                    "timeout_seconds": 5,
                    "auth": {
                        "type": "basic_api_token",
                        "email_env": "OFFLINE_JIRA_EMAIL",
                        "token_env": "OFFLINE_JIRA_API_TOKEN",
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        self._record("offline_adapter_configured", base_url="loopback_ephemeral")

    def _configure_pool_root(self) -> None:
        """隔离安装写入研发员级 source_pool_root（D-048 池根必配）。"""
        user_dir = self.install_root / "user"
        user_dir.mkdir(parents=True, exist_ok=True)
        pool_root = self.sandbox / "source-pool"
        pool_root.mkdir(parents=True, exist_ok=True)
        config = user_dir / "config.yaml"
        config.write_text(f"source_pool_root: {pool_root}\n", encoding="utf-8")

    def _configure_authorization(self) -> None:
        self._ao_work(
            "auth",
            "--agent-id",
            self.manifest.agent_id,
            "--jira-email",
            "offline-agent@example.invalid",
            "--git-name",
            "Offline Integration Agent",
            "--git-email",
            "offline-agent@example.invalid",
            "--github-login",
            self.manifest.agent_id.replace("_", "-"),
            "--execution-auth-mode",
            "global",
            "--token-stdin",
            "--non-interactive",
            input_text="synthetic-offline-token\n",
        )

    def _initialize_workspace(self) -> None:
        self._ao_work(
            "workspace",
            "init",
            "--project",
            self.manifest.project_profile,
            "--source-root",
            str(self.source_checkout),
            "--non-interactive",
        )
        self._process(
            "task_ref_checkout",
            [
                "git",
                "-C",
                str(self.source_checkout),
                "fetch",
                "origin",
                self.manifest.task_repository.ref,
            ],
            cwd=self.workspace,
        )
        self._process(
            "task_ref_detach",
            [
                "git",
                "-C",
                str(self.source_checkout),
                "checkout",
                "--detach",
                "FETCH_HEAD",
            ],
            cwd=self.workspace,
        )
        self._ao_work("workspace", "inspect")

    def _initialize_fixture_task_state(self) -> None:
        issue = self._ao_work("jira", "inspect", "--issue-key", self.manifest.issue_key)
        issue_payload = dict(issue["issue"])
        self._ao_work(
            "task",
            "init",
            "--connection-id",
            "offline-jira",
            "--jira-issue-id",
            str(issue_payload["jira_issue_id"]),
            "--issue-key",
            self.manifest.issue_key,
            "--project-key",
            self.manifest.jira_project_key,
            "--agentic-run-id",
            self.run_id,
        )

    def _write_task_reports(self) -> None:
        input_directory = self.workspace / "test-inputs"
        input_directory.mkdir()
        for kind, content in (
            (
                "analysis",
                "# 测试任务分析\n\nOffline Fake 已建立合成任务状态；这不代表正式接管或真实 Jira 完成。\n",
            ),
            ("plan", "# 测试实施计划\n\n执行显式验证命令并回写完成证据。\n"),
        ):
            path = input_directory / f"{kind}.md"
            path.write_text(content, encoding="utf-8")
            self._ao_work(
                "report",
                "write",
                "--issue-key",
                self.manifest.issue_key,
                "--agentic-run-id",
                self.run_id,
                "--kind",
                kind,
                "--content-file",
                path.relative_to(self.workspace).as_posix(),
            )

    def _run_verification(self) -> None:
        for index, (recipe, arguments) in enumerate(
            self.manifest.verification_commands, start=1
        ):
            command = self._verification_command(recipe, arguments)
            self._process(
                f"verification_{index}",
                command,
                cwd=self.source_checkout,
            )

    def _verification_command(
        self, recipe: str, arguments: tuple[str, ...]
    ) -> list[str]:
        if recipe == "python_unittest":
            return [
                sys.executable,
                "-m",
                "unittest",
                *self._python_unittest_arguments(arguments),
            ]
        if recipe == "shell_script":
            script = self._verification_path(arguments[0])
            return ["/bin/bash", str(script), *arguments[1:]]
        if recipe == "executable":
            executable = self._verification_path(arguments[0])
            if not executable.is_file() or not executable.stat().st_mode & 0o100:
                raise self._step_error(
                    "integration_verification_invalid",
                    "executable recipe 必须指向业务源码内的可执行文件",
                    "verification_prepare",
                )
            return [str(executable), *arguments[1:]]
        raise self._step_error(
            "integration_verification_invalid",
            f"未知验证 recipe：{recipe}",
            "verification_prepare",
        )

    def _python_unittest_arguments(self, arguments: tuple[str, ...]) -> list[str]:
        # unittest 的 -s/-t/-p 与显式模块名都能间接加载宿主机路径；离线合同回归
        # 只接受业务 checkout 内的相对 test 文件，并把它转换为模块名。
        modules: list[str] = []
        for value in arguments:
            if value.startswith("-"):
                raise self._step_error(
                    "integration_verification_path_escape",
                    "python_unittest recipe 不接受 discover 或路径控制参数",
                    "verification_prepare",
                )
            test_file = self._verification_path(value)
            if test_file.suffix != ".py":
                raise self._step_error(
                    "integration_verification_invalid",
                    "python_unittest recipe 只接受业务源码内的相对 .py 测试文件",
                    "verification_prepare",
                )
            relative = test_file.relative_to(self.source_checkout.resolve())
            modules.append(".".join(relative.with_suffix("").parts))
        return modules

    def _verification_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise self._step_error(
                "integration_verification_path_escape",
                "验证 recipe 路径必须是业务源码根内的相对路径",
                "verification_prepare",
            )
        resolved = (self.source_checkout / path).resolve()
        try:
            resolved.relative_to(self.source_checkout.resolve())
        except ValueError as error:
            raise self._step_error(
                "integration_verification_path_escape",
                "验证 recipe 路径越出业务源码根",
                "verification_prepare",
            ) from error
        if not resolved.is_file():
            raise self._step_error(
                "integration_verification_invalid",
                f"验证文件不存在：{value}",
                "verification_prepare",
            )
        return resolved

    def _complete_fixture_task(self) -> dict[str, str]:
        input_directory = self.workspace / "test-inputs"
        input_directory.mkdir(exist_ok=True)
        content = input_directory / "completion-evidence.md"
        content.write_text(
            "- 运行 ID: <agentic_run_id>\n"
            "- 工作空间: 离线烟测工作空间\n"
            "- 执行者: 研发工程师\n"
            "- 任务类型: 集成烟测\n"
            "- 当前阶段: verification\n"
            "- 下一步: 交付结果包\n"
            "- 完成内容: Offline Fake 烟测完成：隔离部署、工作空间初始化、合成任务状态、固定验证与评论回读均通过；不代表正式接管或真实 Jira 完成。\n"
            "- 验证结果: 固定验证通过，评论回读 external_id 一致\n"
            "- 残留风险: 离线 Fake Jira 合同测试，不代表真实 Jira 任务已完成\n"
            "- 已输出表单字段: completion-evidence 评论\n",
            encoding="utf-8",
        )
        relative_plan = (
            f".agentic-ops/tasks/{self.manifest.issue_key}/runs/{self.run_id}/"
            "jira-plans/completion-comment.json"
        )
        plan = self._ao_work(
            "jira",
            "comment",
            "plan",
            "--issue-key",
            self.manifest.issue_key,
            "--idempotency-key",
            f"{self.run_id}:completion",
            "--plan-file",
            relative_plan,
            "--category",
            "evidence",
            "--content-file",
            content.relative_to(self.workspace).as_posix(),
        )
        plan_file = str(plan["plan_file"])
        plan_id = str(plan["plan_id"])
        expected_plan_directory = (
            self.workspace
            / ".agentic-ops"
            / "tasks"
            / self.manifest.issue_key
            / "runs"
            / self.run_id
            / "jira-plans"
        ).resolve()
        try:
            plan_path = Path(plan_file).resolve(strict=True)
            plan_path.relative_to(expected_plan_directory)
        except (OSError, ValueError) as error:
            raise self._step_error(
                "offline_fixture_plan_contract_invalid",
                "Offline Fake Jira plan 输出未绑定当前任务运行的 jira-plans 目录",
                "fixture_jira_plan_contract",
            ) from error
        if plan_path.parent != expected_plan_directory or not plan_id:
            raise self._step_error(
                "offline_fixture_plan_contract_invalid",
                "Offline Fake Jira plan 输出缺少固定 plan_file 或 plan_id",
                "fixture_jira_plan_contract",
            )
        self._record(
            "fixture_jira_plan_contract",
            plan_file=(
                f".agentic-ops/tasks/{self.manifest.issue_key}/runs/{self.run_id}/"
                f"jira-plans/{plan_path.name}"
            ),
            plan_id_present=True,
        )
        self._ao_work(
            "jira",
            "comment",
            "apply",
            "--plan-file",
            plan_file,
            "--confirm-plan-id",
            plan_id,
            "--authorization-reference",
            str(plan["authorization_user_confirmation_reference"]),
            "--decision-summary",
            "维护者已确认 Offline Fake 完成证据写入",
        )
        return {
            "plan_file": plan_file,
            "plan_id": plan_id,
        }

    def _ao_work(self, *arguments: str, input_text: str | None = None) -> dict[str, Any]:
        executable = self.install_root / "bin" / "ao-work"
        return self._process(
            "ao_work_" + "_".join(arguments[:3]),
            [
                str(executable),
                "--workspace-root",
                str(self.workspace),
                *arguments,
            ],
            cwd=self.workspace,
            input_text=input_text,
            json_output=True,
        )

    def _process(
        self,
        step: str,
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        input_text: str | None = None,
        json_output: bool = False,
    ) -> dict[str, Any]:
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=env or self._environment(),
                input=input_text,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise self._step_error(
                "integration_step_failed",
                f"集成步骤 {step} 无法完成：{type(error).__name__}",
                step,
            ) from error
        if completed.returncode != 0:
            raise self._step_error(
                "integration_step_failed",
                f"集成步骤 {step} 失败（exit={completed.returncode}）",
                step,
                output_tail=(completed.stdout + completed.stderr)[-2000:],
            )
        payload: dict[str, Any] = {}
        if json_output:
            lines = [line for line in completed.stdout.splitlines() if line.strip()]
            try:
                payload = json.loads(lines[-1]) if lines else {}
            except (IndexError, json.JSONDecodeError) as error:
                raise self._step_error(
                    "integration_step_output_invalid",
                    f"集成步骤 {step} 未返回有效 JSON",
                    step,
                ) from error
            if not isinstance(payload, dict) or payload.get("ok") is not True:
                raise self._step_error(
                    "integration_step_output_invalid",
                    f"集成步骤 {step} 没有返回成功结果",
                    step,
                )
        self._record(step, exit_code=0)
        return payload

    def _environment(self, additions: dict[str, str] | None = None) -> dict[str, str]:
        environment = {
            "HOME": str(self.home),
            "PATH": f"{self.tool_bin}:/usr/bin:/bin:/usr/sbin:/sbin",
            "TMPDIR": str(self.sandbox / "tmp"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "PYTHONNOUSERSITE": "1",
        }
        if self.certificate.is_file():
            environment["SSL_CERT_FILE"] = str(self.certificate)
        environment.update(additions or {})
        return environment

    def _record(self, step: str, **details: Any) -> None:
        self.steps.append({"step": step, "status": "passed", **details})

    def _step_error(
        self,
        code: str,
        message: str,
        step: str,
        *,
        output_tail: str = "",
    ) -> RuntimeErrorResult:
        details: dict[str, Any] = {"failed_step": step, "steps": self.steps}
        if output_tail:
            details["output_tail"] = output_tail
        return RuntimeErrorResult(
            code=code,
            message=message,
            status="blocked",
            exit_code=EXIT_BLOCKED,
            retry_safe=True,
            required_human_action="请修复清单或实现缺口后重新创建隔离测试环境",
            details=details,
        )


def _handler(state: FakeJiraState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = unquote(urlsplit(self.path).path)
            if path == "/rest/api/3/myself":
                self._json(200, {"accountId": "offline-agent", "displayName": "Offline Agent"})
                return
            if path == f"/rest/api/3/project/{state.project_key}":
                self._json(200, {"key": state.project_key, "name": "Offline Project"})
                return
            if path == "/rest/api/3/field":
                self._json(200, [])
                return
            if path == f"/rest/api/3/issue/{state.issue_key}":
                self._json(
                    200,
                    {
                        "id": f"offline-{state.issue_key}",
                        "key": state.issue_key,
                        "fields": {
                            "summary": "Offline Fake 合同回归任务",
                            "description": None,
                            "status": {"name": "正在进行"},
                            "issuetype": {"name": "Task"},
                            "assignee": {"accountId": "offline-agent"},
                            "project": {"key": state.project_key},
                        },
                    },
                )
                return
            if path == f"/rest/api/3/issue/{state.issue_key}/comment":
                self._json(200, {"comments": state.comments})
                return
            self._json(404, {"errorMessages": ["offline resource not found"]})

        def do_POST(self) -> None:  # noqa: N802
            path = unquote(urlsplit(self.path).path)
            if path != f"/rest/api/3/issue/{state.issue_key}/comment":
                self._json(404, {"errorMessages": ["offline resource not found"]})
                return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._json(400, {"errorMessages": ["invalid json"]})
                return
            comment = {
                "id": str(len(state.comments) + 1),
                "body": body.get("body"),
                "author": {"accountId": "offline-agent"},
                "created": "2026-01-01T00:00:00.000+0000",
            }
            state.comments.append(comment)
            self._json(201, comment)

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _json(self, status: int, payload: object) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler

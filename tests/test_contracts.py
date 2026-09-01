#!/usr/bin/env python3
"""AgenticOps 标准契约一致性测试；只实现本项目使用的 JSON Schema 子集。"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

import sys

sys.path.insert(0, str(ROOT))
from adapters.tools.classifier import classify_bash, classify_tool_call  # noqa: E402
from gate.runner import evaluate_request, validate_request  # noqa: E402


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def type_matches(value, expected):
    if isinstance(expected, list):
        return any(type_matches(value, item) for item in expected)
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": type(value) is int,
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, True)


def assert_schema(testcase, schema, value, path="$", root_schema=None):
    root_schema = root_schema or schema
    if "$ref" in schema:
        self_path = schema["$ref"]
        testcase.assertTrue(self_path.startswith("#/"), "%s 只支持本地 ref" % path)
        target = root_schema
        for part in self_path[2:].split("/"):
            target = target[part]
        return assert_schema(testcase, target, value, path, root_schema)
    if "type" in schema:
        testcase.assertTrue(type_matches(value, schema["type"]), "%s 类型错误" % path)
    if "const" in schema:
        testcase.assertEqual(schema["const"], value, "%s const 错误" % path)
    if "enum" in schema:
        testcase.assertIn(value, schema["enum"], "%s enum 错误" % path)
    if isinstance(value, str):
        if "minLength" in schema:
            testcase.assertGreaterEqual(len(value), schema["minLength"], path)
        if "pattern" in schema:
            testcase.assertRegex(value, re.compile(schema["pattern"]), path)
    if type(value) is int and "minimum" in schema:
        testcase.assertGreaterEqual(value, schema["minimum"], path)
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            testcase.assertIn(required, value, "%s 缺少 %s" % (path, required))
        if schema.get("additionalProperties") is False:
            testcase.assertFalse(set(value) - set(properties), "%s 包含额外字段" % path)
        for key, item in value.items():
            if key in properties:
                assert_schema(testcase, properties[key], item, "%s.%s" % (path, key), root_schema)
            elif isinstance(schema.get("additionalProperties"), dict):
                assert_schema(
                    testcase,
                    schema["additionalProperties"],
                    item,
                    "%s.%s" % (path, key),
                    root_schema,
                )
    if isinstance(value, list):
        if "minItems" in schema:
            testcase.assertGreaterEqual(len(value), schema["minItems"], path)
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, ensure_ascii=False) for item in value]
            testcase.assertEqual(len(encoded), len(set(encoded)), "%s 存在重复项" % path)
        if "items" in schema:
            for index, item in enumerate(value):
                assert_schema(testcase, schema["items"], item, "%s[%d]" % (path, index), root_schema)


class ContractConformanceTest(unittest.TestCase):
    def valid_request(self, cwd):
        return {
            "protocol_version": 1,
            "event": "before_operation",
            "source": {"agent": "test", "adapter": "test-hook", "adapter_version": 1},
            "cwd": str(cwd),
            "operations": ["git_commit"],
            "target": {"branch_relevant": True},
            "note": "契约测试",
        }

    def test_catalog_and_manifests_conform_to_schemas(self):
        catalog_schema = load_json(ROOT / "contracts" / "operation-catalog.schema.json")
        catalog = load_json(ROOT / "contracts" / "operation-catalog.json")
        assert_schema(self, catalog_schema, catalog)
        names = [item["name"] for item in catalog["operations"]]
        self.assertEqual(len(names), len(set(names)))

        manifest_schema = load_json(ROOT / "contracts" / "adapter-manifest.schema.json")
        for path in sorted((ROOT / "adapters" / "agents").glob("*/manifest.json")):
            assert_schema(self, manifest_schema, load_json(path), str(path))
        repository_catalog_schema = load_json(ROOT / "contracts" / "repository-catalog.schema.json")
        assert_schema(
            self,
            repository_catalog_schema,
            load_json(ROOT / "projects" / "tapdata" / "repositories.json"),
        )

    def test_repository_pool_configuration_conforms_to_schema(self):
        schema = load_json(ROOT / "contracts" / "repository-pool.schema.json")
        document = {
            "schema_version": 1,
            "root": "/opt/agentic-ops-repos",
            "provisioning": "manual",
        }
        assert_schema(self, schema, document)

    def test_source_product_state_conforms_to_schema(self):
        schema = load_json(ROOT / "contracts" / "product-state.schema.json")
        document = {
            "schema_version": 1,
            "mode": "source",
            "repository": "git@example.test:tapstate/agentic-ops.git",
            "tracking_branch": "develop",
            "current_ref": "a" * 40,
            "previous_ref": None,
        }
        assert_schema(self, schema, document)

    def test_real_workspace_state_conforms_to_schema(self):
        workspace_schema = load_json(ROOT / "contracts" / "workspace.schema.json")
        init_schema = load_json(ROOT / "contracts" / "workspace-init.schema.json")
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            repository_pool = Path(temporary) / "repository-pool"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bootstrap" / "render.py"),
                    "--install-home",
                    str(ROOT),
                    "--workspace",
                    str(workspace),
                    "--project",
                    "tapdata",
                    "--repository-pool",
                    str(repository_pool),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            assert_schema(
                self,
                workspace_schema,
                load_json(workspace / ".agenticops" / "workspace.json"),
            )
            assert_schema(
                self,
                init_schema,
                load_json(workspace / ".agenticops" / "init.json"),
            )
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "workflow" / "task.py"),
                    "init",
                    "--issue-key",
                    "TAP-123",
                    "--task-class",
                    "defect_fix",
                    "--dir",
                    str(workspace),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            registry_schema = load_json(ROOT / "contracts" / "task-registry.schema.json")
            assert_schema(
                self,
                registry_schema,
                load_json(workspace / ".agenticops" / "tasks" / "index.json"),
            )
            task_schema = load_json(ROOT / "contracts" / "task-state.schema.json")
            assert_schema(
                self,
                task_schema,
                load_json(workspace / ".agenticops" / "tasks" / "TAP-123" / "state.json"),
            )

    def test_task_endpoint_is_optional_v1_compatibility_field(self):
        schema = load_json(ROOT / "contracts" / "task-state.schema.json")
        repository = schema["properties"]["repositories"]["items"]
        self.assertIn("authorized_endpoint", repository["properties"])
        self.assertNotIn("authorized_endpoint", repository["required"])
        endpoint = repository["properties"]["authorized_endpoint"]
        self.assertEqual(endpoint, {"type": "string", "minLength": 1})

    def test_gate_validator_and_schema_accept_the_same_request(self):
        schema = load_json(ROOT / "contracts" / "gate-request.schema.json")
        with tempfile.TemporaryDirectory() as temporary:
            request = self.valid_request(temporary)
            assert_schema(self, schema, request)
            self.assertIsNone(validate_request(request))
            for required in schema["required"]:
                invalid = dict(request)
                invalid.pop(required)
                self.assertIsNotNone(validate_request(invalid), required)
            extra = dict(request)
            extra["platform_private"] = True
            self.assertIsNotNone(validate_request(extra))
            push_request = dict(request)
            push_request["operations"] = ["git_push"]
            push_request["target"] = {
                "push_source_ref": "HEAD",
                "push_destination_ref": "refs/heads/feature/TAP-123",
                "push_target_branch": "feature/TAP-123",
            }
            assert_schema(self, schema, push_request)
            self.assertIsNone(validate_request(push_request))

    def test_gate_response_conforms_to_decision_schema(self):
        schema = load_json(ROOT / "contracts" / "gate-decision.schema.json")
        with tempfile.TemporaryDirectory() as temporary:
            response = evaluate_request(self.valid_request(temporary))
            assert_schema(self, schema, response)

    def test_operation_catalog_policy_and_tool_mapping_do_not_drift(self):
        catalog = load_json(ROOT / "contracts" / "operation-catalog.json")
        operations = {item["name"]: item for item in catalog["operations"]}
        policy = load_json(ROOT / "policies" / "operations.json")
        self.assertEqual(set(operations), set(policy["operations"]))

        mapping = load_json(ROOT / "adapters" / "tools" / "mcp-operations.json")
        requestable = {name for name, item in operations.items() if item["requestable"]}
        mapped_operations = {
            operation
            for service_mapping in mapping["mappings"].values()
            for operation in service_mapping.values()
        }
        self.assertTrue(mapped_operations <= requestable)
        shell_operations = set(classify_bash(
            "git commit -m x && git push origin feature/x; gh pr merge 1"
        ))
        self.assertTrue(shell_operations <= requestable)

    def test_repository_tool_classification_preserves_control_boundary(self):
        self.assertEqual(
            ["prepare_task_repository"],
            classify_bash(
                "python3 workflow/task.py repository prepare --issue-key TAP-123"
            ),
        )
        self.assertEqual(
            ["prepare_task_repository"],
            classify_bash("workflow/task.py repository prepare --issue-key TAP-123"),
        )
        for command in (
            "sed -n '1,200p' ./agenticops",
            "sed -n '1,200p' agenticops",
            "head -n 80 .agenticops/workspace.json",
            "stat ./agenticops",
        ):
            self.assertEqual([], classify_bash(command), command)
        readonly_composition = (
            "rg --files .agenticops %s | rg '(jira|adapter|gate|runner|task)' && "
            "rg -n -C 3 'jira|runner|gate|def advance|def cmd_advance' %s %s .agenticops"
            % (ROOT / "workflow", ROOT / "workflow" / "task.py", ROOT / "workflow" / "task_store.py")
        )
        self.assertEqual([], classify_bash(readonly_composition))
        self.assertEqual([], classify_bash("rg '(jira|task)' memory.md && rg task workflow/task.py"))
        multi_repository_readonly = (
            "git -C /tmp/task-worktree-a status --short --branch && "
            "git -C /tmp/task-worktree-b status --short --branch && "
            "rg -n -i 'elasticsearch|logwarehouse' /tmp/task-worktree-a /tmp/task-worktree-b"
        )
        self.assertEqual([], classify_bash(multi_repository_readonly))
        self.assertEqual(
            [],
            classify_bash(
                "git -C /tmp/task-worktree status --short --branch && "
                "rg -n -i --glob '*.java' --glob '*.kt' --glob '*.xml' "
                "--glob '*.yml' --glob '*.yaml' --glob '*.properties' "
                "'elasticsearch|logwarehouse' ."
            ),
        )
        for command in (
            "cd /tmp/task-worktree && git push origin feature/TAP-123",
            "export GIT_DIR=/tmp/task-worktree/.git && git push origin feature/TAP-123",
        ):
            self.assertEqual([], classify_bash(command), command)
        for command in (
            "find . -exec ./agenticops workspace purge ';'",
            "sed -n 1p -i.bak ./agenticops",
            "cat source > ./agenticops",
            "sed -n 1p source > ./agenticops",
            "cat ./agenticops | sh -s workspace purge --workspace /tmp/example --yes",
            "sed -n 1p ./agenticops | sh -s workspace purge --workspace /tmp/example --yes",
            "rg --pre './agenticops workspace purge --yes' pattern ./agenticops",
            "head -n \"$(./agenticops workspace purge --yes)\" ./agenticops",
            "cat \"$(./agenticops workspace purge --yes)\" ./agenticops",
            "RIPGREP_CONFIG_PATH=/tmp/rg.conf rg pattern ./agenticops",
            "rg --pre tool pattern ./agenticops | rg task",
            "rg pattern ./agenticops > output && rg task .agenticops",
            "rg pattern ./agenticops>output && rg task .agenticops",
            "(rg task workflow/task.py) && rg pattern .agenticops",
        ):
            self.assertEqual([], classify_bash(command), command)
        self.assertIn(
            "unknown_external_write",
            classify_bash("./agenticops workspace purge --worksp /other --yes"),
        )
        self.assertEqual(
            ["git_push"],
            classify_bash("rg task .agenticops && git push origin feature/TAP-123"),
        )
        self.assertEqual(
            ["prepare_task_repository"],
            classify_bash("rg task .agenticops && workflow/task.py repository prepare --issue-key TAP-123"),
        )
        operations, _, target = classify_tool_call(
            "Bash", {"command": "./agenticops workspace purge --workspace /other --yes"}
        )
        self.assertEqual(["manage_repository_worktree"], operations)
        self.assertEqual("/other", target["workspace"])
        for command in (
            "python3 workflow/task.py repository prepare --issue-key TAP-123 --reuse-existing-branch",
            "python3 workflow/task.py repository cleanup --issue-key TAP-123",
            "python3 workflow/repository_worktree.py prepare --issue-key TAP-123",
            "workflow/repository_worktree.py prepare --issue-key TAP-123",
            "git clone git@example.test:a/b.git",
            "git fetch origin develop",
            "git worktree add /tmp/x",
        ):
            self.assertEqual(["manage_repository_worktree"], classify_bash(command), command)
        for command in (
            "python3 workflow/task.py repository prepare --help",
            "python3 workflow/task.py repository context --issue-key TAP-123 --json",
            "python3 workflow/repository_worktree.py roots --issue-key TAP-123",
            "python3 workflow/repository_worktree.py execution-root --issue-key TAP-123",
        ):
            self.assertEqual([], classify_bash(command), command)
        for command in (
            "python3 workflow/task.py purge --issue-key TAP-123 --yes",
            "workflow/task.py purge --issue-key TAP-123 --yes",
            "python3 -m workflow.task purge --issue-key TAP-123 --yes",
            "python3 -mworkflow.task purge --issue-key TAP-123 --yes",
            "python3 --check-hash-based-pycs always workflow/task.py purge --issue-key TAP-123 --yes",
            "python3 --check-hash-based-pycs=always workflow/task.py purge --issue-key TAP-123 --yes",
        ):
            self.assertEqual(["delete_task_state"], classify_bash(command), command)
        self.assertEqual(
            ["manage_repository_worktree"],
            classify_bash("python3 -m workflow.repository_worktree prepare --issue-key TAP-123"),
        )
        self.assertEqual(
            ["prepare_task_repository"],
            classify_bash("python3 -B -m workflow.task repository prepare --issue-key TAP-123"),
        )
        for module in ("workflow.other", "workflow.task.extra", "workflow.repository_worktree.extra"):
            self.assertEqual(
                [],
                classify_bash("python3 -m %s purge --issue-key TAP-123 --yes" % module),
            )
        self.assertEqual(
            [],
            classify_bash("python3 --check-hash-based-pycs workflow/task.py purge --issue-key TAP-123 --yes"),
        )
        self.assertEqual(
            [],
            classify_bash("python3 -c'print(1)' workflow/task.py purge --issue-key TAP-123 --yes"),
        )
        self.assertEqual(
            [],
            classify_bash("python3 - workflow/task.py purge --issue-key TAP-123 --yes"),
        )
        for command in (
            "python3",
            "python3 unregistered.py",
            "perl -we 'print 1'",
            "perl payload.pl",
            "ruby -e 'puts 1'",
            "ruby payload.rb",
            "node --eval='console.log(1)'",
            "node payload.js",
            "nodejs -e 'console.log(1)'",
            "nodejs payload.js",
            "python3 --version unregistered.py",
        ):
            self.assertEqual([], classify_bash(command), command)
        for command in (
            "python3 --version", "python3.11 -V", "perl -V", "ruby --version",
            "node -v", "nodejs --version",
        ):
            self.assertEqual([], classify_bash(command), command)
        self.assertEqual(
            ["delete_task_state"],
            classify_bash("python3 -X dev -W ignore -B workflow/task.py purge --issue-key TAP-123 --yes"),
        )
        self.assertEqual(["git_commit"], classify_bash("git commit -m --help"))
        self.assertEqual(["create_pr"], classify_bash("gh pr create --title --help"))
        self.assertEqual([], classify_bash("git commit -a --help"))
        self.assertEqual([], classify_bash("gh pr create --draft --help"))

        for command in (
            "git push --force-with-lease=refs/heads/feature/TAP-123 origin feature/TAP-123",
            "git push -fu origin feature/TAP-123",
            "git push -uvf origin feature/TAP-123",
            "git push -f4 --dry-run origin feature/TAP-123",
            "git push -f6 --dry-run origin feature/TAP-123",
        ):
            self.assertEqual(["force_push"], classify_bash(command), command)
        self.assertEqual(
            ["git_push"],
            classify_bash("git push -uv origin feature/TAP-123"),
        )

        for command in (
            "AO_MODE=test git push origin feature/TAP-123",
            "env AO_MODE=test git push origin feature/TAP-123",
            "env -u TERM AO_MODE=test command -p git push origin feature/TAP-123",
            "command -- git push origin feature/TAP-123",
        ):
            operations, _, wrapped_target = classify_tool_call(
                "Bash", {"command": command}
            )
            self.assertEqual(["git_push"], operations, command)
            self.assertEqual(
                "feature/TAP-123", wrapped_target["push_target_branch"], command
            )
            self.assertEqual("feature/TAP-123", wrapped_target["push_source_ref"], command)
            self.assertEqual(
                "refs/heads/feature/TAP-123",
                wrapped_target["push_destination_ref"],
                command,
            )
        for command in (
            "env -S 'git push origin feature/TAP-123'",
            "env --unknown git push origin feature/TAP-123",
            "command --unknown git push origin feature/TAP-123",
            "AO_MODE='$(touch /tmp/x)' git push origin feature/TAP-123",
            "$(printf git) push origin feature/TAP-123",
            "env AO_MODE=test $(printf git) push origin feature/TAP-123",
            "G=git; $G push origin feature/TAP-123",
            "G='git'; \"$G\" push origin feature/TAP-123",
            'G="git"; ${G} push origin feature/TAP-123',
            "P=/usr/bin/git; $P push origin feature/TAP-123",
        ):
            self.assertEqual([], classify_bash(command), command)
        for command in (
            "${G} push origin feature/TAP-123",
            "env AO_MODE=test command $G push origin feature/TAP-123",
            "printf '%s' '<(dynamic)'",
        ):
            self.assertEqual([], classify_bash(command), command)
        self.assertEqual([], classify_bash("G=git"))
        self.assertEqual([], classify_bash("command -v git"))
        self.assertEqual(
            ["force_push"],
            classify_bash("git status & git push --force origin feature/TAP-123"),
        )
        for command in (
            "if git status; then git push origin feature/TAP-123; fi",
            "(git push origin feature/TAP-123)",
            "sh -c 'git push origin feature/TAP-123'",
            "bash -c 'git push origin feature/TAP-123'",
            "zsh -c 'git push origin feature/TAP-123'",
            "sudo git push origin feature/TAP-123",
            "custom-wrapper git push origin feature/TAP-123",
            "custom-wrapper 'git push origin feature/TAP-123'",
        ):
            self.assertEqual([], classify_bash(command), command)

        for command in (
            "PATH=/custom/bin git push origin feature/TAP-123",
            "env GIT_EXEC_PATH=/custom/git git push origin feature/TAP-123",
            "GIT_SSH=/custom/ssh git push origin feature/TAP-123",
            "env GIT_SSH_COMMAND='ssh -F custom' git push origin feature/TAP-123",
            "GIT_PROXY_COMMAND=proxy git push origin feature/TAP-123",
            "HOME=/other/home git push origin feature/TAP-123",
            "HTTPS_PROXY=http://other-proxy git push origin feature/TAP-123",
            "env -i git push origin feature/TAP-123",
            "env -u PATH git push origin feature/TAP-123",
            "env --unset=GIT_SSH git push origin feature/TAP-123",
        ):
            self.assertEqual([], classify_bash(command), command)

        operations, _, redirected_target = classify_tool_call(
            "Bash", {"command": "git -C /other push origin feature/TAP-123"}
        )
        self.assertEqual(["git_push"], operations)
        self.assertEqual("/other", redirected_target["git_cwd"])
        self.assertEqual("feature/TAP-123", redirected_target["push_target_branch"])
        for command in (
            "git --git-dir=/other/repo.git push origin feature/TAP-123",
            "git --work-tree=/other/tree push origin feature/TAP-123",
            "git --namespace=other push origin feature/TAP-123",
        ):
            operations, _, redirected_target = classify_tool_call(
                "Bash", {"command": command}
            )
            self.assertEqual(
                ["git_push", "unknown_external_write"], operations, command
            )
            self.assertEqual(
                "feature/TAP-123",
                redirected_target["push_target_branch"],
                command,
            )
        for command in (
            "env -C /other git push origin feature/TAP-123",
            "env --chdir=/other git push origin feature/TAP-123",
        ):
            self.assertEqual([], classify_bash(command), command)
        for command in (
            "git -C /other status --short",
            "git -C /other rev-parse HEAD",
            "git --git-dir=/other/repo.git diff HEAD",
            "git --work-tree=/other/tree --git-dir=/other/repo.git log -1",
            "git --git-dir /other/repo.git show HEAD",
        ):
            self.assertEqual([], classify_bash(command), command)
        self.assertEqual([], classify_bash("git -C /other unknown-subcommand"))
        self.assertEqual([], classify_bash("git add manager/tm/src/main/resources/application-default.yml"))
        self.assertEqual(
            [],
            classify_bash("git -C /tmp/task-worktree add manager/tm/src/main/resources/application-default.yml"),
        )

        for command in (
            "sed -n '1,240p' file && rg -n -i -C 2 'TAP-12289|takeover|接管' memory.md",
            "rg -n 'git|push|commit' docs",
            "rg -n 'G=git; $G push' docs",
            "mvn --batch-mode test",
            "npm test",
            "python3 unregistered.py",
            "node takeover.js --issue-key TAP-12774",
        ):
            self.assertEqual([], classify_bash(command), command)

        for tool_name in (
            "mcp__github__run_secret_scanning",
            "mcp__custom__mutate_unknown_resource",
            "mcp__slack__add_comment",
            "mcp__custom__merge_pull_request",
        ):
            self.assertEqual(([], tool_name, {}), classify_tool_call(tool_name, {}))
        self.assertEqual(
            ["write_jira_comment"],
            classify_tool_call("mcp__atlassian__add_comment", {"issueKey": "TAP-123"})[0],
        )
        self.assertEqual(
            ["pr_merge"],
            classify_tool_call("mcp__github__merge_pull_request", {"repository": "acme/widget"})[0],
        )
        self.assertEqual(
            ["create_pr", "unknown_external_write"],
            classify_tool_call("mcp__github__create_pull_request", {"title": "t"})[0],
        )

        operations, _, newline_target = classify_tool_call(
            "Bash", {"command": "git status\ngit push origin main"}
        )
        self.assertEqual(["git_push"], operations)
        self.assertEqual("main", newline_target["push_target_branch"])
        self.assertEqual(
            ["force_push"],
            classify_bash("git diff\r\ngit push --force origin feature/TAP-123"),
        )

        for command in (
            "git push",
            "git push origin",
            "git push origin HEAD",
            "env AO_MODE=test git push origin",
        ):
            operations, _, implicit_target = classify_tool_call(
                "Bash", {"command": command}
            )
            self.assertEqual(
                ["git_push", "unknown_external_write"], operations, command
            )
            self.assertNotIn("push_target_branch", implicit_target, command)
        operations, _, explicit_target = classify_tool_call(
            "Bash", {"command": "git push origin HEAD:feature/TAP-123"}
        )
        self.assertEqual(["git_push"], operations)
        self.assertEqual("feature/TAP-123", explicit_target["push_target_branch"])
        self.assertEqual("HEAD", explicit_target["push_source_ref"])
        self.assertEqual(
            "refs/heads/feature/TAP-123",
            explicit_target["push_destination_ref"],
        )
        refspec_cases = {
            "git push origin feature/TAP-123": (
                "feature/TAP-123", "refs/heads/feature/TAP-123",
            ),
            "git push origin refs/heads/feature/TAP-123": (
                "refs/heads/feature/TAP-123", "refs/heads/feature/TAP-123",
            ),
            "git push origin evil:feature/TAP-123": (
                "evil", "refs/heads/feature/TAP-123",
            ),
            "git push origin HEAD:refs/tags/v1": ("HEAD", "refs/tags/v1"),
        }
        for command, expected in refspec_cases.items():
            operations, _, refspec_target = classify_tool_call(
                "Bash", {"command": command}
            )
            self.assertEqual(["git_push"], operations, command)
            self.assertEqual(expected[0], refspec_target["push_source_ref"], command)
            self.assertEqual(expected[1], refspec_target["push_destination_ref"], command)
        for command in (
            "git push upstream feature/TAP-123",
            "git push git@github.com:acme/widget.git feature/TAP-123",
            "git push /tmp/widget.git feature/TAP-123",
            "git push --repo=origin feature/TAP-123",
            "git push --receive-pack=custom origin feature/TAP-123",
            "git push --push-option=ci.skip origin feature/TAP-123",
            "git push --no-verify origin feature/TAP-123",
            "git -c color.ui=false push origin feature/TAP-123",
            "git --config-env=remote.origin.pushurl=PUSH_URL push origin feature/TAP-123",
            "git --exec-path=/custom/git push origin feature/TAP-123",
            "git -c remote.origin.pushurl=git@evil.test:acme/widget.git push origin feature/TAP-123",
        ):
            operations, _, unsafe_remote_target = classify_tool_call(
                "Bash", {"command": command}
            )
            self.assertIn("unknown_external_write", operations, command)
        for command in (
            "git push --delete origin feature/TAP-123",
            "git push -d origin feature/TAP-123",
            "git push --prune origin feature/TAP-123",
            "git push origin :feature/TAP-123",
            "git push --follow-tags origin feature/TAP-123",
            "git push --tags origin feature/TAP-123",
            "git push --all origin feature/TAP-123",
            "git push --mirror origin feature/TAP-123",
            "git push --atomic origin feature/TAP-123",
            "git push origin 'refs/heads/*:refs/heads/*'",
        ):
            operations, _, unsafe_push_target = classify_tool_call(
                "Bash", {"command": command}
            )
            self.assertEqual(
                ["git_push", "unknown_external_write"], operations, command
            )
            self.assertNotIn("push_target_branch", unsafe_push_target, command)
        self.assertEqual(
            ["git_push"],
            classify_bash(
                "git push --dry-run --porcelain origin feature/TAP-123"
            ),
        )
        for command in (
            "git ship feature/TAP-123",
            "git -c alias.ship='push origin main' ship feature/TAP-123",
        ):
            self.assertEqual([], classify_bash(command), command)
        self.assertEqual([], classify_bash("git -c color.ui=false status --short"))
        self.assertEqual(
            [],
            classify_bash("git --config-env=color.ui=COLOR_SETTING status --short"),
        )

        operations, _, target = classify_tool_call(
            "Bash",
            {"command": "workflow/task.py repository prepare --issue-key tap-123 --dir ../target"},
        )
        self.assertEqual(["prepare_task_repository"], operations)
        self.assertEqual("TAP-123", target["issue_key"])
        self.assertEqual("../target", target["workspace"])
        for command in (
            "workflow/task.py repository prepare --issue-key TAP-123 --issue-key TAP-123",
            "workflow/task.py repository prepare --issue-key=TAP-123 --issue-key TAP-124",
            "workflow/task.py repository prepare --dir ws-a --issue-key TAP-123 --dir=ws-b",
            "workflow/task.py repository prepare --dir=ws-a --dir ws-a --issue-key=TAP-123",
        ):
            operations, _, duplicate_target = classify_tool_call(
                "Bash", {"command": command}
            )
            self.assertEqual(
                ["prepare_task_repository", "unknown_external_write"],
                operations,
                command,
            )
            self.assertNotIn("issue_key", duplicate_target, command)
            self.assertNotIn("workspace", duplicate_target, command)
        operations, _, target = classify_tool_call(
            "Bash",
            {"command": "git commit -m x && echo --issue-key TAP-999 --dir /tmp/other"},
        )
        self.assertEqual(["git_commit"], operations)
        self.assertNotIn("issue_key", target)
        self.assertNotIn("workspace", target)
        self.assertEqual(
            ["prepare_task_repository"],
            classify_bash("python3 -B workflow/task.py repository prepare --issue-key TAP-123"),
        )
        operations, _, target = classify_tool_call(
            "Bash",
            {"command": "workflow/task.py status --issue-key TAP-999 --dir wsA && workflow/task.py repository prepare --issue-key tap-123 --dir wsB"},
        )
        self.assertEqual(["prepare_task_repository"], operations)
        self.assertEqual("TAP-123", target["issue_key"])
        self.assertEqual("wsB", target["workspace"])
        operations, _, target = classify_tool_call(
            "Bash",
            {"command": "workflow/task.py purge --issue-key TAP-123 --dir wsA --yes && workflow/task.py repository prepare --issue-key TAP-124 --dir wsB"},
        )
        self.assertIn("unknown_external_write", operations)
        self.assertNotIn("issue_key", target)
        self.assertNotIn("workspace", target)
        operations, _, target = classify_tool_call(
            "Bash", {"command": "git push origin feature/TAP-123 && git push origin main"},
        )
        self.assertEqual(["git_push", "git_push", "unknown_external_write"], operations)
        self.assertNotIn("push_target_branch", target)
        operations, _, target = classify_tool_call(
            "Bash", {"command": "git push origin feature/TAP-123 && git push origin feature/TAP-123"},
        )
        self.assertEqual(["git_push", "git_push"], operations)
        self.assertEqual("feature/TAP-123", target["push_target_branch"])
        operations, _, target = classify_tool_call(
            "Bash", {"command": "git push origin feature/TAP-123 feature/TAP-124"},
        )
        self.assertIn("unknown_external_write", operations)
        self.assertNotIn("push_target_branch", target)
        operations, _, target = classify_tool_call(
            "Bash", {"command": "gh pr create -R acme/widget && gh pr edit 1 -R acme/other"},
        )
        self.assertIn("unknown_external_write", operations)
        self.assertNotIn("repository", target)
        operations, _, target = classify_tool_call(
            "Bash", {"command": "gh pr create -R acme/widget && gh pr edit 1 --repo acme/widget"},
        )
        self.assertEqual(["create_pr", "update_pr"], operations)
        self.assertEqual("acme/widget", target["repository"])

    def test_contract_policy_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = load_json(ROOT / "policies" / "operations.json")
            policy["operations"].pop("git_commit")
            policy_path = root / "operations.json"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            response = evaluate_request(self.valid_request(root), policy_path=policy_path)
            self.assertEqual("deny", response["decision"])
            self.assertEqual("contract_policy_drift", response["operation"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

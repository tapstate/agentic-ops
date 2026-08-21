from __future__ import annotations

import ast
import argparse
import io
import json
import re
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import yaml

from ao_maint.cli import build_parser as build_maintainer_parser
from ao_maint.cli import main as maintainer_main
from ao_maint.output import RuntimeErrorResult
from ao_maint.workspace import MAINTAINER, resolve_maintainer_workspace


class WorkplaneBoundaryTest(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[3]
    DEVELOPER_COMMANDS = frozenset({"workspace", "auth", "jira", "task", "report"})
    SWITCH_FLAGS = frozenset({"--mode", "--role", "--workplane"})

    def test_cli_command_sets_are_disjoint(self) -> None:
        """maintainer 顶层命令与 developer 命令的共享名只能是显式白名单。

        命令名相同不表示代码串：同名命令的实现必须位于 ao_maint 包内，
        由 ao_maint 自己的模块处理，不 import ao_work（后者由
        test_workplane_packages_do_not_cross_import 保证）。
        """
        parser = build_maintainer_parser()
        commands = self._subcommands(parser)
        self.assertIn("story", commands)
        # jira 是唯一允许的共享顶层命令名；其它 developer 业务命令
        # （workspace/auth/task/report）不得出现在 maintainer 顶层。
        self.assertEqual({"jira"}, commands & self.DEVELOPER_COMMANDS)
        # 同名命令必须由 ao_maint 独立实现，不能委托给 ao_work。
        from ao_maint.jira.cli import execute_jira

        self.assertTrue(
            execute_jira.__module__.startswith("ao_maint."),
            "maintainer 的 jira 命令必须由 ao_maint 包内模块实现",
        )

    def test_maintainer_task_standard_is_versioned_for_every_worktree(self) -> None:
        maintainer_entry = (self.ROOT / "maintainer/AGENTS.md").read_text(
            encoding="utf-8"
        )
        source_rule = (
            self.ROOT / "maintainer/rules/source-maintenance.md"
        ).read_text(encoding="utf-8")
        skill = self.ROOT / "maintainer/skills/maintain-ao-task/SKILL.md"
        self.assertIn("ao-maint takeover <AO-KEY>", maintainer_entry)
        self.assertIn("设计审查、代码审查、风险", maintainer_entry)
        self.assertIn("功能、修复和任务分支推进到 commit、push、PR", maintainer_entry)
        self.assertIn("提供提交编号并在推送前逐项审查", maintainer_entry)
        self.assertIn("不能要求用户确认或复制内部 `impact_id`", maintainer_entry)
        self.assertIn("allowed_project_keys", maintainer_entry)
        self.assertIn("maintainer_jira_project_scope_mismatch", maintainer_entry)
        self.assertIn("work-authorization:<KEY>:<RUN>:<DESIGN-DIGEST>", source_rule)
        self.assertIn("对应 developer 工作空间使用 `ao-work`", source_rule)
        self.assertTrue(skill.is_file())

    def test_no_parser_level_can_switch_workplane(self) -> None:
        parser = build_maintainer_parser()
        self.assertEqual(set(), self._all_option_strings(parser) & self.SWITCH_FLAGS)

    def test_maintainer_requires_source_marker_and_ai_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / ".agentic-ops-source"
            entry = root / "maintainer" / "AGENTS.md"
            entry.parent.mkdir(parents=True)
            marker.write_text("maintainer\n", encoding="utf-8")
            entry.write_text("# maintainer\n", encoding="utf-8")
            self._prepare_official_repository(root)
            self.assertEqual(MAINTAINER, resolve_maintainer_workspace(str(root)).workplane)

    def test_maintainer_rejects_fabricated_nested_and_rewritten_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "maintainer").mkdir(parents=True)
            (root / ".agentic-ops-source").write_text("maintainer\n", encoding="utf-8")
            (root / "maintainer" / "AGENTS.md").write_text(
                "# maintainer\n", encoding="utf-8"
            )
            with self.assertRaises(RuntimeErrorResult):
                resolve_maintainer_workspace(str(root))

            self._prepare_official_repository(root)
            nested = root / "nested"
            nested.mkdir()
            with self.assertRaises(RuntimeErrorResult) as nested_error:
                resolve_maintainer_workspace(str(nested))
            self.assertEqual(
                "workplane_mismatch", nested_error.exception.code
            )

            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "config",
                    f"url.{root / 'fake'}.insteadOf",
                    "git@github.com:tapstate/agentic-ops.git",
                ],
                check=True,
            )
            with self.assertRaises(RuntimeErrorResult) as rewritten:
                resolve_maintainer_workspace(str(root))
            self.assertEqual(
                "maintainer_repository_identity_invalid", rewritten.exception.code
            )

    def test_developer_clone_cannot_forge_maintainer_identity_with_symlinks(self) -> None:
        for forged_component in ("marker", "maintainer", "entry", "root"):
            with self.subTest(forged_component=forged_component), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                root = base / "developer-managed-clone"
                root.mkdir()
                (root / "developer").mkdir()
                (root / "developer" / "AGENTS.md").write_text(
                    "# developer\n", encoding="utf-8"
                )
                self._prepare_official_repository(root)

                external = base / "external-maintainer-assets"
                external.mkdir()
                (external / "marker").write_text("maintainer\n", encoding="utf-8")
                (external / "maintainer").mkdir()
                (external / "maintainer" / "AGENTS.md").write_text(
                    "# forged maintainer\n", encoding="utf-8"
                )

                if forged_component == "marker":
                    (root / ".agentic-ops-source").symlink_to(external / "marker")
                    (root / "maintainer").mkdir()
                    (root / "maintainer" / "AGENTS.md").write_text(
                        "# maintainer\n", encoding="utf-8"
                    )
                    candidate = root
                elif forged_component == "maintainer":
                    (root / ".agentic-ops-source").write_text(
                        "maintainer\n", encoding="utf-8"
                    )
                    (root / "maintainer").symlink_to(
                        external / "maintainer", target_is_directory=True
                    )
                    candidate = root
                elif forged_component == "entry":
                    (root / ".agentic-ops-source").write_text(
                        "maintainer\n", encoding="utf-8"
                    )
                    (root / "maintainer").mkdir()
                    (root / "maintainer" / "AGENTS.md").symlink_to(
                        external / "maintainer" / "AGENTS.md"
                    )
                    candidate = root
                else:
                    (root / ".agentic-ops-source").write_text(
                        "maintainer\n", encoding="utf-8"
                    )
                    (root / "maintainer").mkdir()
                    (root / "maintainer" / "AGENTS.md").write_text(
                        "# maintainer\n", encoding="utf-8"
                    )
                    candidate = base / "source-root-alias"
                    candidate.symlink_to(root, target_is_directory=True)

                with self.assertRaises(RuntimeErrorResult) as captured:
                    resolve_maintainer_workspace(str(candidate))
                self.assertEqual("workplane_mismatch", captured.exception.code)

    def test_developer_workspace_cannot_run_maintainer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".agentic-ops").mkdir(parents=True)
            (root / ".agentic-ops" / "agent.json").write_text(
                '{"workplane":"developer"}\n', encoding="utf-8"
            )
            with self.assertRaises(RuntimeErrorResult) as captured:
                resolve_maintainer_workspace(str(root))
            self.assertEqual("workplane_mismatch", captured.exception.code)

    def test_developer_workspace_is_blocked_before_maintainer_service(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_root = root / ".agentic-ops"
            state_root.mkdir(parents=True)
            config = state_root / "agent.json"
            config.write_text('{"workplane":"developer"}\n', encoding="utf-8")
            before = config.read_bytes()

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch("ao_maint.cli.execute_story") as execute_story,
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                exit_code = maintainer_main(
                    [
                        "--source-root",
                        str(root),
                        "story",
                        "impact",
                        "--change-source",
                        "worktree",
                    ]
                )

            self.assertEqual(2, exit_code)
            self.assertEqual("workplane_mismatch", json.loads(stdout.getvalue())["code"])
            execute_story.assert_not_called()
            self.assertEqual(before, config.read_bytes())

    def test_maintainer_rejects_switch_flags(self) -> None:
        for flag in self.SWITCH_FLAGS:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = maintainer_main([flag, "developer", "story", "impact"])
            self.assertEqual(2, exit_code)
            self.assertEqual("invalid_arguments", json.loads(stdout.getvalue())["code"])

    def test_workplane_packages_do_not_cross_import(self) -> None:
        self._assert_no_import(
            self.ROOT / "developer" / "runtime" / "src", ("ao_maint",)
        )
        self._assert_no_import(
            self.ROOT / "maintainer" / "runtime" / "src", ("ao_work",)
        )

    def test_shared_python_does_not_depend_on_either_workplane(self) -> None:
        self._assert_no_import(self.ROOT / "shared", ("ao_maint", "ao_work"))

    def test_shared_is_fixed_non_executable_protocol_allowlist(self) -> None:
        shared = self.ROOT / "shared"
        expected_directories = {"integration", "standards"}
        expected_files = {
            "README.md",
            "integration/README.md",
            "integration/task-to-pr-event.schema.json",
            "integration/task-to-pr-manifest.schema.json",
            "integration/task-to-pr-result.schema.json",
            "standards/jira-comment-template.schema.json",
        }
        entries = list(shared.rglob("*"))
        self.assertFalse(
            any(path.is_symlink() for path in entries),
            "shared 不得包含符号链接",
        )
        self.assertEqual(
            expected_directories,
            {
                path.relative_to(shared).as_posix()
                for path in entries
                if path.is_dir()
            },
        )
        files = [path for path in entries if path.is_file()]
        self.assertEqual(
            expected_files,
            {path.relative_to(shared).as_posix() for path in files},
        )
        for path in files:
            self.assertEqual(0, path.stat().st_mode & 0o111, f"shared 文件不得可执行：{path}")
            self.assertNotIn(path.suffix, {".py", ".pyc", ".sh"}, path)

    def test_skills_declare_exactly_one_matching_workplane(self) -> None:
        discovered: set[str] = set()
        for workplane in ("maintainer", "developer"):
            for path in (self.ROOT / workplane / "skills").glob("*/SKILL.md"):
                discovered.add(workplane)
                content = path.read_text(encoding="utf-8")
                parts = content.split("---", 2)
                self.assertEqual(3, len(parts), f"Skill 缺少 YAML frontmatter：{path}")
                frontmatter = parts[1]
                metadata = yaml.safe_load(frontmatter)
                self.assertIsInstance(metadata, dict, path)
                self.assertEqual(
                    1,
                    len(re.findall(r"(?m)^\s+workplane\s*:", frontmatter)),
                    f"Skill 必须且只能在 metadata 中声明一个 workplane：{path}",
                )
                self.assertNotIn("workplane", metadata, path)
                skill_metadata = metadata.get("metadata")
                self.assertIsInstance(skill_metadata, dict, path)
                self.assertEqual(workplane, skill_metadata.get("workplane"), path)
                for legacy_key in ("mode", "allowed_mode", "allowed_modes"):
                    self.assertNotIn(legacy_key, metadata, path)
        self.assertEqual({"maintainer", "developer"}, discovered)

        for path in self.ROOT.rglob("SKILL.md"):
            relative = path.relative_to(self.ROOT)
            self.assertIn(
                relative.parts[0],
                {"maintainer", "developer"},
                f"Skill 位于工作面之外：{relative}",
            )

    def test_root_has_no_mixed_runtime_skill_rule_or_bootstrap_assets(self) -> None:
        for name in ("runtime", "skills", "rules", "bootstrap"):
            path = self.ROOT / name
            files = (
                [
                    item
                    for item in path.rglob("*")
                    if item.is_file()
                    and "__pycache__" not in item.parts
                    and item.suffix != ".pyc"
                ]
                if path.exists()
                else []
            )
            self.assertEqual([], files, f"根级混合资产仍存在：{name}")

        for name in ("agent-guides.md", "agent-init.md"):
            self.assertFalse(
                (self.ROOT / name).exists(),
                f"developer 专属指引不得留在 maintainer 根工作面：{name}",
            )

    def test_shared_has_no_ai_entry_or_skill(self) -> None:
        shared = self.ROOT / "shared"
        self.assertFalse(any(path.name in {"AGENTS.md", "SKILL.md"} for path in shared.rglob("*")))

    def _subcommands(self, parser: argparse.ArgumentParser) -> set[str]:
        actions = [
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        ]
        self.assertEqual(1, len(actions), "CLI 顶层必须只有一个 subparser 注册表")
        return set(actions[0].choices)

    @staticmethod
    def _prepare_official_repository(root: Path) -> None:
        subprocess.run(["git", "-C", str(root), "init", "-b", "main"], check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "remote",
                "add",
                "origin",
                "git@github.com:tapstate/agentic-ops.git",
            ],
            check=True,
            capture_output=True,
        )

    def _all_option_strings(self, parser: argparse.ArgumentParser) -> set[str]:
        options: set[str] = set()
        pending = [parser]
        visited: set[int] = set()
        while pending:
            current = pending.pop()
            if id(current) in visited:
                continue
            visited.add(id(current))
            for action in current._actions:
                options.update(action.option_strings)
                if isinstance(action, argparse._SubParsersAction):
                    pending.extend(action.choices.values())
        return options

    def _assert_no_import(self, root: Path, forbidden: tuple[str, ...]) -> None:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module]
                for module in modules:
                    self.assertFalse(module.startswith(forbidden), f"{path}: {module}")


if __name__ == "__main__":
    unittest.main()

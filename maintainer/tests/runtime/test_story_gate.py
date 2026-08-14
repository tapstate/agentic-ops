from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from ao_maint.cli import main
from ao_maint.output import RuntimeErrorResult
from ao_maint.story_gate.registry import load_story_registry, path_matches
from ao_maint.story_gate.service import StoryGateService, _check_command

STORY_BODY = """\
# {story_id} 测试故事

### 验收标准

- 验收可执行。

### 保护行为

- 行为不得退化。

### 验收证据

- 固定检查结果。
"""

REGISTRY = """\
schema_version: 1
story_categories:
  - maintainer
  - developer
stories:
  - story_id: PM-001
    category: maintainer
    title: 维护测试
    document: docs/user-stories/project-maintainer/pm-001.md
    protected_paths: [maintainer/runtime/**]
    acceptance_checks: [story_registry]
    evidence_requirements: [维护验收]
  - story_id: DE-001
    category: developer
    title: 研发测试
    document: docs/user-stories/development-engineer/de-001.md
    protected_paths: [developer/skills/**]
    acceptance_checks: [story_registry]
    evidence_requirements: [研发验收]
"""


class StoryGateTest(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[3]

    def prepare(self, root: Path) -> None:
        source_marker = root / ".agentic-ops-source"
        goal = root / "docs" / "strategy" / "project-goals.md"
        pm_story = root / "docs" / "user-stories" / "project-maintainer" / "pm-001.md"
        de_story = root / "docs" / "user-stories" / "development-engineer" / "de-001.md"
        registry = root / "maintainer" / "standards" / "stories" / "project-quality.yaml"
        for path in (goal, pm_story, de_story, registry):
            path.parent.mkdir(parents=True, exist_ok=True)
        source_marker.write_text("maintainer\n", encoding="utf-8")
        goal.write_text("# 项目目标\n", encoding="utf-8")
        pm_story.write_text(STORY_BODY.format(story_id="PM-001"), encoding="utf-8")
        de_story.write_text(STORY_BODY.format(story_id="DE-001"), encoding="utf-8")
        registry.write_text(REGISTRY, encoding="utf-8")
        self.git(root, "init")
        self.git(
            root,
            "remote",
            "add",
            "origin",
            "git@github.com:tapstate/agentic-ops.git",
        )
        self.git(root, "config", "user.email", "test@example.test")
        self.git(root, "config", "user.name", "Test")
        self.git(root, "add", ".")
        self.git(root, "commit", "-m", "baseline")

    def git(self, root: Path, *arguments: str) -> None:
        subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def git_output(self, root: Path, *arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout.strip()

    def stage(self, root: Path, relative_path: str, content: str) -> None:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.git(root, "add", relative_path)

    def test_impacted_story_requires_approval_and_matching_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            self.stage(root, "maintainer/runtime/example.py", "value = 1\n")
            service = StoryGateService(root)
            with self.assertRaises(RuntimeErrorResult) as captured:
                service.inspect("staged")
            self.assertEqual("maintenance_story_impacted", captured.exception.code)
            impact_id = str(captured.exception.details["impact_id"])

            reference = f"user-confirmation:AO-11:{impact_id}"
            approved = service.approve(
                "staged", impact_id, reference
            )
            self.assertEqual(True, approved["approved"])
            self.assertEqual(reference, approved["authorization_reference"])
            with self.assertRaises(RuntimeErrorResult) as not_verified:
                service.inspect("staged")
            self.assertEqual("maintenance_story_acceptance_failed", not_verified.exception.code)

            with mock.patch(
                "ao_maint.story_gate.service._check_command",
                return_value=["/usr/bin/true"],
            ):
                verified = service.verify("staged")
            self.assertEqual("passed", verified["acceptance_status"])
            allowed = service.inspect("staged")
            self.assertEqual(True, allowed["approved"])
            self.assertEqual("passed", allowed["acceptance_status"])

    def test_story_document_change_requires_revision_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            self.stage(
                root,
                "docs/user-stories/project-maintainer/pm-001.md",
                STORY_BODY.format(story_id="PM-001") + "\n补充故事。\n",
            )
            with self.assertRaises(RuntimeErrorResult) as captured:
                StoryGateService(root).inspect("staged")
            self.assertEqual("maintenance_story_revision_required", captured.exception.code)
            self.assertEqual(["PM-001"], captured.exception.details["revision_story_ids"])

    def test_governed_path_without_story_mapping_is_capability_gap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            self.stage(root, "maintainer/scripts/new-check.sh", "#!/usr/bin/env bash\n")
            with self.assertRaises(RuntimeErrorResult) as captured:
                StoryGateService(root).inspect("staged")
            self.assertEqual("maintenance_story_mapping_missing", captured.exception.code)
            self.assertEqual(3, captured.exception.exit_code)

    def test_unknown_root_path_is_governed_and_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            self.stage(root, ".github/workflows/unsafe.yml", "name: unsafe\n")
            with self.assertRaises(RuntimeErrorResult) as captured:
                StoryGateService(root).inspect("staged")
            self.assertEqual("maintenance_story_mapping_missing", captured.exception.code)

    def test_content_change_invalidates_old_impact_and_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            self.stage(root, "maintainer/runtime/example.py", "value = 1\n")
            service = StoryGateService(root)
            initial = service.inspect("staged", enforce=False)
            impact_id = str(initial["impact_id"])
            service.approve(
                "staged",
                impact_id,
                f"user-confirmation:AO-11:{impact_id}",
            )
            self.stage(root, "maintainer/runtime/example.py", "value = 2\n")
            changed = service.inspect("staged", enforce=False)
            self.assertNotEqual(initial["impact_id"], changed["impact_id"])
            self.assertEqual(False, changed["approved"])

    def test_first_gate_migration_commit_matches_explicit_staged_approval(self) -> None:
        """首次迁移靠显式 staged gate，不伪称旧 HEAD Hook 已保护候选。"""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            baseline = self.git_output(root, "rev-parse", "HEAD")
            self.stage(root, "maintainer/runtime/example.py", "value = 'migration'\n")
            service = StoryGateService(root)
            impact = service.inspect("staged", enforce=False)
            impact_id = str(impact["impact_id"])
            service.approve(
                "staged",
                impact_id,
                f"user-confirmation:AO-11:{impact_id}",
            )
            with mock.patch(
                "ao_maint.story_gate.service._check_command",
                return_value=["/usr/bin/true"],
            ):
                service.verify("staged")
            allowed = service.inspect("staged")
            self.assertEqual("passed", allowed["acceptance_status"])
            staged_tree = self.git_output(root, "write-tree")
            candidate_tree = root / "maintainer" / ".local" / "migration-index-tree"
            candidate_tree.parent.mkdir(parents=True, exist_ok=True)
            candidate_tree.write_text(staged_tree + "\n", encoding="utf-8")

            # 仅模拟“旧 HEAD 尚无新 Hook/Runtime”的一次性人工迁移提交；真实流程
            # 必须先完成上面的同一 impact 显式确认与验收，不能把该 bypass 当常规入口。
            self.git(
                root,
                "-c",
                "core.hooksPath=/dev/null",
                "commit",
                "-m",
                "install story gate baseline",
            )
            head = self.git_output(root, "rev-parse", "HEAD")
            self.assertEqual(
                candidate_tree.read_text(encoding="utf-8").strip(),
                self.git_output(root, "rev-parse", "HEAD^{tree}"),
            )

            committed = service.inspect(
                "range",
                base=baseline,
                head=head,
            )
            self.assertEqual(impact_id, committed["impact_id"])
            self.assertEqual(True, committed["approved"])
            self.assertEqual("passed", committed["acceptance_status"])

    def test_nonempty_but_unauditable_authorization_references_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            self.stage(root, "maintainer/runtime/example.py", "value = 1\n")
            service = StoryGateService(root)
            impact = service.inspect("staged", enforce=False)
            impact_id = str(impact["impact_id"])

            for reference in (
                "AO-11-comment-46645",
                "jira-comment:AO-11:46645",
                "jira-comment:ao-11:46645",
                "jira-comment:AO-11:comment-46645",
                "jira-comment:AO-11:id/unsafe",
                f"user-confirmation:ao-11:{impact_id}",
            ):
                with self.subTest(reference=reference):
                    with self.assertRaises(RuntimeErrorResult) as captured:
                        service.approve("staged", impact_id, reference)
                    self.assertEqual(
                        "story_authorization_reference_invalid", captured.exception.code
                    )

    def test_user_confirmation_must_bind_current_impact_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            self.stage(root, "maintainer/runtime/example.py", "value = 1\n")
            service = StoryGateService(root)
            impact = service.inspect("staged", enforce=False)
            impact_id = str(impact["impact_id"])

            with self.assertRaises(RuntimeErrorResult) as captured:
                service.approve(
                    "staged",
                    impact_id,
                    f"user-confirmation:AO-11:{'0' * 64}",
                )
            self.assertEqual(
                "story_authorization_impact_mismatch", captured.exception.code
            )

            approved = service.approve(
                "staged",
                impact_id,
                f"user-confirmation:AO-11:{impact_id}",
            )
            approval = json.loads(Path(approved["approval_path"]).read_text(encoding="utf-8"))
            self.assertEqual(3, approval["schema_version"])
            self.assertEqual("user_confirmation", approval["authorization_kind"])
            self.assertEqual("AO-11", approval["authorization_issue_key"])
            self.assertEqual(impact_id, approval["authorization_record_id"])

    def test_tampered_authorization_record_does_not_open_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            self.stage(root, "maintainer/runtime/example.py", "value = 1\n")
            service = StoryGateService(root)
            impact = service.inspect("staged", enforce=False)
            impact_id = str(impact["impact_id"])
            approved = service.approve(
                "staged", impact_id, f"user-confirmation:AO-11:{impact_id}"
            )
            approval_path = Path(approved["approval_path"])
            payload = json.loads(approval_path.read_text(encoding="utf-8"))
            payload.update(
                {
                    "schema_version": 2,
                    "authorization_reference": "jira-comment:AO-11:46645",
                    "authorization_kind": "jira_comment",
                    "authorization_issue_key": "AO-11",
                    "authorization_record_id": "46645",
                }
            )
            approval_path.write_text(json.dumps(payload), encoding="utf-8")

            inspected = service.inspect("staged", enforce=False)
            self.assertEqual(False, inspected["approved"])

    def test_cli_blocked_output_contains_impact_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            maintainer_entry = root / "maintainer" / "AGENTS.md"
            maintainer_entry.write_text("# maintainer\n", encoding="utf-8")
            self.stage(root, "developer/skills/example/SKILL.md", "# 测试\n")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(
                    [
                        "--source-root",
                        str(root),
                        "story",
                        "impact",
                        "--change-source",
                        "staged",
                    ]
                )
            result = json.loads(stdout.getvalue())
            self.assertEqual(2, exit_code)
            self.assertEqual("maintenance_story_impacted", result["code"])
            self.assertEqual(["DE-001"], result["impacted_story_ids"])
            self.assertTrue(result["impact_id"])

    def test_current_registry_maps_all_workplane_assets_and_future_paths(self) -> None:
        registry = load_story_registry(self.ROOT)
        patterns = tuple(
            pattern
            for story in registry.stories
            for pattern in story.protected_paths
        )
        candidates = [
            path.relative_to(self.ROOT).as_posix()
            for workplane in ("maintainer", "developer", "shared")
            for path in (self.ROOT / workplane).rglob("*")
            if path.is_file()
        ]
        candidates.extend(
            (
                "maintainer/future/asset.txt",
                "developer/future/asset.txt",
                "shared/future/asset.txt",
                "agent-guides.md",
                "agent-init.md",
                "docs/ai-working-rules.md",
                "docs/configuration-standards.md",
                "docs/configuration/ao-agentic-defect-jira-configuration.md",
                "docs/contracts/operation-contract.md",
                "docs/decision-log.md",
                "docs/development-engineers/getting-started.md",
                "docs/development-style.md",
                "docs/examples/end-to-end-demo.md",
                "docs/examples/v0.3-ao-pilot-result.md",
                "docs/examples/v0.4-ao-pilot-result.md",
                "docs/forms/task-form-standard.md",
                "docs/maintainers/getting-started.md",
                "docs/processes/standard-process-registry.md",
                "docs/profiles/workflow-profile.md",
            )
        )
        unmapped = [
            path
            for path in candidates
            if not any(path_matches(pattern, path) for pattern in patterns)
        ]
        self.assertEqual([], unmapped)

    def test_acceptance_commands_use_migrated_maintainer_scripts(self) -> None:
        self.assertEqual(
            [str(self.ROOT / "maintainer" / "scripts" / "test-python-runtime.sh")],
            _check_command(self.ROOT, "python_runtime"),
        )
        self.assertEqual(
            [str(self.ROOT / "maintainer" / "scripts" / "test-resources.sh")],
            _check_command(self.ROOT, "resource_contracts"),
        )
        self.assertEqual(
            [str(self.ROOT / "maintainer" / "scripts" / "test-release-workflow.sh")],
            _check_command(self.ROOT, "release_workflow"),
        )


if __name__ == "__main__":
    unittest.main()

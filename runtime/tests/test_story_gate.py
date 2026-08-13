from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from agentic_ops.cli import main
from agentic_ops.output import RuntimeErrorResult
from agentic_ops.story_gate.service import StoryGateService

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
  - project_maintenance
  - development_engineer
stories:
  - story_id: PM-001
    category: project_maintenance
    title: 维护测试
    document: docs/user-stories/project-maintainer/pm-001.md
    protected_paths: [runtime/**]
    acceptance_checks: [story_registry]
    evidence_requirements: [维护验收]
  - story_id: DE-001
    category: development_engineer
    title: 研发测试
    document: docs/user-stories/development-engineer/de-001.md
    protected_paths: [skills/**]
    acceptance_checks: [story_registry]
    evidence_requirements: [研发验收]
"""


class StoryGateTest(unittest.TestCase):
    def prepare(self, root: Path) -> None:
        marker = root / "docs" / "strategy" / "project-goals.md"
        pm_story = root / "docs" / "user-stories" / "project-maintainer" / "pm-001.md"
        de_story = root / "docs" / "user-stories" / "development-engineer" / "de-001.md"
        registry = root / "standards" / "stories" / "project-quality.yaml"
        for path in (marker, pm_story, de_story, registry):
            path.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("# 项目目标\n", encoding="utf-8")
        pm_story.write_text(STORY_BODY.format(story_id="PM-001"), encoding="utf-8")
        de_story.write_text(STORY_BODY.format(story_id="DE-001"), encoding="utf-8")
        registry.write_text(REGISTRY, encoding="utf-8")
        self.git(root, "init")
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

    def stage(self, root: Path, relative_path: str, content: str) -> None:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.git(root, "add", relative_path)

    def test_impacted_story_requires_approval_and_matching_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            self.stage(root, "runtime/example.py", "value = 1\n")
            service = StoryGateService(root)
            with self.assertRaises(RuntimeErrorResult) as captured:
                service.inspect("staged")
            self.assertEqual("maintenance_story_impacted", captured.exception.code)
            impact_id = str(captured.exception.details["impact_id"])

            approved = service.approve("staged", impact_id, "AO-11-comment-46645")
            self.assertEqual(True, approved["approved"])
            with self.assertRaises(RuntimeErrorResult) as not_verified:
                service.inspect("staged")
            self.assertEqual("maintenance_story_acceptance_failed", not_verified.exception.code)

            with mock.patch(
                "agentic_ops.story_gate.service._check_command",
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
            self.stage(root, "scripts/new-check.sh", "#!/usr/bin/env bash\n")
            with self.assertRaises(RuntimeErrorResult) as captured:
                StoryGateService(root).inspect("staged")
            self.assertEqual("maintenance_story_mapping_missing", captured.exception.code)
            self.assertEqual(3, captured.exception.exit_code)

    def test_content_change_invalidates_old_impact_and_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            self.stage(root, "runtime/example.py", "value = 1\n")
            service = StoryGateService(root)
            initial = service.inspect("staged", enforce=False)
            service.approve("staged", str(initial["impact_id"]), "AO-11-comment-46645")
            self.stage(root, "runtime/example.py", "value = 2\n")
            changed = service.inspect("staged", enforce=False)
            self.assertNotEqual(initial["impact_id"], changed["impact_id"])
            self.assertEqual(False, changed["approved"])

    def test_cli_blocked_output_contains_impact_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            self.stage(root, "skills/example/SKILL.md", "# 测试\n")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(
                    [
                        "--workspace-root",
                        str(root),
                        "--mode",
                        "source_maintenance",
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


if __name__ == "__main__":
    unittest.main()

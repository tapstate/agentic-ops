from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from internal.story_gate.branch_policy import resolve_branch_review
from internal.story_gate.errors import StoryGateError
from internal.story_gate.registry import load_story_registry, path_matches
from internal.story_gate.service import StoryGateService, _check_command


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
story_categories: [internal, product]
stories:
  - story_id: INT-001
    category: internal
    title: 内部治理
    document: docs/user-stories/v1/int-001.md
    protected_paths: [internal/**]
    acceptance_checks: [story_registry]
    evidence_requirements: [内部验收]
  - story_id: PROD-001
    category: product
    title: 产品能力
    document: docs/user-stories/v1/prod-001.md
    protected_paths: [gate/**]
    acceptance_checks: [story_registry]
    evidence_requirements: [产品验收]
"""

POLICY = """\
schema_version: 1
default_target_branch: develop
protected_branches: [main]
commit_review_branches: [develop]
pr_review_branches:
  - pattern: '^(?!(?:feature|fix)/)[^/]+/AO-[1-9][0-9]+([-/].+)?$'
    target_branch: develop
special_branch_patterns:
  - pattern: '^release/.+$'
    target_branch: main
"""


class StoryGateTest(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[2]

    def prepare(self, root: Path) -> None:
        paths = {
            root / ".agentic-ops-source": "source\n",
            root / "docs/strategy/project-goals.md": "# 项目目标\n",
            root / "docs/user-stories/v1/int-001.md": STORY_BODY.format(story_id="INT-001"),
            root / "docs/user-stories/v1/prod-001.md": STORY_BODY.format(story_id="PROD-001"),
            root / "internal/story_gate/stories.yaml": REGISTRY,
            root / "internal/story_gate/review-policy.yaml": POLICY,
        }
        for path, content in paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self.git(root, "init", "-q", "-b", "develop")
        self.git(root, "remote", "add", "origin", "git@github.com:tapstate/agentic-ops.git")
        self.git(root, "config", "user.email", "test@example.test")
        self.git(root, "config", "user.name", "Test")
        self.git(root, "add", ".")
        self.git(root, "commit", "-qm", "baseline")

    def git(self, root: Path, *arguments: str) -> None:
        subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True)

    def git_output(self, root: Path, *arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *arguments], check=True,
            capture_output=True, text=True,
        ).stdout.strip()

    def stage(self, root: Path, relative_path: str, content: str) -> None:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.git(root, "add", relative_path)

    def test_registry_uses_single_product_architecture_categories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            registry = load_story_registry(root)
            self.assertEqual({"internal", "product"}, {story.category for story in registry.stories})
            self.assertTrue(path_matches("gate/**", "gate/runner.py"))

    def test_staged_change_maps_to_product_story(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            self.stage(root, "gate/engine.py", "ALLOW = 'allow'\n")
            result = StoryGateService(root).inspect("staged", enforce=False)
            self.assertEqual(["PROD-001"], result["impacted_story_ids"])
            self.assertEqual(["product"], result["impacted_categories"])

    def test_unmapped_change_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            self.stage(root, "unknown/file.txt", "x\n")
            with self.assertRaises(StoryGateError) as captured:
                StoryGateService(root).inspect("staged")
            self.assertEqual("story_mapping_missing", captured.exception.code)

    def test_verify_binds_evidence_to_exact_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            self.stage(root, "internal/tool.py", "VALUE = 1\n")
            with mock.patch("internal.story_gate.service._check_command", return_value=["/usr/bin/true"]):
                verified = StoryGateService(root).verify("staged")
            self.assertEqual("passed", verified["acceptance_status"])
            os.environ["AGENTIC_OPS_STORY_GATE_STAGE"] = "pre_commit"
            try:
                inspected = StoryGateService(root).inspect("staged")
            finally:
                os.environ.pop("AGENTIC_OPS_STORY_GATE_STAGE", None)
            self.assertEqual("passed", inspected["acceptance_status"])
            self.stage(root, "internal/tool.py", "VALUE = 2\n")
            with self.assertRaises(StoryGateError) as captured:
                os.environ["AGENTIC_OPS_STORY_GATE_STAGE"] = "pre_commit"
                try:
                    StoryGateService(root).inspect("staged")
                finally:
                    os.environ.pop("AGENTIC_OPS_STORY_GATE_STAGE", None)
            self.assertEqual("story_acceptance_missing", captured.exception.code)

    def test_commit_review_confirmation_binds_commit_sha(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            base = self.git_output(root, "rev-parse", "HEAD")
            self.stage(root, "internal/tool.py", "VALUE = 1\n")
            self.git(root, "commit", "-qm", "candidate")
            head = self.git_output(root, "rev-parse", "HEAD")
            with mock.patch("internal.story_gate.service._check_command", return_value=["/usr/bin/true"]):
                verified = StoryGateService(root).verify("range", base=base, head=head)
            approved = StoryGateService(root).approve(
                "range", verified["impact_id"], f"user-confirmation:AO-11:commit:{head}",
                base=base, head=head,
            )
            self.assertTrue(approved["approved"])
            self.assertEqual(head, approved["authorization_record_id"])

    def test_review_channels_and_acceptance_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            self.assertEqual("commit_review", resolve_branch_review(root).channel)
        self.assertEqual([str(self.ROOT / "internal/tests/test_runtime.sh")], _check_command(self.ROOT, "python_runtime"))
        self.assertEqual([str(self.ROOT / "internal/tests/test_resources.sh")], _check_command(self.ROOT, "resource_contracts"))
        self.assertEqual([str(self.ROOT / "internal/tests/test_release.sh")], _check_command(self.ROOT, "release_workflow"))


if __name__ == "__main__":
    unittest.main()

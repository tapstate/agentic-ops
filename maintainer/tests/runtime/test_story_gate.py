from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from ao_maint.cli import main
from ao_maint.output import RuntimeErrorResult
from ao_maint.story_gate.registry import load_story_registry, path_matches
from ao_maint.story_gate.branch_policy import PullRequestFact, resolve_branch_review
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

POLICY = """\
schema_version: 1
default_target_branch: develop
protected_branches: [main]
commit_review_branches: [develop]
pr_review_branches:
  - pattern: '^codex/AO-[1-9][0-9]+([-/].+)?$'
    target_branch: develop
special_branch_patterns:
  - pattern: '^release/.+$'
    target_branch: main
"""


class StoryGateTest(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[3]

    def prepare(self, root: Path) -> None:
        source_marker = root / ".agentic-ops-source"
        goal = root / "docs" / "strategy" / "project-goals.md"
        pm_story = root / "docs" / "user-stories" / "project-maintainer" / "pm-001.md"
        de_story = root / "docs" / "user-stories" / "development-engineer" / "de-001.md"
        registry = root / "maintainer" / "standards" / "stories" / "project-quality.yaml"
        policy = root / "maintainer" / "standards" / "git" / "story-review-policy.yaml"
        for path in (goal, pm_story, de_story, registry, policy):
            path.parent.mkdir(parents=True, exist_ok=True)
        source_marker.write_text("maintainer\n", encoding="utf-8")
        goal.write_text("# 项目目标\n", encoding="utf-8")
        pm_story.write_text(STORY_BODY.format(story_id="PM-001"), encoding="utf-8")
        de_story.write_text(STORY_BODY.format(story_id="DE-001"), encoding="utf-8")
        registry.write_text(REGISTRY, encoding="utf-8")
        policy.write_text(POLICY, encoding="utf-8")
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
        self.git(root, "branch", "-M", "develop")

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

    def test_candidate_is_verified_before_commit_and_confirmed_after_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            self.stage(root, "maintainer/runtime/example.py", "value = 1\n")
            service = StoryGateService(root)
            candidate = service.inspect("staged")
            self.assertFalse(candidate["confirmation_required"])
            self.assertFalse(candidate["approval_ready"])
            self.assertIn("不得请求人工确认", candidate["required_human_action"])

            with mock.patch(
                "ao_maint.story_gate.service._check_command",
                return_value=["/usr/bin/true"],
            ):
                verified = service.verify("staged")
            self.assertEqual("passed", verified["acceptance_status"])
            base = self.git_output(root, "rev-parse", "HEAD")
            self.git(root, "commit", "-m", "candidate")
            head = self.git_output(root, "rev-parse", "HEAD")
            review = service.inspect("range", base=base, head=head)
            self.assertTrue(review["approval_ready"])
            self.assertTrue(review["confirmation_required"])
            reference = f"user-confirmation:AO-11:commit:{head}"
            approved = service.approve("range", review["impact_id"], reference, base=base, head=head)
            self.assertTrue(approved["approved"])
            self.assertEqual(head, approved["authorization_record_id"])

    def test_verify_collects_large_check_output_without_pipe_deadlock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            self.stage(root, "maintainer/runtime/example.py", "value = 1\n")
            command = [sys.executable, "-c", "import sys; sys.stdout.write('x' * 131072)"]
            with mock.patch("ao_maint.story_gate.service._check_command", return_value=command):
                result = StoryGateService(root).verify("staged")
            check = result["checks"][0]
            self.assertTrue(check["passed"])
            log = root / check["log_path"]
            self.assertTrue(log.is_file())
            self.assertGreater(check["log_end"], check["log_start"])
            self.assertIn("x" * 1024, log.read_text(encoding="utf-8"))

    def test_verify_persists_compact_events_and_single_output_log(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            self.stage(root, "maintainer/runtime/example.py", "value = 1\n")
            events: list[dict[str, object]] = []
            with mock.patch("ao_maint.story_gate.service._check_command", return_value=["/usr/bin/true"]):
                result = StoryGateService(root).verify("staged", event_sink=events.append)

            evidence = Path(result["evidence_path"])
            run_dir = root / result["checks"][0]["log_path"]
            persisted_events = [json.loads(line) for line in (run_dir.parent / "events.ndjson").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(["check_started", "check_finished", "verify_completed"], [event["event"] for event in events])
            self.assertEqual([event["event"] for event in events], [event["event"] for event in persisted_events])
            self.assertEqual(1, len({check["log_path"] for check in result["checks"]}))
            self.assertIn("log_sha256", result["checks"][0])
            self.assertNotIn("output_tail", json.dumps(json.loads(evidence.read_text(encoding="utf-8"))))

    def test_story_document_change_requires_revision_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            self.stage(
                root,
                "docs/user-stories/project-maintainer/pm-001.md",
                STORY_BODY.format(story_id="PM-001") + "\n补充故事。\n",
            )
            result = StoryGateService(root).inspect("staged")
            self.assertEqual(["PM-001"], result["revision_story_ids"])
            self.assertFalse(result["confirmation_required"])
            self.assertEqual("维护测试", result["review_report"]["impacted_stories"][0]["title"])

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
            self.stage(root, "maintainer/runtime/example.py", "value = 2\n")
            changed = service.inspect("staged", enforce=False)
            self.assertNotEqual(initial["impact_id"], changed["impact_id"])
            self.assertEqual(False, changed["approved"])

    def test_staged_evidence_matches_post_commit_review_object(self) -> None:

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            baseline = self.git_output(root, "rev-parse", "HEAD")
            self.stage(root, "maintainer/runtime/example.py", "value = 'migration'\n")
            service = StoryGateService(root)
            impact = service.inspect("staged", enforce=False)
            impact_id = str(impact["impact_id"])
            with mock.patch(
                "ao_maint.story_gate.service._check_command",
                return_value=["/usr/bin/true"],
            ):
                service.verify("staged")
            allowed = service.inspect("staged")
            self.assertEqual("passed", allowed["acceptance_status"])
            self.git(root, "commit", "-m", "install story gate baseline")
            head = self.git_output(root, "rev-parse", "HEAD")
            committed = service.inspect("range", base=baseline, head=head)
            self.assertEqual(impact_id, committed["impact_id"])
            self.assertEqual(False, committed["approved"])
            self.assertEqual("passed", committed["acceptance_status"])
            self.assertEqual(head, committed["review_report"]["review_object"]["commit_sha"])
            self.assertTrue(committed["confirmation_required"])

    def test_nonempty_but_unauditable_authorization_references_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            self.stage(root, "maintainer/runtime/example.py", "value = 1\n")
            service = StoryGateService(root)
            baseline = self.git_output(root, "rev-parse", "HEAD")
            with mock.patch("ao_maint.story_gate.service._check_command", return_value=["/usr/bin/true"]):
                service.verify("staged")
            self.git(root, "commit", "-m", "candidate")
            head = self.git_output(root, "rev-parse", "HEAD")
            impact = service.inspect("range", base=baseline, head=head)
            impact_id = str(impact["impact_id"])

            for reference in (
                "AO-11-comment-46645",
                "jira-comment:AO-11:46645",
                "jira-comment:ao-11:46645",
                "jira-comment:AO-11:comment-46645",
                "jira-comment:AO-11:id/unsafe",
                f"user-confirmation:ao-11:commit:{head}",
                f"user-confirmation:AO-11:commit:{'0' * 40}",
            ):
                with self.subTest(reference=reference):
                    with self.assertRaises(RuntimeErrorResult) as captured:
                        service.approve("range", impact_id, reference, base=baseline, head=head)
                    self.assertEqual(
                        "story_authorization_reference_invalid", captured.exception.code
                    )

    def test_user_confirmation_binds_reviewed_commit_not_impact_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            self.stage(root, "maintainer/runtime/example.py", "value = 1\n")
            service = StoryGateService(root)
            baseline = self.git_output(root, "rev-parse", "HEAD")
            with mock.patch("ao_maint.story_gate.service._check_command", return_value=["/usr/bin/true"]):
                service.verify("staged")
            self.git(root, "commit", "-m", "candidate")
            head = self.git_output(root, "rev-parse", "HEAD")
            impact = service.inspect("range", base=baseline, head=head)
            impact_id = str(impact["impact_id"])

            with self.assertRaises(RuntimeErrorResult) as captured:
                service.approve(
                    "range",
                    impact_id,
                    f"user-confirmation:AO-11:commit:{'0' * 40}",
                    base=baseline,
                    head=head,
                )
            self.assertEqual("story_authorization_reference_invalid", captured.exception.code)

            approved = service.approve(
                "range",
                impact_id,
                f"user-confirmation:AO-11:commit:{head}",
                base=baseline,
                head=head,
            )
            approval = json.loads(Path(approved["approval_path"]).read_text(encoding="utf-8"))
            self.assertEqual(4, approval["schema_version"])
            self.assertEqual("commit_confirmation", approval["authorization_kind"])
            self.assertEqual("AO-11", approval["authorization_issue_key"])
            self.assertEqual(head, approval["authorization_record_id"])
            self.assertEqual(4, len(approval["confirmation_items"]))

    def test_tampered_authorization_record_does_not_open_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            self.stage(root, "maintainer/runtime/example.py", "value = 1\n")
            service = StoryGateService(root)
            baseline = self.git_output(root, "rev-parse", "HEAD")
            with mock.patch("ao_maint.story_gate.service._check_command", return_value=["/usr/bin/true"]):
                service.verify("staged")
            self.git(root, "commit", "-m", "candidate")
            head = self.git_output(root, "rev-parse", "HEAD")
            impact = service.inspect("range", base=baseline, head=head)
            impact_id = str(impact["impact_id"])
            approved = service.approve(
                "range",
                impact_id,
                f"user-confirmation:AO-11:commit:{head}",
                base=baseline,
                head=head,
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

            inspected = service.inspect("range", base=baseline, head=head, enforce=False)
            self.assertEqual(False, inspected["approved"])

    def test_cli_candidate_output_contains_human_review_report(self) -> None:
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
            self.assertEqual(0, exit_code)
            self.assertEqual(["DE-001"], result["impacted_story_ids"])
            self.assertTrue(result["impact_id"])
            self.assertFalse(result["confirmation_required"])
            self.assertEqual("研发测试", result["review_report"]["impacted_stories"][0]["title"])
            self.assertIn("confirmation_items", result["review_report"])

    def test_cli_progress_writes_events_before_final_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            (root / "maintainer" / "AGENTS.md").write_text("# maintainer\n", encoding="utf-8")
            stdout = io.StringIO()

            def verify(_service: StoryGateService, _source: str, **kwargs: object) -> dict[str, object]:
                sink = kwargs["event_sink"]
                self.assertIsNotNone(sink)
                sink({"event": "check_started", "impact_id": "impact-1"})
                return {"impact_id": "impact-1", "acceptance_status": "passed"}

            with mock.patch.object(StoryGateService, "verify", verify), redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                exit_code = main(["--source-root", str(root), "story", "verify", "--progress"])
            lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
            self.assertEqual(0, exit_code)
            self.assertEqual("check_started", lines[0]["event"])
            self.assertTrue(lines[1]["ok"])
            self.assertEqual("impact-1", lines[1]["impact_id"])

    def test_versioned_branch_policy_selects_review_channel_and_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            cases = {
                "develop": ("commit_review", "develop"),
                "main": ("protected", "main"),
                "codex/AO-43-review": ("pr_review", "develop"),
                "release/v1.2": ("special", "main"),
            }
            for branch, expected in cases.items():
                with self.subTest(branch=branch), mock.patch.dict(
                    os.environ, {"AGENTIC_OPS_STORY_BRANCH": branch}
                ):
                    review = resolve_branch_review(root)
                    self.assertEqual(expected, (review.channel, review.target_branch))
            with mock.patch.dict(
                os.environ, {"AGENTIC_OPS_STORY_BRANCH": "unregistered-branch"}
            ):
                with self.assertRaises(ValueError):
                    resolve_branch_review(root)

    def test_pre_push_blocks_unconfirmed_commit_and_allows_exact_confirmed_sha(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            baseline = self.git_output(root, "rev-parse", "HEAD")
            self.stage(root, "maintainer/runtime/example.py", "value = 1\n")
            service = StoryGateService(root)
            with mock.patch("ao_maint.story_gate.service._check_command", return_value=["/usr/bin/true"]):
                service.verify("staged")
            self.git(root, "commit", "-m", "candidate")
            head = self.git_output(root, "rev-parse", "HEAD")
            review = service.inspect("range", base=baseline, head=head)
            with mock.patch.dict(os.environ, {"AGENTIC_OPS_STORY_GATE_STAGE": "pre_push"}):
                with self.assertRaises(RuntimeErrorResult) as captured:
                    service.inspect("range", base=baseline, head=head)
                self.assertEqual("story_commit_review_required", captured.exception.code)
            service.approve(
                "range",
                review["impact_id"],
                f"user-confirmation:AO-43:commit:{head}",
                base=baseline,
                head=head,
            )
            with mock.patch.dict(os.environ, {"AGENTIC_OPS_STORY_GATE_STAGE": "pre_push"}):
                allowed = service.inspect("range", base=baseline, head=head)
            self.assertTrue(allowed["approved"])

    def test_pr_review_is_bound_to_current_head_and_new_push_invalidates_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            self.git(root, "switch", "-c", "codex/AO-43-review")
            baseline = self.git_output(root, "rev-parse", "develop")
            self.stage(root, "maintainer/runtime/example.py", "value = 1\n")
            service = StoryGateService(root)
            with mock.patch("ao_maint.story_gate.service._check_command", return_value=["/usr/bin/true"]):
                service.verify("staged")
            self.git(root, "commit", "-m", "candidate")
            head = self.git_output(root, "rev-parse", "HEAD")
            pending = PullRequestFact(
                number=7,
                url="https://github.com/tapstate/agentic-ops/pull/7",
                head_sha=head,
                head_branch="codex/AO-43-review",
                base_branch="develop",
                approved_for_head=False,
            )
            with mock.patch(
                "ao_maint.story_gate.service.read_pull_request_fact", return_value=pending
            ):
                review = service.inspect("range", base=baseline, head=head)
            self.assertTrue(review["confirmation_required"])
            self.assertEqual(pending.url, review["review_report"]["review_object"]["url"])

            approved_fact = PullRequestFact(
                **{**pending.__dict__, "approved_for_head": True, "reviewers": ("reviewer",)}
            )
            with mock.patch(
                "ao_maint.story_gate.service.read_pull_request_fact", return_value=approved_fact
            ):
                service.approve(
                    "range",
                    review["impact_id"],
                    f"github-pr-review:AO-43:7:{head}",
                    base=baseline,
                    head=head,
                )
                accepted = service.inspect("range", base=baseline, head=head)
            self.assertTrue(accepted["approved"])

            self.stage(root, "maintainer/runtime/example.py", "value = 2\n")
            self.git(root, "commit", "-m", "new head")
            new_head = self.git_output(root, "rev-parse", "HEAD")
            with mock.patch("ao_maint.story_gate.service._check_command", return_value=["/usr/bin/true"]), mock.patch(
                "ao_maint.story_gate.service.read_pull_request_fact",
                return_value=PullRequestFact(
                    number=7,
                    url=pending.url,
                    head_sha=new_head,
                    head_branch="codex/AO-43-review",
                    base_branch="develop",
                ),
            ):
                service.verify("range", base=baseline, head=new_head)
                changed = service.inspect("range", base=baseline, head=new_head)
            self.assertFalse(changed["approved"])
            self.assertTrue(changed["confirmation_required"])

    def test_review_report_lists_required_sections_and_never_requests_raw_impact_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare(root)
            self.stage(root, "maintainer/runtime/example.py", "value = 1\n")
            result = StoryGateService(root).inspect("staged")
            report = result["review_report"]
            self.assertEqual(
                {
                    "changed_paths",
                    "impacted_stories",
                    "story_revisions",
                    "unmapped_paths",
                    "acceptance_checks",
                    "branch",
                    "review_object",
                    "confirmation_items",
                    "change_points",
                    "risks",
                    "allowed_next_action_after_confirmation",
                },
                set(report),
            )
            self.assertNotIn(result["impact_id"], result["required_human_action"])
            self.assertNotIn("确认 impact_id", json.dumps(result, ensure_ascii=False))

    def test_runtime_rejects_symlinked_local_story_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as outside:
            root = Path(temporary)
            self.prepare(root)
            self.stage(root, "maintainer/runtime/example.py", "value = 1\n")
            local = root / "maintainer" / ".local"
            local.mkdir(parents=True)
            (local / "story-evidence").symlink_to(Path(outside), target_is_directory=True)
            with self.assertRaises(RuntimeErrorResult) as captured:
                StoryGateService(root).inspect("staged")
            self.assertEqual("story_gate_local_state_unsafe", captured.exception.code)

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

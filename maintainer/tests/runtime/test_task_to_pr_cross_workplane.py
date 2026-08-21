from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from ao_maint.integration.service import IntegrationService
from ao_maint.integration.task_to_pr import manifest_digest, validate_manifest
from ao_maint.output import RuntimeErrorResult


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEVELOPER_PYTHON = REPOSITORY_ROOT / "developer" / ".venv" / "bin" / "python"
DEVELOPER_RUNTIME = REPOSITORY_ROOT / "developer" / "runtime" / "src"
PRODUCER = (
    REPOSITORY_ROOT
    / "developer"
    / "tests"
    / "fixtures"
    / "task_to_pr_producer.py"
)
ISSUE_KEY = "TAP-12289"
AGENT_ID = "harsen-mini-test-bot"


class DeveloperResultToMaintainerAcceptanceTest(unittest.TestCase):
    """Cross-workplane black box: the two Runtimes never share an import path."""

    def test_jira_identity_precondition_failure_is_accepted_as_early_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            produced = self._produce(Path(temporary), "early-blocked")
            self.assertEqual("early-blocked", produced["case"])
            self.assertEqual(
                "jira_probe_account_mismatch", produced["probe_error_code"]
            )
            self.assertEqual("blocked", produced["producer_result_status"])

            accepted = IntegrationService(REPOSITORY_ROOT).accept_task_to_pr(
                ISSUE_KEY,
                produced["manifest_path"],
                produced["result_path"],
            )

            self.assertEqual("accepted", accepted["package_status"])
            self.assertEqual("blocked", accepted["reported_result_status"])
            self.assertFalse(accepted["delivery_passed"])
            self.assertEqual(AGENT_ID, accepted["agent_id"])
            self.assertEqual(1, accepted["evidence_counts"]["failures"])
            self.assertIsNone(accepted["pr_url"])
            result = json.loads(
                Path(produced["result_path"]).read_text(encoding="utf-8")
            )
            retrospective = result["retrospective"]["event"]["action_data"]
            automation_review = next(
                review
                for review in retrospective["category_reviews"]
                if review["category"] == "automation_gap"
            )
            self.assertEqual("finding", automation_review["outcome"])
            self.assertTrue(
                set(retrospective["failure_event_ids"])
                <= set(automation_review["source_event_ids"])
            )

    def test_ready_result_with_jira_delivery_and_retry_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            produced = self._produce(Path(temporary), "ready")
            self.assertEqual("ready", produced["case"])
            self.assertEqual("ready_for_pr_review", produced["producer_result_status"])
            self.assertEqual(2, produced["verification_attempts"])

            accepted = IntegrationService(REPOSITORY_ROOT).accept_task_to_pr(
                ISSUE_KEY,
                produced["manifest_path"],
                produced["result_path"],
            )

            self.assertEqual("accepted", accepted["package_status"])
            self.assertEqual("ready_for_pr_review", accepted["reported_result_status"])
            self.assertTrue(accepted["delivery_passed"])
            self.assertTrue(accepted["formal_takeover_verified"])
            self.assertEqual(2, accepted["evidence_counts"]["verifications"])
            self.assertEqual(1, accepted["evidence_counts"]["failures"])
            self.assertEqual(
                "https://github.com/tapdata/tapdata/pull/12289",
                accepted["pr_url"],
            )

            result = json.loads(
                Path(produced["result_path"]).read_text(encoding="utf-8")
            )
            applied = {
                item["action"]
                for item in result["facts"]["external_actions"]
                if item["status"] == "applied"
            }
            self.assertIn("jira_comment", applied)
            self.assertIn("jira_worklog", applied)
            worklog = next(
                envelope["event"]["action_data"]
                for envelope in result["timeline"]
                if envelope["event"]["action"] == "jira_write_readback"
                and envelope["event"]["action_data"]["operation"] == "jira_worklog"
            )
            self.assertEqual(1800, worklog["time_spent_seconds"])
            self.assertEqual(
                1800,
                sum(item["seconds"] for item in worklog["included_work"]),
            )
            self.assertEqual(
                ["等待人工确认", "等待 CI"],
                worklog["excluded_waiting_categories"],
            )
            self.assertTrue(worklog["created"])
            self.assertEqual("absent", worklog["write_precondition"])
            self.assertTrue(worklog["attempt_file"].endswith(".attempt.json"))
            self.assertTrue(worklog["write_attempt_id"].startswith("attempt-"))
            self.assertTrue(worklog["write_attempt_started_at"])
            baseline = next(
                envelope["event"]["action_data"]
                for envelope in result["timeline"]
                if envelope["event"]["action"] == "prohibition_baseline"
            )
            self.assertIsNone(baseline["task_branch_remote_sha"])
            self.assertEqual(
                next(
                    item["sha"]
                    for item in baseline["protected_heads"]
                    if item["branch"] == "develop"
                ),
                baseline["local_head_sha"],
            )
            retrospective = result["retrospective"]["event"]["action_data"]
            automation_review = next(
                review
                for review in retrospective["category_reviews"]
                if review["category"] == "automation_gap"
            )
            self.assertEqual("finding", automation_review["outcome"])
            self.assertTrue(
                set(retrospective["failure_event_ids"])
                <= set(automation_review["source_event_ids"])
            )
            self.assertTrue(
                set(retrospective["retry_event_ids"])
                <= set(automation_review["source_event_ids"])
            )

    def test_manifest_mutation_corpus_has_the_same_fail_closed_boundary(self) -> None:
        """One corpus must be rejected before either workplane accepts the manifest."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            produced = self._produce(root, "ready")
            base = json.loads(
                Path(produced["manifest_path"]).read_text(encoding="utf-8")
            )
            control = root / "valid-manifest.json"
            control.write_text(json.dumps(base), encoding="utf-8")
            self.assertTrue(self._developer_accepts_manifest(control))
            validate_manifest(base, ISSUE_KEY)

            def set_agent_id_too_long(manifest: dict[str, object]) -> None:
                manifest["agent"]["agent_id"] = "a" * 129  # type: ignore[index]

            def set_profile_too_long(manifest: dict[str, object]) -> None:
                manifest["agent"]["project_profile"] = "a" * 129  # type: ignore[index]

            def duplicate_protected_branch(manifest: dict[str, object]) -> None:
                manifest["repository"]["protected_branches"] = [  # type: ignore[index]
                    "main",
                    "develop",
                    "develop",
                ]

            def set_invalid_protected_branch(manifest: dict[str, object]) -> None:
                manifest["repository"]["protected_branches"] = [  # type: ignore[index]
                    "main",
                    "develop",
                    "bad branch",
                ]

            def duplicate_included_scope(manifest: dict[str, object]) -> None:
                manifest["scope"]["included"] = ["src/**", "src/**"]  # type: ignore[index]

            def overlap_scope(manifest: dict[str, object]) -> None:
                manifest["scope"]["excluded"] = ["src/**"]  # type: ignore[index]

            def allow_case_variant_done(manifest: dict[str, object]) -> None:
                manifest["jira"]["allowed_status_categories"] = ["done"]  # type: ignore[index]

            def set_unsafe_remote_name(manifest: dict[str, object]) -> None:
                manifest["repository"]["remote_name"] = "-origin"  # type: ignore[index]

            mutations = (
                ("agent_id_too_long", set_agent_id_too_long),
                ("profile_too_long", set_profile_too_long),
                ("duplicate_protected_branch", duplicate_protected_branch),
                ("invalid_protected_branch", set_invalid_protected_branch),
                ("duplicate_included_scope", duplicate_included_scope),
                ("overlapping_scope", overlap_scope),
                ("case_variant_done", allow_case_variant_done),
                ("unsafe_remote_name", set_unsafe_remote_name),
            )
            for name, mutate in mutations:
                with self.subTest(name=name):
                    candidate = copy.deepcopy(base)
                    mutate(candidate)
                    candidate["authorization"][  # type: ignore[index]
                        "confirmed_manifest_sha256"
                    ] = manifest_digest(candidate)
                    path = root / f"{name}.json"
                    path.write_text(json.dumps(candidate), encoding="utf-8")

                    developer_accepted = self._developer_accepts_manifest(path)
                    try:
                        validate_manifest(candidate, ISSUE_KEY)
                    except RuntimeErrorResult:
                        maintainer_accepted = False
                    else:
                        maintainer_accepted = True
                    self.assertEqual(
                        developer_accepted,
                        maintainer_accepted,
                        f"developer 与 maintainer 接受边界不一致：{name}",
                    )
                    self.assertFalse(
                        developer_accepted,
                        f"危险 manifest 变异必须在外部动作前被拒绝：{name}",
                    )

    def _produce(self, temporary: Path, case: str) -> dict[str, object]:
        environment = {
            key: os.environ[key]
            for key in ("PATH", "TMPDIR", "LANG", "LC_ALL")
            if key in os.environ
        }
        environment["PYTHONPATH"] = str(DEVELOPER_RUNTIME)
        environment["PYTHONPYCACHEPREFIX"] = str(temporary / "pycache")
        completed = subprocess.run(
            [
                str(DEVELOPER_PYTHON),
                str(PRODUCER),
                "--case",
                case,
                "--root",
                str(temporary / "producer"),
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertIsInstance(payload, dict)
        return payload

    def _developer_accepts_manifest(self, path: Path) -> bool:
        environment = {
            key: os.environ[key]
            for key in ("PATH", "TMPDIR", "LANG", "LC_ALL")
            if key in os.environ
        }
        environment["PYTHONPATH"] = str(DEVELOPER_RUNTIME)
        completed = subprocess.run(
            [
                str(DEVELOPER_PYTHON),
                "-c",
                (
                    "import json,sys; "
                    "from ao_work.task_run.protocol import validate_manifest; "
                    "validate_manifest(json.load(open(sys.argv[1], encoding='utf-8')))"
                ),
                str(path),
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        return completed.returncode == 0


if __name__ == "__main__":
    unittest.main()

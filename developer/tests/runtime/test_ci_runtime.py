from __future__ import annotations

import base64
import io
import json
import subprocess
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from ao_work.output import RuntimeErrorResult
from ao_work.task_run.ci import (
    CiRuntime,
    extract_archive,
    parse_maven_failsafe,
    verify_extracted_artifact,
)
from ao_work.workspace import Workspace


SHA = "a" * 40
BASE_SHA = "b" * 40
BLOB_SHA = "c" * 40
LIMITS = {
    "max_archive_bytes": 1_048_576,
    "max_extracted_bytes": 2_097_152,
    "max_file_bytes": 1_048_576,
    "max_files": 20,
    "max_depth": 8,
}


def archive(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as handle:
        for name, content in entries.items():
            handle.writestr(name, content)
    return output.getvalue()


class CiArchiveTest(unittest.TestCase):
    def test_extracts_regular_zip_and_rejects_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "evidence"
            result = extract_archive(archive({"reports/TEST-demo.xml": b"ok"}), destination, LIMITS)
            self.assertEqual("reports/TEST-demo.xml", result[0]["path"])
            with self.assertRaises(RuntimeErrorResult) as captured:
                extract_archive(archive({"other.txt": b"changed"}), destination, LIMITS)
            self.assertEqual("ci_artifact_state_conflict", captured.exception.code)

    def test_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(RuntimeErrorResult) as captured:
                extract_archive(
                    archive({"../outside.txt": b"unsafe"}),
                    Path(temporary) / "evidence",
                    LIMITS,
                )
            self.assertEqual("ci_artifact_unsafe", captured.exception.code)
            self.assertFalse((Path(temporary) / "outside.txt").exists())

    def test_rejects_zip_symlink(self) -> None:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as handle:
            item = zipfile.ZipInfo("reports/link")
            item.create_system = 3
            item.external_attr = 0o120777 << 16
            handle.writestr(item, "target")
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(RuntimeErrorResult) as captured:
                extract_archive(output.getvalue(), Path(temporary) / "evidence", LIMITS)
            self.assertEqual("ci_artifact_unsafe", captured.exception.code)

    def test_rejects_declared_file_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            limits = {**LIMITS, "max_file_bytes": 1024}
            with self.assertRaises(RuntimeErrorResult) as captured:
                extract_archive(
                    archive({"large.bin": b"x" * 1025}),
                    Path(temporary) / "evidence",
                    limits,
                )
            self.assertEqual("ci_artifact_limit_exceeded", captured.exception.code)

    def test_detects_extracted_evidence_tampering_before_parse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "evidence"
            files = extract_archive(
                archive({"reports/TEST-demo.xml": b"original"}),
                destination,
                LIMITS,
            )
            verify_extracted_artifact(destination, files, LIMITS)
            (destination / "reports/TEST-demo.xml").write_bytes(b"changed")
            with self.assertRaises(RuntimeErrorResult) as captured:
                verify_extracted_artifact(destination, files, LIMITS)
            self.assertEqual("ci_artifact_digest_mismatch", captured.exception.code)


class MavenFailsafeParserTest(unittest.TestCase):
    def test_versioned_fixture_recognizes_summary_xml_and_text(self) -> None:
        root = Path(__file__).resolve().parents[1] / "fixtures/ci/maven-failsafe-v1"
        report = parse_maven_failsafe(root, "c" * 64)
        self.assertEqual({"tests": 1, "failures": 1, "errors": 0, "skipped": 0}, {
            field: report[field] for field in ("tests", "failures", "errors", "skipped")
        })
        self.assertEqual(3, len(report["report_files"]))
        self.assertEqual(24, len(report["failed_tests"][0]["failure_fingerprint"]))

    def test_normalizes_failure_and_redacts_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "TEST-demo.xml").write_text(
                '<testsuite name="Demo" tests="1" failures="1" errors="0" skipped="0">'
                '<testcase classname="DemoIT" name="fails">'
                '<failure type="AssertionError" message="token=super-secret-value" />'
                "</testcase></testsuite>",
                encoding="utf-8",
            )
            report = parse_maven_failsafe(root, "b" * 64)
            self.assertEqual(1, report["failures"])
            self.assertEqual("[REDACTED]", report["failed_tests"][0]["message"])

    def test_rejects_dtd_and_summary_conflict(self) -> None:
        cases = {
            "dtd": '<!DOCTYPE foo><testsuite tests="0" failures="0" errors="0" skipped="0"/>',
            "conflict": '<testsuite tests="1" failures="1" errors="0" skipped="0"><testcase name="ok"/></testsuite>',
        }
        for name, content in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "TEST-demo.xml").write_text(content, encoding="utf-8")
                with self.assertRaises(RuntimeErrorResult) as captured:
                    parse_maven_failsafe(root, "b" * 64)
                self.assertEqual("ci_report_parse_failed", captured.exception.code)


class CiObservationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.workspace = Workspace(root, "developer", root / ".agentic-ops/agent.json")
        self.repository = root / "source"
        self.repository.mkdir()
        self.manifest = {
            "process_id": "development_change_v2",
            "issue": {"key": "TAP-12620"},
            "agent": {"agentic_run_id": "run-tap-12620"},
            "authorization": {"reference": "AO-76-confirmed-design"},
            "repository": {
                "root": str(self.repository),
                "slug": "tapdata/tapdata",
                "task_branch": "codex/TAP-12620/fix",
                "target_branch": "develop",
            },
            "pr_endpoint": {
                "ci": {
                    "provider": "github-actions",
                    "start_timeout_seconds": 300,
                    "completion_timeout_seconds": 600,
                    "poll_interval_seconds": 30,
                    "max_remediation_attempts": 2,
                    "required_checks": ["integration-test"],
                    "workflows": ["Integration Tests"],
                    "artifact_name_patterns": ["failsafe-*"],
                    "report_parser": "maven-failsafe-v1",
                    "limits": LIMITS,
                    "completion": {
                        "finish_agent_run_on_pass": True,
                        "transition_jira_done": False,
                    },
                }
            },
        }

    @staticmethod
    def completed(payload: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, json.dumps(payload), "")

    def runtime(
        self,
        conclusion: str,
        *,
        head_sha: str = SHA,
        now: datetime | None = None,
    ) -> CiRuntime:
        pr = {
            "number": 7,
            "url": "https://github.com/tapdata/tapdata/pull/7",
            "state": "OPEN",
            "isDraft": False,
            "mergedAt": None,
            "headRefName": "codex/TAP-12620/fix",
            "headRefOid": head_sha,
            "baseRefName": "develop",
            "baseRefOid": BASE_SHA,
            "statusCheckRollup": [
                {
                    "name": "integration-test",
                    "status": "COMPLETED" if conclusion else "IN_PROGRESS",
                    "conclusion": conclusion,
                }
            ],
        }
        runs = [
            {
                "databaseId": 101,
                "workflowName": "Integration Tests",
                "headSha": head_sha,
                "status": "completed",
                "conclusion": conclusion,
                "url": "https://github.com/tapdata/tapdata/actions/runs/101",
                "createdAt": "2026-08-24T00:00:00Z",
                "updatedAt": "2026-08-24T00:01:00Z",
            }
        ]
        workflow = "name: Integration Tests\non: [pull_request]\njobs: {}\n"
        values = iter((
            self.completed(pr),
            self.completed({
                "truncated": False,
                "tree": [{
                    "path": ".github/workflows/integration.yml",
                    "type": "blob",
                    "sha": BLOB_SHA,
                }],
            }),
            self.completed({
                "sha": BLOB_SHA,
                "encoding": "base64",
                "content": base64.b64encode(workflow.encode()).decode(),
            }),
            self.completed(runs),
        ))
        return CiRuntime(
            self.workspace,
            lock_timeout=1,
            run_text=lambda _argv, _cwd, _timeout: next(values),
            run_bytes=lambda _argv, _cwd, _timeout, _maximum: subprocess.CompletedProcess([], 0, b"", b""),
            now=lambda: now or datetime(2026, 8, 24, tzinfo=timezone.utc),
        )

    def test_pass_requires_exact_success_and_binds_completion(self) -> None:
        runtime = self.runtime("SUCCESS")
        observed = runtime.probe(self.manifest)
        self.assertEqual("passed", observed["ci_status"])
        evidence = runtime.validate_completion(self.manifest, SHA)
        self.assertEqual(SHA, evidence["head_sha"])

    def test_skipped_is_failure_not_success(self) -> None:
        observed = self.runtime("SKIPPED").probe(self.manifest)
        self.assertEqual("failed", observed["ci_status"])

    def no_ci_runtime(self, now: datetime) -> CiRuntime:
        responses = iter(
            (
                self.completed({
                "number": 7,
                "url": "https://github.com/tapdata/tapdata/pull/7",
                "state": "OPEN",
                "isDraft": False,
                "mergedAt": None,
                "headRefName": "codex/TAP-12620/fix",
                "headRefOid": SHA,
                "baseRefName": "develop",
                "baseRefOid": BASE_SHA,
                "statusCheckRollup": [],
                }),
                self.completed({"truncated": False, "tree": []}),
            )
        )
        return CiRuntime(
            self.workspace,
            lock_timeout=1,
            run_text=lambda _argv, _cwd, _timeout: next(responses),
            run_bytes=lambda _argv, _cwd, _timeout, _maximum: subprocess.CompletedProcess([], 0, b"", b""),
            now=lambda: now,
        )

    def configured_ci_runtime(
        self,
        now: datetime,
        *,
        workflow_name: str = "Integration Tests",
        trigger: str = "pull_request",
        conditional_trigger: bool = False,
    ) -> CiRuntime:
        workflow = (
            f"name: {workflow_name}\non:\n  {trigger}:\n    paths: [src/**]\njobs: {{}}\n"
            if conditional_trigger
            else f"name: {workflow_name}\non: [{trigger}]\njobs: {{}}\n"
        )
        responses = iter(
            (
                self.completed({
                    "number": 7,
                    "url": "https://github.com/tapdata/tapdata/pull/7",
                    "state": "OPEN",
                    "isDraft": False,
                    "mergedAt": None,
                    "headRefName": "codex/TAP-12620/fix",
                    "headRefOid": SHA,
                    "baseRefName": "develop",
                    "baseRefOid": BASE_SHA,
                    "statusCheckRollup": [],
                }),
                self.completed({
                    "truncated": False,
                    "tree": [{
                        "path": ".github/workflows/integration.yml",
                        "type": "blob",
                        "sha": BLOB_SHA,
                    }],
                }),
                self.completed({
                    "sha": BLOB_SHA,
                    "encoding": "base64",
                    "content": base64.b64encode(workflow.encode()).decode(),
                }),
                self.completed([]),
            )
        )
        return CiRuntime(
            self.workspace,
            lock_timeout=1,
            run_text=lambda _argv, _cwd, _timeout: next(responses),
            run_bytes=lambda _argv, _cwd, _timeout, _maximum: subprocess.CompletedProcess([], 0, b"", b""),
            now=lambda: now,
        )

    def test_github_pr_without_workflows_skips_ci(self) -> None:
        observed = self.no_ci_runtime(
            datetime(2026, 8, 24, tzinfo=timezone.utc)
        ).probe(self.manifest)
        self.assertEqual("not_required", observed["ci_status"])
        self.assertEqual("completed", observed["current_stage"])
        self.assertEqual("base_has_no_github_actions_workflows", observed["ci_requirement"]["reason"])
        evidence = self.no_ci_runtime(
            datetime(2026, 8, 24, 0, 5, 1, tzinfo=timezone.utc)
        ).validate_completion(self.manifest, SHA)
        self.assertEqual("not_required", evidence["ci_status"])
        self.assertIsNone(evidence["start_deadline_at"])

    def test_configured_ci_must_start_within_five_minutes(self) -> None:
        observed = self.configured_ci_runtime(
            datetime(2026, 8, 24, tzinfo=timezone.utc)
        ).probe(self.manifest)
        self.assertEqual("waiting_to_start", observed["ci_status"])
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.configured_ci_runtime(
                datetime(2026, 8, 24, 0, 5, 1, tzinfo=timezone.utc)
            ).probe(self.manifest)
        self.assertEqual("ci_start_timeout", captured.exception.code)

    def test_manual_only_workflow_does_not_require_ci(self) -> None:
        observed = self.configured_ci_runtime(
            datetime(2026, 8, 24, tzinfo=timezone.utc),
            trigger="workflow_dispatch",
        ).probe(self.manifest)
        self.assertEqual("not_required", observed["ci_status"])
        self.assertEqual(
            "configured_workflows_do_not_trigger_for_pr_head",
            observed["ci_requirement"]["reason"],
        )

    def test_workflow_mapping_drift_requires_human(self) -> None:
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.configured_ci_runtime(
                datetime(2026, 8, 24, tzinfo=timezone.utc),
                workflow_name="Other Workflow",
            ).probe(self.manifest)
        self.assertEqual("ci_requirement_unknown", captured.exception.code)

    def test_conditional_workflow_trigger_requires_human(self) -> None:
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.configured_ci_runtime(
                datetime(2026, 8, 24, tzinfo=timezone.utc),
                conditional_trigger=True,
            ).probe(self.manifest)
        self.assertEqual("ci_requirement_unknown", captured.exception.code)

    def test_running_ci_must_finish_within_ten_minutes(self) -> None:
        first = self.runtime("", now=datetime(2026, 8, 24, tzinfo=timezone.utc))
        self.assertEqual("pending", first.probe(self.manifest)["ci_status"])
        resumed = self.runtime("", now=datetime(2026, 8, 24, 0, 10, 1, tzinfo=timezone.utc))
        with self.assertRaises(RuntimeErrorResult) as captured:
            resumed.probe(self.manifest)
        self.assertEqual("ci_completion_timeout", captured.exception.code)

    def test_unattributed_new_head_is_external_change(self) -> None:
        self.runtime("SUCCESS").probe(self.manifest)
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.runtime("SUCCESS", head_sha="b" * 40).probe(self.manifest)
        self.assertEqual("ci_head_changed_externally", captured.exception.code)

    def test_only_explicit_code_defect_can_consume_remediation_budget(self) -> None:
        runtime = self.runtime("FAILURE")
        runtime.probe(self.manifest)
        state_path = (
            self.workspace.root
            / ".agentic-ops/tasks/TAP-12620/runs/run-tap-12620/ci/state.json"
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["attempts"][SHA]["failure_event_id"] = "ci-failure-explicit"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        readback = {
            "event_id": "git-readback",
            "action": "remote_branch_readback",
            "action_data": {
                "head_sha": "b" * 40,
                "sha": "b" * 40,
                "worktree_clean": True,
                "attributed_actions": ["git_commit", "git_push_task_branch"],
            },
        }
        non_code = {
            "event_id": "ci-failure-explicit",
            "action": "failure",
            "action_data": {
                "code": "ci_runner_failure",
                "detail": "Runner 不可用",
                "retry_safe": False,
            },
        }
        with self.assertRaises(RuntimeErrorResult) as captured:
            runtime.record_remediation(
                self.manifest,
                failure_event_id="ci-failure-explicit",
                commit_sha="b" * 40,
                new_head_sha="b" * 40,
                authorization_reference="AO-76-confirmed-design",
                completed_events=[non_code, readback],
            )
        self.assertEqual("ci_failure_requires_human", captured.exception.code)

        code_defect = {
            **non_code,
            "action_data": {
                "code": "ci_code_defect",
                "detail": "业务代码断言失败",
                "retry_safe": True,
            },
        }
        recorded = runtime.record_remediation(
            self.manifest,
            failure_event_id="ci-failure-explicit",
            commit_sha="b" * 40,
            new_head_sha="b" * 40,
            authorization_reference="AO-76-confirmed-design",
            completed_events=[code_defect, readback],
        )
        self.assertTrue(recorded["recorded"])
        self.assertEqual(1, recorded["remediation_attempts_used"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult
from ao_work.task_state import TaskIdentity, TaskStore
from ao_work.task_state.io import atomic_write_json, read_json
from ao_work.task_state.locking import TaskLock
from ao_work.task_repository_scope import (
    _task_run_repository_facts,
    execute_worktree_cleanup,
    validate_repository_summary_content,
)
from ao_work.workspace import DEVELOPER, Workspace, task_worktree_path


IDENTITY = TaskIdentity(
    connection_id="tapdata",
    jira_issue_id="10001",
    issue_key="TAP-123",
    project_key="TAP",
    agentic_run_id="run-20260813-001",
)


class TaskStateTest(unittest.TestCase):
    def test_repository_facts_can_use_matching_run_from_same_issue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = Workspace(root, DEVELOPER, root / ".agentic-ops/agent.json")
            worktree = (root / "worktree-two").resolve()
            worktree.mkdir()
            runs = root / ".agentic-ops/tasks/TAP-123/runs"

            def write_run(
                run_id: str,
                repository: str,
                repository_root: Path,
                head_sha: str,
                pr_url: str,
                *,
                repository_scoped: bool = False,
            ) -> None:
                task_run = runs / run_id / "task-to-pr"
                if repository_scoped:
                    task_run = task_run / repository.replace("/", "--")
                task_run.mkdir(parents=True)
                atomic_write_json(
                    task_run / "manifest.json",
                    {
                        "issue": {"key": "TAP-123"},
                        "repository": {
                            "slug": repository,
                            "root": str(repository_root.resolve()),
                        },
                    },
                )
                atomic_write_json(
                    task_run / "result.json",
                    {
                        "status": "ready_for_pr_review",
                        "facts": {
                            "remote_branch_readback": {
                                "repository_slug": repository,
                                "status": "exists",
                                "sha": head_sha,
                                "head_sha": head_sha,
                            },
                            "pr_readback": {
                                "url": pr_url,
                                "head_sha": head_sha,
                                "base_branch": "develop",
                            },
                            "verifications": [{"id": "unit", "status": "passed"}],
                        },
                    },
                )

            write_run(
                IDENTITY.agentic_run_id,
                "tapdata/tapdata",
                root / "worktree-one",
                "a" * 40,
                "https://github.com/tapdata/tapdata/pull/1",
            )
            write_run(
                "run-20260813-002",
                "tapdata/tapdata-connectors",
                worktree,
                "b" * 40,
                "https://github.com/tapdata/tapdata-connectors/pull/2",
                repository_scoped=True,
            )

            facts = _task_run_repository_facts(
                workspace,
                "TAP-123",
                IDENTITY.agentic_run_id,
                "tapdata/tapdata-connectors",
                worktree,
                "b" * 40,
            )
            self.assertEqual(
                "https://github.com/tapdata/tapdata-connectors/pull/2",
                facts["pr_url"],
            )
            self.assertEqual([{"id": "unit", "status": "passed"}], facts["verifications"])

            self.assertEqual(
                {},
                _task_run_repository_facts(
                    workspace,
                    "TAP-123",
                    IDENTITY.agentic_run_id,
                    "tapdata/tapdata-connectors",
                    worktree,
                    "c" * 40,
                ),
            )

    def test_cleanup_preflights_every_worktree_before_first_remove(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pool = root / "pool"
            pool.mkdir()
            rows = []
            for repository in ("tapdata/one", "tapdata/two"):
                path = task_worktree_path(pool, IDENTITY.issue_key, "develop", repository)
                path.mkdir(parents=True)
                rows.append(
                    {
                        "repository": repository,
                        "from_branch": "develop",
                        "task_branch": f"actor/{IDENTITY.issue_key}/develop",
                        "worktree_path": str(path),
                        "worktree_status": "prepared",
                        "worktree_baseline_sha": "a" * 40,
                    }
                )
            state = {
                "task": {"agentic_run_id": IDENTITY.agentic_run_id},
                "repository_scope": {
                    "confirmed_repository_branch_map": rows,
                    "completion_summary_readback": {"external_id": "1"},
                },
            }
            store = mock.Mock()
            store.inspect.return_value = state
            workspace = Workspace(root, DEVELOPER, root / ".agentic-ops/agent.json")
            dirty_error = RuntimeErrorResult(
                code="worktree_dirty",
                message="dirty",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="clean",
            )
            commands: list[list[str]] = []

            def git(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                commands.append(command)
                if "symbolic-ref" in command:
                    return subprocess.CompletedProcess(command, 0, "actor/TAP-123/develop\n", "")
                if "branch" in command:
                    return subprocess.CompletedProcess(command, 0, "origin/task\n", "")
                return subprocess.CompletedProcess(command, 0, "a" * 40 + "\n", "")

            with (
                mock.patch(
                    "ao_work.task_repository_scope._live_context",
                    return_value=(
                        SimpleNamespace(profile=SimpleNamespace(status_mapping={"Done": "completed"})),
                        {},
                        SimpleNamespace(status="Done"),
                        object(),
                    ),
                ),
                mock.patch(
                    "ao_work.task_repository_scope.resolve_source_pool_root",
                    return_value=pool,
                ),
                mock.patch(
                    "ao_work.task_repository_scope._refresh_pool_member"
                ),
                mock.patch(
                    "ao_work.task_repository_scope._require_clean_worktree",
                    side_effect=[None, dirty_error],
                ),
                mock.patch(
                    "ao_work.task_repository_scope.subprocess_git",
                    side_effect=git,
                ),
            ):
                with self.assertRaises(RuntimeErrorResult) as captured:
                    execute_worktree_cleanup(
                        workspace,
                        root / "install",
                        store,
                        IDENTITY.issue_key,
                    )
            self.assertEqual("worktree_dirty", captured.exception.code)
            self.assertFalse(any("remove" in command for command in commands))

    def test_repository_summary_requires_field_and_every_actual_repository(self) -> None:
        repositories = [
            {"repository": "tapdata/tapdata-connectors"},
            {"repository": "tapdata/tapdata-application"},
        ]
        with self.assertRaises(RuntimeErrorResult) as missing_field:
            validate_repository_summary_content("完成内容：已修复", repositories)
        self.assertEqual("repository_summary_fields_missing", missing_field.exception.code)
        with self.assertRaises(RuntimeErrorResult) as missing_repository:
            validate_repository_summary_content(
                "实际变更仓库：tapdata/tapdata-connectors\n"
                "已输出表单字段：actual_change_repositories",
                repositories,
            )
        self.assertEqual("repository_summary_incomplete", missing_repository.exception.code)
        validate_repository_summary_content(
            "实际变更仓库：tapdata/tapdata-connectors、tapdata/tapdata-application\n"
            "已输出表单字段：actual_change_repositories",
            repositories,
        )

    def test_repository_proposal_requires_explicit_confirmation_before_worktree_updates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary))
            store.initialize(IDENTITY)
            proposal = {
                "problem_version": "develop",
                "problem_version_repository": "tapdata/tapdata",
                "proposed_repository_branch_map": [
                    {
                        "repository": "tapdata/tapdata-connectors",
                        "proposed_from_branch": "develop",
                    }
                ],
            }
            recorded = store.record_repository_proposal(
                IDENTITY.issue_key,
                IDENTITY.agentic_run_id,
                proposal,
            )
            self.assertEqual("proposal_recorded", recorded["repository_scope"]["phase"])
            with self.assertRaises(RuntimeErrorResult) as captured:
                store.update_repository_worktree(
                    IDENTITY.issue_key,
                    IDENTITY.agentic_run_id,
                    "tapdata/tapdata-connectors",
                    {"worktree_status": "prepared"},
                )
            self.assertEqual(
                "repository_mapping_confirmation_required",
                captured.exception.code,
            )

            confirmed = [
                {
                    "repository": "tapdata/tapdata-connectors",
                    "from_branch": "release-v3.8",
                    "worktree_status": "not_created",
                }
            ]
            result = store.confirm_repository_mapping(
                IDENTITY.issue_key,
                IDENTITY.agentic_run_id,
                confirmed,
                [
                    {
                        "repository": "tapdata/tapdata-connectors",
                        "proposed_from_branch": "develop",
                        "confirmed_from_branch": "release-v3.8",
                    }
                ],
            )
            self.assertEqual("mapping_confirmed", result["repository_scope"]["phase"])
            task_dir = Path(temporary) / ".agentic-ops" / "tasks" / IDENTITY.issue_key
            self.assertEqual(
                (task_dir / "proposals" / "repository-scope.json").resolve(),
                Path(result["path"]),
            )
            confirmation = read_json(Path(result["confirmation_path"]))
            self.assertEqual(IDENTITY.issue_key, confirmation["issue_key"])
            self.assertEqual(confirmed, confirmation["confirmed_repository_branch_map"])
            updated = store.update_repository_worktree(
                IDENTITY.issue_key,
                IDENTITY.agentic_run_id,
                "tapdata/tapdata-connectors",
                {"worktree_status": "prepared", "worktree_baseline_sha": "a" * 40},
            )
            self.assertEqual("worktrees_active", updated["repository_scope"]["phase"])
            with self.assertRaises(RuntimeErrorResult) as outside:
                store.update_repository_worktree(
                    IDENTITY.issue_key,
                    IDENTITY.agentic_run_id,
                    "tapdata/tapdata",
                    {"worktree_status": "prepared"},
                )
            self.assertEqual("repository_outside_confirmed_mapping", outside.exception.code)

    def test_confirmed_repository_mapping_cannot_be_overwritten_by_new_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary))
            store.initialize(IDENTITY)
            original = {
                "problem_version": "develop",
                "problem_version_repository": "tapdata/tapdata",
                "proposed_repository_branch_map": [
                    {"repository": "tapdata/tapdata", "proposed_from_branch": "develop"}
                ],
            }
            store.record_repository_proposal(IDENTITY.issue_key, IDENTITY.agentic_run_id, original)
            store.confirm_repository_mapping(
                IDENTITY.issue_key,
                IDENTITY.agentic_run_id,
                [{"repository": "tapdata/tapdata", "from_branch": "develop"}],
                [],
            )
            changed = {
                **original,
                "proposed_repository_branch_map": [
                    {
                        "repository": "tapdata/tapdata-connectors",
                        "proposed_from_branch": "develop",
                    }
                ],
            }
            with self.assertRaises(RuntimeErrorResult) as captured:
                store.record_repository_proposal(
                    IDENTITY.issue_key,
                    IDENTITY.agentic_run_id,
                    changed,
                )
            self.assertEqual("repository_mapping_confirmation_required", captured.exception.code)

    def test_initialize_creates_complete_state_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            now = datetime(2026, 8, 13, 1, 2, 3, tzinfo=timezone.utc)
            store = TaskStore(root, now=lambda: now)
            created = store.initialize(IDENTITY)
            repeated = store.initialize(IDENTITY)

            self.assertEqual(True, created["created"])
            self.assertEqual(False, repeated["created"])
            task_dir = root / ".agentic-ops" / "tasks" / IDENTITY.issue_key
            for name in ("task.json", "progress.json", "sync.json", "decisions.ndjson", "journal.ndjson"):
                self.assertTrue((task_dir / name).is_file(), name)
            for name in ("proposals", "confirmations"):
                self.assertTrue((task_dir / name).is_dir(), name)
            journal = (task_dir / "journal.ndjson").read_text(encoding="utf-8").splitlines()
            self.assertEqual(1, len(journal))
            self.assertEqual("task_init", json.loads(journal[0])["operation"])

    def test_identity_mismatch_does_not_overwrite_existing_task(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = TaskStore(root)
            store.initialize(IDENTITY)
            changed = TaskIdentity(**{**IDENTITY.__dict__, "agentic_run_id": "another-run"})
            with self.assertRaises(RuntimeErrorResult) as captured:
                store.initialize(changed)
            self.assertEqual("task_identity_mismatch", captured.exception.code)
            task = read_json(root / ".agentic-ops" / "tasks" / IDENTITY.issue_key / "task.json")
            self.assertEqual(IDENTITY.agentic_run_id, task["agentic_run_id"])

    def test_atomic_write_failure_keeps_previous_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "progress.json"
            atomic_write_json(target, {"content_version": 1})
            with mock.patch("ao_work.task_state.io.os.replace", side_effect=OSError("simulated")):
                with self.assertRaises(OSError):
                    atomic_write_json(target, {"content_version": 2})
            self.assertEqual(1, read_json(target)["content_version"])
            self.assertEqual([], list(target.parent.glob(".progress.json.*.tmp")))

    def test_initialization_failure_does_not_publish_partial_task(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = TaskStore(root)
            with mock.patch("ao_work.task_state.store.os.replace", side_effect=OSError("simulated")):
                with self.assertRaises(OSError):
                    store.initialize(IDENTITY)
            task_dir = root / ".agentic-ops" / "tasks" / IDENTITY.issue_key
            self.assertFalse(task_dir.exists())

    def test_second_process_times_out_while_task_is_locked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock_path = root / ".agentic-ops" / "locks" / f"{IDENTITY.issue_key}.lock"
            ready = root / "ready"
            source_root = Path(__file__).resolve().parents[2] / "runtime" / "src"
            code = "\n".join(
                (
                    "import pathlib, sys, time",
                    "from ao_work.task_state.locking import TaskLock",
                    "lock = pathlib.Path(sys.argv[1])",
                    "ready = pathlib.Path(sys.argv[2])",
                    "with TaskLock(lock, 1):",
                    "    ready.write_text('ready')",
                    "    time.sleep(2)",
                )
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(source_root)
            process = subprocess.Popen(
                [sys.executable, "-c", code, str(lock_path), str(ready)],
                env=environment,
            )
            try:
                deadline = time.monotonic() + 2
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(ready.exists())
                with self.assertRaises(RuntimeErrorResult) as captured:
                    TaskStore(root, lock_timeout=0.05).initialize(IDENTITY)
                self.assertEqual("task_lock_timeout", captured.exception.code)
            finally:
                process.terminate()
                process.wait(timeout=5)

    def test_superpowers_directory_is_not_required_for_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = TaskStore(root)
            store.initialize(IDENTITY)
            superpowers = root / ".superpowers"
            superpowers.mkdir()
            (superpowers / "temporary.txt").write_text("临时状态", encoding="utf-8")
            for child in superpowers.iterdir():
                child.unlink()
            superpowers.rmdir()
            inspected = store.inspect(IDENTITY.issue_key)
            self.assertEqual(IDENTITY.issue_key, inspected["task"]["issue_key"])

    def test_reports_decisions_and_sync_are_durable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = TaskStore(root)
            store.initialize(IDENTITY)
            report = store.write_report(
                IDENTITY.issue_key,
                IDENTITY.agentic_run_id,
                "analysis",
                "# 问题分析\n\n确认需要补充 Jira 回读。",
            )
            self.assertEqual(
                "# 问题分析\n\n确认需要补充 Jira 回读。\n",
                Path(report["report_path"]).read_text(encoding="utf-8"),
            )
            created = store.append_decision(
                IDENTITY.issue_key,
                IDENTITY.agentic_run_id,
                "plan_confirmed",
                "研发工程师确认实施计划",
                "jira-comment-100",
            )
            repeated = store.append_decision(
                IDENTITY.issue_key,
                IDENTITY.agentic_run_id,
                "plan_confirmed",
                "研发工程师确认实施计划",
                "jira-comment-100",
            )
            self.assertEqual(True, created)
            self.assertEqual(False, repeated)
            record = store.record_external_readback(
                IDENTITY.issue_key,
                "jira_comment",
                "run-1-analysis",
                "comment-100",
            )
            self.assertEqual("comment-100", record["external_id"])
            task_dir = root / ".agentic-ops" / "tasks" / IDENTITY.issue_key
            decision = json.loads(
                (task_dir / "decisions.ndjson").read_text(encoding="utf-8").splitlines()[0]
            )
            self.assertEqual("plan_confirmed", decision["decision_type"])
            sync = read_json(task_dir / "sync.json")
            self.assertEqual(
                "completed",
                sync["external_writes"]["jira_comment:run-1-analysis"]["status"],
            )

    def test_gate_transition_updates_progress_and_appends_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = TaskStore(root)
            store.initialize(IDENTITY)
            recorded = store.record_gate_transition(
                IDENTITY.issue_key,
                IDENTITY.agentic_run_id,
                stage="solution_classification",
                next_action="review_task_design",
                operation="task_solution_classify",
                status="completed",
                evidence={"intake_digest": "a" * 64},
            )
            self.assertEqual(
                "review_task_design",
                recorded["progress"]["agentic_next_action"],
            )
            self.assertEqual("task_solution_classify", recorded["event"]["operation"])
            self.assertEqual("a" * 64, recorded["event"]["evidence"]["intake_digest"])
            task_dir = root / ".agentic-ops" / "tasks" / IDENTITY.issue_key
            journal = [
                json.loads(line)
                for line in (task_dir / "journal.ndjson")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(2, len(journal))
            self.assertEqual("task_solution_classify", journal[-1]["operation"])

    def test_all_public_methods_reject_path_components_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside"
            outside.mkdir()
            marker = outside / "marker.txt"
            store = TaskStore(root / "workspace")
            calls = (
                lambda: store.inspect("../../outside"),
                lambda: store.write_report("../../outside", "../run", "analysis", "内容"),
                lambda: store.append_decision(
                    "../../outside", "../run", "../decision", "内容", "reference"
                ),
                lambda: store.record_external_readback(
                    "../../outside", "../operation", "../key", "../external"
                ),
            )
            for call in calls:
                with self.assertRaises(RuntimeErrorResult) as captured:
                    call()
                self.assertEqual("invalid_task_identity", captured.exception.code)
            self.assertFalse(marker.exists())
            self.assertEqual([], list(outside.iterdir()))

    def test_symlinked_state_tasks_locks_task_and_reports_are_rejected(self) -> None:
        for protected in ("state", "tasks", "locks", "task", "reports"):
            with self.subTest(protected=protected), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                workspace = root / "workspace"
                workspace.mkdir()
                outside = root / "outside"
                outside.mkdir()
                state = workspace / ".agentic-ops"
                if protected == "state":
                    state.symlink_to(outside, target_is_directory=True)
                else:
                    state.mkdir()
                    if protected in {"tasks", "locks"}:
                        (state / protected).symlink_to(outside, target_is_directory=True)
                    elif protected == "task":
                        (state / "tasks").mkdir()
                        (state / "tasks" / IDENTITY.issue_key).symlink_to(
                            outside, target_is_directory=True
                        )
                    else:
                        store = TaskStore(workspace)
                        store.initialize(IDENTITY)
                        reports = state / "tasks" / IDENTITY.issue_key / "reports"
                        reports.rmdir()
                        reports.symlink_to(outside, target_is_directory=True)
                store = TaskStore(workspace)
                with self.assertRaises(RuntimeErrorResult) as captured:
                    if protected == "reports":
                        store.write_report(
                            IDENTITY.issue_key,
                            IDENTITY.agentic_run_id,
                            "analysis",
                            "禁止越界",
                        )
                    else:
                        store.initialize(IDENTITY)
                self.assertEqual("task_state_path_unsafe", captured.exception.code)
                self.assertEqual([], list(outside.iterdir()))

    def test_lock_symlink_never_truncates_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            victim = root / "victim.txt"
            victim.write_text("KEEP\n", encoding="utf-8")
            lock = root / "state.lock"
            lock.symlink_to(victim)
            with self.assertRaises(RuntimeErrorResult) as captured:
                with TaskLock(lock):
                    pass
            self.assertEqual("task_lock_path_invalid", captured.exception.code)
            self.assertEqual("KEEP\n", victim.read_text(encoding="utf-8"))

    def test_hardlinked_managed_state_leaves_never_read_or_write_external_inode(self) -> None:
        for leaf in (
            "task.json",
            "progress.json",
            "sync.json",
            "decisions.ndjson",
            "journal.ndjson",
        ):
            with self.subTest(leaf=leaf), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                workspace = root / "workspace"
                store = TaskStore(workspace)
                store.initialize(IDENTITY)
                task_dir = workspace / ".agentic-ops/tasks" / IDENTITY.issue_key
                managed = task_dir / leaf
                external = root / f"external-{leaf}"
                original = managed.read_bytes()
                external.write_bytes(original)
                managed.unlink()
                os.link(external, managed)

                with self.assertRaises(RuntimeErrorResult) as captured:
                    if leaf == "decisions.ndjson":
                        store.append_decision(
                            IDENTITY.issue_key,
                            IDENTITY.agentic_run_id,
                            "plan_confirmed",
                            "禁止写入外部 inode",
                            "hardlink-test",
                        )
                    elif leaf == "journal.ndjson":
                        store.write_report(
                            IDENTITY.issue_key,
                            IDENTITY.agentic_run_id,
                            "analysis",
                            "禁止写入外部 inode",
                        )
                    elif leaf == "sync.json":
                        store.record_external_readback(
                            IDENTITY.issue_key,
                            "jira_comment",
                            "hardlink-sync",
                            "comment-1",
                        )
                    else:
                        store.inspect(IDENTITY.issue_key)
                self.assertEqual("task_state_leaf_unsafe", captured.exception.code)
                self.assertEqual(original, external.read_bytes())

    def test_update_stage_timeline_writes_progress_and_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            now = datetime(2026, 8, 13, 1, 2, 3, tzinfo=timezone.utc)
            store = TaskStore(root, now=lambda: now)
            store.initialize(IDENTITY)
            sequence = [
                {"stage_id": "task_intake", "begin": "t1", "end": "t2"},
                {"stage_id": "implementation", "begin": "t3", "end": None},
            ]
            result = store.update_stage_timeline(
                IDENTITY.issue_key,
                IDENTITY.agentic_run_id,
                sequence,
            )
            progress = result["progress"]
            self.assertEqual(sequence, progress["stage_timeline"])
            task_dir = root / ".agentic-ops" / "tasks" / IDENTITY.issue_key
            on_disk = read_json(task_dir / "progress.json")
            self.assertEqual(sequence, on_disk["stage_timeline"])
            journal = (task_dir / "journal.ndjson").read_text(encoding="utf-8").splitlines()
            self.assertEqual("stage_timeline_update", json.loads(journal[-1])["operation"])
            self.assertEqual(sequence, json.loads(journal[-1])["sequence"])

    def test_update_stage_timeline_rejects_bad_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = TaskStore(root)
            store.initialize(IDENTITY)
            with self.assertRaises(RuntimeErrorResult):
                store.update_stage_timeline(
                    IDENTITY.issue_key,
                    IDENTITY.agentic_run_id,
                    [{"stage_id": 123, "begin": "t1", "end": None}],
                )
            with self.assertRaises(RuntimeErrorResult):
                store.update_stage_timeline(
                    IDENTITY.issue_key,
                    "run-other",
                    [],
                )

    def test_update_stage_timeline_identity_mismatch_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = TaskStore(root)
            store.initialize(IDENTITY)
            with self.assertRaises(RuntimeErrorResult) as captured:
                store.update_stage_timeline(
                    IDENTITY.issue_key,
                    "run-other-999",
                    [],
                )
            self.assertEqual("task_identity_mismatch", captured.exception.code)

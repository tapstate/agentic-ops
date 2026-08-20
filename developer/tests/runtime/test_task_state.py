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
from unittest import mock

from ao_work.output import RuntimeErrorResult
from ao_work.task_state import TaskIdentity, TaskStore
from ao_work.task_state.io import atomic_write_json, read_json
from ao_work.task_state.locking import TaskLock


IDENTITY = TaskIdentity(
    connection_id="tapdata",
    jira_issue_id="10001",
    issue_key="TAP-123",
    project_key="TAP",
    agentic_run_id="run-20260813-001",
)


class TaskStateTest(unittest.TestCase):
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

"""正式四项验收、候选绑定与失败关闭；不递归执行真实全量套件。"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from internal.tests import test_story_gate as story_fixture
from internal.story_gate import evidence
from internal.story_gate.errors import StoryGateError
from internal.story_gate.locking import TaskLock
from internal.story_gate.model import FULL_ACCEPTANCE_CHECKS
from internal.story_gate.registry import load_story_registry
from internal.story_gate.branch_policy import resolve_branch_review
from internal.story_gate.service import StoryGateService, _check_command


class VerificationTests(unittest.TestCase):
    prepare = story_fixture.StoryGateTest.prepare
    git = story_fixture.StoryGateTest.git
    git_output = story_fixture.StoryGateTest.git_output
    stage = story_fixture.StoryGateTest.stage

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.prepare(self.root)
        self.base = self.git_output(self.root, 'rev-parse', 'HEAD')
        self.stage(self.root, 'gate/engine.py', 'VALUE = 1\n')
        self.service = StoryGateService(self.root)

    def verify(self, command=None, **kwargs):
        with mock.patch('internal.story_gate.service._check_command', return_value=command or ['/usr/bin/true']) as calls:
            result = self.service.verify('staged', **kwargs)
        return result, calls

    def record(self, result):
        path = Path(result['evidence_path'])
        return path, json.loads(path.read_text())

    def test_exact_four_and_registry_consistency(self):
        result, calls = self.verify()
        self.assertEqual([call.args[1] for call in calls.call_args_list], list(FULL_ACCEPTANCE_CHECKS))
        self.assertEqual(result['acceptance_checks'], list(FULL_ACCEPTANCE_CHECKS))
        self.assertEqual(len(result['checks']), 4)
        root = Path(__file__).resolve().parents[2]
        for story in load_story_registry(root).stories:
            self.assertEqual(story.acceptance_checks, FULL_ACCEPTANCE_CHECKS)
        self.assertEqual(_check_command(root, 'product_install_boundary'), [str(root / 'tests/test_install.sh')])

    def test_inspection_exposes_only_matching_acceptance_summary(self):
        self.assertIsNone(self.service.inspect('staged')['acceptance_evidence'])
        result, _ = self.verify()
        path, original = self.record(result)
        summary = self.service.inspect('staged')['acceptance_evidence']
        self.assertEqual(original['run_id'], summary['run_id'])
        self.assertEqual(list(FULL_ACCEPTANCE_CHECKS), [row['check_id'] for row in summary['checks']])
        for row, check in zip(summary['checks'], original['checks']):
            self.assertEqual({key: check[key] for key in ('check_id', 'passed', 'exit_code', 'duration_seconds')}, row)
        self.assertNotIn('environment', summary)
        broken = dict(original, run_id='invalid')
        path.write_text(json.dumps(broken))
        self.assertIsNone(self.service.inspect('staged')['acceptance_evidence'])
        path.write_text(json.dumps(original))
        self.stage(self.root, 'gate/engine.py', 'VALUE = 2\n')
        self.assertIsNone(self.service.inspect('staged')['acceptance_evidence'])

    def test_trust_root_upgrade_pr_branch_is_unambiguous(self):
        root = Path(__file__).resolve().parents[2]
        for prefix in ('codex', 'feature', 'fix'):
            for suffix, target in (('fix-main', 'main'), ('performance', 'develop')):
                with mock.patch.dict(os.environ, {'AGENTIC_OPS_STORY_BRANCH': prefix + '/AO-167/' + suffix}):
                    review = resolve_branch_review(root)
                    self.assertEqual((review.channel, review.target_branch), ('pr_review', target))

    def test_git_context_overrides_are_removed(self):
        with mock.patch.dict(os.environ, {'GIT_INDEX_FILE': '/missing/index', 'GIT_DIR': '/missing/git',
                                         'GIT_WORK_TREE': '/missing/tree', 'GIT_CONFIG_COUNT': 'broken'}):
            result, _ = self.verify()
            self.assertEqual(result['acceptance_status'], 'passed')
            self.assertEqual(self.service.inspect('staged')['acceptance_status'], 'passed')

    def test_formal_child_environment_cannot_skip_or_import_external_modules(self):
        command = [sys.executable, '-c', 'import os; assert os.environ["PYTHONPATH"] == os.getcwd(); assert "AGENTIC_OPS_RELEASE_WORKFLOW_TEST_RUNNING" not in os.environ']
        with mock.patch.dict(os.environ, {'PYTHONPATH': '/external/modules', 'AGENTIC_OPS_RELEASE_WORKFLOW_TEST_RUNNING': '1'}):
            result, _ = self.verify(command)
            self.assertEqual(result['acceptance_status'], 'passed')

    def test_hidden_index_flags_fail_closed(self):
        for flag in ('assume-unchanged', 'skip-worktree'):
            self.git(self.root, 'update-index', '--' + flag, 'gate/engine.py')
            with self.subTest(flag=flag), self.assertRaises(StoryGateError):
                self.verify()
            self.git(self.root, 'update-index', '--no-' + flag, 'gate/engine.py')

    def test_failed_running_record_write_removes_previous_pass(self):
        result, _ = self.verify()
        path, _ = self.record(result)
        with mock.patch.object(self.service, '_write_evidence_summary', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.verify()
        self.assertFalse(path.exists())
        self.assertEqual(self.service.inspect('staged')['acceptance_status'], 'not_run')

    def test_private_logs_and_strict_run_metadata(self):
        result, _ = self.verify()
        path, original = self.record(result)
        output = self.root / original['checks'][0]['log_path']
        for item in (output, output.parent / 'events.ndjson'):
            self.assertEqual(item.stat().st_mode & 0o777, 0o600)
        for item in (output.parent, output.parent.parent, output.parent.parent.parent):
            self.assertEqual(item.stat().st_mode & 0o777, 0o700)
        for change in ('run', 'error', 'path', 'gap', 'locks'):
            value = json.loads(json.dumps(original))
            if change == 'run': value['run_id'] = '../invalid'
            if change == 'error': value['error'] = 'failure'
            if change == 'path': value['checks'][0]['log_path'] = 'elsewhere'
            if change == 'gap': value['checks'][1]['log_start'] += 1
            if change == 'locks':
                value['environment']['locks'] = {}
                value['environment_digest'] = evidence.digest(value['environment'])
            path.write_text(json.dumps(value))
            with self.subTest(change=change):
                self.assertEqual(self.service.inspect('staged')['acceptance_status'], 'not_run')

    def test_missing_lock_and_optional_integration_rejected(self):
        with mock.patch.dict(os.environ, {'AO_CONNECTOR_MAVEN_TEST': '1'}):
            with self.assertRaises(StoryGateError):
                self.verify()
        (self.root / 'internal/uv.lock').unlink()
        with self.assertRaises(OSError):
            evidence.environment(self.root)

    def test_staged_commit_range_reuses_without_running(self):
        result, _ = self.verify()
        self.git(self.root, 'commit', '-qm', 'candidate')
        head = self.git_output(self.root, 'rev-parse', 'HEAD')
        with mock.patch('internal.story_gate.service._check_command', side_effect=AssertionError('must not run')):
            current = self.service.inspect('range', base=self.base, head=head)
        self.assertEqual(current['acceptance_status'], 'passed')
        self.assertEqual(current['impact_id'], result['impact_id'])

    def test_old_incomplete_and_malformed_records_rejected(self):
        result, _ = self.verify()
        path, original = self.record(result)
        for change in ('old', 'missing', 'duplicate', 'false', 'offset', 'binding', 'environment'):
            value = json.loads(json.dumps(original))
            if change == 'old': value['schema_version'] = 4
            if change == 'missing': value['checks'].pop()
            if change == 'duplicate': value['checks'][1] = value['checks'][0]
            if change == 'false': value['checks'][0]['passed'] = False
            if change == 'offset': value['checks'][0]['log_end'] = -1
            if change == 'binding': value['binding']['candidate_tree'] = '0' * 64
            if change == 'environment': value['environment_digest'] = '0' * 64
            path.write_text(json.dumps(value))
            with self.subTest(change=change):
                self.assertEqual(self.service.inspect('staged')['acceptance_status'], 'not_run')

    def test_self_contained_record_and_release_environment(self):
        result, _ = self.verify()
        _, value = self.record(result)
        (self.root / value['checks'][0]['log_path']).unlink()
        self.assertEqual(self.service.inspect('staged')['acceptance_status'], 'passed')
        with mock.patch('internal.story_gate.evidence.environment', side_effect=ValueError('different verifier')):
            self.assertEqual(self.service.inspect('staged')['acceptance_status'], 'not_run')
            with mock.patch.dict(os.environ, {'AGENTIC_OPS_STORY_GATE_STAGE': 'release'}):
                _, impact = self.service._calculate('staged', base=None, head=None)
                self.assertIsNotNone(self.service._read_matching_evidence(impact))

    def test_repeat_run_has_unique_logs_and_failure_revokes_old_pass(self):
        first, _ = self.verify()
        _, original = self.record(first)
        approval = self.service._approval_path(first['impact_id'])
        approval.parent.mkdir(parents=True, exist_ok=True)
        approval.write_text('{}')
        with self.assertRaises(StoryGateError):
            self.verify(['/usr/bin/false'])
        _, failed = self.record(first)
        self.assertEqual(failed['acceptance_status'], 'failed')
        self.assertNotEqual(failed['run_id'], original['run_id'])
        self.assertTrue((self.root / original['checks'][0]['log_path']).is_file())
        self.assertFalse(approval.exists())
        self.assertEqual(self.service.inspect('staged')['acceptance_status'], 'not_run')

    def test_running_record_cannot_be_consumed(self):
        self.verify()
        observed = []
        def event(value):
            if value['event'] == 'check_started':
                observed.append(self.service.inspect('staged')['acceptance_status'])
        self.verify(event_sink=event)
        self.assertEqual(observed, ['not_run'] * 4)

    def test_unstaged_untracked_and_midrun_drift_rejected(self):
        (self.root / 'gate/engine.py').write_text('VALUE = 2\n')
        with self.assertRaises(StoryGateError): self.verify()
        (self.root / 'gate/engine.py').write_text('VALUE = 1\n')
        stray = self.root / 'extra.py'
        stray.write_text('extra')
        with self.assertRaises(StoryGateError): self.verify()
        stray.unlink()
        def drift(event):
            if event['event'] == 'check_finished':
                (self.root / 'gate/engine.py').write_text('VALUE = 3\n')
        with self.assertRaises(StoryGateError): self.verify(event_sink=drift)

    def test_same_diff_on_different_base_is_not_same_candidate(self):
        result, _ = self.verify()
        self.git(self.root, 'commit', '--allow-empty', '-qm', 'candidate')
        self.git(self.root, 'commit', '--allow-empty', '-qm', 'different base')
        self.stage(self.root, 'gate/engine.py', 'VALUE = 2\n')
        next_result = self.service.inspect('staged')
        self.assertNotEqual(next_result['impact_id'], result['impact_id'])
        self.assertEqual(next_result['acceptance_status'], 'not_run')

    def test_range_requires_actual_checked_out_head(self):
        self.git(self.root, 'commit', '-qm', 'candidate')
        head = self.git_output(self.root, 'rev-parse', 'HEAD')
        self.git(self.root, 'commit', '--allow-empty', '-qm', 'later')
        with mock.patch('internal.story_gate.service._check_command', return_value=['/usr/bin/true']):
            with self.assertRaises(StoryGateError):
                self.service.verify('range', base=self.base, head=head)

    def test_stable_lock_prevents_other_runs_and_approval(self):
        with TaskLock(self.service._verification_lock()):
            with mock.patch('internal.story_gate.service.TaskLock', side_effect=lambda path, timeout: TaskLock(path, timeout=.1)):
                with self.assertRaises(StoryGateError): self.verify()
                with self.assertRaises(StoryGateError): self.service.approve('range', 'x', 'x')

    def test_timeout_reaps_child_and_invalidates_pass(self):
        first, _ = self.verify()
        marker = self.root / '.local/late-write'
        child = 'import time; from pathlib import Path; time.sleep(.6); Path(%r).touch()' % str(marker)
        command = [sys.executable, '-c', 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",%r]); time.sleep(3)' % child]
        with mock.patch.dict(evidence.CHECK_TIMEOUTS, {'python_runtime': .15}):
            with self.assertRaises(StoryGateError): self.verify(command)
        time.sleep(.65)
        self.assertFalse(marker.exists())
        self.assertEqual(self.record(first)[1]['acceptance_status'], 'failed')
        self.assertFalse(self.service._verification_lock().exists())

    def test_keyboard_interrupt_invalidates_pass(self):
        first, _ = self.verify()
        def interrupt(event):
            if event['event'] == 'check_started': raise KeyboardInterrupt()
        with self.assertRaises(StoryGateError): self.verify(event_sink=interrupt)
        self.assertEqual(self.record(first)[1]['acceptance_status'], 'cancelled')

    def test_environment_flags_change_requires_reverification(self):
        with mock.patch.dict(os.environ, {'AO_CONNECTOR_MAVEN_TEST': '0'}):
            self.verify()
            self.assertEqual(self.service.inspect('staged')['acceptance_status'], 'passed')
        with mock.patch.dict(os.environ, {'AO_CONNECTOR_MAVEN_TEST': '1'}):
            self.assertEqual(self.service.inspect('staged')['acceptance_status'], 'not_run')

    def test_index_change_during_run_rejected(self):
        def change(event):
            if event['event'] == 'check_finished':
                self.stage(self.root, 'gate/engine.py', 'VALUE = 2\n')
        with self.assertRaises(StoryGateError): self.verify(event_sink=change)

    def test_exact_base_distinguishes_identical_tree_and_diff(self):
        self.git(self.root, 'commit', '-qm', 'candidate')
        head = self.git_output(self.root, 'rev-parse', 'HEAD')
        with mock.patch('internal.story_gate.service._check_command', return_value=['/usr/bin/true']):
            result = self.service.verify('range', base=self.base, head=head)
        # 另一个基线提交具有同一棵树；不能仅靠 diff/tree 采用旧证据。
        tree = self.git_output(self.root, 'rev-parse', self.base + '^{tree}')
        alternative = self.git_output(self.root, 'commit-tree', tree, '-p', self.base, '-m', 'other base')
        current = self.service.inspect('range', base=alternative, head=head)
        self.assertNotEqual(current['impact_id'], result['impact_id'])
        self.assertEqual(current['acceptance_status'], 'not_run')

    def fake_scripts(self):
        registry = self.root / 'internal/story_gate/stories.yaml'
        self.stage(self.root, str(registry.relative_to(self.root)), registry.read_text().replace(
            'protected_paths: [gate/**]', 'protected_paths: [gate/**, tests/**]'))
        for name in ('internal/tests/test_runtime.sh', 'internal/tests/test_resources.sh',
                     'tests/test_install.sh', 'internal/tests/test_release.sh'):
            self.stage(self.root, name, '#!/bin/sh\necho ' + name + '\n')
            (self.root / name).chmod(0o755)
            self.git(self.root, 'add', name)

    def test_shell_bound_forwarding_and_unbound_diagnostics(self):
        self.fake_scripts()
        source = Path(__file__).resolve().parents[1] / 'acceptance.sh'
        target = self.root / 'internal/acceptance.sh'
        target.write_text(source.read_text())
        entry = self.root / 'internal/bin/story-gate'
        entry.parent.mkdir(parents=True)
        entry.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        entry.chmod(0o755)
        result = subprocess.run(['bash', str(target), 'full', '--change-source', 'staged'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['--source-root', str(self.root), 'verify', '--change-source', 'staged', '--progress'])
        result = subprocess.run(['bash', str(target), 'full'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('不生成提交门禁证据', result.stdout)
        self.assertFalse((self.root / '.local/story-gate/evidence').exists())

    def test_real_cli_sigterm_cancels_and_reaps_descendants(self):
        self.fake_scripts()
        marker = self.root / '.local/late-write'
        started = self.root / '.local/child-started'
        child = 'from pathlib import Path; import time,signal; signal.signal(signal.SIGINT, signal.SIG_IGN); Path(%r).touch(); time.sleep(3); Path(%r).touch()' % (str(started), str(marker))
        self.stage(self.root, 'internal/tests/test_runtime.sh', '#!%s\nimport subprocess,sys,time,signal\nsignal.signal(signal.SIGINT,signal.SIG_IGN)\nsubprocess.Popen([sys.executable,"-c",%r])\ntime.sleep(10)\n' % (sys.executable, child))
        command = [sys.executable, '-m', 'internal.story_gate.cli', '--source-root', str(self.root),
                   'verify', '--change-source', 'staged']
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not started.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(started.exists())
            process.send_signal(signal.SIGTERM)
            time.sleep(.1)
            process.send_signal(signal.SIGTERM)
            _, stderr = process.communicate(timeout=5)
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(json.loads(stderr)['acceptance_status'], 'cancelled')
            time.sleep(1.1)
            self.assertFalse(marker.exists())
            self.assertFalse(self.service._verification_lock().exists())
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()


if __name__ == '__main__':
    unittest.main()

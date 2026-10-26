"""同工位执行交接的真实状态/公开入口验证，不访问业务远端。"""
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
import test_station_lifecycle as fixture
from workflow import execution, task_store

A, B = 'a' * 32, 'b' * 32
ROOT = Path(__file__).resolve().parents[1]


class ExecutionTests(unittest.TestCase):
    write = fixture.StationTests.write

    def setUp(self):
        fixture.StationTests.setUp(self)
        self.env = mock.patch.dict(os.environ, {execution.SESSION_ENV: A})
        self.env.start()
        self.addCleanup(self.env.stop)

    def public_command(self):
        for name in ("workflow", "bootstrap", "gate"):
            shutil.copytree(ROOT / name, self.product / name)
        return [sys.executable, str(self.product / "workflow/task.py"), "execution"]

    def heartbeat(self):
        with task_store.task_state_lock(self.ws):
            pass

    def transfer(self, observed=None, decision='user:handoff'):
        return execution.claim(self.ws, '新会话', observed or execution.status(self.ws)['control_digest'],
                               'agent:已停写并核对原操作', decision)

    def test_missing_identity_allows_read_but_not_write(self):
        os.environ.pop(execution.SESSION_ENV)
        self.assertEqual('unclaimed', execution.status(self.ws)['status'])
        with self.assertRaisesRegex(ValueError, 'AGENTICOPS_EXECUTION_ID'):
            self.heartbeat()
        self.assertNotIn('execution_control', task_store.read_current(self.ws))

    def test_read_and_failed_operation_do_not_refresh(self):
        with mock.patch.object(execution.time, 'time', return_value=100):
            self.heartbeat()
        before = task_store.current_path(self.ws).read_bytes()
        with task_store.task_state_lock(self.ws, execution=False):
            execution.status(self.ws)
        with self.assertRaisesRegex(ValueError, '模拟'):
            with task_store.task_state_lock(self.ws):
                raise ValueError('模拟功能失败')
        self.assertEqual(before, task_store.current_path(self.ws).read_bytes())

    def test_same_session_continues_after_three_days_and_preserves_revision(self):
        with mock.patch.object(execution.time, 'time', return_value=100):
            self.heartbeat()
        with mock.patch.object(execution.time, 'time', return_value=100+3*86400):
            with task_store.task_state_lock(self.ws):
                task_store.compare_and_set(self.ws, 0, None)
        value = task_store.read_current(self.ws)
        self.assertEqual(1, value['revision'])
        self.assertEqual(A, value['execution_control']['session_id'])
        self.assertEqual(100+3*86400, value['execution_control']['last_activity_epoch'])

    def test_active_transfer_requires_decision_and_fences_old_session(self):
        self.heartbeat()
        os.environ[execution.SESSION_ENV] = B
        with self.assertRaisesRegex(ValueError, '已有执行接管者'):
            self.heartbeat()
        self.transfer(decision=None)
        self.heartbeat()
        os.environ[execution.SESSION_ENV] = A
        with self.assertRaisesRegex(ValueError, '已有执行接管者'):
            self.heartbeat()

    def test_idle_transfer_still_requires_conflict_check(self):
        with mock.patch.object(execution.time, 'time', return_value=100):
            self.heartbeat()
        os.environ[execution.SESSION_ENV] = B
        with mock.patch.object(execution.time, 'time', return_value=100+8*3600):
            observed = execution.status(self.ws)
            self.assertNotIn('idle', observed)
            with self.assertRaisesRegex(ValueError, '已有执行接管者'):
                self.heartbeat()
            with self.assertRaisesRegex(ValueError, 'conflict-check-ref'):
                execution.claim(self.ws, 'B', observed['control_digest'])
            self.transfer(decision=None)

    def test_intervening_activity_invalidates_observed_transfer(self):
        self.heartbeat()
        observed = execution.status(self.ws)['control_digest']
        self.heartbeat()
        os.environ[execution.SESSION_ENV] = B
        with self.assertRaisesRegex(ValueError, '已变化'):
            self.transfer(observed)

    def test_transfer_preserves_pending_operation_and_task_envelope(self):
        self.heartbeat()
        operation = self.ws / '.agenticops/operation.json'
        operation.write_text('{"status":"running","operation_id":"original"}')
        before = task_store.read_current(self.ws)
        os.environ[execution.SESSION_ENV] = B
        self.transfer()
        after = task_store.read_current(self.ws)
        self.assertEqual(before['current'], after['current'])
        self.assertEqual(before['revision'], after['revision'])
        self.assertEqual('original', json.loads(operation.read_text())['operation_id'])

    def test_read_only_lock_cannot_hide_nested_write(self):
        with task_store.task_state_lock(self.ws, execution=False):
            with self.assertRaisesRegex(ValueError, '只读'):
                self.heartbeat()
        self.assertEqual('unclaimed', execution.status(self.ws)['status'])

    def test_purge_does_not_recreate_state(self):
        with task_store.task_state_lock(self.ws):
            task_store.current_path(self.ws).unlink()
        self.assertFalse(task_store.current_path(self.ws).exists())

    def test_corrupt_activity_is_rejected(self):
        self.heartbeat()
        value = task_store.read_current(self.ws)
        for invalid in (True, None, 'yesterday', float('nan'), -1):
            value['execution_control']['last_activity_epoch'] = invalid
            task_store.current_path(self.ws).write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                execution.status(self.ws)

    def test_public_read_does_not_claim_and_cli_heartbeat_claims(self):
        env = dict(os.environ)
        env.pop(execution.SESSION_ENV)
        command = self.public_command()
        result = subprocess.run(command + ['status', '--dir', str(self.ws)], env=env, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('unclaimed', json.loads(result.stdout)['status'])
        result = subprocess.run(command + ['heartbeat', '--dir', str(self.ws)], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(A, json.loads(result.stdout)['session_id'])

    def test_concurrent_first_writers_have_one_owner(self):
        command = self.public_command() + ['heartbeat', '--dir', str(self.ws)]
        processes = [subprocess.Popen(command, env=dict(os.environ, AGENTICOPS_EXECUTION_ID=identity),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for identity in (A,B)]
        results = [process.communicate(timeout=30) for process in processes]
        self.assertEqual([0,2], sorted(process.returncode for process in processes), results)


if __name__ == '__main__':
    unittest.main()

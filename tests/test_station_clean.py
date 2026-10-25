#!/usr/bin/env python3
"""目标清理主流程：任务与工位两个范围的真实文件及状态验证。"""
import os
import json
import subprocess
from pathlib import Path
import sys
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_station_lifecycle as fixture
from workflow import station_cleanup as cleanup, station_resources, task_store, execution
from workflow import station_resources as resources


class CleanupTests(unittest.TestCase):
    setUp = fixture.StationTests.setUp
    prepare_engineering = fixture.StationTests.prepare_engineering
    write = fixture.StationTests.write
    git = fixture.StationTests.git
    takeover = fixture.StationTests.takeover

    def cleanup_request(self, scope='task', materials='preserved'):
        plan = cleanup.plan(self.ws, scope)
        return {'scope': scope, 'materials': materials, 'expected_plan_digest': plan['digest'], 'decision_ref': '测试明确清理决定'}

    def run_clean(self, task, request, operation='op-task-cleanup-test'):
        return cleanup.execute(self.ws, task['issue_key'], task['run_id'], task['_revision'], operation, request)

    def test_task_cleanup_retains_configuration_and_releases_execution(self):
        task = self.takeover()
        (self.ws / 'config/user').write_text('keep')
        (self.ws / 'runtime/build').write_text('generated')
        request = self.cleanup_request()
        result = self.run_clean(task, request)
        self.assertTrue(result['cleanup_result']['complete'], result)
        self.assertEqual('done', result['status'])
        self.assertIsNone(task_store.read_task(self.ws))
        self.assertEqual('unclaimed', execution.status(self.ws)['status'])
        self.assertEqual('keep', (self.ws / 'config/user').read_text())
        for name in ('source', 'runtime'):
            self.assertEqual([], list((self.ws / name).iterdir()))
        self.run_clean(task, request)
        self.assertEqual('unclaimed', execution.status(self.ws)['status'])

    def test_final_material_cleanup_interruption_resumes_without_task_payload(self):
        from workflow import station_operation
        task = self.takeover()
        request = self.cleanup_request()
        original = station_operation.collect_payloads
        def interrupted(base):
            operation = station_operation.read(base)
            if operation['phase'] == 'finalizing':
                raise OSError('temporary material cleanup failure')
            return original(base)
        with mock.patch.object(station_operation, 'collect_payloads', side_effect=interrupted):
            result = self.run_clean(task, request)
        self.assertEqual('finalizing', result['phase'])
        self.assertEqual('running', result['status'])
        self.assertNotIn('exit_task', result)
        self.assertNotIn('cleanup_plan', result)
        self.assertFalse(result['cleanup_result']['complete'])
        result = self.run_clean(task, request)
        self.assertTrue(result['cleanup_result']['complete'], result)
        self.assertEqual([], list((self.ws / '.agenticops/operation-data').iterdir()))
        self.assertEqual('unclaimed', execution.status(self.ws)['status'])

    def test_cli_pending_then_resume_and_repeat(self):
        import contextlib
        import io
        from workflow import station_clean
        self.takeover()
        request = self.cleanup_request()
        args = ['--dir', str(self.ws), '--execute', '--scope', 'task',
                '--operation-id', 'op-cli-cleanup-test', '--expected-plan-digest', request['expected_plan_digest'],
                '--decision-ref', request['decision_ref'], '--materials', 'preserved']
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(station_resources, 'cleanup_declared', return_value=[{'reason': 'offline'}]):
            self.assertEqual(2, station_clean.main(args))
        self.assertIsNotNone(task_store.read_task(self.ws))
        for _ in range(2):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, station_clean.main(['--dir', str(self.ws), '--resume',
                                                       '--operation-id', 'op-cli-cleanup-test']))
        self.assertEqual('unclaimed', execution.status(self.ws)['status'])

    def test_dirty_source_needs_explicit_discard(self):
        task = self.takeover()
        (self.ws / 'source/tapdata/tapdata/file.txt').write_text('work')
        with self.assertRaisesRegex(ValueError, '尚有未保全'):
            self.run_clean(task, self.cleanup_request())
        result = self.run_clean(task, self.cleanup_request(materials='discard'))
        self.assertTrue(result['cleanup_result']['complete'], result)

    def test_resource_failure_keeps_task_and_resumes_same_operation(self):
        task = self.takeover()
        request = self.cleanup_request()
        with mock.patch.object(station_resources, 'cleanup_declared', return_value=[{'reason': 'offline'}]):
            result = self.run_clean(task, request)
        self.assertEqual('running', result['status'])
        self.assertFalse(result['cleanup_result']['released'])
        self.assertIsNotNone(task_store.read_task(self.ws))
        self.assertTrue((self.ws / 'source/tapdata/tapdata/file.txt').exists())
        result = self.run_clean(task, request)
        self.assertTrue(result['cleanup_result']['complete'], result)

    def test_idle_handoff_resumes_pending_cleanup_without_new_run(self):
        task = self.takeover()
        request = self.cleanup_request()
        with mock.patch.object(station_resources, 'cleanup_declared', return_value=[{'reason': 'offline'}]):
            pending = self.run_clean(task, request)
        observed = execution.status(self.ws)
        with mock.patch.dict(os.environ, {execution.SESSION_ENV: 'b' * 32}), \
                mock.patch.object(execution.time, 'time', return_value=observed['last_activity_epoch'] + 8 * 3600):
            execution.claim(self.ws, '接续会话', observed['control_digest'], '已确认旧会话停写和资源状态')
            result = self.run_clean(task, request)
        self.assertEqual(pending['operation_id'], result['operation_id'])
        self.assertEqual(task['run_id'], result['run_id'])
        self.assertTrue(result['cleanup_result']['complete'])
        self.assertEqual('unclaimed', execution.status(self.ws)['status'])

    def test_resource_shutdown_may_flush_runtime_before_delete(self):
        task = self.takeover()
        request = self.cleanup_request()
        def stopped(*args):
            (self.ws / 'runtime/final.log').write_text('flushed')
            return []
        from workflow import station_clean
        with mock.patch.object(station_clean, 'stop_resources', side_effect=stopped):
            result = self.run_clean(task, request)
        self.assertTrue(result['cleanup_result']['complete'], result)

    def test_cancel_before_delete_allows_new_preservation_decision(self):
        task = self.takeover()
        request = self.cleanup_request()
        with mock.patch.object(station_resources, 'cleanup_declared', return_value=[{'reason': 'offline'}]):
            self.run_clean(task, request)
        (self.ws / 'source/tapdata/tapdata/file.txt').write_text('new work')
        result = self.run_clean(task, request)
        self.assertEqual('awaiting_cleanup', result['phase'])
        cancelled = cleanup.cancel(self.ws, 'op-task-cleanup-test', '确认取消并重新盘点')
        self.assertEqual('done', cancelled['status'])
        self.assertIsNotNone(task_store.read_task(self.ws))
        result = self.run_clean(task, self.cleanup_request(materials='discard'), 'op-cleanup-replanned')
        self.assertTrue(result['cleanup_result']['complete'], result)

    def test_takeover_interrupted_before_current_can_be_cleaned(self):
        from workflow import station_operation
        self.prepare_engineering()
        with mock.patch.object(task_store, 'write_task', side_effect=OSError('interrupted before current')):
            with self.assertRaises(OSError):
                self.takeover()
        previous = station_operation.read(self.ws)
        self.assertIsNone(task_store.read_task(self.ws))
        request = self.cleanup_request()
        result = cleanup.execute(self.ws, None, previous['run_id'], 0, 'op-initial-takeover-clean', request)
        self.assertTrue(result['cleanup_result']['complete'], result)
        self.assertEqual('unclaimed', execution.status(self.ws)['status'])

    def test_idle_station_can_clear_previous_runtime(self):
        (self.ws / 'runtime/leftover').write_text('generated')
        request = self.cleanup_request()
        result = cleanup.execute(self.ws, None, 'run-' + 'a' * 32, 0, 'op-idle-cleanup-test', request)
        self.assertTrue(result['cleanup_result']['complete'], result)
        self.assertEqual([], list((self.ws / 'runtime').iterdir()))

    def test_partial_delete_retains_task_until_original_operation_finishes(self):
        from workflow import station_cleanup_tree
        task = self.takeover()
        request = self.cleanup_request()
        original = station_cleanup_tree.remove
        def interrupted(base, operation, item):
            if item['path'] == 'runtime':
                raise OSError('temporary failure')
            return original(base, operation, item)
        with mock.patch.object(station_cleanup_tree, 'remove', side_effect=interrupted):
            result = self.run_clean(task, request)
        self.assertFalse(result['cleanup_result']['complete'])
        self.assertIsNotNone(task_store.read_task(self.ws))
        result = self.run_clean(task, request)
        self.assertTrue(result['cleanup_result']['complete'], result)

    def test_new_task_after_cleanup_does_not_inherit_old_execution(self):
        self.prepare_engineering()
        self.takeover()
        task = task_store.read_task(self.ws)
        result = self.run_clean(task, self.cleanup_request())
        self.assertTrue(result['cleanup_result']['complete'], result)
        with mock.patch.dict(os.environ, {'AGENTICOPS_EXECUTION_ID': 'b' * 32}):
            next_task = self.takeover('op-takeover-next')
            self.assertNotEqual(task['run_id'], next_task['run_id'])
            self.assertEqual('b' * 32, execution.status(self.ws)['session_id'])

    def test_station_cleanup_removes_user_data_preserves_initial_state(self):
        task = self.takeover()
        (self.ws / 'config/secret').write_text('discard')
        (self.ws / 'user-note').write_text('discard')
        result = self.run_clean(task, self.cleanup_request('station', 'discard'))
        self.assertTrue(result['cleanup_result']['complete'], result)
        self.assertFalse((self.ws / 'user-note').exists())
        self.assertEqual([], list((self.ws / 'config').iterdir()))
        self.assertTrue((self.ws / '.agenticops/station.json').exists())
        self.assertTrue((self.ws / '.agenticops/init.json').exists())

    def ready(self):
        return self.takeover()

    def run_exit(self, task):
        return self.run_clean(task, self.cleanup_request())

    def enable_docker(self):
        source = Path(__file__).resolve().parents[1] / 'projects/tapdata/station-clean.json'
        target = self.product / 'projects/tapdata/station-clean.json'
        target.write_bytes(source.read_bytes())

    def docker_fixture(self, mismatch=False):
        self.enable_docker()
        root = self.ws / 'runtime/tapdata-test-env'; root.mkdir()
        state = {'container': True, 'network': True}; calls = []
        def run(args, **kwargs):
            if args[0] != 'docker': return self.real_run(args, **kwargs)
            calls.append(args)
            command = args[1:]
            if command[0] == 'ps': output = 'container-id' if state['container'] else ''
            elif command[:2] == ['network', 'ls']: output = 'network-id' if state['network'] else ''
            elif command[0] == 'inspect' or command[:2] == ['network', 'inspect']:
                labels = {'io.agenticops.station': 'wrong' if mismatch else 'a' * 32, 'com.docker.compose.project': 'ao-tapdata-' + 'a' * 32}
                output = json.dumps([{'Id': command[-1], 'Labels': labels, 'Config': {'Labels': labels}}])
            elif command[0] == 'stop': output = ''
            elif command[0] == 'rm': state['container'] = False; output = ''
            elif command[:2] == ['network', 'rm']: state['network'] = False; output = ''
            else: raise AssertionError(args)
            return subprocess.CompletedProcess(args, 0, output, '')
        return root, calls, run

    def test_declared_docker_removes_only_verified_containers_and_networks(self):
        task = self.ready(); self.real_run = subprocess.run
        root, calls, run = self.docker_fixture()
        with mock.patch.object(resources.subprocess, 'run', side_effect=run): result = self.run_exit(task)
        self.assertFalse(root.exists()); self.assertEqual([], result['cleanup_result']['resources'])
        self.assertIn(['docker', 'rm', 'container-id'], calls)
        self.assertIn(['docker', 'network', 'rm', 'network-id'], calls)
        self.assertFalse(any('prune' in c or 'volume' in c or 'image' in c for c in calls))

    def test_docker_resources_discovered_after_runtime_directory_loss(self):
        task = self.ready(); self.real_run = subprocess.run
        root, calls, run = self.docker_fixture()
        root.rmdir()
        with mock.patch.object(resources.shutil, 'which', return_value='/fixture/docker'), \
                mock.patch.object(resources.subprocess, 'run', side_effect=run):
            result = self.run_exit(task)
        self.assertEqual([], result['cleanup_result']['resources'])
        self.assertIn(['docker', 'rm', 'container-id'], calls)
        self.assertIn(['docker', 'network', 'rm', 'network-id'], calls)

    def test_missing_driver_with_environment_configuration_is_unverified(self):
        self.enable_docker()
        task = self.ready()
        (self.ws / 'config/environments').mkdir()
        real_run = subprocess.run
        def unavailable(args, **kwargs):
            if args[0] == 'docker':
                raise FileNotFoundError('Docker unavailable')
            return real_run(args, **kwargs)
        with mock.patch.object(resources.shutil, 'which', return_value=None), \
                mock.patch.object(resources.subprocess, 'run', side_effect=unavailable):
            result = self.run_exit(task)
        self.assertFalse(result['cleanup_result']['complete'])
        self.assertTrue(result['cleanup_result']['resources'])

    def test_unused_optional_environment_does_not_require_docker(self):
        self.enable_docker()
        task = self.ready()
        real_run = subprocess.run
        def no_docker(args, **kwargs):
            if args[0] == 'docker':
                raise AssertionError('从未配置的可选环境不安装 Docker')
            return real_run(args, **kwargs)
        with mock.patch.object(resources.shutil, 'which', return_value=None), \
                mock.patch.object(resources.subprocess, 'run', side_effect=no_docker):
            result = self.run_exit(task)
        self.assertEqual([], result['cleanup_result']['resources'])

    def test_docker_identity_mismatch_never_deletes(self):
        task = self.ready(); self.real_run = subprocess.run
        root, calls, run = self.docker_fixture(mismatch=True)
        with mock.patch.object(resources.subprocess, 'run', side_effect=run): result = self.run_exit(task)
        self.assertTrue(root.exists()); self.assertFalse(result['cleanup_result']['released'])
        self.assertFalse(any('stop' in c or 'rm' in c for c in calls))

    def test_malformed_docker_readback_is_reported_without_deletion(self):
        task = self.ready(); self.real_run = subprocess.run
        root, calls, run = self.docker_fixture()
        def malformed(args, **kwargs):
            if args[:2] == ['docker', 'inspect']:
                return subprocess.CompletedProcess(args, 0, '[]', '')
            return run(args, **kwargs)
        with mock.patch.object(resources.subprocess, 'run', side_effect=malformed):
            result = self.run_exit(task)
        self.assertFalse(result['cleanup_result']['complete'])
        self.assertTrue(root.exists())
        self.assertFalse(any('stop' in c or 'rm' in c for c in calls))
        self.assertIn('回读结构', result['cleanup_result']['resources'][0]['reason'])

    def test_volume_and_malformed_mounts_keep_container_for_agent(self):
        task = self.ready(); self.real_run = subprocess.run
        root, calls, run = self.docker_fixture()
        for mounts in ([{'Type': 'volume', 'Name': 'user-data'}], ['invalid'], {}):
            with self.subTest(mounts=mounts):
                def observation(args, **kwargs):
                    result = run(args, **kwargs)
                    if args[:2] == ['docker', 'inspect']:
                        rows = json.loads(result.stdout)
                        rows[0]['Mounts'] = mounts
                        return subprocess.CompletedProcess(args, 0, json.dumps(rows), '')
                    return result
                with mock.patch.object(resources.subprocess, 'run', side_effect=observation):
                    result = self.run_exit(task)
                self.assertFalse(result['cleanup_result']['complete'])
                self.assertTrue(root.exists())
                self.assertFalse(any('stop' in c or 'rm' in c for c in calls))



if __name__ == '__main__':
    unittest.main()

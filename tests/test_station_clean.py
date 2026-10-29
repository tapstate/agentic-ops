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
        self.assertEqual([], list((self.ws / 'runtime').iterdir()))
        repository = self.ws / 'source/tapdata/tapdata'
        self.assertTrue((repository / '.git').is_dir())
        self.assertEqual(task['engineering_baseline']['repositories']['tapdata/tapdata']['commit_sha'],
                         self.git(repository, 'rev-parse', 'HEAD'))
        self.assertEqual('', self.git(repository, 'status', '--porcelain'))
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
        # Jira 已交接，取消本地未开始删除的清理仍应能接续。
        from workflow import jira_coordination
        jira_coordination.save(self.ws, {'entries': {}, 'proof': None})
        cancelled = cleanup.cancel(self.ws, 'op-task-cleanup-test', '确认取消并重新盘点')
        self.assertEqual('done', cancelled['status'])
        self.assertIsNotNone(task_store.read_task(self.ws))
        result = self.run_clean(task, self.cleanup_request(materials='discard'), 'op-cleanup-replanned')
        self.assertTrue(result['cleanup_result']['complete'], result)

    def test_external_cleanup_readback_after_handoff_is_narrowly_scoped(self):
        from workflow import jira_coordination
        task = self.takeover()
        entry = {'kind': 'external', 'id': 'fixture-resource', 'producer': 'fixture',
                 'action': 'delete', 'status': 'active', 'before': {'id': 'fixture-resource', 'protected': False}}
        task_store._write_json_atomic(task_store.task_directory(self.ws, task['issue_key']) / 'resources.json',
            {'schema_version': 1, 'run_id': task['run_id'], 'entries': [entry]})
        request = self.cleanup_request()
        pending = self.run_clean(task, request)
        self.assertEqual('awaiting_resources', pending['phase'])
        jira_coordination.save(self.ws, {'entries': {}, 'proof': None})
        receipt = dict(entry, status='cleaned', readback_ref='fixture:已核实外部资源不存在')
        with self.assertRaises(ValueError):
            resources.register(self.ws, task['issue_key'], task['run_id'], [dict(receipt, id='other')], pending['operation_id'])
        resources.register(self.ws, task['issue_key'], task['run_id'], [receipt], pending['operation_id'])
        self.assertTrue(self.run_clean(task, request)['cleanup_result']['complete'])

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
                output = json.dumps([{'Id': command[-1], 'Labels': labels, 'Config': {'Labels': labels}, 'Mounts': []}])
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
        for mounts in ([{'Type': 'volume', 'Name': 'user-data'}], ['invalid'], {}, None):
            with self.subTest(mounts=mounts):
                def observation(args, **kwargs):
                    result = run(args, **kwargs)
                    if args[:2] == ['docker', 'inspect']:
                        rows = json.loads(result.stdout)
                        if mounts is None:
                            rows[0].pop('Mounts', None)
                        else:
                            rows[0]['Mounts'] = mounts
                        return subprocess.CompletedProcess(args, 0, json.dumps(rows), '')
                    return result
                with mock.patch.object(resources.subprocess, 'run', side_effect=observation):
                    result = self.run_exit(task)
                self.assertFalse(result['cleanup_result']['complete'])
                self.assertTrue(root.exists())
                self.assertFalse(any('stop' in c or 'rm' in c for c in calls))



class SourceReuseTests(unittest.TestCase):
    setUp = fixture.StationTests.setUp
    prepare_engineering = fixture.StationTests.prepare_engineering
    write = fixture.StationTests.write
    git = fixture.StationTests.git
    takeover = fixture.StationTests.takeover
    cleanup_request = CleanupTests.cleanup_request
    run_clean = CleanupTests.run_clean

    def test_discard_is_concentrated_and_restores_baseline_without_reclone(self):
        task = self.takeover()
        repo = self.ws / 'source/tapdata/tapdata'
        inode, metadata_inode = repo.stat().st_ino, (repo / '.git').stat().st_ino
        self.git(repo, 'config', 'user.name', 'Test')
        self.git(repo, 'config', 'user.email', 'test@example.com')
        self.git(repo, 'checkout', '-b', 'local-work')
        (repo / 'file.txt').write_text('local commit')
        self.git(repo, 'commit', '-am', 'unpublished')
        (repo / 'file.txt').write_text('stash work')
        self.git(repo, 'stash', 'push')
        (repo / '.gitignore').write_text('unique.secret\n')
        (repo / 'unique.secret').write_text('fixture unique data')
        (repo / 'untracked').write_text('keep or explicitly discard')
        (repo / 'file.txt').write_text('staged')
        self.git(repo, 'add', 'file.txt')
        (repo / 'file.txt').write_text('unstaged')
        plan = cleanup.plan(self.ws)
        report = plan['source']['repositories'][0]
        self.assertIn('refs/heads/local-work', report['unpreserved_refs'])
        self.assertIn('refs/stash', report['unpreserved_refs'])
        self.assertIn('unique.secret', report['unique_files'])
        self.assertIn('untracked', report['unique_files'])
        self.assertIn('file.txt', report['changed_files'])
        with self.assertRaisesRegex(ValueError, '尚有未保全'):
            self.run_clean(task, self.cleanup_request())
        result = self.run_clean(task, self.cleanup_request(materials='discard'))
        self.assertTrue(result['cleanup_result']['complete'], result)
        self.assertEqual((inode, metadata_inode), (repo.stat().st_ino, (repo / '.git').stat().st_ino))
        self.assertEqual('baseline\n', (repo / 'file.txt').read_text())
        self.assertFalse((repo / 'unique.secret').exists())
        self.assertEqual('', self.git(repo, 'for-each-ref', '--format=%(refname)', 'refs/heads', 'refs/stash'))
        from workflow import station_source
        original = station_source.git
        calls = []
        def observed(path, *args, **kwargs):
            calls.append(args)
            return original(path, *args, **kwargs)
        with mock.patch.object(station_source, 'git', side_effect=observed):
            self.takeover('op-reuse-next-task')
        self.assertFalse(any('clone' in args for args in calls))
        self.assertEqual(metadata_inode, (repo / '.git').stat().st_ino)

    def test_interrupted_restore_keeps_private_repository_and_resumes(self):
        from workflow import station_source_reset as reset
        task = self.takeover()
        repo = self.ws / 'source/tapdata/tapdata'
        inode = (repo / '.git').stat().st_ino
        request = self.cleanup_request()
        original = reset.source.git
        def interrupted(path, *args, **kwargs):
            if 'reset' in args:
                raise ValueError('fixture reset failure, not delete permission')
            return original(path, *args, **kwargs)
        with mock.patch.object(reset.source, 'git', side_effect=interrupted):
            result = self.run_clean(task, request)
        self.assertFalse(result['cleanup_result']['complete'])
        self.assertFalse(repo.exists())
        kept = list((self.ws / '.agenticops/cleanup/op-task-cleanup-test').glob('restore-*'))
        self.assertEqual(1, len(kept))
        self.assertEqual(inode, (kept[0] / '.git').stat().st_ino)
        result = self.run_clean(task, request)
        self.assertTrue(result['cleanup_result']['complete'], result)
        self.assertEqual(inode, (repo / '.git').stat().st_ino)

    def test_ready_snapshot_recovers_publish_receipt_failure_and_rejects_new_files(self):
        from workflow import station_operation
        task = self.takeover()
        request = self.cleanup_request()
        original = station_operation.receipt
        def interrupted(base, operation, name, value):
            if name.startswith('restore:'):
                raise OSError('receipt unavailable')
            return original(base, operation, name, value)
        with mock.patch.object(station_operation, 'receipt', side_effect=interrupted):
            result = self.run_clean(task, request)
        self.assertFalse(result['cleanup_result']['complete'])
        repo = self.ws / 'source/tapdata/tapdata'
        (repo / 'late').write_text('must survive')
        result = self.run_clean(task, request)
        self.assertFalse(result['cleanup_result']['complete'])
        self.assertEqual('must survive', (repo / 'late').read_text())

    def test_git_lock_or_permission_error_never_deletes_repository(self):
        from workflow import station_source_reset as reset
        task = self.takeover()
        repo = self.ws / 'source/tapdata/tapdata'
        (repo / '.git/index.lock').write_text('another process')
        with self.assertRaisesRegex(ValueError, 'Git 锁'):
            self.cleanup_request(materials='discard')
        self.assertTrue(repo.exists())
        (repo / '.git/index.lock').unlink()
        with mock.patch.object(reset, 'object_type', side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):
                self.cleanup_request(materials='discard')
        self.assertTrue(repo.exists())

    def test_old_delete_plan_keeps_original_semantics(self):
        from workflow import station_cleanup_tree as trees, station_operation, engineering_baseline
        task = self.takeover()
        plan = cleanup.plan(self.ws)
        plan.update(schema_version=8, targets=[trees.entry(self.ws, name, True) for name in ('source', 'runtime')])
        plan.pop('source_reuse')
        plan['digest'] = engineering_baseline.digest({k:v for k,v in plan.items() if k != 'digest'})
        request = {'scope':'task','materials':'preserved','expected_plan_digest':plan['digest'],'decision_ref':'old confirmed delete'}
        with task_store.task_state_lock(self.ws):
            operation = station_operation.begin(self.ws, 'clean', 'op-old-delete-plan', task['_revision'], request, task['run_id'])
            operation.update(cleanup_plan=plan, exit_task=task)
            station_operation.save(self.ws, operation)
        result = self.run_clean(task, request, 'op-old-delete-plan')
        self.assertTrue(result['cleanup_result']['complete'], result)
        self.assertEqual([], list((self.ws / 'source').iterdir()))

    def test_multi_repository_restore_and_station_cleanup_keep_config_boundary(self):
        self.prepare_engineering(2)
        task = self.takeover()
        (self.ws / 'config/user').write_text('user configuration')
        result = self.run_clean(task, self.cleanup_request(scope='station'))
        self.assertTrue(result['cleanup_result']['complete'], result)
        self.assertEqual([], list((self.ws / 'config').iterdir()))
        for name in ('tapdata', 'tapdata-common-lib'):
            self.assertTrue((self.ws / 'source/tapdata' / name / '.git').is_dir())

    def missing_baseline(self, copy_to=None):
        from workflow import engineering_baseline as baseline, source_pool
        task = self.takeover()
        repo = self.ws / 'source/tapdata/tapdata'
        self.git(repo, 'config', 'user.name', 'Test')
        self.git(repo, 'config', 'user.email', 'test@example.com')
        tree = self.git(repo, 'rev-parse', 'HEAD^{tree}')
        sha = self.git(repo, 'commit-tree', tree, '-m', 'old frozen baseline')
        value = task['engineering_baseline']
        value['repositories']['tapdata/tapdata'].update(commit_sha=sha, ref_kind='commit', ref_name=sha)
        value['digest'] = baseline.digest({k:v for k,v in value.items() if k != 'digest'})
        task_store.write_task(self.ws, task)
        if copy_to:
            target = self.remote if copy_to == 'remote' else source_pool.pool_path(self.ws, 'tapdata/tapdata', str(self.remote))
            self.git(target, 'fetch', str(repo), sha)
        (repo / '.git/objects' / sha[:2] / sha[2:]).unlink()
        return task_store.read_task(self.ws), repo, sha

    def test_missing_local_object_recovers_from_cache_or_exact_remote(self):
        # 本用例覆盖远端精确 SHA；另一个用例验证缓存路径。
        task, repo, sha = self.missing_baseline('remote')
        plan = cleanup.plan(self.ws)
        self.assertEqual('remote', plan['source_reuse']['repositories'][0]['object_source'])
        inode = (repo / '.git').stat().st_ino
        result = self.run_clean(task, self.cleanup_request(materials='discard'))
        self.assertTrue(result['cleanup_result']['complete'], result)
        self.assertEqual(sha, self.git(repo, 'rev-parse', 'HEAD'))
        self.assertEqual(inode, (repo / '.git').stat().st_ino)

    def test_missing_local_object_recovers_from_cache(self):
        task, repo, sha = self.missing_baseline('cache')
        plan = cleanup.plan(self.ws)
        self.assertEqual('cache', plan['source_reuse']['repositories'][0]['object_source'])
        result = self.run_clean(task, self.cleanup_request(materials='discard'))
        self.assertTrue(result['cleanup_result']['complete'], result)
        self.assertEqual(sha, self.git(repo, 'rev-parse', 'HEAD'))

    def test_proven_unrecoverable_target_deletes_only_that_repository(self):
        task, repo, sha = self.missing_baseline()
        plan = cleanup.plan(self.ws)
        item = plan['source_reuse']['repositories'][0]
        self.assertEqual('delete', item['action'])
        self.assertIn('not our ref', item['reason'])
        result = self.run_clean(task, self.cleanup_request(materials='discard'))
        self.assertTrue(result['cleanup_result']['complete'], result)
        self.assertFalse(repo.exists())
        self.assertEqual('delete', result['cleanup_result']['source'][0]['action'])
        self.takeover('op-after-unrecoverable')
        self.assertTrue((repo / '.git').is_dir())

    def test_no_checkout_partial_takeover_cleanup_retains_repository(self):
        from workflow import station, station_operation
        self.prepare_engineering()
        request = dict(self.request, version='missing', explicit_branches={})
        with task_store.task_state_lock(self.ws):
            station_operation.begin(self.ws, 'takeover', 'op-old-partial-takeover', 0, request)
        with self.assertRaises(ValueError):
            station.takeover(self.ws, request, 'op-old-partial-takeover', 0)
        task = task_store.read_task(self.ws)
        result = self.run_clean(task, self.cleanup_request())
        self.assertTrue(result['cleanup_result']['complete'], result)
        self.assertTrue((self.ws / 'source/tapdata/tapdata/.git').is_dir())

    def test_ready_snapshot_recovers_after_publish_without_repeating_git(self):
        from workflow import station_operation, station_source_reset as reset
        task = self.takeover()
        request = self.cleanup_request()
        original = station_operation.receipt
        def interrupted(base, operation, name, value):
            if name.startswith('restore:'):
                raise OSError('receipt unavailable')
            return original(base, operation, name, value)
        with mock.patch.object(station_operation, 'receipt', side_effect=interrupted):
            self.assertFalse(self.run_clean(task, request)['cleanup_result']['complete'])
        original_git = reset.source.git
        def reject_repeated_reset(path, *args, **kwargs):
            if 'reset' in args:
                raise AssertionError('must not repeat completed reset')
            return original_git(path, *args, **kwargs)
        with mock.patch.object(reset.source, 'git', side_effect=reject_repeated_reset):
            self.assertTrue(self.run_clean(task, request)['cleanup_result']['complete'])


if __name__ == '__main__':
    unittest.main()

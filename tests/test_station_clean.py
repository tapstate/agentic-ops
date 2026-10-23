#!/usr/bin/env python3
"""两份清单、真实 Git 保护、非阻塞退出及原操作异常接力。"""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_station_lifecycle as fixture
from station_fixture import product_cli
from workflow import station, station_clean as clean, station_clean_rules as rules, station_reset_result as reset
from workflow import station_archive, station_operation as operations, station_resources as resources, task_store


class CleanupTests(unittest.TestCase):
    setUp = fixture.StationTests.setUp
    prepare_engineering = fixture.StationTests.prepare_engineering
    write = fixture.StationTests.write
    git = fixture.StationTests.git
    takeover = fixture.StationTests.takeover

    def test_nested_environment_configuration_clear_preserves_other_environments_and_backups(self):
        for name in ('environments/dev', 'environments/other', 'backups/original'):
            folder = self.ws / 'config' / name
            folder.mkdir(parents=True)
            (folder / 'config.json').write_text('{}')
        result = clean.execute(self.ws, 'work-clean', None, None, 0, 'op-clear-environment',
                               {'clear_config': ['config/environments/dev']})
        self.assertFalse((self.ws / 'config/environments/dev').exists())
        self.assertTrue((self.ws / 'config/environments/other/config.json').exists())
        self.assertTrue((self.ws / 'config/backups/original/config.json').exists())

    def test_runtime_ownership_covers_arbitrary_outputs_without_task(self):
        cache = self.ws / 'runtime/new-build-cache'
        cache.mkdir()
        (cache / 'artifact').write_text('generated')
        (self.ws / 'runtime/one-off.log').write_text('log')
        result = clean.execute(self.ws, 'work-clean', None, None, 0, 'op-idle-cache', {})
        self.assertFalse(cache.exists())
        self.assertFalse((self.ws / 'runtime/one-off.log').exists())
        self.assertTrue((self.ws / 'runtime').is_dir())
        self.assertFalse([row for row in result['cleanup_result']['residuals']
                          if row['path'].startswith('runtime/')])

    def test_runtime_ownership_preserves_nested_git_and_external_link_target(self):
        task = self.ready()
        nested = self.ws / 'runtime/source-copy'
        nested.mkdir()
        (nested / '.git').mkdir()
        (nested / 'build.sh').write_text('source')
        external = self.ws / 'config/persistent'
        external.write_text('keep')
        (self.ws / 'runtime/external-link').symlink_to(external)
        (self.ws / 'runtime/generated.log').write_text('remove')
        result = self.run_exit(task)
        self.assertEqual('keep', external.read_text())
        self.assertTrue((nested / 'build.sh').exists())
        self.assertFalse((self.ws / 'runtime/generated.log').exists())
        self.assertFalse(result['cleanup_result']['complete'])

    def ready(self):
        task = self.takeover()
        self.repo = self.ws / 'source/tapdata/tapdata'
        return task

    def run_exit(self, task, kind='release', options=None, op='op-cleanup-test', mode='execute'):
        return station.execute(self.ws, kind, task['issue_key'], task['run_id'], task['_revision'], op, options or {}, mode)

    def generated(self, relative='target', filename='output.bin'):
        directory = self.repo / relative
        directory.mkdir(parents=True, exist_ok=True)
        (directory / filename).write_bytes(b'generated')
        return directory

    def test_interrupted_release_preserves_all_git_state_and_new_source(self):
        task = self.ready()
        (self.repo / 'file.txt').write_text('staged\n')
        self.git(self.repo, 'add', 'file.txt')
        (self.repo / 'file.txt').write_text('unstaged\n')
        (self.repo / 'new.py').write_text('source\n')
        before = (self.git(self.repo, 'rev-parse', 'HEAD'), (self.repo / '.git/index').read_bytes(), self.git(self.repo, 'diff', '--binary'))
        generated = self.generated()
        result = self.run_exit(task)
        self.assertFalse(generated.exists())
        self.assertIsNone(task_store.read_task(self.ws))
        self.assertEqual(before, (self.git(self.repo, 'rev-parse', 'HEAD'), (self.repo / '.git/index').read_bytes(), self.git(self.repo, 'diff', '--binary')))
        self.assertEqual('source\n', (self.repo / 'new.py').read_text())
        self.assertTrue(result['cleanup_result']['released'])
        self.assertEqual('in_progress', result['exit_task']['outcome'])
        self.assertIn('source/tapdata/tapdata/new.py', [r['path'] for r in result['cleanup_result']['residuals']])

    def test_completed_release_does_not_revalidate_delivery(self):
        task = self.ready()
        task['outcome'] = 'completed'; task['stage'] = 'completed'
        task_store.write_task(self.ws, task)
        with mock.patch.object(station, 'completion_proof', side_effect=AssertionError('不重复完成验收')):
            result = self.run_exit(task)
        self.assertEqual('completed', result['exit_task']['outcome'])
        self.assertTrue(result['cleanup_result']['released'])

    def test_work_cleanup_retains_task_and_authorization(self):
        task = self.ready(); generated = self.generated()
        auth = self.ws / '.agenticops/authorization.json'; auth.write_text('{}')
        result = self.run_exit(task, 'work-clean')
        self.assertFalse(generated.exists()); self.assertTrue(auth.exists())
        self.assertEqual(task['run_id'], task_store.read_task(self.ws)['run_id'])
        self.assertFalse(result['cleanup_result']['released'])

    def test_failed_branch_preparation_can_exit_without_finishing_checkout(self):
        task = self.ready(); original = station.source.git
        def fail_checkout(path, *args, **kwargs):
            if args[0] == 'checkout': raise OSError('interrupted checkout')
            return original(path, *args, **kwargs)
        with mock.patch.object(station.source, 'git', side_effect=fail_checkout):
            with self.assertRaises(OSError):
                station.scope_change(self.ws, task['issue_key'], task['run_id'], task['_revision'], 'op-scope-interrupted', 'tapdata/tapdata', None, 'develop', ['file.txt'], 'unit')
        branches = self.git(self.repo, 'for-each-ref', 'refs/heads/')
        task = task_store.read_task(self.ws)
        result = self.run_exit(task)
        self.assertTrue(result['cleanup_result']['released'])
        self.assertEqual(branches, self.git(self.repo, 'for-each-ref', 'refs/heads/'))
        self.assertEqual('scope_change', result['previous_operation']['kind'])

    def test_unreferenced_unknown_operation_material_is_preserved_without_blocking(self):
        task = self.ready()
        folder = self.ws / '.agenticops/operation-data'; folder.mkdir(exist_ok=True)
        unknown = folder / 'user-note.json'; unknown.write_text('not managed JSON')
        result = self.run_exit(task)
        self.assertTrue(result['cleanup_result']['released'])
        self.assertEqual('not managed JSON', unknown.read_text())
        self.assertIn('.agenticops/operation-data/user-note.json', [r['path'] for r in result['cleanup_result']['residuals']])

    def test_archive_failure_requires_decision_before_release(self):
        task = self.ready(); generated = self.generated()
        with mock.patch.object(station_archive, 'publish', side_effect=OSError('disk unavailable')):
            result = self.run_exit(task)
        self.assertTrue(generated.exists()); self.assertIsNotNone(task_store.read_task(self.ws))
        self.assertEqual('awaiting_decision', result['archive_result']['status'])
        result = clean.execute(self.ws, 'release', task['issue_key'], task['run_id'], task['_revision'], 'op-cleanup-test', {},
                               archive_decision={'action': 'discard', 'digest': result['archive_result']['digest']})
        self.assertFalse(generated.exists()); self.assertIsNone(task_store.read_task(self.ws))
        self.assertEqual('done', result['status'])

    def test_post_cleanup_archive_failure_resumes_without_repeating_cleanup(self):
        task = self.ready(); generated = self.generated()
        publish = station_archive.publish
        def fail_final(*args, **kwargs):
            if kwargs['operation'].get('cleanup_result'):
                raise PermissionError('final unavailable')
            return publish(*args, **kwargs)
        with mock.patch.object(station_archive, 'publish', side_effect=fail_final):
            result = self.run_exit(task)
        self.assertFalse(generated.exists()); self.assertIsNone(task_store.read_task(self.ws))
        self.assertTrue(result['cleanup_result']['released'])
        self.assertEqual('after-cleanup', result['archive_result']['checkpoint'])
        generated.mkdir(); (generated / 'later').write_text('must preserve')
        result = clean.execute(self.ws, 'release', task['issue_key'], task['run_id'], task['_revision'], 'op-cleanup-test', {},
                               archive_decision={'action': 'retry', 'digest': result['archive_result']['digest']})
        self.assertEqual('done', result['status'])
        self.assertTrue((generated / 'later').exists())

    def test_archive_is_repeatable_snapshot_without_freezing_task(self):
        task = self.ready()
        self.run_exit(task, 'archive')
        task = task_store.read_task(self.ws)
        self.assertIsNone(task.get('archive_ref'))
        task_store.require_development(self.ws, task)
        (self.repo / 'file.txt').write_text('still working')
        self.run_exit(task, 'archive', op='op-another-snapshot')
        self.assertEqual('still working', (self.repo / 'file.txt').read_text())

    def test_configuration_default_preserve_and_explicit_remove(self):
        task = self.ready(); config = self.ws / 'config/service.json'; config.write_text('reusable')
        self.run_exit(task, 'work-clean')
        self.assertTrue(config.exists())
        self.run_exit(task, 'work-clean', {'clear_config': ['config/service.json']}, 'op-remove-config')
        self.assertFalse(config.exists())

    def test_tracked_same_name_directory_is_preserved(self):
        task = self.ready(); path = self.generated('target', 'source.java')
        self.git(self.repo, 'add', 'target/source.java')
        before = (self.repo / '.git/index').read_bytes()
        self.run_exit(task)
        self.assertTrue(path.exists()); self.assertEqual(before, (self.repo / '.git/index').read_bytes())

    def test_second_project_configuration_uses_same_executor(self):
        task = self.ready(); path = self.generated('build-cache')
        shutil.copytree(self.product / 'projects/tapdata', self.product / 'projects/second-project')
        binding_path = self.ws / '.agenticops/station.json'
        binding = json.loads(binding_path.read_text()); binding['project'] = 'second-project'; self.write(binding_path, binding)
        profile_path = self.product / 'projects/second-project/profile.json'
        profile = json.loads(profile_path.read_text()); profile['profile_id'] = 'second-project'; self.write(profile_path, profile)
        project = self.product / 'projects/second-project/station-clean.json'
        value = {'version': 3, 'rules': [{'scope': 'repository', 'pattern': 'build-cache', 'type': 'directory', 'action': 'generated'}]}
        self.write(project, value)
        unlisted = self.generated('target')
        self.run_exit(task)
        self.assertFalse(path.exists()); self.assertTrue(unlisted.exists())

    def test_staged_deleted_source_directory_is_still_protected(self):
        task = self.ready(); path = self.generated('target', 'source.java')
        self.git(self.repo, 'add', 'target/source.java')
        self.git(self.repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'source')
        self.git(self.repo, 'rm', '--cached', 'target/source.java')
        before = (self.repo / '.git/index').read_bytes()
        self.run_exit(task)
        self.assertTrue((path / 'source.java').exists())
        self.assertEqual(before, (self.repo / '.git/index').read_bytes())

    def test_branch_disposition_failure_does_not_reoccupy_station(self):
        from workflow import station_disposition, engineering_baseline
        task = self.ready(); self.run_exit(task)
        value = {'disposition_id': 'disposition-test-delete', 'phase': 'intent', 'object_id': 'branch:test',
                 'resource_type': 'git-branch', 'action': 'delete', 'decision_ref': 'user:explicit',
                 'before': {'repository': 'tapdata/tapdata', 'ref': 'refs/heads/test', 'sha': 'a' * 40, 'protected': False}}
        value['confirmed_digest'] = engineering_baseline.digest(value)
        recorded = station_disposition.record(self.ws, task['issue_key'], task['run_id'], value)
        self.assertEqual('intent', recorded['phase'])
        with mock.patch.object(station_disposition.task_store, '_write_json_atomic', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                station_disposition.record(self.ws, task['issue_key'], task['run_id'], {'disposition_id': value['disposition_id'], 'phase': 'unknown', 'object_id': value['object_id'], 'intent_digest': recorded['digest'], 'readback_ref': 'unknown-operation'})
        self.assertIsNone(task_store.read_task(self.ws))
        self.assertTrue(operations.read(self.ws)['cleanup_result']['released'])

    def test_nested_generated_directory_is_found(self):
        task = self.ready()
        (self.repo / 'module').mkdir(); (self.repo / 'module/source.java').write_text('source')
        self.git(self.repo, 'add', 'module/source.java')
        output = self.generated('module/target')
        self.run_exit(task)
        self.assertFalse(output.exists()); self.assertTrue((self.repo / 'module/source.java').exists())

    def test_symlink_never_follows_external_target(self):
        task = self.ready(); external = self.root / 'external'; external.mkdir(); (external / 'important').write_text('keep')
        (self.repo / 'target').symlink_to(external, target_is_directory=True)
        result = self.run_exit(task)
        self.assertTrue((external / 'important').exists()); self.assertTrue((self.repo / 'target').is_symlink())
        self.assertTrue(result['cleanup_result']['released'])

    def test_nested_git_and_special_objects_are_retained(self):
        task = self.ready(); path = self.generated(); (path / '.git').mkdir()
        other = self.generated('node_modules'); os.mkfifo(other / 'pipe')
        result = self.run_exit(task)
        self.assertTrue((path / 'output.bin').exists()); self.assertTrue((other / 'pipe').exists())
        self.assertTrue(result['cleanup_result']['released']); self.assertFalse(result['cleanup_result']['complete'])

    def test_preflight_is_read_only(self):
        task = self.ready(); path = self.generated()
        before = (self.ws / '.agenticops/operation.json').read_bytes()
        clean.check(self.ws, task)
        self.assertEqual(before, (self.ws / '.agenticops/operation.json').read_bytes()); self.assertTrue(path.exists())

    def test_nested_repository_tracked_generated_name_is_preserved(self):
        task = self.ready()
        nested = self.repo / 'vendor/local'; nested.mkdir(parents=True)
        self.git(nested, 'init')
        source = self.generated('vendor/local/target', 'source.py') / 'source.py'
        self.git(nested, 'add', 'target/source.py')
        index = (nested / '.git/index').read_bytes()
        result = self.run_exit(task)
        self.assertEqual(b'generated', source.read_bytes())
        self.assertEqual(index, (nested / '.git/index').read_bytes())
        self.assertTrue(result['cleanup_result']['released'])
        self.assertIn('source/tapdata/tapdata/vendor/local', [row['path'] for row in result['cleanup_result']['residuals']])

    def test_nested_repository_created_after_prepare_is_preserved(self):
        task = self.ready()
        output = self.generated('vendor/local/target')
        self.run_exit(task, mode='prepare')
        self.git(output.parent, 'init')
        self.git(output.parent, 'add', 'target/output.bin')
        result = self.run_exit(task)
        self.assertTrue((output / 'output.bin').exists())
        self.assertTrue(result['cleanup_result']['released'])
        self.assertFalse(result['cleanup_result']['complete'])

    def test_prepare_and_native_cleanup_then_verify(self):
        task = self.ready(); path = self.generated()
        self.run_exit(task, mode='prepare')
        shutil.rmtree(path)
        with mock.patch.object(reset, 'remove', side_effect=AssertionError('不重复执行')):
            result = self.run_exit(task, mode='verify')
        self.assertTrue(result['cleanup_result']['released'])

    def test_failure_of_one_object_does_not_stop_other_objects(self):
        task = self.ready(); first = self.generated(); second = self.generated('node_modules')
        remove = reset.remove
        def fail(base, entry):
            if entry['relative'] == 'target': raise OSError('denied')
            return remove(base, entry)
        with mock.patch.object(reset, 'remove', side_effect=fail): result = self.run_exit(task)
        self.assertTrue(first.exists()); self.assertFalse(second.exists()); self.assertTrue(result['cleanup_result']['released'])

    def test_completed_object_is_not_deleted_again_after_interruption(self):
        task = self.ready(); first = self.generated(); second = self.generated('node_modules')
        remove = reset.remove
        def interrupt(base, entry):
            if entry['relative'] == 'target': raise KeyboardInterrupt()
            return remove(base, entry)
        with mock.patch.object(reset, 'remove', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt): self.run_exit(task)
        self.assertFalse(second.exists()); second.mkdir(); (second / 'late.py').write_text('new source')
        result = self.run_exit(task)
        self.assertTrue((second / 'late.py').exists()); self.assertFalse(first.exists())
        self.assertTrue(result['cleanup_result']['released'])

    def test_unbind_interruption_recovers_without_redeleting(self):
        task = self.ready(); path = self.generated()
        cas = task_store.compare_and_set
        def interrupted(*args):
            cas(*args); raise KeyboardInterrupt()
        with mock.patch.object(task_store, 'compare_and_set', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt): self.run_exit(task)
        self.assertIsNone(task_store.read_task(self.ws)); path.mkdir(); (path / 'late').write_text('keep')
        result = self.run_exit(task)
        self.assertTrue((path / 'late').exists()); self.assertEqual('done', result['status'])

    def test_runtime_residue_precedes_takeover_operation(self):
        task = self.ready(); self.run_exit(task)
        (self.ws / 'runtime/unknown').write_text('keep')
        before = operations.read(self.ws)['operation_id']
        with self.assertRaisesRegex(ValueError, 'runtime'):
            station.takeover(self.ws, self.request, 'op-next-takeover', task_store.read_current(self.ws)['revision'])
        self.assertEqual(before, operations.read(self.ws)['operation_id'])

    def test_new_takeover_after_clean_release(self):
        task = self.ready(); self.generated(); self.run_exit(task)
        request = dict(self.request, issue_key='TAP-124')
        result = station.takeover(self.ws, request, 'op-next-takeover', task_store.read_current(self.ws)['revision'])
        self.assertEqual('done', result['status'])

    def test_old_request_rejected_before_writes(self):
        task = self.ready(); before = operations.read(self.ws)
        with self.assertRaisesRegex(ValueError, '旧清理'):
            self.run_exit(task, options={'cleanup_version': 6})
        self.assertEqual(before, operations.read(self.ws))

    def test_script_cli_executes_without_handwritten_plan(self):
        task = self.ready(); path = self.generated()
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        p = subprocess.run([sys.executable, str(product_cli(self.ws, 'workflow/station-clean.py')), '--dir', str(self.ws), '--execute'], capture_output=True, text=True, env=env)
        self.assertEqual(0, p.returncode, p.stderr); self.assertFalse(path.exists())
        self.assertTrue(json.loads(p.stdout)['cleanup']['released'])


    def test_preservation_registration_overrides_generated_directory(self):
        task = self.ready(); path = self.generated()
        resources.register(self.ws, task['issue_key'], task['run_id'], [{'kind': 'source-disposition', 'producer': 'user', 'path': 'source/tapdata/tapdata/target/output.bin', 'preservation': {'action': 'archive'}}])
        self.run_exit(task)
        self.assertTrue(path.exists())
        result = clean.execute(self.ws, 'work-clean', None, 'run-' + 'a' * 32, task_store.read_current(self.ws)['revision'], 'op-idle-cleanup', {})
        self.assertTrue(path.exists())
        self.assertIn('source/tapdata/tapdata/target', result['cleanup_result']['retained'])

    def test_manifest_changed_after_prepare_does_not_use_old_delete_authority(self):
        task = self.ready(); path = self.generated()
        self.run_exit(task, mode='prepare')
        project = self.product / 'projects/tapdata/station-clean.json'
        value = json.loads(project.read_text())
        value['rules'].append({'scope': 'repository', 'pattern': '**/target', 'type': 'directory', 'action': 'preserve'})
        self.write(project, value)
        result = self.run_exit(task)
        self.assertTrue(path.exists()); self.assertTrue(result['cleanup_result']['released'])
        self.assertFalse(result['cleanup_result']['complete'])

    def test_plan_identity_drift_never_deletes_replacement(self):
        task = self.ready(); path = self.generated()
        self.run_exit(task, mode='prepare')
        path.rename(self.repo / 'old-target'); path.mkdir(); (path / 'new').write_text('keep')
        result = self.run_exit(task)
        self.assertTrue((path / 'new').exists()); self.assertTrue(result['cleanup_result']['released'])

    def test_unknown_source_tree_is_reported_and_preserved(self):
        task = self.ready(); path = self.ws / 'source/notes'; path.mkdir(); (path / 'note').write_text('keep')
        result = self.run_exit(task)
        self.assertIn('source/notes', [r['path'] for r in result['cleanup_result']['residuals']])
        self.assertTrue((path / 'note').exists())

    def test_active_state_error_does_not_claim_unbound(self):
        task = self.ready(); (self.ws / '.agenticops/authorization.json').symlink_to(self.root / 'outside')
        with self.assertRaisesRegex(ValueError, '链接'): self.run_exit(task)
        self.assertIsNotNone(task_store.read_task(self.ws))
        self.assertFalse(operations.read(self.ws)['cleanup_result']['released'])

    def test_writer_failure_retains_affected_objects_but_releases(self):
        task = self.ready(); path = self.generated(); other = self.ws / 'runtime/tmp'; other.mkdir()
        pending = {'resource': {'kind': 'process', 'cwd': str(path), 'pid': 42, 'started_at': 'old'}, 'reason': 'writer alive'}
        with mock.patch.object(clean, 'stop_resources', return_value=[pending]): result = self.run_exit(task)
        self.assertTrue(path.exists()); self.assertFalse(other.exists()); self.assertTrue(result['cleanup_result']['released'])
        previous = (self.ws / '.agenticops/operation.json').read_bytes()
        with mock.patch.object(resources.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'old', '')):
            with self.assertRaisesRegex(ValueError, '写入者'): station.takeover(self.ws, self.request, 'op-next', task_store.read_current(self.ws)['revision'])
        self.assertEqual(previous, (self.ws / '.agenticops/operation.json').read_bytes())

    def test_external_leftover_can_be_read_back_after_unbinding(self):
        task = self.ready()
        entry = {'kind': 'external', 'id': 'env-one', 'producer': 'test', 'resource_type': 'service', 'action': 'delete', 'status': 'unknown', 'before': {'protected': False}}
        resources.register(self.ws, task['issue_key'], task['run_id'], [entry])
        self.run_exit(task)
        with self.assertRaises(ValueError): resources.check_retained_resources(self.ws, operations.read(self.ws))
        resources.register(self.ws, task['issue_key'], task['run_id'], [dict(entry, status='cleaned', readback_ref='observed:absent')], 'op-cleanup-test')
        resources.check_retained_resources(self.ws, operations.read(self.ws))

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
        self.assertTrue(root.exists()); self.assertTrue(result['cleanup_result']['released'])
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

    def test_verify_result_does_not_mutate_docker(self):
        task = self.ready(); self.real_run = subprocess.run
        root, calls, run = self.docker_fixture()
        self.run_exit(task, mode='prepare')
        with mock.patch.object(resources.subprocess, 'run', side_effect=run): result = self.run_exit(task, mode='verify')
        self.assertTrue(root.exists()); self.assertTrue(result['cleanup_result']['released'])
        self.assertFalse(any('stop' in c or 'rm' in c for c in calls))


if __name__ == '__main__':
    unittest.main()

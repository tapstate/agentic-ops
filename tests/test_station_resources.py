#!/usr/bin/env python3
"""版本 6 目录重置、源码成果和恢复边界的真实 Git 回归。"""
import json
import io
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import test_station_lifecycle as lifecycle
from workflow import station, station_resources as resources, station_directories as directories, task as task_cli
from workflow import archive_store, station_artifacts as artifacts, station_operation as operations, station_archive, task_store


class ResourceTests(unittest.TestCase):
    setUp = lifecycle.StationTests.setUp
    prepare_engineering = lifecycle.StationTests.prepare_engineering
    write = lifecycle.StationTests.write
    git = lifecycle.StationTests.git
    takeover = lifecycle.StationTests.takeover

    def ready(self):
        task = self.takeover()
        self.name = 'tapdata/tapdata'
        self.repo = self.ws / 'source' / self.name
        station.scope_change(self.ws, task['issue_key'], task['run_id'], task['_revision'], 'op-resource-scope', self.name, None, 'develop', ['file.txt', 'new.bin'], 'unit')
        task = task_store.read_task(self.ws)
        self.branch = task['task_repositories'][self.name]['work_branch']
        return task

    def reset_request(self, task):
        return dict(cleanup_version=6, abandon_changes=True, summary='保存成果并重置', reason='用户取消', decision_ref='fixture:user', confirmed_digest=resources.plan(self.ws, task)['digest'])

    def execute(self, task, request, kind='clean', op='op-resource-reset'):
        return station.execute(self.ws, kind, task['issue_key'], task['run_id'], task['_revision'], op, request)

    def register_root(self, task, path='target'):
        resources.register(self.ws, task['issue_key'], task['run_id'], [{'kind':'directory', 'producer':'maven', 'path':'source/'+self.name+'/'+path}])
        return directories.load(self.ws, task)['source/'+self.name+'/'+path]

    def discard_for_test(self, task, plan):
        operation = dict(operations.read(self.ws), cleanup_plan=plan)
        operation['steps'] = dict(operation['steps'])
        with mock.patch.object(operations, 'save'):
            for name in plan['rules']['batches']:
                resources.discard_batch(self.ws, task, operation, name, complete=False)

    def gitlink_ready(self):
        self.prepare_engineering()
        sha = self.git(self.seed, 'rev-parse', 'HEAD')
        self.git(self.seed, 'update-index', '--add', '--cacheinfo', '160000,' + sha + ',vendor/api')
        self.git(self.seed, 'commit', '-m', 'gitlink baseline')
        self.git(self.seed, 'push', str(self.remote), 'develop')
        return self.ready()

    def test_empty_gitlink_survives_clean(self):
        task = self.gitlink_ready()
        before = self.git(self.repo, 'ls-files', '--stage')
        self.git(self.repo, 'config', 'submodule.recurse', 'true')
        self.execute(task, self.reset_request(task))
        self.assertIsNone(task_store.read_task(self.ws))
        self.assertEqual(before, self.git(self.repo, 'ls-files', '--stage'))
        self.assertTrue((self.repo / 'vendor/api').is_dir())
        self.assertEqual([], list((self.repo / 'vendor/api').iterdir()))

    def test_gitlink_unsafe_states_are_rejected(self):
        task = self.gitlink_ready()
        path = self.repo / 'vendor/api'
        for name in ('data', '.git'):
            with self.subTest(name=name):
                (path / name).write_text('keep')
                with self.assertRaisesRegex(ValueError, 'submodule'):
                    resources.plan(self.ws, task)
                (path / name).unlink()
        path.rmdir()
        artifacts.verify_special_entries(self.repo)
        path.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, '符号链接'):
            resources.plan(self.ws, task)
        path.unlink()
        path.mkdir()
        self.git(self.repo, 'update-index', '--cacheinfo', '160000,' + self.git(self.repo, 'rev-parse', 'HEAD') + ',vendor/api')
        with self.assertRaisesRegex(ValueError, '不一致'):
            resources.plan(self.ws, task)
        self.git(self.repo, 'restore', '--staged', 'vendor/api')
        with self.assertRaisesRegex(ValueError, '不一致'):
            artifacts.verify_special_entries(self.repo, 'HEAD^')

    def test_gitlink_contents_appearing_before_neutral_are_preserved(self):
        task = self.gitlink_ready()
        request = self.reset_request(task)
        original = resources.neutral
        def late_content(*args, **kwargs):
            (self.repo / 'vendor/api/late').write_text('keep')
            return original(*args, **kwargs)
        with mock.patch.object(resources, 'neutral', side_effect=late_content):
            with self.assertRaisesRegex(ValueError, 'submodule'):
                self.execute(task, request)
        self.assertEqual('keep', (self.repo / 'vendor/api/late').read_text())
        self.assertIsNotNone(task_store.read_task(self.ws))

    def test_runtime_generated_contents_need_no_file_registration(self):
        task = self.ready()
        (self.ws/'runtime/direct-output').write_text('generated')
        request = self.reset_request(task)
        (self.ws/'runtime/stop.log').write_text('written during stop')
        self.execute(task, request)
        self.assertEqual(list((self.ws/'runtime').iterdir()), [])
        self.assertIsNone(task_store.read_task(self.ws))
        entry = task['engineering_baseline']['repositories'][self.name]
        self.assertEqual(self.git(self.repo, 'branch', '--show-current'), 'develop')
        self.assertEqual(self.git(self.repo, 'rev-parse', self.branch), task['reset_baseline'][self.name]['sha'])

    def test_runtime_child_is_lazy_and_rejects_links(self):
        task = self.ready()
        child = directories.runtime_child(self.ws, task, 'maven-local')
        self.assertEqual(child, (self.ws / 'runtime/maven-local').resolve())
        self.assertFalse(child.exists())
        with self.assertRaisesRegex(ValueError, '名称'):
            directories.runtime_child(self.ws, task, '../maven-local')
        outside = self.root / 'outside-runtime-child'
        outside.mkdir()
        child.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, '符号链接'):
            directories.runtime_child(self.ws, task, 'maven-local')

    def test_runtime_path_binds_current_run_and_rejects_terminal_task(self):
        task = self.ready()
        args = SimpleNamespace(dir=self.ws, issue_key=task['issue_key'], expected_run_id=task['run_id'], name='maven-local')
        with mock.patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(0, task_cli.cmd_runtime_path(args))
        context = json.loads(output.getvalue())
        self.assertEqual(task['run_id'], context['run_id'])
        self.assertEqual(str((self.ws / 'runtime/maven-local').resolve()), context['local_repository'])
        current = task_store.read_task(self.ws)
        current['outcome'] = 'completed'
        task_store.write_task(self.ws, current)
        with self.assertRaisesRegex(ValueError, '非进行中'):
            task_cli.cmd_runtime_path(args)

    def test_directory_created_before_producer_and_nonempty_not_adopted(self):
        task = self.ready()
        entry = self.register_root(task)
        self.assertEqual(directories.identity(self.repo/'target'), entry['identity'])
        (self.repo/'target/out').write_text('build')
        self.execute(task, self.reset_request(task))
        self.assertFalse((self.repo/'target').exists())

    def test_nonempty_unregistered_directory_is_not_adopted(self):
        task = self.ready()
        (self.repo/'target').mkdir()
        (self.repo/'target/unknown').write_text('keep')
        with self.assertRaisesRegex(ValueError, '空目录'):
            self.register_root(task)
        self.assertTrue((self.repo/'target/unknown').exists())

    def test_undeclared_and_tracked_roots_rejected(self):
        task = self.ready()
        with self.assertRaisesRegex(ValueError, 'Project'):
            self.register_root(task, 'other')
        (self.repo/'target').mkdir()
        (self.repo/'target/tracked').write_text('code')
        self.git(self.repo,'add','target')
        with self.assertRaisesRegex(ValueError, '已跟踪'):
            self.register_root(task)

    def test_links_inside_runtime_are_unlinked_without_following(self):
        task = self.ready()
        outside = self.root/'keep'
        outside.mkdir(); (outside/'sentinel').write_text('keep')
        (self.ws/'runtime/link').symlink_to(outside, target_is_directory=True)
        self.execute(task,self.reset_request(task))
        self.assertEqual((outside/'sentinel').read_text(),'keep')

    def test_special_file_stops_before_runtime_deletion(self):
        task = self.ready()
        os.mkfifo(self.ws/'runtime/fifo')
        (self.ws/'runtime/keep').write_text('keep')
        with self.assertRaisesRegex(ValueError,'特殊'):
            self.execute(task,self.reset_request(task))
        self.assertTrue((self.ws/'runtime/keep').exists())
        self.assertIsNotNone(task_store.read_task(self.ws))

    def test_nested_git_and_root_replacement_rejected(self):
        task = self.ready()
        request = self.reset_request(task)
        root = self.ws/'runtime'
        root.rename(self.ws/'runtime-old'); root.mkdir()
        with self.assertRaisesRegex(ValueError,'身份|未知材料|确认'):
            self.execute(task,request)
        self.assertTrue((self.ws/'runtime-old').exists())

    def test_unknown_source_object_blocks_without_deletion(self):
        task = self.ready()
        (self.ws/'source/unknown').write_text('keep')
        with self.assertRaisesRegex(ValueError,'未知'):
            resources.plan(self.ws,task)
        self.assertEqual((self.ws/'source/unknown').read_text(),'keep')

    def test_metadata_is_discarded_without_archiving_or_preflight_deletion(self):
        task = self.ready()
        paths = [self.ws / '.DS_Store', self.ws / 'source/.DS_Store',
                 self.ws / 'source/tapdata/.DS_Store', self.repo / '.DS_Store']
        (self.repo / '.git/info/exclude').write_text('.DS_Store\n')
        keep = self.ws / 'config/.DS_Store'
        keep.write_text('retained')
        for path in paths:
            path.write_text('metadata')
        plan = resources.plan(self.ws, task)
        self.assertFalse(any(e['file'] == '.DS_Store' for e in plan['entries']))
        self.assertTrue(all(path.exists() for path in paths))
        self.execute(task, self.reset_request(task))
        self.assertTrue(all(not path.exists() for path in paths))
        self.assertEqual(keep.read_text(), 'retained')
        saved = json.loads((archive_store.run_directory(self.ws, task['run_id']) / 'source-artifacts.json').read_text())
        self.assertNotIn('.DS_Store', saved['repositories'][self.name]['untracked'])

    def test_metadata_appearing_after_confirmation_is_cleaned(self):
        task = self.ready()
        request = self.reset_request(task)
        original = resources.discard_batch
        def late_metadata(base, task, operation, name, **kwargs):
            if not kwargs.get('verify_only') and not kwargs.get('complete', True):
                (self.repo / '.DS_Store').write_text('regenerated')
                (self.ws / '.DS_Store').write_text('regenerated')
            return original(base, task, operation, name, **kwargs)
        with mock.patch.object(resources, 'discard_batch', side_effect=late_metadata):
            self.execute(task, request)
        self.assertFalse((self.repo / '.DS_Store').exists())
        self.assertFalse((self.ws / '.DS_Store').exists())

    def test_metadata_old_plan_preservation_and_nonregular_boundaries(self):
        task = self.ready()
        path = self.repo / '.DS_Store'
        path.write_text('old archive')
        old = artifacts.snapshot(self.ws, self.name, {}, {}, include_ignored=True)
        plan = resources.plan(self.ws, task)
        plan['entries'] = old['entries']
        self.discard_for_test(task, plan)
        self.assertEqual(path.read_text(), 'old archive')
        # 原显式保全选择继续进入新计划，旧状态无需迁移。
        explicit = {'source/' + self.name + '/.DS_Store': {'action': 'archive'}}
        self.assertEqual(resources.plan(self.ws, task, decisions_override=explicit)['entries'][0]['preservation']['action'], 'archive')
        path.unlink()
        outside = self.root / 'outside'
        outside.write_text('keep')
        path.symlink_to(outside)
        plan = resources.plan(self.ws, task)
        self.assertEqual(plan['entries'][0]['preservation']['action'], 'archive')
        self.discard_for_test(task, plan)
        self.assertTrue(path.is_symlink())
        self.assertEqual(outside.read_text(), 'keep')
        root_file = self.ws / '.DS_Store'
        root_file.mkdir()
        with self.assertRaisesRegex(ValueError, '未知'):
            resources.plan(self.ws, task)

    def test_old_metadata_archive_plan_still_executes(self):
        task = self.ready()
        path = self.repo / '.DS_Store'
        path.write_text('old plan bytes')
        original = artifacts.snapshot
        def old_snapshot(*args, **kwargs):
            kwargs['rules'] = None
            return original(*args, **kwargs)
        with mock.patch.object(artifacts, 'snapshot', side_effect=old_snapshot):
            self.execute(task, self.reset_request(task))
        saved = json.loads((archive_store.run_directory(self.ws, task['run_id']) / 'source-artifacts.json').read_text())
        self.assertIn('.DS_Store', saved['repositories'][self.name]['untracked'])
        self.assertFalse(path.exists())
        artifacts.verify_reconstruction(self.repo, saved['repositories'][self.name])

    def test_metadata_cleanup_preserves_aborted_replan_fingerprint(self):
        task = self.ready()
        name = 'tapdata/tapdata-common-lib'
        repo = self.ws / 'source' / name
        self.git(self.root, 'clone', str(self.remote), str(repo))
        kept = repo / '.DS_Store'
        kept.write_bytes(b'preserved metadata')
        from workflow.quality import git_revision
        frozen = git_revision(repo)
        task['replan_preserved'] = {name: {'origin': str(self.remote), 'fingerprint': frozen}}
        task['retained_repositories'][name] = {
            'head': self.git(repo, 'rev-parse', 'HEAD'),
            'branch': self.git(repo, 'branch', '--show-current')}
        task_store.write_task(self.ws, task)
        task = task_store.read_task(self.ws)
        disposable = self.repo / '.DS_Store'
        disposable.write_bytes(b'current task metadata')
        self.execute(task, self.reset_request(task))
        self.assertIsNone(task_store.read_task(self.ws))
        self.assertFalse(disposable.exists())
        self.assertEqual(kept.read_bytes(), b'preserved metadata')
        self.assertEqual(git_revision(repo), frozen)

    def test_metadata_cleanup_is_controlled_by_existing_policy(self):
        task = self.ready()
        policy_path = self.product / 'policies/station-clean.json'
        policy = json.loads(policy_path.read_text())
        policy['rules'] = [row for row in policy['rules'] if row['pattern'] != '.DS_Store']
        self.write(policy_path, policy)
        path = self.repo / '.DS_Store'
        path.write_text('keep without policy')
        plan = resources.plan(self.ws, task)
        self.assertEqual(plan['entries'][0]['preservation']['action'], 'archive')
        self.discard_for_test(task, plan)
        self.assertTrue(path.exists())
        (self.ws / '.DS_Store').write_text('unknown')
        with self.assertRaisesRegex(ValueError, '未知'):
            resources.plan(self.ws, task)

    def test_tracked_metadata_uses_normal_source_preservation(self):
        task = self.ready()
        path = self.repo / '.DS_Store'
        path.write_text('tracked')
        self.git(self.repo, 'add', '.DS_Store')
        plan = resources.plan(self.ws, task)
        self.assertEqual(plan['entries'][0]['action'], 'restore')
        self.assertEqual(plan['entries'][0]['preservation']['action'], 'archive')
        self.discard_for_test(task, plan)
        self.assertEqual(path.read_text(), 'tracked')

    def configure_rules(self, rows):
        self.write(self.product / 'projects/tapdata/station-clean.json', {'version': 2, 'rules': rows})

    def test_generic_discard_names_are_consumed_without_filename_adapters(self):
        task = self.ready()
        self.configure_rules([dict(scope=scope, pattern='temporary.cache', type='file', action='discard')
                              for scope in ('station-root', 'source-layout', 'repository')])
        paths = [self.ws/'temporary.cache', self.ws/'source/temporary.cache',
                 self.ws/'source/tapdata/temporary.cache', self.repo/'temporary.cache']
        retained = self.ws/'config/temporary.cache'
        retained.write_text('configuration')
        for path in paths:
            path.write_text('generated')
        self.execute(task, self.reset_request(task))
        self.assertFalse(any(p.exists() for p in paths))
        self.assertEqual('configuration', retained.read_text())

    def test_project_archive_overrides_central_discard_without_retaining_dirty_source(self):
        task = self.ready()
        self.configure_rules([dict(scope='repository', pattern='.DS_Store', type='file', action='archive')])
        (self.repo/'.DS_Store').write_text('valuable')
        plan = resources.plan(self.ws, task)
        self.assertEqual('archive', plan['entries'][0]['preservation']['action'])
        self.execute(task, self.reset_request(task))
        saved = json.loads((archive_store.run_directory(self.ws, task['run_id'])/'source-artifacts.json').read_text())
        self.assertIn('.DS_Store', saved['repositories'][self.name]['untracked'])
        self.assertFalse((self.repo/'.DS_Store').exists())

    def test_preserve_does_not_hide_initialized_wiring_drift(self):
        task = self.ready()
        import hashlib
        wired = self.ws/'.agents/skills/example'
        wired.parent.mkdir(parents=True)
        wired.write_text('managed')
        init_path = self.ws/'.agenticops/init.json'
        init = json.loads(init_path.read_text())
        init['artifacts'] = [{'path': '.agents/skills/example', 'sha256': hashlib.sha256(b'managed').hexdigest()}]
        self.write(init_path, init)
        self.configure_rules([dict(scope='station-root', pattern='.agents', type='directory', action='preserve')])
        resources.verify_station_inventory(self.ws)
        wired.write_text('changed')
        with self.assertRaisesRegex(ValueError, '初始化接线'):
            resources.plan(self.ws, task)
        self.assertEqual('changed', wired.read_text())

    def test_completed_repository_is_not_reopened_when_another_repository_failed(self):
        self.prepare_engineering(2)
        task = self.ready()
        names = list(task['engineering_baseline']['repositories'])
        other = next(name for name in names if name != self.name)
        from workflow import station_reset_result
        (self.repo/'.DS_Store').write_text('first')
        request = self.reset_request(task)
        original = station_reset_result._apply_repository
        def fail_second(base, task, operation, name, entry):
            if name == other:
                raise OSError('second repository interrupted')
            return original(base, task, operation, name, entry)
        with mock.patch.object(station_reset_result, '_apply_repository', side_effect=fail_second):
            with self.assertRaisesRegex(OSError, 'second repository'):
                self.execute(task, request)
        late = self.repo/'.DS_Store'
        late.write_text('must survive recovery')
        with self.assertRaises(ValueError):
            self.execute(task, request)
        self.assertEqual('must survive recovery', late.read_text())
        self.assertIsNotNone(task_store.read_task(self.ws))

    def test_completed_root_batch_rejects_regenerated_file_after_resource_failure(self):
        task = self.ready()
        request = self.reset_request(task)
        with mock.patch.object(directories, 'reset', side_effect=OSError('directory interrupted')):
            with self.assertRaisesRegex(OSError, 'directory interrupted'):
                self.execute(task, request)
        late = self.ws/'.DS_Store'
        late.write_text('keep after completed root batch')
        with self.assertRaisesRegex(ValueError, '已完成清理对象'):
            self.execute(task, request)
        self.assertEqual('keep after completed root batch', late.read_text())

    def test_frozen_batch_does_not_scan_added_catalog_repository(self):
        task = self.ready()
        plan = resources.plan(self.ws, task)
        other = 'tapdata/tapdata-common-lib'
        repo = self.ws/'source'/other
        self.git(self.root, 'clone', str(self.remote), str(repo))
        keep = repo/'.DS_Store'; keep.write_text('outside confirmed set')
        self.assertNotIn('repository:'+other, plan['rules']['batches'])
        self.discard_for_test(task, plan)
        self.assertEqual('outside confirmed set', keep.read_text())
        with self.assertRaisesRegex(ValueError, '持久仓库'):
            self.execute(task, dict(self.reset_request(task), confirmed_digest=plan['digest']))
        self.assertTrue(keep.exists())

    def test_new_directory_is_not_authorized_by_matching_filename(self):
        task = self.ready()
        request = self.reset_request(task)
        path = self.repo/'new-directory/.DS_Store'
        path.parent.mkdir(); path.write_text('keep')
        with self.assertRaisesRegex(ValueError, '摘要|清单|确认'):
            self.execute(task, request)
        self.assertEqual('keep', path.read_text())

    def test_replaced_parent_is_rejected_before_generic_discard(self):
        task = self.ready()
        directory = self.repo/'generated'; directory.mkdir()
        (directory/'.DS_Store').write_text('old')
        plan = resources.plan(self.ws, task)
        directory.rename(self.root/'old-generated')
        directory.mkdir(); (directory/'.DS_Store').write_text('new')
        with self.assertRaisesRegex(ValueError, '身份变化'):
            self.discard_for_test(task, plan)
        self.assertEqual('new', (directory/'.DS_Store').read_text())

    def test_file_becoming_tracked_during_discard_is_preserved(self):
        task = self.ready()
        path = self.repo/'.DS_Store'; path.write_text('keep')
        plan = resources.plan(self.ws, task)
        from workflow import station_source
        original = station_source.untracked_discard
        matched = [False]
        def concurrent_tracking(repository, filename, rules, reset_sha='HEAD'):
            eligible = original(repository, filename, rules, reset_sha)
            if eligible and not matched[0]:
                matched[0] = True
                self.git(self.repo, 'add', '.DS_Store')
            return eligible
        with mock.patch.object(station_source, 'untracked_discard', side_effect=concurrent_tracking):
            with self.assertRaisesRegex(ValueError, '跟踪状态'):
                self.discard_for_test(task, plan)
        self.assertEqual('keep', path.read_text())

    def test_completed_batches_use_their_own_historical_revision(self):
        import copy
        task = self.ready()
        plan = resources.plan(self.ws, task)
        operation = dict(operations.read(self.ws), cleanup_plan=plan, steps={})
        batch = 'station-root'
        old_key = resources.batch_key(operation, batch)
        expected = {'batch': batch, 'boundary_digest': resources.baseline.digest(plan['rules']['batches'][batch]),
                    'plan_digest': plan['digest']}
        operation['steps'][old_key] = {'before': {}, 'expected': expected, 'receipt': expected}
        new = copy.deepcopy(plan)
        new['retained'].append('new confirmed observation')
        new['digest'] = resources.baseline.digest({k:v for k,v in new.items() if k != 'digest'})
        operation['plan_revisions'] = [{'plan': plan}]
        operation['cleanup_plan'] = new
        new_expected = dict(expected, plan_digest=new['digest'])
        operation['steps'][resources.batch_key(operation, batch)] = {'before': {}, 'expected': new_expected, 'receipt': new_expected}
        before = copy.deepcopy(operation)
        resources.verify_completed_batches(self.ws, task, operation)
        self.assertEqual(before, operation)

    def test_structure_contract_paths_are_shared_by_takeover_preservation_and_reset(self):
        layout_path = self.product/'contracts/station-layout.json'
        layout = json.loads(layout_path.read_text())
        mapping = {'runtime': 'execution'}
        for row in layout['roots']:
            if row['path'] in mapping:
                old = row['path']; row['path'] = mapping[old]
                (self.ws/old).rename(self.ws/row['path'])
        self.write(layout_path, layout)
        policy_path = self.product/'policies/station-clean.json'
        policy = json.loads(policy_path.read_text())
        for row in policy['rules']:
            if row['scope'] == 'station-root' and row['pattern'] in mapping:
                row['pattern'] = mapping[row['pattern']]
        self.write(policy_path, policy)
        task = self.takeover()
        self.name = next(iter(task['engineering_baseline']['repositories']))
        self.repo = self.ws/'source'/self.name
        report = self.repo/'report.txt'; report.write_text('retain in archive')
        disposable = self.repo/'.DS_Store'; disposable.write_text('discard')
        self.execute(task, self.reset_request(task))
        self.assertFalse(disposable.exists())
        self.assertFalse(report.exists())
        self.assertEqual([], list((self.ws/'execution').iterdir()))
        self.assertFalse((self.ws/'runtime').exists())
        saved = json.loads((archive_store.run_directory(self.ws, task['run_id'])/'source-artifacts.json').read_text())
        self.assertIn('report.txt', saved['repositories'][self.name]['untracked'])

    def test_ignored_file_requires_preservation_in_confirmed_plan(self):
        task = self.ready()
        (self.repo/'.git/info/exclude').write_text('ignored\n')
        (self.repo/'ignored').write_text('keep')
        plan = resources.plan(self.ws,task)
        entry = next(e for e in plan['entries'] if e['file'] == 'ignored')
        self.assertEqual(entry['preservation']['action'], 'archive')
        self.assertEqual((self.repo/'ignored').read_text(), 'keep')

    def test_archive_reconstructs_staged_unstaged_binary_new_and_deleted(self):
        task = self.ready()
        (self.repo/'file.txt').write_text('staged\n'); self.git(self.repo,'add','file.txt')
        (self.repo/'file.txt').write_text('unstaged\n')
        (self.repo/'new.bin').write_bytes(b'\x00\xffhello')
        request = self.reset_request(task)
        self.execute(task,request)
        archive = archive_store.run_directory(self.ws, task['run_id'])
        value = json.loads((archive/'source-artifacts.json').read_text())
        artifacts.verify_reconstruction(self.repo,value['repositories'][self.name])
        self.assertEqual((self.repo/'file.txt').read_text(),'baseline\n')
        self.assertFalse((self.repo/'new.bin').exists())

    def test_staged_new_file_is_saved_and_removed(self):
        task = self.ready()
        (self.repo/'new.bin').write_bytes(b'\x00\xffhello')
        self.git(self.repo,'add','new.bin')
        self.execute(task,self.reset_request(task))
        self.assertFalse((self.repo/'new.bin').exists())

    def test_source_change_after_confirmation_is_not_discarded(self):
        task = self.ready(); (self.repo/'file.txt').write_text('first')
        request = self.reset_request(task); (self.repo/'file.txt').write_text('second')
        with self.assertRaisesRegex(ValueError,'确认'):
            self.execute(task,request)
        self.assertEqual((self.repo/'file.txt').read_text(),'second')

    def test_source_archive_failure_keeps_code_and_runtime(self):
        task = self.ready(); (self.repo/'file.txt').write_text('keep')
        request = self.reset_request(task)
        with mock.patch.object(artifacts,'MAX_FILE_BYTES',1), self.assertRaisesRegex(ValueError,'上限'):
            self.execute(task,request)
        self.assertEqual((self.repo/'file.txt').read_text(),'keep')
        self.assertIsNotNone(task_store.read_task(self.ws))

    def test_discard_requires_explicit_content_bound_confirmation(self):
        task = self.ready(); (self.repo/'file.txt').write_text('discard')
        entry = resources.plan(self.ws,task)['entries'][0]
        proof = {k:entry[k] for k in ('head','before','before_index','index_patch','worktree_patch')}
        resources.register(self.ws,task['issue_key'],task['run_id'],[{'kind':'source-disposition','producer':'user','path':entry['path'],'preservation':{'action':'discard','snapshot':proof}}])
        request = self.reset_request(task)
        with self.assertRaisesRegex(ValueError,'额外确认'):
            self.execute(task,request)
        request['discard_digest'] = resources.baseline.digest(resources.plan(self.ws,task)['entries'])
        self.execute(task,request)
        self.assertEqual((self.repo/'file.txt').read_text(),'baseline\n')

    def test_retained_remote_branch_needs_no_network_and_no_delete(self):
        task = self.ready()
        resources.register(self.ws,task['issue_key'],task['run_id'],[{'kind':'external','producer':'git','id':'github:branch:'+self.branch,'resource_type':'git-branch','action':'retain','status':'observed','before':{'repository':self.name,'ref':'refs/heads/'+self.branch,'sha':self.git(self.repo,'rev-parse','HEAD')}}])
        request = self.reset_request(task)
        self.execute(task,request)
        self.assertEqual(self.git(self.repo,'branch','--list',self.branch).strip(),self.branch)

    def test_unknown_remote_write_blocks_and_is_not_relabelled_retain(self):
        task = self.ready()
        resources.register(self.ws,task['issue_key'],task['run_id'],[{'kind':'external','producer':'git','id':'github:branch:'+self.branch,'resource_type':'git-branch','status':'unknown','before':{'repository':self.name,'ref':'refs/heads/'+self.branch,'sha':self.git(self.repo,'rev-parse','HEAD')}}])
        with self.assertRaisesRegex(ValueError,'未知'):
            self.execute(task,self.reset_request(task))
        self.assertIsNotNone(task_store.read_task(self.ws))

    def interrupt_staged_new_file_reset(self):
        task = self.ready()
        path = self.repo/'new.bin'
        path.write_text('saved original')
        self.git(self.repo,'add','new.bin')
        request = self.reset_request(task)
        original = resources.source.git
        def crash(repository, *args, **kwargs):
            result = original(repository, *args, **kwargs)
            if args[:3] == ('--literal-pathspecs', 'rm', '--cached'):
                raise OSError('crash after index removal')
            return result
        with mock.patch.object(resources.source, 'git', side_effect=crash), self.assertRaises(OSError):
            self.execute(task, request)
        return task, request, path

    def test_staged_new_reset_preserves_changed_untracked_file_after_crash(self):
        task, request, path = self.interrupt_staged_new_file_reset()
        path.write_text('unarchived new work')
        with self.assertRaisesRegex(ValueError, '变化'):
            self.execute(task, request)
        self.assertEqual(path.read_text(), 'unarchived new work')
        self.assertIsNotNone(task_store.read_task(self.ws))

    def test_staged_new_reset_resumes_unchanged_untracked_file_after_crash(self):
        task, request, path = self.interrupt_staged_new_file_reset()
        self.execute(task, request)
        self.assertFalse(path.exists())
        self.assertIsNone(task_store.read_task(self.ws))

    def reset_amend_after_neutral(self, kind):
        task = self.ready()
        (self.repo/'file.txt').write_text('task commit')
        self.git(self.repo,'-c','user.name=Test','-c','user.email=test@example.com','commit','-am','task')
        head = self.git(self.repo,'rev-parse','HEAD')
        if kind == 'release':
            observed = station.source.inspect(self.ws, task['engineering_baseline'])
            task.update(stage='completed',outcome='completed',terminal_proof={
                'run_id':task['run_id'],'repositories':observed,'deliveries':[],
                'dispositions':{self.name:'merged'},'candidate_digest':resources.baseline.digest(observed)})
            task_store.write_task(self.ws,task)
            task = task_store.read_task(self.ws)
        request = self.reset_request(task)
        if kind == 'release':
            request['candidate_digest'] = task['terminal_proof']['candidate_digest']
        with mock.patch.object(station,'_clear_active',side_effect=OSError('after neutral')), self.assertRaises(OSError):
            self.execute(task, request, kind=kind)
        (self.ws/'runtime/late').write_text('late generated output')
        current = task_store.read_task(self.ws)
        previous = operations.read(self.ws)
        plan = resources.plan(self.ws,current)
        self.assertNotEqual(plan['source'][self.name]['head'],head)
        self.assertEqual(plan['source'][self.name]['preserved_head'],head)
        with self.assertRaisesRegex(ValueError, '再次产生'):
            station.amend_cleanup(self.ws,task['issue_key'],task['run_id'],current['_revision'],previous['operation_id'],
                previous['cleanup_plan']['digest'],{'confirmed_digest':plan['digest'],'expected_plan_revision':0,'decision_ref':'fixture:user'})
        # 新生成物须独立保全处置，不能用补充计划重删已完成目录。
        (self.ws/'runtime/late').rename(self.root/'preserved-late-output')
        plan = resources.plan(self.ws,current)
        station.amend_cleanup(self.ws,task['issue_key'],task['run_id'],current['_revision'],previous['operation_id'],
            previous['cleanup_plan']['digest'],{'confirmed_digest':plan['digest'],'expected_plan_revision':0,'decision_ref':'fixture:user'})
        self.execute(task,request,kind=kind)
        self.assertIsNone(task_store.read_task(self.ws))
        self.assertEqual(list((self.ws/'runtime').iterdir()),[])
        self.assertEqual(self.git(self.repo,'rev-parse',plan['source'][self.name]['preserved_ref']),head)
        self.assertEqual(self.git(self.repo,'rev-parse',self.branch),head)
        self.assertEqual(self.git(self.repo,'rev-parse','HEAD'),task['reset_baseline'][self.name]['sha'])

    def test_clean_amend_after_neutral_keeps_original_source_commit(self):
        self.reset_amend_after_neutral('clean')

    def test_release_amend_after_neutral_keeps_delivery_proof(self):
        self.reset_amend_after_neutral('release')

    def test_partial_directory_delete_resumes_same_intent(self):
        task = self.ready()
        for i in range(5): (self.ws/'runtime'/str(i)).write_text('x')
        request = self.reset_request(task)
        original = directories._walk
        count = [0]
        def fail(fd,device,delete=False):
            if delete and not count[0]:
                count[0] += 1
                os.unlink('0',dir_fd=fd)
                raise OSError('crash')
            return original(fd,device,delete)
        with mock.patch.object(directories,'_walk',side_effect=fail), self.assertRaises(OSError):
            self.execute(task,request)
        self.execute(task,request)
        self.assertIsNone(task_store.read_task(self.ws))

    def test_completed_directory_receipt_rejects_late_content(self):
        task = self.ready()
        request = self.reset_request(task)
        original = resources.clean
        def fail(*args, **kwargs):
            result = original(*args, **kwargs)
            if kwargs.get('directories_only'):
                raise OSError('crash')
            return result
        with mock.patch.object(resources,'clean',side_effect=fail), self.assertRaises(OSError):
            self.execute(task,request)
        (self.ws/'runtime/late').write_text('keep')
        with self.assertRaisesRegex(ValueError,'再次产生'):
            self.execute(task,request)
        self.assertTrue((self.ws/'runtime/late').exists())

    def test_neutral_does_not_move_branch_and_keeps_task_commit(self):
        task = self.ready()
        baseline = task['reset_baseline'][self.name]['sha']
        (self.repo/'file.txt').write_text('committed')
        self.git(self.repo,'-c','user.name=Test','-c','user.email=test@example.com','commit','-am','task')
        head = self.git(self.repo,'rev-parse','HEAD')
        self.execute(task,self.reset_request(task))
        self.assertEqual(self.git(self.repo,'rev-parse','HEAD'),baseline)
        self.assertEqual(self.git(self.repo,'rev-parse',self.branch),head)
        refs = self.git(self.repo,'for-each-ref','--format=%(objectname)','refs/agenticops/archive/')
        self.assertIn(head,refs)

    def test_epoch_three_rejected_before_state_mutation(self):
        task = self.ready()
        path = self.ws/'.agenticops/init.json'
        value=json.loads(path.read_text());value['station_state_epoch']=3;path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'不兼容|代际|原版本'):
            self.execute(task,{'confirmed_digest':'old'})
        self.assertIsNotNone(task_store.read_task(self.ws))

    def test_key_logs_archived_and_late_logs_appended(self):
        from workflow import station_logs
        task = self.ready()
        logs = self.ws/'runtime/logs'; logs.mkdir(exist_ok=True)
        (logs/'build.log').write_text('build passed\n')
        request = self.reset_request(task)
        self.execute(task, request, kind='archive', op='op-logs-archive')
        task = task_store.read_task(self.ws)
        archive = archive_store.from_reference(self.ws, task['archive_ref'])
        saved = json.loads((archive/'runtime-evidence.json').read_text())
        self.assertEqual(saved['files']['runtime/logs/build.log']['text'], 'build passed\n')
        (logs/'build.log').write_text('build passed\nstop completed\n')
        station_logs.verify_current(self.ws, task, resources.plan(self.ws, task))
        attachments = list((archive/'receipts').glob('runtime-evidence-*.json'))
        self.assertEqual(len(attachments), 1)
        self.assertIn('stop completed', attachments[0].read_text())
        self.execute(task, self.reset_request(task))
        self.assertEqual(list((self.ws/'runtime').iterdir()), [])
        self.assertTrue((archive/'runtime-evidence.json').exists())

    def test_invalid_report_prevents_reset(self):
        task = self.ready()
        reports = self.ws/'runtime/reports'; reports.mkdir(exist_ok=True)
        (reports/'binary').write_bytes(b'\xff\xfe')
        with self.assertRaisesRegex(ValueError, 'UTF-8'):
            self.execute(task, self.reset_request(task))
        self.assertTrue((reports/'binary').exists())
        self.assertIsNotNone(task_store.read_task(self.ws))

    def test_generated_root_becoming_tracked_is_archived_before_reset(self):
        task = self.ready()
        self.register_root(task)
        (self.repo/'target/code').write_text('keep')
        self.git(self.repo, 'add', 'target/code')
        plan = resources.plan(self.ws, task)
        entry = next(e for e in plan['entries'] if e['file'] == 'target/code')
        self.assertEqual(entry['preservation']['action'], 'archive')
        self.assertEqual(entry['action'], 'restore')
        self.assertFalse(any(e['kind'] == 'source-generated' for e in plan['directories']))
        self.assertTrue((self.repo/'target/code').exists())

    def test_existing_directory_requires_same_producer(self):
        task = self.ready()
        self.register_root(task)
        with self.assertRaisesRegex(ValueError, '来源'):
            resources.register(self.ws, task['issue_key'], task['run_id'], [{'kind':'directory', 'producer':'other', 'path':'source/'+self.name+'/target'}])

    def test_disposition_receipts_and_new_task_boundary(self):
        from workflow import station_disposition, engineering_baseline
        task = self.ready()
        self.execute(task, self.reset_request(task))
        value = {'disposition_id':'disposition-test-12345678', 'phase':'intent',
                 'resource_type':'git-branch', 'action':'delete', 'object_id':'origin/'+self.branch,
                 'decision_ref':'fixture:user', 'before':{'repository':self.name, 'ref':'refs/heads/'+self.branch,
                 'sha':task['reset_baseline'][self.name]['sha'], 'protected':False}}
        value['confirmed_digest'] = engineering_baseline.digest(value)
        def record(data):
            return station_disposition.record(self.ws, task['issue_key'], task['run_id'], data)
        first = record(value)
        self.assertEqual(first, record(value))
        unknown = {'disposition_id':value['disposition_id'], 'phase':'unknown',
                   'object_id':value['object_id'], 'intent_digest':engineering_baseline.digest(value), 'readback_ref':'fixture:timeout'}
        record(unknown)
        readback = dict(unknown, phase='readback', status='unchanged', readback_ref='fixture:readback')
        record(readback)
        with self.assertRaisesRegex(ValueError, "最终回读"):
            record(unknown)
        with self.assertRaisesRegex(ValueError, '覆盖'):
            record(dict(readback, status='deleted'))
        with mock.patch.object(task_store, 'read_task', return_value={'run_id':'run-other'}), self.assertRaisesRegex(ValueError, '占用'):
            record(value)

    def test_completed_operation_collects_unreferenced_payloads(self):
        task = self.ready()
        (self.repo/'file.txt').write_text('new source')
        self.execute(task, self.reset_request(task))
        operation = json.loads((self.ws/'.agenticops/operation.json').read_text())
        references = operation.get('payload_refs', {})
        self.assertFalse({'archive_artifacts','archive_logs','archive_evidence'} & set(references))
        files = {p.stem for p in (self.ws/'.agenticops/operation-data').iterdir()}
        self.assertEqual(files, set(references.values()))

    def test_external_terminal_receipt_survives_standalone_archive(self):
        task = self.ready()
        external = {'kind':'external','producer':'test','id':'container:test','resource_type':'container',
                    'action':'delete','status':'quiesced','readback_ref':'fixture:stopped', 'before':{'id':'test','protected':False}}
        resources.register(self.ws,task['issue_key'],task['run_id'],[external])
        self.execute(task,self.reset_request(task),kind='archive',op='op-external-archive')
        task = task_store.read_task(self.ws)
        resources.register(self.ws,task['issue_key'],task['run_id'],[dict(external,status='cleaned',readback_ref='fixture:removed')],'op-external-archive')
        self.execute(task,self.reset_request(task))
        receipts = list(archive_store.receipts(self.ws, task['archive_ref']).glob('external-terminal-*.json'))
        self.assertEqual(len(receipts),1)
        result = json.loads(receipts[0].read_text())['resources'][0]
        self.assertEqual(result['status'],'cleaned')
        self.assertEqual(result['readback_ref'],'fixture:removed')

    def test_missing_generated_root_needs_no_legacy_directory_plan(self):
        task = self.ready()
        self.register_root(task)
        (self.repo/'target').rmdir()
        plan = resources.plan(self.ws, task)
        self.assertFalse(any(e['kind']=='source-generated' for e in plan['directories']))
        self.assertNotIn('target', plan['source'][self.name]['directories'])
        self.execute(task, self.reset_request(task))
        self.assertIsNone(task_store.read_task(self.ws))

    def test_root_idea_configuration_is_retained_during_clean(self):
        task = self.ready()
        idea = self.ws / '.idea'
        idea.mkdir()
        config = idea / 'station.xml'
        config.write_text('<project/>')
        identity = config.stat().st_ino
        self.execute(task, self.reset_request(task))
        self.assertIsNone(task_store.read_task(self.ws))
        self.assertEqual('<project/>', config.read_text())
        self.assertEqual(identity, config.stat().st_ino)

    def test_root_idea_exception_rejects_links_files_and_nested_unknowns(self):
        idea = self.ws / '.idea'
        idea.write_text('keep')
        with self.assertRaisesRegex(ValueError, '未知材料'):
            resources.verify_station_inventory(self.ws)
        idea.unlink()
        idea.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, '普通文件或目录'):
            resources.verify_station_inventory(self.ws)
        idea.unlink()
        (self.ws / '.agenticops/.idea').mkdir()
        with self.assertRaisesRegex(ValueError, '未知'):
            resources.verify_station_inventory(self.ws)

    def test_unknown_station_objects_block_reuse(self):
        for relative in ('unknown', '.agenticops/unknown', '.agenticops/operation-data/unknown.json'):
            with self.subTest(path=relative):
                task = self.ready() if not hasattr(self, 'repo') else task_store.read_task(self.ws)
                path = self.ws/relative; path.parent.mkdir(exist_ok=True); path.write_text('{}')
                with self.assertRaisesRegex(ValueError, '未知|损坏'):
                    resources.verify_station_inventory(self.ws)
                self.assertTrue(path.exists()); path.unlink()
        self.execute(task, self.reset_request(task))

    def test_controlled_export_preserves_actual_source_and_private_permissions(self):
        from workflow import station_export
        task = self.ready()
        (self.repo/'file.txt').write_text('exported content')
        directory = self.root/'exports'; directory.mkdir(mode=0o700)
        output = directory/'source.json'
        original_read = Path.read_bytes
        def bounded_export_read(path):
            if path == output:
                raise AssertionError('导出成果不能为了校验摘要整块读入内存')
            return original_read(path)
        with mock.patch.object(Path, 'read_bytes', bounded_export_read):
            result = station_export.export(self.ws,task['issue_key'],task['run_id'],'source/'+self.name+'/file.txt',str(output.resolve()))
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            plan = resources.plan(self.ws, task)
            self.assertEqual(plan['entries'][0]['preservation']['action'], 'export')
            self.execute(task, self.reset_request(task))
        self.assertTrue(output.exists())
        self.assertEqual(artifacts.digest(output.read_bytes()), result['sha256'])
        self.assertEqual((self.repo/'file.txt').read_text(),'baseline\n')

    def test_export_coverage_rejects_changed_file_with_streamed_digest(self):
        from workflow import station_export
        task = self.ready()
        (self.repo / 'file.txt').write_text('exported content')
        directory = self.root / 'exports'; directory.mkdir(mode=0o700)
        output = directory / 'source.json'
        station_export.export(self.ws, task['issue_key'], task['run_id'], 'source/' + self.name + '/file.txt', str(output.resolve()))
        plan = resources.plan(self.ws, task)
        archive = self.product / '.archive' / task['run_id']
        self.write(archive / 'source-artifacts.json', {'repositories': {}})
        archived_task = dict(task, archive_ref={'scope': 'product', 'run_id': task['run_id'], 'digest': 'a' * 64})
        artifacts.verify_coverage(self.ws, archived_task, plan)
        with output.open('ab') as stream:
            stream.write(b' ')
        with self.assertRaisesRegex(ValueError, '源码导出回读尚未核验'):
            artifacts.verify_coverage(self.ws, archived_task, plan)

    def test_archive_then_late_export_receipt_survives_reset(self):
        from workflow import station_export
        task = self.ready()
        self.execute(task,self.reset_request(task),kind='archive',op='op-before-export')
        task = task_store.read_task(self.ws)
        (self.repo/'file.txt').write_text('late content')
        directory = self.root/'exports'; directory.mkdir(mode=0o700)
        result = station_export.export(self.ws,task['issue_key'],task['run_id'],'source/'+self.name+'/file.txt',str((directory/'late.json').resolve()),'op-before-export')
        self.assertTrue(result['readback_ref'].startswith('product-archive:'))
        self.execute(task, self.reset_request(task))
        self.assertTrue(list(archive_store.receipts(self.ws, task['archive_ref']).glob('export-*.json')))

    def test_late_export_validates_operation_before_any_write(self):
        from workflow import station_export
        task = self.ready()
        self.execute(task,self.reset_request(task),kind='archive',op='op-export-before')
        task = task_store.read_task(self.ws)
        (self.repo/'file.txt').write_text('late content')
        directory = self.root/'exports'; directory.mkdir(mode=0o700)
        output = (directory/'late.json').resolve()
        receipts = archive_store.receipts(self.ws, task['archive_ref'])
        before = set(receipts.iterdir()) if receipts.exists() else set()
        for identifier in (None, 'op-export-wrong'):
            with self.assertRaisesRegex(ValueError, '绑定'):
                station_export.export(self.ws,task['issue_key'],task['run_id'],'source/'+self.name+'/file.txt',str(output),identifier)
            self.assertFalse(output.exists())
            self.assertEqual(set(receipts.iterdir()) if receipts.exists() else set(),before)
        station_export.export(self.ws,task['issue_key'],task['run_id'],'source/'+self.name+'/file.txt',str(output),'op-export-before')
        self.execute(task,self.reset_request(task))

    def test_export_new_snapshot_uses_new_receipt(self):
        from workflow import station_export
        task = self.ready()
        path = 'source/'+self.name+'/file.txt'
        directory = self.root/'exports'; directory.mkdir(mode=0o700)
        (self.repo/'file.txt').write_text('first')
        first = station_export.export(self.ws,task['issue_key'],task['run_id'],path,str((directory/'one.json').resolve()))
        (self.repo/'file.txt').write_text('second')
        second = station_export.export(self.ws,task['issue_key'],task['run_id'],path,str((directory/'two.json').resolve()))
        self.assertNotEqual(first['readback_ref'],second['readback_ref'])
        self.assertTrue((directory/'one.json').exists())
        self.execute(task,self.reset_request(task))

    def test_early_takeover_can_cancel_before_directory_registration(self):
        self.prepare_engineering()
        with mock.patch.object(directories,'create',side_effect=OSError('crash')), self.assertRaises(OSError):
            station.takeover(self.ws,self.request,'op-early-takeover',0)
        task=task_store.read_task(self.ws)
        self.execute(task,self.reset_request(task))
        self.assertIsNone(task_store.read_task(self.ws))

    def test_takeover_checkout_failure_can_cancel_git_only_clone(self):
        self.prepare_engineering()
        with mock.patch.object(station.source,'checkout_baseline',side_effect=OSError('crash')), self.assertRaises(OSError):
            station.takeover(self.ws,self.request,'op-frozen-takeover',0)
        task=task_store.read_task(self.ws)
        self.assertIn('reset_baseline',task)
        self.execute(task,self.reset_request(task))
        self.assertIsNone(task_store.read_task(self.ws))

    def test_generated_file_scale_has_constant_state_writes(self):
        task = self.ready()
        request = self.reset_request(task)
        self.execute(task,request,kind='archive',op='op-scale-archive')
        task=task_store.read_task(self.ws)
        entry=directories.load(self.ws,task)['runtime']
        measures=[]
        for count in (1000,10000,33075):
            for index in range(count): (self.ws/'runtime'/('f%d'%index)).write_bytes(b'x')
            plan_started=time.monotonic();plan=resources.plan(self.ws,task);plan_seconds=time.monotonic()-plan_started
            writes=[];original=task_store._write_json_atomic
            def record(path,value):
                writes.append(len(json.dumps(value)));return original(path,value)
            started=time.monotonic()
            with mock.patch.object(task_store,'_write_json_atomic',side_effect=record):
                directories.reset(self.ws,task,entry,{'operation_id':'op-scale-'+str(count), 'cleanup_plan': plan})
            row={'files':count,'plan_seconds':round(plan_seconds,3),'delete_seconds':round(time.monotonic()-started,3),'state_writes':len(writes),'state_bytes':sum(writes),'plan_bytes':len(json.dumps(plan))}
            measures.append(row);print('RESET_PERFORMANCE '+json.dumps(row),flush=True)
        self.assertEqual([r['state_writes'] for r in measures],[2,2,2])
        self.assertLess(max(r['state_bytes'] for r in measures)-min(r['state_bytes'] for r in measures),50)
        self.assertEqual(len(set(r['plan_bytes'] for r in measures)),1)


if __name__ == '__main__':
    unittest.main()

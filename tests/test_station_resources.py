#!/usr/bin/env python3
"""目录生产归属、独立导出和源码保护的真实 Git 回归。"""
import json
import base64
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
        from workflow import station_cleanup
        return {'scope': 'task', 'expected_plan_digest': station_cleanup.plan(self.ws)['digest'], 'materials': 'discard', 'decision_ref': '确认保全导出并放弃其余本地材料'}

    def execute(self, task, request, kind='clean', op='op-resource-reset'):
        return station.execute(self.ws, kind, task['issue_key'], task['run_id'], task['_revision'], op, request)

    def register_root(self, task, path='target'):
        resources.register(self.ws, task['issue_key'], task['run_id'], [{'kind':'directory', 'producer':'maven', 'path':'source/'+self.name+'/'+path}])
        return directories.load(self.ws, task)['source/'+self.name+'/'+path]


    def gitlink_ready(self):
        self.prepare_engineering()
        sha = self.git(self.seed, 'rev-parse', 'HEAD')
        self.git(self.seed, 'update-index', '--add', '--cacheinfo', '160000,' + sha + ',vendor/api')
        self.git(self.seed, 'commit', '-m', 'gitlink baseline')
        self.git(self.seed, 'push', str(self.remote), 'develop')
        return self.ready()

    def test_empty_gitlink_deleted_only_after_scope_decision(self):
        task = self.gitlink_ready()
        before = self.git(self.repo, 'ls-files', '--stage')
        self.git(self.repo, 'config', 'submodule.recurse', 'true')
        self.execute(task, self.reset_request(task))
        self.assertIsNone(task_store.read_task(self.ws))
        self.assertFalse(self.repo.exists())
        self.assertTrue(before)


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


    def test_existing_directory_requires_same_producer(self):
        task = self.ready()
        self.register_root(task)
        with self.assertRaisesRegex(ValueError, '来源'):
            resources.register(self.ws, task['issue_key'], task['run_id'], [{'kind':'directory', 'producer':'other', 'path':'source/'+self.name+'/target'}])


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
            plan = resources.export_plan(self.ws, task)
            self.assertEqual(plan['entries'][0]['preservation']['action'], 'export')
            self.execute(task, self.reset_request(task))
        self.assertTrue(output.exists())
        self.assertEqual(artifacts.digest(output.read_bytes()), result['sha256'])
        self.assertFalse(self.repo.exists())
        self.assertIn(b'exported content', base64.b64decode(json.loads(output.read_text())['bundle']['worktree_patch']))


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


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""源码保全预检：真实 Git 本地远端，不改动工作树、引用或接管占用。"""
import json
import os
import subprocess
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_station_lifecycle as fixture
from station_fixture import product_cli
from workflow import station_clean, station_clean_rules, station_source_preservation as preservation, task_store


class SourcePreservationTests(unittest.TestCase):
    setUp = fixture.StationTests.setUp
    prepare_engineering = fixture.StationTests.prepare_engineering
    write = fixture.StationTests.write
    git = fixture.StationTests.git
    takeover = fixture.StationTests.takeover

    def ready(self):
        self.task = self.takeover()
        self.repo = self.ws / 'source/tapdata/tapdata'
        self.git(self.repo, 'config', 'user.name', 'Test')
        self.git(self.repo, 'config', 'user.email', 'test@example.com')

    def inspect(self):
        return preservation.source_preservation(self.ws, station_clean_rules.load(self.ws))

    def test_clean_remote_verified_no_mutation(self):
        self.ready()
        paths = [task_store.current_path(self.ws), self.repo / '.git/index', self.repo / '.git/HEAD']
        before = [path.read_bytes() for path in paths]
        refs = self.git(self.repo, 'show-ref')
        result = self.inspect()
        self.assertFalse(result['requires_decision'])
        self.assertTrue(result['repositories'][0]['remote_verified'])
        self.assertEqual(before, [path.read_bytes() for path in paths])
        self.assertEqual(refs, self.git(self.repo, 'show-ref'))

    def test_dirty_index_untracked_ignored_and_generated(self):
        self.ready()
        (self.repo / 'file.txt').write_text('staged')
        self.git(self.repo, 'add', 'file.txt')
        (self.repo / 'file.txt').write_text('baseline\n')
        (self.repo / '.git/info/exclude').write_text('private.env\ntarget/\n')
        (self.repo / 'private.env').write_text('secret never printed')
        (self.repo / ' note ').write_text('unique')
        (self.repo / 'target').mkdir()
        (self.repo / 'target/build.bin').write_text('regenerable')
        result = self.inspect()
        row = result['repositories'][0]
        self.assertEqual(['file.txt'], row['changed_files'])
        self.assertIn('private.env', row['unique_files'])
        self.assertIn(' note ', row['unique_files'])
        self.assertNotIn('target/build.bin', row['unique_files'])
        self.assertEqual(1, row['generated_file_count'])
        self.assertTrue(result['requires_decision'])
        self.assertNotIn('secret never printed', json.dumps(result))

    def test_assume_unchanged_is_not_clean_proof(self):
        self.ready()
        self.git(self.repo, 'update-index', '--assume-unchanged', 'file.txt')
        (self.repo / 'file.txt').write_text('hidden modification')
        result = self.inspect()
        self.assertEqual(['file.txt'], result['repositories'][0]['unchecked_files'])
        self.assertTrue(result['requires_decision'])

    def test_unpushed_branch_and_stash(self):
        self.ready()
        self.git(self.repo, 'switch', '-c', 'feature')
        (self.repo / 'file.txt').write_text('local commit')
        self.git(self.repo, 'commit', '-am', 'local')
        (self.repo / 'file.txt').write_text('stash content')
        self.git(self.repo, 'stash', 'push')
        row = self.inspect()['repositories'][0]
        self.assertIn('refs/heads/feature', row['unpreserved_refs'])
        self.assertIn('refs/stash', row['unpreserved_refs'])

    def test_detached_unpublished_commit_even_with_other_branches(self):
        self.ready()
        self.git(self.repo, 'switch', '--detach')
        (self.repo / 'file.txt').write_text('detached')
        self.git(self.repo, 'commit', '-am', 'detached')
        self.assertIn('detached HEAD', self.inspect()['repositories'][0]['unpreserved_refs'])

    def test_remote_tracking_does_not_prove_remote_preservation(self):
        self.ready()
        self.git(self.remote, 'update-ref', '-d', 'refs/heads/develop')
        row = self.inspect()['repositories'][0]
        self.assertTrue(row['remote_verified'])
        self.assertIn('refs/heads/develop', row['unpreserved_refs'])
        self.assertTrue(self.git(self.repo, 'rev-parse', 'refs/remotes/origin/develop'))

    def test_unavailable_remote_reports_unverified_without_sensitive_stderr(self):
        self.ready()
        original = preservation.source.git
        def call(path, *args):
            if 'ls-remote' in args:
                raise ValueError('sensitive credential')
            return original(path, *args)
        with mock.patch.object(preservation.source, 'git', side_effect=call):
            result = self.inspect()
        self.assertFalse(result['repositories'][0]['remote_verified'])
        self.assertTrue(result['requires_decision'])
        self.assertNotIn('sensitive credential', json.dumps(result))

    def test_external_worktree_kept_and_reported(self):
        self.ready()
        outside = self.root / 'shared-worktree'
        self.git(self.repo, 'worktree', 'add', '--detach', str(outside))
        with self.assertRaisesRegex(ValueError, '外部工作树'):
            self.inspect()
        self.assertTrue((outside / 'file.txt').exists())

    def test_link_does_not_scan_external_data(self):
        self.ready()
        outside = self.root / 'external'
        outside.mkdir()
        (outside / 'private').write_text('private')
        (self.ws / 'source/external-link').symlink_to(outside)
        result = self.inspect()
        self.assertEqual(['external-link'], result['unknown_files'])
        self.assertNotIn('private', str(result))
        self.assertEqual('private', (outside / 'private').read_text())

    def test_unknown_nested_repository_never_queries_its_remote(self):
        self.ready()
        nested = self.repo / 'nested'
        self.git(self.repo, 'clone', str(self.remote), str(nested))
        original = preservation.source.git
        def call(path, *args):
            if Path(path) == nested and 'ls-remote' in args:
                self.fail('未知仓库不能访问任意远端')
            return original(path, *args)
        with mock.patch.object(preservation.source, 'git', side_effect=call):
            rows = self.inspect()['repositories']
        self.assertEqual(2, len(rows))
        self.assertFalse(rows[1]['remote_verified'])

    def test_opt_in_preflight_does_not_change_existing_cleanup_plan(self):
        self.ready()
        original = station_clean.check(self.ws, self.task)
        observed = station_clean.check(self.ws, self.task, source_preservation=True)
        self.assertEqual(original['cleanup_plan'], observed['cleanup_plan'])
        self.assertIn('source_preservation', observed)
        self.assertNotIn('source_preservation', original)
        self.assertIsNotNone(task_store.read_task(self.ws))

    def test_public_preflight_without_execution_identity(self):
        self.ready()
        before = task_store.current_path(self.ws).read_bytes()
        env = dict(os.environ)
        env.pop('AGENTICOPS_EXECUTION_ID', None)
        for command in (
            [str(product_cli(self.ws, 'workflow/station-clean.py')), '--dir', str(self.ws), '--check', '--source-preservation', '--details'],
            [str(product_cli(self.ws, 'workflow/task.py')), 'cleanup-preflight', '--dir', str(self.ws), '--issue-key', self.task['issue_key'], '--expected-run-id', self.task['run_id'], '--source-preservation', '--details'],
        ):
            response = subprocess.run([sys.executable, *command], capture_output=True, text=True, env=env)
            self.assertEqual(0, response.returncode, response.stderr)
            self.assertFalse(json.loads(response.stdout)['source_preservation']['requires_decision'])
            self.assertEqual(before, task_store.current_path(self.ws).read_bytes())

    def test_idle_empty_source(self):
        self.assertFalse(self.inspect()['requires_decision'])

    def test_source_root_link_rejected(self):
        (self.ws / 'source').rmdir()
        (self.ws / 'source').symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, '根为链接'):
            self.inspect()


if __name__ == '__main__':
    unittest.main()

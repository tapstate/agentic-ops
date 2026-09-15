#!/usr/bin/env python3
"""同版本工位生成/清理合同，同时验证安装产物而非只验证源码。"""
import argparse
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

parser = argparse.ArgumentParser()
parser.add_argument('--product-root', default=str(Path(__file__).resolve().parent.parent))
options, remaining = parser.parse_known_args()
ROOT = Path(options.product_root).resolve()
sys.path.insert(0, str(ROOT))
from bootstrap import workspace_registry as registry
from bootstrap.workspace_paths import WorkspaceDirectory
from workflow import task_store, station_operation, git_refs


class StationBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name) / 'workspace'
        self.init()

    def tearDown(self):
        # Remove only this fixture's registry entry, including failed-cleanup cases.
        registry.unregister(ROOT, self.workspace)
        self.temporary.cleanup()

    def init(self, *args, success=True):
        result = subprocess.run(['bash', str(ROOT / 'bootstrap/workspace-init.sh'),
            '--workspace', str(self.workspace), '--agent', 'codex', *args],
            env={**os.environ, 'AGENTIC_OPS_HOME': str(ROOT)}, capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def test_generate_purge_generate(self):
        before = json.loads((self.workspace / '.agenticops/workspace.json').read_text())
        self.assertEqual(before['schema_version'], 3)
        self.assertNotIn('repository_pool', before)
        for name in ('config', 'source', 'runtime', 'archive'):
            self.assertTrue((self.workspace / name).is_dir())
        for name in ('tasks', 'tasks.lock', 'worktrees'):
            self.assertFalse((self.workspace / '.agenticops' / name).exists())
        registry.detach(ROOT, self.workspace, purge=True)
        self.assertFalse((self.workspace / '.agenticops').exists())
        self.assertFalse((self.workspace / 'agenticops').exists())
        self.init()
        after = json.loads((self.workspace / '.agenticops/workspace.json').read_text())
        self.assertNotEqual(before['workspace_id'], after['workspace_id'])
        self.assertIsNone(task_store.read_current(self.workspace)['current'])

    def test_preserve_materials_requires_explicit_reuse(self):
        for name in ('config', 'source', 'archive'):
            (self.workspace / name / 'sentinel').write_text(name)
        registry.detach(ROOT, self.workspace, purge=True)
        self.init(success=False)
        self.init('--reuse-materials')
        for name in ('config', 'source', 'archive'):
            self.assertEqual((self.workspace / name / 'sentinel').read_text(), name)

    def make_cached_repository(self):
        repository = self.workspace / 'source/example/repository'
        repository.mkdir(parents=True)
        for command in (['git', 'init', str(repository)],
                        ['git', '-C', str(repository), 'remote', 'add', 'origin', str(repository)],
                        ['git', '-C', str(repository), '-c', 'user.name=Fixture',
                         '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-m', 'fixture']):
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        return repository

    def snapshot(self, repository):
        return git_refs.snapshot(repository,
            cache_file=self.workspace / '.agenticops/git-ref-cache-v2.json',
            cache_root=self.workspace, refresh='always')

    def test_real_cache_purge_and_rebuild_preserves_materials(self):
        repository = self.make_cached_repository()
        before = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'HEAD'])
        result = self.snapshot(repository)
        self.assertTrue(result['scopes']['heads']['refs'])
        for name in ('git-ref-cache-v2.json', 'git-ref-cache-v2.json.lock'):
            self.assertTrue((self.workspace / '.agenticops' / name).is_file())
        registry.detach(ROOT, self.workspace, purge=True)
        self.assertFalse((self.workspace / '.agenticops').exists())
        self.init('--reuse-materials')
        for name in ('git-ref-cache-v2.json', 'git-ref-cache-v2.json.lock'):
            self.assertFalse((self.workspace / '.agenticops' / name).exists())
        self.assertEqual(subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'HEAD']), before)
        self.assertIsNone(task_store.read_current(self.workspace)['current'])
        self.assertTrue(self.snapshot(repository)['scopes']['heads']['refs'])

    def test_cache_refresh_cannot_recreate_state_after_purge(self):
        repository = self.make_cached_repository()
        original = git_refs._query
        context = multiprocessing.get_context('fork')
        entered = context.Event()
        completed = context.Event()
        def purge():
            entered.set()
            registry.detach(ROOT, self.workspace, purge=True)
            completed.set()
        process = context.Process(target=purge)
        def purge_during_remote_query(*args):
            process.start()
            self.assertTrue(entered.wait(5))
            # A correct implementation keeps purge behind the in-flight writer.
            self.assertFalse(completed.wait(.3), 'Purge completed while a cache writer was active')
            return original(*args)
        try:
            with mock.patch.object(git_refs, '_query', side_effect=purge_during_remote_query):
                self.snapshot(repository)
        finally:
            process.join(5)
            if process.is_alive():
                process.terminate()
                process.join(5)
        self.assertEqual(process.exitcode, 0)
        self.assertTrue(completed.is_set())
        self.assertFalse((self.workspace / '.agenticops').exists(),
                         'An in-flight cache refresh recreated state after successful purge')

    def test_active_task_and_pending_operation_prevent_detach(self):
        state = self.workspace / '.agenticops/current-task.json'
        original = state.read_bytes()
        task_store.compare_and_set(self.workspace, 0, {'issue_key': 'TAP-123', 'run_id': 'run-test'})
        with self.assertRaisesRegex(ValueError, '任务占用'):
            registry.detach(ROOT, self.workspace, purge=True)
        state.write_bytes(original)
        operation = station_operation.begin(self.workspace, 'takeover', 'op-bootstrap-pending', 0, {})
        operation['status'] = 'failed'
        station_operation.save(self.workspace, operation)
        with self.assertRaisesRegex(ValueError, '未完成操作'):
            registry.detach(ROOT, self.workspace, purge=True)
        self.assertTrue((self.workspace / 'agenticops').exists())

    def test_unknown_state_and_runtime_are_not_deleted(self):
        unknown = self.workspace / '.agenticops/unknown'
        unknown.write_text('keep')
        with self.assertRaisesRegex(ValueError, '未知文件'):
            registry.detach(ROOT, self.workspace, purge=True)
        self.assertEqual(unknown.read_text(), 'keep')
        unknown.unlink()
        runtime = self.workspace / 'runtime/unknown'
        runtime.write_text('keep')
        with self.assertRaisesRegex(ValueError, 'runtime'):
            registry.detach(ROOT, self.workspace, purge=True)
        self.assertEqual(runtime.read_text(), 'keep')

    def test_reuse_never_adopts_nonempty_runtime_or_symlink(self):
        registry.detach(ROOT, self.workspace, purge=True)
        (self.workspace / 'runtime/sentinel').write_text('keep')
        self.init('--reuse-materials', success=False)
        self.assertEqual((self.workspace / 'runtime/sentinel').read_text(), 'keep')
        (self.workspace / 'runtime/sentinel').unlink()
        (self.workspace / 'source').rmdir()
        outside = Path(self.temporary.name) / 'outside-source'
        outside.mkdir()
        (outside / 'sentinel').write_text('keep')
        (self.workspace / 'source').symlink_to(outside, target_is_directory=True)
        self.init('--reuse-materials', success=False)
        self.assertEqual((outside / 'sentinel').read_text(), 'keep')

    def test_old_epoch_repair_and_purge_do_not_modify_old_state(self):
        state = self.workspace / '.agenticops'
        init = state / 'init.json'
        document = json.loads(init.read_text())
        document['workspace_state_epoch'] = 2
        init.write_text(json.dumps(document))
        old = state / 'tasks'
        old.mkdir()
        sentinel = old / 'index.json'
        sentinel.write_text('old-format')
        before = init.read_bytes()
        with self.assertRaisesRegex(ValueError, '受控解绑并重建'):
            registry.detach(ROOT, self.workspace, purge=True)
        result = subprocess.run([sys.executable, str(ROOT / 'bootstrap/render.py'),
            '--install-home', str(ROOT), '--workspace', str(self.workspace), '--refresh'],
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('受控解绑并重建', result.stderr)
        self.assertEqual(init.read_bytes(), before)
        self.assertEqual(sentinel.read_text(), 'old-format')

    def test_unknown_unbound_state_prevents_generation(self):
        registry.detach(ROOT, self.workspace, purge=True)
        state = self.workspace / '.agenticops'
        state.mkdir()
        (state / 'unknown.json').write_text('unknown')
        self.init('--reuse-materials', success=False)
        self.assertEqual((state / 'unknown.json').read_text(), 'unknown')
        self.assertFalse((state / 'workspace.json').exists())

    def test_state_replacement_after_preflight_is_rejected(self):
        outside = Path(self.temporary.name) / 'outside'
        outside.mkdir()
        sentinel = outside / 'current-task.json'
        sentinel.write_text('outside')
        original = registry.detach_preflight
        def replace(*args, **kwargs):
            result = original(*args, **kwargs)
            state = self.workspace / '.agenticops'
            state.rename(self.workspace / '.held')
            state.symlink_to(outside, target_is_directory=True)
            return result
        with mock.patch.object(registry, 'detach_preflight', side_effect=replace):
            with self.assertRaises(ValueError):
                registry.detach(ROOT, self.workspace, purge=True)
        self.assertEqual(sentinel.read_text(), 'outside')

    def test_purge_holds_lock_until_binding_removed(self):
        marker = Path(self.temporary.name) / 'entered'
        def writer():
            marker.write_text('started')
            try:
                with task_store.task_state_lock(self.workspace):
                    raise SystemExit(8)
            except ValueError:
                raise SystemExit(0)
        process = multiprocessing.get_context('fork').Process(target=writer)
        original = registry.detach_preflight
        def check(*args, **kwargs):
            result = original(*args, **kwargs)
            process.start()
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(marker.exists())
            time.sleep(.05)
            self.assertTrue(process.is_alive())
            return result
        with mock.patch.object(registry, 'detach_preflight', side_effect=check):
            registry.detach(ROOT, self.workspace, purge=True)
        process.join(5)
        self.assertEqual(process.exitcode, 0)
        self.assertFalse((self.workspace / '.agenticops').exists())


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *remaining], verbosity=2)

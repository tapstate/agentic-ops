#!/usr/bin/env python3
"""同版本工位生成/清理合同，同时验证安装产物而非只验证源码。"""
import argparse
import json
import multiprocessing
import os
import shutil
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
from bootstrap import station_registry as registry
from bootstrap.station_paths import StationDirectory
from workflow import task_store, station_operation, git_refs


class LifecycleCleanTreeTests(unittest.TestCase):
    def check_tree(self, root):
        return subprocess.run(['bash', '-c',
            'set -euo pipefail; source "$1"; lifecycle_require_clean_tree "$2" fixture',
            'bash', str(ROOT / 'bootstrap/lifecycle-common.sh'), str(root)],
            capture_output=True, text=True)

    def test_git_status_failure_never_proves_clean_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self.check_tree(root)
            self.assertNotEqual(0, result.returncode)
            self.assertIn('无法核验', result.stderr)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            index = root / '.git/index'
            index.write_bytes(b'broken index')
            result = self.check_tree(root)
            self.assertNotEqual(0, result.returncode)
            self.assertIn('无法核验', result.stderr)
            self.assertEqual(b'broken index', index.read_bytes())

    def test_clean_tree_checks_hidden_untracked_and_tracked_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def git(*args):
                subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)
            git('init', '-q')
            self.assertEqual(0, self.check_tree(root).returncode)
            git('config', 'status.showUntrackedFiles', 'no')
            user_file = root / 'user-file'
            user_file.write_text('preserve')
            self.assertNotEqual(0, self.check_tree(root).returncode)
            git('add', 'user-file')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                'commit', '-qm', 'fixture')
            self.assertEqual(0, self.check_tree(root).returncode)
            user_file.write_text('modified')
            self.assertNotEqual(0, self.check_tree(root).returncode)
            self.assertEqual('modified', user_file.read_text())


class StationBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.station = Path(self.temporary.name) / 'station'
        self.init()

    def tearDown(self):
        # Remove only this fixture's registry entry, including failed-cleanup cases.
        registry.unregister(ROOT, self.station)
        self.temporary.cleanup()

    def init(self, *args, success=True, project='tapdata'):
        project_args = [] if project is None else ['--project', project]
        result = subprocess.run(['bash', str(ROOT / 'bootstrap/station-init.sh'),
            '--station', str(self.station), '--agent', 'codex', '--source-pool', str(self.station.parent / 'pool'), *project_args, *args],
            env={**os.environ, 'AGENTIC_OPS_HOME': str(ROOT)}, capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def test_generate_purge_generate(self):
        before = json.loads((self.station / '.agenticops/station.json').read_text())
        self.assertEqual(before['schema_version'], 4)
        self.assertEqual(before['source_pool'], str((self.station.parent / 'pool').resolve()))
        self.assertNotIn('repository_pool', before)
        for name in ('config', 'source', 'runtime'):
            self.assertTrue((self.station / name).is_dir())
        self.assertFalse((self.station / 'archive').exists())
        for name in ('tasks', 'tasks.lock', 'worktrees'):
            self.assertFalse((self.station / '.agenticops' / name).exists())
        registry.detach(ROOT, self.station, purge=True)
        self.assertFalse((self.station / '.agenticops').exists())
        self.assertFalse((self.station / 'agenticops').exists())
        self.init()
        after = json.loads((self.station / '.agenticops/station.json').read_text())
        self.assertNotEqual(before['station_id'], after['station_id'])
        self.assertIsNone(task_store.read_current(self.station)['current'])

    def test_wiki_is_prepared_only_by_explicit_repair_option(self):
        with mock.patch.object(registry.project_rules, 'load_profile', return_value={'wiki_repository': 'tapstate/wiki'}), \
                mock.patch.object(registry.shared_repositories, 'run', return_value={
                    'repository': 'tapstate/wiki', 'path': '/tmp/wiki'}) as prepared:
            result = registry.ensure_wiki(ROOT, self.station)
        self.assertEqual(result['repository'], 'tapstate/wiki')
        prepared.assert_called_once_with(ROOT, 'tapstate/wiki', 'ensure', str((self.station.parent / 'pool').resolve()))

    def test_repair_all_prepares_a_shared_wiki_once(self):
        other = self.station.parent / 'other'
        (other / '.agenticops').mkdir(parents=True)
        (other / '.agenticops/station.json').write_text((self.station / '.agenticops/station.json').read_text())
        args = type('Args', (), {'station': None, 'all': True, 'ensure_wiki': True, 'yes': True})()
        with mock.patch.object(registry, 'select_targets', return_value=[self.station, other]), \
                mock.patch.object(registry, 'require_tracked'), \
                mock.patch.object(registry, 'show_targets'), \
                mock.patch.object(registry, 'confirm'), \
                mock.patch.object(registry, 'refresh'), \
                mock.patch.object(registry, 'wiki_repository', return_value='tapstate/wiki'), \
                mock.patch.object(registry.shared_repositories, 'run', return_value={
                    'repository': 'tapstate/wiki', 'path': '/tmp/wiki'}) as prepared:
            registry.command_refresh(args, ROOT, 'repair')
        prepared.assert_called_once_with(ROOT, 'tapstate/wiki', 'ensure', str((self.station.parent / 'pool').resolve()))

    def test_preserve_materials_requires_explicit_reuse(self):
        (self.station / 'archive').mkdir()
        for name in ('config', 'source', 'archive'):
            (self.station / name / 'sentinel').write_text(name)
        registry.detach(ROOT, self.station, purge=True)
        self.init(success=False)
        self.init('--reuse-materials')
        for name in ('config', 'source', 'archive'):
            self.assertEqual((self.station / name / 'sentinel').read_text(), name)

    def make_cached_repository(self):
        repository = self.station / 'source/example/repository'
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
            cache_file=self.station / '.agenticops/git-ref-cache-v2.json',
            cache_root=self.station, refresh='always')

    def test_real_cache_purge_and_rebuild_preserves_materials(self):
        repository = self.make_cached_repository()
        before = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'HEAD'])
        result = self.snapshot(repository)
        self.assertTrue(result['scopes']['heads']['refs'])
        for name in ('git-ref-cache-v2.json', 'git-ref-cache-v2.json.lock'):
            self.assertTrue((self.station / '.agenticops' / name).is_file())
        registry.detach(ROOT, self.station, purge=True)
        self.assertFalse((self.station / '.agenticops').exists())
        self.init('--reuse-materials')
        for name in ('git-ref-cache-v2.json', 'git-ref-cache-v2.json.lock'):
            self.assertFalse((self.station / '.agenticops' / name).exists())
        self.assertEqual(subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'HEAD']), before)
        self.assertIsNone(task_store.read_current(self.station)['current'])
        self.assertTrue(self.snapshot(repository)['scopes']['heads']['refs'])

    def test_cache_refresh_cannot_recreate_state_after_purge(self):
        repository = self.make_cached_repository()
        original = git_refs._query
        context = multiprocessing.get_context('fork')
        entered = context.Event()
        completed = context.Event()
        def purge():
            entered.set()
            registry.detach(ROOT, self.station, purge=True)
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
        self.assertFalse((self.station / '.agenticops').exists(),
                         'An in-flight cache refresh recreated state after successful purge')

    def test_active_task_and_pending_operation_prevent_detach(self):
        state = self.station / '.agenticops/current-task.json'
        original = state.read_bytes()
        task_store.compare_and_set(self.station, 0, {'issue_key': 'TAP-123', 'run_id': 'run-test',
            'task_class': 'technical_task', 'stage': 'waiting_takeover', 'outcome': 'in_progress',
            'facts': {}, 'history': [], 'pending': None, 'engineering_baseline': {'status': 'resolving'},
            'task_repositories': {}, 'terminal_proof': None, 'archive_ref': None})
        with self.assertRaisesRegex(ValueError, '任务占用'):
            registry.detach(ROOT, self.station, purge=True)
        state.write_bytes(original)
        station_config = self.station / '.agenticops/station.json'
        document = json.loads(station_config.read_text())
        document['branch_identity'] = {
            'schema_version': 1,
            'git_name': 'Fixture',
            'source': 'git_global_user_name',
        }
        station_config.write_text(json.dumps(document))
        operation = station_operation.begin(self.station, 'takeover', 'op-bootstrap-pending', 0, {'issue_key': 'TAP-123'})
        operation['status'] = 'failed'
        station_operation.save(self.station, operation)
        with self.assertRaisesRegex(ValueError, '未完成操作'):
            registry.detach(ROOT, self.station, purge=True)
        self.assertTrue((self.station / 'agenticops').exists())

    def test_unknown_state_and_runtime_are_not_deleted(self):
        unknown = self.station / '.agenticops/unknown'
        unknown.write_text('keep')
        with self.assertRaisesRegex(ValueError, '未知文件'):
            registry.detach(ROOT, self.station, purge=True)
        self.assertEqual(unknown.read_text(), 'keep')
        unknown.unlink()
        runtime = self.station / 'runtime/unknown'
        runtime.write_text('keep')
        with self.assertRaisesRegex(ValueError, 'runtime'):
            registry.detach(ROOT, self.station, purge=True)
        self.assertEqual(runtime.read_text(), 'keep')

    def test_reuse_never_adopts_nonempty_runtime_or_symlink(self):
        registry.detach(ROOT, self.station, purge=True)
        (self.station / 'runtime/sentinel').write_text('keep')
        self.init('--reuse-materials', success=False)
        self.assertEqual((self.station / 'runtime/sentinel').read_text(), 'keep')
        (self.station / 'runtime/sentinel').unlink()
        (self.station / 'source').rmdir()
        outside = Path(self.temporary.name) / 'outside-source'
        outside.mkdir()
        (outside / 'sentinel').write_text('keep')
        (self.station / 'source').symlink_to(outside, target_is_directory=True)
        self.init('--reuse-materials', success=False)
        self.assertEqual((outside / 'sentinel').read_text(), 'keep')

    def test_old_epoch_repair_and_purge_do_not_modify_old_state(self):
        state = self.station / '.agenticops'
        init = state / 'init.json'
        document = json.loads(init.read_text())
        document['station_state_epoch'] = 2
        init.write_text(json.dumps(document))
        old = state / 'tasks'
        old.mkdir()
        sentinel = old / 'index.json'
        sentinel.write_text('old-format')
        before = init.read_bytes()
        with self.assertRaisesRegex(ValueError, '受控解绑并重建'):
            registry.detach(ROOT, self.station, purge=True)
        result = subprocess.run([sys.executable, str(ROOT / 'bootstrap/render.py'),
            '--install-home', str(ROOT), '--station', str(self.station), '--refresh'],
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('受控解绑并重建', result.stderr)
        self.assertEqual(init.read_bytes(), before)
        self.assertEqual(sentinel.read_text(), 'old-format')

    def test_unknown_unbound_state_prevents_generation(self):
        registry.detach(ROOT, self.station, purge=True)
        state = self.station / '.agenticops'
        state.mkdir()
        (state / 'unknown.json').write_text('unknown')
        self.init('--reuse-materials', success=False)
        self.assertEqual((state / 'unknown.json').read_text(), 'unknown')
        self.assertFalse((state / 'station.json').exists())

    def test_prune_does_not_unregister_an_unknown_state_directory(self):
        state = self.station / '.agenticops'
        (state / 'station.json').unlink()
        (state / 'unknown.json').write_text('keep')
        args = type('Args', (), {'station': str(self.station), 'all': False, 'yes': True})()
        registry.command_prune(args, ROOT)
        self.assertIn(str(self.station.resolve()), registry.load_registry(ROOT))
        self.assertEqual((state / 'unknown.json').read_text(), 'keep')

    def test_station_init_registers_before_render_under_the_lifecycle_lock(self):
        script = (ROOT / 'bootstrap/station-init.sh').read_text()
        self.assertLess(script.index('lifecycle_acquire_lock'), script.index('--resolve-project'))
        self.assertLess(script.index('--resolve-project'), script.index('register --station'))
        self.assertLess(script.index('register --station'), script.rindex('bootstrap/render.py'))

    def station_bytes(self):
        return {p.relative_to(self.station).as_posix(): p.read_bytes()
                for p in self.station.rglob('*') if p.is_file() and not p.is_symlink()}

    def test_existing_project_is_reused_and_conflicting_project_is_readonly(self):
        task_store.write_task(self.station, {'issue_key': 'TAP-123', 'run_id': 'run-project-binding',
            'task_class': 'technical_task', 'stage': 'task_intake', 'outcome': 'in_progress',
            'facts': {}, 'history': [], 'pending': None, 'engineering_baseline': {'status': 'resolving'},
            'task_repositories': {}, 'terminal_proof': None, 'archive_ref': None})
        current = self.station / '.agenticops/current-task.json'
        before_current = current.read_bytes()
        station_id = json.loads((self.station / '.agenticops/station.json').read_text())['station_id']
        self.init(project=None)
        self.assertEqual(before_current, current.read_bytes())
        self.assertEqual(station_id, json.loads((self.station / '.agenticops/station.json').read_text())['station_id'])
        before = self.station_bytes()
        registered = registry.registry_path(ROOT).read_bytes()
        result = self.init(project='other', success=False)
        self.assertIn('已绑定项目 tapdata', result.stderr)
        self.assertEqual(before, self.station_bytes())
        self.assertEqual(registered, registry.registry_path(ROOT).read_bytes())
        for mode in ([], ['--refresh'], ['--check']):
            result = subprocess.run([sys.executable, str(ROOT / 'bootstrap/render.py'),
                '--install-home', str(ROOT), '--station', str(self.station), '--project', 'other', *mode],
                capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('已绑定项目 tapdata', result.stderr)
            self.assertEqual(before, self.station_bytes())

    def test_new_station_requires_project_before_registration_or_creation(self):
        original = self.station
        self.station = original.parent / 'new-station'
        try:
            registered = registry.registry_path(ROOT).read_bytes()
            for project, message in ((None, '首次初始化'), ('', '项目 ID'), ('missing-project', '未安装项目')):
                result = self.init(project=project, success=False)
                self.assertIn(message, result.stderr)
                self.assertFalse(self.station.exists())
                self.assertEqual(registered, registry.registry_path(ROOT).read_bytes())
                project_args = [] if project is None else ['--project', project]
                result = subprocess.run([sys.executable, str(ROOT / 'bootstrap/render.py'),
                    '--install-home', str(ROOT), '--station', str(self.station), *project_args],
                    capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertFalse(self.station.exists())
        finally:
            self.station = original

    def test_non_tapdata_binding_is_reused_without_default(self):
        product = self.station.parent / 'product'
        for name in ('bootstrap', 'contracts', 'workflow', 'adapters', 'skills'):
            shutil.copytree(ROOT / name, product / name)
        (product / 'projects/demo').mkdir(parents=True)
        (product / 'projects/other').mkdir()
        target = self.station.parent / 'demo-station'
        command = [sys.executable, str(product / 'bootstrap/render.py'), '--install-home', str(product),
                   '--station', str(target), '--source-pool', str(self.station.parent / 'pool'), '--agent', 'codex']
        result = subprocess.run(command + ['--project', 'demo'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        before = json.loads((target / '.agenticops/station.json').read_text())
        for mode in ([], ['--refresh'], ['--check']):
            result = subprocess.run(command + mode, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(before, json.loads((target / '.agenticops/station.json').read_text()))
        result = subprocess.run(command + ['--project', 'other'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('已绑定项目 demo', result.stderr)
        self.assertEqual(before, json.loads((target / '.agenticops/station.json').read_text()))

    def test_state_replacement_after_preflight_is_rejected(self):
        outside = Path(self.temporary.name) / 'outside'
        outside.mkdir()
        sentinel = outside / 'current-task.json'
        sentinel.write_text('outside')
        original = registry.detach_preflight
        def replace(*args, **kwargs):
            result = original(*args, **kwargs)
            state = self.station / '.agenticops'
            state.rename(self.station / '.held')
            state.symlink_to(outside, target_is_directory=True)
            return result
        with mock.patch.object(registry, 'detach_preflight', side_effect=replace):
            with self.assertRaises(ValueError):
                registry.detach(ROOT, self.station, purge=True)
        self.assertEqual(sentinel.read_text(), 'outside')

    def test_purge_holds_lock_until_binding_removed(self):
        marker = Path(self.temporary.name) / 'entered'
        def writer():
            marker.write_text('started')
            try:
                with task_store.task_state_lock(self.station):
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
            registry.detach(ROOT, self.station, purge=True)
        process.join(5)
        self.assertEqual(process.exitcode, 0)
        self.assertFalse((self.station / '.agenticops').exists())


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *remaining], verbosity=2)

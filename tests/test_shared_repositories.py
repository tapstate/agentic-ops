#!/usr/bin/env python3
"""中央共享仓库的隔离 Git 集成验收。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bootstrap import shared_repositories as shared


class SharedRepositoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.seed = self.root / 'seed'
        self.seed.mkdir()
        self.git(self.seed, 'init', '-b', 'main')
        self.git(self.seed, 'config', 'user.name', 'Test')
        self.git(self.seed, 'config', 'user.email', 'test@example.com')
        self.commit('first')
        self.product = self.root / 'product'
        (self.product / 'bootstrap').mkdir(parents=True)
        (self.product / 'bootstrap/shared-repositories.json').write_text(json.dumps({
            'schema_version': 1, 'repositories': {'tapstate/wiki': {
                'origin': str(self.seed), 'branch': 'main'}}}))
        self.path = self.product / '.local/shared-repositories/tapstate/wiki'

    def git(self, path, *args):
        return subprocess.run(['git', '-C', str(path), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, text):
        (self.seed / 'article.md').write_text(text)
        self.git(self.seed, 'add', '.')
        self.git(self.seed, 'commit', '-m', text)

    def run_action(self, action):
        return shared.run(self.product, 'tapstate/wiki', action)

    def test_prepare_update_and_old_objects(self):
        self.assertEqual(self.run_action('ensure')['status'], 'ready')
        self.assertFalse((self.path / '.git/objects/info/alternates').exists())
        old = self.git(self.path, 'rev-parse', 'HEAD')
        with mock.patch.object(shared.source_pool, 'refreshed_at_root', side_effect=AssertionError('network')):
            self.run_action('ensure')
            self.run_action('status')
        self.commit('second')
        self.run_action('update')
        self.assertEqual((self.path / 'article.md').read_text(), 'second')
        self.assertEqual(self.git(self.path, 'show', old + ':article.md'), 'first')
        pool = shared.source_pool.pool_path_at_root(self.product, 'tapstate/wiki', str(self.seed))
        for item in (self.path / '.git/objects').rglob('*'):
            equivalent = pool / item.relative_to(self.path / '.git')
            if item.is_file() and equivalent.is_file():
                self.assertNotEqual(item.stat().st_ino, equivalent.stat().st_ino)

    def test_dirty_unknown_and_busy_are_preserved(self):
        self.path.mkdir(parents=True)
        marker = self.path / 'unknown'
        marker.write_text('keep')
        with self.assertRaises(ValueError):
            self.run_action('ensure')
        self.assertEqual(marker.read_text(), 'keep')
        marker.unlink()
        self.path.rmdir()
        self.run_action('ensure')
        with shared.locked(self.path):
            with self.assertRaisesRegex(ValueError, '管理中'):
                self.run_action('status')
        (self.path / 'article.md').write_text('dirty')
        with self.assertRaisesRegex(ValueError, '本地修改'):
            self.run_action('update')
        self.assertEqual((self.path / 'article.md').read_text(), 'dirty')

    def test_identity_and_incomplete_operations(self):
        self.run_action('ensure')
        for relative in ('index.lock', 'MERGE_HEAD', 'objects/info/alternates'):
            marker = self.path / '.git' / relative
            marker.write_text('')
            with self.assertRaises(ValueError):
                self.run_action('status')
            marker.unlink()
        self.git(self.path, 'remote', 'set-url', 'origin', str(self.root / 'wrong'))
        with self.assertRaisesRegex(ValueError, 'origin'):
            self.run_action('status')

    def test_failed_refresh_preserves_head(self):
        self.run_action('ensure')
        old = self.git(self.path, 'rev-parse', 'HEAD')
        with mock.patch.object(shared.source_pool, 'refreshed_at_root', side_effect=ValueError('offline')):
            with self.assertRaisesRegex(ValueError, 'offline'):
                self.run_action('update')
        self.assertEqual(self.git(self.path, 'rev-parse', 'HEAD'), old)
        self.run_action('status')

    def test_behind_is_usable_but_ahead_is_rejected(self):
        self.run_action('ensure')
        first = self.git(self.path, 'rev-parse', 'HEAD')
        self.commit('second')
        self.run_action('update')
        second = self.git(self.path, 'rev-parse', 'HEAD')
        self.git(self.path, 'reset', '--hard', first)
        self.assertEqual(self.run_action('status')['relation'], 'behind')
        self.run_action('update')
        self.git(self.path, 'update-ref', 'refs/remotes/origin/main', first)
        with self.assertRaisesRegex(ValueError, '超前或分叉'):
            self.run_action('update')
        self.assertEqual(self.git(self.path, 'rev-parse', 'HEAD'), second)

    def test_hooks_disabled_and_filters_rejected(self):
        self.run_action('ensure')
        marker = self.root / 'hook-ran'
        hook = self.path / '.git/hooks/post-merge'
        hook.parent.mkdir(exist_ok=True)
        hook.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\n')
        hook.chmod(0o755)
        self.commit('second')
        self.run_action('update')
        self.assertFalse(marker.exists())
        self.git(self.path, 'config', 'filter.test.clean', 'false')
        with self.assertRaisesRegex(ValueError, '过滤器'):
            self.run_action('status')

    def test_symlink_path_and_invalid_registry(self):
        self.path.parent.mkdir(parents=True)
        self.path.symlink_to(self.seed, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, '真实目录'):
            self.run_action('ensure')
        self.assertTrue(self.path.is_symlink())
        (self.product / 'bootstrap/shared-repositories.json').write_text('null')
        with self.assertRaisesRegex(ValueError, '登记版本'):
            self.run_action('status')

    def test_timeout_stops_descendants(self):
        executable = self.root / 'git'
        marker = self.root / 'late-write'
        child = 'import time; from pathlib import Path; time.sleep(.6); Path(%r).touch()' % str(marker)
        executable.write_text('#!%s\nimport subprocess,sys,time\nsubprocess.Popen([sys.executable,"-c",%r])\ntime.sleep(3)\n' % (sys.executable, child))
        executable.chmod(0o755)
        with mock.patch.dict(os.environ, {'PATH': str(self.root) + os.pathsep + os.environ['PATH']}), \
                mock.patch.object(shared, 'GIT_NETWORK_TIMEOUT', .15):
            with self.assertRaisesRegex(ValueError, '超时'):
                shared.git(self.root, 'fetch')
        time.sleep(.65)
        self.assertFalse(marker.exists())

    def test_interrupt_reaps_process_before_return(self):
        process = mock.Mock(pid=987654)
        process.communicate.side_effect = [KeyboardInterrupt(), ('', '')]
        with mock.patch.object(shared.subprocess, 'Popen', return_value=process), \
                mock.patch.object(shared.os, 'killpg') as kill:
            with self.assertRaises(KeyboardInterrupt):
                shared.git(self.root, 'fetch')
        kill.assert_called_once_with(process.pid, shared.signal.SIGKILL)
        self.assertEqual(process.communicate.call_count, 2)

    def test_cli_sigterm_stops_git_descendants(self):
        executable = self.root / 'git'
        started = self.root / 'child-started'
        marker = self.root / 'late-write'
        child = 'import time; from pathlib import Path; Path(%r).touch(); time.sleep(.8); Path(%r).touch()' % (str(started), str(marker))
        executable.write_text('#!%s\nimport subprocess,sys,time\nsubprocess.Popen([sys.executable,"-c",%r])\ntime.sleep(5)\n' % (sys.executable, child))
        executable.chmod(0o755)
        env = dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ['PATH'])
        process = subprocess.Popen([sys.executable, str(ROOT / 'bootstrap/shared_repositories.py'),
                                    'status', '--product-root', str(self.product),
                                    '--repository', 'tapstate/wiki'], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not started.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(started.exists())
            process.terminate()
            stdout, _ = process.communicate(timeout=3)
            self.assertEqual(process.returncode, 130)
            self.assertEqual(json.loads(stdout)['status'], 'unavailable')
            time.sleep(.85)
            self.assertFalse(marker.exists())
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_marker_branch_and_pack_symlink_rejected(self):
        self.run_action('ensure')
        self.git(self.path, 'config', shared.MARKER, '2:tapstate/wiki')
        with self.assertRaisesRegex(ValueError, '拒绝采用'):
            self.run_action('status')
        self.git(self.path, 'config', shared.MARKER, '1:tapstate/wiki')
        self.git(self.path, 'checkout', '-b', 'other')
        with self.assertRaisesRegex(ValueError, '分支不符'):
            self.run_action('status')
        self.git(self.path, 'checkout', 'main')
        pack = self.path / '.git/objects/pack'
        saved = self.root / 'saved-pack'
        pack.rename(saved)
        pack.symlink_to(saved, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, '真实目录'):
            self.run_action('status')


if __name__ == '__main__':
    unittest.main()

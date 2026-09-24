#!/usr/bin/env python3
"""清理回到开发分支并回收受管基线的真实 Git 回归。"""
import json
import subprocess
import unittest
from unittest import mock

import test_station_resources as fixtures
from workflow import station_resources as resources, task_store


class DevelopmentResetTests(unittest.TestCase):
    setUp = fixtures.ResourceTests.setUp
    prepare_engineering = fixtures.ResourceTests.prepare_engineering
    write = fixtures.ResourceTests.write
    git = fixtures.ResourceTests.git
    takeover = fixtures.ResourceTests.takeover
    ready = fixtures.ResourceTests.ready
    reset_request = fixtures.ResourceTests.reset_request
    execute = fixtures.ResourceTests.execute

    def ref(self, name):
        result = subprocess.run(['git', '-C', str(self.repo), 'rev-parse', '--verify', name], capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None

    def commit(self, text):
        self.git(self.repo, 'config', 'user.name', 'Test')
        self.git(self.repo, 'config', 'user.email', 'test@example.com')
        (self.repo / 'file.txt').write_text(text)
        self.git(self.repo, 'commit', '-am', text)
        return self.git(self.repo, 'rev-parse', 'HEAD')

    def second_baseline(self):
        self.prepare_engineering()
        parent = self.git(self.seed, 'rev-parse', 'HEAD')
        (self.seed / 'file.txt').write_text('next baseline\n')
        self.git(self.seed, 'commit', '-am', 'next baseline')
        self.git(self.seed, 'push', str(self.remote), 'develop')
        return parent

    def managed_ref(self, sha):
        name = 'agenticops/baseline/develop-' + sha[:12]
        if self.ref('refs/heads/' + name) is None:
            self.git(self.repo, 'branch', name, sha)
        return 'refs/heads/' + name

    def test_clean_checks_out_profile_development_branch(self):
        self.prepare_engineering()
        self.git(self.seed, 'branch', 'integration')
        self.git(self.seed, 'push', str(self.remote), 'integration')
        path = self.product / 'projects/tapdata/repositories.json'
        catalog = json.loads(path.read_text())
        for entry in catalog['repositories'].values():
            entry['dev_branch'] = 'integration'
        self.write(path, catalog)
        task = self.ready()
        plan = resources.plan(self.ws, task)
        self.assertEqual('integration', plan['source'][self.name]['checkout_branch'])
        self.execute(task, self.reset_request(task))
        self.assertEqual('integration', self.git(self.repo, 'branch', '--show-current'))
        self.assertEqual(task['reset_baseline'][self.name]['sha'], self.ref('HEAD'))
        self.assertIsNone(task_store.read_task(self.ws))

    def test_existing_development_ancestor_fast_forwards(self):
        parent = self.second_baseline()
        task = self.ready()
        self.git(self.repo, 'branch', '-f', 'develop', parent)
        self.execute(task, self.reset_request(task))
        self.assertEqual('develop', self.git(self.repo, 'branch', '--show-current'))
        self.assertEqual(task['reset_baseline'][self.name]['sha'], self.ref('develop'))

    def test_development_unique_commit_is_not_overwritten(self):
        task = self.ready()
        self.git(self.repo, 'checkout', '-B', 'develop')
        unique = self.commit('independent development work')
        self.git(self.repo, 'checkout', self.branch)
        with self.assertRaises(ValueError):
            self.execute(task, self.reset_request(task))
        self.assertEqual(unique, self.ref('develop'))
        self.assertIsNotNone(task_store.read_task(self.ws))

    def test_diverged_development_is_not_overwritten(self):
        parent = self.second_baseline()
        task = self.ready()
        self.git(self.repo, 'checkout', '-B', 'develop', parent)
        unique = self.commit('diverged development work')
        self.git(self.repo, 'checkout', self.branch)
        with self.assertRaises(ValueError):
            self.execute(task, self.reset_request(task))
        self.assertEqual(unique, self.ref('develop'))
        self.assertIsNotNone(task_store.read_task(self.ws))

    def test_reachable_baselines_removed_task_and_archive_refs_retained(self):
        task = self.ready()
        target = task['reset_baseline'][self.name]['sha']
        temporary = self.managed_ref(target)
        delivered = self.commit('preserved task work')
        plan = resources.plan(self.ws, task)
        self.assertEqual(target, plan['source'][self.name]['baseline_refs'][temporary])
        self.execute(task, self.reset_request(task))
        self.assertIsNone(self.ref(temporary))
        self.assertEqual(delivered, self.ref(self.branch))
        self.assertEqual(delivered, self.ref(plan['source'][self.name]['preserved_ref']))
        self.assertEqual('develop', self.git(self.repo, 'branch', '--show-current'))

    def test_baseline_reachable_only_from_preserved_task_head_is_removed(self):
        task = self.ready()
        delivered = self.commit('task-only preserved commit')
        temporary = self.managed_ref(delivered)
        plan = resources.plan(self.ws, task)
        self.assertEqual(delivered, plan['source'][self.name]['baseline_refs'][temporary])
        self.execute(task, self.reset_request(task))
        self.assertIsNone(self.ref(temporary))
        self.assertEqual(delivered, self.ref(self.branch))
        self.assertEqual(delivered, self.ref(plan['source'][self.name]['preserved_ref']))

    def test_baseline_with_unpreserved_commit_is_not_deleted(self):
        task = self.ready()
        unique = self.commit('unpreserved baseline commit')
        temporary = self.managed_ref(unique)
        self.git(self.repo, 'reset', '--hard', task['reset_baseline'][self.name]['sha'])
        with self.assertRaises(ValueError):
            self.execute(task, self.reset_request(task))
        self.assertEqual(unique, self.ref(temporary))
        self.assertIsNotNone(task_store.read_task(self.ws))

    def interrupt_after_neutral(self, task, request):
        original = resources.neutral
        def completed_then_interrupted(*args, **kwargs):
            result = original(*args, **kwargs)
            raise RuntimeError('fixture: interrupted after neutral')
        with mock.patch.object(resources, 'neutral', side_effect=completed_then_interrupted):
            with self.assertRaisesRegex(RuntimeError, 'fixture: interrupted'):
                self.execute(task, request)

    def test_resume_after_baseline_deletion_is_idempotent(self):
        task = self.ready()
        temporary = self.managed_ref(task['reset_baseline'][self.name]['sha'])
        request = self.reset_request(task)
        self.interrupt_after_neutral(task, request)
        self.assertIsNone(self.ref(temporary))
        original = resources.source.git
        def reject_repeated_delete(path, *args, **kwargs):
            self.assertFalse(args[:2] == ('update-ref', '-d') and temporary in args, '恢复不应重复删除已回收引用')
            return original(path, *args, **kwargs)
        with mock.patch.object(resources.source, 'git', side_effect=reject_repeated_delete):
            self.execute(task, request)
        self.assertIsNone(task_store.read_task(self.ws))
        self.assertEqual('develop', self.git(self.repo, 'branch', '--show-current'))

    def test_resume_rejects_development_drift(self):
        task = self.ready()
        request = self.reset_request(task)
        self.interrupt_after_neutral(task, request)
        drift = self.commit('late independent change')
        with self.assertRaises(ValueError):
            self.execute(task, request)
        self.assertEqual(drift, self.ref('HEAD'))
        self.assertIsNotNone(task_store.read_task(self.ws))

    def test_resume_rejects_recreated_baseline_ref(self):
        task = self.ready()
        temporary = self.managed_ref(task['reset_baseline'][self.name]['sha'])
        request = self.reset_request(task)
        self.interrupt_after_neutral(task, request)
        self.git(self.repo, 'update-ref', temporary, self.ref('HEAD'))
        with self.assertRaises(ValueError):
            self.execute(task, request)
        self.assertIsNotNone(self.ref(temporary))
        self.assertIsNotNone(task_store.read_task(self.ws))


if __name__ == '__main__':
    unittest.main()

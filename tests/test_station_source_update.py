"""空闲工位真实 Git 快进与拒绝覆盖回归。"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_station_lifecycle as fixture
from workflow import station_source_update as refresh


class SourceUpdateTests(unittest.TestCase):
    setUp = fixture.StationTests.setUp
    prepare_engineering = fixture.StationTests.prepare_engineering
    write = fixture.StationTests.write
    git = fixture.StationTests.git
    takeover = fixture.StationTests.takeover

    def prepare(self):
        self.prepare_engineering()
        self.name = 'tapdata/tapdata'
        self.repo = self.ws / 'source' / self.name
        self.repo.parent.mkdir(parents=True)
        self.git(self.root, 'clone', str(self.remote), str(self.repo))
        self.git(self.repo, 'config', 'user.name', 'Test')
        self.git(self.repo, 'config', 'user.email', 'test@example.invalid')

    def advance(self):
        (self.seed / 'file.txt').write_text('updated\n')
        self.git(self.seed, 'commit', '-am', 'advance')
        self.git(self.seed, 'push', str(self.remote), 'develop')
        return self.git(self.seed, 'rev-parse', 'HEAD')

    def test_fast_forward_and_repeat(self):
        self.prepare()
        target = self.advance()
        result = refresh.update(self.ws)
        self.assertEqual(result['status'], 'done')
        self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), target)
        self.assertEqual((self.repo / 'file.txt').read_text(), 'updated\n')
        self.assertEqual(refresh.update(self.ws, self.name)['repositories'][self.name]['status'], 'unchanged')

    def test_dirty_and_ignored_preserved(self):
        self.prepare()
        before = self.git(self.repo, 'rev-parse', 'HEAD')
        self.advance()
        (self.repo / 'file.txt').write_text('local')
        self.assertEqual(refresh.update(self.ws)['status'], 'failed')
        self.assertEqual((self.repo / 'file.txt').read_text(), 'local')
        self.git(self.repo, 'restore', 'file.txt')
        (self.repo / '.git/info/exclude').write_text('cache\n')
        (self.repo / 'cache').write_text('preserve')
        self.assertEqual(refresh.update(self.ws)['status'], 'failed')
        self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), before)

    def test_divergence_preserved(self):
        self.prepare()
        (self.repo / 'local').write_text('local')
        self.git(self.repo, 'add', 'local')
        self.git(self.repo, 'commit', '-m', 'local')
        before = self.git(self.repo, 'rev-parse', 'HEAD')
        self.advance()
        self.assertEqual(refresh.update(self.ws)['status'], 'failed')
        self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), before)

    def test_active_task_rejected(self):
        self.takeover()
        with self.assertRaisesRegex(ValueError, '未接管'):
            refresh.update(self.ws)

    def test_unknown_repository_rejected(self):
        with self.assertRaisesRegex(ValueError, '未在项目'):
            refresh.update(self.ws, 'unknown/repository')

    def test_selected_and_partial_batch(self):
        self.prepare()
        other_name = 'tapdata/tapdata-common-lib'
        other = self.ws / 'source' / other_name
        self.git(self.root, 'clone', str(self.remote), str(other))
        before = self.git(other, 'rev-parse', 'HEAD')
        target = self.advance()
        self.assertEqual(refresh.update(self.ws, self.name)['status'], 'done')
        self.assertEqual(self.git(other, 'rev-parse', 'HEAD'), before)
        (self.repo / 'file.txt').write_text('local')
        result = refresh.update(self.ws)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['repositories'][other_name]['status'], 'updated')
        self.assertEqual(self.git(other, 'rev-parse', 'HEAD'), target)

    def test_branch_switch_during_fetch_does_not_update_wrong_branch(self):
        self.prepare()
        before = self.git(self.repo, 'rev-parse', 'HEAD')
        self.advance()
        original = refresh.source.git
        def switching(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if args[0] == 'fetch':
                self.git(self.repo, 'checkout', '-b', 'other', before)
            return result
        with mock.patch.object(refresh.source, 'git', side_effect=switching):
            self.assertEqual(refresh.update(self.ws)['status'], 'failed')
        self.assertEqual(self.git(self.repo, 'rev-parse', 'other'), before)

    def test_pending_operation_rejected(self):
        self.prepare()
        with mock.patch.object(refresh.station_operation, 'read', return_value={'status': 'running'}):
            with self.assertRaisesRegex(ValueError, '未完成操作'):
                refresh.update(self.ws)


if __name__ == '__main__':
    unittest.main()

"""原客户端定点、维护补丁和有限续接的真实 Git 验证。"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bootstrap import installation_update as update, product_state


class InstallationUpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ao-installation-update-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.seed, self.install = self.root / 'seed', self.root / 'install'
        self.seed.mkdir()
        self.git(self.seed, 'init', '-q', '-b', 'channel')
        self.git(self.seed, 'config', 'user.name', 'Fixture')
        self.git(self.seed, 'config', 'user.email', 'fixture@example.test')
        (self.seed / '.gitignore').write_text('.local/\n')
        (self.seed / 'README.md').write_text('original client without new entry\n')
        (self.seed / 'contracts').mkdir()
        self.manifest = self.seed / 'contracts/station-state-compatibility.json'
        self.manifest.write_text(json.dumps({'station_state_epoch': 27}))
        self.original = self.commit('original')
        (self.seed / 'README.md').write_text('compatible fix\n')
        self.patch = self.commit('compatible fix')
        self.manifest.write_text(json.dumps({'station_state_epoch': 28}))
        self.new = self.commit('incompatible next version')
        self.git(self.root, 'clone', '-q', str(self.seed), str(self.install))
        self.git(self.install, 'checkout', '-q', '--detach', self.original)
        product_state.save(self.install, {'schema_version': 2, 'mode': 'installed',
            'repository': str(self.seed), 'tracking_branch': 'channel', 'current_ref': self.original,
            'previous_ref': None, 'source_pool': str(self.root / 'pool')})
        self.registry = self.install / '.local/stations.json'
        self.registry.write_text(json.dumps({'schema_version': 1, 'stations': []}))
        self.args = SimpleNamespace(mode='update', to_ref=self.patch, expected_current_ref=self.original,
            source_branch=None, check=False, resume=False, writers_stopped=False)

    def git(self, root, *args):
        result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout.strip()

    def commit(self, message):
        self.git(self.seed, 'add', '.')
        self.git(self.seed, 'commit', '-qm', message)
        return self.git(self.seed, 'rev-parse', 'HEAD')

    def bound(self):
        station = self.root / 'old-station'
        state = station / '.agenticops'
        state.mkdir(parents=True)
        (state / 'current-task.json').write_text('opaque old task; updater must not parse')
        self.registry.write_text(json.dumps({'schema_version': 1, 'stations': [str(station)]}))
        return state / 'current-task.json'

    def test_pinned_old_epoch_before_cross_epoch_tip_and_first_old_client(self):
        task = self.bound()
        before = task.read_bytes()
        document = product_state.load(self.install)
        self.args.check = True
        result = update.execute(self.install, self.args)
        self.assertEqual((27, 27), (result['original_epoch'], result['target_epoch']))
        self.assertEqual(self.original, self.git(self.install, 'rev-parse', 'HEAD'))
        self.assertEqual(document, product_state.load(self.install))
        self.args.check = False
        with self.assertRaisesRegex(ValueError, '停写'):
            update.execute(self.install, self.args)
        self.args.writers_stopped = True
        self.assertEqual('updated', update.execute(self.install, self.args)['status'])
        self.assertEqual(before, task.read_bytes())
        self.assertEqual(self.patch, self.git(self.install, 'rev-parse', 'HEAD'))
        self.args.expected_current_ref = self.patch
        self.assertEqual('unchanged', update.execute(self.install, self.args)['status'])
        self.assertEqual(self.original, product_state.load(self.install)['previous_ref'])
        self.args.to_ref = self.new
        with self.assertRaisesRegex(ValueError, '仍有绑定工位'):
            update.execute(self.install, self.args)

    def maintenance(self):
        self.git(self.seed, 'checkout', '-qb', 'maintenance', self.original)
        (self.seed / 'README.md').write_text('old maintenance branch fix')
        reference = self.commit('maintenance fix')
        self.git(self.seed, 'checkout', '-q', 'channel')
        return reference

    def test_explicit_maintenance_keeps_channel_and_rejects_normal_divergence(self):
        target = self.maintenance()
        self.args.to_ref = target
        with self.assertRaises(ValueError):
            update.execute(self.install, self.args)
        self.args.mode, self.args.source_branch = 'recover-install', 'maintenance'
        update.execute(self.install, self.args)
        self.assertEqual('channel', product_state.load(self.install)['tracking_branch'])
        self.assertEqual(target, self.git(self.install, 'rev-parse', 'HEAD'))
        self.args.mode, self.args.source_branch = 'update', None
        self.args.to_ref, self.args.expected_current_ref = self.new, target
        with self.assertRaises(ValueError):
            update.execute(self.install, self.args)

    def test_recovery_checks_every_mainline_epoch_and_missing_registry(self):
        self.git(self.seed, 'checkout', '-qb', 'maintenance', self.new)
        self.manifest.write_text(json.dumps({'station_state_epoch': 27}))
        target = self.commit('reverted epoch is not compatible history')
        self.args.mode, self.args.source_branch, self.args.to_ref = 'recover-install', 'maintenance', target
        with self.assertRaisesRegex(ValueError, '路径经过'):
            update.execute(self.install, self.args)
        self.args.to_ref = self.patch
        self.registry.unlink()
        with self.assertRaisesRegex(ValueError, '登记清单缺失'):
            update.execute(self.install, self.args)

    def test_switch_then_record_interruption_reuses_same_mode_and_previous_ref(self):
        with mock.patch.object(product_state, 'save', side_effect=OSError('record interrupted')):
            with self.assertRaises(OSError):
                update.execute(self.install, self.args)
        self.assertEqual(self.patch, self.git(self.install, 'rev-parse', 'HEAD'))
        self.assertEqual(self.original, product_state.load(self.install)['current_ref'])
        with self.assertRaisesRegex(ValueError, 'HEAD'):
            update.execute(self.install, self.args)
        self.args.resume = True
        update.execute(self.install, self.args)
        before = product_state.load(self.install)
        update.execute(self.install, self.args)
        self.assertEqual(before, product_state.load(self.install))
        self.assertEqual(self.original, before['previous_ref'])
        product_state.save(self.install, dict(before, previous_ref=self.new))
        with self.assertRaisesRegex(ValueError, 'previous_ref'):
            update.execute(self.install, self.args)

    def test_pre_switch_resume_and_illegal_reversed_or_third_values(self):
        with mock.patch.object(update, 'refresh_scope', side_effect=OSError('scope interrupted')):
            with self.assertRaises(OSError):
                update.execute(self.install, self.args)
        self.args.resume = True
        update.execute(self.install, self.args)
        self.git(self.install, 'checkout', '-q', '--detach', self.original)
        with self.assertRaisesRegex(ValueError, '非法'):
            update.execute(self.install, self.args)
        self.git(self.install, 'checkout', '-q', '--detach', self.new)
        with self.assertRaisesRegex(ValueError, '第三值'):
            update.execute(self.install, self.args)

    def test_dirty_origin_unreachable_and_unsupported_configuration_reject(self):
        (self.install / 'dirty').write_text('preserve')
        with self.assertRaisesRegex(ValueError, '未提交'):
            update.execute(self.install, self.args)
        (self.install / 'dirty').unlink()
        self.git(self.install, 'remote', 'set-url', 'origin', str(self.root / 'other'))
        with self.assertRaisesRegex(ValueError, 'origin'):
            update.execute(self.install, self.args)
        self.git(self.install, 'remote', 'set-url', 'origin', str(self.seed))
        self.args.to_ref = '0' * 40
        with self.assertRaises(ValueError):
            update.execute(self.install, self.args)
        path = product_state.state_path(self.install)
        document = product_state.load(self.install)
        document['schema_version'] = 99
        path.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, '结构无效'):
            update.execute(self.install, self.args)


if __name__ == '__main__':
    unittest.main()

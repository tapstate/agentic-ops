"""原客户端定点、维护补丁和有限续接的真实 Git 验证。"""
from contextlib import redirect_stderr, redirect_stdout
import io
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

    def partial_install(self, middle_epoch=27):
        """file:// 强制走 upload-pack；配置服务端 filter，并证明缺失对象而非静默完整克隆。"""
        self.git(self.seed, 'checkout', '-qB', 'channel', self.original)
        (self.seed / 'docs').mkdir()
        (self.seed / 'docs/old.md').write_text('current version newly included by scope refresh\n')
        (self.seed / 'skills').mkdir()
        (self.seed / 'skills/README.md').write_text('original cone ancestor file\n')
        self.original = self.commit('original sparse scope')
        self.manifest.write_text(json.dumps({'station_state_epoch': middle_epoch}, indent=2))
        middle = self.commit('distinct intermediate manifest')
        self.manifest.write_text(json.dumps({'station_state_epoch': 27}))
        (self.seed / 'bootstrap').mkdir()
        (self.seed / 'bootstrap/install.sh').write_text('new installer content\n')
        (self.seed / 'skills/shared/example').mkdir(parents=True)
        (self.seed / 'skills/shared/example/SKILL.md').write_text('shared skill\n')
        (self.seed / 'skills/README.md').write_text('cone ancestor file\n')
        (self.seed / 'internal').mkdir()
        (self.seed / 'internal/excluded.txt').write_text('must stay missing\n')
        target = self.commit('target product')
        self.git(self.seed, 'branch', 'initial', self.original)
        self.git(self.seed, 'config', 'uploadpack.allowFilter', 'true')
        remote = self.seed.as_uri()
        self.install = self.root / 'partial'
        self.git(self.root, 'clone', '-q', '--filter=blob:none', '--no-checkout', '--single-branch',
                 '--branch', 'initial', remote, str(self.install))
        self.assertEqual('true', self.git(self.install, 'config', '--get', 'remote.origin.promisor'))
        self.assertEqual('blob:none', self.git(self.install, 'config', '--get', 'remote.origin.partialclonefilter'))
        # 正式验收环境也禁止按需下载；夹具的原版安装必须显式准备自己的根文件和清单。
        initial_objects = [self.git(self.seed, 'rev-parse', self.original + ':' + path)
                           for path in ('.gitignore', 'README.md', update.station_compatibility.MANIFEST_PATH)]
        self.git(self.install, 'fetch', '--no-tags', '--no-write-fetch-head', 'origin', *initial_objects)
        self.git(self.install, 'sparse-checkout', 'set', 'contracts')
        self.git(self.install, 'checkout', '-q', 'initial')
        product_state.save(self.install, {'schema_version': 2, 'mode': 'installed',
            'repository': remote, 'tracking_branch': 'channel', 'current_ref': self.original,
            'previous_ref': None, 'source_pool': str(self.root / 'pool')})
        self.registry = self.install / '.local/stations.json'
        self.registry.write_text(json.dumps({'schema_version': 1, 'stations': []}))
        self.args.to_ref, self.args.expected_current_ref = target, self.original
        update.git(self.install, 'fetch', 'origin', 'refs/heads/channel:refs/remotes/origin/channel')
        required = [self.git(self.seed, 'rev-parse', reference + ':' + path) for reference, path in (
            (target, 'bootstrap/install.sh'), (middle, update.station_compatibility.MANIFEST_PATH),
            (self.original, 'docs/old.md'), (target, 'skills/README.md'),
            (self.original, 'skills/README.md'),
            (target, 'skills/shared/example/SKILL.md'))]
        excluded = self.git(self.seed, 'rev-parse', target + ':internal/excluded.txt')
        self.assertEqual(sorted(required), update.missing_blobs(self.install, sorted(required)))
        self.assertEqual([excluded], update.missing_blobs(self.install, [excluded]))
        return target, required, excluded

    def test_real_partial_clone_precheck_then_switch_without_lazy_fetch(self):
        target, required, excluded = self.partial_install()
        document = product_state.load(self.install)
        scope = (self.install / '.git/info/sparse-checkout').read_bytes()
        output, diagnostic = io.StringIO(), io.StringIO()
        self.args.check = True
        with redirect_stdout(output), redirect_stderr(diagnostic), mock.patch.object(
                update.subprocess, 'run', wraps=subprocess.run) as runner:
            self.assertEqual('check', update.execute(self.install, self.args)['status'])
        git_calls = [call for call in runner.call_args_list if call.args[0][0] == 'git']
        self.assertTrue(git_calls)
        self.assertTrue(all(call.kwargs['env']['GIT_NO_LAZY_FETCH'] == '1' for call in git_calls))
        self.assertIn('补齐版本材料', diagnostic.getvalue())
        self.assertIn('预检通过，本次未执行版本切换', diagnostic.getvalue())
        self.assertEqual([], update.missing_blobs(self.install, sorted(required)))
        self.assertEqual([excluded], update.missing_blobs(self.install, [excluded]))
        self.assertEqual(self.original, update.git(self.install, 'rev-parse', 'HEAD'))
        self.assertEqual(document, product_state.load(self.install))
        self.assertEqual(scope, (self.install / '.git/info/sparse-checkout').read_bytes())
        self.assertFalse((self.install / 'docs').exists())
        self.args.check = False
        with redirect_stdout(output), redirect_stderr(diagnostic):
            self.assertEqual('updated', update.execute(self.install, self.args)['status'])
        self.assertEqual(target, product_state.load(self.install)['current_ref'])
        self.assertEqual(target, update.git(self.install, 'rev-parse', 'HEAD'))
        for path in ('docs/old.md', 'bootstrap/install.sh', 'skills/README.md', 'skills/shared/example/SKILL.md'):
            self.assertEqual((self.seed / path).read_bytes(), (self.install / path).read_bytes())
        self.assertEqual([excluded], update.missing_blobs(self.install, [excluded]))
        self.assertIn('安装目录已更新：分支=', output.getvalue())
        self.assertNotIn('branch=', output.getvalue())
        self.assertEqual('1', update.git_environment()['GIT_NO_LAZY_FETCH'])

    def test_real_partial_clone_material_fetch_failure_preserves_install_and_task(self):
        _, required, _ = self.partial_install()
        task = self.bound()
        before, document = task.read_bytes(), product_state.load(self.install)
        scope = (self.install / '.git/info/sparse-checkout').read_bytes()
        original_git = update.git
        offline = self.root / 'offline'

        def disconnect(root, *arguments, **kwargs):
            if arguments[:2] == ('fetch', '--no-tags'):
                self.seed.rename(offline)
            return original_git(root, *arguments, **kwargs)

        try:
            with mock.patch.object(update, 'git', side_effect=disconnect):
                with self.assertRaisesRegex(ValueError, '版本材料补齐失败') as failure:
                    update.execute(self.install, self.args)
            self.assertIn('下载版本材料失败', str(failure.exception))
            self.assertIn('Git 原始诊断', str(failure.exception))
        finally:
            if offline.exists():
                offline.rename(self.seed)
        self.assertEqual(self.original, update.git(self.install, 'rev-parse', 'HEAD'))
        self.assertEqual(document, product_state.load(self.install))
        self.assertEqual(scope, (self.install / '.git/info/sparse-checkout').read_bytes())
        self.assertEqual(before, task.read_bytes())
        self.assertEqual(sorted(required), update.missing_blobs(self.install, sorted(required)))
        self.assertFalse((self.install / '.local/lifecycle.lock').exists())
        self.args.writers_stopped = True
        self.assertEqual('updated', update.execute(self.install, self.args)['status'])
        self.assertEqual(before, task.read_bytes())

    def test_real_partial_clone_intermediate_epoch_still_rejects(self):
        self.partial_install(middle_epoch=28)
        document = product_state.load(self.install)
        with self.assertRaisesRegex(ValueError, '路径经过不同 epoch'):
            update.execute(self.install, self.args)
        self.assertEqual(self.original, update.git(self.install, 'rev-parse', 'HEAD'))
        self.assertEqual(document, product_state.load(self.install))

    def test_real_partial_clone_success_receipt_without_objects_still_rejects(self):
        self.partial_install()
        document = product_state.load(self.install)
        original_git = update.git

        def no_objects(root, *arguments, **kwargs):
            if arguments[:2] == ('fetch', '--no-tags'):
                return ''
            return original_git(root, *arguments, **kwargs)

        with mock.patch.object(update, 'git', side_effect=no_objects):
            with self.assertRaisesRegex(ValueError, '补齐后仍缺少文件对象'):
                update.execute(self.install, self.args)
        self.assertEqual(self.original, update.git(self.install, 'rev-parse', 'HEAD'))
        self.assertEqual(document, product_state.load(self.install))

    def test_real_partial_clone_cross_epoch_bound_station_still_rejects(self):
        target, _, _ = self.partial_install()
        self.manifest.write_text(json.dumps({'station_state_epoch': 28}))
        self.args.to_ref = self.commit('cross epoch target')
        task = self.bound()
        before = task.read_bytes()
        with self.assertRaisesRegex(ValueError, '仍有绑定工位'):
            update.execute(self.install, self.args)
        self.assertEqual(self.original, update.git(self.install, 'rev-parse', 'HEAD'))
        self.assertEqual(before, task.read_bytes())
        self.assertNotEqual(target, self.args.to_ref)

    def test_real_partial_clone_maintenance_recovery_keeps_channel(self):
        target, _, _ = self.partial_install()
        self.git(self.seed, 'branch', 'maintenance', target)
        self.args.mode, self.args.source_branch = 'recover-install', 'maintenance'
        self.assertEqual('updated', update.execute(self.install, self.args)['status'])
        self.assertEqual('channel', product_state.load(self.install)['tracking_branch'])
        self.assertEqual('', update.git(self.install, 'branch', '--show-current'))

    def test_real_partial_clone_default_update_and_same_ref_scope_refresh(self):
        target, _, _ = self.partial_install()
        self.args.to_ref = self.args.expected_current_ref = None
        self.assertEqual('updated', update.execute(self.install, self.args)['status'])
        before = product_state.load(self.install)
        self.assertEqual(target, before['current_ref'])
        self.assertEqual('unchanged', update.execute(self.install, self.args)['status'])
        self.assertEqual(before, product_state.load(self.install))

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

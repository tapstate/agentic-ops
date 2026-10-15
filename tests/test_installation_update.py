"""原客户端定点、维护补丁和有限续接的真实 Git 验证。"""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
import shutil
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

    def partial_install(self, merged=False):
        """file:// 强制走 upload-pack；配置服务端 filter，并证明缺失对象而非静默完整克隆。"""
        self.git(self.seed, 'checkout', '-qB', 'channel', self.original)
        (self.seed / 'docs').mkdir()
        (self.seed / 'docs/old.md').write_text('current version newly included by scope refresh\n')
        (self.seed / 'skills').mkdir()
        (self.seed / 'skills/README.md').write_text('original cone ancestor file\n')
        self.original = self.commit('original sparse scope')
        self.manifest.write_text(json.dumps({'station_state_epoch': 27}, indent=2))
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
        if merged:
            # 原安装在第二父链：模拟 main 合并 develop 后再同步回 develop。
            self.git(self.seed, 'checkout', '-qb', 'release', self.original + '^')
            (self.seed / 'release-note').write_text('release branch change\n')
            self.commit('release first parent')
            self.git(self.seed, 'merge', '-q', '--no-ff', 'channel', '-m', 'merge channel')
            target = self.git(self.seed, 'rev-parse', 'HEAD')
            self.git(self.seed, 'checkout', '-q', 'channel')
            self.git(self.seed, 'merge', '-q', '--ff-only', 'release')
            self.assertNotIn(self.original, self.git(self.seed, 'rev-list', '--first-parent', target).splitlines())
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
            (target, 'bootstrap/install.sh'),
            (self.original, 'docs/old.md'), (target, 'skills/README.md'),
            (self.original, 'skills/README.md'),
            (target, 'skills/shared/example/SKILL.md'))]
        intermediate = self.git(self.seed, 'rev-parse', middle + ':' + update.station_compatibility.MANIFEST_PATH)
        self.assertEqual([intermediate], update.missing_blobs(self.install, [intermediate]))
        excluded = self.git(self.seed, 'rev-parse', target + ':internal/excluded.txt')
        self.assertEqual(sorted(required), update.missing_blobs(self.install, sorted(required)))
        self.intermediate_manifest = intermediate
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
        self.assertEqual([self.intermediate_manifest], update.missing_blobs(self.install, [self.intermediate_manifest]))
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

    def test_real_partial_clone_second_parent_updates_and_recovery(self):
        target, required, excluded = self.partial_install(merged=True)
        task = self.bound()
        before = task.read_bytes()
        document = product_state.load(self.install)
        for mode in ('default', 'pinned', 'recover-install'):
            with self.subTest(mode=mode):
                self.git(self.install, 'checkout', '-q', '--detach', self.original)
                product_state.save(self.install, document)
                self.args.mode = 'recover-install' if mode == 'recover-install' else 'update'
                self.args.source_branch = 'channel' if mode == 'recover-install' else None
                self.args.to_ref = None if mode == 'default' else target
                self.args.expected_current_ref = None if mode == 'default' else self.original
                self.args.writers_stopped = True
                self.args.check = True
                self.assertEqual('check', update.execute(self.install, self.args)['status'])
                self.assertEqual(self.original, update.git(self.install, 'rev-parse', 'HEAD'))
                self.args.check = False
                self.assertEqual('updated', update.execute(self.install, self.args)['status'])
                self.assertEqual(target, product_state.load(self.install)['current_ref'])
                self.assertEqual(self.original, product_state.load(self.install)['previous_ref'])
                self.assertEqual('channel', product_state.load(self.install)['tracking_branch'])
                self.assertEqual(before, task.read_bytes())
        self.assertEqual([], update.missing_blobs(self.install, sorted(required)))
        self.assertEqual([excluded], update.missing_blobs(self.install, [excluded]))

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

    def test_recovery_rejects_cross_epoch_and_missing_registry(self):
        self.args.mode, self.args.source_branch = 'recover-install', 'channel'
        self.args.to_ref = self.new
        with self.assertRaisesRegex(ValueError, '当前=27，目标=28.*请选择'):
            update.execute(self.install, self.args)
        self.args.to_ref = self.patch
        self.registry.unlink()
        with self.assertRaisesRegex(ValueError, '登记清单缺失'):
            update.execute(self.install, self.args)

    def test_cli_failure_reports_reason_versions_and_next_step(self):
        (self.install / 'dirty').write_text('preserve')
        diagnostic = io.StringIO()
        with redirect_stderr(diagnostic):
            self.assertEqual(2, update.main(['--product-root', str(self.install)]))
        message = diagnostic.getvalue()
        for text in ('未提交修改', 'git status', '保全修改', str(self.install),
                     'HEAD=' + self.original, '产品记录=' + self.original,
                     'docs/usage/update-and-rollback.md', '不代表更新成功或已回退'):
            self.assertIn(text, message)

    def test_cli_interrupted_switch_does_not_claim_unchanged(self):
        diagnostic = io.StringIO()
        with mock.patch.object(product_state, 'save', side_effect=OSError('record interrupted')):
            with redirect_stderr(diagnostic):
                self.assertEqual(2, update.main(['--product-root', str(self.install),
                    '--to-ref', self.patch, '--expected-current-ref', self.original]))
        message = diagnostic.getvalue()
        self.assertIn('HEAD=' + self.patch, message)
        self.assertIn('产品记录=' + self.original, message)
        self.assertIn('--resume', message)
        self.assertIn('不要直接重试无参 update', message)
        self.assertNotIn('本次未执行版本切换', message)

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


class SourceFreeRecoveryTests(unittest.TestCase):
    """直接执行公开命令；客户端仅有旧安装，产品文件只能由独立 Git 远端下载。"""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ao-source-free-recovery-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.seed = self.root / 'server'
        self.seed.mkdir()
        self.git(self.seed, 'init', '-q', '-b', 'channel')
        self.git(self.seed, 'config', 'user.name', 'Fixture')
        self.git(self.seed, 'config', 'user.email', 'fixture@example.test')
        for directory in update.INSTALL_PATHS:
            shutil.copytree(ROOT / directory, self.seed / directory,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.local'))
        shutil.copytree(ROOT / 'skills/shared', self.seed / 'skills/shared')
        for name in ('agenticops', '.gitignore'):
            shutil.copy2(ROOT / name, self.seed / name)
        # 仅在夹具旧客户端中重现已删除的第一父链检查；不依赖测试仓库的 Git 历史。
        updater = self.seed / 'bootstrap/installation_update.py'
        fixed = updater.read_text()
        old_check = """    if before == after:
        mainline = git(root, 'rev-list', '--first-parent', target).splitlines()
        if original not in mainline:
            raise ValueError('原安装不在恢复主线上，不能证明连续兼容')
"""
        updater.write_text(fixed.replace('    station_compatibility.check_upgrade(root, original, target)',
                                        old_check + '    station_compatibility.check_upgrade(root, original, target)'))
        base = self.commit('base with old updater')
        (self.seed / 'old-release-note').write_text('old installed version')
        self.original = self.commit('old installed version')
        self.git(self.seed, 'checkout', '-qb', 'release', base)
        (self.seed / 'release-note').write_text('first parent release')
        self.commit('release first parent')
        self.git(self.seed, 'merge', '-q', '--no-ff', 'channel', '-m', 'merge old channel')
        updater.write_text(fixed)
        self.target = self.commit('reviewed updater fix')
        self.git(self.seed, 'checkout', '-q', 'channel')
        self.git(self.seed, 'merge', '-q', '--ff-only', 'release')
        self.remote = self.seed.as_uri()
        self.home = self.root / 'user-home'
        self.home.mkdir()
        self.work = self.home / 'work'
        self.work.mkdir()
        self.install = self.home / '.agentic-ops'
        self.downloads = self.home / 'tmp'
        self.downloads.mkdir()
        self.git(self.home, 'clone', '-q', '--no-checkout', '--single-branch',
                 '--branch', 'channel', self.remote, str(self.install))
        self.git(self.install, 'sparse-checkout', 'set', *update.INSTALL_PATHS, 'skills/shared')
        self.git(self.install, 'checkout', '-q', '--detach', self.original)
        product_state.save(self.install, {'schema_version': 2, 'mode': 'installed',
            'repository': self.remote, 'tracking_branch': 'channel', 'current_ref': self.original,
            'previous_ref': None, 'source_pool': str(self.home / 'source-pool')})
        self.station = self.home / 'station'
        for name, content in (('.agenticops/current-task.json', 'opaque active task'),
                              ('source/repository/file', 'uncommitted user work'),
                              ('config/environment', 'persistent configuration')):
            path = self.station / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        self.registry = self.install / '.local/stations.json'
        self.registry.write_text(json.dumps({'schema_version': 1, 'stations': [str(self.station)]}))
        archive = self.install / '.archive/run/snapshot'
        archive.parent.mkdir(parents=True)
        archive.write_text('formal archive')
        self.protected = {path: path.read_bytes() for path in (
            self.registry, archive, self.station / '.agenticops/current-task.json',
            self.station / 'source/repository/file', self.station / 'config/environment')}
        self.document = product_state.load(self.install)
        self.env = dict(os.environ, HOME=str(self.home), TMPDIR=str(self.downloads),
                        PYTHONPATH='', PYTHONDONTWRITEBYTECODE='1', GIT_NO_LAZY_FETCH='1')
        self.env.pop('AGENTIC_OPS_HOME', None)
        self.env.pop('GIT_DIR', None)
        self.env.pop('GIT_WORK_TREE', None)
        self.env.pop('GIT_INDEX_FILE', None)
        guide = (ROOT / 'docs/usage/update-and-rollback.md').read_text()
        section = guide.split('<!-- ao-one-time-update-recovery:start -->', 1)[1].split(
            '<!-- ao-one-time-update-recovery:end -->', 1)[0]
        self.command = section.split('```sh\n', 1)[1].split('```', 1)[0]
        pinned = '2d962efe1ccf53deac16e7b011e6b451a244eed2'
        self.assertEqual(1, self.command.count(pinned))
        # 唯一替换项是测试远端生成的等价固定候选 SHA；不替换下载/核验/确认/应用命令。
        self.command = self.command.replace(pinned, self.target)
        self.assertFalse((self.home / 'source').exists())
        self.assertFalse((self.install / 'internal').exists())
        self.assertNotIn(self.original, self.git(self.seed, 'rev-list', '--first-parent', self.target).splitlines())

    def git(self, root, *arguments):
        result = subprocess.run(['git', '-C', str(root), *arguments], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout.strip()

    def commit(self, message):
        self.git(self.seed, 'add', '.')
        self.git(self.seed, 'commit', '-qm', message)
        return self.git(self.seed, 'rev-parse', 'HEAD')

    def run_command(self, answer='yes\n'):
        return subprocess.run(['bash', '-c', self.command], cwd=self.work, env=self.env,
                              input=answer, capture_output=True, text=True, timeout=120)

    def assert_preserved(self, switched=False):
        for path, content in self.protected.items():
            self.assertEqual(content, path.read_bytes(), str(path))
        self.assertEqual([], list(self.downloads.glob('agenticops-recover.*')))
        self.assertFalse((self.install / '.local/lifecycle.lock').exists())
        self.assertEqual(self.target if switched else self.original,
                         self.git(self.install, 'rev-parse', 'HEAD'))
        if not switched:
            self.assertEqual(self.document, product_state.load(self.install))

    def test_only_old_install_recovers_then_normal_update_works(self):
        old = subprocess.run([str(self.install / 'agenticops'), 'update'], cwd=self.work,
                             env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(2, old.returncode, old.stderr)
        self.assertIn('原安装不在恢复主线上', old.stderr)
        result = self.run_command()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('预检通过', result.stderr)
        self.assertIn('一次性恢复][完成', result.stdout)
        document = product_state.load(self.install)
        self.assertEqual(self.target, document['current_ref'])
        self.assertEqual(self.original, document['previous_ref'])
        self.assertEqual(self.document['tracking_branch'], document['tracking_branch'])
        self.assertEqual(self.document['source_pool'], document['source_pool'])
        self.assert_preserved(switched=True)
        # 远端后续同 epoch 提交只能由原安装的普通 update 获取，无临时源码辅助。
        (self.seed / 'release-note').write_text('next compatible update')
        next_ref = self.commit('next normal update')
        normal = subprocess.run([str(self.install / 'agenticops'), 'update'], cwd=self.work,
                                env=self.env, capture_output=True, text=True, timeout=120)
        self.assertEqual(0, normal.returncode, normal.stderr)
        self.assertEqual(next_ref, product_state.load(self.install)['current_ref'])
        self.assertEqual(self.target, product_state.load(self.install)['previous_ref'])
        for path, content in self.protected.items():
            self.assertEqual(content, path.read_bytes())

    def test_interrupted_switch_keeps_download_and_prints_working_resume(self):
        updater = self.seed / 'bootstrap/installation_update.py'
        source = updater.read_text()
        point = "        if target != original and document['current_ref'] != target:"
        source = source.replace(point, """        interruption = root / '.local/test-record-interruption'
        if interruption.exists():
            interruption.unlink()
            raise OSError('simulated record interruption')
""" + point)
        updater.write_text(source)
        previous_target = self.target
        self.target = self.commit('fixture interrupts once before record')
        self.command = self.command.replace(previous_target, self.target)
        (self.install / '.local/test-record-interruption').touch()
        result = self.run_command()
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertEqual(self.target, self.git(self.install, 'rev-parse', 'HEAD'))
        self.assertEqual(self.original, product_state.load(self.install)['current_ref'])
        self.assertIn('临时产品保留在', result.stderr)
        resume = [line for line in result.stderr.splitlines() if line.startswith('python3 ')][-1]
        self.assertIn('--resume', resume)
        self.assertIn(self.original, resume)
        self.assertIn(self.target, resume)
        recovered = subprocess.run(['bash', '-c', resume], cwd=self.work, env=self.env,
                                   capture_output=True, text=True, timeout=120)
        self.assertEqual(0, recovered.returncode, recovered.stderr)
        self.assertEqual(self.target, product_state.load(self.install)['current_ref'])
        self.assertEqual(self.original, product_state.load(self.install)['previous_ref'])
        for path, content in self.protected.items():
            self.assertEqual(content, path.read_bytes())
        retained = list(self.downloads.glob('agenticops-recover.*'))
        self.assertEqual(1, len(retained))
        shutil.rmtree(retained[0])
        self.assert_preserved(switched=True)

    def test_cancel_or_eof_never_applies(self):
        for answer in ('no\n', ''):
            with self.subTest(answer=answer):
                result = self.run_command(answer)
                self.assertEqual(0 if answer else 1, result.returncode, result.stderr)
                self.assertNotIn('一次性恢复][完成', result.stdout)
                self.assert_preserved()

    def test_download_failure_preserves_old_install(self):
        self.seed.rename(self.root / 'offline-server')
        result = self.run_command()
        self.assertNotEqual(0, result.returncode)
        self.assertIn('从原可信来源获取固定修复版本', result.stderr)
        self.assert_preserved()

    def test_origin_mismatch_stops_before_download(self):
        self.git(self.install, 'remote', 'set-url', 'origin', str(self.root / 'other'))
        result = self.run_command()
        self.assertNotEqual(0, result.returncode)
        self.assertIn('核验原安装身份', result.stderr)
        self.assert_preserved()

    def test_unpublished_pin_stops_without_switch(self):
        self.command = self.command.replace(self.target, '0' * 40)
        result = self.run_command()
        self.assertNotEqual(0, result.returncode)
        self.assertIn('确认已发布到原通道', result.stderr)
        self.assert_preserved()

    def test_dirty_install_stops_before_download(self):
        (self.install / 'user-note').write_text('preserve local changes')
        result = self.run_command()
        self.assertNotEqual(0, result.returncode)
        self.assert_preserved()
        self.assertEqual('preserve local changes', (self.install / 'user-note').read_text())


if __name__ == '__main__':
    unittest.main()

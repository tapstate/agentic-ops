"""环境配置布局、保全、转换与失败恢复回归。"""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from workflow.environment_config import Store, write, read, inventory, check_clear
from projects.tapdata.scripts.test_environment import Environment, RUNTIME_FIELDS
from projects.tapdata.scripts.environment_inputs import prepare, LEGACY_PATHS
from workflow import task_store


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.station = Path(self.temp.name).resolve()
        write(self.station / '.agenticops/station.json', {'schema_version': 4, 'project': 'tapdata', 'station_id': 'a' * 32, 'product_root': str(ROOT), 'source_pool': str(self.station / 'pool'), 'agents': ['codex'], 'branch_identity': {'schema_version': 1, 'git_name': 'Test', 'source': 'git_global_user_name'}})
        write(self.station / '.agenticops/init.json', read(ROOT / 'contracts/station-state-compatibility.json'))
        task_store.initialize_current(self.station)
        self.env = Environment(self.station)
        self.store = self.env.store
        self.config = read(ROOT / 'projects/tapdata/templates/station/test-env.json')
        self.config['platform'] = 'linux/arm64'
        self.config['mongo_uri_file'] = 'tapdata-test-env/dev/mongodb-uri'
        self.config['ports']['node1']['license'] = 'secrets/tapdata-test-env/dev/license.txt'

    def legacy(self, second=False):
        write(self.store.root / 'tapdata-test-env/dev.json', self.config)
        if second:
            write(self.store.root / 'tapdata-test-env/drafts/dev.json', self.config)
        secret = self.store.root / 'secrets/tapdata-test-env/dev'
        secret.mkdir(parents=True, exist_ok=True)
        for name, content in [('mongodb-uri', 'mongodb://user:private@mongo:27017/db'), ('license.txt', 'private-license')]:
            (secret / name).write_text(content)
            (secret / name).chmod(0o600)

    def test_backup_preserves_bytes_and_conversion_occurs_only_on_prepare(self):
        self.legacy()
        before = inventory(self.store.root / 'tapdata-test-env')
        destination = Path(self.store.backup_legacy(LEGACY_PATHS))
        self.assertEqual(before, inventory(destination / 'tapdata-test-env'))
        self.assertFalse(self.store.environments.exists())
        result = self.env.prepare_inputs('dev')
        self.assertEqual(result['missing'], [])
        self.assertEqual(self.env.names(), [])
        draft = read(Path(result['config_file']))
        self.assertEqual(draft['mongo_uri_file'], 'environments/dev/drafts/secrets/mongodb-uri')
        self.assertEqual((self.store.root / draft['mongo_uri_file']).read_text(), 'mongodb://user:private@mongo:27017/db')
        self.assertNotIn('user:private', json.dumps(result))
        self.assertNotIn('private-license', json.dumps(result))
        self.assertEqual(inventory(destination / 'tapdata-test-env'), before)

    def test_prepare_preserves_unrelated_configuration_and_secrets(self):
        self.legacy()
        unrelated = ('jira/rest-auth.json', 'secrets/other/token', 'unknown/input.json')
        for relative in unrelated:
            write(self.store.root / relative, {'private': relative})
        before = {relative: inventory(self.store.root / relative) for relative in unrelated}
        result = self.env.prepare_inputs('dev')
        self.assertEqual(result['missing'], [])
        self.assertFalse((self.store.root / 'tapdata-test-env').exists())
        self.assertFalse((self.store.root / 'secrets/tapdata-test-env').exists())
        for relative in unrelated:
            self.assertEqual(before[relative], inventory(self.store.root / relative))
            self.assertFalse(any((batch / relative).exists() for batch in (self.store.root / 'backups').iterdir()))

    def test_unrelated_configuration_does_not_block_active_environment(self):
        write(self.store.root / 'jira/rest-auth.json', {'private': 'test'})
        write(self.env.root / 'active.json', {'environment': 'other'})
        original = inventory(self.store.root / 'jira')
        result = self.env.prepare_inputs('dev')
        self.assertTrue(result['missing'])
        self.assertEqual(original, inventory(self.store.root / 'jira'))
        self.assertFalse((self.store.root / 'backups').exists())

    def test_active_environment_still_protects_legacy_inputs(self):
        self.legacy()
        write(self.env.root / 'active.json', {'environment': 'dev'})
        before = inventory(self.store.root)
        with self.assertRaisesRegex(ValueError, '存在活动现场'):
            self.env.prepare_inputs('other')
        self.assertEqual(before, inventory(self.store.root))

    def test_backup_rejects_overlapping_or_managed_paths_without_changes(self):
        self.legacy()
        before = inventory(self.store.root)
        for paths in [('secrets', 'secrets/tapdata-test-env'), ('../outside',), ('environments/dev',), ('backups',)]:
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                self.store.backup_legacy(paths)
            self.assertEqual(before, inventory(self.store.root))

    def test_old_broad_pending_backup_is_preserved_without_removing_unrelated_files(self):
        self.legacy()
        write(self.store.root / 'jira/rest-auth.json', {'private': 'test'})
        write(self.store.root / '.backup-pending.json', {
            'id': 'old', 'entries': {'jira': inventory(self.store.root / 'jira')}})
        before = inventory(self.store.root)
        with self.assertRaisesRegex(ValueError, '超出当前功能范围'):
            self.store.backup_legacy(LEGACY_PATHS)
        self.assertEqual(before, inventory(self.store.root))

    def test_invalid_saved_contract_can_prepare_draft_without_overwriting_original(self):
        self.legacy()
        prepared = self.env.prepare_inputs('dev')
        draft = Path(prepared['config_file'])
        prep = draft.parent / 'preparation.json'
        write(prep, read(ROOT / 'projects/tapdata/templates/station/test-env-preparation.json'))
        self.env.configure('dev', draft, prep)
        formal = self.store.environment('dev') / 'config.json'
        saved = read(formal)
        saved['schema_version'] = 99
        write(formal, saved)
        original = formal.read_bytes()
        repaired = self.env.prepare_inputs('dev')
        self.assertFalse(repaired['applied'])
        self.assertEqual(original, formal.read_bytes())
        candidate = Path(repaired['config_file'])
        self.assertNotEqual(candidate, formal)
        self.assertEqual(99, read(candidate)['schema_version'])
        # Agent 已核实字段含义且研发确认后修正草稿，再由原功能校验并发布。
        value = read(candidate)
        value['schema_version'] = 2
        write(candidate, value)
        self.env.check_inputs(candidate)
        self.env.configure('dev', candidate)
        self.assertEqual(2, read(formal)['schema_version'])

    def test_multiple_candidates_require_selection_and_preserve_existing_values(self):
        self.legacy(second=True)
        result = self.env.prepare_inputs('dev')
        self.assertEqual(result['conversion'], 'selection_required')
        self.assertFalse((self.store.environment('dev') / 'drafts/config.json').exists())
        chosen = self.env.prepare_inputs('dev', backup=result['candidates'][0])
        draft = Path(chosen['config_file'])
        config = read(draft)
        config['java']['tm'] = 'user-input'
        write(draft, config)
        again = self.env.prepare_inputs('dev')
        self.assertEqual(read(draft)['java']['tm'], 'user-input')
        self.assertEqual(again['reused'], [])

    def test_backup_copy_failure_retains_original_and_ignores_incomplete_batch(self):
        self.legacy()
        before = inventory(self.store.root)
        with mock.patch('workflow.environment_config.shutil.copytree', side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                self.store.backup_legacy(LEGACY_PATHS)
        self.assertEqual(inventory(self.store.root / 'tapdata-test-env'), {k[len('tapdata-test-env/'):]: v for k,v in before.items() if k.startswith('tapdata-test-env/') } | {'.': None})
        self.assertTrue((self.store.root / 'tapdata-test-env/dev.json').exists())

    def test_backup_resume_after_partial_removal(self):
        self.legacy()
        original = Path.unlink
        stopped = []
        def fail_once(file, *args, **kwargs):
            if file.name == 'dev.json' and not stopped:
                stopped.append(True)
                raise OSError('interrupted')
            return original(file, *args, **kwargs)
        with mock.patch.object(Path, 'unlink', fail_once):
            with self.assertRaises(OSError):
                self.store.backup_legacy(LEGACY_PATHS)
        batch = self.store.backup_legacy(LEGACY_PATHS)
        self.assertTrue((Path(batch) / '.complete.json').exists())
        self.assertIsNone(self.store.backup_legacy(LEGACY_PATHS))

    def test_save_combination_rolls_back_on_failure_and_recovers_interruption(self):
        self.legacy()
        result = self.env.prepare_inputs('dev')
        draft = Path(result['config_file'])
        prep = draft.parent / 'preparation.json'
        write(prep, read(ROOT / 'projects/tapdata/templates/station/test-env-preparation.json'))
        before = inventory(self.store.environment('dev'))
        original = os.replace
        def fail_ready(source, dest):
            if Path(source).name == '.dev-ready':
                raise OSError('interrupted')
            return original(source, dest)
        with mock.patch('workflow.environment_config.os.replace', side_effect=fail_ready):
            with self.assertRaises(OSError):
                self.env.configure('dev', draft, prep)
        self.assertEqual(before, inventory(self.store.environment('dev')))
        self.env.configure('dev', draft, prep)
        self.assertEqual(self.env.names(), ['dev'])
        saved = read(self.store.environment('dev') / 'config.json')
        self.assertNotIn('/drafts/', saved['mongo_uri_file'])
        self.assertFalse(draft.exists())
        target = self.store.environment('dev')
        original(target, target.with_name('.dev-previous'))
        self.store.recover('dev')
        self.assertTrue((target / 'config.json').exists())

    def test_scope_and_link_protection_and_shared_references(self):
        for ref in ('backups/old/secret', 'environments/other/secrets/key', '../secret', '/tmp/secret', 'environments/dev/drafts/secrets/key'):
            with self.assertRaises(ValueError):
                self.store.reference(ref, name='dev', secret=True)
        key = self.store.root / 'shared/secrets/key'
        key.parent.mkdir(parents=True)
        key.symlink_to('/tmp')
        with self.assertRaises(ValueError):
            self.store.reference('shared/secrets/key', name='dev', secret=True)
        key.unlink()
        key.write_text('private')
        write(self.store.environment('dev') / 'drafts/config.json', {'key': 'shared/secrets/key'})
        with self.assertRaisesRegex(ValueError, '引用'):
            check_clear(self.station, ['config/shared/secrets/key'])
        with self.assertRaises(ValueError):
            check_clear(self.station, ['config/backups'])


if __name__ == '__main__':
    unittest.main()

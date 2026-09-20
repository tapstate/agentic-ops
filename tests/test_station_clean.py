"""名单判定及版本 4 清理真实闭环；不执行构建工具。"""
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow import station_clean_rules as rules, station_clean, station_source_reset
from workflow import station_resources as resources, station, task_store, station_operation
import test_station_resources as fixture


class StationCleanTests(unittest.TestCase):
    setUp = fixture.ResourceTests.setUp
    prepare_engineering = fixture.ResourceTests.prepare_engineering
    write = fixture.ResourceTests.write
    git = fixture.ResourceTests.git
    takeover = fixture.ResourceTests.takeover
    ready = fixture.ResourceTests.ready
    execute = fixture.ResourceTests.execute

    def cleanup_request(self, task):
        return dict(summary='清理测试', reason='用户确认停止', decision_ref='fixture:user',
                    cleanup_version=4, abandon_changes=True,
                    confirmed_digest=resources.plan(self.ws, task, version=4)['digest'])

    def project_rules(self, preserve=(), clean=()):
        self.write(self.product/'projects/tapdata/station-clean.json',
                   dict(version=1, preserve=list(preserve), clean=list(clean)))

    def test_priority_and_unmatched(self):
        self.project_rules(['cache/'], [{'pattern':'source/', 'action':'remove'}, {'pattern':'scratch/', 'action':'remove'}])
        config = rules.load(self.ws)
        for name, expected in [('config', 'preserve'), ('cache', 'preserve'), ('source', 'source-reset'), ('scratch', 'remove'), ('unknown', 'block')]:
            with self.subTest(name=name):
                self.assertEqual(expected, rules.classify(config, name, True)['action'])

    def test_whitelist_exception_and_duplicate_rules(self):
        self.project_rules(['cache/'], [{'pattern': 'c*', 'action': 'remove'}])
        self.assertEqual('preserve', rules.classify(rules.load(self.ws), 'cache', True)['action'])
        self.project_rules(['cache/', 'cache/'])
        with self.assertRaisesRegex(ValueError, '重复'):
            rules.load(self.ws)

    def test_legacy_inventory_does_not_read_new_rules(self):
        self.ready()
        self.project_rules(['source/'])
        resources.verify_station_inventory(self.ws)
        with self.assertRaisesRegex(ValueError, '生命周期'):
            rules.inspect(self.ws)

    def test_snapshot_versions_and_types(self):
        for plan in ({'schema_version': 4}, {'schema_version': 3, 'rules': {}},
                     {'schema_version': 4, 'rules': {'digests': [], 'objects': {}}},
                     {'schema_version': 4, 'rules': {'digests': ['a'*64, None], 'objects': {'x': {'action': 'remove', 'layer': 'invalid'}}}}):
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                rules.validate_snapshot(plan)

    def test_invalid_patterns_and_core_conflicts(self):
        for value in ('**', '../x', '/x', 'a/b', '!x', '[ab]', '', '.'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rules.pattern(value)
        self.project_rules(['source/'])
        with self.assertRaisesRegex(ValueError, '生命周期冲突'):
            rules.inspect(self.ws)

    def test_unknown_and_unowned_clean_objects_block(self):
        self.project_rules(clean=[{'pattern':'scratch/', 'action':'remove'}])
        (self.ws/'scratch').mkdir()
        (self.ws/'unknown').write_text('keep')
        with self.assertRaisesRegex(ValueError, '归属.*未知'):
            rules.inspect(self.ws)

    def test_modern_clean_archives_source_before_clearing_runtime(self):
        task = self.ready()
        (self.repo/'file.txt').write_text('saved change')
        (self.ws/'.idea').mkdir()
        (self.ws/'.idea/station.xml').write_text('keep')
        (self.ws/'runtime/report').write_text('temporary')
        original = resources.neutral
        def observe(*args):
            self.assertTrue((self.ws/'runtime/report').exists())
            self.assertTrue(task_store.read_task(self.ws)['archive_ref'])
            return original(*args)
        with mock.patch.object(resources, 'neutral', side_effect=observe) as called:
            self.execute(task, self.cleanup_request(task))
            self.assertEqual(1, called.call_count)
        self.assertIsNone(task_store.read_task(self.ws))
        self.assertEqual('keep', (self.ws/'.idea/station.xml').read_text())
        self.assertEqual([], list((self.ws/'runtime').iterdir()))
        self.assertEqual('baseline', (self.repo/'file.txt').read_text().strip())

    def test_no_abandon_is_zero_write(self):
        self.ready()
        before = {p.relative_to(self.ws):p.read_bytes() for p in (self.ws/'.agenticops').rglob('*') if p.is_file()}
        with mock.patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(0, station_clean.main(['--dir', str(self.ws), '--abandon-changes', 'no']))
        after = {p.relative_to(self.ws):p.read_bytes() for p in (self.ws/'.agenticops').rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_cli_confirmed_cleanup(self):
        task = self.ready()
        request = self.root/'cleanup-request.json'
        self.write(request, self.cleanup_request(task))
        with mock.patch('sys.stdout', new_callable=io.StringIO) as output:
            station_clean.main(['--dir', str(self.ws), '--issue-key', task['issue_key'],
                                  '--expected-run-id', task['run_id'], '--expected-revision', str(task['_revision']),
                                  '--operation-id', 'op-cli-cleanup', '--input', str(request), '--abandon-changes', 'yes'])
        self.assertEqual('done', json.loads(output.getvalue())['status'])
        self.assertIsNone(task_store.read_task(self.ws))

    def test_cli_failed_takeover_handoff_and_resume(self):
        self.prepare_engineering()
        with mock.patch.object(station.source, 'prepare_repositories', side_effect=ValueError('network')), self.assertRaises(ValueError):
            station.takeover(self.ws, self.request, 'op-takeover-fail', 0)
        task = task_store.read_task(self.ws)
        before = station_operation.path(self.ws).read_bytes()
        with mock.patch('sys.stdout', new_callable=io.StringIO) as output:
            station_clean.main(['--dir', str(self.ws), '--abandon-changes', 'no'])
        self.assertEqual('cancelled', json.loads(output.getvalue())['status'])
        self.assertEqual(before, station_operation.path(self.ws).read_bytes())
        with mock.patch('sys.stdout', new_callable=io.StringIO) as output:
            station_clean.main(['--dir', str(self.ws)])
        self.assertIn('cleanup_plan', json.loads(output.getvalue()))
        path = self.root/'handoff-request.json'
        self.write(path, self.cleanup_request(task))
        args = ['--dir', str(self.ws), '--issue-key', task['issue_key'], '--expected-run-id', task['run_id'],
                '--expected-revision', str(task['_revision']), '--operation-id', 'op-clean-handoff',
                '--input', str(path), '--abandon-changes', 'yes']
        original = station_operation.save
        def fail(base, operation):
            if operation['kind'] == 'clean':
                raise OSError('after handoff')
            return original(base, operation)
        with mock.patch.object(station_operation, 'save', side_effect=fail), self.assertRaises(OSError):
            station_clean.main(args)
        changed = json.loads(path.read_text())
        changed['reason'] = 'different'
        self.write(path, changed)
        with self.assertRaisesRegex(ValueError, '原清理交接'):
            station_clean.main(args)
        changed['reason'] = '用户确认停止'
        self.write(path, changed)
        with mock.patch('sys.stdout', new_callable=io.StringIO):
            station_clean.main(args)
        operation = station_operation.read(self.ws)
        self.assertEqual(4, operation['cleanup_plan']['schema_version'])
        self.assertEqual('done', operation['status'])

    def test_cli_takeover_before_task_write_requires_original_resume(self):
        self.prepare_engineering()
        with mock.patch.object(task_store, 'write_task', side_effect=OSError('before task')), self.assertRaises(OSError):
            station.takeover(self.ws, self.request, 'op-takeover-fail', 0)
        operation = station_operation.read(self.ws)
        before = station_operation.path(self.ws).read_bytes()
        for extra in ([], ['--abandon-changes', 'no']):
            with mock.patch('sys.stdout', new_callable=io.StringIO) as output:
                station_clean.main(['--dir', str(self.ws)] + extra)
            self.assertEqual('resume_required', json.loads(output.getvalue())['status'])
        with self.assertRaisesRegex(ValueError, '尚未绑定任务'):
            station_clean.main(['--dir', str(self.ws), '--issue-key', self.request['issue_key'],
                                  '--expected-run-id', operation['run_id'], '--expected-revision', '0',
                                  '--operation-id', 'op-no-task-clean', '--input', str(self.root/'missing.json')])
        self.assertEqual(before, station_operation.path(self.ws).read_bytes())

    def test_registered_root_removed_by_same_directory_mechanism(self):
        task = self.ready()
        self.project_rules(clean=[{'pattern':'scratch/', 'action':'remove'}])
        resources.register(self.ws, task['issue_key'], task['run_id'], [{'kind':'directory','path':'scratch','producer':'fixture'}])
        (self.ws/'scratch/generated').write_text('output')
        self.execute(task, self.cleanup_request(task))
        self.assertFalse((self.ws/'scratch').exists())

    def test_build_root_must_be_removed_before_plan(self):
        task = self.ready()
        resources.register(self.ws,task['issue_key'],task['run_id'],[{'kind':'directory','path':'source/'+self.name+'/target','producer':'fixture'}])
        with self.assertRaisesRegex(ValueError, '原生工具'):
            self.cleanup_request(task)
        (self.repo/'target').rmdir()
        plan = resources.plan(self.ws,task,version=4)
        self.assertTrue(next(e for e in plan['directories'] if e['kind']=='source-generated')['observed_missing_before_intent'])
        self.execute(task,self.cleanup_request(task))

    def test_changed_config_blocks_without_operation(self):
        task = self.ready()
        request = self.cleanup_request(task)
        self.project_rules(['.vscode/'])
        with self.assertRaisesRegex(ValueError, '确认'):
            self.execute(task,request)
        self.assertEqual('scope_change', station_operation.read(self.ws)['kind'])

    def test_source_reset_requires_archive(self):
        task = self.ready()
        with self.assertRaises(ValueError):
            station_source_reset.run(self.ws,task['issue_key'],task['run_id'],task['_revision'],'op-invalid-reset')

    def test_resume_after_source_reset_does_not_repeat_git_restore(self):
        task = self.ready()
        (self.repo/'file.txt').write_text('saved')
        request = self.cleanup_request(task)
        original = resources.clean
        def fail(*args, **kwargs):
            if kwargs.get('directories_only'):
                raise ValueError('fixture interruption')
            return original(*args, **kwargs)
        with mock.patch.object(resources, 'clean', side_effect=fail):
            with self.assertRaisesRegex(ValueError,'fixture interruption'):
                self.execute(task,request)
        self.execute(task,request)
        self.assertIsNone(task_store.read_task(self.ws))

    def interrupted_before_directories(self):
        task = self.ready()
        request = self.cleanup_request(task)
        original = resources.clean
        def fail(*args, **kwargs):
            if kwargs.get('directories_only'):
                raise ValueError('fixture interruption')
            return original(*args, **kwargs)
        with mock.patch.object(resources, 'clean', side_effect=fail), self.assertRaises(ValueError):
            self.execute(task, request)
        return task, request

    def test_pending_no_reports_resume_not_zero_write(self):
        self.interrupted_before_directories()
        with mock.patch('sys.stdout', new_callable=io.StringIO) as output:
            station_clean.main(['--dir', str(self.ws), '--abandon-changes', 'no'])
        self.assertEqual('resume_required', json.loads(output.getvalue())['status'])

    def test_preview_returns_task_question_with_blockers(self):
        task = self.ready()
        (self.ws/'unknown').write_text('keep')
        with mock.patch('sys.stdout', new_callable=io.StringIO) as output:
            station_clean.main(['--dir', str(self.ws)])
        result = json.loads(output.getvalue())
        self.assertEqual(task['run_id'], result['run_id'])
        self.assertTrue(result['question'])
        self.assertIn('未知', result['blockers'][0])
        self.assertNotIn('cleanup_plan', result)

    def test_resume_rejects_ignored_files_and_preserved_ref_drift(self):
        task, request = self.interrupted_before_directories()
        (self.repo/'.git/info/exclude').write_text('ignored-output\n')
        (self.repo/'ignored-output').write_text('keep')
        with self.assertRaisesRegex(ValueError, 'ignored'):
            self.execute(task, request)
        (self.repo/'ignored-output').unlink()
        plan = station_operation.read(self.ws)['cleanup_plan']
        self.git(self.repo, 'update-ref', '-d', plan['source'][self.name]['preserved_ref'])
        with self.assertRaises(ValueError):
            self.execute(task, request)
        self.assertIsNotNone(task_store.read_task(self.ws))

    def test_all_root_identities_checked_before_git(self):
        task = self.ready()
        (self.repo/'file.txt').write_text('keep')
        request = self.cleanup_request(task)
        original = station_source_reset.apply
        def replace(*args):
            (self.ws/'runtime').rename(self.root/'old-runtime')
            (self.ws/'runtime').mkdir()
            return original(*args)
        with mock.patch.object(station_source_reset, 'apply', side_effect=replace), self.assertRaisesRegex(ValueError, '身份'):
            self.execute(task, request)
        self.assertEqual('keep', (self.repo/'file.txt').read_text())

    reset_request = cleanup_request
    reset_amend_after_neutral = fixture.ResourceTests.reset_amend_after_neutral

    def test_modern_clean_amend_after_neutral(self):
        self.reset_amend_after_neutral('clean')

    def test_modern_release_amend_after_neutral(self):
        self.reset_amend_after_neutral('release')


if __name__ == '__main__':
    unittest.main()

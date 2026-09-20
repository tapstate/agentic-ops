"""名单判定及版本 4 清理真实闭环；不执行构建工具。"""
import io
import hashlib
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

    def test_native_project_script_keeps_original_command_and_digest(self):
        from workflow import native_cleanup
        recipe = {"kind": "project-script", "script": "scripts/clean-t-layer3-test.py", "source_ref": "fixture"}
        script = (self.product / "projects/tapdata" / recipe["script"]).resolve()
        paths = ["source/tapdata/t-layer3-test/target"]
        command = native_cleanup.commands(self.ws, {}, "tapdata/t-layer3-test", recipe, paths)
        self.assertEqual(command, {"cwd": "source/tapdata/t-layer3-test",
            "argv": ["python3", str(script), "--repository", str((self.ws / "source/tapdata/t-layer3-test").resolve())],
            "inputs": {str(script): hashlib.sha256(script.read_bytes()).hexdigest()},
            "paths": paths, "source_ref": "fixture"})

    def test_native_project_script_rejects_parent_and_file_links(self):
        from workflow import native_cleanup
        project = self.product / "projects/tapdata"
        scripts = project / "scripts"
        saved = project / "scripts-original"
        scripts.rename(saved)
        scripts.symlink_to(saved, target_is_directory=True)
        recipe = {"kind": "project-script", "script": "scripts/clean-t-layer3-test.py", "source_ref": "fixture"}
        with self.assertRaisesRegex(ValueError, "父目录为链接"):
            native_cleanup.commands(self.ws, {}, "tapdata/t-layer3-test", recipe, [])
        scripts.unlink(); saved.rename(scripts)
        script = scripts / "clean-t-layer3-test.py"
        original = scripts / "original.py"
        script.rename(original); script.symlink_to(original)
        with self.assertRaisesRegex(ValueError, "不是普通文件"):
            native_cleanup.commands(self.ws, {}, "tapdata/t-layer3-test", recipe, [])

    def test_native_cleanup_rejects_linked_project_before_reading_recipe(self):
        from workflow import native_cleanup
        project = self.product / "projects/tapdata"
        target = self.product / "relocated-project"
        project.rename(target); project.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "项目适配目录"):
            native_cleanup.configuration(self.ws)
        with self.assertRaisesRegex(ValueError, "项目适配目录"):
            native_cleanup.commands(self.ws, {}, "tapdata/t-layer3-test",
                {"kind": "project-script", "script": "scripts/clean-t-layer3-test.py", "source_ref": "fixture"}, [])

    def test_native_configuration_validates_before_inspection(self):
        import copy
        from workflow import native_cleanup
        path = self.product / 'projects/tapdata/repo-cleanup.json'
        original = json.loads(path.read_text())
        invalid = [[], None, {'schema_version': True, 'repositories': {}},
                   {'schema_version': 1, 'repositories': []}, {'schema_version': 1},
                   dict(original, unexpected=True)]
        for field, values in {'kind': [[], 'unknown'], 'generated': [[], 'target', [None], ['../target'], ['/target'], ['a/**x']],
                              'reports': [None, ['/report'], ['../report'], [3]], 'source_ref': ['', None]}.items():
            for value in values:
                config = copy.deepcopy(original)
                config['repositories']['tapdata/tapdata'][field] = value
                invalid.append(config)
        for script in (None, '../escape.py', 'scripts/../escape.py', '/scripts/clean.py'):
            config = copy.deepcopy(original)
            config['repositories']['tapdata/t-layer3-test']['script'] = script
            invalid.append(config)
        invalid.append({'schema_version': 1, 'repositories': {'../escape': original['repositories']['tapdata/tapdata']}})
        invalid.append({'schema_version': 1, 'repositories': {'owner/repo': []}})
        for value in invalid:
            with self.subTest(value=value):
                path.write_text(json.dumps(value))
                with mock.patch.object(native_cleanup.source, 'repository_path') as source_path:
                    with self.assertRaises(ValueError):
                        native_cleanup.inspect(self.ws, {}, {}, preserve=True)
                    source_path.assert_not_called()
        for raw in ('{"schema_version": 1, "schema_version": 1, "repositories": {}}',
                    '{"schema_version": 1, "repositories": {"owner/repo": {}, "owner/repo": {}}}'):
            path.write_text(raw)
            with self.assertRaisesRegex(ValueError, '重复 JSON 键'):
                native_cleanup.configuration(self.ws)

    def test_native_configuration_keeps_original_bytes_digest(self):
        from workflow import native_cleanup
        path = self.product / 'projects/tapdata/repo-cleanup.json'
        raw = path.read_bytes()
        root, value, digest = native_cleanup.configuration(self.ws)
        self.assertEqual(root, self.product.resolve())
        self.assertEqual(value, json.loads(raw))
        self.assertEqual(digest, hashlib.sha256(raw).hexdigest())
        self.assertEqual(raw, path.read_bytes())
        native_cleanup.validate_configuration({'schema_version': 1, 'repositories': {
            'owner/repo': {'kind': 'maven', 'source_ref': 'fixture', 'generated': ['**/target'], 'reports': []}}})

    def cleanup_request(self, task):
        return dict(summary='清理测试', reason='用户确认停止', decision_ref='fixture:user',
                    cleanup_version=4, abandon_changes=True,
                    confirmed_digest=resources.plan(self.ws, task, version=4)['digest'])

    def project_rules(self, preserve=(), clean=()):
        self.write(self.product/'projects/tapdata/station-clean.json',
                   dict(version=1, preserve=list(preserve), clean=list(clean)))

    def native_fixture(self, web=False):
        from workflow import native_cleanup
        task = self.ready()
        (self.repo / '.gitignore').write_text('target/\nnode_modules/\n')
        if web:
            (self.repo / 'package.json').write_text(json.dumps({'packageManager': 'pnpm@10.32.1', 'scripts': {'clean': 'rm -rf node_modules'}}))
            config_path = self.product / 'projects/tapdata/repo-cleanup.json'
            config = json.loads(config_path.read_text())
            config['repositories'][self.name] = dict(config['repositories']['tapdata/tapdata-web'])
            config_path.write_text(json.dumps(config))
        else:
            (self.repo / 'pom.xml').write_text('<project/>')
        root = 'node_modules' if web else 'target'
        resources.register(self.ws, task['issue_key'], task['run_id'], [{'kind': 'directory', 'producer': 'fixture-native', 'path': 'source/' + self.name + '/' + root}])
        report = self.repo / root / 'surefire-reports' / 'TEST-fixture.xml'
        report.parent.mkdir(); report.write_text('<testsuite tests="1"/>')
        with self.assertRaisesRegex(ValueError, '尚未保全'):
            resources.plan(self.ws, task, version=5)
        result = native_cleanup.preserve(self.ws, task['issue_key'], task['run_id'])
        self.assertFalse(result['problems'], result)
        plan = resources.plan(self.ws, task, version=5)
        request = dict(summary='原生清理与证据保全', reason='结束本轮', decision_ref='fixture:once', cleanup_version=5,
                       abandon_changes=True, confirmed_digest=plan['digest'])
        return task, plan, request, root

    def test_native_maven_preserves_reports_before_cleanup_and_reuses_confirmation(self):
        import shutil
        from workflow import native_cleanup
        task, plan, request, root = self.native_fixture()
        command = plan['native_clean']['repositories'][self.name]
        self.assertEqual(['mvn', '-Dmaven.repo.local=' + str((self.ws / 'runtime/maven-local').resolve()), 'clean'], command['argv'])
        operation = self.execute(task, request)
        self.assertEqual('awaiting_native_clean', operation['phase'])
        self.assertIsNone(task_store.read_task(self.ws)['archive_ref'])
        for row in plan['native_clean']['reports'].values():
            self.assertTrue((self.ws / row['backup']).is_file())
        # Fixture 模拟原生 mvn clean 效果；产品本身不执行构建工具。
        shutil.rmtree(self.repo / root)
        result = native_cleanup.receipt(self.ws, task['issue_key'], task['run_id'], 'op-resource-reset',
                                       {self.name: {'exit_code': 0, 'source_ref': 'fixture:mvn-clean-exit'}})
        self.assertEqual([], result['problems'])
        self.assertEqual(plan['digest'], resources.plan(self.ws, task_store.read_task(self.ws), version=5)['digest'])
        self.execute(task, request)
        self.assertIsNone(task_store.read_task(self.ws))
        record = next((self.ws / 'archive').rglob('runtime-evidence.json'))
        self.assertIn('TEST-fixture.xml', record.read_text())
        self.assertEqual('baseline', (self.repo / 'file.txt').read_text().strip())

    def test_native_web_receipts_aggregate_failure_residue_and_source_drift(self):
        from workflow import native_cleanup
        task, plan, request, root = self.native_fixture(web=True)
        self.assertEqual(['pnpm', 'run', 'clean'], plan['native_clean']['repositories'][self.name]['argv'])
        self.execute(task, request)
        (self.repo / 'file.txt').write_text('unexpected source change')
        result = native_cleanup.receipt(self.ws, task['issue_key'], task['run_id'], 'op-resource-reset',
            {self.name: {'exit_code': 1, 'source_ref': 'fixture:pnpm-failed'}})
        self.assertTrue(any('残留' in p for p in result['problems']))
        self.assertTrue(any('源码指纹' in p for p in result['problems']))
        self.assertTrue(any('未成功' in p for p in result['problems']))
        self.assertIsNotNone(task_store.read_task(self.ws))

    def test_native_changed_script_and_unknown_generated_paths_fail_together(self):
        from workflow import native_cleanup, station_directories
        task, plan, request, root = self.native_fixture(web=True)
        (self.repo / 'package.json').write_text(json.dumps({'packageManager': 'pnpm@10.32.1', 'scripts': {}}))
        (self.repo / 'nested/node_modules').mkdir(parents=True)
        (self.repo / 'nested/node_modules/file').write_text('new object')
        value, errors = native_cleanup.inspect(self.ws, task, station_directories.load(self.ws, task), previous=plan['native_clean'])
        self.assertTrue(any('没有已声明' in p for p in errors))
        self.assertTrue(any('未登记目录' in p for p in errors))
        self.assertTrue(any('脚本或 Git' in p for p in errors))

    def test_project_clean_scripts_preserve_sources_and_reject_tracked_candidates(self):
        import subprocess
        for name in ('tapdata-application', 't-layer3-test'):
            with self.subTest(repository=name):
                repo = self.ws.parent / name
                repo.mkdir()
                self.git(repo, 'init')
                (repo / 'source.txt').write_text('keep')
                self.git(repo, 'add', 'source.txt')
                (repo / 'target').mkdir()
                (repo / 'target/result').write_text('generated')
                script = fixture.ROOT / 'projects/tapdata/scripts' / ('clean-' + name + '.py')
                command = [sys.executable, str(script), '--repository', str(repo)]
                subprocess.run(command, check=True, capture_output=True)
                self.assertFalse((repo / 'target').exists())
                self.assertEqual('keep', (repo / 'source.txt').read_text())
                (repo / 'target').mkdir()
                (repo / 'target/tracked').write_text('source')
                self.git(repo, 'add', 'target/tracked')
                result = subprocess.run(command, capture_output=True)
                self.assertNotEqual(0, result.returncode)
                self.assertTrue((repo / 'target/tracked').exists())

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

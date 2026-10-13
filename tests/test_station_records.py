"""两类任务档案、真实覆盖和独立批量源码保全。"""
import json
import unittest
from unittest import mock

import test_station_resources as fixture
from workflow import station, station_records as records, station_resources as resources, station_export, archive_store, task_store


class RecordTests(unittest.TestCase):
    setUp = fixture.ResourceTests.setUp
    prepare_engineering = fixture.ResourceTests.prepare_engineering
    write = fixture.ResourceTests.write
    git = fixture.ResourceTests.git
    takeover = fixture.ResourceTests.takeover
    ready = fixture.ResourceTests.ready
    reset_request = fixture.ResourceTests.reset_request

    def conversation(self, task, coverage='complete', source_kind='user_export'):
        value = {'schema_version': 1, 'kind': 'conversation', 'issue_key': task['issue_key'], 'run_id': task['run_id'],
                 'platform': 'fixture', 'thread_id': 'conversation-1', 'source_ref': 'fixture:export', 'cutoff': 'message:1',
                 'coverage': coverage, 'source_kind': source_kind, 'first': 0, 'last': 1, 'missing': 'only summaries available',
                 'messages': [{'role': 'user', 'text': '保存任务\n状态', 'order': 0}, {'role': 'assistant', 'text': '已记录', 'order': 1}]}
        self.write(task_store.state_path(self.ws) / 'evidence/conversation.json', value)
        self.write(task_store.state_path(self.ws) / 'evidence/conversation-scope.json',
            {'schema_version': 1, 'kind': 'conversation-scope', 'issue_key': task['issue_key'], 'run_id': task['run_id'],
             'sources': [{k: value[k] for k in ('platform', 'thread_id', 'source_ref', 'cutoff', 'first', 'last')}]})
        scope_path = task_store.state_path(self.ws) / 'evidence/conversation-scope.json'
        scope = json.loads(scope_path.read_text())
        scope['sources'][0].update(boundary_ref='fixture:export-boundary', source_kind='user_export', missing='只取得摘要' if coverage != 'complete' else '')
        self.write(scope_path, scope)
        return value

    def test_only_two_content_types_and_large_runtime_not_archived(self):
        task = self.ready()
        value = self.conversation(task)
        value['messages'][0]['attachments'] = [{'source_ref': 'fixture:attachment', 'title': 'report', 'data': 'BINARY-ATTACHMENT'}]
        self.write(task_store.state_path(self.ws)/'evidence/conversation.json', value)
        logs = self.ws / 'runtime/logs'
        logs.mkdir(exist_ok=True)
        with (logs/'large.bin').open('wb') as stream:
            stream.truncate(17*1024*1024)
        request = self.reset_request(task)
        request['require_complete_conversation'] = True
        result = station.execute(self.ws, 'clean', task['issue_key'], task['run_id'], task['_revision'], 'op-records-clean', request)
        directory = archive_store.from_reference(self.ws, result['archive_ref'])
        evidence = json.loads((directory/'evidence.json').read_text())
        self.assertEqual('complete', evidence['conversation_coverage']['coverage'])
        self.assertIn('\n', evidence['conversation.json']['messages'][0]['text'])
        self.assertEqual('fixture:attachment', evidence['conversation.json']['messages'][0]['attachments'][0]['source_ref'])
        self.assertNotIn('BINARY-ATTACHMENT', (directory/'evidence.json').read_text())
        self.assertEqual({}, json.loads((directory/'runtime-evidence.json').read_text())['files'])
        self.assertFalse(any((self.ws/'runtime').iterdir()))
        self.assertIsNone(task_store.read_task(self.ws))
        self.assertEqual('incomplete', json.loads((directory/'record.json').read_text())['task_result'])

    def test_required_complete_missing_record_refuses_before_operation_mutation(self):
        task = self.ready()
        request = self.reset_request(task)
        request['require_complete_conversation'] = True
        request.pop('accept_conversation_digest', None)
        before = (self.ws/'.agenticops/operation.json').read_bytes()
        with self.assertRaisesRegex(ValueError, '完整会话不可取得'):
            station.execute(self.ws, 'clean', task['issue_key'], task['run_id'], task['_revision'], 'op-records-required', request)
        self.assertEqual(before, (self.ws/'.agenticops/operation.json').read_bytes())

    def test_declared_second_source_missing_and_fixed_cutoff(self):
        task = self.ready()
        self.conversation(task)
        path = task_store.state_path(self.ws) / 'evidence/conversation-scope.json'
        scope = json.loads(path.read_text())
        scope['sources'].append(dict(scope['sources'][0], thread_id='conversation-2'))
        self.write(path, scope)
        snapshot = records.conversations(self.ws, task)
        self.assertEqual('partial', snapshot['coverage'])
        with self.assertRaisesRegex(ValueError, '完整会话'):
            records.verify_decision(self.ws, task, {'decision_ref': 'fixture:user'})
        request = {'decision_ref': 'fixture:accept-current-cutoff', 'accept_conversation_digest': snapshot['digest']}
        records.verify_decision(self.ws, task, request)
        scope['sources'][1]['cutoff'] = 'message:2'
        self.write(path, scope)
        with self.assertRaises(ValueError):
            records.verify_decision(self.ws, task, request)

    def test_large_control_json_uses_control_reader_not_conversation_quota(self):
        from workflow import station_archive
        task = self.ready()
        self.conversation(task)
        control = {'schema_version': 1, 'run_id': task['run_id'], 'entries': [],
                   'diagnostic': 'x' * (17 * 1024 * 1024)}
        self.write(task_store.state_path(self.ws) / 'evidence/resources.json', control)
        self.assertEqual('complete', records.conversations(self.ws, task)['coverage'])
        body = station_archive._evidence(self.ws, task)
        self.assertLess(len(body), 1024 * 1024)
        self.assertNotIn(control['diagnostic'].encode(), body)
        control['run_id'] = 'run-other'
        self.write(task_store.state_path(self.ws) / 'evidence/resources.json', control)
        with self.assertRaises(ValueError):
            station_archive._evidence(self.ws, task)

    def test_summary_cannot_be_complete_and_partial_requires_bound_acceptance(self):
        task = self.ready()
        self.conversation(task, source_kind='turn_summary')
        with self.assertRaisesRegex(ValueError, '摘要'):
            records.conversations(self.ws, task)
        self.conversation(task, coverage='partial', source_kind='turn_summary')
        request = {'require_complete_conversation': True, 'decision_ref': 'fixture:user'}
        with self.assertRaisesRegex(ValueError, '接受'):
            records.verify_decision(self.ws, task, request)
        request['accept_conversation_digest'] = records.conversations(self.ws, task)['digest']
        records.verify_decision(self.ws, task, request)
        value = self.conversation(task, coverage='partial')
        value['messages'][0]['text'] = 'changed coverage'
        self.write(task_store.state_path(self.ws)/'evidence/conversation.json', value)
        with self.assertRaisesRegex(ValueError, '接受'):
            records.verify_decision(self.ws, task, request)

    def test_unsubmitted_source_requires_precise_export_or_discard(self):
        task = self.ready()
        (self.repo/'file.txt').write_text('keep source')
        request = self.reset_request(task)
        with self.assertRaisesRegex(ValueError, 'export'):
            station.execute(self.ws, 'clean', task['issue_key'], task['run_id'], task['_revision'], 'op-records-source', request)
        self.assertEqual('keep source', (self.repo/'file.txt').read_text())

    def test_batch_registration_failure_resumes_existing_outputs_with_two_plans(self):
        task = self.ready()
        (self.repo/'a.txt').write_text('a')
        (self.repo/'b.txt').write_text('b')
        output = (self.root/'private-export').resolve()
        output.mkdir(mode=0o700)
        items = [{'path': 'source/'+self.name+'/'+name, 'output': str(output/name)} for name in ('a.txt', 'b.txt')]
        with mock.patch.object(resources, 'register', side_effect=OSError('registration interrupted')):
            with self.assertRaisesRegex(OSError, 'interrupted'):
                station_export.export_batch(self.ws, task['issue_key'], task['run_id'], items)
        before = [(p.stat().st_ino, p.read_bytes()) for p in (output/'a.txt', output/'b.txt')]
        with mock.patch.object(resources, 'plan', wraps=resources.plan) as planner:
            station_export.export_batch(self.ws, task['issue_key'], task['run_id'], items)
            self.assertEqual(2, planner.call_count)
        self.assertEqual(before, [(p.stat().st_ino, p.read_bytes()) for p in (output/'a.txt', output/'b.txt')])
        self.assertEqual(2, len([r for r in resources.inventory(self.ws, task) if r['kind'] == 'source-disposition']))

    def test_batch_source_drift_keeps_outputs_without_registering_coverage(self):
        task = self.ready()
        path = self.repo/'a.txt'
        path.write_text('old')
        output = (self.root/'private-export').resolve()
        output.mkdir(mode=0o700)
        original = station_export.export
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            path.write_text('changed')
            return result
        with mock.patch.object(station_export, 'export', side_effect=changed):
            with self.assertRaisesRegex(ValueError, '变化'):
                station_export.export_batch(self.ws, task['issue_key'], task['run_id'],
                    [{'path': 'source/'+self.name+'/a.txt', 'output': str(output/'a.txt')}])
        self.assertTrue((output/'a.txt').exists())
        self.assertFalse(any(r['kind'] == 'source-disposition' for r in resources.inventory(self.ws, task)))

    def test_message_ids_and_alternate_refs_cannot_hide_duplicate_coverage(self):
        task = self.ready()
        first = self.conversation(task)
        first.update(part=1, parts=2, first=0, last=0, messages=[{'role': 'user', 'text': 'one', 'order': 0, 'id': 'same'}])
        second = dict(first, part=2, first=1, last=1, source_ref='fixture:other-export',
                      messages=[{'role': 'assistant', 'text': 'two', 'order': 1, 'id': 'same'}])
        self.write(task_store.state_path(self.ws)/'evidence/conversation.json', first)
        self.write(task_store.state_path(self.ws)/'evidence/conversation-other.json', second)
        with self.assertRaisesRegex(ValueError, 'ID 重复'):
            records.conversations(self.ws, task)
        second.update(part=1, first=0, last=0, messages=first['messages'])
        self.write(task_store.state_path(self.ws)/'evidence/conversation-other.json', second)
        with self.assertRaisesRegex(ValueError, '缺失或重复'):
            records.conversations(self.ws, task)

    def test_unknown_json_body_not_copied_into_task_record(self):
        task = self.ready()
        self.write(task_store.state_path(self.ws)/'evidence/scalar.json', 'RAW-BUILD-LOG')
        self.write(task_store.state_path(self.ws)/'evidence/array.json', ['RAW-BUILD-LOG'])
        self.write(task_store.state_path(self.ws)/'evidence/report.json', {'stdout': 'RAW-BUILD-LOG', 'data': 'BASE64-SOURCE', 'tests': 3})
        result = station.execute(self.ws, 'archive', task['issue_key'], task['run_id'], task['_revision'], 'op-records-projection', self.reset_request(task))
        saved = (archive_store.from_reference(self.ws, result['archive_ref'])/'evidence.json').read_text()
        self.assertNotIn('RAW-BUILD-LOG', saved)
        self.assertNotIn('BASE64-SOURCE', saved)
        self.assertEqual({}, json.loads(saved)['report.json']['state'])

    def test_sync_results_and_gaps_saved_without_native_request_body(self):
        from workflow import jira_status, jira_watermark, station_archive, external_sync
        task = self.ready()
        identity = {'schema_version': 1, 'issue_key': task['issue_key'], 'run_id': task['run_id']}
        self.write(jira_status.state_path(self.ws, task), dict(identity, attempts={
            'takeover': {'outcome': 'failed', 'target_status': 'In Progress', 'reason': 'transport_failed', 'readback_ref': 'fixture:failed'},
            'tests_passed': {'outcome': 'skipped', 'reason': 'project_expected_skip', 'native_request': {'body': 'RAW-BUILD-LOG'}}}))
        self.write(jira_watermark.state_path(self.ws, task), dict(identity, watermark={
            'outcome': 'verified', 'version': 'v1.8', 'readback_value': 'v1.8', 'readback_ref': 'fixture:verified',
            'at': '2026-10-13T00:00:00+00:00', 'completed_at': '2026-10-13T00:00:01+00:00',
            'source_ref': 'fixture:watermark', 'issue_key': task['issue_key'], 'field_id': 'customfield_123',
            'field_name': 'version', 'logical_key': 'agenticops_version', 'issue_type_id': '1',
            'write_mode': 'overwrite', 'reason': 'verified',
            'payload_digest': jira_watermark.payload_digest('customfield_123', 'v1.8'),
            'native_request': {'body': 'RAW-BUILD-LOG'}}))
        self.write(external_sync.state_path(self.ws, task), {'issue_key': task['issue_key'], 'run_id': task['run_id'],
            'fields': {'problem_version': {'status': 'verified', 'fact_digest': 'abc', 'source_ref': 'fixture:field'}}})
        saved = json.loads(station_archive._evidence(self.ws, task))
        states = saved[jira_status.state_path(self.ws, task).name]['state']['attempts']
        self.assertEqual({'failed', 'skipped'}, {row['outcome'] for row in states})
        self.assertEqual('transport_failed', states[0]['reason'])
        self.assertEqual('fixture:verified', saved[jira_watermark.state_path(self.ws, task).name]['state']['watermark']['readback_ref'])
        self.assertEqual('verified', saved[external_sync.state_path(self.ws, task).name]['state']['fields'][0]['status'])
        self.assertNotIn('RAW-BUILD-LOG', json.dumps(saved))

    def test_amend_uses_new_cover_acceptance_without_changing_original_request(self):
        from workflow import station_operation as operations
        task = self.ready()
        self.conversation(task)
        request = self.reset_request(task)
        request['require_complete_conversation'] = True
        args = (self.ws, 'clean', task['issue_key'], task['run_id'], task['_revision'], 'op-records-amend', request)
        with mock.patch.object(station.archives, 'publish', side_effect=OSError('before freeze')):
            with self.assertRaises(OSError):
                station.execute(*args)
        original = operations.read(self.ws)
        self.conversation(task, coverage='partial')
        current = task_store.read_task(self.ws)
        plan = resources.plan(self.ws, current)
        amendment = {'confirmed_digest': plan['digest'], 'expected_plan_revision': 0, 'decision_ref': 'fixture:accept-partial',
                     'accept_conversation_digest': records.conversations(self.ws, current)['digest']}
        station.amend_cleanup(self.ws, task['issue_key'], task['run_id'], current['_revision'], args[5], original['cleanup_plan']['digest'], amendment)
        self.assertEqual(original['request'], operations.read(self.ws)['request'])
        # 后续仅修订清理范围仍继承已接受的覆盖决定，但原请求身份不变。
        current = task_store.read_task(self.ws)
        active = operations.read(self.ws)
        plan = resources.plan(self.ws, current)
        station.amend_cleanup(self.ws, task['issue_key'], task['run_id'], current['_revision'], args[5], active['cleanup_plan']['digest'],
            {'confirmed_digest': plan['digest'], 'expected_plan_revision': 1, 'decision_ref': 'fixture:scope-only', 'archive_runtime': []})
        self.assertEqual(original['request'], operations.read(self.ws)['request'])
        self.assertEqual('done', station.execute(*args)['status'])

    def test_state_projection_rejects_scalar_bodies(self):
        self.assertEqual({}, records.state_projection('RAW-BUILD-LOG'))
        self.assertEqual([], records.state_projection(['RAW-BUILD-LOG']))
        value = records.state_projection({'schema_version': 1, 'payload': 'RAW-BUILD-LOG',
            'result': 'RAW-BUILD-LOG', 'raw_result': 'RAW-BUILD-LOG', 'status': 'passed'})
        self.assertNotIn('RAW-BUILD-LOG', json.dumps(value))
        self.assertEqual('passed', value['status'])

    def test_only_known_receipt_fields_excluded_from_range_digest(self):
        from workflow import sync_recovery
        task = self.ready()
        name = 'jira-status-'+'a'*24+'.json'
        path = task_store.state_path(self.ws)/'evidence'/name
        before = {'schema_version': 1, 'issue_key': task['issue_key'], 'run_id': task['run_id'],
                  'attempts': {'tests_passed': {'operation_id': 'original-op', 'target_status': 'passed', 'outcome': 'succeeded'}}}
        self.write(path, before)
        snapshot = resources.active_files(self.ws)
        before['attempts']['tests_passed'].update(readback_ref='fixture:new-readback', completed_at='later')
        self.write(path, before)
        self.assertEqual(snapshot, resources.active_files(self.ws))
        before['attempts']['tests_passed']['target_status'] = 'other'
        self.write(path, before)
        self.assertNotEqual(snapshot, resources.active_files(self.ws))

    def test_original_receipt_can_recover_before_freeze_without_new_scope_confirmation(self):
        from workflow import sync_recovery
        task = self.ready()
        request = self.reset_request(task)
        args = (self.ws, 'clean', task['issue_key'], task['run_id'], task['_revision'], 'op-receipt-before-freeze', request)
        with mock.patch.object(station.archives, 'publish', side_effect=OSError('before freeze')):
            with self.assertRaises(OSError):
                station.execute(*args)
        with task_store.task_state_lock(self.ws):
            sync_recovery.require_receipt(self.ws, task_store.read_task(self.ws))
        self.assertEqual(request['confirmed_digest'], resources.plan(self.ws, task_store.read_task(self.ws))['digest'])
        current = task_store.read_task(self.ws)
        current['history'].append({'type': 'delivery_readback', 'source_ref': 'fixture:merged'})
        task_store.write_task(self.ws, current)
        self.assertEqual(request['confirmed_digest'], resources.plan(self.ws, task_store.read_task(self.ws))['digest'])
        self.assertEqual('done', station.execute(*args)['status'])


class ExitInputTests(unittest.TestCase):
    setUp = RecordTests.setUp
    prepare_engineering = RecordTests.prepare_engineering
    write = RecordTests.write
    git = RecordTests.git
    takeover = RecordTests.takeover
    ready = RecordTests.ready
    reset_request = RecordTests.reset_request

    def inputs(self, task, *, pending=False, missing=False):
        import hashlib
        from workflow import station_operation
        source = {'platform': 'fixture', 'thread_id': 'thread-exit', 'source_ref': 'fixture:export',
                  'cutoff': 'message:1', 'boundary_ref': 'fixture:boundary', 'source_kind': 'user_export',
                  'first': 0, 'last': 1}
        identity = {'schema_version': 1, 'issue_key': task['issue_key'], 'run_id': task['run_id']}
        scope = dict(identity, kind='conversation-scope', sources=[source])
        if missing:
            scope['sources'].append(dict(source, thread_id='other-thread'))
        value = dict(identity, kind='conversation', **{k: v for k, v in source.items() if k != 'boundary_ref'},
                     coverage='complete', messages=[{'role': 'user', 'text': '保存本轮状态', 'order': 0},
                        {'role': 'assistant', 'text': '截止点之后确认由退出回执保存', 'order': 1}])
        items = []
        for name, document in (('scope.json', scope), ('visible.json', value)):
            path = (self.root / name).resolve()
            self.write(path, document)
            raw = path.read_bytes()
            items.append({'path': str(path), 'kind': document['kind'], 'size': len(raw),
                          'sha256': hashlib.sha256(raw).hexdigest()})
        manifest = dict(identity, files=items)
        if pending:
            operation = station_operation.read(self.ws)
            manifest.update(operation_id=operation['operation_id'], revision=task['_revision'],
                            plan_digest=operation['cleanup_plan']['digest'])
        path = (self.root / 'conversation-input.json').resolve()
        self.write(path, manifest)
        return path

    def test_completed_record_is_bound_idempotent_and_does_not_release(self):
        from workflow import station_exit_inputs, station_operation
        task = self.ready()
        task.update(stage='completed', outcome='completed')
        task_store.write_task(self.ws, task)
        task = task_store.read_task(self.ws)
        manifest = self.inputs(task)
        before = (self.ws / '.agenticops/current-task.json').read_bytes()
        result = station_exit_inputs.record(self.ws, task['issue_key'], task['run_id'], task['_revision'], manifest)
        self.assertEqual('complete', result['conversation']['coverage'])
        evidence = {p: (p.stat().st_ino, p.read_bytes()) for p in (self.ws / '.agenticops/evidence').glob('conversation*.json')}
        station_exit_inputs.record(self.ws, task['issue_key'], task['run_id'], task['_revision'], manifest)
        self.assertEqual(evidence, {p: (p.stat().st_ino, p.read_bytes()) for p in evidence})
        self.assertEqual(before, (self.ws / '.agenticops/current-task.json').read_bytes())
        self.assertEqual('done', station_operation.read(self.ws)['status'])

    def test_partial_input_write_recovers_same_manifest_without_false_confirmation(self):
        from workflow import station_exit_inputs
        task = self.ready()
        manifest = self.inputs(task)
        writer = task_store._write_json_atomic
        writes = []
        def interrupt(path, value):
            writes.append(path)
            if len(writes) == 2:
                raise OSError('second input interrupted')
            return writer(path, value)
        with mock.patch.object(task_store, '_write_json_atomic', side_effect=interrupt):
            with self.assertRaises(OSError):
                station_exit_inputs.record(self.ws, task['issue_key'], task['run_id'], task['_revision'], manifest)
        files = list((self.ws / '.agenticops/evidence').glob('conversation*.json'))
        self.assertEqual(1, len(files))
        first = files[0].read_bytes()
        self.assertEqual('complete', station_exit_inputs.record(self.ws, task['issue_key'], task['run_id'], task['_revision'], manifest)['conversation']['coverage'])
        self.assertEqual(first, files[0].read_bytes())
        self.assertIsNone(task_store.read_task(self.ws)['archive_ref'])

    def test_pending_input_needs_original_operation_and_amendment(self):
        from workflow import station_exit_inputs, station_operation
        task = self.ready()
        request = self.reset_request(task)
        with mock.patch.object(station.archives, 'publish', side_effect=OSError('before freeze')):
            with self.assertRaises(OSError):
                station.execute(self.ws, 'clean', task['issue_key'], task['run_id'], task['_revision'], 'op-exit-input-pending', request)
        task = task_store.read_task(self.ws)
        manifest = self.inputs(task, pending=True)
        operation = station_operation.read(self.ws)
        path = station_exit_inputs.interaction(self.ws, task['issue_key'], task['run_id'], 'exit-summary.json',
            operation['operation_id'], task['_revision'], operation['cleanup_plan']['digest'])
        self.assertTrue(path.parent.is_dir())
        with self.assertRaises(ValueError):
            station_exit_inputs.interaction(self.ws, task['issue_key'], task['run_id'], 'wrong.json',
                'op-other-operation', task['_revision'], operation['cleanup_plan']['digest'])
        self.assertTrue(station_exit_inputs.record(self.ws, task['issue_key'], task['run_id'], task['_revision'], manifest)['requires_amendment'])
        self.assertEqual(operation['request'], station_operation.read(self.ws)['request'])
        with self.assertRaises(ValueError):
            station.execute(self.ws, 'clean', task['issue_key'], task['run_id'], operation['expected_revision'], operation['operation_id'], request)

    def test_published_body_rejects_recording(self):
        from workflow import station_exit_inputs
        task = self.ready()
        request = self.reset_request(task)
        station.execute(self.ws, 'archive', task['issue_key'], task['run_id'], task['_revision'], 'op-exit-published', request)
        task = task_store.read_task(self.ws)
        manifest = self.inputs(task)
        with self.assertRaisesRegex(ValueError, '正式档案'):
            station_exit_inputs.record(self.ws, task['issue_key'], task['run_id'], task['_revision'], manifest)

    def test_frozen_draft_input_amendment_checks_future_plan_and_retains_old_draft(self):
        from workflow import station_exit_inputs, station_operation
        task = self.ready()
        request = self.reset_request(task)
        original = station.archives._directory
        def interrupt(path):
            raise OSError('after evidence freeze')
        with mock.patch.object(station.archives, '_directory', side_effect=interrupt):
            with self.assertRaises(OSError):
                station.execute(self.ws, 'clean', task['issue_key'], task['run_id'], task['_revision'], 'op-exit-input-frozen', request)
        task = task_store.read_task(self.ws)
        operation = station_operation.read(self.ws)
        self.assertTrue(operation.get('archive_record'))
        manifest = self.inputs(task, pending=True)
        with self.assertRaisesRegex(ValueError, '冻结'):
            station_exit_inputs.record(self.ws, task['issue_key'], task['run_id'], task['_revision'], manifest)
        prepared = station_exit_inputs.prepare(self.ws, task, manifest, amendment=True)
        future = resources.plan(self.ws, task, evidence_override=prepared['files'])
        amendment = {'confirmed_digest': future['digest'], 'expected_plan_revision': 0,
                     'decision_ref': 'fixture:accept-future-input'}
        changed = station.amend_cleanup(self.ws, task['issue_key'], task['run_id'], task['_revision'],
            operation['operation_id'], operation['cleanup_plan']['digest'], amendment, str(manifest))
        self.assertTrue(changed['archive_drafts'])
        self.assertEqual(operation['archive_record'], changed['archive_drafts'][0]['record'])
        self.assertEqual(operation['request'], changed['request'])
        self.assertEqual(future['digest'], changed['cleanup_plan']['digest'])
        again = station.amend_cleanup(self.ws, task['issue_key'], task['run_id'], task['_revision'],
            operation['operation_id'], operation['cleanup_plan']['digest'], amendment, str(manifest))
        self.assertEqual(changed['last_amendment'], again['last_amendment'])
        station.execute(self.ws, 'clean', task['issue_key'], task['run_id'], operation['expected_revision'],
                        operation['operation_id'], operation['request'])
        self.assertIsNone(task_store.read_task(self.ws))

    def test_default_check_uses_actual_material_without_mutation(self):
        from workflow import station_clean
        task = self.ready()
        before = {p: p.read_bytes() for p in (self.ws / '.agenticops').rglob('*') if p.is_file()}
        result = station_clean.check(self.ws, task)
        self.assertTrue(result['cleanup_scope']['scope_complete'])
        self.assertFalse(result['cleanup_scope']['archive_ready'])
        self.assertFalse(result['cleanup_scope']['execution_ready'])
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        request = self.reset_request(task)
        result = station_clean.check(self.ws, task, request)
        self.assertTrue(result['cleanup_scope']['archive_ready'], result)
        self.assertTrue(result['cleanup_scope']['execution_ready'], result)
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_published_resume_does_not_budget_existing_archive_again(self):
        from workflow import station_clean, station_archive
        from types import SimpleNamespace
        task = self.ready()
        request = self.reset_request(task)
        with mock.patch('shutil.disk_usage', return_value=SimpleNamespace(free=1024)):
            self.assertFalse(station_clean.check(self.ws, task, request)['cleanup_scope']['archive_ready'])
        operation = station.execute(self.ws, 'clean', task['issue_key'], task['run_id'], task['_revision'],
            'op-published-budget', request, cleanup_mode='prepare')
        task = task_store.read_task(self.ws)
        archive = archive_store.from_reference(self.ws, task['archive_ref'])
        before = {p: p.read_bytes() for p in archive.rglob('*') if p.is_file()}
        with mock.patch('shutil.disk_usage', return_value=SimpleNamespace(free=1024)), \
                mock.patch.object(station_archive, 'prepare_material', side_effect=AssertionError('must reuse archive')):
            result = station_clean.check(self.ws, task, request, operation)
        self.assertTrue(result['cleanup_scope']['archive_ready'], result)
        self.assertTrue(result['cleanup_scope']['execution_ready'], result)
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_100000_objects_all_default_results_bounded_and_full_output_private(self):
        import io, os
        from workflow import station_clean_view
        task = self.ready()
        rows = [{'path': 'source/repo/' + str(i), 'proof': '中' * 1000} for i in range(100000)]
        for status in ('check', 'done', 'needs_attention', 'resume_required'):
            value = {'status': status, 'cleanup_scope': {'source_inventory': rows, 'refs': rows, 'receipts': rows}}
            text = station_clean_view.render(value)
            self.assertLessEqual(len((text + '\n').encode()), 64 * 1024)
            summary = json.loads(text)
            self.assertEqual(100000, summary['cleanup_scope']['source_inventory_count'])
        self.assertEqual(100000, len(rows))
        output = (self.root / 'full-cleanup.json').resolve()
        with mock.patch('sys.stdout', new_callable=io.StringIO):
            station_clean_view.emit({'status': 'check', 'run_id': task['run_id'], 'rows': rows[:20]},
                details=True, output=str(output), base=self.ws)
        self.assertEqual(0o600, os.stat(output).st_mode & 0o777)
        self.assertEqual(20, len(json.loads(output.read_text())['rows']))
        with self.assertRaises(OSError):
            station_clean_view.emit({'status': 'check'}, details=True, output=str(output), base=self.ws)
        with self.assertRaises(ValueError):
            station_clean_view.emit({'status': 'check'}, details=True, output=str(self.ws / 'runtime/report.json'), base=self.ws)


if __name__ == '__main__':
    unittest.main()

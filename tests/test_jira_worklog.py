#!/usr/bin/env python3
"""工作段计时、等待排除和原生工时回读；仅使用隔离工位及模拟 Jira。"""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workflow import jira_worklog as log, jira_watermark, task_store, external_sync, station_archive
from station_fixture import save_task, ownership_snapshot


class WorklogTests(unittest.TestCase):
    def test_rest_dates_and_explicit_minute_precision_preserve_remainder(self):
        self.call('start')
        self.call('pause', at='2026-11-03T10:07:18+00:00')
        snapshot = self.snapshot(worklog_precision_ref='fixture:actual-server-minute-readback')
        snapshot['capabilities']['worklog_precision_seconds'] = 60
        request = self.call('prepare', snapshot=self.snapshot_file(snapshot))['native_request']
        self.assertEqual('2026-11-03T10:00:00.000+0000', request['body']['started'])
        self.assertEqual(420, request['body']['timeSpentSeconds'])
        result = self.call('complete', snapshot=self.snapshot_file(self.readback(request)))
        self.assertEqual('verified', result['segments'][0]['status'])
        self.assertEqual(18, result['segments'][0]['unlogged_seconds'])
        self.assertNotIn('native_request', self.call('prepare', snapshot=self.snapshot_file(snapshot)))

    def test_old_rest_intent_rounding_requires_evidence_and_keeps_payload(self):
        self.call('start')
        self.call('pause', at='2026-11-03T10:07:18+00:00')
        request = self.call('prepare', snapshot=self.snapshot_file(self.snapshot()))['native_request']
        snapshot = self.readback(request)
        snapshot['worklogs'][0]['timeSpentSeconds'] = 420
        with self.assertRaisesRegex(ValueError, '不一致'):
            self.call('complete', snapshot=self.snapshot_file(snapshot))
        snapshot['capabilities']['worklog_precision_seconds'] = 60
        snapshot['worklog_precision_ref'] = 'fixture:verified-server-minute-precision'
        changed = copy.deepcopy(snapshot)
        changed['worklogs'][0]['timeSpentSeconds'] = 360
        with self.assertRaisesRegex(ValueError, '不一致'):
            self.call('complete', snapshot=self.snapshot_file(changed))
        result = self.call('complete', snapshot=self.snapshot_file(snapshot))
        self.assertEqual(18, result['segments'][0]['unlogged_seconds'])
        self.assertEqual(request, log.load(self.base, 'TAP-123')['segments']['segment-0001']['request'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ao-jira-worklog-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.env = mock.patch.dict(os.environ, {'AGENTICOPS_EXECUTION_ID': '0' * 32})
        self.env.start()
        self.addCleanup(self.env.stop)
        (self.base / '.agenticops').mkdir()
        task_store._write_json_atomic(self.base / '.agenticops/station.json',
                                     {'product_root': str(ROOT), 'project': 'tapdata'})
        self.task = {'issue_key': 'TAP-123', 'run_id': 'run-0123456789ab', 'task_class': 'defect_fix',
                     'stage': 'implementation', 'facts': {}, 'history': [], 'pending': None, 'repositories': []}
        save_task(self.base, self.task)

    def call(self, action, at='2026-11-03T10:00:00+00:00', **overrides):
        args = dict(action=action, dir=str(self.base), issue_key='TAP-123', expected_run_id=self.task['run_id'],
                    segment_id='segment-0001', category='plan', summary='定位缓存阻塞', snapshot=None, reason=None)
        args.update(overrides)
        output = io.StringIO()
        with mock.patch.object(log, 'now', return_value=at), contextlib.redirect_stdout(output):
            log.command(SimpleNamespace(**args))
        return json.loads(output.getvalue())

    def snapshot(self, **extra):
        task = task_store.read_task(self.base, 'TAP-123')
        marker = jira_watermark.status(self.base, task)
        snapshot = ownership_snapshot(self.base, values=marker['expected_fields'])
        snapshot['capabilities'] = {'worklog_properties': True, 'adjust_estimate_leave': True}
        snapshot.update(extra)
        return snapshot

    def snapshot_file(self, data):
        path = self.base / 'native-snapshot.json'
        path.write_text(json.dumps(data))
        return str(path)

    def timed(self):
        self.call('start')
        self.call('pause', at='2026-11-03T10:12:00+00:00')

    def prepared(self):
        self.timed()
        return self.call('prepare', snapshot=self.snapshot_file(self.snapshot()))['native_request']

    def readback(self, request):
        body = copy.deepcopy(request['body'])
        return self.snapshot(worklogs_complete=True, worklogs=[dict(body, id='456', issueId='123',
                             author={'accountId': 'fixture-user'})])

    def mcp_snapshot(self, **extra):
        return self.snapshot(capabilities={'worklog_create': True, 'worklog_property_edit': True},
                             worklogs_complete=True, worklogs=[], **extra)

    def mcp_prepared(self, seconds=720):
        self.call('start')
        from datetime import timedelta
        end = log.instant('2026-11-03T10:00:00+00:00') + timedelta(seconds=seconds)
        self.call('pause', at=end.isoformat())
        return self.call('prepare', snapshot=self.snapshot_file(self.mcp_snapshot()))

    def mcp_readback(self, extra=None):
        row = log.load(self.base, 'TAP-123')['segments']['segment-0001']
        wanted = copy.deepcopy(row['expected_worklog'])
        wanted['comment'] = '方案｜定位缓存阻塞'
        wanted['properties'] = []
        worklog = dict(wanted, id='456', issueId='123', author={'accountId': 'fixture-user'})
        return self.snapshot(worklogs_complete=True, worklogs=[worklog], **(extra or {}))

    def test_wait_is_excluded_retries_are_idempotent_and_labels_are_minimal(self):
        self.timed()
        self.call('pause', at='2026-11-04T10:00:00+00:00')
        self.call('start', at='2026-11-04T10:00:00+00:00')  # 重放已结束段不重新开表。
        self.call('start', at='2026-11-04T10:00:00+00:00', segment_id='segment-0002', category='test', summary='并发回归通过，12 项')
        self.call('pause', at='2026-11-04T10:00:20+00:00', segment_id='segment-0002')
        state = log.load(self.base, 'TAP-123')
        self.assertEqual([720, 20], [row['seconds'] for row in state['segments'].values()])
        result = self.call('prepare', snapshot=self.snapshot_file(self.snapshot()))
        request = result['native_request']
        self.assertEqual({'adjustEstimate': 'leave'}, request['query'])
        self.assertEqual('方案｜定位缓存阻塞', request['body']['comment']['content'][0]['content'][0]['text'])
        self.assertEqual(720, request['body']['timeSpentSeconds'])
        repeated = self.call('prepare', snapshot=self.snapshot_file(self.snapshot()))
        self.assertNotIn('native_request', repeated)
        self.assertEqual(request['body']['properties'][0]['value']['id'], repeated['readbacks'][0]['property_id'])

    def test_conflicting_replay_and_overlapping_work_are_rejected(self):
        self.call('start')
        with self.assertRaisesRegex(ValueError, '已变化'):
            self.call('start', summary='改变既有工作段')
        with self.assertRaisesRegex(ValueError, '先 pause'):
            self.call('start', segment_id='segment-0002')

    def test_idle_takeover_resume_rejects_other_issue_timer_without_mutation(self):
        current = task_store.read_current(self.base)
        task_store.compare_and_set(self.base, current['revision'], None)
        idle = dict(expected_run_id=None, category='takeover')
        self.call('start', **idle)
        self.call('pause', at='2026-11-03T10:00:20+00:00', **idle)
        self.call('start', issue_key='TAP-456', at='2026-11-03T10:00:30+00:00', **idle)
        paths = [log.state_path(self.base, issue) for issue in ('TAP-123', 'TAP-456')]
        before = [path.read_bytes() for path in paths]
        for action in ('start', 'resume'):
            with self.subTest(action=action):
                with self.assertRaisesRegex(ValueError, '其它任务还有计时工作段'):
                    self.call(action, at='2026-11-03T10:00:40+00:00',
                              segment_id='segment-0002' if action == 'start' else 'segment-0001', **idle)
                self.assertEqual(before, [path.read_bytes() for path in paths])
        self.call('pause', issue_key='TAP-456', at='2026-11-03T10:01:00+00:00', **idle)
        self.call('resume', at='2026-11-03T10:01:10+00:00', **idle)
        self.call('pause', at='2026-11-03T10:01:30+00:00', **idle)
        for issue, seconds in (('TAP-123', 40), ('TAP-456', 30)):
            state = log.load(self.base, issue)
            self.assertIsNone(state['active'])
            self.assertIsNone(state['run_id'])
            self.assertEqual(seconds, state['segments']['segment-0001']['seconds'])

    def test_mcp_create_bind_property_complete_and_repeat_never_recreates(self):
        prepared = self.mcp_prepared()
        request = prepared['native_request']
        self.assertEqual('addOrEditJiraIssueWorklog', request['tool'])
        self.assertEqual('12m', request['arguments']['timeSpent'])
        self.assertEqual('方案｜定位缓存阻塞', request['arguments']['comment'])
        snapshot = self.mcp_readback({'created_worklog_id': '456'})
        bound = self.call('bind', snapshot=self.snapshot_file(snapshot))['native_request']
        self.assertEqual('editJiraEntityProperty', bound['tool'])
        self.assertEqual(log.MCP_PROPERTY, bound['arguments']['propertyKey'])
        self.assertNotIn('native_request', self.call('prepare', snapshot=self.snapshot_file(self.mcp_snapshot())))
        snapshot['worklogs'][0]['properties'] = [{'key': log.MCP_PROPERTY, 'value': bound['arguments']['value']}]
        done = self.call('complete', snapshot=self.snapshot_file(snapshot))
        self.assertEqual('verified', done['segments'][0]['status'])
        self.assertEqual([], log.pending(self.base, 'TAP-123'))

    def test_mcp_missing_response_recovers_unique_new_record_without_creating(self):
        self.mcp_prepared()
        snapshot = self.mcp_readback()
        bound = self.call('bind', snapshot=self.snapshot_file(snapshot))['native_request']
        self.assertEqual('456', bound['arguments']['worklogId'])
        self.assertEqual('editJiraEntityProperty', self.call('bind', snapshot=self.snapshot_file(snapshot))['native_request']['tool'])

    def test_mcp_ambiguous_result_never_attaches_property_or_recreates(self):
        self.mcp_prepared()
        snapshot = self.mcp_readback()
        snapshot['worklogs'].append(dict(snapshot['worklogs'][0], id='457'))
        self.assertNotIn('native_request', self.call('bind', snapshot=self.snapshot_file(snapshot)))
        self.assertNotIn('native_request', self.call('prepare', snapshot=self.snapshot_file(self.mcp_snapshot())))

    def test_mcp_property_owned_by_other_intent_is_not_overwritten(self):
        self.mcp_prepared()
        snapshot = self.mcp_readback()
        snapshot['worklogs'][0]['properties'] = [{'key': log.MCP_PROPERTY, 'value': {'id': 'another-intent'}}]
        with self.assertRaisesRegex(ValueError, '不覆盖'):
            self.call('bind', snapshot=self.snapshot_file(snapshot))

    def test_mcp_minutes_are_floored_and_unlogged_seconds_remain_in_preservation(self):
        prepared = self.mcp_prepared(seconds=125)
        self.assertEqual('2m', prepared['native_request']['arguments']['timeSpent'])
        snapshot = self.mcp_readback()
        bound = self.call('bind', snapshot=self.snapshot_file(snapshot))['native_request']
        snapshot['worklogs'][0]['properties'] = [{'key': log.MCP_PROPERTY, 'value': bound['arguments']['value']}]
        self.call('complete', snapshot=self.snapshot_file(snapshot))
        row = log.pending(self.base, 'TAP-123')[0]
        self.assertEqual((125, 120, 5), (row['seconds'], row['logged_seconds'], row['unlogged_seconds']))

    def test_short_mcp_segment_can_resume_same_work_excluding_wait(self):
        deferred = self.mcp_prepared(seconds=38)
        self.assertNotIn('native_request', deferred)
        self.assertEqual('deferred', deferred['segments'][0]['status'])
        self.call('resume', at='2026-11-04T10:00:00+00:00')
        self.call('pause', at='2026-11-04T10:00:30+00:00')
        request = self.call('prepare', snapshot=self.snapshot_file(self.mcp_snapshot()))['native_request']
        self.assertEqual('1m', request['arguments']['timeSpent'])
        self.assertEqual(68, log.load(self.base, 'TAP-123')['segments']['segment-0001']['seconds'])

    def test_mcp_property_failure_cannot_rearm_worklog_creation(self):
        self.mcp_prepared()
        snapshot = self.mcp_readback()
        self.call('bind', snapshot=self.snapshot_file(snapshot))
        row = log.load(self.base, 'TAP-123')['segments']['segment-0001']
        snapshot.update(http_status=400, rejection_ref='fixture:property/400', rejected_request_digest=row['request_digest'])
        with self.assertRaisesRegex(ValueError, '不得重新创建'):
            self.call('retry', snapshot=self.snapshot_file(snapshot))

    def test_mcp_expected_body_cannot_drift_from_frozen_tool_request(self):
        self.mcp_prepared()
        path = log.state_path(self.base, 'TAP-123')
        state = json.loads(path.read_text())
        state['segments']['segment-0001']['expected_worklog']['timeSpentSeconds'] += 60
        path.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError, '冻结回读要求'):
            log.load(self.base, 'TAP-123')

    def test_recover_preserves_measured_spans_and_excludes_unfinished_tail(self):
        self.timed()
        self.call('resume', at='2026-11-04T10:00:00+00:00')
        self.call('recover', at='2026-11-05T10:00:00+00:00')
        row = log.load(self.base, 'TAP-123')['segments']['segment-0001']
        self.assertEqual(720, row['seconds'])
        self.assertEqual('pending', row['status'])
        self.assertIn('unmeasured_tail', row)

    def test_zero_length_is_not_registered_and_run_drift_does_not_mutate(self):
        self.call('start')
        self.call('pause')
        self.assertEqual([], log.pending(self.base, 'TAP-123'))
        before = log.state_path(self.base, 'TAP-123').read_bytes()
        with self.assertRaisesRegex(ValueError, '已变化'):
            self.call('start', segment_id='segment-0002', expected_run_id='run-another123')
        self.assertEqual(before, log.state_path(self.base, 'TAP-123').read_bytes())

    def test_failed_takeover_work_is_included_in_preservation_without_borrowing_run(self):
        current = task_store.read_current(self.base)
        task_store.compare_and_set(self.base, current['revision'], None)
        self.call('start', issue_key='TAP-456', category='takeover', expected_run_id=None)
        self.call('pause', issue_key='TAP-456', expected_run_id=None, at='2026-11-03T10:01:00+00:00')
        save_task(self.base, self.task)
        pending = log.all_pending(self.base)
        self.assertEqual('TAP-456', pending[0]['issue_key'])
        self.assertIsNone(pending[0]['run_id'])
        self.assertEqual(60, pending[0]['seconds'])
        self.assertTrue(any(w['kind'] == 'jira_worklog' for w in external_sync.existing_warnings(self.base, self.task)))

    def test_takeover_before_run_binds_original_segment_without_reset(self):
        current = task_store.read_current(self.base)
        task_store.compare_and_set(self.base, current['revision'], None)
        self.call('start', category='takeover', expected_run_id=None)
        save_task(self.base, self.task)
        self.call('pause', at='2026-11-03T10:05:00+00:00')
        state = log.load(self.base, 'TAP-123')
        self.assertEqual(self.task['run_id'], state['run_id'])
        self.assertEqual(300, state['segments']['segment-0001']['seconds'])

    def test_recovery_does_not_estimate_old_tail_and_clock_regression_is_preserved(self):
        self.call('start')
        state = log.load(self.base, 'TAP-123')
        with self.assertRaisesRegex(ValueError, '原会话'):
            log.pause(state, 'segment-0001', '1' * 32)
        with self.assertRaisesRegex(ValueError, '时钟倒退'):
            self.call('pause', at='2026-11-02T10:00:00+00:00')
        result = self.call('recover', at='2026-11-02T10:00:00+00:00')
        self.assertEqual('unmeasured', result['segments'][0]['status'])
        self.assertNotIn('seconds', result['segments'][0])

    def test_pre_takeover_validation_rejects_foreign_damaged_and_sent_records(self):
        current = task_store.read_current(self.base)
        task_store.compare_and_set(self.base, current['revision'], None)
        self.call('start', category='takeover', expected_run_id=None)
        path = log.state_path(self.base, 'TAP-123')
        # save_task 夹具产生的其它证据不属于本次预接管记录。
        for entry in path.parent.iterdir():
            if entry != path:
                entry.unlink()
        original = log.load(self.base, 'TAP-123')
        log.check_takeover_evidence(self.base, 'TAP-123')
        cases = [dict(original, run_id='old-run'), dict(original, station_id='b' * 32),
                 dict(original, issue_key='TAP-456'), dict(original, schema_version=99)]
        for field, value in [('category', 'plan'), ('session_id', None), ('status', 'verified'),
                             ('request', {}), ('active_started', None)]:
            damaged = copy.deepcopy(original)
            damaged['segments']['segment-0001'][field] = value
            cases.append(damaged)
        for damaged in cases:
            with self.subTest(damaged=damaged):
                path.write_text(json.dumps(damaged))
                before = path.read_bytes()
                with self.assertRaises((ValueError, KeyError, TypeError, AttributeError)):
                    log.check_takeover_evidence(self.base, 'TAP-123')
                self.assertEqual(before, path.read_bytes())
        path.write_text(json.dumps(original))
        other = path.with_name('jira-worklog-TAP-456.json')
        other.write_text(json.dumps(original))
        with self.assertRaisesRegex(ValueError, '未知或其它任务'):
            log.check_takeover_evidence(self.base, 'TAP-123')
        other.unlink()
        path.unlink()
        path.symlink_to(self.base / 'absent')
        with self.assertRaisesRegex(ValueError, '未知或其它任务'):
            log.check_takeover_evidence(self.base, 'TAP-123')
        path.unlink()
        path.parent.rmdir()
        path.parent.symlink_to(self.base / 'absent-directory')
        with self.assertRaisesRegex(ValueError, '目录不可信'):
            log.check_takeover_evidence(self.base, 'TAP-123')

    def test_takeover_attachment_preserves_existing_unknown_send(self):
        self.prepared()
        before = log.state_path(self.base, 'TAP-123').read_bytes()
        log.attach_takeover(self.base, self.task)
        self.assertEqual(before, log.state_path(self.base, 'TAP-123').read_bytes())
        with self.assertRaises(ValueError):
            log.attach_takeover(self.base, dict(self.task, run_id='another-run'))
        self.assertEqual(before, log.state_path(self.base, 'TAP-123').read_bytes())

    def test_write_response_loss_converges_by_property_without_second_send(self):
        request = self.prepared()
        snapshot = self.readback(request)
        complete = self.call('complete', snapshot=self.snapshot_file(snapshot))
        self.assertEqual('verified', complete['segments'][0]['status'])
        self.assertEqual('456', complete['segments'][0]['worklog_id'])
        self.assertNotIn('native_request', self.call('prepare', snapshot=self.snapshot_file(self.snapshot())))
        self.assertEqual([], log.pending(self.base, 'TAP-123'))

    def test_absent_duplicate_or_partial_readback_never_allows_resend(self):
        request = self.prepared()
        for worklogs in ([], self.readback(request)['worklogs'] * 2):
            result = self.call('complete', snapshot=self.snapshot_file(self.snapshot(worklogs_complete=True, worklogs=worklogs)))
            self.assertEqual('intent', result['segments'][0]['status'])
            self.assertNotIn('native_request', self.call('prepare', snapshot=self.snapshot_file(self.snapshot())))
        with self.assertRaisesRegex(ValueError, '读完分页'):
            self.call('complete', snapshot=self.snapshot_file(self.readback(request) | {'worklogs_complete': False}))
        with self.assertRaisesRegex(ValueError, '已知未发送'):
            self.call('defer', reason='超时不是未发送')

    def test_readback_checks_target_actor_start_duration_and_body(self):
        request = self.prepared()
        for field, value in [('issueId', '999'), ('author', {'accountId': 'another-user'}),
                             ('started', '2026-11-03T11:00:00+00:00'), ('timeSpentSeconds', 1), ('comment', {})]:
            with self.subTest(field=field):
                snapshot = self.readback(request)
                snapshot['worklogs'][0][field] = value
                with self.assertRaisesRegex(ValueError, '不一致'):
                    self.call('complete', snapshot=self.snapshot_file(snapshot))

    def test_changed_ownership_and_missing_native_capability_are_not_written(self):
        self.timed()
        snapshot = self.snapshot()
        snapshot['capabilities'] = {}
        with self.assertRaisesRegex(ValueError, '原生能力'):
            self.call('prepare', snapshot=self.snapshot_file(snapshot))
        self.assertEqual('deferred', self.call('defer', reason='原生工具缺少属性及预估参数')['segments'][0]['status'])
        snapshot = self.snapshot()
        snapshot['issue']['fields']['customfield_10365'] = 'run-another123'
        with self.assertRaises(ValueError):
            self.call('prepare', snapshot=self.snapshot_file(snapshot))
        self.assertNotIn('request', log.load(self.base, 'TAP-123')['segments']['segment-0001'])

    def test_only_proven_rejection_can_rearm_original_payload(self):
        request = self.prepared()
        row = log.load(self.base, 'TAP-123')['segments']['segment-0001']
        snapshot = self.snapshot(worklogs_complete=True, worklogs=[], rejection_ref='fixture:http/400',
                                 http_status=400, rejected_request_digest=row['request_digest'])
        with self.assertRaisesRegex(ValueError, '明确拒绝'):
            self.call('retry', snapshot=self.snapshot_file(snapshot | {'http_status': 504}))
        self.call('retry', snapshot=self.snapshot_file(snapshot))
        retried = self.call('prepare', snapshot=self.snapshot_file(self.snapshot()))['native_request']
        self.assertEqual(request, retried)

    def test_boundary_settles_and_pending_material_is_preserved_for_handoff(self):
        self.call('start')
        with mock.patch.object(log, 'now', return_value='2026-11-03T10:02:00+00:00'):
            with task_store.task_state_lock(self.base, ownership=False):
                log.boundary(self.base, task_store.read_task(self.base, 'TAP-123'))
        pending = [w for w in external_sync.existing_warnings(self.base, self.task) if w['kind'] == 'jira_worklog']
        self.assertEqual(120, pending[0]['worklogs'][0]['seconds'])
        archive = station_archive.prepare(self.base, self.task, {})
        material = archive['snapshot']['evidence']['jira-worklog-TAP-123.json']
        self.assertEqual(120, material['segments']['segment-0001']['seconds'])

    def test_terminal_allows_existing_receipt_but_no_new_timing(self):
        request = self.prepared()
        task = task_store.read_task(self.base, 'TAP-123')
        task['outcome'] = 'completed'
        task_store.write_task(self.base, task)
        with self.assertRaisesRegex(ValueError, '已归档或终止'):
            self.call('start', segment_id='segment-0002')
        self.assertEqual('verified', self.call('complete', snapshot=self.snapshot_file(self.readback(request)))['segments'][0]['status'])

    def test_corrupt_state_and_sensitive_summary_preserve_existing_material(self):
        self.timed()
        path = log.state_path(self.base, 'TAP-123')
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, '敏感'):
            self.call('start', segment_id='segment-0002', summary='password=private-value')
        self.assertEqual(before, path.read_bytes())
        path.write_text('{broken')
        self.assertIn('未核验', log.settle_boundary(self.base, self.task))
        self.assertEqual('{broken', path.read_text())


if __name__ == '__main__':
    unittest.main()

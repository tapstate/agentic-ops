#!/usr/bin/env python3
"""四项归属与预检后通知：使用真实本地 Git 和现役确定性入口。"""
import copy
from datetime import datetime, timezone
import unittest
from unittest import mock
import test_station_lifecycle as fixture
from station_fixture import ownership_snapshot
from workflow import station, station_operation, station_takeover, task_store
from workflow import jira_watermark, jira_ownership, external_sync, quality, jira_status


class OwnershipTests(unittest.TestCase):
    prepare_engineering = fixture.StationTests.prepare_engineering
    write = fixture.StationTests.write
    git = fixture.StationTests.git

    def setUp(self):
        fixture.StationTests.setUp(self)
        self.ownership_barrier.stop()
        self.prepare_engineering()
        (self.product / '.gitignore').write_text('__pycache__/\n')
        self.git(self.product, 'init', '-b', 'develop')
        self.git(self.product, 'config', 'user.email', 'fixture@example.invalid')
        self.git(self.product, 'config', 'user.name', 'Fixture')
        self.git(self.product, 'add', '.')
        self.git(self.product, 'commit', '-m', 'fixture product')
        self.request['task_class'] = 'defect_fix'
        self.snapshot = ownership_snapshot(self.ws)
        self.request['jira_snapshot'] = copy.deepcopy(self.snapshot)

    def begin(self):
        result = station.takeover(self.ws, self.request, 'op-ownership-takeover', 0)
        self.task = task_store.read_task(self.ws)
        return result

    def markers(self):
        result = jira_watermark.prepare(self.ws, 'TAP-123', self.snapshot)
        self.snapshot['issue']['fields'].update(result['expected_fields'])
        jira_watermark.complete(self.ws, 'TAP-123', 'unknown', self.snapshot)
        self.task = task_store.read_task(self.ws)

    def apply(self, action, payload):
        state = quality.load(self.ws, self.task)
        quality.apply(self.ws, 'TAP-123', self.task['run_id'], state['revision'],
                      {'action': action, 'payload': payload})
        return quality.replay(quality.load(self.ws, self.task))

    def notice(self, branch="develop", unknown=False):
        rows = external_sync.actions(self.ws, self.task)['sync_actions']
        row = next(r for r in rows if r['kind'] == 'takeover_comment')
        self.assertIn('源码准备尚未完成', row['body'])
        self.assertFalse(any(line.startswith(('- ', '* ')) for line in row['body'].splitlines()))
        self.assertIn('tapdata/tapdata：' + branch, row['body'])
        model = self.apply('draft', {'id': row['id'], 'body': row['body']})
        # publication_digest 是 report 派生值，使用原生查询回读后确认。
        rules = quality.config(self.ws, self.task)
        view = quality.report(quality.load(self.ws, self.task), rules,
                              quality.context(self.ws, self.task), base=self.ws, task=self.task)
        digest = view['publications'][row['id']]['digest']
        self.apply('confirm', {'id': row['id'], 'digest': digest,
            'proof': {'actor': 'Fixture', 'source': 'user_message', 'reference': 'fixture:takeover',
                      'at': datetime.now(timezone.utc).isoformat()}})
        model = self.apply('prepare_write', {'id': row['id'], 'digest': digest,
                                           'takeover_snapshot': self.snapshot})
        record = model['publications'][row['id']]
        if unknown:
            self.apply('receipt', {'id': row['id'], 'operation_id': record['operation_id'],
                                  'result': 'unknown', 'reason': 'fixture:timeout'})
            return row
        self.apply('readback', {'id': row['id'], 'operation_id': record['operation_id'],
            'site': record['site'], 'issue_key': 'TAP-123', 'comment_id': '12345',
            'body': row['body'], 'source_ref': 'fixture:jira/comment/12345',
            'author_account_id': 'fixture-user'})
        return row

    def test_preflight_markers_comment_status_then_source(self):
        operation = self.begin()
        self.assertEqual('awaiting_markers', operation['phase'])
        self.assertFalse((self.ws / 'source/tapdata/tapdata').exists())
        with self.assertRaisesRegex(ValueError, '归属'):
            jira_ownership.require(self.ws, 'TAP-123')
        self.markers()
        row = self.notice()
        pending = station.takeover(self.ws, self.request, operation['operation_id'], 0)
        self.assertEqual('awaiting_jira_status', pending['phase'])
        snapshot = copy.deepcopy(self.snapshot)
        snapshot['issue']['fields']['status'] = {'id': '3', 'name': 'In Progress'}
        result = jira_status.prepare(self.ws, 'TAP-123', 'takeover', snapshot)
        self.assertEqual('satisfied', result['outcome'])
        done = station.takeover(self.ws, self.request, operation['operation_id'], 0)
        self.assertEqual('done', done['status'])
        self.assertTrue(task_store.read_task(self.ws)['source_prepared'])
        self.assertEqual(operation['run_id'], done['run_id'])
        self.assertFalse(any(r['id'] == row['id'] for r in external_sync.actions(self.ws, task_store.read_task(self.ws))['sync_actions']))

    def test_missing_branch_creates_no_operation_or_comment(self):
        self.request['version'] = 'absent'
        self.request['explicit_branches'] = {}
        with self.assertRaisesRegex(ValueError, '分支'):
            self.begin()
        self.assertIsNone(station_operation.read(self.ws))
        self.assertIsNone(task_store.read_task(self.ws))

    def test_unreadable_or_partial_markers_do_not_reserve(self):
        del self.request['jira_snapshot']['issue']['fields']['customfield_10462']
        with self.assertRaisesRegex(ValueError, '不完整'):
            self.begin()
        self.assertIsNone(station_operation.read(self.ws))

    def test_two_stations_can_both_observe_success_until_next_readback(self):
        other = OwnershipTests('test_missing_branch_creates_no_operation_or_comment')
        other.setUp()
        self.addCleanup(other.doCleanups)
        self.begin(); other.begin()
        self.markers(); other.markers()
        jira_ownership.require(self.ws, 'TAP-123')
        jira_ownership.require(other.ws, 'TAP-123')
        observed = copy.deepcopy(other.snapshot)
        result = jira_watermark.complete(self.ws, 'TAP-123', 'unknown', observed)
        self.assertEqual('unknown', result['outcome'])
        with self.assertRaisesRegex(ValueError, '归属'):
            jira_ownership.require(self.ws, 'TAP-123')
        jira_ownership.require(other.ws, 'TAP-123')
        self.assertNotIn('native_request', result)

    def test_correct_published_sources_keeps_original_run_and_first_markers(self):
        operation = self.begin(); self.markers()
        initial = copy.deepcopy(jira_watermark.status(self.ws, self.task)['expected_fields'])
        old = self.notice()
        corrected = station_takeover.correct(self.ws, 'TAP-123', self.task['run_id'], operation['operation_id'],
            self.task['_revision'], operation['request_digest'], 'main', {'tapdata/tapdata': 'main'}, 'user:main')
        row = self.notice('main')
        self.assertNotEqual(old['id'], row['id'])
        self.assertIn('替代本 run', row['body'])
        self.assertEqual(initial, jira_watermark.status(self.ws, self.task)['expected_fields'])
        self.assertEqual(operation['run_id'], corrected['run_id'])
        self.assertEqual(2, len(quality.replay(quality.load(self.ws, self.task))['publications']))
        self.task = task_store.read_task(self.ws)
        station_takeover.correct(self.ws, 'TAP-123', self.task['run_id'], operation['operation_id'],
            self.task['_revision'], corrected['request_digest'], 'develop', {'tapdata/tapdata': 'develop'}, 'user:revert')
        reverted = self.notice('develop')
        self.assertNotIn(reverted['id'], (old['id'], row['id']))
        self.assertEqual(3, len(quality.replay(quality.load(self.ws, self.task))['publications']))
        self.assertFalse(any(r['kind'] == 'takeover_comment' for r in external_sync.actions(self.ws, self.task)['sync_actions']))

    def test_frozen_expanded_sources_replace_original_preflight(self):
        operation = self.begin()
        task = copy.deepcopy(self.task)
        current = copy.deepcopy(operation['takeover_baseline'])
        current['repositories']['tapdata/additional'] = copy.deepcopy(next(iter(current['repositories'].values())))
        task['engineering_baseline'] = current
        self.assertIn('tapdata/additional', external_sync.takeover_sources(self.ws, task))
        self.assertNotIn('tapdata/additional', external_sync.takeover_sources(self.ws, self.task))

    def test_old_comment_is_not_reissued_for_upgrade_but_expansion_is_explicit(self):
        operation = self.begin(); self.markers()
        key = external_sync.takeover_publication_id(self.ws, self.task, {})
        records = {key: {'status': 'verified', 'body': '旧接管模板'}}
        self.assertEqual(key, external_sync.takeover_publication_id(self.ws, self.task, records))
        task = copy.deepcopy(self.task)
        task['engineering_baseline'] = copy.deepcopy(operation['takeover_baseline'])
        task['replan'] = {'sources': {name: {} for name in task['engineering_baseline']['repositories']}}
        task['engineering_baseline']['repositories']['tapdata/additional'] = copy.deepcopy(next(iter(task['engineering_baseline']['repositories'].values())))
        changed = external_sync.takeover_publication_id(self.ws, task, records)
        self.assertTrue(changed.startswith(key + '-sources-'))
        body = external_sync.takeover_body(task, changed, jira_watermark.status(self.ws, self.task), external_sync.takeover_sources(self.ws, task))
        records[changed] = {'status': 'verified', 'body': body}
        self.assertEqual(changed, external_sync.takeover_publication_id(self.ws, task, records))

    def test_marker_input_path_is_available_before_ownership_without_development(self):
        import contextlib, io
        from types import SimpleNamespace
        from workflow import task as cli
        self.begin()
        args = SimpleNamespace(dir=self.ws, issue_key='TAP-123', expected_run_id=self.task['run_id'],
                               name='marker-readback.json')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, cli.cmd_interaction_path(args))
        self.assertTrue(output.getvalue().strip().endswith('/interactions/marker-readback.json'))
        with self.assertRaisesRegex(ValueError, '未完成操作'):
            task_store.require_development(self.ws, self.task)
        args.expected_run_id = 'wrong-run'
        with self.assertRaises(ValueError):
            cli.cmd_interaction_path(args)

    def test_first_marker_prepare_must_match_intake_issue_id(self):
        self.begin()
        task = task_store.read_task(self.ws)
        task['facts']['jira_routing'] = {'issue_id': '123', 'issue_type_id': '10011',
            'issue_type_name': 'Bug', 'task_class': task['task_class'], 'source_ref': 'fixture:intake'}
        task_store.write_task(self.ws, task)
        changed = copy.deepcopy(self.snapshot)
        changed['issue']['id'] = '999'
        with self.assertRaisesRegex(ValueError, '任务 ID'):
            jira_watermark.prepare(self.ws, 'TAP-123', changed)
        self.assertEqual('missing', jira_watermark.status(self.ws, task)['outcome'])

    def test_readback_identity_and_ended_run_cannot_restore_ownership(self):
        self.begin(); self.markers()
        changed = copy.deepcopy(self.snapshot)
        changed['issue']['id'] = '999'
        with self.assertRaisesRegex(ValueError, '任务 ID'):
            jira_watermark.complete(self.ws, 'TAP-123', 'unknown', changed)
        with self.assertRaisesRegex(ValueError, '归属'):
            jira_ownership.require(self.ws, 'TAP-123')
        jira_watermark.complete(self.ws, 'TAP-123', 'unknown', self.snapshot)
        task = task_store.read_task(self.ws)
        task['outcome'] = 'interrupted'
        task_store.write_task(self.ws, task)
        with self.assertRaisesRegex(ValueError, '已结束'):
            jira_ownership.require(self.ws, 'TAP-123')

    def test_unknown_comment_allows_state_attempt_but_does_not_resend(self):
        operation = self.begin(); self.markers()
        row = self.notice(unknown=True)
        pending = station.takeover(self.ws, self.request, operation['operation_id'], 0)
        self.assertEqual('awaiting_jira_status', pending['phase'])
        snapshot = copy.deepcopy(self.snapshot)
        snapshot['issue']['fields']['status'] = {'id': '3', 'name': 'In Progress'}
        jira_status.prepare(self.ws, 'TAP-123', 'takeover', snapshot)
        done = station.takeover(self.ws, self.request, operation['operation_id'], 0)
        self.assertEqual('done', done['status'])
        action = next(r for r in external_sync.actions(self.ws, task_store.read_task(self.ws))['sync_actions']
                      if r['id'] == row['id'])
        self.assertEqual('readback', action['next_action'])
        self.assertNotIn('draft_command', action)
        self.assertNotIn('native_request', action)

    def test_notice_draft_is_not_a_publication_attempt(self):
        self.begin(); self.markers()
        row = next(r for r in external_sync.actions(self.ws, self.task)['sync_actions']
                   if r['kind'] == 'takeover_comment')
        self.apply('draft', {'id': row['id'], 'body': row['body']})
        with self.assertRaisesRegex(ValueError, '先发布'):
            jira_status.prepare(self.ws, 'TAP-123', 'takeover', self.snapshot)

    def test_unreadable_identity_revokes_previous_proof(self):
        self.begin(); self.markers()
        broken = copy.deepcopy(self.snapshot)
        broken['issue']['key'] = 'TAP-999'
        with self.assertRaises(ValueError):
            jira_watermark.complete(self.ws, 'TAP-123', 'unknown', broken)
        with self.assertRaisesRegex(ValueError, '归属'):
            jira_ownership.require(self.ws, 'TAP-123')


if __name__ == '__main__':
    unittest.main()

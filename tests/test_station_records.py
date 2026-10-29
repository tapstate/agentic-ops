#!/usr/bin/env python3
"""持久归档与清理独立；Jira 回读是接续依据，本地副本失败不得伪造丢失。"""
import json
from pathlib import Path
import sys
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_station_lifecycle as fixture
from workflow import station, station_archive as archives, task_store, station_operation, station_cleanup


class RecordTests(unittest.TestCase):
    setUp = fixture.StationTests.setUp
    prepare_engineering = fixture.StationTests.prepare_engineering
    write = fixture.StationTests.write
    git = fixture.StationTests.git
    takeover = fixture.StationTests.takeover

    def begin(self):
        self.task = self.takeover()
        self.handoff = {'handoff': {'plan': '已确认方案', 'progress': '完成方案', 'remaining': '继续实现', 'environment': '研发补齐配置'}}
        return self.archive()

    def archive(self, readback=None, op='op-records-handoff'):
        task = self.task
        return station.execute(self.ws, 'archive', task['issue_key'], task['run_id'], task['_revision'], op, self.handoff, jira_readback=readback)

    def receipt(self, pending, sent=True):
        result = pending['archive_result']
        receipt = {key: result[key] for key in ('site', 'issue_key', 'body')}
        receipt['source_ref'] = 'fixture:jira/readback'
        receipt.update({'comment_id': '101'} if sent else {'status': 'not_sent', 'decision_ref': '用户明确未发送并取消'})
        return receipt

    def cleanup_request(self):
        return {'scope': 'task', 'expected_plan_digest': station_cleanup.plan(self.ws)['digest'], 'materials': 'discard', 'decision_ref': '用户确认清理本地成果'}

    def test_completed_task_can_archive_without_restoring_development(self):
        from workflow import jira_ownership
        self.task = self.takeover()
        self.task.update(stage='completed', outcome='completed', terminal_proof={'fixture': 'already-verified'})
        task_store.write_task(self.ws, self.task)
        self.task = task_store.read_task(self.ws)
        self.handoff = {'handoff': {'plan': '已确认方案', 'progress': '已完成', 'remaining': '无', 'environment': '已记录'}}
        with self.assertRaisesRegex(ValueError, '已结束'):
            jira_ownership.require(self.ws, self.task['issue_key'])
        pending = self.archive()
        self.assertEqual('awaiting_jira', pending['archive_result']['status'])
        saved = self.archive(self.receipt(pending))
        self.assertEqual('done', saved['status'])
        with self.assertRaisesRegex(ValueError, '归档|终止'):
            task_store.require_development(self.ws, task_store.read_task(self.ws))

    def test_cleanup_correction_survives_new_task_and_is_read_back_without_reclean(self):
        task = self.takeover()
        request = self.cleanup_request()
        request['progress_correction'] = '放弃本轮实现，仅保留已确认方案；后续需重新实施。'
        result = station_cleanup.execute(self.ws, task['issue_key'], task['run_id'], task['_revision'],
            'op-clean-progress-correction', request)
        self.assertTrue(result['cleanup_result']['complete'])
        candidate = result['cleanup_result']['progress_correction']
        self.assertEqual('pending', candidate['status'])
        self.assertIsNone(task_store.read_task(self.ws))
        next_task = self.takeover('op-new-after-correction')
        with self.assertRaisesRegex(ValueError, '待外部回读的旧操作'):
            station_cleanup.execute(self.ws, next_task['issue_key'], next_task['run_id'], next_task['_revision'],
                'op-clean-progress-correction', self.cleanup_request())
        again = station_cleanup.execute(self.ws, next_task['issue_key'], next_task['run_id'], next_task['_revision'],
            'op-clean-with-previous-correction', self.cleanup_request())
        self.assertTrue(again['cleanup_result']['complete'])
        self.assertEqual(['op-clean-progress-correction'], station_cleanup.pending_corrections(self.ws))
        next_task = self.takeover('op-third-after-correction')
        before = task_store.current_path(self.ws).read_bytes()
        receipt = {key: candidate[key] for key in ('site', 'body')}
        receipt.update(issue_key=task['issue_key'], comment_id='200', source_ref='fixture:jira/comment/200')
        bad = dict(receipt, issue_key='TAP-999')
        with self.assertRaisesRegex(ValueError, '不匹配'):
            station_cleanup.correction_readback(self.ws, 'op-clean-progress-correction', bad)
        value = station_cleanup.correction_readback(self.ws, 'op-clean-progress-correction', receipt)
        self.assertEqual('saved', value['progress_correction'])
        station_cleanup.correction_readback(self.ws, 'op-clean-progress-correction', receipt)
        self.assertEqual(before, task_store.current_path(self.ws).read_bytes())
        self.assertEqual(next_task['run_id'], task_store.read_task(self.ws)['run_id'])

    def test_task_cleanup_uses_owner_to_remove_repair_backups(self):
        task = self.takeover()
        document = task_store.read_current(self.ws)
        document['schema_version'] = 2
        task_store._write_json_atomic(task_store.current_path(self.ws), document)
        report = task_store.inspect_data(self.ws)
        request = {'source_digest': report['source_digest'], 'renames': {}, 'analysis_ref': '测试已核实相同字段语义'}
        preview = task_store.preview_data_repair(self.ws, request)
        repaired = task_store.apply_data_repair(self.ws, request, preview['proposal_digest'], '研发确认', True)
        self.assertTrue(Path(repaired['backup']).exists())
        result = station_cleanup.execute(self.ws, task['issue_key'], task['run_id'], task['_revision'],
            'op-clean-repaired-task', self.cleanup_request())
        self.assertTrue(result['cleanup_result']['complete'])
        self.assertFalse(Path(repaired['backup']).exists())
        self.assertIsNone(task_store.read_task(self.ws))

    def test_archive_resumes_after_operation_saved_before_payload(self):
        from workflow import station_clean
        self.task = self.takeover()
        self.handoff = {'handoff': {'plan': '已确认方案', 'progress': '完成方案',
                                    'remaining': '继续实现', 'environment': '研发补齐配置'}}
        with mock.patch.object(station_clean, '_handoff_archive', side_effect=OSError('interrupted before payload')):
            with self.assertRaises(OSError):
                self.archive()
        pending = self.archive()
        self.assertEqual('awaiting_jira', pending['archive_result']['status'])
        saved = self.archive(self.receipt(pending))
        self.assertEqual('saved', saved['archive_result']['status'])
        self.assertEqual(self.task, task_store.read_task(self.ws))

    def test_handoff_does_not_collect_discarded_local_snapshot(self):
        with mock.patch.object(archives, 'redact_evidence', side_effect=AssertionError('unused legacy collection')):
            pending = self.begin()
        self.assertEqual('awaiting_jira', pending['archive_result']['status'])

    def test_archive_receipt_after_handoff_does_not_require_reclaim(self):
        import os
        from workflow import execution
        from station_fixture import transferred_ownership_fixture
        pending = self.begin()
        transferred_ownership_fixture(self.ws)
        with self.assertRaisesRegex(ValueError, '归属尚未核验'):
            self.archive(op='op-new-archive-after-handoff')
        with self.assertRaises(ValueError):
            self.archive(dict(self.receipt(pending), body='错误正文'))
        with mock.patch.dict(os.environ, {execution.SESSION_ENV: 'b' * 32}):
            with self.assertRaisesRegex(ValueError, '已有执行接管者'):
                self.archive(self.receipt(pending))
        saved = self.archive(self.receipt(pending))
        self.assertEqual('saved', saved['archive_result']['status'])
        result = station_cleanup.execute(self.ws, self.task['issue_key'], self.task['run_id'],
            self.task['_revision'], 'op-clean-after-archive-readback', self.cleanup_request())
        self.assertTrue(result['cleanup_result']['complete'])

    def test_previous_null_payload_can_resume_before_any_jira_write(self):
        pending = self.begin()
        pending['archive_payload']['snapshot']['handoff'] = None
        pending.pop('archive_result')
        station_operation.save(self.ws, pending)
        restored = self.archive()
        self.assertEqual('awaiting_jira', restored['archive_result']['status'])
        self.assertEqual('saved', self.archive(self.receipt(restored))['archive_result']['status'])

    def test_invalid_optional_local_copy_does_not_block_verified_jira(self):
        pending = self.begin()
        from workflow import archive_store
        directory = archive_store.run_directory(self.ws, self.task['run_id'])
        directory.mkdir(exist_ok=True)
        damaged = directory / 'record.json'
        damaged.write_text('invalid local copy')
        saved = self.archive(self.receipt(pending))
        self.assertEqual('done', saved['status'])
        self.assertEqual('saved', saved['archive_result']['status'])
        self.assertIn('local_warning', saved['archive_result'])
        self.assertEqual('invalid local copy', damaged.read_text())
        self.assertEqual(self.task, task_store.read_task(self.ws))

    def test_archive_does_not_collect_configuration_or_runtime(self):
        pending = self.begin()
        (self.ws / 'config/private').write_text('must-not-copy-config')
        (self.ws / 'runtime/private').write_text('must-not-copy-runtime')
        result = self.archive(self.receipt(pending))
        self.assertEqual('saved', result['archive_result']['status'])
        files = list((self.product / '.archive' / self.task['run_id']).glob('result-*.json'))
        self.assertEqual(1, len(files))
        self.assertNotIn('must-not-copy', files[0].read_text())
        self.assertEqual(self.task, task_store.read_task(self.ws))

    def test_local_archive_failure_keeps_verified_jira_result(self):
        pending = self.begin()
        with mock.patch.object(archives, 'publish', side_effect=PermissionError('local disk unavailable')):
            result = self.archive(self.receipt(pending))
        self.assertEqual('done', result['status'])
        self.assertEqual('saved', result['archive_result']['status'])
        self.assertIn('local_warning', result['archive_result'])
        self.assertEqual(self.task, task_store.read_task(self.ws))

    def test_unknown_jira_write_cannot_be_bypassed_by_cleanup(self):
        self.begin()
        with self.assertRaisesRegex(ValueError, 'Jira 写入尚未核实'):
            station.execute(self.ws, 'clean', self.task['issue_key'], self.task['run_id'], self.task['_revision'], 'op-records-bypass', self.cleanup_request())
        self.assertTrue((self.ws / 'source/tapdata/tapdata/file.txt').exists())

    def test_completed_local_write_with_lost_receipt_is_not_a_lost_jira_archive(self):
        pending = self.begin()
        publish = archives.publish
        def lose_receipt(*args, **kwargs):
            publish(*args, **kwargs)
            raise OSError('receipt lost')
        with mock.patch.object(archives, 'publish', side_effect=lose_receipt):
            result = self.archive(self.receipt(pending))
        self.assertEqual('saved', result['archive_result']['status'])
        self.assertTrue(archives.observe(self.ws, result['archive_payload']))
        self.assertEqual('done', self.archive()['status'])

    def test_bad_jira_readback_keeps_original_pending(self):
        pending = self.begin()
        receipt = self.receipt(pending)
        receipt['body'] += '不同正文'
        with self.assertRaises(ValueError):
            self.archive(receipt)
        self.assertEqual('running', station_operation.read(self.ws)['status'])
        self.assertEqual(pending['archive_result']['body'], self.archive()['archive_result']['body'])

    def test_not_sent_decision_survives_result_save_interruption(self):
        pending = self.begin()
        save = station_operation.save
        def interrupted(base, operation):
            if operation.get('archive_result', {}).get('status') == 'discarded':
                raise OSError('result save interrupted')
            return save(base, operation)
        with mock.patch.object(station_operation, 'save', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.archive(self.receipt(pending, sent=False))
        result = self.archive()
        self.assertEqual('done', result['status'])
        self.assertEqual('discarded', result['archive_result']['status'])
        self.assertEqual(self.task, task_store.read_task(self.ws))

    def test_partial_local_write_retains_jira_and_can_retry_same_material(self):
        pending = self.begin()
        write = task_store._write_json_atomic
        def partial(path, value):
            if path.name.startswith('result-'):
                raise OSError('snapshot write failed')
            return write(path, value)
        with mock.patch.object(task_store, '_write_json_atomic', side_effect=partial):
            saved = self.archive(self.receipt(pending))
        self.assertEqual('saved', saved['archive_result']['status'])
        again = self.archive(op='op-records-repeat')
        self.assertEqual(pending['archive_result']['body'], again['archive_result']['body'])
        result = self.archive(self.receipt(again), op='op-records-repeat')
        self.assertTrue(archives.observe(self.ws, result['archive_payload']))

    def test_archive_snapshot_is_immutable_after_cleanup(self):
        pending = self.begin()
        self.archive(self.receipt(pending))
        files = {p: p.read_bytes() for p in (self.product / '.archive' / self.task['run_id']).glob('*.json')}
        result = station.execute(self.ws, 'clean', self.task['issue_key'], self.task['run_id'], self.task['_revision'], 'op-later-cleanup', self.cleanup_request())
        self.assertTrue(result['cleanup_result']['complete'], result)
        for path, data in files.items():
            self.assertEqual(data, path.read_bytes())


if __name__ == '__main__':
    unittest.main()

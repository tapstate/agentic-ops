#!/usr/bin/env python3
"""归档记录结果，不采集源码、配置和会话，不阻止清理。"""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_station_lifecycle as fixture
from workflow import station, station_clean, station_archive as archives, task_store, station_operation


class RecordTests(unittest.TestCase):
    setUp = fixture.StationTests.setUp
    prepare_engineering = fixture.StationTests.prepare_engineering
    write = fixture.StationTests.write
    git = fixture.StationTests.git
    takeover = fixture.StationTests.takeover

    def test_archive_has_only_available_task_state_and_gaps(self):
        task = self.takeover()
        (self.ws / 'config/private').write_text('must-not-read-config')
        (self.ws / 'runtime/private').write_text('must-not-read-runtime')
        op = station.execute(self.ws, 'archive', task['issue_key'], task['run_id'], task['_revision'], 'op-records-test', {})
        files = list((self.product / '.archive' / task['run_id']).glob('result-*.json'))
        self.assertEqual(1, len(files)); saved = json.loads(files[0].read_text())
        self.assertTrue(saved['gaps']); self.assertEqual('available_state', saved['coverage'])
        self.assertNotIn('must-not-read', files[0].read_text())
        self.assertEqual(task, task_store.read_task(self.ws))

    def test_archive_failure_waits_for_explicit_decision_and_freezes_snapshot(self):
        task = self.takeover()
        with mock.patch.object(archives, 'publish', side_effect=PermissionError('archive unavailable')):
            result = station.execute(self.ws, 'archive', task['issue_key'], task['run_id'], task['_revision'], 'op-records-failure', {})
        self.assertEqual('running', result['status']); self.assertEqual('awaiting_decision', result['archive_result']['status'])
        self.assertEqual(task, task_store.read_task(self.ws))
        payload = copy.deepcopy(result['archive_payload'])
        with mock.patch.object(archives, 'publish', side_effect=AssertionError('不得自动重试')):
            station.execute(self.ws, 'archive', task['issue_key'], task['run_id'], task['_revision'], 'op-records-failure', {})
        result = station_clean.execute(self.ws, 'archive', task['issue_key'], task['run_id'], task['_revision'], 'op-records-failure', {},
                                       archive_decision={'action': 'retry', 'digest': result['archive_result']['digest']})
        self.assertEqual('done', result['status'])
        files = list((self.product / '.archive' / task['run_id']).glob('result-*.json'))
        self.assertEqual(payload['snapshot'], json.loads(files[0].read_text()))

    def test_discard_requires_matching_digest_and_cannot_bypass_with_clean(self):
        task = self.takeover()
        with mock.patch.object(archives, 'publish', side_effect=PermissionError('unwritable')):
            op = station.execute(self.ws, 'archive', task['issue_key'], task['run_id'], task['_revision'], 'op-records-discard', {})
        with self.assertRaisesRegex(ValueError, '先恢复原操作'):
            station.execute(self.ws, 'clean', task['issue_key'], task['run_id'], task['_revision'], 'op-bypass-records', {})
        with self.assertRaisesRegex(ValueError, '匹配'):
            station_clean.execute(self.ws, 'archive', task['issue_key'], task['run_id'], task['_revision'], 'op-records-discard', {},
                                  archive_decision={'action': 'discard', 'digest': '0' * 64})
        result = station_clean.execute(self.ws, 'archive', task['issue_key'], task['run_id'], task['_revision'], 'op-records-discard', {},
                                       archive_decision={'action': 'discard', 'digest': op['archive_result']['digest']})
        self.assertEqual('discarded', result['archive_result']['status'])
        self.assertEqual(task, task_store.read_task(self.ws))

    def test_completed_write_with_lost_receipt_is_read_back_before_discard(self):
        task = self.takeover()
        publish = archives.publish
        def lose_receipt(*args, **kwargs):
            publish(*args, **kwargs)
            raise OSError('receipt lost')
        with mock.patch.object(archives, 'publish', side_effect=lose_receipt):
            op = station.execute(self.ws, 'archive', task['issue_key'], task['run_id'], task['_revision'], 'op-records-lost', {})
        result = station_clean.execute(self.ws, 'archive', task['issue_key'], task['run_id'], task['_revision'], 'op-records-lost', {},
                                       archive_decision={'action': 'discard', 'digest': op['archive_result']['digest']})
        self.assertEqual('saved', result['archive_result']['status'])

    def test_unknown_readback_cannot_be_discarded(self):
        task = self.takeover()
        with mock.patch.object(archives, 'publish', side_effect=PermissionError('unwritable')):
            op = station.execute(self.ws, 'archive', task['issue_key'], task['run_id'], task['_revision'], 'op-records-unknown', {})
        with mock.patch.object(archives, 'observe', side_effect=PermissionError('unreadable')):
            with self.assertRaises(PermissionError):
                station_clean.execute(self.ws, 'archive', task['issue_key'], task['run_id'], task['_revision'], 'op-records-unknown', {},
                                      archive_decision={'action': 'discard', 'digest': op['archive_result']['digest']})
        self.assertEqual('running', station_operation.read(self.ws)['status'])

    def test_discard_authorization_survives_interrupted_result_save(self):
        task = self.takeover()
        with mock.patch.object(archives, 'publish', side_effect=PermissionError('unwritable')):
            op = station.execute(self.ws, 'archive', task['issue_key'], task['run_id'], task['_revision'], 'op-records-interrupt', {})
        save = station_operation.save
        calls = []
        def interrupted(base, operation):
            calls.append(1)
            if len(calls) == 2:
                raise OSError('result save interrupted')
            return save(base, operation)
        with mock.patch.object(station_operation, 'save', side_effect=interrupted):
            with self.assertRaises(OSError):
                station_clean.execute(self.ws, 'archive', task['issue_key'], task['run_id'], task['_revision'], 'op-records-interrupt', {},
                                      archive_decision={'action': 'discard', 'digest': op['archive_result']['digest']})
        result = station.execute(self.ws, 'archive', task['issue_key'], task['run_id'], task['_revision'], 'op-records-interrupt', {})
        self.assertEqual('done', result['status'])
        self.assertEqual('discarded', result['archive_result']['status'])
        self.assertEqual(1, len(result['archive_decisions']))

    def test_partial_record_write_retries_identical_frozen_payload(self):
        task = self.takeover()
        write = task_store._write_json_atomic
        def partial(path, value):
            if path.name.startswith('result-'):
                raise OSError('snapshot write failed')
            return write(path, value)
        with mock.patch.object(task_store, '_write_json_atomic', side_effect=partial):
            op = station.execute(self.ws, 'archive', task['issue_key'], task['run_id'], task['_revision'], 'op-records-partial', {})
        self.assertTrue((self.product / '.archive' / task['run_id'] / 'record.json').exists())
        result = station_clean.execute(self.ws, 'archive', task['issue_key'], task['run_id'], task['_revision'], 'op-records-partial', {},
                                       archive_decision={'action': 'retry', 'digest': op['archive_result']['digest']})
        self.assertEqual('saved', result['archive_result']['status'])
        self.assertTrue(archives.observe(self.ws, op['archive_payload']))

    def test_archive_snapshot_is_immutable_after_later_cleanup(self):
        task = self.takeover()
        station.execute(self.ws, 'archive', task['issue_key'], task['run_id'], task['_revision'], 'op-first-records', {})
        files = {p: p.read_bytes() for p in (self.product / '.archive' / task['run_id']).glob('*.json')}
        station.execute(self.ws, 'release', task['issue_key'], task['run_id'], task['_revision'], 'op-later-cleanup', {})
        for path, data in files.items(): self.assertEqual(data, path.read_bytes())


if __name__ == '__main__': unittest.main()

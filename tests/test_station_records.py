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
from workflow import station, station_archive as archives, task_store


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

    def test_archive_failure_does_not_leave_pending_operation(self):
        task = self.takeover()
        with mock.patch.object(archives, 'publish', side_effect=RuntimeError('archive unavailable')):
            result = station.execute(self.ws, 'archive', task['issue_key'], task['run_id'], task['_revision'], 'op-records-failure', {})
        self.assertEqual('done', result['status']); self.assertEqual('warning', result['archive_result']['status'])
        self.assertEqual(task, task_store.read_task(self.ws))

    def test_archive_snapshot_is_immutable_after_later_cleanup(self):
        task = self.takeover()
        station.execute(self.ws, 'archive', task['issue_key'], task['run_id'], task['_revision'], 'op-first-records', {})
        files = {p: p.read_bytes() for p in (self.product / '.archive' / task['run_id']).glob('*.json')}
        station.execute(self.ws, 'release', task['issue_key'], task['run_id'], task['_revision'], 'op-later-cleanup', {})
        for path, data in files.items(): self.assertEqual(data, path.read_bytes())


if __name__ == '__main__': unittest.main()

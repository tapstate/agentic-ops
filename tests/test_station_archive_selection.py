"""原计划运行证据选择的兼容修订与恢复。"""
import json
import unittest
from unittest import mock

import test_station_lifecycle as lifecycle
import test_station_resources as resource_tests
from workflow import archive_store, station, station_resources as resources, station_operation as operations, task_store


class ArchiveSelectionTests(unittest.TestCase):
    setUp = lifecycle.StationTests.setUp
    prepare_engineering = lifecycle.StationTests.prepare_engineering
    write = lifecycle.StationTests.write
    git = lifecycle.StationTests.git
    takeover = lifecycle.StationTests.takeover
    ready = resource_tests.ResourceTests.ready
    reset_request = resource_tests.ResourceTests.reset_request
    execute = resource_tests.ResourceTests.execute

    def test_explicit_empty_selection_preserves_history_and_resumes(self):
        task = self.ready()
        logs = self.ws / 'runtime/logs'
        logs.mkdir(exist_ok=True)
        (logs / 'unsupported.bin').write_bytes(b'\xff')
        request = self.reset_request(task)
        with mock.patch.object(station.archives, 'publish', side_effect=ValueError('interrupted')), self.assertRaisesRegex(ValueError, 'interrupted'):
            self.execute(task, request, op='op-selection-recovery')
        operation = operations.read(self.ws)
        original = operation['cleanup_plan']
        self.assertEqual([], resources.plan(self.ws, task)['archive_runtime'])
        plan = resources.plan(self.ws, task, archive_runtime=[])
        current = task_store.read_task(self.ws)
        station.amend_cleanup(self.ws, task['issue_key'], task['run_id'], current['_revision'],
            operation['operation_id'], original['digest'], {'archive_runtime': [],
                'confirmed_digest': plan['digest'], 'expected_plan_revision': 0, 'decision_ref': 'fixture:user'})
        amended = operations.read(self.ws)
        self.assertEqual(original, amended['plan_revisions'][0]['plan'])
        self.assertEqual([], resources.plan(self.ws, current)['archive_runtime'])
        self.execute(task, request, op=operation['operation_id'])
        self.assertIsNone(task_store.read_task(self.ws))
        saved = json.loads((archive_store.run_directory(self.ws, task['run_id']) / 'runtime-evidence.json').read_text())
        self.assertEqual({}, saved['files'])

    def test_selection_without_current_operation_and_expansion_rejected(self):
        task = self.ready()
        before = (self.ws / '.agenticops/operation.json').read_bytes()
        for selection in ([], ['elsewhere'], ['logs', 'logs']):
            with self.assertRaisesRegex(ValueError, '显式收缩'):
                resources.plan(self.ws, task, archive_runtime=selection)
        self.assertEqual(before, (self.ws / '.agenticops/operation.json').read_bytes())


if __name__ == '__main__':
    unittest.main()

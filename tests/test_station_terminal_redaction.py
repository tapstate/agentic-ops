#!/usr/bin/env python3
"""真实工位六阶段清理的外部终态脱敏回归。"""
import copy
import json
import unittest
from unittest import mock

import test_station_resources as fixtures
from workflow import archive_store, station_archive, station_reset_result, task_store
from workflow import station_resources as resources, engineering_baseline as baseline


class TerminalRedactionTests(unittest.TestCase):
    setUp = fixtures.ResourceTests.setUp
    prepare_engineering = fixtures.ResourceTests.prepare_engineering
    write = fixtures.ResourceTests.write
    git = fixtures.ResourceTests.git
    takeover = fixtures.ResourceTests.takeover
    ready = fixtures.ResourceTests.ready
    reset_request = fixtures.ResourceTests.reset_request
    execute = fixtures.ResourceTests.execute

    def external(self):
        return {'kind': 'external', 'producer': 'docker-compose fixture',
                'id': 'container:terminal-fixture', 'resource_type': 'docker-container',
                'action': 'delete', 'status': 'cleaned',
                'readback_ref': '/Users/example/runtime/reports/container-after.json',
                'before': {'id': 'container:terminal-fixture', 'protected': False,
                           'mounts': [{'Source': '/Users/example/runtime/data', 'Destination': '/app/data'}]}}

    def test_clean_redacts_receipt_and_final_result_without_mutating_facts(self):
        task = self.ready()
        external = self.external()
        resources.register(self.ws, task['issue_key'], task['run_id'], [external])
        original = copy.deepcopy(resources.inventory(self.ws, task))
        request = self.reset_request(task)
        verify = station_reset_result.verify
        observations = []
        def verify_with_original_facts(base, current, operation):
            facts = resources.inventory(base, current)
            if facts:
                self.assertEqual(original, facts)
                observations.append(True)
            return verify(base, current, operation)
        redact = station_archive.redact_evidence
        with mock.patch.object(station_archive, 'redact_evidence', wraps=redact) as helper, mock.patch.object(station_reset_result, 'verify', side_effect=verify_with_original_facts):
            operation = self.execute(task, request)
        self.assertTrue(observations, '最终验收前必须观测到未改写的活动资源事实')
        self.assertGreater(helper.call_count, 1)
        self.assertIsNone(task_store.read_task(self.ws))
        self.assertEqual('done', operation['status'])
        reference = operation['final_task']['archive_ref']
        receipts = list(archive_store.receipts(self.ws, reference).glob('external-terminal-*.json'))
        self.assertEqual(1, len(receipts))
        receipt_text = receipts[0].read_text()
        receipt = json.loads(receipt_text)
        final = operation['cleanup_manifest']['result']
        self.assertNotIn('/Users/example/', receipt_text)
        self.assertNotIn('/Users/example/', json.dumps(final))
        self.assertEqual(baseline.digest(original), receipt['resources_digest'])
        for output in (receipt['resources'][0], final['external'][0]):
            for key in ('id', 'resource_type', 'action', 'status'):
                self.assertEqual(external[key], output[key])
            self.assertEqual('[redacted]', output['readback_ref'])
            self.assertEqual('[redacted]', output['before']['mounts'][0]['Source'])
        self.assertEqual('/Users/example/runtime/data', original[0]['before']['mounts'][0]['Source'])

    def test_sensitive_dictionary_key_fails_closed(self):
        task = self.ready()
        external = self.external()
        external['before']['/Users/example/private-key-name'] = 'ordinary value'
        resources.register(self.ws, task['issue_key'], task['run_id'], [external])
        with self.assertRaisesRegex(ValueError, '敏感'):
            self.execute(task, self.reset_request(task))
        self.assertIsNotNone(task_store.read_task(self.ws))
        operation = json.loads((self.ws / '.agenticops/operation.json').read_text())
        self.assertNotEqual('done', operation['status'])
        self.assertEqual([], list((self.product / '.archive').glob('**/external-terminal-*.json')))


if __name__ == '__main__':
    unittest.main()

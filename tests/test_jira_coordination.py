"""旧协调只保留退役核验，不再发送、报活或判断闲置。"""
import copy
import json
import unittest
import test_station_lifecycle as fixture
from workflow import jira_coordination as legacy, task_store, execution


class LegacyCoordinationTests(unittest.TestCase):
    setUp = fixture.StationTests.setUp
    write = fixture.StationTests.write

    def install(self, pending=True):
        packet = {'format': legacy.MARKER, 'site': 'https://tapdata.atlassian.net', 'issue_id': '123',
            'issue_key': 'TAP-123', 'station_id': 'a'*32, 'session_id': execution.session_id(),
            'executor': 'Fixture', 'claim_id': 'old-claim', 'update_id': 'old-update', 'active': True,
            'supersedes': [], 'decision_ref': '', 'conflict_check_ref': ''}
        state = {'entries': {'old-claim': {'packet': packet, 'comment_id': '', 'pending': pending,
            'actor': execution.session_id(), 'before': None}}, 'proof': None}
        legacy.save(self.ws, state)
        return packet

    def observation(self, packet):
        return {'source_ref': 'fixture:jira', 'observations': [{'claim_id': 'old-claim',
            'site': packet['site'], 'issue_id': packet['issue_id'], 'comment_id': '7',
            'source_ref': 'fixture:jira/7', 'body': legacy.MARKER + '\n\n```json\n' +
                json.dumps(packet, ensure_ascii=False, sort_keys=True, indent=2) + '\n```'}]}

    def test_unknown_write_must_be_read_before_retirement(self):
        packet = self.install()
        with self.assertRaisesRegex(ValueError, '尚未退役'):
            legacy.require_retired(self.ws)
        before = legacy.state(self.ws)
        with self.assertRaisesRegex(ValueError, '逐项核实'):
            legacy.retire(self.ws, {'source_ref': 'fixture:jira'}, 'user:retire', 'old:stopped')
        self.assertEqual(before, legacy.state(self.ws))
        supplied = self.observation(packet)
        self.assertTrue(legacy.retire(self.ws, supplied, 'user:retire', 'old:stopped')['retired'])
        legacy.require_retired(self.ws)
        self.assertEqual([], legacy.pending(self.ws))
        self.assertTrue(legacy.state(self.ws)['entries']['old-claim']['packet']['active'])
        self.assertTrue(legacy.retire(self.ws, supplied, 'user:retire', 'old:stopped')['repeated'])

    def test_identity_mismatch_preserves_unknown_intent(self):
        packet = self.install()
        supplied = self.observation(packet)
        supplied['observations'][0]['issue_id'] = '456'
        with self.assertRaisesRegex(ValueError, '身份不符'):
            legacy.retire(self.ws, supplied, 'user:retire', 'old:stopped')
        self.assertTrue(legacy.pending(self.ws))

    def test_verified_not_applied_keeps_original_packet(self):
        packet = self.install()
        supplied = self.observation(packet)
        row = supplied['observations'][0]
        row.pop('body')
        row.update(confirmed_not_applied=True, not_applied_ref='fixture:complete-comments-and-original-result')
        legacy.retire(self.ws, supplied, 'user:retire', 'old:stopped')
        self.assertEqual(packet, legacy.state(self.ws)['entries']['old-claim']['packet'])

    def test_known_legacy_record_still_needs_stopped_executor(self):
        self.install(False)
        with self.assertRaisesRegex(ValueError, '停写'):
            legacy.retire(self.ws, {'source_ref': 'fixture:old'}, 'user:retire', '')
        self.assertTrue(legacy.retire(self.ws, {'source_ref': 'fixture:old'}, 'user:retire', 'old:stopped')['retired'])

    def test_new_station_has_no_coordination_write_or_idle_api(self):
        legacy.require_retired(self.ws)
        self.assertFalse(hasattr(legacy, 'run'))
        self.assertFalse(hasattr(legacy, 'IDLE_SECONDS'))
        self.assertFalse(hasattr(legacy, 'body'))


if __name__ == '__main__': unittest.main()

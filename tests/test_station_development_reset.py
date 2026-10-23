#!/usr/bin/env python3
"""源码处置必须明确；拒绝未保全删除，明确放弃后整区删除。"""
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_station_lifecycle as fixture
from workflow import station, task_store, station_cleanup


class DevelopmentPreservationTests(unittest.TestCase):
    setUp = fixture.StationTests.setUp
    prepare_engineering = fixture.StationTests.prepare_engineering
    write = fixture.StationTests.write
    git = fixture.StationTests.git
    takeover = fixture.StationTests.takeover

    def test_branch_and_staged_deletion_require_discard_before_cleanup(self):
        task = self.takeover(); repo = self.ws / 'source/tapdata/tapdata'
        self.git(repo, 'switch','-c','local-user-branch')
        self.git(repo, 'rm','file.txt')
        before = (self.git(repo,'show-ref'), self.git(repo,'status','--porcelain'), self.git(repo,'branch','--show-current'))
        request = {'scope': 'task', 'expected_plan_digest': station_cleanup.plan(self.ws)['digest'], 'materials': 'preserved', 'decision_ref': '本次清理决定'}
        with self.assertRaisesRegex(ValueError, '尚有未保全'):
            station.execute(self.ws,'clean',task['issue_key'],task['run_id'],task['_revision'],'op-git-preserve',request)
        self.assertEqual(before,(self.git(repo,'show-ref'), self.git(repo,'status','--porcelain'), self.git(repo,'branch','--show-current')))
        request['materials'] = 'discard'
        request['expected_plan_digest'] = station_cleanup.plan(self.ws)['digest']
        result = station.execute(self.ws,'clean',task['issue_key'],task['run_id'],task['_revision'],'op-git-preserve',request)
        self.assertTrue(result['cleanup_result']['complete'], result)
        self.assertFalse(repo.exists())
        self.assertIsNone(task_store.read_task(self.ws))


if __name__ == '__main__': unittest.main()

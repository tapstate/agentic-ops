#!/usr/bin/env python3
"""任务清理保留开发状态；不再强制切换或快进开发基线。"""
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_station_lifecycle as fixture
from workflow import station, task_store


class DevelopmentPreservationTests(unittest.TestCase):
    setUp = fixture.StationTests.setUp
    prepare_engineering = fixture.StationTests.prepare_engineering
    write = fixture.StationTests.write
    git = fixture.StationTests.git
    takeover = fixture.StationTests.takeover

    def test_branch_head_refs_and_staged_deletion_survive_cleanup(self):
        task = self.takeover(); repo = self.ws / 'source/tapdata/tapdata'
        self.git(repo, 'switch','-c','local-user-branch')
        self.git(repo, 'rm','file.txt')
        before = (self.git(repo,'show-ref'), self.git(repo,'status','--porcelain'), self.git(repo,'branch','--show-current'))
        station.execute(self.ws,'clean',task['issue_key'],task['run_id'],task['_revision'],'op-git-preserve',{})
        self.assertEqual(before,(self.git(repo,'show-ref'), self.git(repo,'status','--porcelain'), self.git(repo,'branch','--show-current')))
        self.assertIsNone(task_store.read_task(self.ws))


if __name__ == '__main__': unittest.main()

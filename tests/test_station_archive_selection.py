#!/usr/bin/env python3
"""两份清单的纯规则测试，不创建 Git 工程。"""
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow import station_clean_rules as rules


class SelectionTests(unittest.TestCase):
    def test_preservation_wins_across_both_manifests(self):
        row = {'scope':'repository','pattern':'**/target','type':'directory','action':'generated'}
        config = {'layers':[{'rules':[row]}, {'rules':[dict(row, action='preserve')]}]}
        self.assertEqual('preserve', rules.classify(config,'module/target',True,'repository')['action'])

    def test_patterns_are_relative_and_bounded(self):
        self.assertTrue(rules.matches('**/target','target'))
        self.assertTrue(rules.matches('**/target','a/b/target'))
        self.assertFalse(rules.matches('*/target','a/b/target'))
        for pattern in ('../target','/target','a/.git','a//target','[abc]'):
            with self.subTest(pattern=pattern), self.assertRaises(ValueError):
                rules.validate_layer({'version':3,'rules':[{'scope':'repository','pattern':pattern,'type':'directory','action':'generated'}]})

    def test_arbitrary_commands_and_state_cleanup_are_rejected(self):
        for extra in ({'command':'rm -rf'}, {'callback':'project.py'}):
            with self.assertRaises(ValueError):
                rules.validate_layer({'version':3,'rules':[dict(scope='runtime',pattern='tmp',type='directory',action='generated',**extra)]})
        with self.assertRaises(ValueError):
            rules.validate_layer({'version':3,'rules':[dict(scope='station-root',pattern='.agenticops',type='directory',action='generated')]})


if __name__ == '__main__': unittest.main()

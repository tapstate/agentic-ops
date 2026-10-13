#!/usr/bin/env python3
"""归档脱敏不改写原事实，不能成为退出门禁。"""
import copy
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow import station_archive, project_rules


class TerminalRedactionTests(unittest.TestCase):
    def test_redaction_does_not_mutate_source(self):
        rules = project_rules.load_admission(project='tapdata')
        original = {'password':'private-value','result':'completed','nested':{'access_token':'private-token'}}
        before = copy.deepcopy(original)
        saved = station_archive.redact_evidence(rules, original)
        self.assertEqual(before, original)
        self.assertEqual('[redacted]', saved['password'])
        self.assertEqual('[redacted]', saved['nested']['access_token'])
        self.assertEqual('completed', saved['result'])


if __name__ == '__main__': unittest.main()

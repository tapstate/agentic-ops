#!/usr/bin/env python3
"""统一资源合同与 .gitignore 的匹配回归。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from internal import resource_contract


class ResourceContractTest(unittest.TestCase):
    def setUp(self):
        self.contract_path = ROOT / "internal" / "resource-contract.json"
        self.gitignore_path = ROOT / ".gitignore"
        self.contract = resource_contract.load(self.contract_path)

    def test_current_gitignore_matches_contract(self):
        resource_contract.validate_gitignore(self.contract, self.gitignore_path)

    def test_missing_tool_ignore_pattern_is_rejected(self):
        lines = self.gitignore_path.read_text(encoding="utf-8").splitlines()
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / ".gitignore"
            candidate.write_text(
                "\n".join(line for line in lines if line != ".idea/") + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "模式集合不一致"):
                resource_contract.validate_gitignore(self.contract, candidate)


if __name__ == "__main__":
    unittest.main(verbosity=2)

from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path

from ao_maint.install.identity import (
    identity_file_path,
    load_maintainer_identity,
    save_maintainer_identity,
)
from ao_maint.output import RuntimeErrorResult


class MaintainerIdentityTest(unittest.TestCase):
    def test_save_and_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source_root = Path(temporary)
            saved = save_maintainer_identity(
                source_root,
                "hermes-agent",
                "测试身份",
                agent_type="hermes-agent",
                model="deepseek-v4-flash",
                environment="macOS 本机 / profile agentic-ops",
            )
            self.assertEqual("hermes-agent", saved["agent_id"])
            self.assertEqual("deepseek-v4-flash", saved["model"])
            identity_path = identity_file_path(source_root)
            self.assertTrue(identity_path.is_file())
            self.assertEqual(
                stat.S_IRUSR | stat.S_IWUSR,
                identity_path.stat().st_mode & 0o777,
            )
            loaded = load_maintainer_identity(source_root)
            self.assertEqual("hermes-agent", loaded["agent_id"])
            self.assertEqual("hermes-agent", loaded["agent_type"])
            self.assertEqual("deepseek-v4-flash", loaded["model"])
            self.assertEqual("macOS 本机 / profile agentic-ops", loaded["environment"])
            self.assertEqual("测试身份", loaded["note"])

    def test_load_missing_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source_root = Path(temporary)
            with self.assertRaises(RuntimeErrorResult) as captured:
                load_maintainer_identity(source_root)
            self.assertEqual("maintainer_identity_missing", captured.exception.code)

    def test_load_empty_agent_id_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source_root = Path(temporary)
            identity_path = identity_file_path(source_root)
            identity_path.parent.mkdir(parents=True, exist_ok=True)
            identity_path.write_text("agent_id: ''\n", encoding="utf-8")
            with self.assertRaises(RuntimeErrorResult) as captured:
                load_maintainer_identity(source_root)
            self.assertEqual("maintainer_identity_invalid", captured.exception.code)

    def test_save_rejects_empty_agent_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source_root = Path(temporary)
            with self.assertRaises(RuntimeErrorResult) as captured:
                save_maintainer_identity(source_root, "  ")
            self.assertEqual("invalid_agent_id", captured.exception.code)


if __name__ == "__main__":
    unittest.main()

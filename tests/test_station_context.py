"""统一工位读取的真实合同与兼容边界回归。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workflow import station_context as context, quality_contract, task_store


class StationContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.station = Path(self.temp.name)
        self.state = self.station / ".agenticops"
        self.state.mkdir()
        self.binding = {"schema_version": 4, "product_root": str(ROOT), "source_pool": str(self.station / "pool"),
                        "station_id": "a" * 32, "project": "tapdata", "agents": ["codex"],
                        "branch_identity": {"schema_version": 1, "git_name": "Developer", "source": "git_global_user_name"}}
        self.path = self.state / "station.json"
        self.path.write_text(json.dumps(self.binding))
        self.epoch = context.read_object(ROOT / "contracts/station-state-compatibility.json", "contract")
        (self.state / "init.json").write_text(json.dumps(self.epoch))

    def test_existing_binding_nested_discovery_and_no_writes(self):
        before = self.path.read_bytes()
        station, path, document = context.find_binding(self.station / "source/tapdata/tapdata")
        self.assertEqual(station, self.station.resolve())
        self.assertEqual(path, self.path.resolve())
        self.assertEqual(document, self.binding)
        self.assertEqual(context.require_epoch(station, ROOT), self.epoch["station_state_epoch"])
        self.assertEqual(before, self.path.read_bytes())

    def test_contract_controls_version_without_consumer_constants(self):
        schema = json.loads((ROOT / "contracts/station.schema.json").read_text())
        schema["properties"]["schema_version"]["const"] = 5
        document = dict(self.binding, schema_version=5)
        original = quality_contract.validate
        with mock.patch.object(quality_contract, "validate", side_effect=lambda value, contract, *args: original(value, schema if isinstance(contract, str) else contract, *args)):
            self.assertEqual(context.validate_binding(document), document)

    def test_malformed_binding_and_git_names_are_rejected_without_repair(self):
        invalid = [dict(self.binding, schema_version=3), dict(self.binding, agents=["codex", "codex"]),
                   dict(self.binding, project="../tapdata"), dict(self.binding, extra=True),
                   {k: v for k, v in self.binding.items() if k != "station_id"}]
        for name in ("-bad", ".bad", "@", "a..b", "a@{b", "a/b", "a.lock", "a.", "a\n"):
            document = copy.deepcopy(self.binding)
            document["branch_identity"]["git_name"] = name
            invalid.append(document)
        for document in invalid:
            with self.subTest(document=document):
                self.path.write_text(json.dumps(document))
                before = self.path.read_bytes()
                with self.assertRaises(ValueError):
                    context.read_binding(self.station)
                self.assertEqual(before, self.path.read_bytes())

    def test_unknown_keyword_inside_negation_still_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "未支持"):
            quality_contract.validate("value", {"not": {"unsupported": True}})

    def test_binding_links_are_rejected(self):
        target = self.station / "binding.json"
        self.path.rename(target)
        self.path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "符号链接"):
            context.read_binding(self.station)

    def test_runtime_rechecks_epoch_after_initial_read(self):
        context.read_binding(self.station)
        (self.state / "init.json").write_text(json.dumps({"station_state_epoch": self.epoch["station_state_epoch"] - 1}))
        with self.assertRaisesRegex(ValueError, "代际"):
            with task_store.task_state_lock(self.station):
                self.fail("旧代际不能进入写入步骤")


if __name__ == "__main__":
    unittest.main()

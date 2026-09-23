#!/usr/bin/env python3
"""AgenticOps 标准契约一致性测试；只实现本项目使用的 JSON Schema 子集。"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

import sys

sys.path.insert(0, str(ROOT))
from gate.runner import evaluate_request, validate_request  # noqa: E402


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def type_matches(value, expected):
    if isinstance(expected, list):
        return any(type_matches(value, item) for item in expected)
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": type(value) is int,
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, True)


def assert_schema(testcase, schema, value, path="$", root_schema=None):
    root_schema = root_schema or schema
    if "$ref" in schema:
        self_path = schema["$ref"]
        testcase.assertTrue(self_path.startswith("#/"), "%s 只支持本地 ref" % path)
        target = root_schema
        for part in self_path[2:].split("/"):
            target = target[part]
        return assert_schema(testcase, target, value, path, root_schema)
    if "type" in schema:
        testcase.assertTrue(type_matches(value, schema["type"]), "%s 类型错误" % path)
    if "const" in schema:
        testcase.assertEqual(schema["const"], value, "%s const 错误" % path)
    if "enum" in schema:
        testcase.assertIn(value, schema["enum"], "%s enum 错误" % path)
    if isinstance(value, str):
        if "minLength" in schema:
            testcase.assertGreaterEqual(len(value), schema["minLength"], path)
        if "pattern" in schema:
            testcase.assertRegex(value, re.compile(schema["pattern"]), path)
    if type(value) is int and "minimum" in schema:
        testcase.assertGreaterEqual(value, schema["minimum"], path)
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            testcase.assertIn(required, value, "%s 缺少 %s" % (path, required))
        if schema.get("additionalProperties") is False:
            testcase.assertFalse(set(value) - set(properties), "%s 包含额外字段" % path)
        for key, item in value.items():
            if key in properties:
                assert_schema(testcase, properties[key], item, "%s.%s" % (path, key), root_schema)
            elif isinstance(schema.get("additionalProperties"), dict):
                assert_schema(
                    testcase,
                    schema["additionalProperties"],
                    item,
                    "%s.%s" % (path, key),
                    root_schema,
                )
    if isinstance(value, list):
        if "minItems" in schema:
            testcase.assertGreaterEqual(len(value), schema["minItems"], path)
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, ensure_ascii=False) for item in value]
            testcase.assertEqual(len(encoded), len(set(encoded)), "%s 存在重复项" % path)
        if "items" in schema:
            for index, item in enumerate(value):
                assert_schema(testcase, schema["items"], item, "%s[%d]" % (path, index), root_schema)


class ContractConformanceTest(unittest.TestCase):
    def valid_request(self, cwd):
        return {
            "protocol_version": 1,
            "event": "before_operation",
            "source": {"agent": "test", "adapter": "test-hook", "adapter_version": 1},
            "cwd": str(cwd),
            "operations": ["git_commit"],
            "target": {"branch_relevant": True},
            "note": "契约测试",
        }

    def test_catalog_and_manifests_conform_to_schemas(self):
        catalog_schema = load_json(ROOT / "contracts" / "operation-catalog.schema.json")
        catalog = load_json(ROOT / "contracts" / "operation-catalog.json")
        assert_schema(self, catalog_schema, catalog)
        names = [item["name"] for item in catalog["operations"]]
        self.assertEqual(len(names), len(set(names)))

        manifest_schema = load_json(ROOT / "contracts" / "adapter-manifest.schema.json")
        for path in sorted((ROOT / "adapters" / "agents").glob("*/manifest.json")):
            assert_schema(self, manifest_schema, load_json(path), str(path))
        repository_catalog_schema = load_json(ROOT / "contracts" / "repository-catalog.schema.json")
        assert_schema(
            self,
            repository_catalog_schema,
            load_json(ROOT / "projects" / "tapdata" / "repositories.json"),
        )
        compatibility_schema = load_json(
            ROOT / "contracts" / "station-state-compatibility.schema.json"
        )
        assert_schema(
            self,
            compatibility_schema,
            load_json(ROOT / "contracts" / "station-state-compatibility.json"),
        )

    def test_source_product_state_conforms_to_schema(self):
        schema = load_json(ROOT / "contracts" / "product-state.schema.json")
        document = {
            "schema_version": 2,
            "mode": "source",
            "repository": "git@example.test:tapstate/agentic-ops.git",
            "tracking_branch": "develop",
            "current_ref": "a" * 40,
            "previous_ref": None,
            "source_pool": "/tmp/agentic-ops-repos",
        }
        assert_schema(self, schema, document)

    def test_real_station_state_conforms_to_schema(self):
        station_schema = load_json(ROOT / "contracts" / "station.schema.json")
        init_schema = load_json(ROOT / "contracts" / "station-init.schema.json")
        with tempfile.TemporaryDirectory() as temporary:
            station = Path(temporary) / "station"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bootstrap" / "render.py"),
                    "--install-home",
                    str(ROOT),
                    "--station",
                    str(station),
                    "--project",
                    "tapdata",
                    "--source-pool",
                    str(Path(temporary) / "pool"),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            assert_schema(
                self,
                station_schema,
                load_json(station / ".agenticops" / "station.json"),
            )
            assert_schema(
                self,
                init_schema,
                load_json(station / ".agenticops" / "init.json"),
            )
            task_schema = load_json(ROOT / "contracts" / "task-state.schema.json")
            assert_schema(self, task_schema, load_json(station / ".agenticops" / "current-task.json"))
            from station_fixture import save_task
            save_task(station, {"issue_key": "TAP-123", "run_id": "run-fixture", "task_class": "defect_fix",
                "stage": "task_intake", "facts": {}, "repositories": [{"repository": "tapdata/tapdata"}],
                "pending": None, "history": []})
            assert_schema(self, task_schema, load_json(station / ".agenticops" / "current-task.json"))

    def test_task_bindings_reference_one_baseline(self):
        schema = load_json(ROOT / "contracts" / "task-state.schema.json")
        current = schema["properties"]["current"]
        self.assertNotIn("repositories", current["properties"])
        binding = current["properties"]["task_repositories"]["additionalProperties"]
        self.assertIn("baseline_entry_digest", binding["required"])
        self.assertNotIn("base_sha", binding["properties"])
        self.assertNotIn("worktree", binding["properties"])

    def test_gate_validator_and_schema_accept_the_same_request(self):
        schema = load_json(ROOT / "contracts" / "gate-request.schema.json")
        with tempfile.TemporaryDirectory() as temporary:
            request = self.valid_request(temporary)
            assert_schema(self, schema, request)
            self.assertIsNone(validate_request(request))
            for required in schema["required"]:
                invalid = dict(request)
                invalid.pop(required)
                self.assertIsNotNone(validate_request(invalid), required)
            extra = dict(request)
            extra["platform_private"] = True
            self.assertIsNotNone(validate_request(extra))
            push_request = dict(request)
            push_request["operations"] = ["git_push"]
            push_request["target"] = {
                "push_source_ref": "HEAD",
                "push_destination_ref": "refs/heads/feature/TAP-123",
                "push_target_branch": "feature/TAP-123",
            }
            assert_schema(self, schema, push_request)
            self.assertIsNone(validate_request(push_request))

    def test_gate_response_conforms_to_decision_schema(self):
        schema = load_json(ROOT / "contracts" / "gate-decision.schema.json")
        with tempfile.TemporaryDirectory() as temporary:
            response = evaluate_request(self.valid_request(temporary))
            assert_schema(self, schema, response)

    def test_operation_catalog_and_policy_do_not_drift(self):
        catalog = load_json(ROOT / "contracts" / "operation-catalog.json")
        policy = load_json(ROOT / "policies" / "operations.json")
        self.assertEqual({item["name"] for item in catalog["operations"]}, set(policy["operations"]))

    def test_free_operations_do_not_duplicate_task_authorization_scope(self):
        policy = load_json(ROOT / "policies" / "operations.json")
        free = {name for name, value in policy["operations"].items() if value["level"] == "free"}
        self.assertIn("write_jira_comment", free)
        self.assertFalse(free & set(policy["authorization_scopes"]["task_execution"]["covered_operations"]))

    def test_contract_policy_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = load_json(ROOT / "policies" / "operations.json")
            policy["operations"].pop("git_commit")
            policy_path = root / "operations.json"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            response = evaluate_request(self.valid_request(root), policy_path=policy_path)
            self.assertEqual("deny", response["decision"])
            self.assertEqual("contract_policy_drift", response["operation"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

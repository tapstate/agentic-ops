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
from adapters.tools.classifier import classify_bash  # noqa: E402
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

    def test_real_workspace_binding_conforms_to_schema(self):
        schema = load_json(ROOT / "contracts" / "workspace-binding.schema.json")
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bootstrap" / "render.py"),
                    "--install-home",
                    str(ROOT),
                    "--workspace",
                    str(workspace),
                    "--project",
                    "tapdata",
                    "--agent",
                    "both",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            assert_schema(
                self,
                schema,
                load_json(workspace / ".agenticops.json"),
            )
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "workflow" / "task.py"),
                    "init",
                    "--issue-key",
                    "TAP-123",
                    "--task-class",
                    "defect_fix",
                    "--dir",
                    str(workspace),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            registry_schema = load_json(ROOT / "contracts" / "task-registry.schema.json")
            assert_schema(
                self,
                registry_schema,
                load_json(workspace / ".gate" / "tasks.json"),
            )
            task_schema = load_json(ROOT / "contracts" / "task-state.schema.json")
            assert_schema(
                self,
                task_schema,
                load_json(workspace / ".gate" / "tasks" / "TAP-123" / "task.json"),
            )

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

    def test_gate_response_conforms_to_decision_schema(self):
        schema = load_json(ROOT / "contracts" / "gate-decision.schema.json")
        with tempfile.TemporaryDirectory() as temporary:
            response = evaluate_request(self.valid_request(temporary))
            assert_schema(self, schema, response)

    def test_operation_catalog_policy_and_tool_mapping_do_not_drift(self):
        catalog = load_json(ROOT / "contracts" / "operation-catalog.json")
        operations = {item["name"]: item for item in catalog["operations"]}
        policy = load_json(ROOT / "policies" / "operations.json")
        self.assertEqual(set(operations), set(policy["operations"]))

        mapping = load_json(ROOT / "adapters" / "tools" / "mcp-operations.json")
        requestable = {name for name, item in operations.items() if item["requestable"]}
        self.assertTrue(set(mapping["mappings"].values()) <= requestable)
        shell_operations = set(classify_bash(
            "git commit -m x && git push origin feature/x; gh pr merge 1"
        ))
        self.assertTrue(shell_operations <= requestable)

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

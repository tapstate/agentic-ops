#!/usr/bin/env python3
"""适配层重量门禁：阻止平台适配演变成第二套 Runtime。"""
from __future__ import annotations

import ast
from collections import Counter
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADAPTERS = ROOT / "adapters"
AGENT_ROOT = ADAPTERS / "agents"
TOOL_ROOT = ADAPTERS / "tools"
TOOL_PYTHON_FILES = {
    "classifier.py", "git_push_syntax.py", "shell_classifier.py", "shell_syntax.py",
}
TOOL_LOGICAL_LINE_BUDGET = 545
TOOL_AST_STATEMENT_BUDGET = 400
MAX_ADAPTER_LINE_LENGTH = 119


def logical_lines(path):
    return sum(
        1
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def entrypoint_adapter_version(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values = [
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "ADAPTER_VERSION" for target in node.targets)
        and isinstance(node.value, ast.Constant)
        and type(node.value.value) is int
    ]
    if len(values) != 1:
        raise AssertionError("%s 必须声明唯一的整数 ADAPTER_VERSION" % path)
    return values[0]


class AdapterBoundaryTest(unittest.TestCase):
    def test_each_agent_has_one_small_stateless_entrypoint(self):
        for agent_root in sorted(path for path in AGENT_ROOT.iterdir() if path.is_dir()):
            python_files = sorted(agent_root.glob("*.py"))
            self.assertEqual([path.name for path in python_files], ["hook.py"])
            self.assertLessEqual(logical_lines(python_files[0]), 100)
            tree = ast.parse(python_files[0].read_text(encoding="utf-8"))
            functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
            branches = [
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.If, ast.For, ast.While, ast.Try))
            ]
            self.assertLessEqual(len(functions), 4)
            self.assertLessEqual(len(branches), 12)

    def test_shared_adapter_code_has_explicit_budget(self):
        self.assertLessEqual(logical_lines(ADAPTERS / "runtime.py"), 80)
        self.assertLessEqual(logical_lines(TOOL_ROOT / "classifier.py"), 240)
        self.assertLessEqual(logical_lines(TOOL_ROOT / "git_push_syntax.py"), 70)
        self.assertLessEqual(logical_lines(TOOL_ROOT / "shell_classifier.py"), 310)
        self.assertLessEqual(logical_lines(TOOL_ROOT / "shell_syntax.py"), 130)

    def test_tool_adapters_have_file_total_and_readability_budgets(self):
        python_files = sorted(TOOL_ROOT.glob("*.py"))
        self.assertEqual({path.name for path in python_files}, TOOL_PYTHON_FILES)
        self.assertLessEqual(
            sum(logical_lines(path) for path in python_files),
            TOOL_LOGICAL_LINE_BUDGET,
        )
        statement_total = 0
        for path in python_files:
            content = path.read_text(encoding="utf-8")
            lines = content.splitlines()
            self.assertLessEqual(max(map(len, lines), default=0), MAX_ADAPTER_LINE_LENGTH, str(path))
            tree = ast.parse(content)
            statements = [node for node in ast.walk(tree) if isinstance(node, ast.stmt)]
            statement_total += len(statements)
            statements_by_line = Counter(node.lineno for node in statements)
            crowded = sorted(line for line, count in statements_by_line.items() if count > 1)
            self.assertFalse(crowded, "%s 存在同一行多个语句：%s" % (path, crowded))
        self.assertLessEqual(statement_total, TOOL_AST_STATEMENT_BUDGET)

    def test_agent_manifests_are_bounded_and_declarative(self):
        for manifest_path in sorted(AGENT_ROOT.glob("*/manifest.json")):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], 1)
            entrypoint = ROOT / manifest["entrypoint"]
            self.assertEqual(
                manifest["adapter_version"],
                entrypoint_adapter_version(entrypoint),
                "%s 与 %s 的 adapter_version 不一致" % (manifest_path, entrypoint),
            )
            self.assertLessEqual(len(manifest["artifacts"]), 3)
            self.assertIn(manifest["capabilities"]["ask_fallback"], (
                "native",
                "deny_with_guidance",
            ))

    def test_adapters_do_not_depend_on_business_layers_or_write_state(self):
        forbidden_imports = {
            "workflow", "projects", "policies", "subprocess", "sqlite3", "urllib", "http",
        }
        forbidden_calls = {
            "write_text", "write_bytes", "mkdir", "unlink", "rename", "remove", "rmdir",
        }
        for path in sorted(ADAPTERS.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = {alias.name.split(".")[0] for alias in node.names}
                    self.assertFalse(imported & forbidden_imports, str(path))
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module.split(".")[0], forbidden_imports, str(path))
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    self.assertNotIn(node.func.attr, forbidden_calls, str(path))
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "open"
                    and len(node.args) > 1
                    and isinstance(node.args[1], ast.Constant)
                ):
                    self.assertFalse(
                        set(str(node.args[1].value)) & set("wax+"),
                        "%s 不得写本地状态" % path,
                    )
        forbidden_directories = {"skills", "rules", "policies", "workflow", "state"}
        for agent_root in sorted(path for path in AGENT_ROOT.iterdir() if path.is_dir()):
            self.assertFalse(
                {path.name for path in agent_root.iterdir()} & forbidden_directories,
                str(agent_root),
            )

    def test_gate_contains_no_platform_protocol(self):
        forbidden_tokens = (
            "Claude",
            "Codex",
            "PreToolUse",
            "hookSpecificOutput",
            "mcp__",
            "AO_GATE_BINARY",
        )
        for path in sorted((ROOT / "gate").glob("*.py")):
            content = path.read_text(encoding="utf-8")
            for token in forbidden_tokens:
                self.assertNotIn(token, content, "%s 包含平台协议 %s" % (path, token))
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotEqual(node.module.split(".")[0], "adapters", str(path))


if __name__ == "__main__":
    unittest.main(verbosity=2)

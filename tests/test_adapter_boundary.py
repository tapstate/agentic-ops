#!/usr/bin/env python3
"""适配层重量门禁：阻止平台适配演变成第二套 Runtime。"""
from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bootstrap"))
from agent_registry import discover
from render import rendered_content  # noqa: E402

ADAPTERS = ROOT / "adapters"
AGENT_ROOT = ADAPTERS / "agents"
TOOL_ROOT = ADAPTERS / "tools"
class AdapterBoundaryTest(unittest.TestCase):
    def test_adapters_are_declarative_without_tool_interception(self):
        self.assertEqual(list(ADAPTERS.rglob("*.py")), [])
        for agent, manifest in discover(ROOT).items():
            self.assertEqual(manifest["schema_version"], 3)
            self.assertFalse({"hook", "entrypoint", "capabilities"} & set(manifest))
            self.assertLessEqual(len(manifest["artifacts"]), 3)
            self.assertLessEqual(len(list((AGENT_ROOT / agent).rglob("*.*"))), 3)
        for retired in ("runtime.py", "tools/classifier.py", "tools/mcp-operations.json",
                        "agents/codex/templates/hooks.json", "agents/claude/templates/settings.json"):
            self.assertFalse((ADAPTERS / retired).exists(), retired)

    def test_generic_agent_needs_no_python_hook(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agent = root / "adapters/agents/fixture"
            agent.mkdir(parents=True)
            path = agent / "manifest.json"
            manifest = {"schema_version": 3, "name": "fixture", "adapter_version": 1,
                        "artifacts": [], "launch": {"mode": "manual", "command": None, "message": "手动启动"}}
            path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(discover(root), {"fixture": manifest})
            for invalid in (
                dict(manifest, schema_version=2), dict(manifest, hook={}),
                dict(manifest, entrypoint="missing.py"), dict(manifest, capabilities={}),
                dict(manifest, retired_artifacts=["../settings.json"]),
                dict(manifest, skill_target="/outside"), dict(manifest, adapter_version=True),
                dict(manifest, artifacts=[{"template": "../outside", "target": "x"}]),
                [],
            ):
                path.write_text(json.dumps(invalid), encoding="utf-8")
                with self.assertRaises(ValueError):
                    discover(root)

    def test_common_renderer_rejects_unconsumed_hook_marker(self):
        manifest = json.loads((AGENT_ROOT / "claude" / "manifest.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "template.txt").write_text("__AGENTIC_OPS_HOOK_UNSUPPORTED__", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "模板变量未被完整消费"):
                rendered_content(root, "tapdata", "template.txt", manifest)

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

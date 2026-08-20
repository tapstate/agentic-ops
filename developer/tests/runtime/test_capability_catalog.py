from __future__ import annotations

import argparse
import io
import json
import re
import shlex
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import yaml

from ao_work.capabilities import CapabilityCatalog
from ao_work.work_cli import build_parser, main

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS_ROOT = REPO_ROOT / "developer" / "standards" / "contracts" / "operations"


class CapabilityCatalogTest(unittest.TestCase):
    def test_every_operation_contract_has_exactly_one_catalog_entry(self) -> None:
        catalog = CapabilityCatalog.load(REPO_ROOT)
        contract_entries = [
            capability for capability in catalog.capabilities if capability.contract
        ]
        expected_files = {path.name for path in CONTRACTS_ROOT.glob("*.yaml")}
        declared_files = {
            Path(str(capability.contract)).name for capability in contract_entries
        }
        self.assertEqual(expected_files, declared_files)
        self.assertEqual(len(expected_files), len(contract_entries))

        expected_operations = {
            str(yaml.safe_load(path.read_text(encoding="utf-8"))["operation"])
            for path in CONTRACTS_ROOT.glob("*.yaml")
        }
        self.assertEqual(
            expected_operations,
            {capability.capability_id for capability in contract_entries},
        )

    def test_catalog_status_and_gap_action_are_closed(self) -> None:
        catalog = CapabilityCatalog.load(REPO_ROOT)
        self.assertTrue(catalog.capabilities)
        for capability in catalog.capabilities:
            self.assertIn(capability.status, {"implemented", "capability_gap"})
            if capability.status == "capability_gap":
                self.assertEqual((), capability.commands)
                self.assertRegex(capability.next_action, r"[\u3400-\u9fff]")

    def test_all_implemented_commands_exist_in_current_parser(self) -> None:
        catalog = CapabilityCatalog.load(REPO_ROOT)
        parser_paths = self._leaf_command_paths(build_parser())
        declared_paths = {
            command
            for capability in catalog.capabilities
            if capability.status == "implemented"
            for command in capability.commands
        }
        self.assertEqual(
            parser_paths
            - {
                ("capability", "list"),
                ("capability", "show"),
                ("task", "takeover"),
            },
            declared_paths,
        )

    def test_internal_task_state_is_not_public_takeover(self) -> None:
        catalog = CapabilityCatalog.load(REPO_ROOT)
        public = {entry["id"] for entry in catalog.list()}
        all_entries = {
            entry["id"] for entry in catalog.list(include_internal=True)
        }
        self.assertNotIn("task_state_init", public)
        self.assertNotIn("task_state_inspect", public)
        self.assertNotIn("report_write", public)
        self.assertIn("task_state_init", all_entries)
        takeover = catalog.show("takeover_task")
        self.assertEqual("implemented", takeover["status"])
        self.assertEqual([["takeover"]], takeover["commands"])

    def test_cli_list_and_show_are_read_only_stable_json(self) -> None:
        first = self._run_cli("capability", "list")
        second = self._run_cli("capability", "list")
        self.assertEqual(first, second)
        payload = json.loads(first)
        self.assertEqual(True, payload["ok"])
        self.assertEqual("developer", payload["workplane"])
        self.assertEqual(False, payload["internal_included"])
        self.assertTrue(
            all(item["visibility"] == "public" for item in payload["capabilities"])
        )

        shown = json.loads(self._run_cli("capability", "show", "jira_comment"))
        self.assertEqual("implemented", shown["capability_status"])
        self.assertEqual(True, shown["callable"])
        self.assertEqual("implemented", shown["capability"]["status"])
        self.assertEqual(
            [["jira", "comment", "plan"], ["jira", "comment", "apply"], ["jira", "comment", "readback"]],
            shown["capability"]["commands"],
        )

    def test_cli_without_managed_install_identity_fails_closed(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(["capability", "list"])
        self.assertEqual(2, exit_code)
        self.assertEqual(
            "install_root_source_rejected",
            json.loads(stdout.getvalue())["code"],
        )

    def test_unknown_capability_fails_closed(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch(
                "ao_work.work_cli.validate_install_root",
                return_value=REPO_ROOT,
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = main(["capability", "show", "imagined_operation"])
        self.assertEqual(3, exit_code)
        payload = json.loads(stdout.getvalue())
        self.assertEqual("capability_not_found", payload["code"])
        self.assertEqual("capability_gap", payload["status"])
        self.assertIn("不要猜测", payload["required_human_action"])

    def test_installed_markdown_only_shows_current_single_line_ao_work_syntax(
        self,
    ) -> None:
        parser = build_parser()
        documented: set[tuple[str, ...]] = set()
        for path in (REPO_ROOT / "developer").rglob("*.md"):
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped.startswith("ao-work ") or stripped.endswith("\\"):
                    continue
                documented.add(self._resolve_documented_command(parser, stripped))

        parser_paths = self._leaf_command_paths(parser)
        self.assertTrue(documented)
        self.assertEqual(set(), documented - parser_paths)

    def test_installed_markdown_does_not_publish_legacy_gap_commands(self) -> None:
        catalog = CapabilityCatalog.load(REPO_ROOT)
        for capability_id in ("inspect_task", "add_task_comment"):
            capability = catalog.show(capability_id)
            self.assertEqual("capability_gap", capability["status"])
            self.assertEqual([], capability["commands"])

        legacy_command = re.compile(
            r"\bao-work\s+(?:inspect-task|takeover-task|add-task-comment)\b"
        )
        for path in (REPO_ROOT / "developer").rglob("*.md"):
            self.assertIsNone(
                legacy_command.search(path.read_text(encoding="utf-8")), path
            )

    def test_installed_markdown_does_not_direct_ai_to_unavailable_source_assets(
        self,
    ) -> None:
        unavailable_link = re.compile(r"\]\((?:\.\./)+(?:docs|maintainer)/")
        actionable_reference = re.compile(
            r"(?:读取|加载|执行|遵守|参照|参考|由[^。]{0,60}约束)"
            r"[^。]*(?:maintainer/|docs/[A-Za-z0-9._/-]+)"
        )
        negative_boundary = re.compile(
            r"不得|禁止|不应|不能|不允许|不适用|不属于|无需|不需要"
        )

        for path in (REPO_ROOT / "developer").rglob("*.md"):
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                location = f"{path}:{line_number}"
                self.assertIsNone(unavailable_link.search(line), location)
                if actionable_reference.search(line):
                    self.assertIsNotNone(negative_boundary.search(line), location)

    def test_project_machine_assets_only_reference_cataloged_capabilities(self) -> None:
        catalog = CapabilityCatalog.load(REPO_ROOT)
        statuses = {
            capability.capability_id: capability.status
            for capability in catalog.capabilities
        }
        assets_root = REPO_ROOT / "developer" / "standards" / "projects"
        for path in assets_root.rglob("*.yaml"):
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            for mapping in self._mappings(payload):
                capability_id = mapping.get("capability")
                declared_status = mapping.get("capability_status")
                if capability_id is None:
                    continue
                self.assertIn(capability_id, statuses, path)
                self.assertEqual(statuses[capability_id], declared_status, path)
                if declared_status == "capability_gap":
                    self.assertFalse(mapping.get("command"), path)
                    self.assertFalse(mapping.get("commands"), path)
                    self.assertRegex(
                        str(mapping.get("required_human_action", "")),
                        r"[\u3400-\u9fff]",
                        path,
                    )

        for path in assets_root.rglob("*.yaml"):
            content = path.read_text(encoding="utf-8")
            self.assertNotRegex(
                content,
                r"ao-work\s+(?:tapdata\s+branch-align|add-task-comment|update-task-description-sections|update-task-form)\b",
                path,
            )

    def _mappings(self, value: object) -> list[dict[str, object]]:
        discovered: list[dict[str, object]] = []
        if isinstance(value, dict):
            discovered.append(value)
            for child in value.values():
                discovered.extend(self._mappings(child))
        elif isinstance(value, list):
            for child in value:
                discovered.extend(self._mappings(child))
        return discovered

    def _run_cli(self, *arguments: str) -> str:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch(
                "ao_work.work_cli.validate_install_root",
                return_value=REPO_ROOT,
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = main(arguments)
        self.assertEqual(0, exit_code, stderr.getvalue())
        self.assertEqual(1, len(stdout.getvalue().splitlines()))
        return stdout.getvalue()

    def _leaf_command_paths(
        self,
        parser: argparse.ArgumentParser,
        prefix: tuple[str, ...] = (),
    ) -> set[tuple[str, ...]]:
        subparser_actions = [
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        ]
        if not subparser_actions:
            return {prefix}
        self.assertEqual(1, len(subparser_actions))
        paths: set[tuple[str, ...]] = set()
        for name, child in subparser_actions[0].choices.items():
            paths.update(self._leaf_command_paths(child, (*prefix, name)))
        return paths

    def _resolve_documented_command(
        self,
        parser: argparse.ArgumentParser,
        line: str,
    ) -> tuple[str, ...]:
        tokens = shlex.split(line)[1:]
        path: list[str] = []
        current = parser
        while True:
            subparser_actions = [
                action
                for action in current._actions
                if isinstance(action, argparse._SubParsersAction)
            ]
            if not subparser_actions:
                return tuple(path)
            self.assertEqual(1, len(subparser_actions), line)
            self.assertGreater(len(tokens), len(path), line)
            token = tokens[len(path)]
            self.assertIn(token, subparser_actions[0].choices, line)
            path.append(token)
            current = subparser_actions[0].choices[token]


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""开发诊断的保守 affected 测试选择；正式 Story Gate 不调用此模块。"""
import argparse
import json
import subprocess
from pathlib import Path

RULES = (
    ("workflow/workspace", ("workspace_clean", "station_resources", "station_lifecycle")),
    ("policies/workspace-clean.json", ("workspace_clean",)),
    ("workflow/station_archive.py", ("station_resources", "station_lifecycle")),
    ("workflow/station_operation.py", ("workspace_clean", "station_resources", "station_lifecycle")),
    ("workflow/station_directories.py", ("workspace_clean", "station_resources", "station_lifecycle")),
    ("workflow/station_resources.py", ("workspace_clean", "station_resources", "station_lifecycle")),
    ("workflow/station.py", ("workspace_clean", "station_resources", "station_lifecycle")),
    ("workflow/station_source.py", ("station_source", "repository_recovery", "station_lifecycle")),
    ("workflow/source_pool.py", ("station_source", "shared_repositories")),
    ("workflow/quality", ("quality", "jira_status", "jira_watermark", "issue_versions")),
    ("workflow/task_store.py", ("station_resources", "station_lifecycle", "quality", "checkpoints", "task_identity")),
    ("workflow/engineering_baseline.py", ("engineering_baseline", "station_lifecycle", "station_resources", "quality")),
    ("workflow/", ("workflow", "checkpoints", "failures", "task_identity", "station_state")),
    ("gate/", ("gate", "contracts")),
    ("policies/", ("gate", "contracts")),
    ("contracts/", ("contracts", "workspace_compatibility")),
    ("bootstrap/", ("install", "station_bootstrap")),
    ("adapters/", ("adapter_boundary", "gate")),
    ("projects/tapdata/", ("engineering_baseline", "maven", "java_impact", "branch_alignment")),
    ("internal/story_gate/", ("story_gate", "verification", "release")),
    ("internal/release/", ("release", "verification")),
    ("internal/acceptance.sh", ("test_selection", "verification")),
    ("internal/test_selection.py", ("test_selection",)),
    ("internal/tests/test_test_selection.py", ("test_selection",)),
    (".githooks/", ("story_gate", "verification", "release")),
)

TEST_SUITES = {
    "workspace_clean": ("python3", "tests/test_workspace_clean.py"),
    "gate": ("python3", "tests/test_gate.py"), "contracts": ("python3", "tests/test_contracts.py"),
    "adapter_boundary": ("python3", "tests/test_adapter_boundary.py"), "workflow": ("python3", "tests/test_workflow.py"),
    "checkpoints": ("python3", "tests/test_checkpoints.py"), "failures": ("python3", "tests/test_failures.py"),
    "repair_strategy": ("python3", "tests/test_repair_strategy.py"), "git_refs": ("python3", "tests/test_git_refs.py"),
    "task_identity": ("python3", "tests/test_task_identity.py"), "station_state": ("python3", "tests/test_station_state.py"),
    "station_resources": ("python3", "tests/test_station_resources.py"), "station_lifecycle": ("python3", "tests/test_station_lifecycle.py"),
    "station_source": ("python3", "tests/test_station_source.py"), "repository_recovery": ("python3", "tests/test_repository_recovery.py"),
    "shared_repositories": ("python3", "tests/test_shared_repositories.py"), "quality": ("python3", "tests/test_quality.py"),
    "jira_status": ("python3", "tests/test_jira_status.py"), "jira_watermark": ("python3", "tests/test_jira_watermark.py"),
    "issue_versions": ("python3", "tests/test_issue_versions.py"), "workspace_compatibility": ("python3", "tests/test_workspace_compatibility.py"),
    "engineering_baseline": ("python3", "tests/test_engineering_baseline.py"), "install": ("bash", "tests/test_install.sh"),
    "station_bootstrap": ("python3", "tests/test_station_bootstrap.py"), "maven": ("python3", "-m", "unittest", "discover", "-s", "projects/tapdata/tests", "-p", "test_maven*.py"),
    "maven_reports": ("python3", "projects/tapdata/tests/test_maven_reports.py"), "java_impact": ("python3", "projects/tapdata/tests/test_java_impact.py"),
    "branch_alignment": ("python3", "projects/tapdata/tests/test_branch_alignment.py"), "story_gate": ("python3", "-m", "unittest", "internal.tests.test_story_gate"),
    "verification": ("python3", "-m", "unittest", "internal.tests.test_verification"), "release": ("bash", "internal/tests/test_release.sh"),
    "test_selection": ("python3", "-m", "unittest", "internal.tests.test_test_selection"),
    "workspace_recovery": ("python3", "-m", "unittest", "internal.tests.test_workspace_recovery"),
}


def changes(root, source, base=None, head=None):
    if source == "staged": args = ("diff", "--cached", "--name-only")
    elif source == "range":
        if not base or not head: raise ValueError("range 必须提供 --base 与 --head")
        args = ("diff", "--name-only", base + "..." + head)
    else: args = ("diff", "HEAD", "--name-only")
    output = subprocess.check_output(("git", "-C", str(root), *args), text=True)
    paths = set(filter(None, output.splitlines()))
    if source == "worktree":
        output = subprocess.check_output(("git", "-C", str(root), "ls-files", "--others", "--exclude-standard"), text=True)
        paths.update(filter(None, output.splitlines()))
    return sorted(paths)


def select(paths):
    suites, unmapped = [], []
    for path in paths:
        matched = ()
        if path.startswith("tests/") and path.endswith(".py"):
            candidate = path.removeprefix("tests/").removesuffix(".py").removeprefix("test_")
            matched = (candidate,) if candidate in TEST_SUITES else ()
        elif path == "tests/test_install.sh": matched = ("install",)
        else:
            for prefix, candidates in RULES:
                if path.startswith(prefix): matched = candidates; break
        if not matched: unmapped.append(path)
        for suite in matched:
            if suite not in suites: suites.append(suite)
    return suites, unmapped


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=("worktree", "staged", "range"), required=True)
    parser.add_argument("--base"); parser.add_argument("--head"); parser.add_argument("--root", default=".")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args(); root = Path(args.root).resolve()
    paths = changes(root, args.source, args.base, args.head)
    suites, unmapped = select(paths)
    result = {"paths": paths, "suites": suites, "unmapped": unmapped,
              "commands": [TEST_SUITES[name] for name in suites]}
    print(json.dumps(result, ensure_ascii=False))
    if not paths or unmapped or not suites: raise SystemExit(2)
    if args.run:
        for name in suites:
            command = TEST_SUITES[name]
            print("[affected] %s: %s" % (name, " ".join(command)), flush=True)
            if subprocess.run(command, cwd=root).returncode:
                raise SystemExit(1)


if __name__ == "__main__": main()

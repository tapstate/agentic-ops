"""正式验收的被测材料与可移植环境事实；不执行测试或管理任务。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

from internal.story_gate.model import FULL_ACCEPTANCE_CHECKS
from internal import test_selection
from bootstrap import python_runtime

EVIDENCE_SCHEMA_VERSION = 6
SLOW_CHECK_SECONDS = 600
BEHAVIOR_FLAGS = ("AO_CONNECTOR_MAVEN_TEST", "AO_JAR_MAVEN_TEST")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":")).encode()).hexdigest()


def git_environment():
    result = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result.update(GIT_OPTIONAL_LOCKS="0", GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1")
    return result


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=20,
                            env=git_environment())
    if result.returncode:
        raise ValueError("无法核验验收材料：git " + args[0])
    return result.stdout


def candidate_tree(root, source, head=None):
    """对完整 mode/path/blob 集合取摘要，避免只绑定 diff 或写入 Git 对象。"""
    rows = []
    if source == "range":
        data = git(root, "ls-tree", "-r", "-z", "--full-tree", head or "HEAD")
        for entry in data.split(b"\0"):
            if entry:
                meta, path = entry.split(b"\t", 1)
                mode, kind, oid = meta.split()
                rows.append((path.hex(), mode.decode(), oid.decode()))
    else:
        for entry in git(root, "ls-files", "--stage", "-z").split(b"\0"):
            if entry:
                meta, path = entry.split(b"\t", 1)
                mode, oid, stage = meta.split()
                if stage != b"0":
                    raise ValueError("索引存在未解决冲突，不能验收")
                rows.append((path.hex(), mode.decode(), oid.decode()))
    return digest(sorted(rows))


def require_material(root, impact):
    if any(entry and (entry[:1].islower() or entry[:1] == b"S")
           for entry in git(root, "ls-files", "-v", "-z").split(b"\0")):
        raise ValueError("正式验收不接受 assume-unchanged 或 skip-worktree 索引标记")
    if impact.change_source not in ("staged", "range"):
        raise ValueError("正式验收必须显式选择 staged 或 range；worktree 仅供影响诊断")
    current = git(root, "rev-parse", "HEAD^{commit}").decode().strip()
    expected = impact.comparison_base if impact.change_source == "staged" else impact.commit_sha
    if current != expected:
        raise ValueError("验收 HEAD 与指定候选不一致")
    if git(root, "diff", "--name-only", "--no-ext-diff"):
        raise ValueError("存在未暂存修改；请先明确候选，不自动暂存或清理")
    if git(root, "ls-files", "--others", "--exclude-standard", "-z"):
        raise ValueError("存在非忽略的未跟踪输入，不能绑定正式验收")
    if impact.change_source == "range" and git(root, "diff", "--cached", "--name-only"):
        raise ValueError("range 验收要求索引与 HEAD 一致")
    if candidate_tree(root, "staged") != impact.candidate_tree:
        raise ValueError("验收候选树发生变化")


def contract_digest():
    return digest({"contract": "candidate-selection/v6", "checks": FULL_ACCEPTANCE_CHECKS,
                   "slow_check_seconds": SLOW_CHECK_SECONDS,
                   "selection_digest": hashlib.sha256(Path(test_selection.__file__).read_bytes()).hexdigest()})


def environment(root):
    def version(command):
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise ValueError("无法读取验收工具版本")
        return (result.stdout or result.stderr).strip()

    root = Path(root)
    product_python = python_runtime.test_python(root)
    default_internal = root / ".local/venv/internal/bin/python"
    internal_python = os.environ.get("AGENTIC_OPS_INTERNAL_TEST_PYTHON") or (
        str(default_internal))
    python_runtime.inspect(internal_python)
    opa = shutil.which("opa")
    locks = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
             for name in ("internal/uv.lock", "internal/pyproject.toml")}
    if any(os.environ.get(key) == "1" for key in BEHAVIOR_FLAGS):
        raise ValueError("正式候选验收不启用可选 Maven 集成测试；请单独运行诊断")
    return {
        "tools": {"product_python": version([product_python, "--version"]),
                  "internal_python": version([internal_python, "--version"]),
                  "git": version(["git", "--version"]),
                  "opa": version([opa, "version"]) if opa else "unavailable"},
        "platform": {"system": platform.system(), "machine": platform.machine()},
        "locks": locks,
        "flags": {key: "enabled" if os.environ.get(key) == "1" else "disabled" for key in BEHAVIOR_FLAGS},
    }


def valid_environment(value):
    if not isinstance(value, dict) or set(value) != {"tools", "platform", "locks", "flags"}:
        return False
    required = {"tools": {"product_python", "internal_python", "git", "opa"},
                "platform": {"system", "machine"}, "flags": set(BEHAVIOR_FLAGS)}
    for key, fields in required.items():
        if not isinstance(value[key], dict) or set(value[key]) != fields:
            return False
        if not all(isinstance(item, str) for item in value[key].values()):
            return False
    if any(not item for item in value["tools"].values()) or any(
            item not in ("enabled", "disabled") for item in value["flags"].values()):
        return False
    return isinstance(value["locks"], dict) and set(value["locks"]) == {"internal/uv.lock", "internal/pyproject.toml"} and all(
        key in ("internal/uv.lock", "internal/pyproject.toml") and isinstance(item, str)
        and re.fullmatch(r"[0-9a-f]{64}", item)
        for key, item in value["locks"].items())


def full_acceptance_matches(value, tree, expected_environment):
    """复用完整 v6 测试事实；不沿用原比较范围的审批或发布身份。"""
    if (not isinstance(value, dict) or value.get("schema_version") != EVIDENCE_SCHEMA_VERSION
            or value.get("acceptance_status") != "passed" or value.get("error") != ""
            or value.get("verification_scope") != "full"
            or value.get("planned_checks") != list(FULL_ACCEPTANCE_CHECKS)):
        return False
    binding = value.get("binding")
    if (not isinstance(binding, dict) or binding.get("candidate_tree") != tree
            or binding.get("contract_digest") != contract_digest()
            or binding.get("executed_checks") != list(FULL_ACCEPTANCE_CHECKS)):
        return False
    if (not valid_environment(value.get("environment")) or value["environment"] != expected_environment
            or value.get("environment_digest") != digest(expected_environment)):
        return False
    run_id, impact_id = value.get("run_id"), value.get("impact_id")
    if (not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id)
            or not isinstance(impact_id, str) or not re.fullmatch(r"[0-9a-f]{64}", impact_id)
            or not isinstance(value.get("verified_at"), str) or not value["verified_at"]):
        return False
    checks = value.get("checks")
    if not isinstance(checks, list) or len(checks) != len(FULL_ACCEPTANCE_CHECKS):
        return False
    previous_end = 0
    for name, check in zip(FULL_ACCEPTANCE_CHECKS, checks):
        if (not isinstance(check, dict) or check.get("check_id") != name or check.get("passed") is not True
                or type(check.get("exit_code")) is not int or check["exit_code"] != 0
                or type(check.get("log_start")) is not int or type(check.get("log_end")) is not int
                or check["log_start"] != previous_end or check["log_end"] <= previous_end
                or check.get("log_path") != f".local/story-gate/runs/{impact_id}/{run_id}/output.log"
                or not isinstance(check.get("log_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", check["log_sha256"])):
            return False
        previous_end = check["log_end"]
    return True

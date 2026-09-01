#!/usr/bin/env python3
"""AgenticOps 标准门禁请求执行器。

stdin/stdout 只使用 contracts/ 中的标准协议，不包含任何 Agent 平台字段。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gate import engine  # noqa: E402

PROTOCOL_VERSION = 1
CATALOG_PATH = Path(__file__).resolve().parent.parent / "contracts" / "operation-catalog.json"


def validate_request(request):
    if not isinstance(request, dict):
        return "请求必须是 JSON 对象"
    if request.get("protocol_version") != PROTOCOL_VERSION:
        return "不支持的 protocol_version：%s" % request.get("protocol_version")
    if request.get("event") != "before_operation":
        return "event 必须是 before_operation"
    request_fields = {"protocol_version", "event", "source", "cwd", "operations", "target", "note"}
    unexpected = sorted(set(request) - request_fields)
    if unexpected:
        return "请求包含未声明字段：%s" % ", ".join(unexpected)
    source = request.get("source")
    if not isinstance(source, dict):
        return "source 必须是对象"
    source_fields = {"agent", "adapter", "adapter_version", "tool_kind", "tool_name"}
    source_unexpected = sorted(set(source) - source_fields)
    if source_unexpected:
        return "source 包含未声明字段：%s" % ", ".join(source_unexpected)
    if not all(isinstance(source.get(key), str) and source.get(key) for key in ("agent", "adapter")):
        return "source 缺少 agent、adapter 或 adapter_version"
    if type(source.get("adapter_version")) is not int or source["adapter_version"] < 1:
        return "adapter_version 必须是大于等于 1 的整数"
    if any(
        key in source and not isinstance(source[key], str)
        for key in ("tool_kind", "tool_name")
    ):
        return "tool_kind 和 tool_name 必须是字符串"
    if not isinstance(request.get("cwd"), str) or not request["cwd"]:
        return "cwd 必须是非空字符串"
    operations = request.get("operations")
    if not isinstance(operations, list) or not operations or not all(
        isinstance(operation, str) and operation for operation in operations
    ):
        return "operations 必须是非空字符串列表"
    target = request.get("target", {})
    if not isinstance(target, dict):
        return "target 必须是对象"
    target_fields = {
        "repository", "git_cwd", "workspace", "issue_key", "branch", "push_source_ref",
        "push_destination_ref", "push_target_branch", "branch_relevant",
    }
    target_unexpected = sorted(set(target) - target_fields)
    if target_unexpected:
        return "target 包含未声明字段：%s" % ", ".join(target_unexpected)
    if any(
        key in target and not isinstance(target[key], str)
        for key in (
            "repository", "git_cwd", "workspace", "issue_key", "branch", "push_source_ref",
            "push_destination_ref", "push_target_branch",
        )
    ):
        return "target 仓库和分支字段必须是字符串"
    if "branch_relevant" in target and not isinstance(target["branch_relevant"], bool):
        return "branch_relevant 必须是布尔值"
    if "note" in request and not isinstance(request["note"], str):
        return "note 必须是字符串"
    return None


def load_operation_catalog(path=None):
    catalog_path = Path(path) if path else CATALOG_PATH
    with open(catalog_path, "r", encoding="utf-8") as stream:
        document = json.load(stream)
    return {item["name"]: item for item in document["operations"]}


def _context(request):
    for_push = "git_push" in request["operations"]
    target = request.get("target", {})
    git_cwd = request["cwd"]
    git_cwd_error = ""
    if target.get("git_cwd"):
        candidate = Path(target["git_cwd"])
        if not candidate.is_absolute():
            candidate = Path(request["cwd"]) / candidate
        candidate = candidate.resolve()
        workspace = engine.find_gate_root(request["cwd"])
        try:
            candidate.relative_to(workspace)
        except ValueError:
            git_cwd_error = "git -C 目录不在当前项目工作空间内"
        else:
            if not candidate.is_dir():
                git_cwd_error = "git -C 目录不存在或不是目录"
            else:
                git_cwd = str(candidate)
    context = engine.git_context(git_cwd, for_push=for_push)
    if target.get("git_cwd"):
        context["git_cwd"] = git_cwd
    if git_cwd_error:
        context["repository_fact_error"] = git_cwd_error
    context["push_refspec_required"] = (
        for_push and "unknown_external_write" not in request["operations"]
    )
    if target.get("repository"):
        requested_repository = engine.normalize_repo(target["repository"])
        if for_push:
            if context.get("origin") and context["origin"] != requested_repository:
                context["repository_fact_error"] = (
                    "标准请求仓库与 Git 实际 push URL 不一致"
                )
        else:
            context["origin"] = requested_repository
    if target.get("workspace"):
        workspace = Path(target["workspace"])
        if not workspace.is_absolute():
            workspace = Path(request["cwd"]) / workspace
        context["workspace"] = str(workspace.resolve())
    if target.get("issue_key"):
        context["issue_key"] = target["issue_key"]
    if target.get("branch"):
        context["branch"] = target["branch"]
    for field in ("push_source_ref", "push_destination_ref", "push_target_branch"):
        if target.get(field):
            context[field] = target[field]
    context["branch_relevant"] = target.get("branch_relevant", True)
    return context


def _evaluate_via_opa(operation, context, authorization, policy_path):
    rego_path = Path(__file__).resolve().parent.parent / "policies" / "operations.rego"
    input_document = {
        "operation": operation,
        "context": context,
        "authorization": authorization or {},
    }
    result = subprocess.run(
        [
            "opa",
            "eval",
            "--format=json",
            "-d",
            str(rego_path),
            "-d",
            str(policy_path),
            "-I",
            "data.agenticops.result",
        ],
        input=json.dumps(input_document),
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError("opa eval failed: %s" % result.stderr.strip())
    document = json.loads(result.stdout)
    return document["result"][0]["expressions"][0]["value"]


def _audit(cwd, context, record):
    try:
        task_directory = engine.find_task_directory(
            cwd, context=context, issue_key=context.get("issue_key")
        )
        if task_directory is None:
            root = engine.find_gate_root(cwd)
            workspace_state = root / ".agenticops"
            product_state = root / ".local" / "product.json"
            if (root / ".agentic-ops-source").is_file() or product_state.is_file():
                task_directory = root / ".local" / "gate"
            else:
                task_directory = workspace_state
        task_directory.mkdir(parents=True, exist_ok=True)
        with open(task_directory / "events.jsonl", "a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        return None
    except OSError as error:
        return "门禁事件写入失败：%s" % error


def evaluate_request(request, policy_path=None):
    """校验标准请求、执行判定并返回标准响应。"""
    validation_error = validate_request(request)
    if validation_error:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "decision": engine.DENY,
            "operation": "invalid_gate_request",
            "operations": ["invalid_gate_request"],
            "reason": validation_error,
            "reason_code": "invalid_gate_request",
            "required_action": "请修复标准 Gate 请求后重试。",
            "warnings": [],
        }

    policy_path = Path(
        policy_path or os.environ.get("AO_GATE_POLICY") or engine.POLICY_PATH
    )
    policy = engine.load_policy(policy_path)
    catalog = load_operation_catalog()
    target = request.get("target", {})
    context = _context(request)
    gate_cwd = context.get("workspace") or request["cwd"]
    resolution_context = context
    if "git_push" in request["operations"] and context.get("fetch_origin"):
        resolution_context = dict(context)
        resolution_context["origin"] = context["fetch_origin"]
    task_directory, task_resolution = engine.resolve_task_directory(
        gate_cwd, context=resolution_context, issue_key=context.get("issue_key")
    )
    context["task_resolution"] = task_resolution
    if (
        target.get("git_cwd")
        and task_directory is not None
        and not context.get("repository_fact_error")
        and not engine.task_worktree_matches(task_directory, context["git_cwd"], context)
    ):
        context["repository_fact_error"] = "git -C 目录不是当前任务已准备的 worktree"
    authorization = None
    authorization_path = None
    if task_directory is not None:
        path = task_directory / "authorization.json"
        authorization_path = str(path)
        if path.is_file():
            authorization = engine._read_json(path)
            context["authorization_state"] = (
                "loaded" if isinstance(authorization, dict) else "invalid"
            )
        else:
            context["authorization_state"] = "missing"
    audit_cwd = gate_cwd if task_directory is not None else request["cwd"]
    warnings = []

    policy_operations = set(policy["operations"])
    catalog_operations = set(catalog)
    if policy_operations != catalog_operations:
        missing_policy = sorted(catalog_operations - policy_operations)
        missing_contract = sorted(policy_operations - catalog_operations)
        result = {
            "decision": engine.DENY,
            "operation": "contract_policy_drift",
            "reason": "操作契约与 Policy 漂移：Policy 缺少=%s；Contract 缺少=%s"
            % (missing_policy, missing_contract),
            "reason_code": "contract_policy_drift",
            "required_action": "请维护者修复操作契约与 Policy 漂移后重试。",
        }
    elif any(operation not in catalog for operation in request["operations"]):
        unknown = sorted(operation for operation in request["operations"] if operation not in catalog)
        result = {
            "decision": engine.ASK,
            "operation": unknown[0],
            "reason": "未知标准操作，需人工确认并登记操作词表：%s" % ", ".join(unknown),
            "reason_code": "unknown_operation",
            "required_action": "请研发工程师确认本次操作；维护者应登记操作词表后再重试。",
        }
    elif any(not catalog[operation]["requestable"] for operation in request["operations"]):
        derived = sorted(
            operation for operation in request["operations"] if not catalog[operation]["requestable"]
        )
        result = {
            "decision": engine.DENY,
            "operation": "invalid_standard_operation",
            "reason": "派生操作不能作为 Adapter 请求输入：%s" % ", ".join(derived),
            "reason_code": "invalid_standard_operation",
            "required_action": "请修复 Adapter 映射，只请求可请求的标准操作。",
        }
    elif request["operations"].count("prepare_task_repository") > 1:
        result = {
            "decision": engine.ASK,
            "operation": "prepare_task_repository",
            "reason": "一个 Gate 请求包含多个受控仓库准备目标，无法唯一绑定工作空间",
            "reason_code": "ambiguous_workflow_target",
            "required_action": "请将每个任务工作空间的 repository prepare 拆成独立命令后重试。",
        }
    elif os.environ.get("AO_GATE_USE_OPA") == "1" and len(request["operations"]) == 1:
        try:
            result = _evaluate_via_opa(
                request["operations"][0], context, authorization, policy_path
            )
        except Exception as error:  # OPA 失败回退，但必须向调用方和审计暴露
            result = engine.evaluate_all(
                request["operations"], context, authorization, policy
            )
            warnings.append("OPA 回退到 Python 评估器：%s" % error)
    else:
        result = engine.evaluate_all(
            request["operations"], context, authorization, policy
        )

    response = {
        "protocol_version": PROTOCOL_VERSION,
        "decision": result["decision"],
        "operation": result["operation"],
        "operations": request["operations"],
        "reason": result["reason"],
        "reason_code": result["reason_code"],
        "warnings": warnings,
    }
    if result.get("required_action"):
        response["required_action"] = result["required_action"]
    audit_error = _audit(
        audit_cwd,
        context,
        {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "protocol_version": PROTOCOL_VERSION,
            "source": request["source"],
            "note": str(request.get("note", ""))[:400],
            "operations": request["operations"],
            "decision": response["decision"],
            "reason": response["reason"],
            "reason_code": response["reason_code"],
            "required_action": response.get("required_action"),
            "warnings": warnings,
            "authorization_file": authorization_path,
        },
    )
    if audit_error:
        response["warnings"].append(audit_error)
    return response


def main():
    try:
        request = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, TypeError):
        request = {}
    print(json.dumps(evaluate_request(request), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

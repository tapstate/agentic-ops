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
    target_fields = {"repository", "issue_key", "branch", "push_target_branch", "branch_relevant"}
    target_unexpected = sorted(set(target) - target_fields)
    if target_unexpected:
        return "target 包含未声明字段：%s" % ", ".join(target_unexpected)
    if any(
        key in target and not isinstance(target[key], str)
        for key in ("repository", "issue_key", "branch", "push_target_branch")
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
    context = engine.git_context(request["cwd"])
    target = request.get("target", {})
    if target.get("repository"):
        context["origin"] = engine.normalize_repo(target["repository"])
    if target.get("issue_key"):
        context["issue_key"] = target["issue_key"]
    if target.get("branch"):
        context["branch"] = target["branch"]
    if target.get("push_target_branch"):
        context["push_target_branch"] = target["push_target_branch"]
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
            task_directory = engine.find_gate_root(cwd) / ".gate"
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
            "warnings": [],
        }

    policy_path = Path(
        policy_path or os.environ.get("AO_GATE_POLICY") or engine.POLICY_PATH
    )
    policy = engine.load_policy(policy_path)
    catalog = load_operation_catalog()
    context = _context(request)
    authorization, authorization_path = engine.find_authorization(
        request["cwd"], context=context, issue_key=context.get("issue_key")
    )
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
        }
    elif any(operation not in catalog for operation in request["operations"]):
        unknown = sorted(operation for operation in request["operations"] if operation not in catalog)
        result = {
            "decision": engine.ASK,
            "operation": unknown[0],
            "reason": "未知标准操作，需人工确认并登记操作词表：%s" % ", ".join(unknown),
        }
    elif any(not catalog[operation]["requestable"] for operation in request["operations"]):
        derived = sorted(
            operation for operation in request["operations"] if not catalog[operation]["requestable"]
        )
        result = {
            "decision": engine.DENY,
            "operation": "invalid_standard_operation",
            "reason": "派生操作不能作为 Adapter 请求输入：%s" % ", ".join(derived),
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
        "warnings": warnings,
    }
    audit_error = _audit(
        request["cwd"],
        context,
        {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "protocol_version": PROTOCOL_VERSION,
            "source": request["source"],
            "note": str(request.get("note", ""))[:400],
            "operations": request["operations"],
            "decision": response["decision"],
            "reason": response["reason"],
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

#!/usr/bin/env python3
"""从调用方提供的 Jira 快照核对关联 Test；不访问 Jira 或创建用例。"""
from __future__ import annotations


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _policy(rules):
    policy = (rules.get("tests_passed") or {}).get("linked_test_task") or {}
    relations = set(policy.get("relations") or [])
    issue_types = set(policy.get("issue_types") or [])
    if not relations or not issue_types:
        raise ValueError("当前 Project 未配置 Tests Passed 关联 Test 的关系和任务类型")
    return policy, relations, issue_types


def _details(document):
    values = document.get("linked_test_details")
    if not isinstance(values, list):
        return None, ["Jira 快照缺少 linked_test_details；请读取每个关联 Test 的 Test Details，或请用户提供 Test key、Test Type、用例版本引用和 Jira 来源。"]
    by_key, problems = {}, []
    for value in values:
        if not isinstance(value, dict) or not _text(value.get("key")):
            problems.append("linked_test_details 含缺少 Test key 的记录")
            continue
        key = value["key"].upper()
        if key in by_key:
            problems.append("linked_test_details 中 Test %s 重复" % key)
            continue
        if not _text(value.get("test_type")):
            problems.append("关联 Test %s 无法读取 Test Type；请从 Jira Test Details 补充或请用户提供。" % key)
            continue
        if not _text(value.get("case_version")):
            problems.append("关联 Test %s 缺少 Jira 用例版本引用；请提供 Test 的 updated 时间或 Xray 版本引用。" % key)
            continue
        if not _text(value.get("source_ref")):
            problems.append("关联 Test %s 缺少可回查 Jira 来源。" % key)
            continue
        by_key[key] = dict(value, key=key)
    return by_key, problems


def linked_tests(document, issue_key, rules):
    """返回 (problems, managed, ignored)。

    ``linked_test_details`` 是 Agent 从 Jira/Xray 读取后随快照提供的事实，不是本地
    推断字段。每项必须含 key、test_type、case_version 与 source_ref。
    """
    issue = document.get("issue") or {}
    if str(issue.get("key") or "").upper() != issue_key:
        raise ValueError("Jira 验收快照不是当前任务")
    fields = issue.get("fields")
    if not isinstance(fields, dict):
        raise ValueError("Jira 验收快照缺少 issue.fields")
    links = fields.get("issuelinks")
    if not isinstance(links, list):
        return ["Jira 快照缺少 issue.fields.issuelinks；请重新读取缺陷「已链接工作项」或请用户提供当前链接。"], [], []

    _, relations, issue_types = _policy(rules)
    detail_by_key, problems = _details(document)
    if detail_by_key is None:
        detail_by_key = {}
    candidates = {}
    for link in links:
        if not isinstance(link, dict) or not isinstance(link.get("type"), dict):
            continue
        for direction, relation_key, target_key in (("outward", "outward", "outwardIssue"),
                                                     ("inward", "inward", "inwardIssue")):
            if link["type"].get(relation_key) not in relations:
                continue
            target = link.get(target_key)
            if not isinstance(target, dict):
                problems.append("关联测试任务链接缺少 %sIssue" % direction)
                continue
            if not _text(target.get("key")):
                problems.append("关联测试任务链接缺少工作项 key")
                continue
            target_fields = target.get("fields")
            issue_type = (target_fields or {}).get("issuetype") if isinstance(target_fields, dict) else None
            if not isinstance(issue_type, dict) or issue_type.get("name") not in issue_types:
                problems.append("关联任务 %s 不是允许的测试任务类型（期望 %s）" %
                                (target.get("key") or "未知", "、".join(sorted(issue_types))))
                continue
            candidates[str(target["key"]).upper()] = target

    if not candidates:
        problems.append("Jira 未返回符合配置的关联 Test；请由用户与 Agent 在「已链接工作项」创建或关联验收用例。")
        return problems, [], []

    type_rules = (rules.get("tests_passed") or {}).get("test_types") or {}
    ignored_rules = (rules.get("tests_passed") or {}).get("ignored_test_types") or {}
    managed, ignored = [], []
    for key in sorted(candidates):
        detail = detail_by_key.get(key)
        if not detail:
            problems.append("关联 Test %s 未返回 Test Details；请读取 Test Type、用例版本引用和 Jira 来源，或请用户提供。" % key)
            continue
        test_type = detail["test_type"].strip()
        if test_type in ignored_rules:
            ignored.append(dict(candidates[key], test_type=test_type,
                                case_version=detail["case_version"], source_ref=detail["source_ref"],
                                guidance=ignored_rules[test_type]))
            continue
        mapping = type_rules.get(test_type)
        if not isinstance(mapping, dict) or not _text(mapping.get("method")):
            problems.append("关联 Test %s 的 Test Type 为 %s，当前不支持；请用户调整 Test Type 或验收方案后重新读取 Jira。" %
                            (key, test_type))
            continue
        managed.append(dict(candidates[key], test_type=test_type,
                            case_version=detail["case_version"], source_ref=detail["source_ref"],
                            method=mapping["method"], guidance=mapping.get("guidance", "")))
    if not managed:
        suffix = "；已忽略 %s" % "、".join(item["key"] for item in ignored) if ignored else ""
        problems.append("没有可纳管的 Manual、TapTest 或 Unit 验收用例%s；请用户调整 Jira 或验收方案后重新读取 Jira。" % suffix)
    return problems, managed, ignored


def confirmation_problems(report, tests):
    """每个受管 Jira Test 都要有同版本、同方式的 Q4 PASS + 用户 accept。"""
    problems = []
    for test in tests:
        key = str(test["key"]).upper()
        items = [item for item in report["items"].values()
                 if item["plan"]["checkpoint"] == "q4-acceptance" and
                 str(item["plan"]["case_ref"]).upper() == key and
                 item["plan"]["case_version"] == test["case_version"] and
                 item["plan"]["method"] == test["method"]]
        if not items:
            problems.append("关联 Test %s 尚未以当前 Jira 用例版本和 Test Type 在 Q4 建立检查项" % key)
            continue
        if not any(item["decision_valid"] and
                   ((item.get("decision") or {}).get("decision") or {}).get("outcome") == "accept"
                   for item in items):
            problems.append("关联 Test %s 尚未由用户基于当前 SHA 的 PASS 证据确认测试成功" % key)
    return problems

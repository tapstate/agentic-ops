from __future__ import annotations

import json
import unittest
from unittest import mock

from ao_work.config.model import FieldMapping, ProjectProfile
from ao_work.jira.adf import markdown_to_adf
from ao_work.jira.model import JiraComment, JiraIssue
from ao_work.jira.task_facts import read_task_facts
from ao_work.output import RuntimeErrorResult
from ao_work.task_facts import execute_task_inspect


def _issue(description: dict[str, object] | None) -> JiraIssue:
    return JiraIssue(
        issue_id="99",
        key="TAP-99",
        project_key="TAP",
        summary="富事实读取",
        status="正在进行",
        issue_type="任务",
        assignee="account-1",
        description=description,
    )


def _profile() -> ProjectProfile:
    return ProjectProfile(
        profile_id="tapdata",
        connection_id="tapdata-cloud",
        project_key="TAP",
        task_query="project = TAP",
        repository_list=("tapdata/tapdata",),
        fields={
            "problem_branch": FieldMapping(
                logical_name="problem_branch",
                source="jira_description_section",
                section="问题分支",
            ),
            "raw_defect_log": FieldMapping(
                logical_name="raw_defect_log",
                source="jira_description_section",
                section="原始缺陷日志",
                state="read_only",
            ),
        },
    )


class TaskFactsTest(unittest.TestCase):
    def test_extracts_goal_description_and_comment_clues_without_sensitive_content(self) -> None:
        facts = read_task_facts(
            _issue(
                markdown_to_adf(
                    "# 目标\n修复 Description 富事实读取。\n\n"
                    "# 问题版本\ndevelop\n\n"
                    "# 问题现象\ntoken=super-secret\n\n"
                    "# 验收标准\n读取结果可用于设计。\n\n"
                    "# 问题分支\nrelease-v1\n\n"
                    "# 仓库分支\ntapdata/tapdata: develop\n\n"
                    "# 原始缺陷日志\npassword=must-not-leak"
                )
            ),
            [
                JiraComment(
                    comment_id="100",
                    author="reviewer",
                    created="2026-08-25T00:00:00Z",
                    body="# 异常摘要\npassword: comment-secret\n\n# 候选仓库/分支\ntapdata/tapdata: feature/facts",
                )
            ],
            _profile(),
        )

        description_facts = {item["field"]: item for item in facts["description"]["facts"]}
        self.assertEqual("修复 Description 富事实读取。", description_facts["task_goal"]["value"])
        self.assertEqual("token=[REDACTED]", description_facts["exception_summary"]["value"])
        self.assertEqual("release-v1", description_facts["problem_branch"]["value"])
        self.assertNotIn("原始缺陷日志", facts["description"]["sections"])
        self.assertEqual("password:[REDACTED]", facts["comments"]["facts"][0]["value"])
        self.assertEqual("100", facts["comments"]["facts"][0]["comment_id"])
        self.assertEqual("proposal_only", facts["repository_branch_hints"][1]["confirmation_status"])
        self.assertNotIn("super-secret", json.dumps(facts, ensure_ascii=False))
        self.assertNotIn("comment-secret", json.dumps(facts, ensure_ascii=False))
        self.assertNotIn("must-not-leak", json.dumps(facts, ensure_ascii=False))

    def test_uses_bounded_description_overview_as_goal_when_no_heading_exists(self) -> None:
        facts = read_task_facts(
            _issue(markdown_to_adf("修复任务必须围绕这个 Description 的目标展开。")),
            [],
            _profile(),
        )

        self.assertEqual(
            "修复任务必须围绕这个 Description 的目标展开。",
            facts["description"]["facts"][0]["value"],
        )
        self.assertEqual("__overview__", facts["description"]["facts"][0]["section"])

    def test_rejects_unsupported_comment_body(self) -> None:
        with self.assertRaises(RuntimeErrorResult) as captured:
            read_task_facts(
                _issue(markdown_to_adf("# 目标\n安全读取")),
                [JiraComment(comment_id="1", body="", body_supported=False)],
                _profile(),
            )
        self.assertEqual("jira_task_comment_unsupported", captured.exception.code)

    def test_task_inspect_keeps_local_state_and_returns_task_facts(self) -> None:
        store = mock.Mock()
        store.inspect.return_value = {
            "task": {"issue_key": "TAP-99", "agentic_run_id": "run-99"}
        }
        facts = {
            "schema_version": 1,
            "description": {"facts": [{"field": "task_goal", "value": "读取 Description"}]},
            "comments": {"facts": []},
            "repository_branch_hints": [],
        }
        with mock.patch(
            "ao_work.task_facts.execute_task_facts",
            return_value={"task_facts": facts, "side_effects": []},
        ) as task_facts:
            result = execute_task_inspect(
                mock.Mock(),
                mock.Mock(),
                store,
                "TAP-99",
            )

        self.assertEqual("TAP-99", result["task"]["issue_key"])
        self.assertEqual(facts, result["task_facts"])
        self.assertEqual([], result["side_effects"])
        task_facts.assert_called_once()

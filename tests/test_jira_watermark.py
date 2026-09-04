#!/usr/bin/env python3
"""接管 Jira 版本水印：本地意图、回读与 Product Root 版本测试。"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bootstrap import product_version  # noqa: E402
from workflow import jira_watermark, project_rules, task_store  # noqa: E402


class JiraWatermarkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ao-jira-watermark-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.product = self.base / "product"
        shutil.copytree(ROOT / "projects" / "tapdata", self.product / "projects" / "tapdata")
        subprocess.run(["git", "init", "-q", "-b", "develop"], cwd=self.product, check=True)
        subprocess.run(["git", "add", "."], cwd=self.product, check=True)
        subprocess.run(
            ["git", "-c", "user.email=test@example.invalid", "-c", "user.name=Test", "commit", "-qm", "fixture"],
            cwd=self.product, check=True,
        )
        (self.base / ".agenticops").mkdir()
        task_store._write_json_atomic(
            self.base / ".agenticops/workspace.json",
            {"project": "tapdata", "product_root": str(self.product)},
        )
        self.task = {
            "issue_key": "TAP-123", "run_id": "run-0123456789ab", "task_class": "defect_fix",
            "stage": "waiting_takeover", "facts": {}, "repositories": [], "pending": None, "history": [],
        }
        task_store._write_json_atomic(task_store.task_path(self.base, "TAP-123"), self.task)
        task_store.register(self.base, "TAP-123")

    def snapshot(self, value=None, issue_type_id="10011"):
        fields = {"issuetype": {"id": issue_type_id, "name": "Bug"}}
        if value is not None:
            fields["customfield_10421"] = value
        return {"source_ref": "fixture:jira/TAP-123", "issue": {"key": "TAP-123", "fields": fields}}

    def test_prepare_then_verified_readback(self):
        record = jira_watermark.prepare(self.base, "TAP-123", self.snapshot())
        self.assertEqual(record["outcome"], "ready")
        self.assertEqual(record["field_id"], "customfield_10421")
        self.assertEqual(record["native_request"]["issue_key"], "TAP-123")
        version = record["version"]
        self.assertEqual(record["native_request"]["fields"], {"customfield_10421": version})
        self.assertEqual(record["payload_digest"], jira_watermark.payload_digest("customfield_10421", version))
        done = jira_watermark.complete(self.base, "TAP-123", "unknown", self.snapshot(version))
        self.assertEqual(done["outcome"], "verified")
        self.assertEqual(jira_watermark.takeover_problems(self.base, self.task), [])

    def test_matching_readback_skips_external_write(self):
        version = product_version.describe(self.product)
        record = jira_watermark.prepare(self.base, "TAP-123", self.snapshot(version))
        self.assertEqual(record["outcome"], "verified")
        self.assertEqual(record["reason"], "already_current")
        self.assertNotIn("native_request", record)

    def test_unverified_write_can_converge_by_readback_without_rewrite(self):
        jira_watermark.prepare(self.base, "TAP-123", self.snapshot())
        result = jira_watermark.complete(self.base, "TAP-123", "unknown", self.snapshot("old-version"))
        self.assertEqual(result["outcome"], "unknown")
        self.assertTrue(jira_watermark.takeover_problems(self.base, self.task))
        repeated = jira_watermark.prepare(self.base, "TAP-123", self.snapshot())
        self.assertTrue(repeated["repeated"])
        self.assertEqual(repeated["outcome"], "unknown")
        resolved = jira_watermark.complete(self.base, "TAP-123", "unknown", self.snapshot(result["version"]))
        self.assertEqual(resolved["outcome"], "verified")
        self.assertNotIn("native_request", resolved)

    def test_external_error_message_is_redacted(self):
        jira_watermark.prepare(self.base, "TAP-123", self.snapshot())
        result = jira_watermark.complete(
            self.base, "TAP-123", "failed", self.snapshot("old-version"), "password=do-not-store"
        )
        self.assertEqual(result["message"], "外部错误信息含敏感内容，原文未保存")

    def test_unconfigured_issue_type_and_dirty_product_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "事务类型"):
            jira_watermark.prepare(self.base, "TAP-123", self.snapshot(issue_type_id="99999"))
        (self.product / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "未提交改动"):
            product_version.describe(self.product)

    def test_readback_rejects_changed_issue_type_or_product_version(self):
        record = jira_watermark.prepare(self.base, "TAP-123", self.snapshot())
        with self.assertRaisesRegex(ValueError, "工作项类型"):
            jira_watermark.complete(self.base, "TAP-123", "unknown", self.snapshot(
                record["version"], issue_type_id="10008"
            ))
        (self.product / "next.txt").write_text("next\n", encoding="utf-8")
        subprocess.run(["git", "add", "next.txt"], cwd=self.product, check=True)
        subprocess.run(
            ["git", "-c", "user.email=test@example.invalid", "-c", "user.name=Test", "commit", "-qm", "next"],
            cwd=self.product, check=True,
        )
        stale = jira_watermark.complete(self.base, "TAP-123", "unknown", self.snapshot(record["version"]))
        self.assertEqual(stale["outcome"], "stale")

    def test_incomplete_verified_record_fails_closed(self):
        path = jira_watermark.state_path(self.base, self.task)
        task_store._write_json_atomic(
            path,
            {"schema_version": 1, "issue_key": "TAP-123", "run_id": self.task["run_id"],
             "watermark": {"outcome": "verified", "reason": "forged"}},
        )
        with self.assertRaisesRegex(ValueError, "缺少必要字段"):
            jira_watermark.takeover_problems(self.base, self.task)

    def test_missing_project_watermark_configuration_fails_closed(self):
        profile_path = self.product / "projects" / "tapdata" / "profile.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        del profile["jira"]["takeover_watermark"]
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "takeover_watermark"):
            project_rules.load_profile(root=self.product)


if __name__ == "__main__":
    unittest.main()

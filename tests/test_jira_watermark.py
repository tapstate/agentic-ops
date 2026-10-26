#!/usr/bin/env python3
"""接管 Jira 版本水印：本地意图、回读与 Product Root 版本测试。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bootstrap import product_version, station_compatibility  # noqa: E402
from workflow import jira_watermark, project_rules, task_store  # noqa: E402
from station_fixture import save_task as save_station_task


class JiraWatermarkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ao-jira-watermark-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.product = self.base / "product"
        shutil.copytree(ROOT / "projects" / "tapdata", self.product / "projects" / "tapdata")
        # 旧版单字段记录仍可按原意图回读；新协议另有专项场景。
        profile_path = self.product / "projects/tapdata/profile.json"
        profile = json.loads(profile_path.read_text())
        profile["jira"]["takeover_watermark"]["schema_version"] = 1
        profile["jira"]["takeover_watermark"].pop("marker_fields", None)
        profile_path.write_text(json.dumps(profile))
        shutil.copytree(ROOT / "contracts", self.product / "contracts")
        shutil.copy2(ROOT / ".gitignore", self.product / ".gitignore")
        subprocess.run(["git", "init", "-q", "-b", "develop"], cwd=self.product, check=True)
        subprocess.run(["git", "add", "."], cwd=self.product, check=True)
        subprocess.run(
            ["git", "-c", "user.email=test@example.invalid", "-c", "user.name=Test", "commit", "-qm", "fixture"],
            cwd=self.product, check=True,
        )
        (self.base / ".agenticops").mkdir()
        task_store._write_json_atomic(
            self.base / ".agenticops/station.json",
            {"project": "tapdata", "product_root": str(self.product)},
        )
        self.task = {
            "issue_key": "TAP-123", "run_id": "run-0123456789ab", "task_class": "defect_fix",
            "stage": "waiting_takeover", "facts": {}, "repositories": [], "pending": None, "history": [],
        }
        save_station_task(self.base, self.task)


    def test_product_git_facts_ignore_foreign_repository_environment(self):
        foreign = self.base / "foreign"
        subprocess.run(["git", "clone", "-q", str(self.product), str(foreign)], check=True)
        subprocess.run(["git", "checkout", "-qb", "foreign"], cwd=foreign, check=True)
        manifest = self.product / "contracts/station-state-compatibility.json"
        old_epoch = json.loads(manifest.read_text())["station_state_epoch"]
        manifest.write_text(json.dumps({"station_state_epoch": old_epoch + 1}))
        for repo in (self.product, foreign):
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "-c", "user.email=test@example.invalid", "-c", "user.name=Test",
                            "commit", "--allow-empty", "-qm", "fixture-next"], cwd=repo, check=True)
        expected = product_version.describe(self.product)
        for environment in ({"GIT_DIR": str(foreign / ".git"), "GIT_WORK_TREE": str(foreign)},
                            {"GIT_CONFIG_COUNT": "invalid"}):
            with self.subTest(environment=environment), mock.patch.dict(os.environ, environment):
                inherited = dict(os.environ)
                with self.subTest(fact="version"):
                    self.assertEqual(expected, product_version.describe(self.product))
                with self.subTest(fact="epoch"):
                    self.assertEqual(old_epoch + 1, station_compatibility.manifest_at_ref(self.product, "HEAD")["station_state_epoch"])
                with self.subTest(fact="upgrade"), self.assertRaisesRegex(ValueError, "工位登记清单缺失"):
                    station_compatibility.check_upgrade(self.product, "HEAD~1", "HEAD")
                result = subprocess.run([sys.executable, str(ROOT / "bootstrap/product_version.py"),
                    "--product-root", str(self.product)], cwd=self.base, capture_output=True, text=True)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(expected, result.stdout.strip())
                self.assertEqual(inherited, dict(os.environ))

    def snapshot(self, value=None, issue_type_id="10011"):
        fields = {"issuetype": {"id": issue_type_id, "name": "Bug"}}
        if value is not None:
            fields["customfield_10421"] = value
        return {"source_ref": "fixture:jira/TAP-123", "issue": {"key": "TAP-123", "fields": fields}}

    def test_prepare_then_verified_readback(self):
        from workflow import archive_store
        reservation = archive_store.reserve(self.base, self.task['issue_key'], self.task['run_id'])
        record = jira_watermark.prepare(self.base, "TAP-123", self.snapshot())
        self.assertEqual(record["outcome"], "ready")
        self.assertEqual(record["field_id"], "customfield_10421")
        self.assertEqual(record["native_request"]["issue_key"], "TAP-123")
        version = record["version"]
        self.assertEqual(record["native_request"]["fields"], {"customfield_10421": version})
        self.assertEqual(record["payload_digest"], jira_watermark.payload_digest("customfield_10421", version))
        done = jira_watermark.complete(self.base, "TAP-123", "unknown", self.snapshot(version))
        self.assertEqual(done["outcome"], "verified")
        self.assertEqual(jira_watermark.takeover_warnings(self.base, self.task), [])
        self.assertEqual([], list(reservation.iterdir()))

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
        self.assertTrue(jira_watermark.takeover_warnings(self.base, self.task))
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
            jira_watermark.takeover_warnings(self.base, self.task)

    def test_missing_project_watermark_configuration_fails_closed(self):
        profile_path = self.product / "projects" / "tapdata" / "profile.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        del profile["jira"]["takeover_watermark"]
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "takeover_watermark"):
            project_rules.load_profile(root=self.product, project="tapdata")


class TerminalWatermarkTests(unittest.TestCase):
    setUp = JiraWatermarkTests.setUp
    snapshot = JiraWatermarkTests.snapshot

    def test_terminal_failed_stays_unknown_until_original_readback(self):
        from workflow import external_sync, station_resources
        record = jira_watermark.prepare(self.base, "TAP-123", self.snapshot())
        jira_watermark.complete(self.base, "TAP-123", "failed", self.snapshot("old"))
        self.task.update(stage="completed", outcome="completed")
        save_station_task(self.base, self.task)
        with self.assertRaisesRegex(ValueError, "未知"):
            station_resources.verify_known_external(self.base, self.task)
        with self.assertRaises(ValueError):
            jira_watermark.prepare(self.base, "TAP-123", self.snapshot())
        result = jira_watermark.complete(self.base, "TAP-123", "unknown", self.snapshot(record["version"]))
        self.assertEqual(result["outcome"], "verified")
        station_resources.verify_known_external(self.base, self.task)

    def test_stale_product_does_not_hide_unknown_original_write(self):
        from workflow import external_sync, station_resources
        record = jira_watermark.prepare(self.base, "TAP-123", self.snapshot())
        self.task.update(stage="completed", outcome="completed")
        save_station_task(self.base, self.task)
        subprocess.run(["git", "-c", "user.email=test@example.invalid", "-c", "user.name=Test", "commit", "--allow-empty", "-qm", "next"], cwd=self.product, check=True)
        stale = jira_watermark.complete(self.base, "TAP-123", "unknown", self.snapshot("different"))
        self.assertEqual(stale["outcome"], "stale")
        with self.assertRaisesRegex(ValueError, "未知"):
            station_resources.verify_known_external(self.base, self.task)
        jira_watermark.complete(self.base, "TAP-123", "unknown", self.snapshot(record["version"]))
        station_resources.verify_known_external(self.base, self.task)
        action = next(row for row in external_sync.actions(self.base, self.task)["sync_actions"] if row["id"] == "agenticops_version")
        self.assertTrue(action["original_target_observed"])
        self.assertEqual(action["next_action"], "manual_handoff")


class FourMarkerTests(unittest.TestCase):
    def setUp(self):
        JiraWatermarkTests.setUp(self)
        path = self.product / "projects/tapdata/profile.json"
        profile = json.loads(path.read_text())
        profile['jira']['takeover_watermark'] = json.loads((ROOT / 'projects/tapdata/profile.json').read_text())['jira']['takeover_watermark']
        path.write_text(json.dumps(profile))
        subprocess.run(['git', 'add', '.'], cwd=self.product, check=True)
        subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'markers'], cwd=self.product, check=True)

    def snapshot(self, values=None):
        fields = dict.fromkeys(['customfield_10421', 'customfield_10366', 'customfield_10462', 'customfield_10365'])
        fields.update(values or {})
        fields['issuetype'] = {'id': '10011'}
        return {'source_ref': 'fixture:jira/read', 'issue': {'key': 'TAP-123', 'fields': fields},
                'current_user': {'accountId': 'jira-login', 'displayName': '登录研发', 'active': True, 'source_ref': 'fixture:jira/myself'}}

    def test_four_field_write_readback_and_resume_preserves_original(self):
        record = jira_watermark.prepare(self.base, 'TAP-123', self.snapshot())
        self.assertEqual(4, len(record['native_request']['fields']))
        self.assertEqual('a'*32, record['expected_fields']['customfield_10462'])
        self.assertEqual(self.task['run_id'], record['expected_fields']['customfield_10365'])
        self.assertFalse(jira_watermark.summary_ready(self.base, self.task))
        readback = self.snapshot(record['expected_fields'])
        result = jira_watermark.complete(self.base, 'TAP-123', 'unknown', readback)
        self.assertTrue(jira_watermark.summary_ready(self.base, self.task))
        self.assertEqual('verified', result['outcome'])
        with mock.patch.object(product_version, 'describe', side_effect=ValueError('升级后的产品不应刷新原版本')):
            repeated = jira_watermark.prepare(self.base, 'TAP-123', readback)
        self.assertEqual(record['expected_fields'], repeated['expected_fields'])
        self.assertNotIn('native_request', repeated)
        # Jira 时区和毫秒表示差异不应导致误判。
        readback['issue']['fields']['customfield_10366'] = jira_watermark.marker_time(record['expected_fields']['customfield_10366']).isoformat(timespec='milliseconds')
        self.assertEqual('verified', jira_watermark.complete(self.base, 'TAP-123', 'unknown', readback)['outcome'])

    def test_partial_readback_or_lost_ownership_blocks_summary(self):
        record = jira_watermark.prepare(self.base, 'TAP-123', self.snapshot())
        partial = self.snapshot(record['expected_fields'])
        del partial['issue']['fields']['customfield_10462']
        with self.assertRaisesRegex(ValueError, '不完整'):
            jira_watermark.complete(self.base, 'TAP-123', 'unknown', partial)
        changed = dict(record['expected_fields'], customfield_10462='b'*32, customfield_10365='TAP-123-other')
        result = jira_watermark.complete(self.base, 'TAP-123', 'unknown', self.snapshot(changed))
        self.assertEqual('unknown', result['outcome'])
        self.assertNotIn('native_request', result)
        self.assertFalse(jira_watermark.summary_ready(self.base, self.task))
        self.assertEqual(record['expected_fields'], result['expected_fields'])

    def test_previous_run_requires_bound_resolution_and_current_coordination(self):
        prior = {'customfield_10421': 'old-version', 'customfield_10366': '2026-01-01T00:00:00Z',
                 'customfield_10462': 'b'*32, 'customfield_10365': 'TAP-123-old'}
        snapshot = self.snapshot(prior)
        with self.assertRaisesRegex(ValueError, '旧 run'):
            jira_watermark.prepare(self.base, 'TAP-123', snapshot)
        snapshot['previous_takeover'] = {'observed_fields': prior, 'evidence_ref': 'fixture:jira/confirmed-handoff'}
        record = jira_watermark.prepare(self.base, 'TAP-123', snapshot)
        self.assertEqual(self.task['run_id'], record['expected_fields']['customfield_10365'])
        self.assertEqual(prior, record['previous_fields'])

    def test_same_station_new_run_also_needs_cleanup_evidence(self):
        snapshot = self.snapshot({'customfield_10462': 'a'*32, 'customfield_10365': 'old-run'})
        with self.assertRaisesRegex(ValueError, '旧 run'):
            jira_watermark.prepare(self.base, 'TAP-123', snapshot)

    def test_missing_field_or_actor_not_inferred_from_assignee(self):
        snapshot = self.snapshot()
        snapshot['issue']['fields']['assignee'] = {'accountId': 'assignee', 'displayName': '负责人'}
        del snapshot['current_user']
        with self.assertRaisesRegex(ValueError, '登录用户'):
            jira_watermark.prepare(self.base, 'TAP-123', snapshot)
        snapshot = self.snapshot()
        del snapshot['issue']['fields']['customfield_10366']
        with self.assertRaisesRegex(ValueError, '不完整'):
            jira_watermark.prepare(self.base, 'TAP-123', snapshot)

    def test_proven_not_applied_retries_original_payload_only(self):
        record = jira_watermark.prepare(self.base, 'TAP-123', self.snapshot())
        self.assertEqual('unknown', jira_watermark.prepare(self.base, 'TAP-123', self.snapshot())['outcome'])
        snapshot = self.snapshot()
        snapshot['not_applied_ref'] = 'fixture:request-rejected-no-write'
        retried = jira_watermark.prepare(self.base, 'TAP-123', snapshot)
        self.assertEqual('ready', retried['outcome'])
        self.assertEqual(record['native_request'], retried['native_request'])
        snapshot['issue']['fields']['customfield_10365'] = 'another-run'
        with self.assertRaisesRegex(ValueError, '原写前观察'):
            jira_watermark.prepare(self.base, 'TAP-123', snapshot)
        self.assertNotIn('native_request', jira_watermark.status(self.base, self.task))

    def test_legacy_verified_is_preserved_and_upgraded_without_replacing_version(self):
        rules = jira_watermark.config(self.base)
        legacy_rules = dict(rules, schema_version=1)
        legacy_rules.pop('marker_fields')
        with mock.patch.object(jira_watermark, 'config', return_value=legacy_rules):
            legacy = jira_watermark.prepare(self.base, 'TAP-123', self.snapshot())
        with self.assertRaisesRegex(ValueError, '旧版水印'):
            jira_watermark.prepare(self.base, 'TAP-123', self.snapshot())
        observed = self.snapshot({'customfield_10421': legacy['version']})
        jira_watermark.complete(self.base, 'TAP-123', 'unknown', observed)
        self.assertFalse(jira_watermark.summary_ready(self.base, self.task))
        observed['previous_takeover'] = {'observed_fields': {k:v for k,v in observed['issue']['fields'].items() if k.startswith('customfield_')},
                                         'evidence_ref': 'fixture:legacy-run-observed'}
        with mock.patch.object(product_version, 'describe', side_effect=AssertionError('不刷新接管版本')):
            upgraded = jira_watermark.prepare(self.base, 'TAP-123', observed)
        self.assertEqual(legacy['version'], upgraded['version'])
        self.assertEqual(legacy['at'], upgraded['expected_fields']['customfield_10366'])
        self.assertIn('legacy_watermark', jira_watermark.load_state(self.base, self.task))

    def test_terminal_readback_confirms_only_original_four_fields(self):
        record = jira_watermark.prepare(self.base, 'TAP-123', self.snapshot())
        self.task.update(stage='completed', outcome='completed')
        save_station_task(self.base, self.task)
        with self.assertRaises(ValueError):
            jira_watermark.prepare(self.base, 'TAP-123', self.snapshot())
        result = jira_watermark.complete(self.base, 'TAP-123', 'unknown', self.snapshot(record['expected_fields']))
        self.assertEqual('verified', result['outcome'])

if __name__ == "__main__":
    unittest.main()

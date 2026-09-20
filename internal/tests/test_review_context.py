"""维护审查摘要保留原始判定与错误，不产生验收或批准。"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("review_context", ROOT / "skills/ao-review-change/scripts/review-context.py")
review_context = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review_context)


class ReviewContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / ".agentic-ops-source").touch()
        self.entry = self.root / "internal/bin/story-gate"
        self.entry.parent.mkdir(parents=True)

    def gate(self, payload, code=0):
        self.entry.write_text('#!%s\nimport json,sys\npayload=%r\npayload["changed_paths"]=sys.argv[1:]\nprint(json.dumps(payload), file=sys.stderr if %s else sys.stdout)\nraise SystemExit(%s)\n' %
                              (sys.executable, payload, code, code))
        self.entry.chmod(0o700)

    def test_real_process_keeps_candidate_risks_and_unverified_status(self):
        self.gate({"ok": True, "impact_id": "candidate", "acceptance_status": "not_run", "approved": False,
                   "review_report": {"risks": [{"description": "仍需审查"}], "confirmation_items": ["scope"],
                                     "change_points": ["duplicate detail"]}})
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result, code = review_context.collect(self.root, "range", "literal;$(false)", "HEAD")
        self.assertEqual(code, 0)
        self.assertEqual(result["changed_paths"], ["impact", "--change-source", "range", "--base", "literal;$(false)", "--head", "HEAD"])
        self.assertEqual(result["acceptance_status"], "not_run")
        self.assertFalse(result["approved"])
        self.assertEqual(result["risks"], [{"description": "仍需审查"}])
        self.assertEqual(result["confirmation_items"], ["scope"])
        self.assertNotIn("change_points", result)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_acceptance_summary_is_preserved_without_inventing_approval(self):
        evidence = {"run_id": "a" * 32, "checks": [{"check_id": "python_runtime", "passed": True,
                    "exit_code": 0, "duration_seconds": 2.5}]}
        for value, status in ((evidence, "passed"), (None, "not_run")):
            with self.subTest(status=status):
                self.gate({"ok": True, "acceptance_status": status, "acceptance_evidence": value, "approved": False})
                result, code = review_context.collect(self.root, "staged")
                self.assertEqual(code, 0)
                self.assertEqual(value, result["acceptance_evidence"])
                self.assertEqual(status, result["acceptance_status"])
                self.assertFalse(result["approved"])

    def test_original_failure_and_handoff_are_preserved(self):
        self.gate({"ok": False, "code": "story_mapping_missing", "message": "缺少映射",
                   "required_human_action": "补齐映射", "unmapped_paths": ["new.py"]}, 3)
        result, code = review_context.collect(self.root, "staged")
        self.assertEqual(code, 3)
        self.assertEqual(result["code"], "story_mapping_missing")
        self.assertEqual(result["unmapped_paths"], ["new.py"])
        self.assertEqual(result["required_human_action"], "补齐映射")

    def test_malformed_protocol_does_not_become_success(self):
        self.entry.touch()
        for output in ("not-json", "[]", '{"ok":"true"}'):
            with self.subTest(output=output), mock.patch.object(review_context.subprocess, "run", return_value=
                    subprocess.CompletedProcess([], 0, output, "")):
                with self.assertRaises(ValueError):
                    review_context.collect(self.root, "staged")

    def test_failure_payload_with_zero_exit_is_still_failure(self):
        self.gate({"ok": False, "message": "失败"})
        self.assertEqual(review_context.collect(self.root, "staged")[1], 1)

    def test_installed_root_is_rejected_before_execution(self):
        self.gate({"ok": True})
        (self.root / ".agentic-ops-source").unlink()
        with mock.patch.object(review_context.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "源码产品根"):
                review_context.collect(self.root, "staged")
            run.assert_not_called()

    def test_range_cli_requires_explicit_anchors(self):
        result = subprocess.run([sys.executable, str(spec.origin), "--change-source", "range"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--base", result.stderr)


if __name__ == "__main__":
    unittest.main()

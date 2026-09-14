"""真实 XML/ZIP 输入和多 PR 分析边界回归。"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/maven_reports.py"
SPEC = importlib.util.spec_from_file_location("reports", SCRIPT)
reports = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reports)


def xml(child="", skipped=0, failures=0, name="case"):
    return ('<testsuite name="Suite" tests="1" failures="%s" errors="0" skipped="%s">'
            '<testcase classname="IT" name="%s">%s</testcase></testsuite>' %
            (failures, skipped, name, child)).encode()


class ReportsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def archive(self, members):
        path = self.root / "reports.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name, raw in members.items():
                archive.writestr(name, raw)
        return path

    def test_multimodule_zip_and_summary_not_double_counted(self):
        summary = b'<failsafe-summary><completed>1</completed><failures>0</failures><errors>0</errors><skipped>0</skipped></failsafe-summary>'
        path = self.archive({"a/TEST-A.xml": xml(), "b/TEST-B.xml": xml(name="other"), "a/failsafe-summary.xml": summary})
        result = reports.analyze(path)
        self.assertEqual("complete", result["parse_status"])
        self.assertEqual(2, len(result["reports"]))
        self.assertEqual(1, len(result["summary_files"]))

    def test_skip_reason_failure_detail_and_redaction(self):
        parsed = reports.parse_xml(xml('<failure message="password=abc">bad assertion token=secret</failure>', failures=1), "TEST.xml")
        detail = parsed["cases"][0]["details"][0]
        self.assertNotIn("abc", detail["message"])
        self.assertNotIn("secret", detail["text"])
        skipped = reports.parse_xml(xml('<skipped message="unsupported"/>', skipped=1), "TEST.xml")
        self.assertEqual("unsupported", skipped["cases"][0]["details"][0]["message"])

    def test_invalid_counts_entities_and_duplicate_cases_rejected(self):
        samples = [xml().replace(b'tests="1"', b'tests="2"'),
                   b'<!DOCTYPE x [<!ENTITY a "b">]>' + xml(),
                   xml().replace(b'</testcase>', b'</testcase><testcase classname="IT" name="case"/>').replace(b'tests="1"', b'tests="2"')]
        for raw in samples:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                reports.parse_xml(raw, "TEST.xml")

    def test_partial_duplicate_and_broken_reports_remain_visible(self):
        path = self.archive({"TEST-A.xml": xml(), "TEST-copy.xml": xml(), "TEST-bad.xml": b'<broken'})
        result = reports.analyze(path)
        self.assertEqual("partial", result["parse_status"])
        self.assertEqual(1, len(result["reports"]))
        self.assertEqual(2, len(result["problems"]))

    def test_path_traversal_rejected_without_extraction(self):
        path = self.archive({"../TEST-escape.xml": xml()})
        self.assertEqual("unavailable", reports.analyze(path)["parse_status"])
        self.assertFalse((self.root.parent / "TEST-escape.xml").exists())

    def test_summary_conflict_and_zero_tests(self):
        path = self.archive({"TEST-A.xml": xml(), "failsafe-summary.xml": b'<failsafe-summary><completed>2</completed></failsafe-summary>'})
        self.assertEqual("partial", reports.analyze(path)["parse_status"])
        zero = reports.parse_xml(b'<testsuite tests="0" failures="0" errors="0" skipped="0"/>', "TEST-zero.xml")
        self.assertEqual(0, zero["counts"]["tests"])
        self.assertNotIn("status", zero)

    def test_multi_pr_missing_report_and_nonzero_exit_not_pass(self):
        path = self.archive({"TEST-A.xml": xml()})
        document = {"schema_version": 1, "objects": [
            {"id": "a", "repository": "org/a", "path": str(path), "execution": {"exit_code": 1}},
            {"id": "b", "repository": "org/b", "reason": "artifact 过期"}]}
        result = reports.task_report(document)
        self.assertTrue(result["processing_complete"])
        self.assertEqual(1, result["objects"][0]["execution"]["exit_code"])
        self.assertEqual("unavailable", result["objects"][1]["analysis"]["parse_status"])
        self.assertEqual("unknown", result["objects"][0]["provenance"]["status"])
        self.assertNotIn("passed", result)

    def test_candidates_are_not_selected_by_success_or_time(self):
        result = reports.task_report({"schema_version": 1, "objects": [
            {"id": "old", "repository": "org/a", "execution": {"conclusion": "success"}},
            {"id": "new", "repository": "org/a", "execution": {"conclusion": "failure"}}]})
        self.assertEqual(["unresolved", "unresolved"], [x["selection"] for x in result["objects"]])
        self.assertEqual(2, len(result["objects"]))

    def test_different_files_with_overlapping_cases_are_conflict(self):
        path = self.archive({"TEST-A.xml": xml(), "TEST-B.xml": xml().replace(b'<testcase ', b'<testcase time="2" ')})
        self.assertEqual("partial", reports.analyze(path)["parse_status"])

    def test_invalid_manifest_has_explicit_error(self):
        for value in ([], {"schema_version": 1, "objects": [None]}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                reports.task_report(value)

    def test_limits_and_utf16_entities(self):
        raw = ('<!DOCTYPE x [<!ENTITY a "b">]>' + xml().decode()).encode("utf-16")
        with self.assertRaises(ValueError):
            reports.parse_xml(raw, "TEST.xml")
        path = self.archive({"TEST-large.xml": b"x" * (reports.LIMIT + 1)})
        self.assertEqual("unavailable", reports.analyze(path)["parse_status"])

    def test_cli_processing_exit_not_quality_pass(self):
        file = self.root / "manifest.json"
        file.write_text(json.dumps({"schema_version": 1, "objects": [{"id": "missing", "repository": "org/a"}]}))
        result = subprocess.run([sys.executable, str(SCRIPT), "--input", str(file)], capture_output=True, text=True)
        self.assertEqual(0, result.returncode)
        self.assertEqual("unavailable", json.loads(result.stdout)["objects"][0]["analysis"]["parse_status"])


if __name__ == "__main__":
    unittest.main()

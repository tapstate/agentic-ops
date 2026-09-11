"""连接器选择与真实 Failsafe 结果核验。"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/connector_tests.py"
SPEC = importlib.util.spec_from_file_location("connector_tests", SCRIPT)
connector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(connector)


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.root / ".gitignore").write_text("target/\n")
        (self.root / "pom.xml").write_text("<project/>")
        for name in ("a", "b"):
            module = self.root / ("connectors/" + name)
            (module / "src/it/java").mkdir(parents=True)
            (module / "pom.xml").write_text("<project/>")
            (module / "src/it/java/SampleIT.java").write_text("// fixture\n")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.PIPE)

    def plan(self, modules=None):
        return connector.plan(self.root, modules or ["connectors/a"], [])

    def execution(self, plan, skipped=0, fail=0, exit_code=0):
        started = time.time_ns()
        folder = self.root / "connectors/a/target/failsafe-reports"
        folder.mkdir(parents=True, exist_ok=True)
        child = "<failure/>" if fail else "<skipped/>" if skipped else ""
        (folder / "TEST-SampleIT.xml").write_text('<testsuite tests="1" failures="%d" errors="0" skipped="%d"><testcase name="one">%s</testcase></testsuite>' % (fail, skipped, child))
        return {"plan_id": plan["plan_id"], "modules": [{"module": "connectors/a", "exit_code": exit_code, "started_ns": started, "finished_ns": time.time_ns()}]}

    def test_selects_only_requested_module_without_case_filters(self):
        plan = self.plan()
        self.assertEqual(["connectors/a"], plan["selected"])
        self.assertEqual(2, len(plan["commands"]))
        test = plan["commands"][1]
        self.assertNotIn("-am", test["argv"])
        self.assertIn("clean", test["argv"])
        self.assertIn("-DskipITs=false", test["argv"])
        self.assertFalse(any(a.startswith("-Dit.test") for a in test["argv"]))

    def test_missing_framework_kept_as_gap(self):
        (self.root / "connectors/b/src/it/java/SampleIT.java").unlink()
        plan = self.plan(["connectors/a", "connectors/b"])
        report = connector.report(plan, self.execution(plan))
        self.assertFalse(report["passed"])
        self.assertEqual("unsupported", report["modules"][1]["status"])

    def test_missing_module_not_silently_dropped(self):
        with self.assertRaises(ValueError):
            self.plan(["connectors/missing"])

    def test_path_escape_rejected(self):
        with self.assertRaises(ValueError):
            self.plan(["connectors/../a"])

    def test_missing_execution_kept(self):
        plan = self.plan(["connectors/a", "connectors/b"])
        report = connector.report(plan, self.execution(plan))
        self.assertFalse(report["passed"])
        self.assertEqual("not_executed", report["modules"][1]["status"])

    def test_failures_skips_and_exit_failure_not_passed(self):
        for skipped, fail, code in [(1, 0, 0), (0, 1, 0), (0, 0, 1)]:
            with self.subTest(skipped=skipped, fail=fail, code=code):
                plan = self.plan()
                self.assertFalse(connector.report(plan, self.execution(plan, skipped, fail, code))["passed"])

    def test_zero_cases_and_missing_reports_not_passed(self):
        plan = self.plan()
        run = self.execution(plan)
        file = self.root / "connectors/a/target/failsafe-reports/TEST-SampleIT.xml"
        file.write_text('<testsuite tests="0" failures="0" errors="0" skipped="0"/>')
        run["modules"][0]["finished_ns"] = time.time_ns()
        self.assertFalse(connector.report(plan, run)["passed"])
        file.unlink()
        self.assertFalse(connector.report(plan, run)["passed"])

    def test_stale_report_rejected(self):
        plan = self.plan()
        run = self.execution(plan)
        run["modules"][0]["started_ns"] = run["modules"][0]["finished_ns"]
        self.assertEqual("unknown", connector.report(plan, run)["modules"][0]["status"])

    def test_changed_code_rejects_prior_results(self):
        plan = self.plan()
        run = self.execution(plan)
        (self.root / "connectors/a/src/it/java/SampleIT.java").write_text("// changed\n")
        with self.assertRaisesRegex(ValueError, "代码已变化"):
            connector.report(plan, run)

    def test_edited_plan_rejected(self):
        plan = self.plan()
        run = self.execution(plan)
        plan["selected"] = []
        with self.assertRaisesRegex(ValueError, "清单已变化"):
            connector.report(plan, run)

    def test_valid_report_and_cli(self):
        plan = self.plan()
        run = self.execution(plan)
        self.assertTrue(connector.report(plan, run)["passed"])
        # 执行记录在仓库之外，避免影响工作区快照。
        with tempfile.TemporaryDirectory() as d:
            p, e = Path(d) / "plan.json", Path(d) / "run.json"
            p.write_text(json.dumps(plan))
            e.write_text(json.dumps(run))
            proc = subprocess.run([sys.executable, str(SCRIPT), "report", "--plan", str(p), "--execution", str(e)], capture_output=True, text=True)
            self.assertEqual(0, proc.returncode, proc.stderr)

    @unittest.skipUnless(os.environ.get("AO_CONNECTOR_MAVEN_TEST") == "1", "显式开启隔离 Maven 真实执行")
    def test_real_maven_runs_all_cases_only_in_selected_module(self):
        parent = '''<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion><groupId>ao.fixture</groupId><artifactId>parent</artifactId><version>1</version><packaging>pom</packaging><modules><module>connectors/a</module><module>connectors/b</module></modules><properties><maven.compiler.source>8</maven.compiler.source><maven.compiler.target>8</maven.compiler.target><skipITs>true</skipITs></properties><build><testSourceDirectory>src/it/java</testSourceDirectory><plugins><plugin><artifactId>maven-compiler-plugin</artifactId><version>3.8.1</version></plugin><plugin><artifactId>maven-failsafe-plugin</artifactId><version>3.5.6</version><configuration><skipITs>${skipITs}</skipITs></configuration></plugin></plugins></build><dependencies><dependency><groupId>junit</groupId><artifactId>junit</artifactId><version>4.13.2</version><scope>test</scope></dependency></dependencies></project>'''
        (self.root / "pom.xml").write_text(parent)
        for name in ("a", "b"):
            module = self.root / ("connectors/" + name)
            (module / "pom.xml").write_text('<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion><parent><groupId>ao.fixture</groupId><artifactId>parent</artifactId><version>1</version><relativePath>../../pom.xml</relativePath></parent><artifactId>%s</artifactId></project>' % name)
            (module / "src/it/java/SampleIT.java").write_text('import org.junit.Test; import static org.junit.Assert.*; public class SampleIT { @Test public void first() { assertTrue(%s); } @Test public void second() { assertTrue(true); } }' % ("true" if name == "a" else "false"))
        plan = self.plan()
        runs = []
        for command in plan["commands"]:
            start = time.time_ns()
            proc = subprocess.run(command["argv"], cwd=command["cwd"], capture_output=True, text=True, timeout=180)
            end = time.time_ns()
            self.assertEqual(0, proc.returncode, proc.stdout[-6000:] + proc.stderr[-1000:])
            if command["kind"] == "test":
                runs.append(dict(module=command["module"], exit_code=proc.returncode, started_ns=start, finished_ns=end))
        report = connector.report(plan, dict(plan_id=plan["plan_id"], modules=runs))
        self.assertTrue(report["passed"], report)
        self.assertEqual(2, report["modules"][0]["counts"]["tests"])
        self.assertFalse((self.root / "connectors/b/target").exists())


if __name__ == "__main__":
    unittest.main()

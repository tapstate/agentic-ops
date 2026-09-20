"""Maven 模块选择、跨仓 Jar 与真实测试结果核验。"""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/maven_tests.py"
SPEC = importlib.util.spec_from_file_location("connector_tests", SCRIPT)
connector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(connector)


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.temp_root = Path(self.tmp.name).resolve()
        self.root = self.temp_root / "repository"
        self.root.mkdir()
        self.runtime = self.temp_root / "station/runtime"
        self.runtime.mkdir(parents=True)
        self.context = {"station": str(self.temp_root / "station"), "station_id": "station-fixture",
                        "run_id": "run-fixture", "local_repository": str(self.runtime / "maven-local")}
        self.maven = self.root / "mvn"
        self.maven.write_text("#!/bin/sh\nexit 0\n")
        self.maven.chmod(0o700)
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
        return connector.plan(self.root, modules or ["connectors/a"], [], runtime=self.context, executable=self.maven)

    def execution(self, plan, skipped=0, fail=0, exit_code=0):
        started = time.time_ns()
        folder = self.root / "connectors/a/target/failsafe-reports"
        folder.mkdir(parents=True, exist_ok=True)
        child = "<failure/>" if fail else "<skipped/>" if skipped else ""
        (folder / "TEST-SampleIT.xml").write_text('<testsuite tests="1" failures="%d" errors="0" skipped="%d"><testcase name="one">%s</testcase></testsuite>' % (fail, skipped, child))
        commands = []
        for command in plan["commands"]:
            entry = {"kind": command["kind"], "exit_code": exit_code if command["kind"] == "test" else 0, "started_ns": started,
                     "finished_ns": time.time_ns(), "runtime_context": plan["runtime_context"]}
            if "module" in command:
                entry["module"] = command["module"]
            if command["kind"] == "toolchain":
                entry["observed_maven_version"] = "3.9.16"
            commands.append(entry)
        return {"plan_id": plan["plan_id"], "runtime_context": plan["runtime_context"], "commands": commands}

    def test_selects_only_requested_module_without_case_filters(self):
        plan = self.plan()
        self.assertEqual(["connectors/a"], plan["selected"])
        self.assertEqual(3, len(plan["commands"]))
        self.assertEqual(str(self.maven.resolve()), plan["maven_executable"])
        self.assertEqual(self.context, plan["runtime_context"])
        test = plan["commands"][-1]
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
        run = self.execution(plan)
        run["commands"].pop()
        with self.assertRaisesRegex(ValueError, "一一对应"):
            connector.report(plan, run)

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
        next(item for item in run["commands"] if item["kind"] == "test")["finished_ns"] = time.time_ns()
        self.assertFalse(connector.report(plan, run)["passed"])
        file.unlink()
        self.assertFalse(connector.report(plan, run)["passed"])

    def test_stale_report_rejected(self):
        plan = self.plan()
        run = self.execution(plan)
        item = next(item for item in run["commands"] if item["kind"] == "test")
        item["started_ns"] = item["finished_ns"]
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

    def test_surefire_module_report_keeps_coverage_boundary(self):
        module = self.root / "connectors/a"
        (module / "src/test").mkdir()
        (module / "src/it/java").rename(module / "src/test/java")
        plan = connector.plan(self.root, ["connectors/a"], [], "surefire", runtime=self.context, executable=self.maven)
        run = self.execution(plan)
        (module / "target/failsafe-reports").rename(module / "target/surefire-reports")
        result = connector.report(plan, run)
        self.assertTrue(result["passed"])
        self.assertEqual("surefire", result["framework"])
        self.assertIn("不自动证明集成覆盖", result["boundary"])
        self.assertIn("test", plan["commands"][-1]["argv"])

    def test_jar_pair_rejects_stale_cache_and_later_replacement(self):
        with tempfile.TemporaryDirectory() as d:
            built, loaded = Path(d) / "built.jar", Path(d) / "loaded.jar"
            built.write_bytes(b"new")
            loaded.write_bytes(b"old")
            with self.assertRaisesRegex(ValueError, "内容不同"):
                connector.plan(self.root, ["connectors/a"], [], jar_pairs=[(built, loaded)], runtime=self.context, executable=self.maven)
            loaded.write_bytes(b"new")
            plan = connector.plan(self.root, ["connectors/a"], [], jar_pairs=[(built, loaded)], runtime=self.context, executable=self.maven)
            run = self.execution(plan)
            self.assertTrue(connector.report(plan, run)["passed"])
            built.write_bytes(b"changed")
            loaded.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "Jar 已变化"):
                connector.report(plan, run)

    def test_dependency_source_change_invalidates_test(self):
        with tempfile.TemporaryDirectory() as d:
            dependency = Path(d).resolve()
            for args in [("init", "-q"), ("config", "user.name", "Fixture"), ("config", "user.email", "fixture@example.invalid")]:
                subprocess.run(["git", "-C", str(dependency), *args], check=True, capture_output=True)
            file = dependency / "source.java"
            file.write_text("old")
            subprocess.run(["git", "-C", str(dependency), "add", "."], check=True)
            subprocess.run(["git", "-C", str(dependency), "commit", "-qm", "fixture"], check=True)
            plan = connector.plan(self.root, ["connectors/a"], [], dependency_repos=[dependency], runtime=self.context, executable=self.maven)
            run = self.execution(plan)
            file.write_text("new")
            with self.assertRaisesRegex(ValueError, "依赖源码已变化"):
                connector.report(plan, run)

    def test_single_directory_and_root_module_supported(self):
        self.assertEqual(self.root, connector.module_path(self.root, "."))
        (self.root / "single").mkdir()
        (self.root / "single/pom.xml").write_text("<project/>")
        self.assertEqual(self.root / "single", connector.module_path(self.root, "single"))

    def test_old_transient_plan_rejected(self):
        plan = self.plan()
        plan["schema_version"] = 2
        unsigned = dict(plan)
        unsigned.pop("plan_id")
        plan["plan_id"] = connector.digest(unsigned)
        with self.assertRaisesRegex(ValueError, "清单已变化"):
            connector.report(plan, {"plan_id": plan["plan_id"], "runtime_context": plan["runtime_context"], "commands": []})

    def test_runtime_context_and_command_records_are_bound(self):
        plan = self.plan()
        run = self.execution(plan)
        run["commands"][1]["runtime_context"] = dict(run["runtime_context"], run_id="run-other")
        with self.assertRaisesRegex(ValueError, "runtime"):
            connector.report(plan, run)
        self.assertTrue(all(command["argv"].count("-Dmaven.repo.local=" + plan["runtime_context"]["local_repository"]) == 1
                            for command in plan["commands"]))

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
        plan = connector.plan(self.root, ["connectors/a"], [], runtime=self.context, executable=shutil.which("mvn"))
        runs = []
        for command in plan["commands"]:
            start = time.time_ns()
            proc = subprocess.run(command["argv"], cwd=command["cwd"], capture_output=True, text=True, timeout=180)
            end = time.time_ns()
            self.assertEqual(0, proc.returncode, proc.stdout[-6000:] + proc.stderr[-1000:])
            entry = dict(kind=command["kind"], exit_code=proc.returncode, started_ns=start, finished_ns=end,
                         runtime_context=plan["runtime_context"])
            if command["kind"] == "toolchain":
                match = re.search(r"Apache Maven (\S+)", proc.stdout + proc.stderr)
                entry["observed_maven_version"] = match.group(1) if match else ""
            if command["kind"] == "test":
                entry["module"] = command["module"]
            runs.append(entry)
        report = connector.report(plan, dict(plan_id=plan["plan_id"], runtime_context=plan["runtime_context"], commands=runs))
        self.assertTrue(report["passed"], report)
        self.assertEqual(2, report["modules"][0]["counts"]["tests"])
        self.assertFalse((self.root / "connectors/b/target").exists())

    @unittest.skipUnless(os.environ.get("AO_JAR_MAVEN_TEST") == "1", "显式开启跨仓 Jar 真实执行")
    def test_real_cross_repository_old_jar_fails_new_jar_passes(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d).resolve()
            producer, cache = base / "producer", Path(self.context["local_repository"])
            producer.mkdir()
            for args in [("init", "-q"), ("config", "user.name", "Fixture"), ("config", "user.email", "fixture@example.invalid")]:
                subprocess.run(["git", "-C", str(producer), *args], check=True, capture_output=True)
            (producer / ".gitignore").write_text("target/\n")
            header = '<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion><groupId>ao.jar.fixture</groupId><version>1</version>'
            compiler = '<properties><maven.compiler.source>8</maven.compiler.source><maven.compiler.target>8</maven.compiler.target></properties>'
            (producer / "pom.xml").write_text(header + '<artifactId>library</artifactId>' + compiler + '</project>')
            java = producer / "src/main/java/sample/Library.java"
            java.parent.mkdir(parents=True)
            java.write_text('package sample; public class Library { public static String value() { return "old"; } }')
            subprocess.run(["git", "-C", str(producer), "add", "."], check=True)
            subprocess.run(["git", "-C", str(producer), "commit", "-qm", "old library"], check=True)
            (self.root / "pom.xml").write_text(header + '<artifactId>consumer-root</artifactId><packaging>pom</packaging><modules><module>connectors/a</module></modules>' + compiler + '</project>')
            module = self.root / "connectors/a"
            (module / "pom.xml").write_text(header + '<artifactId>consumer</artifactId>' + compiler + '<dependencies><dependency><groupId>ao.jar.fixture</groupId><artifactId>library</artifactId><version>1</version></dependency><dependency><groupId>junit</groupId><artifactId>junit</artifactId><version>4.13.2</version><scope>test</scope></dependency></dependencies><build><testSourceDirectory>src/it/java</testSourceDirectory><plugins><plugin><artifactId>maven-failsafe-plugin</artifactId><version>3.5.6</version></plugin></plugins></build></project>')
            built = producer / "target/library-1.jar"
            consumed = cache / "ao/jar/fixture/library/1/library-1.jar"
            expected_path = json.dumps(str(consumed))
            (module / "src/it/java/SampleIT.java").write_text('import org.junit.Test; import static org.junit.Assert.*; import sample.Library; public class SampleIT { @Test public void changedBehavior() { assertEquals("new", Library.value()); } @Test public void actualJarLocation() throws Exception { assertEquals(new java.io.File(%s).getCanonicalPath(), new java.io.File(Library.class.getProtectionDomain().getCodeSource().getLocation().toURI()).getCanonicalPath()); } }' % expected_path)

            def execute(argv, cwd, success=True):
                start = time.time_ns()
                proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=240)
                end = time.time_ns()
                if success:
                    self.assertEqual(0, proc.returncode, proc.stdout[-5000:] + proc.stderr[-1000:])
                return proc, start, end

            def execute_plan(plan, test_success):
                runs = []
                for command in plan["commands"]:
                    proc, start, end = execute(command["argv"], command["cwd"], success=command["kind"] != "test" or test_success)
                    entry = {"kind": command["kind"], "exit_code": proc.returncode, "started_ns": start,
                             "finished_ns": end, "runtime_context": plan["runtime_context"]}
                    if command["kind"] == "toolchain":
                        match = re.search(r"Apache Maven (\S+)", proc.stdout + proc.stderr)
                        entry["observed_maven_version"] = match.group(1) if match else ""
                    if command["kind"] == "test":
                        entry["module"] = command["module"]
                    runs.append(entry)
                return {"plan_id": plan["plan_id"], "runtime_context": plan["runtime_context"], "commands": runs}

            maven = shutil.which("mvn")
            execute([maven, "-Dmaven.repo.local=" + str(cache), "-B", "clean", "install", "-DskipTests=true"], producer)
            old_plan = connector.plan(self.root, ["connectors/a"], [], dependency_repos=[producer], jar_pairs=[(built, consumed)], runtime=self.context, executable=maven)
            old_execution = execute_plan(old_plan, test_success=False)
            old_test = next(item for item in old_execution["commands"] if item["kind"] == "test")
            self.assertNotEqual(0, old_test["exit_code"])
            old_report = connector.report(old_plan, old_execution)
            self.assertEqual("failed", old_report["modules"][0]["status"])
            self.assertEqual(1, old_report["modules"][0]["counts"]["failures"])

            java.write_text('package sample; public class Library { public static String value() { return "new"; } }')
            execute([maven, "-Dmaven.repo.local=" + str(cache), "-B", "clean", "install", "-DskipTests=true"], producer)
            new_plan = connector.plan(self.root, ["connectors/a"], [], dependency_repos=[producer], jar_pairs=[(built, consumed)], runtime=self.context, executable=maven)
            new_report = connector.report(new_plan, execute_plan(new_plan, test_success=True))
            self.assertTrue(new_report["passed"], new_report)
            self.assertEqual(2, new_report["modules"][0]["counts"]["tests"])
            self.assertNotEqual(old_plan["artifacts"][0]["sha256"], new_plan["artifacts"][0]["sha256"])


if __name__ == "__main__":
    unittest.main()

"""有效模型闭包不依赖本机 Maven、网络或业务工作区。"""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/java_impact.py"
SPEC = importlib.util.spec_from_file_location("java_impact", SCRIPT)
impact = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(impact)


class JavaImpactTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.doc = {"schema_version": 1, "modules": [], "changed_modules": ["a"]}

    def model(self, name, deps=(), version="1", extra=""):
        dependencies = "".join(
            "<dependency><groupId>g</groupId><artifactId>%s</artifactId>"
            "<version>%s</version><scope>%s</scope></dependency>" % dep for dep in deps)
        xml = ('<project xmlns="http://maven.apache.org/POM/4.0.0">'
               '<groupId>g</groupId><artifactId>%s</artifactId><version>%s</version>'
               '<dependencies>%s</dependencies>%s</project>') % (name, version, dependencies, extra)
        path = self.root / (name + ".xml")
        path.write_text(xml)
        entry = {"id": name, "repository": "owner/" + name,
                 "source_revision": "a" * 40, "pom": "pom.xml", "profiles": ["it"],
                 "effective_pom": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        self.doc["modules"].append(entry)
        return entry

    def run_analysis(self):
        return impact.analyze(self.doc, self.root)

    def test_transitive_cross_repository_and_separate_build_prerequisites(self):
        self.model("a", [("base", "1", "compile")])
        self.model("b", [("a", "1", "provided")])
        self.model("c", [("b", "1", "test")])
        self.model("base")
        self.model("unrelated")
        result = self.run_analysis()
        self.assertEqual(["a", "b", "c"], [x["id"] for x in result["test_candidates"]])
        self.assertEqual(["a", "b", "c"], result["test_candidates"][2]["reason_path"])
        self.assertEqual(["base"], result["build_prerequisites"])
        self.assertEqual(["it"], result["sources"][0]["profiles"])

    def test_version_difference_keeps_consumer_and_reports_gap(self):
        self.model("a", version="2")
        self.model("b", [("a", "1", "compile")])
        result = self.run_analysis()
        self.assertEqual(2, len(result["test_candidates"]))
        self.assertTrue(result["relations"][0]["version_mismatch"])
        self.assertIn("版本差异", result["unresolved"][0])

    def test_aggregation_does_not_create_consumption_edge(self):
        self.model("a", extra="<modules><module>b</module></modules>")
        self.model("b")
        self.assertEqual(["a"], [x["id"] for x in self.run_analysis()["test_candidates"]])

    def test_parent_change_propagates_and_cycles_terminate(self):
        self.model("a", [("b", "1", "compile")])
        self.model("b", extra="<parent><groupId>g</groupId><artifactId>a</artifactId><version>1</version></parent>")
        self.assertEqual(2, len(self.run_analysis()["test_candidates"]))

    def test_model_changes_rejected(self):
        entry = self.model("a")
        (self.root / entry["effective_pom"]).write_text("broken")
        with self.assertRaisesRegex(ValueError, "内容已变化"):
            self.run_analysis()

    def test_unresolved_maven_property_rejected(self):
        self.model("a", [("b", "${revision}", "compile")])
        with self.assertRaisesRegex(ValueError, "未解析"):
            self.run_analysis()

    def test_unknown_change_rejected(self):
        self.model("a")
        self.doc["changed_modules"] = ["missing"]
        with self.assertRaisesRegex(ValueError, "不在模块清单"):
            self.run_analysis()

    def test_cli_preserves_incomplete_inventory_warning(self):
        self.model("a")
        self.doc["unresolved"] = ["仓库 c 尚无模型"]
        path = self.root / "input.json"
        path.write_text(json.dumps(self.doc))
        proc = subprocess.run([sys.executable, str(SCRIPT), "--input", str(path)], capture_output=True, text=True)
        self.assertEqual(0, proc.returncode, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(["仓库 c 尚无模型"], result["unresolved"])
        self.assertEqual("supplied_effective_models", result["scope"])
        self.assertTrue(result["limitations"])


if __name__ == "__main__":
    unittest.main()

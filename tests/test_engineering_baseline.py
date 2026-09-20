#!/usr/bin/env python3
"""AO-158 完整工程基线与任务分支引用合同，不改写任何真实工位。"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from workflow import engineering_baseline as baseline
from workflow.project_rules import canonical_repository_endpoint
from projects.tapdata.scripts import engineering_baseline as tapdata
from test_contracts import assert_schema


class BaselineTest(unittest.TestCase):
    def setUp(self):
        self.profile = {"id": "test", "revision": 1, "repositories": ["org/a", "org/b"],
                        "optional_repositories": ["org/test"]}
        self.catalog = {name: {"origin": "git@example.org:%s.git" % name}
                        for name in ("org/a", "org/b", "org/test")}
        self.resolutions = {name: {
            "verification": "verified", "ref_kind": "branch", "ref_name": "develop",
            "commit_sha": "a" * 40, "resolution_source": "explicit_branch", "rule_version": "v1",
        } for name in self.profile["repositories"]}

    def freeze(self):
        return baseline.freeze(self.profile, self.catalog, self.resolutions, {"version": "develop"})

    def test_append_chain_preserves_old_entries_and_rejects_replacement(self):
        before = self.freeze()
        full = baseline.freeze(self.profile, self.catalog, dict(self.resolutions, **{"org/test": self.resolutions["org/a"]}),
                               {"version": "develop"}, optional=["org/test"])
        added = {"org/test": full["repositories"]["org/test"]}
        result = baseline.append_repositories(before, added, "fixture:decision")
        baseline.validate(result)
        assert_schema(self, json.loads((ROOT / "contracts/engineering-baseline.schema.json").read_text()), result)
        self.assertEqual(before["repositories"]["org/a"], result["repositories"]["org/a"])
        self.assertEqual(2, result["revision"])
        with self.assertRaisesRegex(ValueError, "不能替换"):
            baseline.append_repositories(result, added, "fixture:another")
        changed = copy.deepcopy(result)
        changed["repositories"]["org/a"]["commit_sha"] = "b" * 40
        changed["digest"] = baseline.digest({k:v for k,v in changed.items() if k != "digest"})
        with self.assertRaisesRegex(ValueError, "摘要链"):
            baseline.validate(changed)

    def test_complete_baseline_is_stable_and_detached_from_inputs(self):
        result = self.freeze()
        schema = json.loads((ROOT / "contracts" / "engineering-baseline.schema.json").read_text())
        assert_schema(self, schema, result)
        self.assertEqual(result, baseline.validate(result))
        self.assertEqual("frozen", result["status"])
        self.assertEqual("source/org/b", result["repositories"]["org/b"]["path"])
        self.profile["repositories"].reverse()
        # Profile 内容变化（包括顺序）改变身份；相同输入仅 JSON 键序不影响摘要。
        self.assertNotEqual(result["digest"], self.freeze()["digest"])
        self.resolutions["org/a"]["commit_sha"] = "b" * 40
        self.assertEqual("a" * 40, result["repositories"]["org/a"]["commit_sha"])
        self.assertEqual(baseline.digest({"a": 1, "b": 2}), baseline.digest({"b": 2, "a": 1}))

    def test_missing_extra_or_duplicate_repository_rejected(self):
        for key in ("org/a", "org/extra"):
            with self.subTest(key=key):
                rows = copy.deepcopy(self.resolutions)
                if key in rows:
                    rows.pop(key)
                else:
                    rows[key] = dict(rows["org/a"])
                with self.assertRaises(ValueError):
                    baseline.freeze(self.profile, self.catalog, rows, {"version": "develop"})
        self.profile["repositories"].append("org/a")
        with self.assertRaises(ValueError):
            self.freeze()

    def test_optional_repositories_must_be_explicit_and_complete(self):
        self.assertEqual(["org/a", "org/b"], baseline.selected_repositories(self.profile, self.catalog))
        self.resolutions["org/test"] = dict(self.resolutions["org/a"])
        with self.assertRaises(ValueError):
            self.freeze()
        result = baseline.freeze(self.profile, self.catalog, self.resolutions,
                                 {"version": "develop"}, ["org/test"])
        self.assertEqual(3, len(result["repositories"]))
        for optional in (["org/unknown"], ["org/test", "org/test"], "org/test"):
            with self.subTest(optional=optional), self.assertRaises(ValueError):
                baseline.selected_repositories(self.profile, self.catalog, optional)

    def test_profile_identity_and_catalog_are_required(self):
        for revision in (True, 0, "1", None):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                baseline.selected_repositories(dict(self.profile, revision=revision), self.catalog)
        self.catalog.pop("org/b")
        with self.assertRaises(ValueError):
            self.freeze()

    def test_uncertain_ref_and_incomplete_sha_cannot_freeze(self):
        cases = [("verification", x) for x in (None, "cached", "stale", "refresh_failed")]
        cases += [("resolution_source", x) for x in
                  ("current", "current_branch", "unchanged", "keep_current", "fallback", "display_fallback", "unresolved")]
        cases += [("commit_sha", x) for x in (None, "abc1234", "g" * 40)]
        cases += [("ref_kind", "unknown"), ("rule_version", "")]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                rows = copy.deepcopy(self.resolutions)
                rows["org/a"][field] = value
                with self.assertRaises(ValueError):
                    baseline.freeze(self.profile, self.catalog, rows, {"version": "develop"})

    def test_ref_names_reject_revision_expressions_and_paths(self):
        for value in ("--help", "HEAD~1", "a..b", "refs/heads/main", "origin/main", "@",
                      "a/.b", "a.lock", "a//b", "a b", "a?b", "a\x7fb", "a\\b"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                baseline.ref_name(value)
        self.assertEqual("codex/AO-158", baseline.ref_name("codex/AO-158"))
        for value in ("../a", "org/../b", "/org/a", "org/a\\b", "org/-a"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                baseline.repository_id(value)

    def test_origin_transports_preserve_host_identity(self):
        for origin in ("git@example.org:org/repo.git", "https://example.org/org/repo.git",
                       "ssh://git@example.org/org/repo.git", "git://example.org/org/repo.git"):
            with self.subTest(origin=origin):
                self.assertEqual("example.org/org/repo", canonical_repository_endpoint(origin))
        self.assertNotEqual(canonical_repository_endpoint("https://other.org/org/repo.git"),
                            canonical_repository_endpoint("git@example.org:org/repo.git"))

    def test_explicit_commit_and_tag_supported(self):
        self.resolutions["org/a"].update(ref_kind="commit", ref_name="a" * 40)
        self.resolutions["org/b"].update(ref_kind="tag", ref_name="v1.0")
        baseline.validate(self.freeze())
        self.resolutions["org/a"]["ref_name"] = "b" * 40
        with self.assertRaises(ValueError):
            self.freeze()

    def test_baseline_mutation_and_invalid_structure_detected(self):
        for change in (lambda d: d["repositories"]["org/a"].update(commit_sha="b" * 40),
                       lambda d: d.update(status="resolving")):
            result = self.freeze()
            change(result)
            with self.assertRaises(ValueError):
                baseline.validate(result)
        for field, value in (("path", "../escape"), ("ref_kind", "unknown"), ("extra", 1)):
            result = self.freeze()
            result["repositories"]["org/a"][field] = value
            result["digest"] = baseline.digest({k: v for k, v in result.items() if k != "digest"})
            with self.subTest(field=field), self.assertRaises(ValueError):
                baseline.validate(result)

    def test_task_binding_has_one_baseline_and_mutable_observation(self):
        value = self.freeze()
        scope = ["src/"]
        binding = baseline.task_repository(value, "org/a", "codex/task", "develop", scope, "unit tests")
        scope.append("other/")
        self.assertEqual(["src/"], binding["approved_scope"])
        before = value["digest"]
        binding["observation"] = {"head": "b" * 40}
        binding["deliveries"].append({"pr": 1})
        baseline.validate_task_repositories(value, {"org/a": binding})
        self.assertEqual(before, value["digest"])
        binding["base_sha"] = "b" * 40
        with self.assertRaises(ValueError):
            baseline.validate_task_repositories(value, {"org/a": binding})

    def test_task_binding_rejects_foreign_baseline_or_target_branch(self):
        value = self.freeze()
        for repo, work, target in (("org/test", "codex/task", "develop"),
                                   ("org/a", "develop", "develop")):
            with self.subTest(repo=repo, work=work), self.assertRaises(ValueError):
                baseline.task_repository(value, repo, work, target, ["src/"], "tests")
        binding = baseline.task_repository(value, "org/a", "codex/task", "develop", ["src/"], "tests")
        self.resolutions["org/a"]["commit_sha"] = "b" * 40
        with self.assertRaises(ValueError):
            baseline.validate_task_repositories(self.freeze(), {"org/a": binding})

    def test_local_baseline_requires_all_repositories_and_returns_copy(self):
        value = self.freeze()
        with mock.patch.object(baseline, "verify_local_repository") as verify:
            result = baseline.verify_local_baseline("/station", value)
        self.assertEqual(2, verify.call_count)
        self.assertEqual(value, result)
        self.assertIsNot(value, result)
        with mock.patch.object(baseline, "verify_local_repository", side_effect=[{}, ValueError("missing")]):
            with self.assertRaises(ValueError):
                baseline.verify_local_baseline("/station", value)
        self.assertEqual(value, baseline.validate(value))


class TapDataBaselineTest(unittest.TestCase):
    def setUp(self):
        root = ROOT / "projects" / "tapdata"
        self.config = json.loads((root / "version-branch-alignments.json").read_text())
        self.catalog = json.loads((root / "repositories.json").read_text())["repositories"]
        self.profile = json.loads((root / "engineering-profiles.json").read_text())["profiles"]["full-application"]
        self.observations = {name: {
            "selection": "required", "local": {"status": "available"},
            "refs": {"verification": "verified"}, "_path": None,
            "_refs": {ref: "a" * 40 for ref in ("main", "develop", "release-v5.5.0", "codex/task")},
        } for name in self.catalog}

    def resolve(self, version="develop", **kwargs):
        return tapdata.resolve(version, self.config, self.catalog, self.profile, self.observations, **kwargs)

    def test_product_override_cannot_change_dependency_version(self):
        product = self.config["derivation"]["product_repository"]
        for version, branch in (("develop", "main"), ("release-v4.19.0", "develop")):
            with self.subTest(version=version), mock.patch.object(tapdata, "build_plan") as build:
                with self.assertRaisesRegex(ValueError, "主仓显式分支"):
                    self.resolve(version, explicit_branches={product: branch})
                build.assert_not_called()
        result = self.resolve("develop", explicit_branches={product: "develop"})
        self.assertEqual("develop", result["repositories"][product]["ref_name"])

    def test_full_application_consumes_existing_rules(self):
        value = self.resolve()
        self.assertEqual(9, len(value["repositories"]))
        self.assertEqual("main", value["repositories"]["tapdata/tapdata-license"]["ref_name"])
        self.assertEqual("release-v5.5.0", value["repositories"]["tapdata/hazelcast"]["ref_name"])
        self.assertNotIn("tapdata/docs", value["repositories"])
        baseline.validate(value)

    def test_current_matrix_and_cached_refs_rejected(self):
        with self.assertRaises(ValueError):
            self.resolve("current")
        self.observations["tapdata/tapdata-web"]["refs"]["verification"] = "cached"
        with self.assertRaises(ValueError):
            self.resolve()

    def test_optional_display_fallback_requires_explicit_branch(self):
        test = "tapdata/t-layer3-test"
        # main 不存在时现役解析器展示 fallback=develop；工位不能默认沿用。
        self.observations[test]["_refs"].pop("main")
        with self.assertRaises(ValueError):
            self.resolve("main", optional=[test])
        result = self.resolve("main", optional=[test], explicit_branches={test: "develop"})
        self.assertEqual("explicit_branch", result["repositories"][test]["resolution_source"])
        with self.assertRaises(ValueError):
            self.resolve("main", optional=[test], explicit_branches={test: "missing"})

    def test_feature_rules_reused_without_fixed_fallback(self):
        result = self.resolve("codex/task")
        self.assertTrue(all(row["ref_name"] == "codex/task" for row in result["repositories"].values()))
        self.observations["tapdata/hazelcast"]["_refs"].pop("codex/task")
        with self.assertRaises(ValueError):
            self.resolve("codex/task")


class LocalRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ao-158-baseline-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / "source" / "org" / "repo"
        self.path.mkdir(parents=True)
        self.git("init", "-b", "main")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.org",
                 "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "fixture")
        self.sha = self.git("rev-parse", "HEAD")
        self.git("remote", "add", "origin", "https://example.org/org/repo.git")
        self.git("update-ref", "refs/remotes/origin/main", self.sha)

    def git(self, *args):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        return subprocess.run(["git", "-C", str(self.path), *args], env=environment,
                              check=True, capture_output=True, text=True).stdout.strip()

    def verify(self, **kwargs):
        return baseline.verify_local_repository(self.root, "org/repo", "git@example.org:org/repo.git",
                                                kwargs.get("kind", "branch"), kwargs.get("ref", "main"),
                                                kwargs.get("sha", self.sha))

    def test_read_only_identity_and_objects(self):
        before = self.git("status", "--porcelain")
        with mock.patch.dict(os.environ, {"GIT_DIR": "/does-not-exist", "GIT_OBJECT_DIRECTORY": "/wrong"}):
            self.assertEqual(self.sha, self.verify()["commit_sha"])
        self.assertEqual(before, self.git("status", "--porcelain"))
        self.assertFalse((self.root / ".agenticops").exists())
        self.assertEqual("main", self.git("branch", "--show-current"))

    def test_wrong_origin_missing_object_and_moved_ref_rejected(self):
        with self.assertRaises(ValueError):
            self.verify(sha="b" * 40)
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.org",
                 "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "second")
        with self.assertRaises(ValueError):
            self.verify(sha=self.git("rev-parse", "HEAD"))
        self.git("remote", "set-url", "origin", "https://other.example.org/org/repo.git")
        with self.assertRaises(ValueError):
            self.verify()

    def test_annotated_tag_is_peeled_and_tree_is_not_commit(self):
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.org",
                 "-c", "tag.gpgsign=false", "tag", "-a", "v1", "-m", "fixture")
        self.verify(kind="tag", ref="v1")
        tree = self.git("rev-parse", "HEAD^{tree}")
        with self.assertRaises(ValueError):
            self.verify(kind="commit", ref=tree, sha=tree)

    def test_alternates_symlink_and_linked_worktree_rejected(self):
        alternate = self.path / ".git" / "objects" / "info" / "alternates"
        alternate.write_text(str(self.root / "unknown") + "\n")
        with self.assertRaises(ValueError):
            self.verify()
        alternate.unlink()
        original = self.path.with_name("original")
        self.path.rename(original)
        self.path.symlink_to(original, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.verify()
        self.path.unlink()
        subprocess.run(["git", "-C", str(original), "worktree", "add", "--detach", str(self.path)],
                       check=True, capture_output=True)
        with self.assertRaises(ValueError):
            self.verify()


if __name__ == "__main__":
    unittest.main()

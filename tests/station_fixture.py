"""测试专用单工位状态夹具；不提供产品迁移入口。"""
import json
from pathlib import Path
from workflow import engineering_baseline, task_store


def save_task(base, task):
    initialize_station(base)
    (task_store.state_path(base) / "evidence").mkdir(parents=True, exist_ok=True)
    repositories = task.get("repositories", [])
    if any((row.get("worktree") or {}).get("status") == "prepared" for row in repositories):
        task["source_prepared"] = True
    if repositories:
        catalog = {r["repository"]: {"origin": r.get("origin") or "https://" + r.get("authorized_endpoint", "github.com/" + r["repository"])} for r in repositories}
        profile = {"id": "test-fixture", "revision": 1,
                   "repositories": list(catalog), "optional_repositories": []}
        resolutions = {r["repository"]: {"verification": "verified", "ref_kind": "branch",
                        "ref_name": r.get("base_branch") or "develop", "commit_sha": r.get("base_sha") or "a" * 40,
                        "resolution_source": "fixture", "rule_version": "fixture-1"} for r in repositories}
        frozen = engineering_baseline.freeze(profile, catalog, resolutions, {"fixture": True})
        task["engineering_baseline"] = frozen
        bindings = {}
        for repository in repositories:
            name = repository["repository"]
            scope = repository.get("approved_scope") or "fixture scope"
            binding = engineering_baseline.task_repository(frozen, name,
                repository.get("work_branch") or "fix/fixture", repository.get("base_branch") or "develop",
                scope if isinstance(scope, list) else [scope], repository.get("verification_method") or "fixture verification")
            binding["observation"] = {key: value for key, value in repository.items()
                                      if key in ("worktree", "pull_request", "ci", "catalog_digest")}
            bindings[name] = binding
        task["task_repositories"] = bindings
    else:
        task.setdefault("engineering_baseline", {"status": "resolving"})
        task.setdefault("task_repositories", {})
    task.setdefault("outcome", "in_progress")
    task.setdefault("archive_ref", None)
    task.setdefault("terminal_proof", None)
    current = task_store.initialize_current(base)
    previous = current["current"]
    if previous and (previous["issue_key"], previous["run_id"]) != (task["issue_key"], task["run_id"]):
        current = task_store.compare_and_set(base, current["revision"], None)
    task["_revision"] = current["revision"]
    task_store.write_task(base, task)
    normalized = task_store.read_task(base, task["issue_key"])
    task["repositories"] = normalized["repositories"]


def initialize_station(base):
    state = task_store.state_path(base)
    binding_path = state / "station.json"
    binding = json.loads(binding_path.read_text())
    binding["schema_version"] = 4
    binding.setdefault("station_id", "a" * 32)
    binding["agents"] = binding.get("agents") or ["codex"]
    binding["source_pool"] = binding.get("source_pool") or str(Path(base) / "pool")
    binding.pop("repository_pool", None)
    task_store._write_json_atomic(binding_path, binding)
    task_store._write_json_atomic(state / "init.json", {"station_state_epoch": 10})
    manifest = Path(binding["product_root"]) / "contracts/station-state-compatibility.json"
    if not manifest.exists():
        original = Path(__file__).resolve().parents[1] / "contracts/station-state-compatibility.json"
        task_store._write_json_atomic(manifest, json.loads(original.read_text()))

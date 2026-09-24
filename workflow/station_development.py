"""开发分支归位与已保全受管基线引用回收。"""
from workflow import engineering_baseline as baseline, station_source as source


def require_fast_forward(repository, before, target):
    if before and source.git(repository, "merge-base", "--is-ancestor", before, target, check=False).returncode:
        raise ValueError("开发分支含独有提交或分叉，不能覆盖")


def plan(repository, state, branch):
    branch = baseline.ref_name(branch)
    if state["neutral"].get("ref", branch) != branch:
        raise ValueError("项目开发分支与已确认归位引用不一致")
    target = state["neutral"]["sha"]
    ref = "refs/heads/" + branch
    require_fast_forward(repository, state["refs"].get(ref), target)
    for block in source.git(repository, "worktree", "list", "--porcelain").stdout.split("\n\n"):
        if "branch " + ref in block.splitlines() and "worktree " + str(repository) not in block.splitlines():
            raise ValueError("开发分支在其它工作树检出")
    obsolete = {}
    for name, sha in state["refs"].items():
        if not name.startswith("refs/heads/agenticops/baseline/"):
            continue
        # 只回收命名与真实提交一致且成果仍可到达的受管引用。
        if not name.endswith("-" + sha[:12]):
            raise ValueError("受管基线引用与命名提交不一致")
        if all(source.git(repository, "merge-base", "--is-ancestor", sha, head, check=False).returncode
               for head in (target, state["preserved_head"])):
            raise ValueError("受管基线含未保全提交，不能自动回收")
        for block in source.git(repository, "worktree", "list", "--porcelain").stdout.split("\n\n"):
            if "branch " + name in block.splitlines() and "worktree " + str(repository) not in block.splitlines():
                raise ValueError("受管基线在其它工作树检出")
        obsolete[name] = sha
    state["checkout_branch"] = branch
    state["baseline_refs"] = obsolete


def checkout(repository, entry):
    branch = entry["checkout_branch"]
    target = entry["neutral"]["sha"]
    ref = "refs/heads/" + branch
    current = source.git(repository, "rev-parse", "--verify", ref, check=False)
    before = current.stdout.strip() if current.returncode == 0 else None
    original = entry["refs"].get(ref)
    if before not in (original, target):
        raise ValueError("开发分支在确认后变化")
    require_fast_forward(repository, before, target)
    if before is None:
        source.git(repository, "branch", branch, target)
    source.git(repository, "checkout", branch)
    if before is not None and before != target:
        source.git(repository, "merge", "--ff-only", target)
    for name, sha in entry["baseline_refs"].items():
        actual = source.git(repository, "rev-parse", "--verify", name, check=False)
        if actual.returncode == 0:
            if actual.stdout.strip() != sha:
                raise ValueError("受管基线在确认后变化")
            if any("branch " + name in block.splitlines() for block in
                   source.git(repository, "worktree", "list", "--porcelain").stdout.split("\n\n")):
                raise ValueError("受管基线仍被工作树检出，拒绝删除")
            source.git(repository, "update-ref", "-d", name, sha)


def expected_refs(entry, current, expected):
    ref = "refs/heads/" + entry["checkout_branch"]
    if current.get(ref) == entry["neutral"]["sha"]:
        expected[ref] = entry["neutral"]["sha"]
    for name in entry["baseline_refs"]:
        if name not in current:
            expected.pop(name, None)
    return expected

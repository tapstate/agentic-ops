#!/usr/bin/env python3
"""只读核对工作分支是否包含指定来源提交；不 fetch、Merge 或改写基线。"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workflow import quality
from workflow.git_environment import git_environment


def git(root, *args):
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30, env=git_environment(read_only=True))
    if proc.returncode:
        raise ValueError("Git 核对失败：%s" % (proc.stderr.strip() or " ".join(args)))
    return proc.stdout.strip()


def ancestor(root, before, after):
    result = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", before, after],
                            capture_output=True, text=True, timeout=30, env=git_environment(read_only=True))
    if result.returncode not in (0, 1):
        raise ValueError("无法核对提交祖先关系")
    return result.returncode == 0


def verify(root, work_branch, base_revision, source_revision):
    root = Path(root).resolve()
    if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise ValueError("必须指定任务 Git 根目录")
    for revision in (base_revision, source_revision):
        if not quality.exact_commit(revision):
            raise ValueError("基线与来源必须使用完整提交 SHA")
        git(root, "cat-file", "-e", revision + "^{commit}")
    branch = git(root, "branch", "--show-current")
    if not branch or branch != work_branch:
        raise ValueError("当前分支不是已登记工作分支")
    if git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("存在未提交修改或冲突；先保留并完成处理")
    head = git(root, "rev-parse", "HEAD")
    if not ancestor(root, base_revision, head):
        raise ValueError("当前分支不包含冻结任务基线，不能猜测替换")
    if not ancestor(root, base_revision, source_revision):
        raise ValueError("来源不包含冻结基线，需研发决定历史变化的处理")
    if not ancestor(root, source_revision, head):
        raise ValueError("工作分支尚未包含本次核对的来源提交")
    return {"base_revision": base_revision, "source_revision": source_revision,
            "task_revision": head, "work_branch": branch, "contains_source": True,
            "boundary": "仅证明包含给定来源提交；最新远端来源和 Merge 授权须原生回读核对。"}


def impact(root, work_branch, base_revision, source_revision, before_merge_revision):
    result = verify(root, work_branch, base_revision, source_revision)
    if not quality.exact_commit(before_merge_revision):
        raise ValueError("同步前任务版本必须为完整提交 SHA")
    if not ancestor(root, base_revision, before_merge_revision) or not ancestor(
            root, before_merge_revision, result["task_revision"]):
        raise ValueError("同步前任务成果未保留，需研发决定后续处理")
    comparisons = {}
    for name, start, end in (("original_task", base_revision, before_merge_revision),
                             ("incoming_source", base_revision, source_revision),
                             ("final_task", source_revision, result["task_revision"])):
        paths = git(root, "diff", "--name-only", "-z", start, end, "--")
        patch = git(root, "diff", "--binary", "--no-ext-diff", "--no-textconv", start, end, "--")
        comparisons[name] = {"from": start, "to": end,
                             "paths": [p for p in paths.split("\0") if p],
                             "diff_sha256": hashlib.sha256(patch.encode()).hexdigest()}
    result.update(before_merge_revision=before_merge_revision, comparisons=comparisons,
                  analysis_required=True,
                  boundary="差异只供 Agent 分析；无文本冲突不证明无行为影响。测试证据必须绑定最终源码与用例。")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--work-branch", required=True)
    parser.add_argument("--base-revision", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--before-merge-revision")
    args = parser.parse_args()
    try:
        arguments = (args.repo, args.work_branch, args.base_revision, args.source_revision)
        result = impact(*arguments, args.before_merge_revision) if args.before_merge_revision else verify(*arguments)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print("错误：%s" % error, file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())

"""任务仓库合并事实及当前候选选择。"""
from datetime import datetime
import re


FACT_FIELDS = ("repository", "target_branch", "candidate_head", "pr_head", "merged_at", "merge_commit")


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", value) is not None


def validate(binding, row):
    name = binding["repository_id"]
    if (not isinstance(row, dict) or row.get("repository") != name
            or row.get("target_branch") != binding["target_branch"]
            or re.fullmatch(r"[1-9][0-9]*", str(row.get("pr", ""))) is None
            or not valid_sha(row.get("candidate_head")) or row.get("candidate_head") != row.get("pr_head")
            or not valid_sha(row.get("merge_commit"))
            or not isinstance(row.get("readback_ref"), str) or not row["readback_ref"].strip()
            or row.get("superseded_by")):
        raise ValueError("交付回读的仓库、目标、PR 或合并事实不符：" + name)
    try:
        stamp = datetime.fromisoformat(row["merged_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("缺少时区")
        return stamp
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise ValueError("合并时间不是带时区的可核验时间戳：" + name) from error


def current(binding, head):
    """旧绑定无当前 PR 时按 Head 识别唯一事实；返工待交付时要求新 PR。"""
    observation = binding.get("observation") or {}
    pr = observation.get("results", {}).get("pull_request")
    previous = observation.get("follow_up_after_pr")
    if previous is not None and (not pr or str(pr) == str(previous)):
        return None
    matches = []
    for row in binding["deliveries"]:
        try:
            stamp = validate(binding, row)
        except ValueError:
            continue
        matches.append((stamp, row))
    if not matches:
        return None
    latest = max(stamp for stamp, _ in matches)
    candidates = [row for stamp, row in matches if stamp == latest and row["candidate_head"] == head
                  and (not pr or str(row["pr"]) == str(pr))]
    return candidates[0] if len(candidates) == 1 else None


def record(binding, row, repair_digest=None):
    """追加合并事实，重复回读保持原记录；仅修补未满足合同的旧事实。"""
    from workflow.engineering_baseline import digest

    validate(binding, row)
    for index, previous in enumerate(binding["deliveries"]):
        if str(previous.get("pr")) != str(row["pr"]):
            continue
        try:
            validate(binding, previous)
        except ValueError:
            if (previous.get("superseded_by") or not repair_digest or digest(previous) != repair_digest
                    or any(previous.get(field) not in (None, "", row[field]) for field in FACT_FIELDS)):
                raise ValueError("既有交付缺项或事实冲突；回读原 PR 后以原记录摘要和研发决定修补")
            binding["deliveries"][index] = row
            return previous
        if any(previous[field] != row[field] for field in FACT_FIELDS):
            raise ValueError("同一 PR 已有不同合并事实，保留原记录并核对来源")
        return None
    if repair_digest:
        raise ValueError("原交付记录已变化或不存在")
    binding["deliveries"].append(row)
    return None

"""既有外部意图的有限回执恢复；不授予发送或开发权限。"""
from functools import wraps

from workflow import archive_store, station_operation, task_store


def locked(function):
    @wraps(function)
    def invoke(base, issue_key, *args, **kwargs):
        with task_store.task_run_lock(base, issue_key):
            return function(base, issue_key, *args, **kwargs)
    return invoke


def local_locked(function):
    """仅用于归属核验自身：仍保护本地会话，不循环依赖已有远端归属。"""
    @wraps(function)
    def invoke(base, issue_key, *args, **kwargs):
        with task_store.task_run_lock(base, issue_key, ownership=False):
            return function(base, issue_key, *args, **kwargs)
    return invoke


def require_receipt(base, task):
    """调用者另须核验原意图；必须在工位锁内检查和保存。"""
    current = task_store.check_expected_run(base, task["issue_key"], task["run_id"])
    if current.get("_revision") != task.get("_revision"):
        raise ValueError("工位 revision 已变化，拒绝过期回执")
    operation = station_operation.read(base) or {}
    if operation.get("run_id") != task["run_id"] and operation.get("status") == "done":
        operation = {}
    target = archive_store.run_directory(base, task["run_id"])
    # 不可变快照与活动任务分离：历史归档不冻结后续活动回执。
    unsafe_archive = target.is_symlink() or (target.exists() and not target.is_dir())
    archive_content = not unsafe_archive and target.is_dir() and any(target.iterdir())
    pending_snapshot = operation.get("status") != "done" and (
        any(key in operation for key in ("archive_payload", "archive_record", "archive_evidence", "archive_artifacts", "archive_logs"))
        or any(key.startswith("archive-publish:") for key in operation.get("steps", {})))
    frozen = (current.get("archive_ref") or unsafe_archive or pending_snapshot
              or (current.get("outcome") in ("completed", "interrupted") and archive_content))
    if frozen:
        raise ValueError("归档证据已冻结；禁止补写活动回执，请交维护侧核对原操作与档案")
    if current.get("outcome") == "interrupted":
        raise ValueError("任务已终止，不能补写活动回执")
    if operation and operation.get("status") != "done":
        from workflow import station_takeover
        if station_takeover.preparing(base, current):
            return
        if operation.get("run_id") != task["run_id"] or operation.get("kind") not in ("archive", "release", "clean"):
            raise ValueError("工位有其它未完成操作，请先恢复")
        plan = operation.get('cleanup_plan')
        confirmed = operation.get('confirmation_digest', operation.get('request', {}).get('confirmed_digest'))
        if (plan and confirmed == plan.get('digest')
                and operation.get('phase') in ('intent', 'cleanup_admission', 'cleanup_archive')):
            return  # 尚未冻结正文；调用者仍核验原意图和可变回执字段，不允许新增发送。
        if (operation.get("kind") != "archive" or operation.get("phase") != "intent"
                or operation.get("request", {}).get("confirmed_digest")
                or any(key in operation for key in ("cleanup_plan", "plan_revisions", "handoff", "archive_drafts"))):
            raise ValueError("退出计划已绑定活动材料；禁止改变确认摘要，请交维护侧核对原操作")


def validate_update(before, after, mutable):
    """恢复只能补结果，不能修改原目标、载荷或发送意图身份。"""
    if {k: v for k, v in before.items() if k not in mutable} != {k: v for k, v in after.items() if k not in mutable}:
        raise ValueError("回执恢复不得改变原外部操作目标或意图")

"""清理入口：只读预检或显式确认后恢复同一个生命周期操作。"""
import argparse
import json
from pathlib import Path

from workflow import station, station_operation, station_resources, task_store, station_clean_view


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default=".")
    parser.add_argument("--issue-key")
    parser.add_argument("--expected-run-id")
    parser.add_argument("--expected-revision", type=int)
    parser.add_argument("--operation-id")
    parser.add_argument("--input", help="工位外确认请求 JSON；省略时只读预检")
    parser.add_argument("--abandon-changes", choices=("yes", "no"), help="未完成任务必须明确选择；不会交互猜测")
    args = parser.parse_args(argv)
    task = task_store.read_task(args.dir)
    operation = station_operation.read(args.dir)
    pending = operation and operation["status"] != "done"
    takeover = pending and operation["kind"] == "takeover"
    new_cleanup = not pending or (takeover and task and not operation.get("handoff"))
    if args.abandon_changes == "no" and task and task.get("outcome") != "completed" and new_cleanup:
        print(json.dumps({"status": "cancelled", "message": "保留当前变更，未执行清理"}, ensure_ascii=False))
        return 0
    if not task and not (operation and operation["status"] != "done"):
        print(json.dumps({"status": "idle", "message": "工位空闲，本次未删除任何材料",
            "cleanup_scope": station_clean_view.safe_describe(args.dir, operation=operation)}, ensure_ascii=False, indent=2))
        return 0
    if not args.input or (pending and args.abandon_changes == "no"):
        result = {"status": "confirmation_required" if new_cleanup else "resume_required",
                  "issue_key": task["issue_key"] if task else operation["request"].get("issue_key"),
                  "run_id": task["run_id"] if task else operation["run_id"],
                  "revision": task["_revision"] if task else task_store.read_current(args.dir)["revision"],
                  "operation_id": (operation.get("handoff", {}).get("operation_id", operation["operation_id"]) if pending else None)}
        if task and new_cleanup:
            result["question"] = None if task.get("outcome") == "completed" else "任务未完成，是否放弃当前变更并在归档后清理？"
            result["blockers"] = []
            from workflow import native_cleanup, station_directories
            try:
                result["native_clean"], native_errors = native_cleanup.inspect(args.dir, task, station_directories.load(args.dir, task))
                result["blockers"].extend(native_errors)
            except (ValueError, OSError) as exc:
                result["blockers"].append(str(exc))
            try:
                result["cleanup_plan"] = station_resources.plan(args.dir, task, version=5)
            except (ValueError, OSError) as exc:
                result["blockers"].append(str(exc))
            try:
                station_resources.verify_stopped(args.dir, task)
                station_resources.verify_known_external(args.dir, task)
            except (ValueError, OSError) as exc:
                result["blockers"].append(str(exc))
        original_plan = (operation or {}).get("cleanup_plan") if not new_cleanup else result.get("cleanup_plan")
        result["cleanup_scope"] = station_clean_view.safe_describe(args.dir, task, original_plan,
            operation if not new_cleanup else None, result.get("blockers", []))
        if pending and original_plan:
            try:
                station_resources.verify_station_inventory(args.dir, original_plan.get("rules", {}), allow_pending=True)
            except (ValueError, OSError, KeyError) as error:
                result["cleanup_scope"]["blockers"].append("当前范围需核验：" + str(error))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if any(value is None for value in (args.issue_key, args.expected_run_id, args.expected_revision, args.operation_id)):
        raise ValueError("执行必须绑定 issue/run/revision/operation-id")
    if takeover and not task:
        raise ValueError("接管尚未绑定任务，只能恢复原接管操作")
    request_path = Path(args.input).resolve()
    station_dir = Path(args.dir).resolve()
    if station_dir == request_path or station_dir in request_path.parents:
        raise ValueError("确认请求必须位于工位外")
    request = json.loads(request_path.read_text())
    if not isinstance(request, dict):
        raise ValueError("确认请求必须为 JSON 对象")
    if pending and not takeover:
        if operation["operation_id"] != args.operation_id or operation["kind"] not in ("clean", "release"):
            raise ValueError("只能恢复原清理操作")
        kind = operation["kind"]
    else:
        kind = "release" if task.get("outcome") == "completed" else "clean"
        if kind == "clean" and args.abandon_changes != "yes":
            raise ValueError("任务未完成，请明确是否放弃变更；未写入任何状态")
        if request.get("cleanup_version") not in (4, 5):
            raise ValueError("新入口请求必须声明 cleanup_version=5（旧版 4 请求只按旧合同执行）")
        if kind == "clean":
            if request.get("abandon_changes") is not True:
                raise ValueError("确认请求必须记录 abandon_changes=true")
    result = station.execute(args.dir, kind, args.issue_key, args.expected_run_id,
                             args.expected_revision, args.operation_id, request)
    output = {key: result.get(key) for key in ("operation_id", "status", "phase", "archive_ref", "native_problems")}
    output["cleanup_scope"] = station_clean_view.safe_describe(args.dir, task_store.read_task(args.dir), result.get("cleanup_plan"), result)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0

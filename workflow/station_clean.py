"""清理入口：只读预检或显式确认后恢复同一个生命周期操作。"""
import argparse
import json
from pathlib import Path

from workflow import station, station_operation, station_resources, task_store, station_clean_view


def check(base, task, request=None, operation=None, replacements=None):
    """不 begin/freeze/publish；复用正式材料与精确准入函数。"""
    from workflow import station_archive, station_records, engineering_baseline as baseline
    from workflow import archive_store
    import shutil
    blockers, execution_blockers, plan, material = [], [], None, None
    pending = operation and operation['status'] != 'done'
    try:
        plan = (operation.get('cleanup_plan') if pending and task.get('archive_ref') else
                station_resources.plan(base, task, evidence_override=replacements))
    except (ValueError, OSError) as error:
        blockers.append(str(error))
    scope_complete = bool(plan)
    proposed = request or {'summary': '任务退出材料预检', 'reason': '只读材料预检', 'require_complete_conversation': True}
    coverage = None
    documents = None
    for action in (lambda: station_resources.verify_stopped(base, task),
                   lambda: station_resources.verify_known_external(base, task)):
        try:
            action()
        except (ValueError, OSError) as error:
            execution_blockers.append(str(error))
    if task.get('archive_ref'):
        try:
            record = station_archive.verify(base, task['archive_ref'], task)
            if plan and pending:
                station_resources.verify_active(base, plan, operation)
            material = {key: record['files'][name]['size'] for key, name in
                (('summary', 'summary.md'), ('evidence', 'evidence.json'), ('artifacts', 'source-artifacts.json'), ('logs', 'runtime-evidence.json'))}
        except (ValueError, OSError) as error:
            blockers.append(str(error))
    else:
        try:
            documents = station_records.read_documents(base, task, replacements)
            coverage = station_records.conversations(base, task, documents)
        except (ValueError, OSError) as error:
            blockers.append(str(error))
        checks = [lambda: station_records.verify_decision(base, task, proposed, documents),
                  lambda: station_archive._evidence(base, task, replacements, documents)]
        if plan:
            checks.append(lambda: station_records.verify_source_choices(plan))
        for action in checks:
            try:
                action()
            except (ValueError, OSError) as error:
                blockers.append(str(error))
        if plan and not blockers:
            try:
                prepared = station_archive.prepare_material(base, task, proposed, plan, replacements)
                material = {key: len(prepared[key]) for key in ('summary', 'evidence', 'artifacts', 'logs')}
                material['record'] = len((json.dumps(prepared['record'], sort_keys=True, ensure_ascii=False, indent=2) + '\n').encode())
            except (ValueError, OSError) as error:
                blockers.append(str(error))
    archive_ready = bool(plan and material and not blockers)
    if material:
        capacity_path = archive_store.root(base)
        while not capacity_path.exists():
            capacity_path = capacity_path.parent
        available = shutil.disk_usage(capacity_path).free
        if not task.get('archive_ref') and sum(material.values()) > available:
            blockers.append('归档目标可用容量不足')
            archive_ready = False
    else:
        available = None
    kind = 'release' if task.get('outcome') == 'completed' else 'clean'
    if request and plan:
        try:
            if pending:
                if (operation['request'] != request or operation['run_id'] != task['run_id']):
                    raise ValueError('只读恢复检查必须使用原请求')
            else:
                station._verify_cleanup_decision(base, task, request, kind, prepared_plan=plan)
                if kind == 'clean' and request.get('abandon_changes') is not True:
                    raise ValueError('未完成 run 缺少精确 clean 决定')
                if kind == 'release':
                    proof = task.get('terminal_proof') or station.completion_proof(base, task)
                    if request.get('candidate_digest') != proof['candidate_digest']:
                        raise ValueError('释放决定未绑定最终候选')
                    from workflow import station_source
                    if baseline.digest(station_source.inspect(base, task['engineering_baseline'])) != proof['candidate_digest']:
                        raise ValueError('完成后源码已变化')
        except (ValueError, OSError) as error:
            execution_blockers.append(str(error))
    else:
        execution_blockers.append('尚无完整确认请求')
    scope = station_clean_view.safe_describe(base, task, plan, operation if pending else None,
        blockers + execution_blockers, conversation=coverage)
    scope.update(scope_complete=scope_complete and not scope.get('unknown'), archive_ready=archive_ready,
        execution_ready=archive_ready and not execution_blockers and not scope.get('unknown'),
        archive_budget={'conversation_input_limit': station_records.MAX_FILE_BYTES,
                        'record_body_limit': station_records.MAX_TOTAL_BYTES,
                        'encoded_bytes': material, 'available_bytes': available})
    scope['complete'] = bool(scope['scope_complete'] and scope['archive_ready'])
    scope['executable'] = bool(scope['execution_ready'])
    return {'status': 'check', 'issue_key': task['issue_key'], 'run_id': task['run_id'],
        'revision': task['_revision'], 'operation_id': (operation or {}).get('operation_id'),
        'cleanup_plan': plan, 'cleanup_scope': scope, 'blockers': list(dict.fromkeys(blockers + execution_blockers)),
        'boundary': '只读预检；未确认、未归档、未删除、未释放或注销'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default=".")
    parser.add_argument("--issue-key")
    parser.add_argument("--expected-run-id")
    parser.add_argument("--expected-revision", type=int)
    parser.add_argument("--operation-id")
    parser.add_argument("--input", help="工位外确认请求 JSON；省略时只读预检")
    parser.add_argument("--check", action="store_true", help="只读复用实际材料准入；可与完整 --input 请求组合")
    parser.add_argument("--conversation-input", help="工位外会话清单；默认仅录入，--check 只读预览")
    parser.add_argument("--output", help="--details 的工位外私有新文件")
    parser.add_argument("--details", action="store_true", help="显示完整精确清单；默认聚合展示")
    parser.add_argument("--archive-runtime", choices=("none", "logs", "reports", "logs,reports"),
                        help="只读预览：收缩原操作的运行证据保全列表；用 cleanup-amend 确认后生效")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--prepare-only", action="store_true", help="确认并保全，不执行清理，交由 Agent 接力")
    modes.add_argument("--verify-result", action="store_true", help="只验收已完成的清理成果并正式解绑，不要求脚本回执")
    parser.add_argument("--abandon-changes", choices=("yes", "no"), help="未完成任务必须明确选择；不会交互猜测")
    args = parser.parse_args(argv)
    from workflow import project_rules
    from bootstrap.station_compatibility import require_station_can_adopt
    require_station_can_adopt(project_rules.product_root_from_station(args.dir), args.dir)
    task = task_store.read_task(args.dir)
    operation = station_operation.read(args.dir)
    if task and (args.issue_key is not None and args.issue_key != task['issue_key']
                 or args.expected_run_id is not None and args.expected_run_id != task['run_id']):
        raise ValueError('预检或执行的 issue/run 与当前任务不一致')
    pending = operation and operation["status"] != "done"
    takeover = pending and operation["kind"] == "takeover"
    new_cleanup = not pending or (takeover and task and not operation.get("handoff"))
    if args.output and not args.details:
        raise ValueError('--output 必须与 --details 组合')
    if args.conversation_input and (args.prepare_only or args.verify_result or args.input and not args.check):
        raise ValueError('会话录入与清理执行/prepare/verify 互斥')
    if args.conversation_input and not args.check:
        from workflow import station_exit_inputs
        if any(x is None for x in (args.issue_key, args.expected_run_id, args.expected_revision)):
            raise ValueError('退出录入必须绑定 issue/run/revision')
        result = station_exit_inputs.record(args.dir, args.issue_key, args.expected_run_id,
                                            args.expected_revision, args.conversation_input)
        station_clean_view.emit(result, details=args.details, output=args.output, base=args.dir)
        return 0
    if args.check:
        if args.prepare_only or args.verify_result or args.archive_runtime is not None:
            raise ValueError('--check 不能同时执行或修订运行资源选择')
        if not task:
            raise ValueError('没有当前任务可供材料预检')
        request = None
        if args.input:
            path = Path(args.input).resolve()
            if Path(args.dir).resolve() in path.parents:
                raise ValueError('确认请求必须位于工位外')
            request = json.loads(path.read_text())
            if not isinstance(request, dict):
                raise ValueError('确认请求必须为对象')
        prepared = None
        if args.conversation_input:
            from workflow import station_exit_inputs
            prepared = station_exit_inputs.prepare(args.dir, task, args.conversation_input, amendment=True)
        result = check(args.dir, task, request, operation, prepared['files'] if prepared else None)
        station_clean_view.emit(result, details=args.details, output=args.output, base=args.dir)
        return 0
    if args.archive_runtime is not None and (args.input or new_cleanup or not task):
        raise ValueError("运行证据选择仅用于当前退出操作的只读修订预览")
    if args.abandon_changes == "no" and task and task.get("outcome") != "completed" and new_cleanup:
        station_clean_view.emit({"status": "cancelled", "message": "保留当前变更，未执行清理"})
        return 0
    if not task and not (operation and operation["status"] != "done"):
        station_clean_view.emit({"status": "idle", "message": "工位空闲，本次未删除任何材料",
            "cleanup_scope": station_clean_view.safe_describe(args.dir, operation=operation)},
            details=args.details, output=args.output, base=args.dir)
        return 0
    if not args.input or (pending and args.abandon_changes == "no"):
        result = {"status": "confirmation_required" if new_cleanup else "resume_required",
                  "issue_key": task["issue_key"] if task else operation["request"].get("issue_key"),
                  "run_id": task["run_id"] if task else operation["run_id"],
                  "revision": task["_revision"] if task else task_store.read_current(args.dir)["revision"],
                  "operation_id": (operation.get("handoff", {}).get("operation_id", operation["operation_id"]) if pending else None)}
        if task and new_cleanup:
            result["question"] = None if task.get("outcome") == "completed" else "任务未完成，是否放弃当前变更并在归档后清理？"
            checked = check(args.dir, task)
            result['blockers'] = checked['blockers']
            result['cleanup_plan'] = checked['cleanup_plan']
        original_plan = (operation or {}).get("cleanup_plan") if not new_cleanup else result.get("cleanup_plan")
        if args.archive_runtime is not None:
            selection = [] if args.archive_runtime == "none" else args.archive_runtime.split(",")
            original_plan = station_resources.plan(args.dir, task, archive_runtime=selection)
            result["amendment_preview"] = {"archive_runtime": selection,
                "expected_plan_digest": operation["cleanup_plan"]["digest"],
                "expected_plan_revision": len(operation.get("plan_revisions", [])),
                "confirmed_digest": original_plan["digest"], "cleanup_plan": original_plan}
            discarded = [e for e in original_plan["entries"] if e["preservation"]["action"] == "discard"]
            if discarded:
                from workflow.engineering_baseline import digest
                result["amendment_preview"]["discard_digest"] = digest(discarded)
        result["cleanup_scope"] = (checked['cleanup_scope'] if task and new_cleanup else
            station_clean_view.safe_describe(args.dir, task, original_plan, operation, result.get('blockers', [])))
        if pending and original_plan:
            try:
                station_resources.verify_station_inventory(args.dir, original_plan.get("rules", {}), allow_pending=True)
            except (ValueError, OSError, KeyError) as error:
                result["cleanup_scope"]["blockers"].append("当前范围需核验：" + str(error))
        if not args.details:
            result.pop('cleanup_plan', None)
            if 'amendment_preview' in result:
                result['amendment_preview'].pop('cleanup_plan', None)
        station_clean_view.emit(result, details=args.details, output=args.output, base=args.dir)
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
        if request.get("cleanup_version") != 6:
            raise ValueError("请求必须声明 cleanup_version=6；旧计划由原版本退出，不在线迁移")
        if kind == "clean":
            if request.get("abandon_changes") is not True:
                raise ValueError("确认请求必须记录 abandon_changes=true")
    result = station.execute(args.dir, kind, args.issue_key, args.expected_run_id,
                             args.expected_revision, args.operation_id, request,
                             cleanup_mode="prepare" if args.prepare_only else "verify" if args.verify_result else "execute")
    output = {key: result.get(key) for key in ("operation_id", "status", "phase", "archive_ref")}
    output.update(issue_key=task['issue_key'], run_id=task['run_id'], revision=args.expected_revision)
    output["cleanup_scope"] = station_clean_view.safe_describe(args.dir, task_store.read_task(args.dir), result.get("cleanup_plan"), result)
    station_clean_view.emit(output, details=args.details, output=args.output, base=args.dir)
    return 0


def cli(argv=None):
    """命令行输出结构化接管信息；Python 调用者仍可捕获原异常。"""
    import sys
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        return main(arguments)
    except (ValueError, OSError) as error:
        base = arguments[arguments.index("--dir") + 1] if "--dir" in arguments else "."
        try:
            operation = station_operation.read(base)
            task = task_store.read_task(base)
            scope = station_clean_view.safe_describe(base, task, (operation or {}).get("cleanup_plan"), operation, [str(error)])
        except (ValueError, OSError):
            operation, scope = None, {"complete": False}
        station_clean_view.emit({"status": "needs_attention", "operation_id": (operation or {}).get("operation_id"),
            "phase": (operation or {}).get("phase", "admission"), "problems": str(error).splitlines(),
            "next_action": "处理具体对象后用原请求恢复；范围变化使用 cleanup-amend，不手改状态。",
            "cleanup_scope": scope}, details="--details" in arguments)
        return 2

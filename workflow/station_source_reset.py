"""归档后的 Git 阶段；复用原操作，不拥有独立任务生命周期。"""
from workflow import station_archive, station_artifacts, station_operation, station_resources, station_directories, station_source, task_store


def apply(base, task, operation):
    plan = operation.get("cleanup_plan", {})
    if (plan.get("schema_version") not in (4, 5) or operation["run_id"] != task["run_id"]
            or operation["kind"] not in ("clean", "release") or operation["status"] != "running"):
        raise ValueError("源码复位必须属于当前版本 4/5 清理操作")
    station_archive.verify(base, task.get("archive_ref"), task)
    station_resources.verify_stopped(base, task)
    station_resources.verify_station_inventory(base, plan["rules"], allow_pending=True)
    for entry in plan["directories"]:
        path = station_directories.precheck(base, task, entry, operation)
        if entry["kind"] == "source-generated" and path[0].exists():
            raise ValueError("源码构建产物尚未清理，请使用项目原生工具：" + entry["path"])
    generation = str(len(operation.get("plan_revisions", [])))
    step_name = "station-source-reset:" + generation + ":" + plan["digest"]
    done = operation["steps"].get(step_name, {}).get("receipt")
    if done is None:
        # 按当前计划的源码成果回执判断，不用旧计划的 neutral 推定新增成果已恢复。
        names = {e['repository'] for e in plan['entries']}
        pending = any(operation['steps'].get('source-reset:' + generation + ':' + plan['digest'] + ':' + name, {}).get('receipt') is None for name in names)
        if pending:
            station_resources.clean(base, task, plan, operation.get("confirmation_digest", operation["request"]["confirmed_digest"]), operation, source_only=True)
        station_operation.intent(base, operation, step_name, {}, {"plan_digest": plan["digest"]})
        station_resources.neutral(base, task, operation)
        station_operation.receipt(base, operation, step_name, {"plan_digest": plan["digest"]})
    else:
        for name, entry in plan["source"].items():
            repo = station_source.repository_path(base, name)
            station_source.identity(repo, entry['origin'])
            if entry.get("initial_checkout"):
                if {p.name for p in repo.iterdir()} != {".git"}:
                    raise ValueError("未完成检出仓库出现未知内容")
                continue
            station_source.require_clean(repo)
            station_artifacts.verify_special_entries(repo, entry['neutral']['sha'])
            if station_source.git(repo, "ls-files", "--others", "--ignored", "--exclude-standard").stdout:
                raise ValueError("源码复位后出现未清理 ignored 产物")
            checkout_branch = station_source.baseline_branch(task["engineering_baseline"]["repositories"][name])
            if (station_source.git(repo, "rev-parse", "HEAD").stdout.strip() != entry["neutral"]["sha"]
                    or station_source.git(repo, "branch", "--show-current").stdout.strip() != (checkout_branch or "")
                    or station_source.git(repo, "rev-parse", entry["preserved_ref"]).stdout.strip() != entry["preserved_head"]):
                raise ValueError("源码复位后 HEAD 已变化")


def run(base, issue, run_id, revision, operation_id):
    with task_store.task_state_lock(base):
        task = task_store.check_expected_run(base, issue, run_id)
        operation = station_operation.read(base)
        if task["_revision"] != revision or not operation or operation["operation_id"] != operation_id:
            raise ValueError("源码复位操作或 revision 不匹配")
        apply(base, task, operation)

"""质量日志内的共用验证材料；只核对完整性、版本与具体处置。"""
import copy
import hashlib
import re
from pathlib import Path

from workflow.file_digest import sha256_file as file_hash

KINDS = {"local", "source_sync", "ci", "review"}


def plan_rows(rules, ctx):
    contract = (rules or {}).get("verification_plan")
    if not contract:
        return []
    plan = ctx.get("facts", {}).get(contract["fact_key"], {})
    return plan.get(contract["field"]) if isinstance(plan, dict) else None


def plan_problems(rules, ctx):
    """项目提供知识字段形状；内核只检查完整性、仓库与范围。"""
    contract = (rules or {}).get("verification_plan")
    if not contract:
        return []
    from workflow import plan_review
    rows = plan_rows(rules, ctx)
    errors = plan_review.shape(rows, contract["schema"], "方案." + contract["field"])
    if errors:
        return errors
    seen, repositories = set(), set(ctx["repositories"])
    for row in rows:
        key = (row["repository"], row["scope"])
        if key in seen:
            errors.append("验证方案范围重复：%s / %s" % key)
        seen.add(key)
        if row["repository"] not in repositories:
            errors.append("验证方案引用未登记仓库：" + row["repository"])
    errors += ["验证方案未覆盖仓库：" + repo for repo in sorted(repositories - {r["repository"] for r in rows})]
    return errors


def coverage_problems(p, rules, ctx):
    contract = (rules or {}).get("verification_plan")
    if not contract or p["kind"] not in contract["kinds"]:
        return []
    errors = plan_problems(rules, ctx)
    if errors:
        return errors
    from workflow import quality
    if p["kind"] == "ci" and not quality.exact_commit(p["target_revision"]):
        errors.append("CI 验证必须绑定当前完整提交 SHA")
    results = {r["scope"]: r for r in p["results"]}
    for row in plan_rows(rules, ctx):
        if row["repository"] != p["repository"]:
            continue
        result = results.get(row["scope"])
        if not result or result.get("method") != contract["method"]:
            errors.append("%s %s 缺少 %s / %s 实测或缺口材料；请求研发确认具体未覆盖范围及处置，不能以其它 Checks 成功替代" %
                          (p["repository"], p["kind"], contract["method"], row["scope"]))
    return errors


def nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("验证材料缺少 " + label)
    return value


def strings(value, label):
    if not isinstance(value, list) or not value or any(not isinstance(x, str) or not x.strip() for x in value):
        raise ValueError("验证材料缺少 " + label)
    if len(value) != len(set(value)):
        raise ValueError(label + " 有重复项")
    return value


def versions(ctx):
    return {name: repo.get("live_revision") for name, repo in ctx["repositories"].items()}


def bindings(ctx, repository=None):
    from workflow import quality
    keys = ("repository", "base_branch", "work_branch", "base_sha", "catalog_digest",
            "approved_scope", "verification_method")
    return quality.digest({name: {key: repo.get(key) for key in keys}
                           for name, repo in ctx["repositories"].items()
                           if repository is None or name == repository})


def binding(p, ctx):
    """旧事件缺省全仓；新来源事实只绑定所属仓，不能重解释历史。"""
    if "binding_version" not in p:
        return versions(ctx), bindings(ctx)
    if type(p["binding_version"]) is not int or p["binding_version"] != 2 or p.get("kind") != "source_sync":
        raise ValueError("验证材料绑定版本无效")
    repo = p["repository"]
    return {repo: ctx["repositories"][repo].get("live_revision")}, bindings(ctx, repo)


def verify_artifacts(p):
    if p.get("kind") == "local":
        for jar in p.get("jars", []):
            for side in ("built", "consumed"):
                if file_hash(jar[side + "_path"]) != jar[side + "_sha256"]:
                    raise ValueError("本地 Jar 与验证记录不一致")


def cleanup_artifacts(base, task, root):
    """删除工作树前保留已核验的文件哈希；不把缺失或变化的文件固化为有效。"""
    from workflow import quality
    model = quality.replay(quality.load(base, task))
    verified = {}
    root = Path(root).resolve()
    for entries in model.get("verification", {}).values():
        for jar in entries.get("local", {}).get("data", {}).get("jars", []):
            for side in ("built", "consumed"):
                path = jar[side + "_path"]
                try:
                    if root in Path(path).resolve().parents and file_hash(path) == jar[side + "_sha256"]:
                        verified[hashlib.sha256(path.encode()).hexdigest()] = jar[side + "_sha256"]
                except OSError:
                    pass
    return verified


def artifact_matches(path, expected, ctx):
    # 仅受控 removed 工作树提供收尾凭据；恢复工作树后重新读取实际文件。
    key = hashlib.sha256(path.encode()).hexdigest()
    if key in ctx.get("cleanup_artifacts", {}):
        return ctx["cleanup_artifacts"][key] == expected
    return file_hash(path) == expected


def gap(decision):
    from workflow import quality
    if not isinstance(decision, dict):
        raise ValueError("缺口缺少具体人工决定")
    for key in ("reason", "uncovered", "follow_up"):
        nonempty(decision.get(key), key)
    proof = decision.get("proof") or {}
    for key in ("actor", "source", "reference", "at"):
        nonempty(proof.get(key), key)
    if proof["source"] != "user_message":
        raise ValueError("缺口必须引用研发明确决定")
    quality.check_proof(proof)


def validate(p, ctx):
    from workflow import quality
    kind, repo = p.get("kind"), p.get("repository")
    if kind not in KINDS or repo not in ctx["repositories"]:
        raise ValueError("验证种类或任务仓库无效")
    binding(p, ctx)
    revision = nonempty(p.get("target_revision"), "target_revision")
    if not (quality.exact_commit(revision) or quality.exact_worktree(revision)) or revision != versions(ctx)[repo]:
        raise ValueError("验证材料不是当前任务代码版本")
    nonempty(p.get("source_ref"), "可回查来源")
    if kind in ("local", "ci"):
        for key in ("analysis_ref", "case_review_ref", "case_version", "dependency_analysis_ref"):
            nonempty(p.get(key), key)
        required = strings(p.get("required_scope"), "应测范围")
        results = p.get("results")
        if not isinstance(results, list):
            raise ValueError("缺少逐项验证结果")
        seen = []
        for result in results:
            if not isinstance(result, dict) or result.get("scope") not in required:
                raise ValueError("验证结果范围不属于应测清单")
            seen.append(result["scope"])
            if result.get("result") not in ("PASS", "FAIL", "UNKNOWN", "NOT_RUN", "SKIPPED"):
                raise ValueError("验证结果无效")
            nonempty(result.get("report_ref"), "报告或缺失原因来源")
            if result["result"] == "PASS":
                for key in ("tests", "failures", "errors", "skipped"):
                    if type(result.get(key)) is not int or result[key] < 0:
                        raise ValueError("PASS 必须有实际报告计数")
                if result["tests"] == 0 or any(result[k] for k in ("failures", "errors", "skipped")):
                    raise ValueError("零用例、失败、错误或跳过不能登记为 PASS")
            if "decision" in result:
                gap(result["decision"])
                if result["decision"]["uncovered"] != result["scope"]:
                    raise ValueError("人工决定必须精确对应当前缺口范围")
        if set(seen) != set(required) or len(seen) != len(set(seen)):
            raise ValueError("应测范围存在遗漏或重复结果")
        jars = p.get("jars", [])
        if not isinstance(jars, list):
            raise ValueError("Jar 材料无效")
        for jar in jars:
            if not isinstance(jar, dict):
                raise ValueError("Jar 材料无效")
            for key in ("built_sha256", "consumed_sha256"):
                if not re.fullmatch(r"[0-9a-f]{64}", str(jar.get(key, ""))):
                    raise ValueError("Jar 缺少完整哈希")
            if jar["built_sha256"] != jar["consumed_sha256"]:
                raise ValueError("构建和消费 Jar 不一致")
            nonempty(jar.get("loaded_from"), "实际加载路径证据")
            if kind == "local":
                for side in ("built", "consumed"):
                    file = nonempty(jar.get(side + "_path"), side + " Jar 路径")
                    if not Path(file).is_absolute():
                        raise ValueError("本地 Jar 必须使用绝对路径")
        if kind == "ci":
            if p.get("head_revision") != revision:
                raise ValueError("CI 运行 Head 与当前代码不一致")
            status = p.get("run_status", "observed")
            if status == "not_triggered":
                if not quality.exact_commit(revision):
                    raise ValueError("未触发 CI 也必须绑定完整 PR Head")
                if any(key in p for key in ("run_ref", "attempt", "checkout_ref")):
                    raise ValueError("未触发 CI 不能填写不存在的运行、attempt 或 checkout")
                if any(r["result"] != "NOT_RUN" for r in results):
                    raise ValueError("未触发 CI 只能记录 NOT_RUN")
            elif status == "observed":
                nonempty(p.get("run_ref"), "CI 运行")
                nonempty(p.get("checkout_ref"), "实际 checkout 核对来源")
                if type(p.get("attempt")) is not int or p["attempt"] < 1:
                    raise ValueError("CI 运行 attempt 无效")
            else:
                raise ValueError("CI run_status 无效")
    elif kind == "source_sync":
        sync = p.get("sync") or {}
        r = ctx["repositories"][repo]
        if (sync.get("task_revision") != revision or sync.get("base_revision") != r.get("base_sha")
                or sync.get("work_branch") != r.get("work_branch") or sync.get("contains_source") is not True
                or p.get("source_branch") != r.get("base_branch")):
            raise ValueError("来源同步与冻结任务登记不一致")
        nonempty(p.get("impact_analysis_ref"), "同步后的行为影响分析")
        nonempty(p.get("observed_at"), "远端核对时间")
    else:
        if p.get("complete") is not True or not isinstance(p.get("items"), list):
            raise ValueError("审查意见尚未完整回读")
        seen = set()
        for item in p["items"]:
            if not isinstance(item, dict):
                raise ValueError("审查意见无效")
            key = nonempty(item.get("id"), "意见 ID")
            if key in seen:
                raise ValueError("审查意见重复")
            seen.add(key)
            nonempty(item.get("source_ref"), "意见来源")
            nonempty(item.get("reason"), "处理依据")
            if item.get("status") not in ("fixed", "not_applicable", "accepted_gap", "pending"):
                raise ValueError("意见处理结论无效")
            if item["status"] == "fixed":
                nonempty(item.get("verification_ref"), "返工验证")
            if item["status"] == "accepted_gap":
                gap(item.get("decision"))


def record(model, p, ctx, rules=None):
    validate(p, ctx)
    revisions, scope = binding(p, ctx)
    value = {"data": copy.deepcopy(p), "versions": revisions, "bindings": scope}
    contract = (rules or {}).get("verification_plan")
    if contract and p["kind"] in contract["kinds"]:
        from workflow import quality
        value["plan_digest"] = quality.digest(plan_rows(rules, ctx))
    if p["kind"] == "ci":
        value["ci_digest"] = ctx["repositories"][p["repository"]].get("ci_digest")
    model.setdefault("verification", {}).setdefault(p["repository"], {})[p["kind"]] = value


def problems(model, ctx, kinds, rules=None):
    if not kinds:
        return []
    if not isinstance(kinds, list) or not set(kinds) <= KINDS:
        raise ValueError("项目验证要求无效")
    result = []
    for repo in ctx["repositories"]:
        for kind in kinds:
            entry = model.get("verification", {}).get(repo, {}).get(kind)
            if not entry:
                result.append("%s 缺少 %s 验证材料" % (repo, kind))
                continue
            revisions, scope = binding(entry["data"], ctx)
            if (entry["versions"] != revisions or entry.get("bindings") != scope
                    or (kind == "ci" and entry.get("ci_digest") != ctx["repositories"][repo].get("ci_digest"))):
                result.append("%s 的 %s 材料已失效，重新核对当前版本" % (repo, kind))
                continue
            p = entry["data"]
            contract = (rules or {}).get("verification_plan")
            if contract and kind in contract["kinds"]:
                from workflow import quality
                if entry.get("plan_digest") != quality.digest(plan_rows(rules, ctx)):
                    result.append("%s 的 %s 验证方案已变化，重新核对报告和缺口决定" % (repo, kind))
                result.extend(coverage_problems(p, rules, ctx))
            if kind == "local":
                for jar in p.get("jars", []):
                    try:
                        valid = all(artifact_matches(jar[side + "_path"], jar[side + "_sha256"], ctx) for side in ("built", "consumed"))
                    except OSError:
                        valid = False
                    if not valid:
                        result.append("%s 的依赖 Jar 已变化或缺失，需重验" % repo)
            for item in p.get("results", []):
                if item["result"] != "PASS" and not item.get("decision"):
                    result.append("%s %s：%s 尚未解决或明确处置；请求研发确认具体缺口及后续动作，确认不等于 PASS" % (repo, item["scope"], item["result"]))
            for item in p.get("items", []):
                if item["status"] == "pending":
                    result.append("%s 审查意见 %s 尚未处理" % (repo, item["id"]))
    for key, p in ctx.get("failures", {}).items():
        if p["status"] not in ("resolved", "accepted_gap"):
            result.append("失败 %s 尚未解决或明确处置（累计 %s 轮）" % (key, p["attempts"]))
        elif p["status"] == "resolved" and p["latest_result"]["source_revision"] != versions(ctx).get(p["repository"]):
            result.append("失败 %s 的修复结果属于旧版本，需重验" % key)
    return result

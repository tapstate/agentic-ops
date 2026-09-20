#!/usr/bin/env python3
"""Maven 模块执行清单与报告核验；不执行构建、测试或任务状态写入。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from maven_reports import parse_xml


SUPPORTED_MAVEN_VERSION = "3.9.x"
RUNTIME_CONTEXT_KEYS = {"station", "station_id", "run_id", "local_repository"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.PIPE)


def snapshot(root):
    if Path(git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve() != root.resolve():
        raise ValueError("源码快照必须绑定 Git 仓库根目录")
    head = git(root, "rev-parse", "HEAD").decode().strip()
    sha = hashlib.sha256(head.encode())
    sha.update(git(root, "diff", "--no-ext-diff", "--no-textconv", "--binary", "HEAD", "--"))
    for item in sorted(git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")):
        if item:
            path = root / item.decode()
            if path.is_symlink():
                raise ValueError("未跟踪符号链接须先处理：%s" % path)
            sha.update(item + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return {"head": head, "fingerprint": sha.hexdigest()}


def module_path(root, name):
    if not isinstance(name, str) or (name != "." and (not re.fullmatch(r"[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_.-]+)*", name) or any(p in (".", "..") for p in name.split("/")))):
        raise ValueError("模块必须为明确的仓库内相对路径：%s" % name)
    path = root / name
    if path.resolve() != path.absolute() or not (path / "pom.xml").is_file():
        raise ValueError("模块不存在或经过符号链接：%s" % name)
    return path


def file_hash(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def jar_pair(built, consumed):
    paths = [Path(p).resolve() for p in (built, consumed)]
    hashes = [file_hash(p) for p in paths]
    if hashes[0] != hashes[1]:
        raise ValueError("构建 Jar 与消费路径内容不同：%s" % paths[1])
    return {"built": str(paths[0]), "consumed": str(paths[1]), "sha256": hashes[0]}


def runtime_context(value):
    if not isinstance(value, dict) or set(value) != RUNTIME_CONTEXT_KEYS:
        raise ValueError("runtime context 必须包含 station、station_id、run_id 和 local_repository")
    if not all(isinstance(value[key], str) and value[key] for key in RUNTIME_CONTEXT_KEYS):
        raise ValueError("runtime context 字段无效")
    station = Path(value["station"])
    local = Path(value["local_repository"])
    if not station.is_absolute() or not local.is_absolute() or local != station / "runtime" / "maven-local":
        raise ValueError("Maven local repository 必须是 station/runtime/maven-local")
    runtime = station / "runtime"
    if runtime.is_symlink() or not runtime.is_dir() or runtime.is_mount():
        raise ValueError("station runtime 必须是非链接真实目录")
    if local.is_symlink() or (local.exists() and (not local.is_dir() or local.is_mount())):
        raise ValueError("Maven local repository 必须是非链接真实目录或尚未创建")
    return {key: str(value[key]) for key in sorted(RUNTIME_CONTEXT_KEYS)}


def maven_executable(value):
    candidate = Path(value) if value else Path(shutil.which("mvn") or "")
    if not candidate or not candidate.is_absolute():
        raise ValueError("必须提供绝对 Maven 可执行文件路径")
    try:
        path = candidate.resolve(strict=True)
    except OSError as error:
        raise ValueError("Maven 可执行文件不存在") from error
    if path.is_symlink() or not path.is_file() or not os.access(str(path), os.X_OK):
        raise ValueError("Maven 可执行文件必须是可执行普通文件")
    return str(path)


def command(executable, local_repository, *arguments):
    return [executable, "-Dmaven.repo.local=" + local_repository, *arguments]


def plan(root, modules, profiles, framework="failsafe", dependency_repos=(), jar_pairs=(), runtime=None, executable=None):
    root = root.resolve()
    if Path(git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve() != root:
        raise ValueError("repo 必须是 Git 仓库根目录")
    if not modules or len(set(modules)) != len(modules):
        raise ValueError("模块清单不能为空或重复")
    if any(not re.fullmatch(r"[a-zA-Z0-9_.-]+", p) or p.startswith("-") for p in profiles):
        raise ValueError("profile 名称无效")
    if framework not in ("failsafe", "surefire"):
        raise ValueError("必须明确 Maven 测试框架")
    context = runtime_context(runtime)
    executable = maven_executable(executable)
    source_dir = "src/it/java" if framework == "failsafe" else "src/test/java"
    selected, unavailable = [], []
    for name in modules:
        path = module_path(root, name)
        if not any((path / source_dir).rglob("*.java")):
            unavailable.append({"module": name, "reason": "未发现 %s 用例，需 Agent 核实框架或由研发决定补齐能力" % source_dir})
        else:
            selected.append(name)
    options = ["-P" + ",".join(profiles)] if profiles else []
    local_repository = context["local_repository"]
    commands = [{"kind": "toolchain", "cwd": str(root),
                 "argv": command(executable, local_repository, "--version")}]
    if selected:
        commands.append({"kind": "prepare", "cwd": str(root), "argv": command(executable, local_repository, "-B", "install", "-pl", ",".join(selected), "-am", "-DskipITs=true", "-DskipTests=true", "-Dmaven.javadoc.skip=true", *options)})
    for name in selected:
        goals = ["test-compile", "failsafe:integration-test", "failsafe:verify"] if framework == "failsafe" else ["test"]
        commands.append({"kind": "test", "module": name, "cwd": str(root / name),
                         "argv": command(executable, local_repository, "-B", "clean", *goals, "-DskipITs=false", "-DskipTests=false", "-Dmaven.test.skip=false", "-DfailIfNoTests=true", *options)})
    dependencies = [{"repo": str(Path(p).resolve()), "source": snapshot(Path(p).resolve())} for p in dependency_repos]
    artifacts = [jar_pair(*pair) for pair in jar_pairs]
    result = {"schema_version": 3, "repo": str(root), "source": snapshot(root),
              "framework": framework, "dependency_repositories": dependencies, "artifacts": artifacts,
              "created_ns": time.time_ns(), "requested": modules, "selected": selected,
              "unavailable": unavailable, "profiles": profiles, "commands": commands,
              "runtime_context": context, "maven_executable": executable,
              "supported_maven": SUPPORTED_MAVEN_VERSION}
    result["plan_id"] = digest(result)
    return result


def counts(report_dir, started, finished):
    files = sorted(report_dir.glob("TEST-*.xml"))
    if not files:
        raise ValueError("缺少本轮 Maven 用例报告")
    total = dict(tests=0, failures=0, errors=0, skipped=0)
    evidence = []
    for file in files:
        if file.is_symlink() or not started <= file.stat().st_mtime_ns <= finished:
            raise ValueError("报告不属于本轮执行：%s" % file.name)
        raw = file.read_bytes()
        parsed = parse_xml(raw, file.name)
        numbers = parsed["counts"]
        for key in total:
            total[key] += numbers[key]
        evidence.append(parsed)
    return total, evidence


def report(document, execution):
    expected = dict(document)
    plan_id = expected.pop("plan_id")
    if document["schema_version"] != 3 or digest(expected) != plan_id or execution["plan_id"] != plan_id:
        raise ValueError("执行清单已变化或结果不属于本清单")
    if execution.get("runtime_context") != document["runtime_context"]:
        raise ValueError("执行 runtime context 与清单不一致")
    observed = execution.get("commands")
    if not isinstance(observed, list) or len(observed) != len(document["commands"]):
        raise ValueError("执行命令必须与清单一一对应")
    test_runs = {}
    version = None
    for expected_command, actual in zip(document["commands"], observed):
        if not isinstance(actual, dict) or actual.get("kind") != expected_command["kind"]:
            raise ValueError("执行命令类型或顺序不一致")
        if actual.get("module") != expected_command.get("module"):
            raise ValueError("执行命令模块不一致")
        started, finished, exit_code = actual.get("started_ns"), actual.get("finished_ns"), actual.get("exit_code")
        if (type(started) is not int or type(finished) is not int or type(exit_code) is not int
                or not document["created_ns"] <= started <= finished <= time.time_ns()):
            raise ValueError("执行时间或退出码无效")
        if actual.get("runtime_context") != document["runtime_context"]:
            raise ValueError("执行命令未在清单绑定的 runtime 中运行")
        if exit_code != 0 and expected_command["kind"] != "test":
            raise ValueError("Maven 命令失败")
        if expected_command["kind"] == "toolchain":
            version = actual.get("observed_maven_version")
            if not isinstance(version, str) or not re.fullmatch(r"3\.9\.\d+(?:[-+].*)?", version):
                raise ValueError("当前 Maven 版本不在支持的 3.9.x 范围")
        if expected_command["kind"] == "test":
            test_runs[expected_command["module"]] = actual
    root = Path(document["repo"])
    if snapshot(root) != document["source"]:
        raise ValueError("代码已变化，须重新生成清单并执行")
    for dependency in document["dependency_repositories"]:
        if snapshot(Path(dependency["repo"])) != dependency["source"]:
            raise ValueError("依赖源码已变化，须重新构建和验证")
    for artifact in document["artifacts"]:
        if jar_pair(artifact["built"], artifact["consumed"]) != artifact:
            raise ValueError("依赖 Jar 已变化，须重新验证")
    results = []
    for name in document["selected"]:
        item = {"module": name, "status": "not_executed"}
        if name in test_runs:
            run = test_runs[name]
            try:
                started, finished = run["started_ns"], run["finished_ns"]
                if (type(started) is not int or type(finished) is not int or type(run["exit_code"]) is not int
                        or not document["created_ns"] <= started <= finished <= time.time_ns()):
                    raise ValueError("执行时间或退出码无效")
                folder = module_path(root, name) / ("target/%s-reports" % document["framework"])
                if folder.resolve() != folder.absolute():
                    raise ValueError("报告目录不能经过符号链接")
                totals, evidence = counts(folder, started, finished)
                status = "passed"
                if run["exit_code"] != 0 or totals["failures"] or totals["errors"]:
                    status = "failed"
                elif totals["tests"] == 0 or totals["skipped"]:
                    status = "incomplete"
                item.update(status=status, counts=totals, reports=evidence, exit_code=run["exit_code"])
            except (ValueError, OSError, KeyError, ET.ParseError) as error:
                item.update(status="unknown", reason=str(error))
        results.append(item)
    results.extend(dict(item, status="unsupported") for item in document["unavailable"])
    return {"plan_id": plan_id, "source": document["source"], "requested": document["requested"],
            "framework": document["framework"], "dependency_repositories": document["dependency_repositories"],
            "artifacts": document["artifacts"], "runtime_context": document["runtime_context"],
            "maven_executable": document["maven_executable"], "observed_maven_version": version,
            "modules": results, "passed": bool(results) and all(r["status"] == "passed" for r in results),
            "boundary": "仅核对本轮模块测试、已声明依赖源码和 Jar 内容。Surefire 成功不自动证明集成覆盖；Agent 仍须核对构建事实、实际加载路径、无用例过滤及测试预期。"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("plan")
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--module", action="append", required=True)
    p.add_argument("--profile", action="append", default=[])
    p.add_argument("--framework", choices=("failsafe", "surefire"), default="failsafe")
    p.add_argument("--dependency-repo", action="append", type=Path, default=[])
    p.add_argument("--jar-pair", action="append", nargs=2, default=[], metavar=("BUILT", "CONSUMED"))
    p.add_argument("--runtime-context", type=Path, required=True)
    p.add_argument("--maven-executable", required=True)
    p = commands.add_parser("report")
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--execution", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            result = plan(args.repo, args.module, args.profile, args.framework, args.dependency_repo, args.jar_pair,
                          json.loads(args.runtime_context.read_text()), args.maven_executable)
        else:
            result = report(json.loads(args.plan.read_text()), json.loads(args.execution.read_text()))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if args.command == "plan" or result["passed"] else 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.CalledProcessError, ET.ParseError) as error:
        print("Maven 模块测试核验未完成：%s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

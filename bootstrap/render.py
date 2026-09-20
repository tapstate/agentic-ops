#!/usr/bin/env python3
"""为项目工位生成中央产品根目录的薄接线。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

from agent_registry import select
from product_state import load as load_product_state
from skill_wiring import validate_skill
from station_paths import StationDirectory, station_artifact_path
from station_compatibility import (
    load_manifest,
    require_station_can_adopt,
    station_epoch,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workflow import task_store


SCHEMA_VERSION = 4
INIT_SCHEMA_VERSION = 2
STATE_DIRECTORY = ".agenticops"
INIT_NAME = "init.json"
STATION_NAME = "station.json"
HOOK_TEMPLATE_MARKER = re.compile(r"__AGENTIC_OPS_HOOK_[A-Z0-9_]+__")


def safe_path(root, relative):
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError("产物路径越界：%s" % relative) from error
    return candidate


def state_path(station, name):
    return station_artifact_path(station, Path(STATE_DIRECTORY) / name)


def read_json(path, label):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("%s无法读取：%s" % (label, error)) from error


def write_json_atomic(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(".%s.%s.tmp" % (path.name, os.getpid()))
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.chmod(temporary, 0o600)
    os.replace(str(temporary), str(path))


def replacements(install_root, project, manifest=None):
    values = {
        "__AGENTIC_OPS_HOME__": str(install_root.resolve()),
        "__AGENTIC_OPS_PROJECT__": project,
    }
    if manifest is not None:
        hook = manifest["hook"]
        native = hook["native"]
        tool_matchers = native["tool_matchers"]
        native_matcher = (
            None
            if tool_matchers is None
            else "|".join(tool_matchers[kind] for kind in hook["tool_kinds"])
        )
        values['"__AGENTIC_OPS_HOOK_TIMEOUT_SECONDS__"'] = str(
            hook["timeout_seconds"]
        )
        values['"__AGENTIC_OPS_HOOK_NATIVE_EVENT__"'] = json.dumps(native["event"])
        values['"__AGENTIC_OPS_HOOK_NATIVE_TOOL_MATCHER__"'] = json.dumps(native_matcher)
    return values


def rendered_content(install_root, project, template, manifest=None):
    source = safe_path(install_root, template)
    content = source.read_text(encoding="utf-8")
    for marker, value in replacements(install_root, project, manifest).items():
        content = content.replace(marker, value)
    if manifest:
        unresolved = sorted(set(HOOK_TEMPLATE_MARKER.findall(content)))
        if unresolved:
            raise ValueError(
                "Agent Hook 模板变量未被完整消费：%s：%s"
                % (manifest["name"], ", ".join(unresolved))
            )
    return content


def product_ref(install_root):
    local_state = install_root / ".local" / "product.json"
    if local_state.is_file():
        try:
            document = load_product_state(install_root)
        except ValueError:
            # 源码产品根可由 Git HEAD 自证版本；旧本地生命周期文件不会被采用或改写。
            document = None
        if document and document.get("mode") == "installed":
            current_ref = document.get("current_ref")
            if isinstance(current_ref, str) and current_ref:
                return current_ref
    try:
        result = subprocess.run(
            ["git", "-C", str(install_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "source"


def content_hash(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def load_station(station, tree=None):
    if tree is None:
        path = state_path(station, STATION_NAME)
        if path.is_file():
            document = read_json(path, "工位配置")
        else:
            return None, None
    elif tree.is_file(Path(STATE_DIRECTORY) / STATION_NAME):
        document = tree.read_json(Path(STATE_DIRECTORY) / STATION_NAME, "工位配置")
    else:
        return None, None
    if document is not None:
        if document.get("schema_version") not in (1, SCHEMA_VERSION):
            raise ValueError("工位不兼容，请使用原版本将这个旧工位受控解绑并重建")
        return document, None
    return None, None


def load_init(station, tree=None):
    relative = Path(STATE_DIRECTORY) / INIT_NAME
    if tree is None:
        path = state_path(station, INIT_NAME)
        if not path.is_file():
            return None
        document = read_json(path, "工位初始化信息")
    elif not tree.is_file(relative):
        return None
    else:
        document = tree.read_json(relative, "工位初始化信息")
    if document.get("schema_version") not in (1, INIT_SCHEMA_VERSION):
        raise ValueError("不支持的工位初始化版本")
    return document


def common_artifacts(install_root, project):
    return {
        "AGENTS.md": rendered_content(
            install_root, project, "adapters/station/AGENTS.md"
        ),
        "agenticops": rendered_content(
            install_root, project, "adapters/station/agenticops"
        ),
        ".mcp.json": rendered_content(
            install_root, project, "adapters/tools/mcp.template.json"
        ),
    }


def file_artifact(content):
    return {"kind": "file", "content": content}


def symlink_artifact(target):
    return {"kind": "symlink", "target": target}


def project_skill_sources(install_root, project):
    root = install_root / "projects" / project / "skills"
    if not root.is_dir():
        return []
    sources = []
    for candidate in sorted(root.iterdir()):
        if candidate.name.startswith("."):
            continue
        validate_skill(candidate)
        source = candidate.resolve()
        try:
            source.relative_to(root.resolve())
        except ValueError as error:
            raise ValueError("项目 Skill 路径越界：%s" % candidate) from error
        sources.append(source)
    return sources


def expected_artifacts(install_root, station, project, agents, manifests):
    artifacts = {
        target: file_artifact(content)
        for target, content in common_artifacts(install_root, project).items()
    }
    owners = {target: "station" for target in artifacts}
    messages = []
    for agent_id in agents:
        manifest = manifests[agent_id]
        message = manifest.get("launch", {}).get("message")
        if message:
            messages.append("%s：%s" % (agent_id, message))
        for artifact in manifest["artifacts"]:
            target = artifact["target"]
            if target in artifacts:
                raise ValueError(
                    "Agent 接线目标冲突：%s 同时由 %s 和 %s 生成"
                    % (target, owners[target], agent_id)
                )
            content = rendered_content(
                install_root,
                project,
                artifact["template"],
                manifest,
            )
            artifacts[target] = file_artifact(content)
            owners[target] = agent_id
        skill_target = manifest.get("skill_target")
        if skill_target:
            for source in project_skill_sources(install_root, project):
                target = str(Path(skill_target) / source.name)
                if target in artifacts:
                    raise ValueError(
                        "Agent 项目 Skill 接线目标冲突：%s 同时由 %s 和 %s 生成"
                        % (target, owners[target], agent_id)
                    )
                destination = station_artifact_path(
                    station, target, allow_final_symlink=True
                )
                artifacts[target] = symlink_artifact(
                    os.path.relpath(str(source), str(destination.parent))
                )
                owners[target] = agent_id
    return artifacts, messages


def require_current_station_document(document):
    if document is not None and document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("工位不兼容，请使用原版本将这个旧工位受控解绑并重建")
    return document


def global_git_name():
    try:
        result = subprocess.run(
            ["git", "config", "--global", "--get", "user.name"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = result.stdout.strip() if result.returncode == 0 else ""
    try:
        return task_store.validate_git_name(value)
    except ValueError:
        return None


def branch_identity(existing):
    if existing and "branch_identity" in existing:
        identity = existing["branch_identity"]
        if (not isinstance(identity, dict) or set(identity) != {"schema_version", "git_name", "source"}
                or identity.get("schema_version") != 1
                or identity.get("source") != "git_global_user_name"):
            raise ValueError("工位 git_name 配置结构无效")
        task_store.validate_git_name(identity.get("git_name"))
        return dict(identity)
    if existing is not None:
        # 旧工位没有该可选字段时，只允许补写一次；已有值绝不跟随机器配置改写。
        value = global_git_name()
        if value is None:
            return None
        return {"schema_version": 1, "git_name": value, "source": "git_global_user_name"}
    value = global_git_name()
    if value is None:
        return None
    return {"schema_version": 1, "git_name": value, "source": "git_global_user_name"}


def station_document(install_root, station, project, agents, source_pool, existing):
    station_id = existing["station_id"] if existing else uuid.uuid4().hex
    result = {
        "schema_version": SCHEMA_VERSION,
        "product_root": str(install_root.resolve()),
        "station_id": station_id,
        "project": project,
        "agents": agents,
        "source_pool": str(Path(source_pool).resolve()),
    }
    identity = branch_identity(existing)
    if identity is not None:
        result["branch_identity"] = identity
    return result


def init_document(install_root, artifacts, station_state_epoch=None):
    recorded_artifacts = []
    for target, artifact in sorted(artifacts.items()):
        if artifact["kind"] == "file":
            recorded_artifacts.append(
                {"path": target, "kind": "file", "sha256": content_hash(artifact["content"])}
            )
        else:
            recorded_artifacts.append(
                {"path": target, "kind": "symlink", "target": artifact["target"]}
            )
    return {
        "schema_version": INIT_SCHEMA_VERSION,
        "product_ref": product_ref(install_root),
        "station_state_epoch": (
            station_state_epoch
            if station_state_epoch is not None
            else load_manifest(install_root)["station_state_epoch"]
        ),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "artifacts": recorded_artifacts,
    }


def owned_artifacts(init, legacy):
    if init:
        result = {}
        for item in init.get("artifacts", []):
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                continue
            kind = item.get("kind", "file")
            if kind == "file" and isinstance(item.get("sha256"), str):
                result[item["path"]] = {"kind": "file", "sha256": item["sha256"]}
            elif kind == "symlink" and isinstance(item.get("target"), str):
                result[item["path"]] = {"kind": "symlink", "target": item["target"]}
        return result
    if legacy:
        return {
            item: {"kind": "file", "sha256": None}
            for item in legacy.get("generated_artifacts", [])
        }
    return {}


def artifact_record(artifact):
    if artifact["kind"] == "file":
        return {"kind": "file", "sha256": content_hash(artifact["content"])}
    return {"kind": "symlink", "target": artifact["target"]}


def assert_artifact_ownership(station, owned, artifacts, tree):
    for target, expected in artifacts.items():
        path = tree.path(target)
        if not tree.exists(target):
            continue
        if target in owned:
            continue
        if expected["kind"] == "symlink":
            if not tree.is_symlink(target) or tree.readlink(target) != expected["target"]:
                raise ValueError("工位已有非 AgenticOps 文件，拒绝覆盖：%s" % path)
            continue
        try:
            content = tree.read_text(target)
        except ValueError as error:
            raise ValueError("工位同名文件无法读取：%s：%s" % (path, error)) from error
        if tree.is_symlink(target) or content != expected["content"]:
            raise ValueError("工位已有非 AgenticOps 文件，拒绝覆盖：%s" % path)


def remove_stale_artifacts(station, owned, expected_targets, tree):
    expected_parents = {
        parent
        for target in expected_targets
        for parent in Path(target).parents
        if parent.parts
    }
    for target, recorded in owned.items():
        if target in expected_targets:
            continue
        path = tree.path(target)
        if not tree.exists(target):
            continue
        if recorded["kind"] == "symlink":
            if not tree.is_symlink(target) or tree.readlink(target) != recorded["target"]:
                raise ValueError("旧 Skill 接线已被修改或异常，拒绝删除：%s" % path)
        else:
            if not tree.is_file(target) or tree.is_symlink(target):
                raise ValueError("旧接线不是普通文件，拒绝删除：%s" % path)
            content = tree.read_text(target)
            if recorded["sha256"] and content_hash(content) != recorded["sha256"]:
                raise ValueError("旧接线已被修改，拒绝删除：%s" % path)
        tree.unlink(target)
        parent = Path(target).parent
        while parent.parts:
            if parent in expected_parents:
                break
            if not tree.rmdir_cached(parent):
                break
            parent = parent.parent


def check_checkpoint_migration(owned, manifests, accepted, tree):
    """撤除已托管控制前显式确认；先检查所有目标，避免部分撤除。"""
    retired = sorted({target for manifest in manifests.values()
                      for target in manifest.get("retired_artifacts", []) if target in owned})
    if not retired:
        return []
    if not accepted:
        raise ValueError(
            "需要显式迁移流程检查点：将撤除已托管 Agent Hook %s；Git/Jira/PR 不再自动进入 Gate，"
            "方案确认只在 Workflow 检查点核验，Jira 不再保证强制单次调用。"
            "请核对后执行 agenticops station repair --station <当前工位> --accept-checkpoint-migration；"
            "当前接线与任务状态保留。" % ", ".join(retired))
    for target in retired:
        record = owned[target]
        if not tree.exists(target):
            continue
        if record.get("kind") != "file" or not record.get("sha256"):
            raise ValueError("退役 Hook 缺少可验证归属哈希，拒绝删除：%s" % target)
        if (tree.is_symlink(target) or not tree.is_file(target)
                or content_hash(tree.read_text(target)) != record["sha256"]):
            raise ValueError("旧 Hook 已被修改，拒绝迁移：%s" % target)
    return retired


def validate_station_document(install_root, document):
    project = document.get("project")
    agents = document.get("agents")
    source_pool = document.get("source_pool")
    if not isinstance(project, str) or not project:
        raise ValueError("工位配置缺少 project")
    if document.get("product_root") != str(install_root.resolve()):
        raise ValueError("工位产品根目录不一致，请执行 agenticops station repair")
    if not isinstance(agents, list) or not agents:
        raise ValueError("工位配置 agents 无效")
    station_id = document.get("station_id")
    if not isinstance(station_id, str) or not re.fullmatch(r"[a-f0-9]{32}", station_id):
        raise ValueError("工位配置缺少 station_id")
    if document.get("schema_version") != SCHEMA_VERSION or "repository_pool" in document:
        raise ValueError("工位不兼容，请使用原版本将这个旧工位受控解绑并重建")
    if not isinstance(source_pool, str) or not source_pool or not Path(source_pool).is_absolute():
        raise ValueError("工位配置 source_pool 无效")
    branch_identity(document)
    selected, manifests = select(install_root, agents)
    return project, selected, manifests


def check_station(install_root, station, config, init, tree):
    if init is None:
        raise ValueError("工位缺少 init.json，请执行 agenticops station repair")
    project, agents, manifests = validate_station_document(install_root, config)
    compatibility = load_manifest(install_root)
    if station_epoch(station, compatibility) not in compatibility["supported_station_state_epochs"]:
        raise ValueError("工位状态代际与当前产品不兼容；请先在原版本结束并清理任务，再重新初始化工位")
    from workflow import station_operation, task_store
    task_store.read_current(station)
    station_operation.read(station)
    for name in ("config", "source", "runtime", "archive"):
        if not tree.is_dir(name) or tree.is_symlink(name):
            raise ValueError("工位目录缺失或不安全，请检查后执行 repair：%s" % name)
    artifacts, _ = expected_artifacts(install_root, station, project, agents, manifests)
    if init.get("product_ref") != product_ref(install_root):
        raise ValueError("产品根目录版本已变化，请执行 agenticops station repair")
    recorded = owned_artifacts(init, None)
    expected_records = {path: artifact_record(artifact) for path, artifact in artifacts.items()}
    if recorded != expected_records:
        raise ValueError("工位初始化清单漂移，请执行 agenticops station repair")
    drift = []
    for target, expected in artifacts.items():
        path = tree.path(target)
        if expected["kind"] == "symlink":
            valid = tree.is_symlink(target) and tree.readlink(target) == expected["target"]
        else:
            valid = (
                tree.is_file(target)
                and not tree.is_symlink(target)
                and tree.read_text(target) == expected["content"]
            )
        if not valid:
            drift.append(target)
    if drift:
        raise ValueError("工位薄接线漂移：%s" % ", ".join(sorted(drift)))
    return project, agents


def update_git_exclude(station, artifacts):
    result = subprocess.run(
        ["git", "-C", str(station), "rev-parse", "--git-path", "info/exclude"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return
    exclude = Path(result.stdout.strip())
    if not exclude.is_absolute():
        exclude = station / exclude
    existing = set(exclude.read_text(encoding="utf-8").splitlines()) if exclude.is_file() else set()
    patterns = [STATE_DIRECTORY + "/"] + sorted(artifacts)
    missing = [pattern for pattern in patterns if pattern not in existing]
    if missing:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a", encoding="utf-8") as stream:
            for pattern in missing:
                stream.write(pattern + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-home", required=True)
    parser.add_argument("--station", required=True)
    parser.add_argument("--agent", action="append")
    parser.add_argument("--project")
    parser.add_argument("--source-pool")
    parser.add_argument("--reuse-materials", action="store_true")
    parser.add_argument("--accept-checkpoint-migration", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--refresh", action="store_true")
    mode.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    if arguments.accept_checkpoint_migration and not arguments.refresh:
        parser.error("--accept-checkpoint-migration 只能用于显式 repair/refresh")

    install_root = Path(arguments.install_home).resolve()
    station = Path(arguments.station).resolve()
    station.mkdir(parents=True, exist_ok=True)
    try:
        with StationDirectory(station) as tree:
            config, legacy = load_station(station, tree)
            config = require_current_station_document(config)
            init = load_init(station, tree)
            if arguments.refresh or arguments.check:
                if config is None:
                    parser.error("工位尚未初始化，请先执行 agenticops station init")
                project = config["project"]
                requested_agents = config["agents"]
                requested_source_pool = config["source_pool"]
            else:
                project = arguments.project or "tapdata"
                requested_agents = arguments.agent
                requested_source_pool = arguments.source_pool or load_product_state(install_root)["source_pool"]

            project_root = install_root / "projects" / project
            if not project_root.is_dir():
                parser.error("未安装项目适配：%s" % project)

            if arguments.check:
                _, all_manifests = select(install_root, None)
                check_checkpoint_migration(owned_artifacts(init, legacy), all_manifests, False, tree)
                checked_project, checked_agents = check_station(
                    install_root, station, config, init, tree
                )
                print(
                    "AgenticOps 工位检查通过：%s（project=%s，agents=%s，ref=%s）"
                    % (
                        station,
                        checked_project,
                        ",".join(checked_agents),
                        init["product_ref"],
                    )
                )
                return 0

            for name in ("config", "source", "runtime", "archive"):
                if tree.exists(name):
                    if not tree.is_dir(name) or tree.is_symlink(name):
                        raise ValueError("工位目录已有未知内容或不安全路径，拒绝覆盖：%s" % name)
                    if config is None and any(tree.path(name).iterdir()):
                        if name == "runtime" or not arguments.reuse_materials:
                            raise ValueError("已有材料不能自动采用；runtime 必须为空，其它目录需明确 --reuse-materials：%s" % name)
            if config is None:
                if tree.exists(STATE_DIRECTORY):
                    if not tree.is_dir(STATE_DIRECTORY) or tree.is_symlink(STATE_DIRECTORY):
                        raise ValueError("未绑定的状态目录不安全，拒绝生成")
                    if any(tree.path(STATE_DIRECTORY).iterdir()):
                        raise ValueError("发现未绑定的旧状态或未知材料，请使用原版本受控解绑并重建")
            if config is not None:
                validate_station_document(install_root, config)
            agents, manifests = select(install_root, requested_agents)
            artifacts, messages = expected_artifacts(
                install_root, station, project, agents, manifests
            )
            adopted_epoch = None
            if init is not None:
                adopted_epoch = require_station_can_adopt(install_root, station)
            document = init_document(
                install_root,
                artifacts,
                station_state_epoch=adopted_epoch,
            )
            owned = owned_artifacts(init, legacy)
            _, all_manifests = select(install_root, None)
            migrated = check_checkpoint_migration(owned, all_manifests, arguments.accept_checkpoint_migration, tree)
            if init and init.get("checkpoint_migration"):
                document["checkpoint_migration"] = init["checkpoint_migration"]
            if migrated:
                document["checkpoint_migration"] = {
                    "from_product_ref": (init or {}).get("product_ref", "legacy"),
                    "accepted_at": document["generated_at"],
                    "retired_artifacts": migrated,
                }
            assert_artifact_ownership(station, owned, artifacts, tree)
            remove_stale_artifacts(station, owned, set(artifacts), tree)
            station_config = station_document(
                install_root,
                station,
                project,
                agents,
                requested_source_pool,
                config,
            )

            for target, artifact in artifacts.items():
                destination = tree.path(target)
                if artifact["kind"] == "symlink":
                    if tree.exists(target):
                        if tree.is_symlink(target) or tree.is_file(target):
                            tree.unlink(target)
                        else:
                            parser.error(
                                "工位 Skill 接线位置是目录，拒绝覆盖：%s" % destination
                            )
                    tree.symlink(artifact["target"], target)
                else:
                    tree.write_text_atomic(target, artifact["content"])
                if target == "agenticops":
                    tree.chmod(target, 0o700)
            tree.write_json_atomic(Path(STATE_DIRECTORY) / STATION_NAME, station_config)
            tree.write_json_atomic(Path(STATE_DIRECTORY) / INIT_NAME, document)
            for name in ("config", "source", "runtime", "archive"):
                tree.path(name).mkdir(mode=0o700, exist_ok=True)
            task_store.initialize_current(station)
    except ValueError as error:
        parser.error(str(error))

    update_git_exclude(station, artifacts)
    print(
        "AgenticOps 工位接线已刷新：%s（project=%s，agents=%s，ref=%s）"
        % (station, project, ",".join(agents), document["product_ref"])
    )
    for message in messages:
        print(message)
    if "branch_identity" not in station_config:
        print(
            "AgenticOps：当前工位未配置 git_name。请补充全局 Git user.name 后重新初始化，或执行 "
            "agenticops station identity --station <目录>；"
            "该值会一次性保存，用于稳定生成并恢复任务工作分支。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

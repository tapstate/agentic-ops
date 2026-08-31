#!/usr/bin/env python3
"""为项目工作空间生成中央产品根目录的薄接线。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

from agent_registry import select
from product_state import load as load_product_state
from repository_pool import load as load_repository_pool
from repository_pool import validate_root as validate_repository_pool_root
from skill_wiring import validate_skill
from workspace_paths import WorkspaceDirectory, workspace_artifact_path


SCHEMA_VERSION = 2
INIT_SCHEMA_VERSION = 2
STATE_DIRECTORY = ".agenticops"
INIT_NAME = "init.json"
WORKSPACE_NAME = "workspace.json"
LEGACY_BINDING_NAME = ".agenticops.json"


def safe_path(root, relative):
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError("产物路径越界：%s" % relative) from error
    return candidate


def state_path(workspace, name):
    return workspace_artifact_path(workspace, Path(STATE_DIRECTORY) / name)


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


def replacements(install_root, project):
    return {
        "__AGENTIC_OPS_HOME__": str(install_root.resolve()),
        "__AGENTIC_OPS_PROJECT__": project,
    }


def rendered_content(install_root, project, template):
    source = safe_path(install_root, template)
    content = source.read_text(encoding="utf-8")
    for marker, value in replacements(install_root, project).items():
        content = content.replace(marker, value)
    return content


def product_ref(install_root):
    local_state = install_root / ".local" / "product.json"
    if local_state.is_file():
        document = load_product_state(install_root)
        if document.get("mode") == "installed":
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


def load_workspace(workspace, tree=None):
    if tree is None:
        path = state_path(workspace, WORKSPACE_NAME)
        if path.is_file():
            document = read_json(path, "工作空间配置")
        else:
            legacy = workspace_artifact_path(workspace, LEGACY_BINDING_NAME)
            if not legacy.is_file():
                return None, None
            document = read_json(legacy, "旧工作空间绑定")
            return {
                "schema_version": 1,
                "product_root": document.get("product_root"),
                "project": document.get("project"),
                "agents": document.get("agents"),
            }, document
    elif tree.is_file(Path(STATE_DIRECTORY) / WORKSPACE_NAME):
        document = tree.read_json(Path(STATE_DIRECTORY) / WORKSPACE_NAME, "工作空间配置")
    elif tree.is_file(LEGACY_BINDING_NAME):
        document = tree.read_json(LEGACY_BINDING_NAME, "旧工作空间绑定")
        return {
            "schema_version": 1,
            "product_root": document.get("product_root"),
            "project": document.get("project"),
            "agents": document.get("agents"),
        }, document
    else:
        return None, None
    if document is not None:
        if document.get("schema_version") not in (1, SCHEMA_VERSION):
            raise ValueError("不支持的工作空间配置版本")
        return document, None
    return None, None


def load_init(workspace, tree=None):
    relative = Path(STATE_DIRECTORY) / INIT_NAME
    if tree is None:
        path = state_path(workspace, INIT_NAME)
        if not path.is_file():
            return None
        document = read_json(path, "工作空间初始化信息")
    elif not tree.is_file(relative):
        return None
    else:
        document = tree.read_json(relative, "工作空间初始化信息")
    if document.get("schema_version") not in (1, INIT_SCHEMA_VERSION):
        raise ValueError("不支持的工作空间初始化版本")
    return document


def common_artifacts(install_root, project):
    return {
        "AGENTS.md": rendered_content(
            install_root, project, "adapters/workspace/AGENTS.md"
        ),
        ".agenticops/agenticops": rendered_content(
            install_root, project, "adapters/workspace/agenticops"
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


def expected_artifacts(install_root, workspace, project, agents, manifests):
    artifacts = {
        target: file_artifact(content)
        for target, content in common_artifacts(install_root, project).items()
    }
    owners = {target: "workspace" for target in artifacts}
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
            artifacts[target] = file_artifact(
                rendered_content(install_root, project, artifact["template"])
            )
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
                destination = workspace_artifact_path(
                    workspace, target, allow_final_symlink=True
                )
                artifacts[target] = symlink_artifact(
                    os.path.relpath(str(source), str(destination.parent))
                )
                owners[target] = agent_id
    return artifacts, messages


def default_repository_pool(install_root):
    return load_repository_pool(install_root)["root"]


def migrate_workspace_document(install_root, workspace, document):
    if document is None:
        return None
    migrated = dict(document)
    if migrated.get("schema_version") == 1:
        migrated.update(
            {
                "schema_version": SCHEMA_VERSION,
                "workspace_id": uuid.uuid4().hex,
                "repository_pool": {
                    "root": default_repository_pool(install_root),
                    "source": "product-default-migration",
                },
            }
        )
    return migrated


def workspace_document(install_root, workspace, project, agents, existing, repository_pool):
    if existing:
        workspace_id = existing["workspace_id"]
        pool = existing["repository_pool"]
    else:
        selected = repository_pool or default_repository_pool(install_root)
        pool = {
            "root": str(validate_repository_pool_root(install_root, selected, create=True)),
            "source": "workspace-override" if repository_pool else "product-default",
        }
        workspace_id = uuid.uuid4().hex
    pool_root = Path(pool["root"]).resolve()
    if workspace == pool_root or pool_root in workspace.parents or workspace in pool_root.parents:
        raise ValueError("项目工作空间与 Source Pool 不能互相嵌套：%s" % workspace)
    return {
        "schema_version": SCHEMA_VERSION,
        "product_root": str(install_root.resolve()),
        "workspace_id": workspace_id,
        "project": project,
        "agents": agents,
        "repository_pool": pool,
    }


def init_document(install_root, artifacts):
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


def assert_artifact_ownership(workspace, owned, artifacts, tree):
    for target, expected in artifacts.items():
        path = tree.path(target)
        if not tree.exists(target):
            continue
        if target in owned:
            continue
        if expected["kind"] == "symlink":
            if not tree.is_symlink(target) or tree.readlink(target) != expected["target"]:
                raise ValueError("工作空间已有非 AgenticOps 文件，拒绝覆盖：%s" % path)
            continue
        try:
            content = tree.read_text(target)
        except ValueError as error:
            raise ValueError("工作空间同名文件无法读取：%s：%s" % (path, error)) from error
        if tree.is_symlink(target) or content != expected["content"]:
            raise ValueError("工作空间已有非 AgenticOps 文件，拒绝覆盖：%s" % path)


def remove_stale_artifacts(workspace, owned, expected_targets, tree):
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
            if not tree.rmdir_cached(parent):
                break
            parent = parent.parent


def validate_workspace_document(install_root, document):
    project = document.get("project")
    agents = document.get("agents")
    if not isinstance(project, str) or not project:
        raise ValueError("工作空间配置缺少 project")
    if document.get("product_root") != str(install_root.resolve()):
        raise ValueError("工作空间产品根目录不一致，请执行 agenticops repair")
    if not isinstance(agents, list) or not agents:
        raise ValueError("工作空间配置 agents 无效")
    workspace_id = document.get("workspace_id")
    if not isinstance(workspace_id, str) or not re.fullmatch(r"[a-f0-9]{32}", workspace_id):
        raise ValueError("工作空间配置缺少 workspace_id")
    pool = document.get("repository_pool")
    if not isinstance(pool, dict) or not isinstance(pool.get("root"), str):
        raise ValueError("工作空间配置缺少 repository_pool.root")
    if pool.get("source") not in (
        "product-default", "workspace-override", "product-default-migration"
    ):
        raise ValueError("工作空间 repository_pool.source 无效")
    pool_root = validate_repository_pool_root(install_root, pool["root"], create=False)
    if not pool_root.is_dir():
        raise ValueError("工作空间绑定的 Source Pool 不存在：%s" % pool_root)
    selected, manifests = select(install_root, agents)
    return project, selected, manifests


def check_workspace(install_root, workspace, config, init, tree):
    if init is None:
        raise ValueError("工作空间缺少 init.json，请执行 agenticops repair")
    project, agents, manifests = validate_workspace_document(install_root, config)
    artifacts, _ = expected_artifacts(install_root, workspace, project, agents, manifests)
    if init.get("product_ref") != product_ref(install_root):
        raise ValueError("产品根目录版本已变化，请执行 agenticops repair")
    recorded = owned_artifacts(init, None)
    expected_records = {path: artifact_record(artifact) for path, artifact in artifacts.items()}
    if recorded != expected_records:
        raise ValueError("工作空间初始化清单漂移，请执行 agenticops repair")
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
        raise ValueError("工作空间薄接线漂移：%s" % ", ".join(sorted(drift)))
    return project, agents


def update_git_exclude(workspace, artifacts):
    result = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "--git-path", "info/exclude"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return
    exclude = Path(result.stdout.strip())
    if not exclude.is_absolute():
        exclude = workspace / exclude
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
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--agent", action="append")
    parser.add_argument("--project")
    parser.add_argument("--repository-pool")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--refresh", action="store_true")
    mode.add_argument("--check", action="store_true")
    arguments = parser.parse_args()

    install_root = Path(arguments.install_home).resolve()
    workspace = Path(arguments.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        with WorkspaceDirectory(workspace) as tree:
            config, legacy = load_workspace(workspace, tree)
            legacy_workspace_schema = bool(config and config.get("schema_version") == 1)
            config = migrate_workspace_document(install_root, workspace, config)
            init = load_init(workspace, tree)
            if arguments.refresh or arguments.check:
                if config is None:
                    parser.error("工作空间尚未初始化，请先执行 agenticops init")
                project = config["project"]
                requested_agents = config["agents"]
            else:
                project = arguments.project or "tapdata"
                requested_agents = arguments.agent

            project_root = install_root / "projects" / project
            if not project_root.is_dir():
                parser.error("未安装项目适配：%s" % project)

            if arguments.check:
                if legacy_workspace_schema:
                    parser.error("工作空间配置需要迁移 Source Pool 绑定，请执行 agenticops repair")
                checked_project, checked_agents = check_workspace(
                    install_root, workspace, config, init, tree
                )
                print(
                    "AgenticOps 工作空间检查通过：%s（project=%s，agents=%s，ref=%s）"
                    % (
                        workspace,
                        checked_project,
                        ",".join(checked_agents),
                        init["product_ref"],
                    )
                )
                return 0

            if config is not None:
                validate_workspace_document(install_root, config)
            agents, manifests = select(install_root, requested_agents)
            artifacts, messages = expected_artifacts(
                install_root, workspace, project, agents, manifests
            )
            document = init_document(install_root, artifacts)
            owned = owned_artifacts(init, legacy)
            assert_artifact_ownership(workspace, owned, artifacts, tree)
            remove_stale_artifacts(workspace, owned, set(artifacts), tree)
            workspace_config = workspace_document(
                install_root,
                workspace,
                project,
                agents,
                config,
                arguments.repository_pool,
            )

            for target, artifact in artifacts.items():
                destination = tree.path(target)
                if artifact["kind"] == "symlink":
                    if tree.exists(target):
                        if tree.is_symlink(target) or tree.is_file(target):
                            tree.unlink(target)
                        else:
                            parser.error(
                                "工作空间 Skill 接线位置是目录，拒绝覆盖：%s" % destination
                            )
                    tree.symlink(artifact["target"], target)
                else:
                    tree.write_text_atomic(target, artifact["content"])
                if target == ".agenticops/agenticops":
                    tree.chmod(target, 0o700)
            tree.write_json_atomic(Path(STATE_DIRECTORY) / WORKSPACE_NAME, workspace_config)
            tree.write_json_atomic(Path(STATE_DIRECTORY) / INIT_NAME, document)
            if tree.is_file(LEGACY_BINDING_NAME):
                tree.unlink(LEGACY_BINDING_NAME)
    except ValueError as error:
        parser.error(str(error))

    update_git_exclude(workspace, artifacts)
    print(
        "AgenticOps 工作空间接线已刷新：%s（project=%s，agents=%s，ref=%s）"
        % (workspace, project, ",".join(agents), document["product_ref"])
    )
    for message in messages:
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

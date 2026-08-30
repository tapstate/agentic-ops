#!/usr/bin/env python3
"""为项目工作空间生成中央产品根目录的薄接线。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from agent_registry import select
from product_state import load as load_product_state


SCHEMA_VERSION = 1
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
    return workspace / STATE_DIRECTORY / name


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


def load_workspace(workspace):
    path = state_path(workspace, WORKSPACE_NAME)
    if path.is_file():
        document = read_json(path, "工作空间配置")
        if document.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("不支持的工作空间配置版本")
        return document, None
    legacy = workspace / LEGACY_BINDING_NAME
    if legacy.is_file():
        document = read_json(legacy, "旧工作空间绑定")
        return {
            "schema_version": SCHEMA_VERSION,
            "product_root": document.get("product_root"),
            "project": document.get("project"),
            "agents": document.get("agents"),
        }, document
    return None, None


def load_init(workspace):
    path = state_path(workspace, INIT_NAME)
    if not path.is_file():
        return None
    document = read_json(path, "工作空间初始化信息")
    if document.get("schema_version") != SCHEMA_VERSION:
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


def expected_artifacts(install_root, project, agents, manifests):
    artifacts = common_artifacts(install_root, project)
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
            artifacts[target] = rendered_content(
                install_root, project, artifact["template"]
            )
            owners[target] = agent_id
    return artifacts, messages


def workspace_document(install_root, project, agents):
    return {
        "schema_version": SCHEMA_VERSION,
        "product_root": str(install_root.resolve()),
        "project": project,
        "agents": agents,
    }


def init_document(install_root, artifacts):
    return {
        "schema_version": SCHEMA_VERSION,
        "product_ref": product_ref(install_root),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "artifacts": [
            {"path": target, "sha256": content_hash(content)}
            for target, content in sorted(artifacts.items())
        ],
    }


def owned_artifacts(init, legacy):
    if init:
        return {
            item["path"]: item.get("sha256")
            for item in init.get("artifacts", [])
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        }
    if legacy:
        return {item: None for item in legacy.get("generated_artifacts", [])}
    return {}


def assert_artifact_ownership(workspace, owned, artifacts):
    for target, expected in artifacts.items():
        path = safe_path(workspace, target)
        if not path.exists() and not path.is_symlink():
            continue
        if target in owned:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as error:
            raise ValueError("工作空间同名文件无法读取：%s：%s" % (path, error)) from error
        if content != expected:
            raise ValueError("工作空间已有非 AgenticOps 文件，拒绝覆盖：%s" % path)


def remove_stale_artifacts(workspace, owned, expected_targets):
    for target, expected_hash in owned.items():
        if target in expected_targets:
            continue
        path = safe_path(workspace, target)
        if not path.exists() and not path.is_symlink():
            continue
        if not path.is_file() or path.is_symlink():
            raise ValueError("旧接线不是普通文件，拒绝删除：%s" % path)
        content = path.read_text(encoding="utf-8")
        if expected_hash and content_hash(content) != expected_hash:
            raise ValueError("旧接线已被修改，拒绝删除：%s" % path)
        path.unlink()
        parent = path.parent
        while parent != workspace:
            try:
                parent.rmdir()
            except OSError:
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
    selected, manifests = select(install_root, agents)
    return project, selected, manifests


def check_workspace(install_root, workspace, config, init):
    if init is None:
        raise ValueError("工作空间缺少 init.json，请执行 agenticops repair")
    project, agents, manifests = validate_workspace_document(install_root, config)
    artifacts, _ = expected_artifacts(install_root, project, agents, manifests)
    if init.get("product_ref") != product_ref(install_root):
        raise ValueError("产品根目录版本已变化，请执行 agenticops repair")
    recorded = owned_artifacts(init, None)
    expected_hashes = {path: content_hash(content) for path, content in artifacts.items()}
    if recorded != expected_hashes:
        raise ValueError("工作空间初始化清单漂移，请执行 agenticops repair")
    drift = []
    for target, expected in artifacts.items():
        path = safe_path(workspace, target)
        if not path.is_file() or path.read_text(encoding="utf-8") != expected:
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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--refresh", action="store_true")
    mode.add_argument("--check", action="store_true")
    arguments = parser.parse_args()

    install_root = Path(arguments.install_home).resolve()
    workspace = Path(arguments.workspace).resolve()
    config, legacy = load_workspace(workspace)
    init = load_init(workspace)
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
    workspace.mkdir(parents=True, exist_ok=True)

    if arguments.check:
        try:
            checked_project, checked_agents = check_workspace(
                install_root, workspace, config, init
            )
        except ValueError as error:
            parser.error(str(error))
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

    try:
        agents, manifests = select(install_root, requested_agents)
        artifacts, messages = expected_artifacts(
            install_root, project, agents, manifests
        )
        document = init_document(install_root, artifacts)
        owned = owned_artifacts(init, legacy)
        assert_artifact_ownership(workspace, owned, artifacts)
        remove_stale_artifacts(workspace, owned, set(artifacts))
    except ValueError as error:
        parser.error(str(error))

    for target, content in artifacts.items():
        destination = safe_path(workspace, target)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
        if target == ".agenticops/agenticops":
            os.chmod(destination, 0o700)
    write_json_atomic(
        state_path(workspace, WORKSPACE_NAME),
        workspace_document(install_root, project, agents),
    )
    write_json_atomic(state_path(workspace, INIT_NAME), document)
    legacy_path = workspace / LEGACY_BINDING_NAME
    if legacy_path.is_file():
        legacy_path.unlink()
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
